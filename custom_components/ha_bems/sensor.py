"""Expose a shared threshold for FBC and future battery modules."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENTITY, CONF_MODULE, CONF_SOURCE, DOMAIN, MODULE_FBC, MODULE_LOAD,
    MODULE_SOC, MODULE_PV_IMPORT, CONF_PV_IMPORT, SEASONAL_SOC_NAME, SOURCE_ENTITY, SOURCE_MONTHLY,
)
from .coordinator import BEMSCoordinator
from .import_registry import coordinators, import_signal
from .seasonal_soc import resolve_threshold


async def async_setup_entry(hass, entry, async_add_entities):
    module = entry.options.get(CONF_MODULE, entry.data.get(CONF_MODULE, MODULE_SOC))
    if module == MODULE_SOC:
        async_add_entities([SeasonalSOCSensor(entry)])
        return
    if module == MODULE_PV_IMPORT:
        async_add_entities([ImportedPVStatusSensor(coordinators(hass)[entry.entry_id], entry)])
        return
    coordinator = BEMSCoordinator(hass, entry, module)
    # Keep the entry configurable and its sensor visible even if a source is
    # initially missing or the forecast helper needs correction.
    await coordinator.async_refresh()
    async_add_entities([BEMSForecastSensor(coordinator, entry, module)])


class SeasonalSOCSensor(SensorEntity):
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
    # Rolling plans remain available live without duplicating them in recorder.
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(self, coordinator, entry, module):
        super().__init__(coordinator)
        self._module = module
        self._attr_unique_id = f"{entry.entry_id}_{module}"
        self._attr_name = "FBC recommended charging limit" if module == MODULE_FBC else "Non-flexible load profile"
        self._attr_icon = "mdi:battery-charging" if module == MODULE_FBC else "mdi:home-lightning-bolt"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
            manufacturer="HA-BEMS", model="Battery control strategy" if module == MODULE_FBC else "BEMS entity creator",
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        @callback
        def refresh(_event):
            self.hass.async_create_task(self.coordinator.async_request_refresh())

        self.async_on_remove(async_track_time_change(
            self.hass, refresh, minute=[0, 15, 30, 45], second=0,
        ))
        if self._module == MODULE_FBC:
            config = self.coordinator.config
            self.async_on_remove(async_track_state_change_event(
                self.hass, [config["soc_entity"], config[CONF_ENTITY]], refresh,
            ))
            if entry_id := config.get(CONF_PV_IMPORT):
                self.async_on_remove(async_dispatcher_connect(
                    self.hass, import_signal(entry_id), lambda: refresh(None),
                ))
                # Also pick up an importer that finished loading while this
                # entity was being added during HA startup.
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


class ImportedPVStatusSensor(CoordinatorEntity, SensorEntity):
    """Expose input health without duplicating the source's forecast attribute."""

    _attr_name = "Input status"
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:import"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_input_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
            manufacturer="HA-BEMS", model="BEMS entity importer",
        )

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
            "input_type": "pv_generation_forecast",
            "source_entity": self.coordinator.config["forecast_entity"],
        }
        if self.coordinator.last_update_success and self.coordinator.data:
            attributes.update({key: value for key, value in self.coordinator.data.items() if key != "forecast"})
        else:
            attributes["reason"] = str(self.coordinator.last_exception)
        return attributes
