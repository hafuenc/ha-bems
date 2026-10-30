"""Resumable split into two service entries and one entry per planner."""

from copy import deepcopy
import logging

from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.start import async_at_started

from .const import (CONF_ENABLED, CONF_GROUP, CONF_ITEMS, CONF_LAYOUT, CONF_MODULE,
                    DOMAIN, GROUP_CREATOR, GROUP_IMPORTER, GROUP_TITLES, LAYOUT_FLAT, LAYOUT_SPLIT)
from .devices import SERVICE_GROUPS
from .entries import group_for_module, is_split

_LOGGER = logging.getLogger(__name__)
PENDING = "entry_split_pending"
STAGING = "entry_split_staging"


def split_specifications(records):
    """Validate all records before persisting or changing registry ownership."""
    groups = {GROUP_CREATOR: {}, GROUP_IMPORTER: {}}
    specs = {}
    for identifier, record in records.items():
        group = group_for_module(record["data"][CONF_MODULE])
        if group in SERVICE_GROUPS:
            groups[group][identifier] = record
        else:
            specs[f"planner_entry:{identifier}"] = {"title": record["title"],
                "data": {CONF_LAYOUT: LAYOUT_SPLIT, CONF_ITEMS: {identifier: record}}}
    for group, items in groups.items():
        specs[f"service_entry:{group}"] = {"title": GROUP_TITLES[group],
            "data": {CONF_LAYOUT: LAYOUT_SPLIT, CONF_GROUP: group, CONF_ITEMS: items}}
    return specs


async def async_split_entry(hass, root):
    manager = hass.config_entries
    if root.version != 1:
        raise ValueError("Unsupported HA-BEMS configuration version; entry split was not started")
    records = deepcopy(root.data.get(PENDING, root.data[CONF_ITEMS]))
    if PENDING not in root.data and root.disabled_by:
        for record in records.values():
            record["data"][CONF_ENABLED] = False
    specs = split_specifications(records)
    # Save every record before the first await. Keep this journal until all
    # ownership changes finish, so an interrupted startup can resume safely.
    manager.async_update_entry(root, data={**root.data, PENDING: records})
    if not await manager.async_unload(root.entry_id):
        raise ValueError("Could not unload HA-BEMS before splitting entries")
    targets = {f"service_entry:{GROUP_CREATOR}": root}
    for key, spec in specs.items():
        if key in targets:
            continue
        destination = next((entry for entry in manager.async_entries(DOMAIN)
                            if entry.unique_id == key), None)
        if destination is None:
            result = await manager.flow.async_init(DOMAIN, context={"source": "import"},
                data={"split_unique_id": key, "title": spec["title"],
                      "data": {**spec["data"], STAGING: True}})
            if result["type"] != "create_entry":
                raise ValueError(f"Could not create integration entry {spec['title']}")
            destination = result["result"]
        if destination.version != 1 or destination.data.get(CONF_ITEMS) != spec["data"][CONF_ITEMS]:
            raise ValueError(f"Conflicting configuration for {spec['title']}")
        targets[key] = destination
    entities, devices = er.async_get(hass), dr.async_get(hass)
    for key, spec in specs.items():
        destination = targets[key]
        for identifier, record in spec["data"][CONF_ITEMS].items():
            group = group_for_module(record["data"][CONF_MODULE])
            candidates = [entity for entity in entities.entities.values()
                          if entity.platform == DOMAIN and entity.unique_id.startswith(f"{identifier}_")]
            old_devices = [devices.async_get(entity.device_id) for entity in candidates if entity.device_id]
            device = None
            if group in SERVICE_GROUPS:
                area = next((old.area_id for old in old_devices if old and old.area_id), None)
                device = devices.async_get_or_create(config_entry_id=destination.entry_id,
                    identifiers={(DOMAIN, identifier)}, name=record["title"],
                    manufacturer="HA-BEMS", model=GROUP_TITLES[group],
                    entry_type=dr.DeviceEntryType.SERVICE)
                if area and not device.area_id:
                    devices.async_update_device(device.id, area_id=area)
            for entity in candidates:
                updates = {"config_entry_id": destination.entry_id, "config_subentry_id": None}
                if device:
                    updates["device_id"] = device.id
                entities.async_update_entity(entity.entity_id, **updates)
            if group not in SERVICE_GROUPS:
                for old in dr.async_entries_for_config_entry(devices, root.entry_id):
                    if (DOMAIN, identifier) in old.identifiers:
                        devices.async_update_device(old.id, new_config_entry_id=destination.entry_id,
                                                    new_config_subentry_id=None)
                # A partially migrated planner device may already have its owner.
    for old in list(dr.async_entries_for_config_entry(devices, root.entry_id)):
        if any((DOMAIN, f"service:{group}") in old.identifiers for group in SERVICE_GROUPS):
            if not er.async_entries_for_device(entities, old.id):
                devices.async_remove_device(old.id)
    # Registries now refer only to the final owners. Publish complete entries,
    # then load importers/helpers before planners consume their forecasts.
    for key, spec in specs.items():
        manager.async_update_entry(targets[key], data=spec["data"], options={},
            title=spec["title"], unique_id=key, minor_version=5)
    ordered = [f"service_entry:{GROUP_IMPORTER}", f"service_entry:{GROUP_CREATOR}"]
    ordered += [key for key in specs if key.startswith("planner_entry:")]
    for key in ordered:
        await manager.async_reload(targets[key].entry_id)


def async_schedule_entry_split(hass):
    roots = [entry for entry in hass.config_entries.async_entries(DOMAIN)
             if not is_split(entry) and entry.data.get(CONF_LAYOUT) == LAYOUT_FLAT]
    if not roots:
        return

    async def migrate(_hass):
        for root in roots:
            try:
                await async_split_entry(hass, root)
                from homeassistant.components import persistent_notification

                persistent_notification.async_dismiss(hass, "ha_bems_entry_migration")
            except Exception:
                _LOGGER.exception("HA-BEMS entry migration interrupted; saved configuration retained for retry")
                from homeassistant.components import persistent_notification

                persistent_notification.async_create(hass,
                    "HA-BEMS entry migration could not finish. Configuration is retained. Check the HA-BEMS logs and restart to retry.",
                    title="BEMS setup migration needs attention", notification_id="ha_bems_entry_migration")

    async_at_started(hass, migrate)
