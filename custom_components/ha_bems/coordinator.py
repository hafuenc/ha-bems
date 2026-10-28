"""Compute forecasts outside the event loop; publish recommendations only."""

from dataclasses import asdict
from datetime import UTC, timedelta
import logging
from zoneinfo import ZoneInfo

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_ENTITY, CONF_LOAD_INPUT, MODULE_FBC
from .fbc import FBCSettings, calculate_fbc
from .forecast import daily_forecast, surplus_forecast
from .import_registry import read_load_input, read_pv_import
from .load_profile import load_forecast
from .seasonal_soc import validate_threshold

_LOGGER = logging.getLogger(__name__)


class BEMSCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, module):
        super().__init__(hass, _LOGGER, name=entry.title, update_interval=None)
        self.config = {**entry.data, **entry.options}
        self.config.pop("battery_strategy", None)
        self.module = module
        self.limitation_enabled = None  # Set by the restored switch before the first FBC plan.

    async def async_set_limitation_enabled(self, enabled):
        self.limitation_enabled = enabled
        await self.async_request_refresh()

    async def _async_update_data(self):
        now = dt_util.now()
        try:
            if self.module != MODULE_FBC:
                records = await self.hass.async_add_executor_job(
                    load_forecast, self.config, now, self.hass.config.time_zone,
                )
                return {"value": records[0]["power_w"], "forecast": records,
                        "generated_at": now.isoformat(), "status": "estimated_load_profile",
                        "schema_version": 1, "source_updated_at": now.isoformat()}

            if self.limitation_enabled is None:
                raise ValueError("Waiting for the FBC charging limitation switch to initialise")
            if not self.limitation_enabled:
                return self._maximum_charging_plan(None, now, "disabled_by_switch")

            values = []
            for entity_id in (self.config["soc_entity"], self.config[CONF_ENTITY]):
                state = self.hass.states.get(entity_id)
                if state is None or state.attributes.get("unit_of_measurement") != "%":
                    raise ValueError("SOC and seasonal threshold sources must be available percentages")
                values.append(validate_threshold(state.state))
            # The seasonal safeguard does not need a working PV forecast.
            if values[0] <= values[1]:
                return self._maximum_charging_plan(values[1], now)
            intervals, source_info = read_pv_import(self.hass, self.config, now)
            load_intervals, load_info = read_load_input(self.hass, self.config, now)
            result = await self.hass.async_add_executor_job(
                self._calculate_fbc, intervals, values, now, load_intervals,
            )
            # A calculation already running when the user switches off must
            # not overwrite the maximum-power bypass plan on completion.
            if not self.limitation_enabled:
                return self._maximum_charging_plan(None, now, "disabled_by_switch")
            return {**result, **source_info, **load_info}
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
            if self.module == MODULE_FBC and self.limitation_enabled is False:
                return self._maximum_charging_plan(None, now, "disabled_by_switch")
            raise UpdateFailed(str(err)) from err

    def _calculate_fbc(self, intervals, values, now, load_intervals=None):
        intervals = daily_forecast(intervals, now, ZoneInfo(self.hass.config.time_zone))
        if load_intervals is not None:
            intervals = surplus_forecast(intervals, load_intervals)
        settings = FBCSettings(**{key: self.config[key] for key in (
            "capacity_kwh", "target_soc_percent", "charge_efficiency",
            "max_charge_power_w",
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
        details = {key: value for key, value in asdict(result).items() if key != "charge_profile_w"}
        if load_intervals is not None:
            details["override_reasons"] = [
                "first_charge_equals_first_surplus" if reason == "first_charge_equals_first_pv" else reason
                for reason in details["override_reasons"]
            ]
        return {
            "value": result.charge_limit_w, "forecast": profile,
            "generated_at": now.isoformat(), "status": "plan_ready",
            "application": "user_automation",
            "charging_limitation_enabled": True,
            "calculation_input": "pv_surplus" if load_intervals is not None else "pv_generation",
            **details,
        }

    def _maximum_charging_plan(self, threshold, now, reason="at_or_below_seasonal_soc_min_threshold"):
        maximum = self.config["max_charge_power_w"]
        cursor = now.astimezone(UTC).replace(second=0, microsecond=0)
        cursor -= timedelta(minutes=cursor.minute % 15)
        local = now.astimezone(ZoneInfo(self.hass.config.time_zone))
        end = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(UTC)
        records = []
        while cursor < end:
            stop = cursor + timedelta(minutes=15)
            records.append({"start": cursor.isoformat(), "end": stop.isoformat(), "power_w": maximum})
            cursor = stop
        return {
            "value": maximum, "forecast": records, "generated_at": now.isoformat(),
            "status": "plan_ready", "application": "user_automation",
            "limitation_disabled": True,
            "override_reasons": [reason],
            "seasonal_soc_min_threshold_percent": threshold,
            "charging_limitation_enabled": self.limitation_enabled,
            "calculation_input": "maximum_charging_override",
            "load_consideration_active": False,
            "configured_load_input": self.config.get(CONF_LOAD_INPUT) or None,
            "forecast_status": ("not_required_for_switch_override" if reason == "disabled_by_switch"
                                else "not_required_for_seasonal_override"),
        }
