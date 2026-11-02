"""Static, numeric characteristic descriptions using the operational evaluators."""

from math import isfinite

from .thermal_cost import cop_points, interpolate_cop, validate_settings
from .thermal_milp import required_runtimes, validate_plan_settings


def characteristic_values(config, function, temperatures):
    """Return effective value, raw value and COP-fallback flag per temperature."""
    if function == "runtime_requirement":
        values = required_runtimes(config, temperatures, len(temperatures))
        if config["runtime_model"] == "constant":
            return [(v, v, False) for v in values]
        points = sorted((p["temperature_c"], p["runtime_h_per_24h"]) for p in config["runtime_points"])
        return [(v, interpolate_cop(t, points), False) for t, v in zip(temperatures, values)]
    validate_settings(config)
    if config["cop_model"] == "constant":
        value = float(config["constant_cop"])
        return [(value, value, False) for _ in temperatures]
    points = cop_points(config["cop_points"])
    result = []
    for temperature in temperatures:
        raw = interpolate_cop(temperature, points)
        fallback = raw <= 0 or not isfinite(raw)
        result.append((float(config["fallback_cop"]) if fallback else raw, raw if isfinite(raw) else None, fallback))
    return result


def build_characteristic(config, function):
    """Build once per setup/reload; never run the MILP or query providers."""
    runtime = function == "runtime_requirement"
    if runtime:
        validate_plan_settings(config)
    else:
        validate_settings(config)
    model = config["runtime_model" if runtime else "cop_model"]
    key = "runtime_h_per_24h" if runtime else "cop"
    references = sorted(
        ({"temperature_c": float(p["temperature_c"]), "value": float(p[key])}
         for p in config["runtime_points" if runtime else "cop_points"]),
        key=lambda p: p["temperature_c"]) if model == "lookup" else []
    low = min(-25., references[0]["temperature_c"] - 5) if references else -25.
    high = max(40., references[-1]["temperature_c"] + 5) if references else 40.
    samples = {low, high, *(p["temperature_c"] for p in references)}
    samples.update(low + i * .5 for i in range(int((high - low) / .5) + 1))
    cutoff_enabled = runtime and model == "lookup" and config.get("heating_cutoff_enabled", True)
    if cutoff_enabled:
        cutoff = float(config.get("heating_cutoff_c", 20))
        if low <= cutoff <= high:
            samples.update((cutoff, min(high, cutoff + 1e-6)))
    # Include every clipping kink and COP fallback boundary, not just grid samples.
    for index, (left, right) in enumerate(zip(references, references[1:])):
        slope = (right["value"] - left["value"]) / (right["temperature_c"] - left["temperature_c"])
        if slope == 0:
            continue
        targets = (config.get("runtime_min_h", 0), config.get("runtime_max_h", 24)) if runtime else (0,)
        start = low if index == 0 else left["temperature_c"]
        end = high if index == len(references) - 2 else right["temperature_c"]
        for target in targets:
            crossing = left["temperature_c"] + (target - left["value"]) / slope
            if start <= crossing <= end:
                samples.add(crossing)
                if not runtime:
                    samples.update((max(low, crossing - 1e-6), min(high, crossing + 1e-6)))
    temperatures = sorted(samples)
    curves = []
    for temperature, (value, raw, fallback) in zip(temperatures, characteristic_values(config, function, temperatures)):
        region = "constant" if not references else "interpolation" if references[0]["temperature_c"] <= temperature <= references[-1]["temperature_c"] else "extrapolation"
        curves.append({"temperature_c": temperature, "value": value, "raw_value": raw,
                       "region": region, "fallback_active": fallback})
    attributes = {"characteristic_schema_version": 1, "function": function, "model": model,
        "input_unit": "°C", "output_unit": "h/24h" if runtime else "dimensionless",
        "reference_points": references, "curve_points": curves,
        "interpolation": "linear" if references else "constant",
        "extrapolation": "linear" if references else "constant",
        "display_range_c": [low, high], "curve_step_c": .5,
        "value_basis": "configured_function_at_current_forecast_temperature"}
    if model == "constant":
        attributes["constant_value"] = float(config["runtime_h_per_24h" if runtime else "constant_cop"])
    if runtime:
        attributes.update(minimum=float(config.get("runtime_min_h", 0)) if references else 0.,
                          maximum=float(config.get("runtime_max_h", 24)) if references else 24.,
                          heating_cutoff_enabled=bool(cutoff_enabled),
                          heating_cutoff_c=float(config.get("heating_cutoff_c", 20)) if cutoff_enabled else None,
                          heating_cutoff_comparison="temperature_strictly_above")
    elif references:
        attributes["fallback_value"] = float(config["fallback_cop"])
    return attributes
