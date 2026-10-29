"""User-controlled FBC bypass; never sends commands to physical equipment."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, MODULE_FBC
from .entries import add_item_entities, enabled_module_entries, module_type
from .import_registry import planners


async def async_setup_entry(hass, entry, async_add_entities):
    for item in enabled_module_entries(entry):
        if module_type(item) == MODULE_FBC:
            add_item_entities(async_add_entities, [FBCEnabledSwitch(planners(hass)[item.entry_id], item)], item)


class FBCEnabledSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_name = "FBC charging limitation enabled"
    _attr_icon = "mdi:battery-clock"
    _attr_should_poll = False

    def __init__(self, coordinator, entry):
        self.coordinator = coordinator
        self._attr_is_on = None
        self._attr_unique_id = f"{entry.entry_id}_fbc_limitation_enabled"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
            manufacturer="HA-BEMS", model="Battery control strategy",
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        self._attr_is_on = not (last_state is not None and last_state.state == "off")
        await self.coordinator.async_set_limitation_enabled(self._attr_is_on)

    async def async_turn_on(self, **kwargs):
        await self._async_set_enabled(True)

    async def async_turn_off(self, **kwargs):
        await self._async_set_enabled(False)

    async def _async_set_enabled(self, enabled):
        self._attr_is_on = enabled
        self.async_write_ha_state()
        await self.coordinator.async_set_limitation_enabled(enabled)
