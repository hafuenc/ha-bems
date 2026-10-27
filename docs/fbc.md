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
- A shared PV forecast registered under **BEMS entity importer**, using the
  documented 72-hour contract.
- Usable (net) battery capacity in kWh corresponding to the selected SOC
  sensor's full 0–100% range, target SOC, maximum charging power and physical
  device output cap.
- **PV forecast-to-battery storage efficiency**: one constant factor, e.g. 0.95.
  It represents the fraction of forecast PV energy becoming stored battery energy.
  Include conversion/charging losses once; no extra DC/AC setup choice is required.

The output cap must be at least the configured maximum charging power. Normally
these are equal. Review both parameters rather than silently clipping a
maximum-power override to a smaller value.

## PV generation forecast input

Register a prepared sensor under **BEMS entity importer → PV generation forecast**
and select that named input in FBC. The source has state/unit W and attributes `schema_version: 1`,
`source_updated_at` and `forecast`. Each forecast record has timezone-aware
`start`, `end` and interval-average `power_w`. The ordered, contiguous series
must include the current quarter-hour and at least the next 72 hours, at exactly
15-minute resolution. Normally a rolling helper supplies 289 intervals.

Importer setup tests the full format and horizon; source age is informational. FBC
checks the selected registration before showing a successful preview. Importer
validation continues on source updates and at least once a minute; consumers
recheck time-dependent coverage before using its shared snapshot. Provider selectors,
in-integration template code and generic field mappings have been removed.
For helper requirements, an Open-Meteo example and existing-entry update steps,
see [Shared PV generation forecast entity](pv_forecast_helper.md) and the
[module input matrix](shared_inputs.md).

The **algorithm horizon remains the current quarter-hour through local midnight**.
Only this part of the validated input enters FBC v1.0. The current quarter-hour
is counted in full even if partly elapsed. UTC timestamps preserve DST intervals.
The informational source age follows the helper's `source_updated_at`, never its regeneration time.

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
