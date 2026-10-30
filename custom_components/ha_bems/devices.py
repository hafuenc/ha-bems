"""Shared virtual service devices for reusable inputs and generated entities."""

from .const import DOMAIN, GROUP_CREATOR, GROUP_IMPORTER, GROUP_TITLES
from .entries import ModuleEntry, group_for_module, is_flat, module_type

SERVICE_GROUPS = (GROUP_IMPORTER, GROUP_CREATOR)


def service_identifier(group):
    return (DOMAIN, f"service:{group}")


def shared_group(item):
    if isinstance(item, ModuleEntry) and is_flat(item.parent):
        group = group_for_module(module_type(item))
        if group in SERVICE_GROUPS:
            return group
    return None


def shared_device_info(item):
    if (group := shared_group(item)) is None:
        return None
    from homeassistant.helpers.device_registry import DeviceEntryType

    return {"identifiers": {service_identifier(group)}, "name": GROUP_TITLES[group],
            "manufacturer": "HA-BEMS", "model": GROUP_TITLES[group],
            "entry_type": DeviceEntryType.SERVICE,
            "configuration_url": "homeassistant://config/integrations/integration/ha_bems"}


def ensure_service_devices(hass, entry):
    from homeassistant.helpers import device_registry as dr

    registry = dr.async_get(hass)
    return {group: registry.async_get_or_create(config_entry_id=entry.entry_id,
                config_subentry_id=None, identifiers={service_identifier(group)},
                name=GROUP_TITLES[group], manufacturer="HA-BEMS", model=GROUP_TITLES[group],
                entry_type=dr.DeviceEntryType.SERVICE,
                configuration_url="homeassistant://config/integrations/integration/ha_bems")
            for group in SERVICE_GROUPS}
