"""Connect reusable validated inputs to the solver-independent cost model."""

from copy import deepcopy
from datetime import timedelta
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (CONF_EXPORT_PRICE, CONF_IMPORT_PRICE, CONF_PV_IMPORT, CONF_TEMPERATURE,
                    CONF_PV_POWER_TYPE, MODULE_EXPORT_PRICE, MODULE_IMPORT_PRICE, MODULE_OUTDOOR_TEMP)
from .forecast import FILL_MARKER, ForecastCoverageError, parse_timestamp, validate_forecast
from .forecast_input import ForecastConfigurationError, ForecastExpired
from .import_registry import read_forecast_import, read_load_input, read_pv_import
from .thermal_cost import calculate_cost_forecast

_LOGGER = logging.getLogger(__name__)


class TemperatureRefreshPending(ValueError):
    """Allow the previous cost forecast before switching to fallback COP."""


def thermal_references(config):
    references = [config.get(key) for key in (CONF_PV_IMPORT, CONF_IMPORT_PRICE, CONF_EXPORT_PRICE)]
    if config.get("cop_model") == "lookup":
        references.append(config.get(CONF_TEMPERATURE))
    if config.get("load_forecast_input"):
        references.append(config["load_forecast_input"].partition(":")[2])
    return [reference for reference in references if reference]


def read_cost_inputs(hass, config, now):
    pv, pv_info = read_pv_import(hass, config, now)
    if pv_info[CONF_PV_POWER_TYPE] != "ac":
        raise ValueError("Select a PV AC forecast for thermal electrical operating costs")
    imports, import_info = read_forecast_import(hass, config.get(CONF_IMPORT_PRICE), MODULE_IMPORT_PRICE, now)
    exports, export_info = read_forecast_import(hass, config.get(CONF_EXPORT_PRICE), MODULE_EXPORT_PRICE, now)
    load, load_info = read_load_input(hass, config, now)
    temperatures, temperature_info, temperature_error = None, {}, None
    temperature_major_failure = False
    if config.get("cop_model") == "lookup":
        try:
            temperatures, temperature_info = read_forecast_import(
                hass, config.get(CONF_TEMPERATURE), MODULE_OUTDOOR_TEMP, now)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
            if isinstance(err, ForecastConfigurationError):
                raise
            temperature_error = str(err)
            temperature_major_failure = isinstance(err, (ForecastCoverageError, ForecastExpired))
    updated = [pv_info["source_updated_at"], import_info["source_updated_at"], export_info["source_updated_at"]]
    if load is not None:
        updated.append(load_info["load_source_updated_at"])
    if temperature_info:
        updated.append(temperature_info["source_updated_at"])
    metadata = {"source_updated_at": min(updated, key=parse_timestamp),
                "pv_import_entry_id": config[CONF_PV_IMPORT], "pv_power_type": "ac",
                CONF_IMPORT_PRICE: config[CONF_IMPORT_PRICE], CONF_EXPORT_PRICE: config[CONF_EXPORT_PRICE],
                CONF_TEMPERATURE: config.get(CONF_TEMPERATURE) if config.get("cop_model") == "lookup" else None,
                "temperature_input_error": temperature_error,
                "temperature_major_failure": temperature_major_failure, **load_info}
    return (pv, imports, exports, load, temperatures, temperature_error), metadata


async def async_calculate_cost(hass, config, defer_temperature_fallback=False, *, prepared=None, now=None):
    now = now if now is not None else dt_util.now()
    inputs, metadata = prepared if prepared is not None else read_cost_inputs(hass, config, now)
    if defer_temperature_fallback and inputs[-1] and not metadata["temperature_major_failure"]:
        raise TemperatureRefreshPending(inputs[-1])
    result = await hass.async_add_executor_job(calculate_cost_forecast, config, now, *inputs)
    return {**result, **metadata}


class ThermalCostCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, parent=None):
        super().__init__(hass, _LOGGER, name=entry.title, config_entry=parent or entry,
                         update_interval=None, always_update=False)
        self.config = {**entry.data, **entry.options}
        self._temperature_failure_since = None
        self._last_good_cost = None
        self._calculation_key = None
        self._calculation_result = None

    def _deduplicate(self, result):
        """Do not publish clock-only metadata changes as a new cost forecast."""
        def comparable(data):
            return {key: value for key, value in data.items()
                    if key != "generated_at"
                    and not key.endswith(("source_age_minutes", "input_validated_at"))}

        if self.data is not None and comparable(result) == comparable(self.data):
            return self.data
        return result

    async def _async_update_data(self):
        now = dt_util.now()
        try:
            # Always validate current input health/coverage before reusing a
            # calculation. A cached result must never hide a removed source.
            prepared = read_cost_inputs(self.hass, self.config, now)
            defer_fallback = (self._temperature_failure_since is None or
                now - self._temperature_failure_since < timedelta(hours=1))
            key = (int(now.timestamp()) // 900, self.config, prepared[0], defer_fallback)
            if key == self._calculation_key:
                result = {**self._calculation_result, **prepared[1]}
            else:
                result = await async_calculate_cost(self.hass, self.config, defer_fallback,
                                                   prepared=prepared, now=now)
                # Keep an independent key even if a provider mutates its rows.
                self._calculation_key = deepcopy(key)
                self._calculation_result = result
            if not result.get("temperature_input_error"):
                self._temperature_failure_since = None
            result["using_previous_forecast"] = False
            result = self._deduplicate(result)
            if not result.get("cop_fallback_active"):
                self._last_good_cost = result
            return result
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
            if isinstance(err, ForecastConfigurationError):
                raise UpdateFailed(str(err)) from err
            if isinstance(err, TemperatureRefreshPending) and self._temperature_failure_since is None:
                self._temperature_failure_since = now
            cached = self._last_good_cost
            if cached and now - parse_timestamp(cached["generated_at"]) <= timedelta(hours=1):
                try:
                    rows = validate_forecast(cached["forecast"], now, "cost_eur_per_timestep", False)
                except ValueError:
                    pass
                else:
                    row = rows[0]
                    return self._deduplicate({**cached, "forecast": rows, "value": row["cost_eur_per_timestep"],
                            "current_cop": row["cop"], "current_electrical_power_w": row["electrical_power_w"],
                            "filled_previous_intervals": sum(r.get(FILL_MARKER, False) for r in rows),
                            "status": "using_previous_forecast", "using_previous_forecast": True,
                            "refresh_error": str(err)})
            raise UpdateFailed(str(err)) from err
