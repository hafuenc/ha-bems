"""Read one provider-independent PV forecast entity; no source adapters."""

from math import isfinite

from homeassistant.util import dt as dt_util

from .forecast import parse_timestamp, validate_forecast


class ForecastUnavailable(ValueError):
    """The source cannot currently be read."""


def source_age(updated, now):
    """Report source age as information, without an age-based rejection."""
    updated = parse_timestamp(updated)
    seconds = (now - updated).total_seconds()
    if seconds < -300:
        raise ValueError("source_updated_at is in the future; check timestamps and clock settings")
    age = max(0.0, seconds / 60)
    return age, updated.isoformat()


async def async_read_pv_forecast(hass, config):
    now = dt_util.now()
    state = hass.states.get(config["forecast_entity"])
    if state is None or state.state in ("unknown", "unavailable"):
        raise ForecastUnavailable("PV forecast entity is unavailable")
    if state.attributes.get("unit_of_measurement") != "W":
        raise ValueError("PV forecast entity must use unit W")
    try:
        power = float(state.state)
    except (ValueError, TypeError) as err:
        raise ValueError("PV forecast entity state must be numeric power in W") from err
    if not isfinite(power) or power < 0:
        raise ValueError("PV forecast entity state must be finite and nonnegative")
    version = state.attributes.get("schema_version")
    if type(version) is not int or version != 1:
        raise ValueError("PV forecast entity requires the numeric attribute schema_version: 1")
    age, updated = source_age(state.attributes.get("source_updated_at"), now)
    intervals = await hass.async_add_executor_job(
        validate_forecast, state.attributes.get("forecast"), now,
    )
    return intervals, {
        "source_entity": state.entity_id, "source_age_minutes": round(age, 1),
        "source_updated_at": updated, "forecast_schema_version": 1,
        "input_forecast_start": intervals[0]["start"],
        "input_forecast_end": intervals[-1]["end"],
        "input_forecast_intervals": len(intervals),
    }
