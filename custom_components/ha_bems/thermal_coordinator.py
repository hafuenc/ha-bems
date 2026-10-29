"""Connect reusable validated inputs to the solver-independent cost model."""

from datetime import timedelta
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (CONF_EXPORT_PRICE, CONF_IMPORT_PRICE, CONF_PV_IMPORT, CONF_TEMPERATURE,
                    CONF_PV_POWER_TYPE, MODULE_EXPORT_PRICE, MODULE_IMPORT_PRICE, MODULE_OUTDOOR_TEMP)
from .forecast import parse_timestamp
from .import_registry import read_forecast_import, read_load_input, read_pv_import
from .thermal_cost import calculate_cost_forecast

_LOGGER = logging.getLogger(__name__)


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
    if config.get("cop_model") == "lookup":
        try:
            temperatures, temperature_info = read_forecast_import(
                hass, config.get(CONF_TEMPERATURE), MODULE_OUTDOOR_TEMP, now)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
            temperature_error = str(err)
    updated = [pv_info["source_updated_at"], import_info["source_updated_at"], export_info["source_updated_at"]]
    if load is not None:
        updated.append(load_info["load_source_updated_at"])
    if temperature_info:
        updated.append(temperature_info["source_updated_at"])
    metadata = {"source_updated_at": min(updated, key=parse_timestamp),
                "pv_import_entry_id": config[CONF_PV_IMPORT], "pv_power_type": "ac",
                CONF_IMPORT_PRICE: config[CONF_IMPORT_PRICE], CONF_EXPORT_PRICE: config[CONF_EXPORT_PRICE],
                CONF_TEMPERATURE: config.get(CONF_TEMPERATURE) if config.get("cop_model") == "lookup" else None,
                "temperature_input_error": temperature_error, **load_info}
    return (pv, imports, exports, load, temperatures, temperature_error), metadata


async def async_calculate_cost(hass, config):
    now = dt_util.now()
    inputs, metadata = read_cost_inputs(hass, config, now)
    result = await hass.async_add_executor_job(calculate_cost_forecast, config, now, *inputs)
    return {**result, **metadata}


class ThermalCostCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, parent=None):
        super().__init__(hass, _LOGGER, name=entry.title, config_entry=parent or entry,
                         update_interval=timedelta(minutes=1))
        self.config = {**entry.data, **entry.options}

    async def _async_update_data(self):
        try:
            return await async_calculate_cost(self.hass, self.config)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
            raise UpdateFailed(str(err)) from err
