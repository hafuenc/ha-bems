"""Explicit whole-sequence optimization outside the regular clock."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .const import MODULE_OPTIMIZATION_MANAGER
from .entries import enabled_module_entries, module_type
from .optimization import ensure_runtime
from .optimization_entity import device_info


async def async_setup_entry(hass, entry, async_add_entities):
    if any(module_type(item) == MODULE_OPTIMIZATION_MANAGER for item in enabled_module_entries(entry)):
        async_add_entities([RecalculateButton(ensure_runtime(hass))])


class RecalculateButton(ButtonEntity):
    _attr_name = "Recalculate now"
    _attr_has_entity_name = True
    _attr_unique_id = "ha_bems_optimization_manager_recalculate"
    _attr_icon = "mdi:refresh"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, control):
        self.control = control
        self._attr_device_info = device_info()

    async def async_press(self):
        # Return promptly; the status entity tracks progress. The manager rejects overlaps.
        self.hass.async_create_task(self.control.async_run("manual"))
