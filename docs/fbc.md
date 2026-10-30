# FBC (Forecast based charging)

Select **HA-BEMS → Configure → Add operation planner → FBC**. There is no algorithm/version
selector. With no building load selected, the original v1 calculation remains
unchanged. Selecting a load forecast supplies PV surplus to the same engine.
Electricity prices do not enter either calculation. Its virtual device defaults to **FBC Battery 1**, of
type **Battery control strategy**. Existing entry names/IDs are retained.

HA-BEMS always publishes operating plans only. A user-defined HA automation
translates those plans to the installation's device commands. No HA-BEMS module
will directly control batteries, heat pumps, EVs or other plant.

## Setup inputs

- Actual battery SOC entity: available numeric 0..100 with unit `%`. The picker
  filters for percentage entities and setup validates the current value.
- Battery seasonal SOC min threshold entity: an existing percentage source or
  the helper created under **BEMS entity creator**. No duplicate helper is required.
- A shared PV forecast registered under **BEMS forecast importer**, using the
  documented 72-hour contract.
- An optional building load forecast: choose an already configured HA-BEMS
  non-flexible load profile or a **Building load forecast** registration from the
  importer. Choose **None — use PV generation only** to keep original behaviour.
- Usable (net) battery capacity in kWh corresponding to the selected SOC
  sensor's full 0–100% range, target SOC in %, and maximum charging power in W.
- **PV forecast-to-battery storage efficiency**: one constant factor, e.g. 0.95.
  It represents the fraction of forecast PV energy becoming stored battery energy.
  Include conversion/charging losses once for the selected AC or DC forecast.

Target SOC is the charge level FBC aims to reach by the end of the day. Keep it
aligned with the maximum SOC configured in the battery system; FBC does not
change that device setting.

Maximum charging power is the single setting for both the charging-power
overrides and the current output limit. The separate device output cap has been
removed. Existing entries retain their configured maximum charging power;
the old independent cap is discarded. The current output is capped directly
at that maximum, including when the old cap was lower. Battery settings and
entity IDs are preserved.

## PV generation forecast input

Register a prepared sensor under **BEMS forecast importer → PV AC generation forecast or PV DC generation forecast**
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
Only this part of the validated input enters FBC. The current quarter-hour
is counted in full even if partly elapsed. UTC timestamps preserve DST intervals.
The informational source age follows the helper's `source_updated_at`, never its regeneration time.

## Optional building load

Both inputs must cover the current quarter through at least now + 72 hours with
aligned 15-minute intervals. Generated profiles are reused through their stable
item identity; renaming their sensor does not break selection. External
load sensors first pass [building load import](load_forecast_helper.md). Failure,
disable, removal or expired coverage invalidates FBC without silently switching
to PV-only mode; the maximum-power overrides below remain available.

For each interval, prepare `max(0, PV power - building load power)` and use that
series in the unchanged threshold calculation. Demand must be before PV/battery
balancing and must exclude battery charging. A generated non-flexible profile
excludes separately controlled heat pumps and EVs; an external forecast may
include them if their expected consumption is known. Do not count loads twice.

Choose either an AC or a DC PV registration in FBC; its type is displayed beside
the input name and reported as `pv_power_type` in the plan. HA-BEMS uses the
selected power directly, without AC/DC conversion. Match charging efficiency to
the source. When subtracting load, keep demand and PV on a consistent basis;
account for any conversion losses externally. It does not
recover clipped DC production or simulate battery discharge in deficit periods.
Negative balance becomes zero charging surplus. Actual SOC updates correct past
discharge in subsequent calculations, without guaranteeing the end-of-day target.

## User enable switch

Each FBC virtual device includes **FBC charging limitation enabled**, a normal HA
switch usable from the UI or automations. On means normal calculation; off means
the current output and every remaining interval equal **Maximum charging power**.
The output stays available, with `override_reasons: [disabled_by_switch]`.
No physical device is controlled. Allowing maximum charging does not request grid
charging or allocate PV between EV and battery.

New switches default on. Their last on/off state is restored on reload/restart,
before the first charging plan is published. Turn the switch off to bypass FBC;
disabling the switch entity in the entity registry is different and leaves the
planner waiting for switch initialisation. Manual bypass requires no SOC,
seasonal threshold, PV or load input. Switching back on validates inputs again.

## Algorithm

1. Required input energy is
   `max(0, target_soc - current_soc) / 100 * capacity_kwh / efficiency`.
2. Use the PV-only curve, or the prepared nonnegative PV-surplus curve if load is
   selected. Search synthetic thresholds from 0 to maximum curve power in 10 W steps.
   Select the first threshold minimising absolute mismatch between required
   energy and forecast energy above that threshold.
3. Charging power per interval is `max(0, curve power - threshold)`, rounded to 0.1 W.
4. At **SOC <= seasonal minimum**, replace the full profile with maximum
   charging power.
   The HA adapter can issue this plan without a valid PV forecast. An invalid SOC
   or threshold still makes the plan unavailable.
5. Apply the first-value override: compare first charging power and
   first input-curve power rounded to whole W; if equal, allow maximum power throughout.
   With load selected, this refers to surplus rather than gross PV. The inherited
   rule also applies when the first curve value is zero, even if later PV exists.
6. Clamp the current first value to the configured maximum charging power.
   The non-override future profile is not individually capped.

No future battery SOC trajectory, dynamic prices or physical
charging-power clipping is included in the threshold energy matching. Target SOC
is an aim, not a guarantee. User automations must interpret the charging-limit
output on the correct equipment power basis.

## Published plan and status

The sensor state is the current maximum recommended charging power in W. The
`forecast` attribute contains interval start/end and `power_w` through midnight.
Recalculation occurs on input changes and every quarter-hour; numerical work
runs outside the HA event loop. `status` is `plan_ready`, and `application` is
`user_automation`. This describes a plan, never confirmation of physical application.

The PV importer's one-minute health checks do not themselves trigger FBC when
only validation time or source-age metadata changes. Forecast values/coverage,
input failures/recovery, actual SOC and seasonal threshold changes still trigger
recalculation. The 15-minute forecast resolution does not impose a strict
15-minute controller update cycle.

Load-provider changes and switch operations also trigger recalculation.
`charging_limitation_enabled` describes the switch, `load_consideration_active`
reports whether the plan used load, and `calculation_input` is `pv_generation`,
`pv_surplus` or `maximum_charging_override`. A selected load's name, source age
and coverage use `load_` attributes, separate from PV source metadata.

`limitation_disabled`, `override_reasons` and the active seasonal threshold explain
overrides. The seasonal-only plan has `forecast_status` equal to
`not_required_for_seasonal_override`; no synthetic threshold/energy result is
invented for that path. Otherwise, source metadata and pre-override energy
diagnostics are included. Invalid input makes the sensor unavailable and removes
its last exposed forecast; the user's applying automation must handle that state.

For standalone result review, run `python -m examples.fbc_calculation`. Preserve
existing operational control until the user decides to enable their applying
automation. HA-BEMS itself always remains a planning system.
