"""Move standalone modules into category subentries without changing identity."""

from types import MappingProxyType

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.start import async_at_started

from .const import (
    CONF_ENABLED, CONF_GROUP, CONF_ITEM_ID, CONF_MODULE, DOMAIN,
    GROUP_TITLES, MODULE_FBC, MODULE_SOC,
)
from .entries import group_for_module


def _move_registrations(hass, old_id, parent, subentry):
    """Update entities before devices: queued device events must see new owners."""
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    for entity in er.async_entries_for_config_entry(entities, old_id):
        if entity.config_subentry_id not in (None, subentry.subentry_id):
            continue
        entities.async_update_entity(
            entity.entity_id, config_entry_id=parent.entry_id,
            config_subentry_id=subentry.subentry_id,
        )
    for device in dr.async_entries_for_config_entry(devices, old_id):
        if device.config_subentry_id not in (None, subentry.subentry_id):
            continue
        devices.async_update_device(
            device.id, new_config_entry_id=parent.entry_id,
            new_config_subentry_id=subentry.subentry_id,
        )


async def async_group_legacy_entries(hass):
    """Run before entry setup, retaining resumable data until registry moves finish.

    Retired entry shells are removed after HA startup, when pending setup tasks
    have finished. Their configuration already lives in the parent's subentry.
    A failed/interrupted move can safely be retried at the next startup.
    """
    manager = hass.config_entries
    entries = list(manager.async_entries(DOMAIN))
    legacy = [entry for entry in entries
              if CONF_GROUP not in entry.data and not entry.data.get("migrated_to")]
    if any(entry.version != 1 for entry in legacy):
        raise ValueError("Unsupported HA-BEMS configuration version; grouping was not started")
    # Validate every module before changing anything, including forward-version
    # modules that this installation cannot safely classify.
    categories = {entry.entry_id: group_for_module(entry.options.get(CONF_MODULE, entry.data.get(CONF_MODULE, MODULE_SOC)))
                  for entry in legacy}
    parents = {entry.data[CONF_GROUP]: entry for entry in entries if CONF_GROUP in entry.data}
    for old in legacy:
        if CONF_GROUP in old.data:
            continue  # This enabled anchor was already captured while moving a sibling.
        group = categories[old.entry_id]
        if group not in parents:
            # Prefer an enabled anchor; each child's previous disabled state is
            # retained independently by its own enabled setting.
            candidates = [entry for entry in legacy
                          if categories[entry.entry_id] == group]
            parents[group] = next((entry for entry in candidates if not entry.disabled_by), old)
        parent = parents[group]
        # Ensure an anchor's own original data is captured before replacing it.
        for source in ([parent, old] if CONF_GROUP not in parent.data and parent != old else [old]):
            source_values = {**source.data, **source.options}
            source_module = source_values.get(CONF_MODULE, MODULE_SOC)
            found = next((sub for sub in parent.subentries.values()
                          if sub.data.get(CONF_ITEM_ID) == source.entry_id), None)
            if found is None:
                source_values.update({CONF_MODULE: source_module, CONF_ITEM_ID: source.entry_id,
                                      CONF_ENABLED: not bool(source.disabled_by)})
                if source_module == MODULE_FBC:
                    source_values.pop("output_limit_w", None)
                found = ConfigSubentry(
                    data=MappingProxyType(source_values), title=source.title,
                    subentry_type=group, unique_id=source.entry_id,
                )
                manager.async_add_subentry(parent, found)
            # No awaits between registering the destination and moving ownership.
            _move_registrations(hass, source.entry_id, parent, found)
        if CONF_GROUP not in parent.data:
            manager.async_update_entry(
                parent, title=GROUP_TITLES[group], data={CONF_GROUP: group}, options={},
                unique_id=f"group:{group}", minor_version=3,
            )
        if old != parent:
            manager.async_update_entry(old, data={"migrated_to": parent.entry_id}, options={}, minor_version=3)

    async def remove_retired(_hass):
        for entry in list(manager.async_entries(DOMAIN)):
            if entry.data.get("migrated_to"):
                await manager.async_remove(entry.entry_id)

    if any(entry.data.get("migrated_to") for entry in manager.async_entries(DOMAIN)):
        async_at_started(hass, remove_retired)
