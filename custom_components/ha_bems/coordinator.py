"""Compute forecasts outside the event loop; publish recommendations only."""

from dataclasses import asdict
from datetime import UTC, timedelta
import logging
from zoneinfo import ZoneInfo

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_ENTITY, MODULE_FBC
from .fbc import FBCSettings, calculate_fbc
from .forecast_input import async_read_pv_forecast
from .forecast import daily_forecast
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

            values = []
            for entity_id in (self.config["soc_entity"], self.config[CONF_ENTITY]):
                state = self.hass.states.get(entity_id)
                if state is None or state.attributes.get("unit_of_measurement") != "%":
                    raise ValueError("SOC and seasonal threshold sources must be available percentages")
                values.append(validate_threshold(state.state))
            # The seasonal safeguard does not need a working PV forecast.
            if values[0] <= values[1]:
                return self._maximum_charging_plan(values[1], now)
            intervals, source_info = await async_read_pv_forecast(self.hass, self.config)
            result = await self.hass.async_add_executor_job(self._calculate_fbc, intervals, values, now)
            return {**result, **source_info}
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
            raise UpdateFailed(str(err)) from err

    def _calculate_fbc(self, intervals, values, now):
        intervals = daily_forecast(intervals, now, ZoneInfo(self.hass.config.time_zone))
        settings = FBCSettings(**{key: self.config[key] for key in (
            "capacity_kwh", "target_soc_percent", "charge_efficiency",
            "max_charge_power_w", "output_limit_w",
        )})
        result = calculate_fbc(
            [record["power_w"] for record in intervals], step_hours=0.25,
            current_soc_percent=values[0], current_month=now.month, settings=settings,
            seasonal_soc_min_threshold_percent=values[1],
        )
        profile = [
            {"start": record["start"], "end": record["end"], "power_w": value}
            for record, value in zip(intervals, result.charge_profile_w)
        ]
        return {
            "value": result.charge_limit_w, "forecast": profile,
            "generated_at": now.isoformat(), "status": "plan_ready",
            "application": "user_automation",
            **{key: value for key, value in asdict(result).items() if key != "charge_profile_w"},
        }

    def _maximum_charging_plan(self, threshold, now):
        maximum = self.config["max_charge_power_w"]
        if self.config["output_limit_w"] < maximum:
            raise ValueError("Device output cap must not be lower than maximum charging power; review battery settings")
        cursor = now.astimezone(UTC).replace(second=0, microsecond=0)
        cursor -= timedelta(minutes=cursor.minute % 15)
        end = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(UTC)
        records = []
        while cursor < end:
            stop = cursor + timedelta(minutes=15)
            records.append({"start": cursor.isoformat(), "end": stop.isoformat(), "power_w": maximum})
            cursor = stop
        return {
            "value": maximum, "forecast": records, "generated_at": now.isoformat(),
            "status": "plan_ready", "application": "user_automation",
            "limitation_disabled": True,
            "override_reasons": ["at_or_below_seasonal_soc_min_threshold"],
            "seasonal_soc_min_threshold_percent": threshold,
            "forecast_status": "not_required_for_seasonal_override",
        }
