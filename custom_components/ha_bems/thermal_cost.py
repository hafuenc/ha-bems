"""Standalone thermal activation costs; no demand model, storage or solver."""

from bisect import bisect_right
from math import isfinite

from .forecast import parse_timestamp, validate_forecast


def number(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def cop_points(points):
    """Accept two to ten unique, freely ordered temperature/COP anchors."""
    if not isinstance(points, list) or not 2 <= len(points) <= 10:
        raise ValueError("Define between 2 and 10 temperature/COP points")
    values = []
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("Each COP point requires temperature_c and cop")
        values.append((number(point.get("temperature_c"), "Anchor temperature"),
                       number(point.get("cop"), "Anchor COP", positive=True)))
    values.sort()
    if len({temperature for temperature, _cop in values}) != len(values):
        raise ValueError("COP anchor temperatures must be unique")
    return values


def validate_settings(config):
    number(config.get("thermal_power_kw"), "Thermal power", positive=True)
    number(config.get("auxiliary_power_w", 0), "Auxiliary electrical power", nonnegative=True)
    model = config.get("cop_model")
    if model == "constant":
        number(config.get("constant_cop"), "Constant COP", positive=True)
    elif model == "lookup":
        cop_points(config.get("cop_points"))
        number(config.get("fallback_cop"), "Fallback COP", positive=True)
    else:
        raise ValueError("Choose a constant COP or a temperature/COP lookup table")
    if type(config.get("feed_in_limit_enabled", False)) is not bool:
        raise ValueError("Feed-in limit enabled must be a boolean")
    if config.get("feed_in_limit_enabled"):
        number(config.get("feed_in_limit_w"), "Feed-in limit", nonnegative=True)
        number(config.get("curtailed_price_eur_per_kwh", 0), "Curtailed PV price")


def interpolate_cop(temperature, points):
    """Use the nearest end segment for linear extrapolation, as in MATLAB."""
    index = max(0, min(len(points) - 2, bisect_right([point[0] for point in points], temperature) - 1))
    (x0, y0), (x1, y1) = points[index:index + 2]
    return y0 + (temperature - x0) * (y1 - y0) / (x1 - x0)


def _matched_rows(reference, rows, key, label, now, nonnegative=False):
    rows = validate_forecast(rows, now, key, nonnegative)
    indexed = {parse_timestamp(row["start"]): row for row in rows}
    result = []
    for item in reference:
        row = indexed.get(parse_timestamp(item["start"]))
        if row is None or parse_timestamp(row["end"]) != parse_timestamp(item["end"]):
            raise ValueError(f"{label} must match the cost forecast intervals")
        result.append(row[key])
    return result


def calculate_cost_forecast(config, now, pv, import_prices, export_prices,
                            load=None, temperatures=None, temperature_error=None):
    """Cost of running this complete system for one full 15-minute interval.

    Each setup independently evaluates the available PV surplus. It does not
    reserve PV or allocate it jointly between DHW, UFH, a battery or other loads.
    A later scheduler must resolve competing uses.
    """
    validate_settings(config)
    pv = validate_forecast(pv, now)
    # Publish precisely the rolling 72-hour window, including the current slot.
    pv = pv[:289]
    imports = _matched_rows(pv, import_prices, "price_eur_per_kwh", "Import price", now)
    exports = _matched_rows(pv, export_prices, "price_eur_per_kwh", "Export price", now)
    loads = _matched_rows(pv, load, "power_w", "Building load", now, True) if load is not None else [0] * len(pv)
    points = cop_points(config["cop_points"]) if config["cop_model"] == "lookup" else None
    values = None
    if points and temperatures is not None:
        # Invalid temperatures are an explicit COP fallback, never zero-filled.
        try:
            values = _matched_rows(pv, temperatures, "temperature_c", "Outdoor temperature", now)
        except ValueError as err:
            temperature_error = str(err)
    reasons = set()
    records = []
    for index, row in enumerate(pv):
        fallback = False
        if points:
            if values is None:
                cop = config["fallback_cop"]
                fallback = True
                reasons.add(temperature_error or "Outdoor temperature input is unavailable")
            else:
                cop = interpolate_cop(values[index], points)
                if not isfinite(cop) or cop <= 0:
                    cop = config["fallback_cop"]
                    fallback = True
                    reasons.add("Interpolated/extrapolated COP is non-positive or non-finite")
        else:
            cop = config["constant_cop"]
        heat_pump_w = config["thermal_power_kw"] * 1000 / cop
        power = heat_pump_w + config.get("auxiliary_power_w", 0)
        surplus = max(0, row["power_w"] - loads[index])
        pv_power = min(power, surplus)
        curtailed = min(power, max(0, surplus - config["feed_in_limit_w"])) if config.get("feed_in_limit_enabled") else 0
        exported = pv_power - curtailed
        imported = power - pv_power
        cost = 0.25 / 1000 * (imported * imports[index] + exported * exports[index]
                 + curtailed * config.get("curtailed_price_eur_per_kwh", 0))
        for value in (power, imported, exported, curtailed, cost):
            number(value, "Calculated runtime cost/power")
        records.append({"start": row["start"], "end": row["end"],
                        "cost_eur_per_timestep": cost, "cop": cop,
                        "electrical_power_w": power, "grid_import_power_w": imported,
                        "pv_export_opportunity_power_w": exported,
                        "curtailed_pv_power_w": curtailed, "cop_fallback": fallback})
    return {"value": records[0]["cost_eur_per_timestep"], "forecast": records,
            "cop_model": config["cop_model"], "current_cop": records[0]["cop"],
            "thermal_power_kw": config["thermal_power_kw"],
            "auxiliary_power_w": config.get("auxiliary_power_w", 0),
            "current_electrical_power_w": records[0]["electrical_power_w"],
            "cop_fallback_active": any(row["cop_fallback"] for row in records),
            "cop_fallback_intervals": sum(row["cop_fallback"] for row in records),
            "cop_fallback_reason": "; ".join(sorted(reasons)),
            "status": "ready_cop_fallback" if reasons else "ready",
            "schema_version": 1, "interval_minutes": 15,
            "forecast_unit": "EUR/timestep", "forecast_value_key": "cost_eur_per_timestep",
            "generated_at": now.isoformat(), "cost_basis": "full_interval_activation",
            "pv_allocation": "independent_per_setup_without_reservations"}
