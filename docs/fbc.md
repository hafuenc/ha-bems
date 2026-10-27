# FBC (Forecast based charging)

Select **Electrical storage management → FBC**. The strategy is named
**FBC v1.0 - only PV generation considered**. It uses PV generation, current
battery SOC and battery settings; household demand and electricity prices do
not enter this algorithm. Its virtual device defaults to **FBC Battery 1**, of
type **Battery control strategy**. Existing entry names/IDs are retained.

HA-BEMS always publishes operating plans only. A user-defined HA automation
translates those plans to the installation's device commands. No HA-BEMS module
will directly control batteries, heat pumps, EVs or other plant.

## Setup inputs

- Actual battery SOC entity: available numeric 0..100 with unit `%`. The picker
  filters for percentage entities and setup validates the current value.
- Battery seasonal SOC min threshold entity: an existing percentage source or
  the helper created under **BEMS entity creator**. No duplicate helper is required.
- A PV generation forecast, through a prepared source or custom template.
- Battery capacity in kWh on the same basis as the SOC sensor, target SOC,
  maximum charging power and physical device output cap.
- **PV forecast-to-battery storage efficiency**: one constant factor, e.g. 0.95.
  It represents the fraction of forecast PV energy becoming stored battery energy.
  Include conversion/charging losses once; no extra DC/AC setup choice is required.

The output cap must be at least the configured maximum charging power. Normally
these are equal. Review both parameters rather than silently clipping a
maximum-power override to a smaller value.

## PV generation forecast sources

### Open-Meteo Solar Forecast

Prepared adapter for [rany2/ha-open-meteo-solar-forecast](https://github.com/rany2/ha-open-meteo-solar-forecast).
Select its **Energy Production Today** sensor (e.g. `sensor.energy_production_today`).
No attribute/key mapping is needed. The standard Open-Meteo *weather* integration
is a different integration and does not supply this PV series.

- If `wh_period_15m` is available, use its exact 15-minute Wh records keyed by
  interval start; average power is `Wh * 4`.
- For older versions with a `watts` mapping, linearly integrate the point-power
  curve into interval-average powers. Do not treat instantaneous watts as energy.
  Interior sample gaps greater than one hour and duplicates are rejected.
- The observed 0.1.x format ends at 23:45. A final zero-power sample is extended
  to midnight only if the containing hourly `wh_period` explicitly reports zero
  energy. This is not a general rule to assume missing forecasts are zero.

The adapter preserves the provider's production model and inverter limits. With
PV oversizing, choose a forecast retaining available generation peaks. A constant
efficiency cannot reconstruct peaks already removed by inverter clipping. The
adapter never reconfigures the provider or claims clipped production is unclipped.

### Custom forecast template

The form contains an editable HA/Jinja template example. `source_entity` refers
to the selected sensor. Adapt attribute names, field names and conversions. The
template must produce a list of records such as:

```json
[
  {"start": "2026-10-27T12:00:00+01:00", "end": "2026-10-27T12:15:00+01:00", "power_w": 3200}
]
```

Use interval-average W and ISO timestamps with timezone offsets. For a source
already supplying these records:

```jinja
{{ state_attr(source_entity, 'forecast') }}
```

The longer editable example shows how to build the list from source rows. For
kW multiply by 1000; for interval energy, convert using the real duration.
Unavailable data must not be converted to zero. Only the selected source's
update age is checked automatically; any additional entities used by the
template must have appropriate availability/freshness handling in the template.
The template is HA's sandboxed Jinja, not arbitrary Python. Explicit template
dependencies trigger recalculation in addition to the selected sources.

### Advanced: existing interval records

The version 0.2 attribute/key mapping remains available for existing setups.
Select the list attribute, start/end/value keys, W/kW/Wh/kWh and default source
duration if an end key is absent. Existing entries without `forecast_source`
continue to use this adapter. The historic default value key is `power_dc_w`.

Every source is resampled to 15-minute intervals covering the current quarter-hour
through local midnight. Setup previews the normalised series, peak power and
remaining energy. UTC timestamps preserve DST intervals. The current quarter-hour
is counted in full even if partly elapsed. Missing intervals are not zero-filled.
Freshness uses the selected entity's `last_updated`, not a provider-generation
timestamp; availability is therefore not a guarantee of meteorological freshness.

## Algorithm

1. Required input energy is
   `max(0, target_soc - current_soc) / 100 * capacity_kwh / efficiency`.
2. Search synthetic thresholds from 0 to maximum forecast power in 10 W steps.
   Select the first threshold minimising absolute mismatch between required
   energy and forecast energy above that threshold.
3. Charging power per interval is `max(0, PV power - threshold)`, rounded to 0.1 W.
4. At **SOC <= seasonal minimum**, replace the full profile with maximum
   charging power. This intentionally changes the MATLAB strict `<` comparison.
   The HA adapter can issue this plan without a valid PV forecast. An invalid SOC
   or threshold still makes the plan unavailable.
5. Preserve the MATLAB first-value override: compare first charging power and
   first PV power rounded to whole W; if equal, allow maximum power throughout.
6. Clamp the current first value to the device output cap. As in the MATLAB
   fragment, the non-override future profile is not individually capped.

No future battery SOC trajectory, household demand, dynamic prices or physical
charging-power clipping is included in the threshold energy matching. Target SOC
is an aim, not a guarantee. User automations must interpret the charging-limit
output on the correct equipment power basis.

## Published plan and status

The sensor state is the current maximum recommended charging power in W. The
`forecast` attribute contains interval start/end and `power_w` through midnight.
Recalculation occurs on input changes and every quarter-hour; numerical work
runs outside the HA event loop. `status` is `plan_ready`, and `application` is
`user_automation`. This describes a plan, never confirmation of physical application.

`limitation_disabled`, `override_reasons` and the active seasonal threshold explain
overrides. The seasonal-only plan has `forecast_status` equal to
`not_required_for_seasonal_override`; no synthetic threshold/energy result is
invented for that path. Otherwise, source metadata and pre-override energy
diagnostics are included. Invalid input makes the sensor unavailable and removes
its last exposed forecast; the user's applying automation must handle that state.

For standalone result review, run `python -m examples.fbc_calculation`. Preserve
existing operational control until the user decides to enable their applying
automation. HA-BEMS itself always remains a planning system.
