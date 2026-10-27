"""Validate the shared forecast contract and select FBC's daily horizon."""

from datetime import UTC, datetime, timedelta
from math import isfinite

INTERVAL = timedelta(minutes=15)
MIN_HORIZON = timedelta(hours=72)


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Timestamps must be ISO 8601 strings with timezone offsets")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise ValueError("Invalid ISO 8601 timestamp") from err
    if result.tzinfo is None:
        raise ValueError("Timestamps require a timezone offset")
    return result.astimezone(UTC)


def validate_forecast(records, now):
    """Require explicit, ordered, contiguous quarter-hours through now + 72 h.

    Never resample, sort, fill gaps, guess field names or infer units.
    Epoch/UTC arithmetic preserves DST slots.
    """
    if not isinstance(records, list) or not 0 < len(records) <= 10000:
        raise ValueError("Attribute 'forecast' must be a nonempty list of at most 10000 intervals")
    intervals = []
    previous_end = None
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not {"start", "end", "power_w"} <= record.keys():
            raise ValueError(f"Interval {index + 1} requires start, end and power_w")
        start = parse_timestamp(record["start"])
        end = parse_timestamp(record["end"])
        if end - start != INTERVAL or start.timestamp() % 900 != 0:
            raise ValueError(f"Interval {index + 1} must cover exactly one aligned 15-minute interval")
        value = record["power_w"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
            raise ValueError(f"Interval {index + 1}: power_w must be a finite, nonnegative number in W")
        if previous_end is not None and start != previous_end:
            raise ValueError(f"Gap, overlap, duplicate or unordered interval at {start.isoformat()}")
        intervals.append({"start": start.isoformat(), "end": end.isoformat(), "power_w": value})
        previous_end = end
    now = now.astimezone(UTC)
    current_quarter = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 15)
    if parse_timestamp(intervals[0]["start"]) > current_quarter:
        raise ValueError("Forecast must include the current 15-minute interval")
    if previous_end < now + MIN_HORIZON:
        raise ValueError("Forecast must cover at least the next 72 hours from the current time, without gaps")
    return [row for row in intervals if parse_timestamp(row["start"]) >= current_quarter]


def daily_forecast(intervals, now, timezone):
    """FBC v1 still optimises only the remaining local calendar day."""
    local = now.astimezone(timezone)
    midnight = (local.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1)).astimezone(UTC)
    return [row for row in intervals if parse_timestamp(row["end"]) <= midnight]
