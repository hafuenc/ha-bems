"""Consolidate category entries and per-helper devices into one installation."""

from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.start import async_at_started

from .const import (CONF_ENABLED, CONF_GROUP, CONF_ITEM_ID, CONF_ITEMS, CONF_LAYOUT,
                    CONF_MODULE, DOMAIN, LAYOUT_FLAT)
from .devices import SERVICE_GROUPS, ensure_service_devices
from .entries import group_for_module, is_flat

PENDING = "device_migration_pending"


def remove_item_registrations(hass, entry, identifier):
    """Remove just this item's entities; keep shared service devices intact."""
    entities, devices = er.async_get(hass), dr.async_get(hass)
    for entity in list(er.async_entries_for_config_entry(entities, entry.entry_id)):
        if entity.unique_id.startswith(f"{identifier}_"):
            entities.async_remove(entity.entity_id)
    for device in list(dr.async_entries_for_config_entry(devices, entry.entry_id)):
        if (DOMAIN, identifier) in device.identifiers and not er.async_entries_for_device(entities, device.id):
            devices.async_remove_device(device.id)


async def async_consolidate_entries(hass):
    manager = hass.config_entries
    entries = [entry for entry in manager.async_entries(DOMAIN) if not entry.data.get("migrated_to")]
    if not entries:
        return
    if any(entry.version != 1 for entry in entries):
        raise ValueError("Unsupported HA-BEMS configuration version; consolidation was not started")
    if sum(is_flat(entry) for entry in entries) > 1:
        raise ValueError("Multiple flat HA-BEMS installations found; consolidation was not started")
    root = next((entry for entry in entries if is_flat(entry)), None)
    if root is None:
        root = next((entry for entry in entries if not entry.disabled_by), entries[0])
    records = dict(root.data.get(CONF_ITEMS, {})) if is_flat(root) else {}
    pending = list(root.data.get(PENDING, [])) if is_flat(root) else []
    for source in entries:
        if is_flat(source):
            continue
        if CONF_GROUP not in source.data:
            raise ValueError("Standalone entries must be migrated before device consolidation")
        for subentry in source.subentries.values():
            values = dict(subentry.data)
            group_for_module(values[CONF_MODULE])  # Validate before changing any registry ownership.
            identifier = values.get(CONF_ITEM_ID, subentry.subentry_id)
            values[CONF_ITEM_ID] = identifier
            values[CONF_ENABLED] = values.get(CONF_ENABLED, True) and not bool(source.disabled_by)
            record = {"title": subentry.title, "data": values}
            if identifier in records and records[identifier] != record:
                raise ValueError(f"Conflicting saved configuration for item {identifier}")
            records[identifier] = record
            plan = {"source_entry_id": source.entry_id, "source_subentry_id": subentry.subentry_id,
                    "item_id": identifier}
            if plan not in pending:
                pending.append(plan)
    if not pending and is_flat(root):
        return
    # Save every item and the resumable ownership plan before moving anything.
    manager.async_update_entry(root, title="HA-BEMS", unique_id="ha_bems_installation",
        data={CONF_LAYOUT: LAYOUT_FLAT, CONF_ITEMS: records, PENDING: pending}, options={}, minor_version=4)
    services = ensure_service_devices(hass, root)
    shared_ids = {device.id for device in services.values()}
    entities, devices = er.async_get(hass), dr.async_get(hass)
    for plan in pending:
        identifier = plan["item_id"]
        group = group_for_module(records[identifier]["data"][CONF_MODULE])
        source_id = plan["source_entry_id"]
        old_devices = set()
        # Match stable module identities even after a partially completed move.
        candidates = list(er.async_entries_for_config_entry(entities, source_id))
        if source_id != root.entry_id:
            candidates += list(er.async_entries_for_config_entry(entities, root.entry_id))
        for entity in candidates:
            if not entity.unique_id.startswith(f"{identifier}_"):
                continue
            old_device = devices.async_get(entity.device_id) if entity.device_id else None
            updates = {"config_entry_id": root.entry_id, "config_subentry_id": None}
            if group in services:
                updates["device_id"] = services[group].id
                if old_device and old_device.id not in shared_ids:
                    old_devices.add(old_device.id)
                    if not entity.area_id and old_device.area_id:
                        updates["area_id"] = old_device.area_id
            entities.async_update_entity(entity.entity_id, **updates)
        for device in list(dr.async_entries_for_config_entry(devices, source_id)):
            if (DOMAIN, identifier) not in device.identifiers:
                continue
            if group in SERVICE_GROUPS:
                old_devices.add(device.id)
            else:
                devices.async_update_device(device.id, new_config_entry_id=root.entry_id,
                                            new_config_subentry_id=None)
        for device_id in old_devices - shared_ids:
            if not er.async_entries_for_device(entities, device_id):
                devices.async_remove_device(device_id)
    # Remove old subentries only after their entities/devices have new owners.
    for source in entries:
        for subentry_id in list(source.subentries):
            manager.async_remove_subentry(source, subentry_id)
        if source.entry_id != root.entry_id:
            manager.async_update_entry(source, data={"migrated_to": root.entry_id}, options={}, minor_version=4)
    manager.async_update_entry(root, data={CONF_LAYOUT: LAYOUT_FLAT, CONF_ITEMS: records}, options={}, minor_version=4)

    async def remove_retired(_hass):
        for entry in list(manager.async_entries(DOMAIN)):
            if entry.data.get("migrated_to"):
                await manager.async_remove(entry.entry_id)

    if any(entry.data.get("migrated_to") for entry in manager.async_entries(DOMAIN)):
        async_at_started(hass, remove_retired)
