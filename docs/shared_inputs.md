# Shared BEMS inputs: generator and importer

HA-BEMS separates shared input preparation from device strategies.

- **BEMS entity creator** groups generated inputs: seasonal SOC minimum,
  non-flexible building load and thermal runtime-cost forecasts.
  Existing seasonal percentage entities remain usable.
- **BEMS forecast importer** registers an existing, prepared forecast entity,
  validates it and shares its snapshot with consuming modules. Supported types
  are PV AC, PV DC, non-flexible building load, import/export electricity prices
  outdoor air temperature, and operation constraints with binary permissions.
- **Device modules** select the registered inputs they need. FBC no longer has
  provider options, a template editor, field mappings or its own source-age setting.

[Shared service devices and migration](grouped_setup.md) keep each input and
module independently configurable through the single HA-BEMS Configure menu.

## Configure a PV input and FBC

1. Prepare a forecast helper using the [PV input contract](pv_forecast_helper.md).
   An Open-Meteo example is included; any provider can supply that same contract.
   [Copy-and-paste AI prompts](helper_prompts.md) help build a compatible helper
   from your provider's actual entities and attributes.
2. Open **HA-BEMS → Configure → BEMS forecast importer → PV AC generation forecast
   or PV DC generation forecast**.
3. Name the shared input and select the prepared sensor. Submit to test, review the 72-hour preview, then submit to register.
4. In **HA-BEMS → Configure**, add an **FBC** operation planner or configure an existing item and select that named
   imported PV input. Several batteries can select the same registration.

The importer exposes an **Input status** sensor on a virtual **BEMS forecast
importer** device. Its states are `ready`, `unavailable` and `invalid`.
Attributes identify the source, accepted horizon, source age, validation time
and a failure reason when relevant. The original helper retains the forecast;
the status entity does not duplicate its large forecast attribute.

Source changes trigger validation, and a one-minute periodic check detects format failures
and horizon expiry even if the source stops updating. The input-status entity
continues to show each validation time. Consumers receive notifications only
when forecast values/coverage or input health change; validation timestamps and
informational source age alone do not trigger a new device plan. Consumers also
check current coverage before calculating.
Source age is reported for information only, without a rejection threshold.
They never use a retained successful snapshot after the importer reports failure.
Changing the importer's source updates all its consumers without
reconfiguring them. Disable/delete/unload makes the selected input unavailable;
reloading it reconnects consumers through the stable registration ID. No fallback
to another import or an old direct entity selection is made.

FBC has two maximum-power overrides: the user enable switch is off, or SOC is
at/below the seasonal minimum. Neither needs PV or load forecasts. Manual bypass
also needs no SOC/threshold values. Otherwise an invalid selected input invalidates
the plan. Setup always tests PV and any selected load, including while an override
would apply at runtime.

The importer also registers **Building load forecast** sensors using the same
15-minute/72-hour nonnegative-power contract, with consumption rather than PV as
the value meaning. See [load helper format and AI prompt](load_forecast_helper.md).
FBC's optional load selector offers both these registrations and existing HA-BEMS
non-flexible load profiles. No load keeps the v1 engine input unchanged; selecting
load uses max(0, PV minus load), without simulating battery discharge. Selections
use stable item IDs (retained from previous configuration entry IDs). Generated providers and imported providers
notify FBC when their forecast or health changes, and on removal/reload.

## Input matrix: implemented modules

| Input | How it is supplied | FBC |
| --- | --- | --- |
| Actual battery SOC in % | Existing HA entity, selected directly in FBC | Required |
| Seasonal minimum SOC in % | BEMS entity creator or existing HA entity | Required |
| PV AC or DC generation forecast (user choice) | Prepared HA entity registered in BEMS forecast importer | Required at setup; maximum-power runtime exceptions above |
| Building load forecast | Existing BEMS load profile or registered external building load forecast | Optional; subtracts demand from PV when selected |
| Electricity import price forecast | Registered forecast sensor | Not used |
| Electricity export price forecast | Registered forecast sensor | Not used |
| Outdoor temperature forecast | Registered forecast sensor | Not used |
| Upstream thermal operating plans | Registered operation-constraint forecasts | Not used |

The [thermal operation planner](thermal_operation_planner.md) uses these inputs:

| Input | How it is supplied | Thermal MILP |
| --- | --- | --- |
| Runtime costs in EUR/timestep | Existing BEMS runtime-cost entity or compatible forecast entity | Required |
| Outdoor temperature | Registered forecast | Required for the runtime lookup model |
| Operation permissions | Registered constraint forecast or existing MILP operation plan | Optional; interpreted permission zero forces operation off |

Imported constraint values always mean **1 = allowed, 0 = blocked**. In each
MILP setup, choose an imported forecast or another MILP's operation plan and
select **Direct** or **Inverted** interpretation. For DHW → UFH, select the DHW
operation plan directly and invert it; no extra importer is required. This supports
a sequential DHW → UFH hierarchy without equipment feedback or shared runtime
subtraction. Invalid upstream plans invalidate downstream planning.

Battery capacity, efficiency, target SOC and power limits are module parameters,
not shared forecast entities. The generated load and seasonal SOC helpers are
input providers, not consumers of the imported PV forecast.

Both PV types use `power_w` in W. Price imports use `price_eur_per_kwh` in
`EUR/kWh`, and temperature uses `temperature_c` in `°C`. Price and temperature
values may be negative. All currently use explicit contiguous 15-minute records
covering the current quarter and at least now + 72 hours. The importer does not
invent missing future tariffs. See [price and temperature contracts](price_temperature_forecasts.md).

## Planning matrix: future modules

This table is a design overview, not a list of implemented features or final
mandatory inputs. Each future strategy will define its own required/optional
inputs in setup. Night feed-in dependencies remain to be specified separately.

| Shared input | Domestic hot water MILP | Building heating MILP | Overall battery MILP |
| --- | --- | --- | --- |
| PV generation forecast | Planned, depending on objective | Planned, depending on objective | Planned |
| Non-flexible building load | Depends on site-power constraints | Depends on site-power constraints | Planned |
| Electricity import prices | Planned for tariff shifting | Planned for tariff shifting | Planned |
| Electricity export prices | Optional opportunity-cost input | Optional opportunity-cost input | Planned for export valuation |
| Outdoor temperature forecast | Not the demand driver; may affect COP | Planned demand/COP driver | Indirectly through thermal plans |
| DHW reserved operating windows | Produces them | Planned input where heat pump is shared | Receives predicted electrical load |
| Heating operating plan | Runs later | Produces it | Planned predicted electrical load |
| Battery SOC and seasonal minimum | Not a direct input | Not a direct input | Planned |

Forecast types share timezone, interval, source timestamp, availability and
horizon validation while keeping type-specific value fields, units and sign
rules. The importer prepares shared snapshots; thermal optimization and its
input selection remain future work. Import tariffs must include the applicable
marginal fees/taxes; export values represent actual remuneration.
