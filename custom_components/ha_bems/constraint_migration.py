"""Move legacy importer interpretation into each consuming MILP's settings."""

from copy import deepcopy

from .const import CONF_ITEMS, CONF_MODULE, DOMAIN, MODULE_OPERATION_IMPORT, MODULE_THERMAL_PLAN
from .entries import all_module_entries, module_type
from .operation_constraints import constraint_selection


def migrate_constraint_settings(hass):
    """Prepare all changes before removing any legacy importer interpretation.

    Planner-containing entries are saved first, so an interrupted upgrade can
    retry without losing the old effective inversion. Each entry update is atomic.
    """
    manager = hass.config_entries
    modes = {item.entry_id: {**item.data, **item.options}.get("operation_meaning", "running")
             for item in all_module_entries(hass) if module_type(item) == MODULE_OPERATION_IMPORT}

    def normalize(values):
        values = deepcopy(dict(values))
        module = values.get(CONF_MODULE)
        if module == MODULE_THERMAL_PLAN:
            source, mode = constraint_selection(hass, values, modes)
            values.update(constraint_source=source, constraint_mode=mode)
            values.pop("constraint_entry_id", None)
        elif module == MODULE_OPERATION_IMPORT:
            values.pop("operation_meaning", None)
        return values

    changes = []
    for entry in manager.async_entries(DOMAIN):
        if entry.data.get("migrated_to"):
            continue
        if CONF_ITEMS in entry.data:
            data = deepcopy(dict(entry.data))
            has_planner = False
            for key in (CONF_ITEMS, "entry_split_pending"):
                if key not in data:
                    continue
                for record in data[key].values():
                    has_planner |= record["data"].get(CONF_MODULE) == MODULE_THERMAL_PLAN
                    record["data"] = normalize(record["data"])
            if data != entry.data:
                changes.append((has_planner, "entry", entry, data, dict(entry.options)))
        elif hasattr(entry, "subentries") and entry.subentries:
            for subentry in entry.subentries.values():
                data = normalize(subentry.data)
                if data != subentry.data:
                    changes.append((data.get(CONF_MODULE) == MODULE_THERMAL_PLAN, "subentry", (entry, subentry), data, None))
        elif {**entry.data, **entry.options}.get(CONF_MODULE) in (MODULE_OPERATION_IMPORT, MODULE_THERMAL_PLAN):
            data = normalize({**entry.data, **entry.options})
            if data != entry.data or entry.options:
                changes.append((data.get(CONF_MODULE) == MODULE_THERMAL_PLAN, "entry", entry, data, {}))
    for _, kind, target, data, options in sorted(changes, key=lambda change: not change[0]):
        if kind == "subentry":
            manager.async_update_subentry(*target, data=data)
        else:
            manager.async_update_entry(target, data=data, options=options)
