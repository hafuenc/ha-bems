"""Validate the shared forecast contract and select FBC's daily horizon."""

from datetime import UTC, datetime, timedelta
from math import isfinite

INTERVAL = timedelta(minutes=15)
MIN_HORIZON = timedelta(hours=72)
MAX_FILL_INTERVALS = 4
FILL_MARKER = "filled_previous"


class ForecastCoverageError(ValueError):
    """The one-hour missing-data allowance has been exhausted."""


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


def validate_forecast(records, now, value_key="power_w", nonnegative=True):
    """Normalize quarter-hours, holding previous values for at most four gaps.

    Never invent the current interval, sort, resample or repair invalid values.
    Filled-row provenance survives consumers, preventing unlimited extensions.
    """
    if not isinstance(records, list) or not 0 < len(records) <= 10000:
        raise ValueError("Attribute 'forecast' must be a nonempty list of at most 10000 intervals")
    intervals = []
    previous_end = None
    now = now.astimezone(UTC)
    current_quarter = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 15)
    # 72 hours ahead plus the current interval, consistently across consumers.
    finish = current_quarter + MIN_HORIZON + INTERVAL
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not {"start", "end", value_key} <= record.keys():
            raise ValueError(f"Interval {index + 1} requires start, end and {value_key}")
        start = parse_timestamp(record["start"])
        end = parse_timestamp(record["end"])
        if end - start != INTERVAL or start.timestamp() % 900 != 0:
            raise ValueError(f"Interval {index + 1} must cover exactly one aligned 15-minute interval")
        value = record[value_key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or (nonnegative and value < 0)):
            qualifier = "finite, nonnegative" if nonnegative else "finite"
            raise ValueError(f"Interval {index + 1}: {value_key} must be a {qualifier} number")
        if previous_end is not None and start < previous_end:
            raise ValueError(f"Overlap, duplicate or unordered interval at {start.isoformat()}")
        if FILL_MARKER in record and type(record[FILL_MARKER]) is not bool:
            raise ValueError("filled_previous must be a boolean")
        if previous_end is not None and start > previous_end:
            if previous_end <= current_quarter < start:
                raise ValueError("Forecast must include the current 15-minute interval")
            previous_end = max(previous_end, min(start, current_quarter))
            while previous_end < min(start, finish):
                if previous_end >= current_quarter:
                    if sum(row.get(FILL_MARKER, False) for row in intervals
                           if current_quarter <= parse_timestamp(row["start"]) < finish) >= MAX_FILL_INTERVALS:
                        raise ForecastCoverageError("Forecast has more than four missing 15-minute intervals (one hour)")
                    intervals.append({**intervals[-1], "start": previous_end.isoformat(),
                                      "end": (previous_end + INTERVAL).isoformat(), FILL_MARKER: True})
                previous_end += INTERVAL
        intervals.append({**record, "start": start.isoformat(), "end": end.isoformat(), value_key: value})
        previous_end = end
    intervals = [row for row in intervals if current_quarter <= parse_timestamp(row["start"]) < finish]
    if not intervals or parse_timestamp(intervals[0]["start"]) != current_quarter:
        raise ValueError("Forecast must include the current 15-minute interval")
    missing = int((finish - parse_timestamp(intervals[-1]["end"])) / INTERVAL)
    if missing + sum(row.get(FILL_MARKER, False) for row in intervals) > MAX_FILL_INTERVALS:
        raise ForecastCoverageError("Forecast cannot cover the next 72 hours: more than four missing 15-minute intervals (one hour)")
    for _ in range(missing):
        end = parse_timestamp(intervals[-1]["end"])
        intervals.append({**intervals[-1], "start": end.isoformat(),
                          "end": (end + INTERVAL).isoformat(), FILL_MARKER: True})
    return intervals


def daily_forecast(intervals, now, timezone):
    """FBC v1 still optimises only the remaining local calendar day."""
    local = now.astimezone(timezone)
    midnight = (local.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1)).astimezone(UTC)
    return [row for row in intervals if parse_timestamp(row["end"]) <= midnight]


def surplus_forecast(pv_intervals, load_intervals):
    """Subtract load on matching instants, without modelling battery discharge."""
    loads = {parse_timestamp(row["start"]): row for row in load_intervals}
    result = []
    for pv in pv_intervals:
        load = loads.get(parse_timestamp(pv["start"]))
        if load is None or parse_timestamp(load["end"]) != parse_timestamp(pv["end"]):
            raise ValueError("Building load forecast must match the PV forecast's 15-minute intervals")
        result.append({**pv, "power_w": max(0.0, pv["power_w"] - load["power_w"])})
    return result
