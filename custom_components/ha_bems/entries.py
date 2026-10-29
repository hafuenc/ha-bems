"""Flatten grouped subentries into stable module identities for planning."""

from dataclasses import dataclass

from .const import CONF_ENABLED, CONF_GROUP, CONF_ITEM_ID, CONF_MODULE, DOMAIN, GROUP_MODULES, MODULE_SOC


def group_for_module(module):
    for group, modules in GROUP_MODULES.items():
        if module in modules:
            return group
    raise ValueError(f"Unsupported HA-BEMS module: {module}")


@dataclass
class ModuleEntry:
    """Expose item configuration while delegating lifecycle to its parent."""

    parent: object
    subentry: object

    @property
    def entry_id(self):
        # Retaining the legacy ID preserves entity unique IDs, device identifiers,
        # shared-input references and dispatcher subscriptions through migration.
        return self.subentry.data.get(CONF_ITEM_ID, self.subentry.subentry_id)

    @property
    def title(self):
        return self.subentry.title

    @property
    def data(self):
        return self.subentry.data

    @property
    def options(self):
        return {}

    @property
    def enabled(self):
        return self.data.get(CONF_ENABLED, True) and not getattr(self.parent, "disabled_by", None)

    @property
    def config_subentry_id(self):
        return self.subentry.subentry_id

    def async_on_unload(self, callback):
        return self.parent.async_on_unload(callback)


def module_entries(entry):
    """Include a legacy entry only for upgrade compatibility."""
    if entry.data.get("migrated_to"):
        return []
    if CONF_GROUP not in entry.data:
        return [entry]
    allowed = GROUP_MODULES[entry.data[CONF_GROUP]]
    return [ModuleEntry(entry, subentry) for subentry in entry.subentries.values()
            if subentry.data.get(CONF_MODULE) in allowed]


def all_module_entries(hass):
    return [item for parent in hass.config_entries.async_entries(DOMAIN)
            for item in module_entries(parent)]


def enabled_module_entries(entry):
    return [item for item in module_entries(entry)
            if item_enabled(item)]


def item_enabled(item):
    if isinstance(item, ModuleEntry):
        return item.enabled
    return item.data.get(CONF_ENABLED, True) and not getattr(item, "disabled_by", None)


def module_type(entry):
    return entry.options.get(CONF_MODULE, entry.data.get(CONF_MODULE, MODULE_SOC))


def add_item_entities(async_add_entities, entities, item):
    """Associate entities/devices with their actual HA subentry."""
    if isinstance(item, ModuleEntry):
        async_add_entities(entities, config_subentry_id=item.config_subentry_id)
    else:
        async_add_entities(entities)
