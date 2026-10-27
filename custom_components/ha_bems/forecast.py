"""Normalise explicit DC forecast interval records for the first FBC adapter."""

from datetime import UTC, datetime, timedelta
from math import isfinite


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Forecast timestamps must be ISO 8601 strings with timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Forecast timestamps require a timezone offset")
    return result.astimezone(UTC)


def prepare_forecast(records, config, now, timezone):
    """Resample interval-average power/energy to complete 15-minute intervals.

    No missing interval is assumed to be zero, including nighttime. Existing
    forecasts must explicitly cover the remaining local day to preserve FBC's
    day-end energy horizon. Entity freshness is checked by the HA adapter.
    """
    if not isinstance(records, list) or not records:
        raise ValueError("Forecast attribute must be a nonempty list of interval records")
    intervals = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Each forecast interval must be an object")
        start = parse_timestamp(record[config.get("timestamp_key", "start")])
        end_key = config.get("end_key", "end")
        end = parse_timestamp(record[end_key]) if end_key and end_key in record else (
            start + timedelta(minutes=config.get("forecast_interval_minutes", 15))
        )
        if end <= start:
            raise ValueError("Forecast intervals must have positive duration")
        value = float(record[config.get("value_key", "power_dc_w")])
        if not isfinite(value) or value < 0:
            raise ValueError("DC forecast must be finite and nonnegative")
        unit = config.get("forecast_unit", "W")
        if unit == "kW":
            value *= 1000
        elif unit in ("Wh", "kWh"):
            value *= (1000 if unit == "kWh" else 1) / ((end - start).total_seconds() / 3600)
        elif unit != "W":
            raise ValueError("Unsupported forecast unit")
        intervals.append((start, end, value))
    intervals.sort()
    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
        raise ValueError("Forecast intervals overlap or have duplicate timestamps")

    cursor = now.astimezone(UTC).replace(second=0, microsecond=0)
    cursor -= timedelta(minutes=cursor.minute % 15)
    local = now.astimezone(timezone)
    day_end = (local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).astimezone(UTC)
    result = []
    while cursor + timedelta(minutes=15) <= day_end:
        stop = cursor + timedelta(minutes=15)
        seconds = 0.0
        power_seconds = 0.0
        for start, end, value in intervals:
            overlap = (min(stop, end) - max(cursor, start)).total_seconds()
            if overlap > 0:
                seconds += overlap
                power_seconds += overlap * value
        if abs(seconds - 900) > 0.001:
            raise ValueError(f"Forecast gap at {cursor.isoformat()}; explicit coverage through day end is required")
        result.append({"start": cursor.isoformat(), "end": stop.isoformat(), "power_dc_w": power_seconds / 900})
        cursor = stop
    if not result:
        raise ValueError("No remaining complete FBC intervals")
    return result
