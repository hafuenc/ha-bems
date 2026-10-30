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
- Electricity import and export prices, in `EUR/kWh`.
- Outdoor temperature in `°C`, only when using the COP lookup table.
- Optionally non-flexible building load, either a generated BEMS load profile or
  an imported load forecast. Exclude the thermal tasks costed by this module to
  avoid counting their electrical consumption twice. Leaving load empty explicitly
  makes all forecast PV available to the task.

The input contracts are described in [price/temperature forecasts](price_temperature_forecasts.md)
and [PV forecasts](pv_forecast_helper.md). Existing helpers such as `cs_import`,
`cs_export` and `te_outdoor` must be registered first; the module selects shared
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

## Calculation

For each complete 15-minute timestep:

```text
running_w = thermal_power_kw × 1000 / COP
surplus_w = max(PV_AC_w - non_flexible_load_w, 0)
grid_w = max(running_w - surplus_w, 0)
curtailed_w = min(running_w, max(surplus_w - export_limit_w, 0))
              [zero when no export limit is configured]
export_opportunity_w = min(running_w, surplus_w) - curtailed_w

cost_eur_per_timestep = 0.25 / 1000 × (
    grid_w × import_price_eur_per_kwh
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
running DHW, UFH and other consumers. No PV is reserved between setups and no
battery charging/discharging is modelled. A future coordinated scheduler must
resolve those competing uses.

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
refresh the output through shared-input subscriptions, plus a one-minute rolling
check and quarter-hour updates. The cost calculation runs outside the event loop.

## Failure handling

A missing/invalid temperature forecast, or a non-positive/non-finite extrapolated
COP, uses the explicitly configured fallback COP. Attributes expose
`cop_fallback_active`, `cop_fallback_intervals`, `cop_fallback_reason` and
`temperature_input_error`; affected records set `cop_fallback: true`. Recovery
restores normal lookup operation automatically.

PV, price or selected building-load failures make the cost sensor unavailable
and remove its forecast from the published attributes. An expired 72-hour horizon
also fails validation. BEMS does not manufacture prices, reuse a failed input's
retained good snapshot or silently ignore a selected load. Provider-side helpers
may implement their own explicit price fallback, as `cs_export` does.
