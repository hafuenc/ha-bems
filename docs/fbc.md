# FBC calculation

The Python calculation in `custom_components/ha_bems/fbc.py` is a direct port
of the calculation in `matlab_reference/fbc_original_excerpt.m`. It is independent
of Home Assistant and performs no device writes. It is not yet an installable
integration. Its results still need review with the actual system inputs.

## Preserved calculation

1. Required battery input energy is
   `max(0, target_soc - current_soc) / 100 * capacity_kwh / efficiency`.
2. Search synthetic thresholds from zero to the forecast maximum in 10 W steps.
   Choose the first threshold with the smallest absolute energy mismatch.
3. For each interval, charge power is `max(0, pv_dc_w - threshold_w)`, rounded
   to 0.1 W. Forecast energy uses the fixed interval duration in hours.
4. When SOC is below the current month's threshold, replace the entire profile
   with the configured maximum charging power.
5. Compare the first resulting profile value with the first PV value, both
   rounded to whole watts. If equal, replace the entire profile with maximum
   charging power. The comparison follows the monthly override, as in MATLAB.
6. Clamp only the first output value to 0..5000 W by default. The profile itself
   is not capped, as in the original calculation. `output_limit_w` represents
   the separate hardcoded output cap; it is not a new battery setting to tune.

The selected threshold and energy above it describe the calculation before any
override. `override_reasons` explains why the final profile allows maximum power.

## Inputs still required from the actual system

- DC PV forecast source, format, timestamps and interval duration.
- SOC entity and charging-limit target entity.
- Target SOC, capacity on the same SOC basis, charge efficiency as a factor,
  maximum charging power and 12 monthly SOC thresholds.
- Recalculation schedule.

The caller must prepare the remaining local day-end horizon, validate timestamps
and freshness, and normalize the forecast. This port does not implement that
adapter yet. It does not use forecast household demand: that value is calculated
but unused in the MATLAB FBC fragment.

Empty forecasts, negative/nonfinite power, invalid SOC, invalid efficiency or
invalid settings raise an error. No device fallback has been selected yet.

## Reviewing a Python result

Run `python -m examples.fbc_calculation` from the repository root. This prints a
synthetic example with the selected threshold, energy, profile and first output.
Replace its illustrative values with actual inputs for site review. No automated
test suite or MATLAB comparison dataset has been added, per the owner's request.

Preserve the running MATLAB control until the Python recommendations have been
reviewed and a separate switch to device control is authorized.
