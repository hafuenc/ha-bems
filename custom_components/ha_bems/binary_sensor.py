"""Native on/off history with numeric operation forecasts for automations."""

from homeassistant.components.binary_sensor import BinarySensorEntity

from .const import MODULE_THERMAL_PLAN
from .entries import add_item_entities, enabled_module_entries, module_type
from .import_registry import planners
from .thermal_plan_entity import ThermalPlanEntity


async def async_setup_entry(hass, entry, async_add_entities):
    for item in enabled_module_entries(entry):
        if module_type(item) == MODULE_THERMAL_PLAN:
            add_item_entities(async_add_entities,
                [ThermalOperationSensor(planners(hass)[item.entry_id], item)], item)


class ThermalOperationSensor(ThermalPlanEntity, BinarySensorEntity):
    _attr_name = "Operation plan"
    _attr_icon = "mdi:heat-pump"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_operation_plan"

    @property
    def is_on(self):
        row = self.current_row
        return bool(row["operation"]) if row else None

    @property
    def extra_state_attributes(self):
        attributes = self.plan_attributes
        attributes.update({"forecast_value_key": "operation", "forecast_unit": "0/1"})
        return attributes
