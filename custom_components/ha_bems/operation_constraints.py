"""Binary operation permissions and explicit upstream planner dependencies."""

from .const import MODULE_OPERATION_IMPORT, MODULE_THERMAL_PLAN
from .entries import all_module_entries, module_type
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


def constraint_dependency(hass, config):
    reference = config.get("constraint_entry_id")
    if not reference:
        return None
    entry = next((item for item in all_module_entries(hass)
                  if item.entry_id == reference and module_type(item) == MODULE_OPERATION_IMPORT), None)
    if entry is None:
        raise ValueError("The selected operation constraint import no longer exists")
    return upstream_planner(hass, {**entry.data, **entry.options}["forecast_entity"])


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
    meaning = config.get("operation_meaning", "running")
    if meaning not in ("running", "allowed"):
        raise ValueError("Choose whether 1 means running or operation allowed")
    return [{"start": row["start"], "end": row["end"],
             "allowed": int(1 - row["operation"] if meaning == "running" else row["operation"])} for row in rows]
