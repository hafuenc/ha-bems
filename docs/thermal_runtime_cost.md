# Thermal runtime cost forecasts

Under **HA-BEMS → Configure → BEMS entity creator → Thermal runtime cost (EUR/timestep)**,
create an independent named system. Repeat the same setup procedure for any
number of systems. There are no separate DHW/UFH modes. Each item has its own
parameters, input selections, Configure action, enabled setting and Runtime cost
sensor. Renaming/configuring an item retains its entity identity.

This helper calculates the **cost of operation** as a reusable entity.
Select it in the [thermal operation planner](thermal_operation_planner.md) under
**Add operation planner → Thermal storage management (MILP)**. Cost generation
and operation planning remain separate, independently configurable items.

Existing runtime-cost setups move onto the shared BEMS entity creator service
device at startup. Their settings, user-defined item names, module IDs and
sensor entity IDs are preserved. Item configuration is managed through the
single HA-BEMS Configure menu. No new operating strategy is created by migration.

## Setup

First register the needed forecasts under **HA-BEMS → Configure → BEMS forecast importer**:

- **PV AC** generation. Thermal electrical demand and building load use an AC
  basis. DC registrations remain selectable in FBC, but not in thermal costs.
- Electricity import and export prices, in `EUR/kWh`, when choosing price forecasts
  rather than fixed prices.
- Outdoor temperature in `°C`, only when using the COP lookup table.
- Optionally non-flexible building load, either a generated BEMS load profile or
  an imported load forecast. Exclude the thermal tasks costed by this module to
  avoid counting their electrical consumption twice. Leaving load empty explicitly
  makes all forecast PV available to the task.

The input contracts are described in [price/temperature forecasts](price_temperature_forecasts.md)
and [PV forecasts](pv_forecast_helper.md). Existing helpers such as `cs_import`,
`cs_export` and `te_outdoor` must be registered first when used as forecast inputs; the module selects shared
registrations rather than copying provider-specific attributes.

Each runtime-cost setup asks for:

1. A system name and constant nominal **thermal output in kW**, e.g. the nominal
   thermal output of the heat pump or rated output of an electric heating element.
   Electrical demand is thermal output divided by COP, focusing on compressor
   consumption for heat pumps.
2. A **constant positive COP**, e.g. for a ground source heat pump or direct
   electric heating elements with COP = 1, or an **outdoor temperature dependent
   COP**, e.g. for an air source heat pump. Define **2–10 reference points in a
   lookup table**, using your heat pump specifications. The default count is four,
   prefilled with −7 °C / COP 2.32, 2 °C / COP 3.12, 7 °C / COP 4.11 and
   12 °C / COP 4.61. Keep the desired reference count in the preceding step;
   the text field contains exactly that many rows. Enter one `outdoor temperature;COP`
   pair per line, using decimal dots or commas. The submitted row count must
   match the selected count. Saved reference values are reused when editing;
   reducing the count keeps a subset including both endpoints, and increasing
   it adds linearly interpolated points within the existing temperature range.
   Reference temperatures must be unique and COPs positive; points are sorted
   automatically. The lookup model linearly interpolates between reference points
   and extrapolates using the nearest end segment.
   No supply temperature is modelled. A lookup also requires an outdoor temperature
   registration and a positive fallback COP.
3. Electricity/PV/load registrations and an optional export power limit. With the
   limit enabled, PV above the limit is costed at the configured curtailed-PV
   price, normally zero. A limit of zero means no export. With the option off,
   these two settings are ignored.
4. A calculated preview showing current cost, range, coverage and COP fallback
   use. Sources are revalidated before saving.

Older saved additional electrical power settings are ignored and removed when
the setup is saved again. Only thermal output divided by COP determines electrical
demand.

## Price forecasts or fixed prices

For **grid import**, **grid export** and **EEG**, independently choose a price
source: **Price forecast** or **Fixed price**. A fixed price is entered directly
in `EUR/kWh` and applies to every 15-minute interval of the cost forecast. No price
import registration or external helper is needed for that tariff. Mixed choices
are supported, e.g. dynamic grid import, fixed export and fixed EEG prices.

A fixed value must be entered explicitly; there is no implicit zero-price default.
Zero and negative prices are supported. The forecast selection is ignored in fixed
mode, and the fixed value is ignored in forecast mode. With EEG disabled, its
entire price configuration is ignored. EEG energy availability remains a forecast.

Existing configurations without a source selector continue to use their selected
price forecasts. Fixed prices contribute no source timestamp and subscribe to no
price importer. `source_updated_at` remains the oldest actual contributing forecast
timestamp. Output attributes identify each active price source and fixed value.

## Optional energy community (EEG) supply and load priorities

Register **Energy community (EEG) available energy forecast** in the BEMS forecast
importer. Enable EEG in each runtime-cost setup and select this registration.
Choose an imported **Energy community (EEG) price forecast** or enter a fixed EEG
price directly. Existing setups default to EEG disabled, retaining their previous
calculation. Disabled EEG ignores its price and energy settings.

The price contract uses `EUR/kWh` and `price_eur_per_kwh`. The availability
contract uses `kWh/timestep` and nonnegative `energy_kwh` for each 15-minute
interval. Both use the existing schema_version 1 forecast envelope and rolling
72-hour coverage/fill rules. Prepare both forecasts outside HA-BEMS. Price must
include the applicable consumption-dependent charges, on the same basis as the
import price. Energy is the **total potentially available energy at this metering
point before any load deduction**, rather than an allocation already limited by
predicted consumption. Convert mean W to kWh/timestep by dividing by 4000.
Negative prices are accepted; negative available energy is rejected.

Supply priority is fixed: own PV, then EEG, then residual grid supply. EEG is
used and charged even when more expensive than residual grid supply. Building
and higher-priority loads consume PV first, then EEG. Only the remaining PV and
EEG are available to the device being costed. Costs cover this device only;
considered background loads are deducted from resources, not billed again.
An explicit zero energy value means no EEG supply. Missing/invalid EEG forecasts
follow the same bounded previous-forecast retention as other required inputs;
selected sources are never silently ignored. Deleted or disabled sources fail
immediately.

Keep the existing optional building-load selection. **Additional higher-priority
loads** is an optional multiple-selection field offering registered load forecasts,
generated load profiles and existing MILP plans. No source is selected automatically.
For a MILP plan, HA-BEMS multiplies its operation (0/1) by the electrical power
forecast from its associated runtime-cost entity, on the full-quarter-hour basis.
This conversion is entirely in the cost consumer; the MILP solver and plan outputs
are unchanged. Plan and cost-power updates refresh dependent runtime costs.
Duplicate selections and circular dependencies through BEMS plans are rejected.
Do not include the same physical load in multiple source forecasts.

For a hierarchy A before B, A considers building load only; B considers building
load plus A's plan. Use an existing valid upstream plan when configuring B.
This uses the latest published plan and does not request an upstream solve.
The existing MILP recalculation policy remains unchanged.

Forecast rows additionally expose `eeg_power_w`, `eeg_energy_kwh`,
`eeg_cost_eur_per_timestep` and `considered_load_power_w`. EEG amounts refer to
this device's consumption after background loads have been deducted.

## Calculation

For each complete 15-minute timestep:

```text
running_w = thermal_power_kw × 1000 / COP
total_considered_load_w = non_flexible_load_w + sum(higher_priority_loads_w)
surplus_w = max(PV_AC_w - total_considered_load_w, 0)
remaining_load_w = max(total_considered_load_w - PV_AC_w, 0)
eeg_available_w = max(eeg_energy_kwh * 4000 - remaining_load_w, 0)
eeg_w = min(max(running_w - surplus_w, 0), eeg_available_w)
grid_w = max(running_w - surplus_w - eeg_w, 0)
curtailed_w = min(running_w, max(surplus_w - export_limit_w, 0))
              [zero when no export limit is configured]
export_opportunity_w = min(running_w, surplus_w) - curtailed_w

cost_eur_per_timestep = 0.25 / 1000 × (
    grid_w × import_price_eur_per_kwh
    + eeg_w × eeg_price_eur_per_kwh
    + export_opportunity_w × export_price_eur_per_kwh
    + curtailed_w × curtailed_price_eur_per_kwh
)
```

PV consumption is valued
at lost export revenue, not added on top of the import tariff. Negative prices
and negative operating costs are retained. No taxes, markups, battery losses or
unconfigured additional costs are added inside BEMS.

Example: 7 kW thermal output / COP 3.5 = 2 kW electrical. For one quarter-hour,
grid-only operation at 0.30 EUR/kWh costs 0.15 EUR/timestep. With 1 kW of available
PV and a 0.10 EUR/kWh export price, it costs 0.10 EUR/timestep.

**Each setup evaluates the same available PV independently.** These are
conditional full-activation costs, not additive joint costs for simultaneously
running DHW, UFH and other consumers. No PV or EEG is reserved automatically between setups. To establish priorities,
include higher-priority devices in each lower-priority setup's additional loads,
or use operation constraints. No battery charging/discharging is modelled.

## Output contract

The sensor's numeric state is the **cost of running for the full current
quarter-hour**, not remaining-minute cost or measured electricity expenditure.
Its unit is `EUR/timestep`. The state equals the first forecast value.

```yaml
schema_version: 1
source_updated_at: "2026-10-29T10:00:00+00:00"
interval_minutes: 15
forecast_unit: EUR/timestep
forecast_value_key: cost_eur_per_timestep
forecast:
  - start: "2026-10-29T10:15:00+00:00"
    end: "2026-10-29T10:30:00+00:00"
    cost_eur_per_timestep: 0.10
    cop: 3.5
    electrical_power_w: 2000
    grid_import_power_w: 1000
    pv_export_opportunity_power_w: 1000
    curtailed_pv_power_w: 0
    cop_fallback: false
```

This example contains one record; the real output has **289 ordered, contiguous
UTC quarter-hours** including the current interval and at least the next 72
hours. UTC stepping handles daylight-saving changes. All interval fields are
native numbers/booleans, not formatted strings. The forecast is available live
and excluded from Recorder to avoid storing large rolling attribute lists.

`source_updated_at` is the oldest contributing source timestamp, including the
optional load and valid temperature source. Regenerating the cost forecast does
not reset it. `generated_at` records the calculation time separately. Sources
refresh the output through shared-input subscriptions and the fixed
HH:00/15/30/45 clock, without a separate minute poll. Repeated requests within
the same quarter-hour reuse the calculation when the validated inputs and
settings are unchanged. Input health and coverage are checked before reuse.
The cost calculation runs outside the event loop.

Identical results do not notify entities again merely because calculation or
validation timestamps, or informational source ages, changed. Source freshness,
forecast contents, availability and fallback/recovery changes remain meaningful
updates. A retained forecast keeps its original generation time and expiry.

## Failure handling

A brief temperature refresh failure retains the last valid cost forecast for
at most one hour, selecting its current interval rather than freezing one state.
Only a prolonged failure, exhausted forecast-fill allowance, or invalid
extrapolated COP uses the configured fallback COP. Attributes expose
`cop_fallback_active`, `cop_fallback_intervals`, `cop_fallback_reason` and
`temperature_input_error`; affected records set `cop_fallback: true`. Recovery
restores normal lookup operation automatically.

All forecast consumers may copy the previous value for up to four missing
15-minute intervals in total, including a short tail. The current interval must
exist; invalid values, duplicates and unordered timestamps are not repaired.
`filled_previous` is preserved through derived forecasts so chaining modules
cannot renew the allowance. `filled_previous_intervals` reports the count.

Brief PV, price or selected-load refresh failures can retain the last valid cost
forecast for up to one hour, while its coverage remains within that same fill
allowance. `using_previous_forecast`, `status` and `refresh_error` identify this
mode; the original generation timestamp is retained. Disabled/deleted inputs
still fail immediately. No selected load is silently ignored. Once retention
or coverage limits are exhausted, the sensor becomes unavailable.
