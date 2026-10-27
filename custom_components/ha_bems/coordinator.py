"""Compute forecasts outside the event loop; publish recommendations only."""

from dataclasses import asdict
from datetime import timedelta
import logging
from zoneinfo import ZoneInfo

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_ENTITY, MODULE_FBC
from .fbc import FBCSettings, calculate_fbc
from .forecast import prepare_forecast
from .load_profile import load_forecast
from .seasonal_soc import validate_threshold

_LOGGER = logging.getLogger(__name__)


class BEMSCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, module):
        super().__init__(hass, _LOGGER, name=entry.title, update_interval=None)
        self.config = {**entry.data, **entry.options}
        self.module = module

    async def _async_update_data(self):
        now = dt_util.now()
        try:
            if self.module != MODULE_FBC:
                records = await self.hass.async_add_executor_job(
                    load_forecast, self.config, now, self.hass.config.time_zone,
                )
                return {"value": records[0]["power_w"], "forecast": records,
                        "generated_at": now.isoformat(), "status": "estimated_load_profile"}

            forecast = self.hass.states.get(self.config["forecast_entity"])
            if forecast is None or forecast.state in ("unknown", "unavailable"):
                raise ValueError("DC PV forecast source is unavailable")
            if now - forecast.last_updated > timedelta(minutes=self.config["forecast_max_age_minutes"]):
                raise ValueError("DC PV forecast source is stale")
            values = []
            for entity_id in (self.config["soc_entity"], self.config[CONF_ENTITY]):
                state = self.hass.states.get(entity_id)
                if state is None or state.attributes.get("unit_of_measurement") != "%":
                    raise ValueError("SOC and seasonal threshold sources must be available percentages")
                values.append(validate_threshold(state.state))
            records = forecast.attributes.get(self.config["forecast_attribute"])
            return await self.hass.async_add_executor_job(self._calculate_fbc, records, values, now)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
            raise UpdateFailed(str(err)) from err

    def _calculate_fbc(self, records, values, now):
        intervals = prepare_forecast(records, self.config, now, ZoneInfo(self.hass.config.time_zone))
        settings = FBCSettings(**{key: self.config[key] for key in (
            "capacity_kwh", "target_soc_percent", "charge_efficiency",
            "max_charge_power_w", "output_limit_w",
        )})
        result = calculate_fbc(
            [record["power_dc_w"] for record in intervals], step_hours=0.25,
            current_soc_percent=values[0], current_month=now.month, settings=settings,
            seasonal_soc_min_threshold_percent=values[1],
        )
        profile = [
            {"start": record["start"], "end": record["end"], "power_w": value}
            for record, value in zip(intervals, result.charge_profile_w)
        ]
        return {
            "value": result.charge_limit_w, "forecast": profile,
            "generated_at": now.isoformat(), "status": "observation_only",
            **{key: value for key, value in asdict(result).items() if key != "charge_profile_w"},
        }
