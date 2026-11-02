"""Always-readable diagnostic curves; no extra provider or solver refreshes."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .characteristics import build_characteristic, characteristic_values
from .const import DOMAIN, MODULE_OUTDOOR_TEMP
from .devices import shared_device_info
from .forecast import parse_timestamp
from .import_registry import read_forecast_import


class ThermalCharacteristicSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _unrecorded_attributes = frozenset({"curve_points", "reference_points"})

    def __init__(self, coordinator, entry, function):
        super().__init__(coordinator)
        self._function = function
        self._config = coordinator.config
        self._curve = build_characteristic(self._config, function)
        runtime = function == "runtime_requirement"
        self._attr_name = "Runtime characteristic" if runtime else "COP characteristic"
        self._attr_unique_id = f"{entry.entry_id}_{'runtime' if runtime else 'cop'}_characteristic"
        self._attr_native_unit_of_measurement = "h/24h" if runtime else None
        self._attr_device_info = shared_device_info(entry) or {
            "identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title,
            "manufacturer": "HA-BEMS", "model": "Thermal storage management — MILP" if runtime else "Thermal runtime cost"}

    @property
    def available(self):
        # Missing live inputs must not hide the configured characteristic.
        return True

    def _current(self):
        config, function = self._config, self._function
        constant = self._curve["model"] == "constant"
        reference = config.get("runtime_temperature_entry_id" if function == "runtime_requirement" else "temperature_entry_id")
        if not reference and constant and function == "runtime_requirement":
            cost = self.hass.states.get(config.get("cost_entity"))
            if cost:
                reference = cost.attributes.get("temperature_entry_id")
        temperature, source, error = None, None, None
        if reference:
            try:
                rows, info = read_forecast_import(self.hass, reference, MODULE_OUTDOOR_TEMP, dt_util.now())
                temperature, source = rows[0]["temperature_c"], info.get("source_entity")
            except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
                error = str(err)
        if constant or temperature is not None:
            value, raw, fallback = characteristic_values(config, function, [temperature if temperature is not None else 0])[0]
            return {"current_temperature_c": temperature, "current_value": value,
                    "current_raw_value": raw, "fallback_active": fallback,
                    "status": "fallback" if fallback else "ready", "source_entity": source, "reason": None}
        # Reflect a real, current cost-model fallback, never invent a new one here.
        data = self.coordinator.data or {}
        now = dt_util.now()
        first = next((r for r in data.get("forecast", [])
                      if parse_timestamp(r["start"]) <= now < parse_timestamp(r["end"])), None)
        fallback = (function == "cop" and self.coordinator.last_update_success
                    and not data.get("using_previous_forecast") and first and first.get("cop_fallback"))
        value = config.get("fallback_cop") if fallback else None
        return {"current_temperature_c": None, "current_value": value, "current_raw_value": None,
                "fallback_active": bool(fallback), "status": "fallback" if fallback else "input_unavailable",
                "source_entity": source, "reason": error or "Outdoor temperature input is unavailable"}

    @property
    def native_value(self):
        return self._current()["current_value"]

    @property
    def extra_state_attributes(self):
        return {**self._curve, **self._current()}
