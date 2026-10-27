"""PV forecast adapters. Pure conversions; no equipment control."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from math import isfinite

from .forecast import parse_timestamp

OPEN_METEO = "open_meteo_solar_forecast"
CUSTOM_TEMPLATE = "custom_template"
GENERIC_RECORDS = "generic_records"

TEMPLATE_EXAMPLE = """{# source_entity is the forecast sensor selected above.
Adapt the attribute and field names to your source.
Return a list of start/end timestamps and average power in W.
For kW, multiply the power by 1000. Do not replace missing data with zero. -#}
{%- set rows = state_attr(source_entity, 'forecast') -%}
{%- set ns = namespace(records=[]) -%}
{%- if rows is iterable and rows is not string and rows is not mapping -%}
  {%- for row in rows -%}
    {%- set ns.records = ns.records + [{
      'start': row['start'],
      'end': row['end'],
      'power_w': row['power_w']
    }] -%}
  {%- endfor -%}
  {{- ns.records -}}
{%- else -%}
  {{- none -}}
{%- endif -%}"""


def canonical_mapping():
    return {"timestamp_key": "start", "end_key": "end", "value_key": "power_w", "forecast_unit": "W"}


def _power(value):
    if isinstance(value, bool):
        raise ValueError("Forecast values must be nonnegative numbers")
    value = float(value)
    if not isfinite(value) or value < 0:
        raise ValueError("Forecast values must be finite and nonnegative")
    return value


def open_meteo_records(attributes):
    """Prefer exact interval energy; older releases expose power samples only.

    rany2/ha-open-meteo-solar-forecast: wh_period_15m is Wh keyed by
    interval-start; watts is point power and must be integrated, not treated as
    interval energy. Preserve the provider's configured production model.
    """
    energy = attributes.get("wh_period_15m")
    if isinstance(energy, Mapping) and energy:
        records = []
        for timestamp, value in energy.items():
            start = parse_timestamp(timestamp)
            records.append({"start": start.isoformat(),
                            "end": (start + timedelta(minutes=15)).isoformat(),
                            "power_w": _power(value) * 4})
        return records, "Open-Meteo wh_period_15m (interval energy)"

    watts = attributes.get("watts")
    if not isinstance(watts, Mapping) or len(watts) < 2:
        raise ValueError("Select Open-Meteo's Energy Production Today sensor with a watts or wh_period_15m attribute")
    points = sorted((parse_timestamp(key), _power(value)) for key, value in watts.items())
    if len({point[0] for point in points}) != len(points):
        raise ValueError("Duplicate Open-Meteo power timestamps")
    # The installed 0.1.x sensor publishes samples through 23:45, omitting the
    # following midnight. Extend only a final zero sample whose containing
    # hourly energy is explicitly zero; never invent daytime production.
    last_key = max(watts, key=parse_timestamp)
    local_last = datetime.fromisoformat(last_key.replace("Z", "+00:00"))
    day_end = (local_last.replace(hour=0, minute=0, second=0, microsecond=0)
               + timedelta(days=1)).astimezone(UTC)
    hourly = attributes.get("wh_period", {})
    explicit_zero_tail = isinstance(hourly, Mapping) and any(
        parse_timestamp(key) <= points[-1][0] < parse_timestamp(key) + timedelta(hours=1)
        and parse_timestamp(key) + timedelta(hours=1) >= day_end and _power(value) == 0
        for key, value in hourly.items()
    )
    if points[-1][1] == 0 and 0 < (day_end - points[-1][0]).total_seconds() <= 900 and explicit_zero_tail:
        points.append((day_end, 0.0))
    records = []
    for (start, left), (end, right) in zip(points, points[1:]):
        duration = (end - start).total_seconds()
        if not 0 < duration <= 3600:
            raise ValueError("Open-Meteo power samples contain a gap longer than one hour")
        cursor = start
        while cursor < end:
            boundary = cursor.replace(second=0, microsecond=0) + timedelta(minutes=15 - cursor.minute % 15)
            stop = min(boundary, end)
            first = left + (right - left) * ((cursor - start).total_seconds() / duration)
            last = left + (right - left) * ((stop - start).total_seconds() / duration)
            records.append({"start": cursor.isoformat(), "end": stop.isoformat(), "power_w": (first + last) / 2})
            cursor = stop
    return records, "Open-Meteo watts (linearly integrated power samples)"
