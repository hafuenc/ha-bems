"""Expose a shared threshold for FBC and future battery modules."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import MATCH_ALL
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADDITIONAL_LOADS, CONF_ENTITY, CONF_LOAD_INPUT, CONF_MODULE, CONF_SOURCE, DOMAIN, MODULE_FBC, MODULE_LOAD,
    MODULE_LOAD_IMPORT, MODULE_SOC, MODULE_PV_IMPORT, CONF_PV_IMPORT,
    FORECAST_IMPORT_MODULES, CONF_PV_POWER_TYPE, MODULE_THERMAL_COST, MODULE_THERMAL_PLAN,
    SEASONAL_SOC_NAME, SOURCE_ENTITY, SOURCE_MONTHLY, MODULE_EEG_WIND,
)
from .import_registry import coordinators, import_signal, planners
from .seasonal_soc import resolve_threshold
from .entries import add_item_entities, enabled_module_entries, module_type, is_split
from .devices import shared_device_info
from .thermal_plan_entity import ThermalPlanEntity


async def async_setup_entry(hass, entry, async_add_entities):
    for item in enabled_module_entries(entry):
        await _async_setup_item(hass, item, async_add_entities)


async def _async_setup_item(hass, entry, async_add_entities):
    module = module_type(entry)
    if module == MODULE_SOC:
        add_item_entities(async_add_entities, [SeasonalSOCSensor(entry)], entry)
        return
    if module == MODULE_EEG_WIND:
        add_item_entities(async_add_entities, [WindEnergySensor(coordinators(hass)[entry.entry_id], entry)], entry)
        return
    if module in FORECAST_IMPORT_MODULES:
        add_item_entities(async_add_entities, [ImportedForecastStatusSensor(coordinators(hass)[entry.entry_id], entry)], entry)
        return
    coordinator = planners(hass)[entry.entry_id]
    # Keep the entry configurable and its sensor visible even if a source is
    # initially missing or the forecast helper needs correction.
    await coordinator.async_refresh()
    if module == MODULE_THERMAL_PLAN:
        from .thermal_plan_sensors import thermal_plan_sensors

        add_item_entities(async_add_entities,
            [ThermalStorageSensor(coordinator, entry), *thermal_plan_sensors(coordinator, entry)], entry)
        return
    entities = [BEMSForecastSensor(coordinator, entry, module)]
    if module == MODULE_THERMAL_COST:
        from .characteristic_sensor import ThermalCharacteristicSensor

        entities.append(ThermalCharacteristicSensor(coordinator, entry, "cop"))
    add_item_entities(async_add_entities, entities, entry)


class WindEnergySensor(CoordinatorEntity, SensorEntity):
    """Publish the generated EEG energy directly for forecasts and runtime costs."""

    _unrecorded_attributes = frozenset({MATCH_ALL})
    _attr_name = "EEG available wind energy"
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "kWh/timestep"
    _attr_suggested_display_precision = 4
    _attr_icon = "mdi:wind-turbine"
    _attr_should_poll = False

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_eeg_wind_energy"
        self._attr_device_info = shared_device_info(entry) or DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
            manufacturer="HA-BEMS", model="BEMS entity creator")
        if self._attr_device_info and hasattr(entry, "parent"):
            self._attr_name = entry.title

    @property
    def native_value(self):
        if not self.coordinator.last_update_success:
            return None
        return (self.coordinator.data or {}).get("value")

    @property
    def extra_state_attributes(self):
        attributes = {key: value for key, value in (self.coordinator.data or {}).items() if key != "value"}
        attributes["module"] = MODULE_EEG_WIND
        if not self.coordinator.last_update_success:
            attributes.pop("forecast", None)
            attributes.update(status="unavailable", reason=str(self.coordinator.last_exception))
        return attributes


class SeasonalSOCSensor(SensorEntity):
    _unrecorded_attributes = frozenset({MATCH_ALL})
    _attr_name = SEASONAL_SOC_NAME
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:battery-lock"
    _attr_should_poll = False

    def __init__(self, entry):
        self._config = {**entry.data, **entry.options}
        self._attr_unique_id = f"{entry.entry_id}_seasonal_soc_min_threshold"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
            manufacturer="HA-BEMS", model="Battery settings",
        )
        if info := shared_device_info(entry):
            self._attr_device_info = info
            self._attr_name = "SOC min threshold" if is_split(entry.parent) else f"{entry.title} SOC min threshold"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self._config.get(CONF_SOURCE) == SOURCE_ENTITY:
            self.async_on_remove(async_track_state_change_event(
                self.hass, [self._config[CONF_ENTITY]], self._async_refresh,
            ))
        else:
            # Local midnight also handles the first day of each calendar month.
            self.async_on_remove(async_track_time_change(
                self.hass, self._async_refresh, hour=0, minute=0, second=0,
            ))
        self._refresh()

    @callback
    def _async_refresh(self, _event):
        self._refresh()
        self.async_write_ha_state()

    def _refresh(self):
        source_entity = self._config.get(CONF_ENTITY)
        state = self.hass.states.get(source_entity) if source_entity else None
        try:
            value = resolve_threshold(
                self._config, dt_util.now().month,
                state.state if state else None,
                state.attributes.get("unit_of_measurement") if state else None,
            )
        except ValueError:
            self._attr_available = False
            self._attr_native_value = None
        else:
            self._attr_available = True
            self._attr_native_value = value
        self._attr_extra_state_attributes = {
            "source": self._config.get(CONF_SOURCE, SOURCE_MONTHLY),
            "source_entity": self._config.get(CONF_ENTITY)
                if self._config.get(CONF_SOURCE) == SOURCE_ENTITY else None,
        }


class BEMSForecastSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "W"
    _attr_should_poll = False
    # Keep live attributes for consumers; recorder retains state and core metadata.
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, coordinator, entry, module):
        super().__init__(coordinator)
        self._module = module
        self._attr_unique_id = f"{entry.entry_id}_{module}"
        self._attr_name = "FBC recommended charging limit" if module == MODULE_FBC else "Non-flexible load profile"
        self._attr_icon = "mdi:battery-charging" if module == MODULE_FBC else "mdi:home-lightning-bolt"
        if module == MODULE_THERMAL_COST:
            self._attr_name = "Runtime cost"
            self._attr_icon = "mdi:heat-pump"
            self._attr_native_unit_of_measurement = "EUR/timestep"
            self._attr_suggested_display_precision = 4
        if module == MODULE_LOAD:
            self._attr_suggested_display_precision = 0
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
            manufacturer="HA-BEMS", model=("Battery control strategy" if module == MODULE_FBC
                else "Thermal runtime cost" if module == MODULE_THERMAL_COST else "BEMS entity creator"),
        )
        if info := shared_device_info(entry):
            self._attr_device_info = info
            self._attr_name = ("Runtime cost" if module == MODULE_THERMAL_COST else "Non-flexible load profile") if is_split(entry.parent) else (f"{entry.title} runtime cost" if module == MODULE_THERMAL_COST else entry.title)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        @callback
        def refresh(_event=None):
            self.hass.async_create_task(self.coordinator.async_request_refresh())

        self.async_on_remove(async_track_time_change(
            self.hass, refresh, minute=[0, 15, 30, 45], second=0,
        ))
        if self._module == MODULE_THERMAL_COST:
            from .thermal_coordinator import thermal_references

            for reference in thermal_references(self.coordinator.config):
                self.async_on_remove(async_dispatcher_connect(
                    self.hass, import_signal(reference), refresh,
                ))
            priority_ids = {ref.partition(":")[2] for ref in self.coordinator.config.get(CONF_ADDITIONAL_LOADS, [])
                            if ref.startswith("planner:")}
            if priority_ids:
                from .import_registry import input_entries

                priority_cost_entities = {{**item.data, **item.options}.get("cost_entity")
                    for item in input_entries(self.hass, MODULE_THERMAL_PLAN) if item.entry_id in priority_ids}

                @callback
                def priority_changed(event):
                    # Watch published outputs, including plans loaded after this sensor.
                    states = [event.data.get("old_state"), event.data.get("new_state")]
                    if (event.data.get("entity_id") in priority_cost_entities or
                            any(state and state.attributes.get("planner_item_id") in priority_ids for state in states)):
                        refresh(event)

                self.async_on_remove(async_track_state_change_event(self.hass, MATCH_ALL, priority_changed))
            refresh(None)  # Inputs may have loaded after the thermal category.
        if self._module == MODULE_FBC:
            config = self.coordinator.config
            self.async_on_remove(async_track_state_change_event(
                self.hass, [config["soc_entity"], config[CONF_ENTITY]], refresh,
            ))
            if entry_id := config.get(CONF_PV_IMPORT):
                self.async_on_remove(async_dispatcher_connect(
                    self.hass, import_signal(entry_id), refresh,
                ))
                # Also pick up an importer that finished loading while this
                # entity was being added during HA startup.
                refresh(None)
            if load_input := config.get(CONF_LOAD_INPUT):
                self.async_on_remove(async_dispatcher_connect(
                    self.hass, import_signal(load_input.partition(":")[2]), refresh,
                ))
                refresh(None)

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("value")

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        attributes = {key: value for key, value in data.items() if key != "value"}
        attributes["interval_minutes"] = 15
        attributes["module"] = self._module
        if self._module == MODULE_LOAD:
            attributes["annual_consumption_kwh"] = self.coordinator.config["annual_consumption_kwh"]
            attributes["load_profile"] = self.coordinator.config["load_profile"]
        if not self.coordinator.last_update_success:
            # Do not present a previous recommendation as a valid current plan.
            attributes.pop("forecast", None)
            attributes["status"] = "unavailable"
            attributes["reason"] = str(self.coordinator.last_exception)
        return attributes


class ThermalStorageSensor(ThermalPlanEntity, SensorEntity):
    _attr_name = "Storage duration"
    _attr_native_unit_of_measurement = "h"
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:storage-tank"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_storage_duration"

    @property
    def native_value(self):
        if not self.available:
            return None
        from .thermal_plan_coordinator import projected_state

        return projected_state(self.coordinator.data, dt_util.now())[0]

    @property
    def extra_state_attributes(self):
        attributes = self.plan_attributes
        attributes.update({"forecast_value_key": "duration_h", "forecast_unit": "h"})
        if "forecast" in attributes:
            attributes["forecast"] = [{"start": row["start"], "end": row["end"],
                "effective_start": row["effective_start"], "duration_h": row["storage_start_h"],
                "duration_end_h": row["storage_end_h"]} for row in attributes["forecast"]]
        return attributes


class ImportedForecastStatusSensor(CoordinatorEntity, SensorEntity):
    """Expose input health without duplicating the source's forecast attribute."""

    _unrecorded_attributes = frozenset({MATCH_ALL})

    _attr_name = "Input status"
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:import"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_input_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
            manufacturer="HA-BEMS", model="BEMS forecast importer",
        )
        if info := shared_device_info(entry):
            self._attr_device_info = info
            self._attr_name = "Input status" if is_split(entry.parent) else f"{entry.title} input status"

    @property
    def available(self):
        # The status itself must remain readable when the input has failed.
        return True

    @property
    def native_value(self):
        return self.coordinator.input_status

    @property
    def extra_state_attributes(self):
        attributes = {
            "input_type": {
                MODULE_PV_IMPORT: "pv_generation_forecast",
                MODULE_LOAD_IMPORT: "building_load_forecast",
            }.get(self.coordinator.config.get(CONF_MODULE), self.coordinator.config.get(CONF_MODULE)),
            "source_entity": self.coordinator.config["forecast_entity"],
        }
        if self.coordinator.config.get(CONF_MODULE) == MODULE_PV_IMPORT:
            attributes[CONF_PV_POWER_TYPE] = self.coordinator.config.get(CONF_PV_POWER_TYPE, "unspecified")
        if self.coordinator.last_update_success and self.coordinator.data:
            attributes.update({key: value for key, value in self.coordinator.data.items() if key != "forecast"})
        else:
            attributes["reason"] = str(self.coordinator.last_exception)
        return attributes
