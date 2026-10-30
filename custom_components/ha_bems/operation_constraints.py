"""Binary operation permissions and explicit upstream planner dependencies."""

from .const import MODULE_OPERATION_IMPORT, MODULE_THERMAL_PLAN
from .entries import all_module_entries, item_enabled, module_type
from .forecast import validate_forecast


def upstream_planner(hass, entity_id):
    """Identify BEMS output ownership, including an unavailable startup entity."""
    from homeassistant.helpers import entity_registry as er

    state = hass.states.get(entity_id)
    if state and (identifier := state.attributes.get("planner_item_id")):
        return identifier
    record = er.async_get(hass).async_get(entity_id)
    if record and record.platform == "ha_bems" and record.unique_id.endswith("_operation_plan"):
        return record.unique_id.removesuffix("_operation_plan")
    return None


def constraint_selection(hass, config, legacy_modes=None):
    """Return a stable source reference and consumer-side interpretation.

    Legacy planner/import pairs retain their effective permissions until the
    upgrade moves interpretation into each consuming planner's own settings.
    """
    if "constraint_source" in config:
        source = config["constraint_source"]
        mode = config.get("constraint_mode", "direct")
    else:
        reference = config.get("constraint_entry_id")
        source = f"imported:{reference}" if reference and reference != "none" else None
        if legacy_modes is None:
            legacy_modes = {item.entry_id: {**item.data, **item.options}.get("operation_meaning", "running")
                            for item in all_module_entries(hass) if module_type(item) == MODULE_OPERATION_IMPORT}
        mode = config.get("constraint_mode", "inverted" if legacy_modes.get(reference, "running") == "running" else "direct")
    if not source or source == "none":
        return None, "direct"
    if not isinstance(source, str):
        raise ValueError("Choose an imported constraint forecast or an existing MILP operation plan")
    kind, separator, identifier = source.partition(":")
    if not separator or kind not in ("imported", "planner") or not identifier:
        raise ValueError("Choose an imported constraint forecast or an existing MILP operation plan")
    if mode not in ("direct", "inverted"):
        raise ValueError("Choose direct or inverted constraint interpretation")
    return source, mode


def constraint_dependency(hass, config):
    source, _ = constraint_selection(hass, config)
    if not source:
        return None
    kind, reference = source.split(":", 1)
    if kind == "planner":
        return reference
    entry = next((item for item in all_module_entries(hass)
                  if item.entry_id == reference and module_type(item) == MODULE_OPERATION_IMPORT), None)
    if entry is None:
        raise ValueError("The selected operation constraint import no longer exists")
    return upstream_planner(hass, {**entry.data, **entry.options}["forecast_entity"])


def constraint_choices(hass, identifier, config):
    choices = [{"value": "none", "label": "No operation constraint"}]
    for item in all_module_entries(hass):
        module = module_type(item)
        if module == MODULE_OPERATION_IMPORT:
            kind, label = "imported", "imported constraint forecast"
        elif module == MODULE_THERMAL_PLAN and item.entry_id != identifier:
            kind, label = "planner", "MILP operation plan"
        else:
            continue
        choices.append({"value": f"{kind}:{item.entry_id}", "label": f"{item.title} ({label})"})
    source, _ = constraint_selection(hass, config)
    if source and source not in {choice["value"] for choice in choices}:
        choices.append({"value": source, "label": "Previously selected constraint (unavailable)"})
    return choices


def read_constraint_forecast(hass, config, now):
    from .import_registry import planners, read_forecast_import

    source, mode = constraint_selection(hass, config)
    if not source:
        return None
    kind, identifier = source.split(":", 1)
    if kind == "imported":
        rows, _ = read_forecast_import(hass, identifier, MODULE_OPERATION_IMPORT, now)
    else:
        item = next((item for item in all_module_entries(hass)
                     if item.entry_id == identifier and module_type(item) == MODULE_THERMAL_PLAN), None)
        if item is None or not item_enabled(item):
            raise ValueError("The selected MILP operation plan no longer exists or is disabled")
        coordinator = planners(hass).get(identifier)
        if coordinator is None or not coordinator.last_update_success or not coordinator.data:
            raise ValueError("The selected MILP operation plan is unavailable")
        rows = permission_rows(coordinator.data.get("forecast"), {}, now)
    return [{**row, "allowed": 1 - row["allowed"] if mode == "inverted" else row["allowed"]} for row in rows]


def validate_hierarchy(hass, identifier, config):
    """Reject cycles before requesting any upstream coordinator refresh."""
    entries = {item.entry_id: {**item.data, **item.options} for item in all_module_entries(hass)
               if module_type(item) == MODULE_THERMAL_PLAN}
    entries[identifier] = config
    seen = set()
    current = identifier
    while current:
        if current in seen:
            raise ValueError("Circular operation-planner dependency: use a one-way hierarchy")
        seen.add(current)
        if current not in entries:
            raise ValueError("The upstream thermal planner no longer exists")
        current = constraint_dependency(hass, entries[current])


def permission_rows(rows, config, now):
    rows = validate_forecast(rows, now, "operation", True)
    if any(type(row["operation"]) not in (int, float) or row["operation"] not in (0, 1) for row in rows):
        raise ValueError("Operation forecast values must be exactly 0 or 1")
    return [{"start": row["start"], "end": row["end"],
             "allowed": int(row["operation"])} for row in rows]
