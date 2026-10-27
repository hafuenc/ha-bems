# FBC calculation

The Python calculation in `custom_components/ha_bems/fbc.py` is a direct port
of the calculation in `matlab_reference/fbc_original_excerpt.m`. It is independent
of Home Assistant and performs no device writes. Its results still need review
with the actual system inputs. Version 0.2 connects the calculation to selected
HA source entities and exposes a recommendation sensor. It never writes battery
setpoints. Select **Electrical storage management → FBC** during setup.

## Preserved calculation

1. Required battery input energy is
   `max(0, target_soc - current_soc) / 100 * capacity_kwh / efficiency`.
2. Search synthetic thresholds from zero to the forecast maximum in 10 W steps.
   Choose the first threshold with the smallest absolute energy mismatch.
3. For each interval, charge power is `max(0, pv_dc_w - threshold_w)`, rounded
   to 0.1 W. Forecast energy uses the fixed interval duration in hours.
4. When SOC is below the active seasonal threshold, replace the entire profile
   with the configured maximum charging power.
5. Compare the first resulting profile value with the first PV value, both
   rounded to whole watts. If equal, replace the entire profile with maximum
   charging power. The comparison follows the monthly override, as in MATLAB.
6. Clamp only the first output value to 0..5000 W by default. The profile itself
   is not capped, as in the original calculation. `output_limit_w` represents
   the separate hardcoded output cap; it is not a new battery setting to tune.

The selected threshold and energy above it describe the calculation before any
override. `override_reasons` explains why the final profile allows maximum power.
The threshold defaults to the configured month. An explicitly supplied
`seasonal_soc_min_threshold_percent` can instead use a shared entity-derived value.
`seasonal_soc_min_threshold_percent` in the result reports the active value.

## Inputs still required from the actual system

- DC PV forecast source, format, timestamps and interval duration.
- SOC entity and charging-limit target entity.
- Target SOC, capacity on the same SOC basis, charge efficiency as a factor,
  maximum charging power and 12 monthly SOC thresholds.
- Recalculation schedule.

The first adapter prepares the remaining local day-end horizon, checks source
update age, and normalizes explicit interval records. It does not use forecast household demand: that value is calculated
but unused in the MATLAB FBC fragment.

Empty forecasts, negative/nonfinite power, invalid SOC, invalid efficiency or
invalid settings make the recommendation unavailable. No device fallback or
automatic battery control has been selected yet.

## First forecast adapter

Select a forecast sensor and the attribute containing its list of records. The
default contract is:

```json
[
  {"start": "2026-10-27T12:00:00+01:00", "end": "2026-10-27T12:15:00+01:00", "power_dc_w": 3200}
]
```

Map the start, end and value keys in setup; select W/kW for interval-average
power or Wh/kWh for interval energy. A missing end uses the configured source
interval duration. All timestamps require timezone offsets. Records are sorted,
overlaps and duplicates rejected, and complete intervals resampled to 15 minutes.
The first interval is the quarter-hour containing the current time; the horizon
ends at local midnight, including on DST transition days. Zero-power nighttime
records must be explicit. Missing values are not treated as zero. Source update
age is checked against the selected entity's `last_updated`; provider-specific
forecast generation timestamps are not supported yet.

The generic adapter requires a list of flat records. Nested or action-response
forecasts need an external template translating them to this contract until
dedicated provider adapters are added. An AC-clipped forecast cannot recover
the missing DC power above the inverter limit.

Recalculation occurs at each local quarter-hour and when input entities change.
Numerical work runs outside the HA event loop. The sensor's state is the first
charging recommendation in W; its `forecast` attribute holds the remaining
profile with interval start/end timestamps. Status is `observation_only`.
Invalid input makes the sensor unavailable and removes the last forecast from
its exposed attributes. `reason` explains the missing recommendation.

## Reviewing a Python result

Run `python -m examples.fbc_calculation` from the repository root. This prints a
synthetic example with the selected threshold, energy, profile and first output.
Replace its illustrative values with actual inputs for site review. No automated
test suite or MATLAB comparison dataset has been added, per the owner's request.

Preserve the running MATLAB control until the Python recommendations have been
reviewed and a separate switch to device control is authorized.
