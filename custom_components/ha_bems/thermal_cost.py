"""Standalone thermal activation costs; no demand model, storage or solver."""

from bisect import bisect_right
from math import isfinite

from .const import CONF_ADDITIONAL_LOADS, CONF_LOAD_INPUT, THERMAL_PRICE_INPUTS

from .forecast import FILL_MARKER, MAX_FILL_INTERVALS, ForecastCoverageError, parse_timestamp, validate_forecast


def number(value, label, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def cop_points(points):
    """Accept two to ten unique, freely ordered temperature/COP reference points."""
    if not isinstance(points, list) or not 2 <= len(points) <= 10:
        raise ValueError("Define between 2 and 10 temperature/COP points")
    values = []
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("Each COP point requires temperature_c and cop")
        values.append((number(point.get("temperature_c"), "Reference temperature"),
                       number(point.get("cop"), "Reference COP", positive=True)))
    values.sort()
    if len({temperature for temperature, _cop in values}) != len(values):
        raise ValueError("COP reference temperatures must be unique")
    return values


def validate_settings(config):
    if type(config.get("eeg_enabled", False)) is not bool:
        raise ValueError("EEG enabled must be a boolean")
    for prefix, _key, _module in THERMAL_PRICE_INPUTS:
        if prefix == "eeg" and not config.get("eeg_enabled", False):
            continue
        source = config.get(f"{prefix}_price_source", "forecast")
        if source not in ("forecast", "constant"):
            raise ValueError(f"Choose a forecast or fixed {prefix} price")
        if source == "constant":
            number(config.get(f"{prefix}_price_eur_per_kwh"), f"Fixed {prefix} price")
    additional = config.get(CONF_ADDITIONAL_LOADS, [])
    if not isinstance(additional, list) or any(not isinstance(ref, str) or not ref for ref in additional):
        raise ValueError("Additional loads must be a list of selected load forecasts")
    if len(set(additional)) != len(additional) or config.get(CONF_LOAD_INPUT) in additional:
        raise ValueError("Select each load only once")
    number(config.get("thermal_power_kw"), "Thermal power", positive=True)
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
        if row.get(FILL_MARKER):
            item[FILL_MARKER] = True
        result.append(row[key])
    return result


def calculate_cost_forecast(config, now, pv, import_prices, export_prices,
                            load=None, temperatures=None, temperature_error=None,
                            eeg_prices=None, eeg_energy=None, additional_loads=None):
    """Cost of running this complete system for one full 15-minute interval.

    Each setup evaluates PV and optional EEG remaining after its selected loads.
    Higher-priority devices can be included as background loads. Resources are
    not automatically reserved between independent setups.
    """
    validate_settings(config)
    pv = validate_forecast(pv, now)
    # Publish precisely the rolling 72-hour window, including the current slot.
    pv = pv[:289]
    imports = _matched_rows(pv, import_prices, "price_eur_per_kwh", "Import price", now)
    exports = _matched_rows(pv, export_prices, "price_eur_per_kwh", "Export price", now)
    loads = _matched_rows(pv, load, "power_w", "Building load", now, True) if load is not None else [0] * len(pv)
    for additional in additional_loads or []:
        extra = _matched_rows(pv, additional, "power_w", "Higher-priority load", now, True)
        loads = [base + value for base, value in zip(loads, extra)]
    eeg_enabled = config.get("eeg_enabled", False)
    community_prices = (_matched_rows(pv, eeg_prices, "price_eur_per_kwh", "EEG price", now)
                        if eeg_enabled else [0] * len(pv))
    community_energy = (_matched_rows(pv, eeg_energy, "energy_kwh", "EEG available energy", now, True)
                        if eeg_enabled else [0] * len(pv))
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
        power = config["thermal_power_kw"] * 1000 / cop
        number(loads[index], "Total considered electrical load", nonnegative=True)
        surplus = max(0, row["power_w"] - loads[index])
        pv_power = min(power, surplus)
        curtailed = min(power, max(0, surplus - config["feed_in_limit_w"])) if config.get("feed_in_limit_enabled") else 0
        exported = pv_power - curtailed
        community_available = max(0, community_energy[index] * 4000 - max(0, loads[index] - row["power_w"]))
        community_power = min(power - pv_power, community_available)
        imported = power - pv_power - community_power
        cost = 0.25 / 1000 * (imported * imports[index] + exported * exports[index]
                 + curtailed * config.get("curtailed_price_eur_per_kwh", 0)
                 + community_power * community_prices[index])
        for value in (power, imported, exported, curtailed, community_power, cost):
            number(value, "Calculated runtime cost/power")
        records.append({"start": row["start"], "end": row["end"],
                        "cost_eur_per_timestep": cost, "cop": cop,
                        "electrical_power_w": power, "grid_import_power_w": imported,
                        "eeg_power_w": community_power, "eeg_energy_kwh": community_power / 4000,
                        "eeg_cost_eur_per_timestep": community_power / 4000 * community_prices[index],
                        "considered_load_power_w": loads[index],
                        "pv_export_opportunity_power_w": exported,
                        "curtailed_pv_power_w": curtailed, "cop_fallback": fallback,
                        **({FILL_MARKER: True} if row.get(FILL_MARKER) else {})})
    if sum(row.get(FILL_MARKER, False) for row in records) > MAX_FILL_INTERVALS:
        raise ForecastCoverageError("Combined cost inputs contain more than four filled intervals")
    return {"value": records[0]["cost_eur_per_timestep"], "forecast": records,
            "eeg_enabled": eeg_enabled,
            "cop_model": config["cop_model"], "current_cop": records[0]["cop"],
            "thermal_power_kw": config["thermal_power_kw"],
            "current_electrical_power_w": records[0]["electrical_power_w"],
            "cop_fallback_active": any(row["cop_fallback"] for row in records),
            "cop_fallback_intervals": sum(row["cop_fallback"] for row in records),
            "filled_previous_intervals": sum(row.get(FILL_MARKER, False) for row in records),
            "cop_fallback_reason": "; ".join(sorted(reasons)),
            "status": "ready_cop_fallback" if reasons else "ready",
            "schema_version": 1, "interval_minutes": 15,
            "forecast_unit": "EUR/timestep", "forecast_value_key": "cost_eur_per_timestep",
            "generated_at": now.isoformat(), "cost_basis": "full_interval_activation",
            "pv_allocation": "independent_per_setup_without_reservations"}
