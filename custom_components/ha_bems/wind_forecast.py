"""Pure wind-to-energy model, using an editable electrical power curve."""

from bisect import bisect_right
from datetime import UTC, timedelta
from math import isfinite

from .forecast import INTERVAL, MIN_HORIZON

WIND_HEIGHTS = (10, 80, 120, 180)
DEFAULT_WIND_POINTS = tuple(enumerate(
    [0, 0, 2, 18, 56, 127, 240, 400, 626, 892, 1223, 1590, 1900, 2080, 2230, 2300]
    + [2310] * 10))


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("A finite number is required")
    return float(value)


def power_points(points):
    if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= 200:
        raise ValueError("Enter between 2 and 200 wind-speed;power rows")
    result = []
    for pair in points:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Each row requires wind speed and electrical power")
        speed, power = (finite_number(v) for v in pair)
        if speed < 0 or power < 0 or (result and speed <= result[-1][0]):
            raise ValueError("Speeds must strictly increase; speed and power must be nonnegative")
        result.append((speed, power))
    return result


def curve_text(points=DEFAULT_WIND_POINTS):
    return "\n".join(f"{v:g};{p:g}" for v, p in power_points(points))


def parse_curve(text):
    if not isinstance(text, str):
        raise ValueError("Enter one wind speed in m/s;power in kW pair per line")
    points = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split(";")
        if len(fields) != 2:
            raise ValueError("Enter one wind speed in m/s;power in kW pair per line")
        points.append([float(v.strip().replace(",", ".")) for v in fields])
    return [list(point) for point in power_points(points)]


def validate_wind_settings(config):
    for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180),
                           ("hub_height_m", 10, 180), ("share_percent", 0, 100)):
        value = finite_number(config[key])
        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
    power_points(config["wind_power_points"])


def validate_weather(payload):
    """Validate every hourly point before replacing a working weather cache."""
    if not isinstance(payload, dict):
        raise ValueError("Weather response must be an object")
    hourly, units = payload["hourly"], payload["hourly_units"]
    if not isinstance(hourly, dict) or not isinstance(units, dict):
        raise ValueError("Weather data and units must be objects")
    times = hourly["time"]
    if not isinstance(times, list) or not 2 <= len(times) <= 200:
        raise ValueError("Expected a seven-day hourly weather response")
    times = [finite_number(t) for t in times]
    if any(t % 3600 for t in times) or any(b - a != 3600 for a, b in zip(times, times[1:])):
        raise ValueError("Weather timestamps must be consecutive hours")
    if units.get("time") != "unixtime":
        raise ValueError("Weather timestamps must use Unix seconds")
    normalized = {"time": times}
    for height in WIND_HEIGHTS:
        key = f"wind_speed_{height}m"
        if units.get(key) != "m/s" or len(hourly[key]) != len(times):
            raise ValueError("Wind data must be complete and expressed in m/s")
        values = [finite_number(v) for v in hourly[key]]
        if any(v < 0 for v in values):
            raise ValueError("Wind speeds must be nonnegative")
        normalized[key] = values
    return normalized


def wind_at_height(weather, height):
    upper = min(bisect_right(WIND_HEIGHTS, height), len(WIND_HEIGHTS) - 1)
    lower = max(0, upper - 1)
    fraction = (height - WIND_HEIGHTS[lower]) / (WIND_HEIGHTS[upper] - WIND_HEIGHTS[lower])
    return [a + fraction * (b - a) for a, b in zip(
        weather[f"wind_speed_{WIND_HEIGHTS[lower]}m"], weather[f"wind_speed_{WIND_HEIGHTS[upper]}m"])]


def power_at(speed, points):
    if speed < points[0][0] or speed > points[-1][0]:
        return 0.0
    index = min(bisect_right([p[0] for p in points], speed) - 1, len(points) - 2)
    a, b = points[index], points[index + 1]
    return a[1] + (speed - a[0]) / (b[0] - a[0]) * (b[1] - a[1])


def interval_energy(v0, v1, points, hours=0.25):
    """Exactly integrate the linear wind trajectory across curve/cutoff knots."""
    cuts = [0.0, 1.0]
    if v1 != v0:
        cuts.extend((v - v0) / (v1 - v0) for v, _ in points if 0 < (v - v0) / (v1 - v0) < 1)
    cuts.sort()
    result = 0.0
    for a, b in zip(cuts, cuts[1:]):
        middle = v0 + (v1 - v0) * (a + b) / 2
        if points[0][0] <= middle <= points[-1][0]:
            # Select the linear curve segment at its midpoint. Evaluating its
            # endpoints directly avoids floating-point misclassification at cutoffs.
            index = min(bisect_right([p[0] for p in points], middle) - 1, len(points) - 2)
            x, y = points[index], points[index + 1]
            result += (x[1] + (middle - x[0]) / (y[0] - x[0]) * (y[1] - x[1])) * (b - a) * hours
    return max(0.0, result)


def wind_energy_forecast(config, weather, now):
    validate_wind_settings(config)
    times = weather["time"]
    winds = wind_at_height(weather, config["hub_height_m"])
    points = power_points(config["wind_power_points"])
    cursor = now.astimezone(UTC).replace(second=0, microsecond=0)
    cursor -= timedelta(minutes=cursor.minute % 15)
    # Match HA-BEMS consumers: current quarter plus the next 288 quarters.
    finish = cursor + MIN_HORIZON + INTERVAL
    if times[0] > cursor.timestamp() or times[-1] < finish.timestamp():
        raise ValueError("Cached wind data no longer cover the full next 72 hours")

    def speed_at(stamp):
        i = min(bisect_right(times, stamp) - 1, len(times) - 2)
        return winds[i] + (winds[i + 1] - winds[i]) * (stamp - times[i]) / 3600

    result = []
    while cursor < finish:
        end = cursor + INTERVAL
        energy = interval_energy(speed_at(cursor.timestamp()), speed_at(end.timestamp()), points)
        result.append({"start": cursor.isoformat(), "end": end.isoformat(),
                       "energy_kwh": energy * config["share_percent"] / 100})
        cursor = end
    return result
