# Shared BEMS inputs: generator and importer

HA-BEMS separates shared input preparation from device strategies.

- **BEMS entity creator** generates a missing input: seasonal SOC minimum or
  non-flexible building load. Existing seasonal percentage entities remain usable.
- **BEMS entity importer** registers an existing, prepared forecast entity,
  validates it and shares its snapshot with consuming modules. The first input
  type is PV generation. Import/export electricity prices are future input types.
- **Device modules** select the registered inputs they need. FBC no longer has
  provider options, a template editor, field mappings or its own source-age setting.

## Configure a PV input and FBC

1. Prepare a forecast helper using the [PV input contract](pv_forecast_helper.md).
   An Open-Meteo example is included; any provider can supply that same contract.
   [Copy-and-paste AI prompts](helper_prompts.md) help build a compatible helper
   from your provider's actual entities and attributes.
2. Add HA-BEMS → **BEMS entity importer → PV generation forecast**.
3. Name the shared input, select the prepared sensor and set its maximum original
   source age. Submit to test, review the 72-hour preview, then submit to register.
4. Add or configure **Electrical storage management → FBC** and select that named
   imported PV input. Several batteries can select the same registration.

The importer exposes an **Input status** sensor on a virtual **BEMS entity
importer** device. Its states are `ready`, `stale`, `unavailable` and `invalid`.
Attributes identify the source, accepted horizon, source age, validation time
and a failure reason when relevant. The original helper retains the forecast;
the status entity does not duplicate its large forecast attribute.

Source changes trigger validation, and a one-minute periodic check detects age
and horizon expiry even if the source stops updating. Consumers receive input
change notifications and also check age/current coverage before calculating.
They never use a retained successful snapshot after the importer reports failure.
Changing the importer's source or age setting updates all its consumers without
reconfiguring them. Disable/delete/unload makes the selected input unavailable;
reloading it reconnects consumers through the stable registration ID. No fallback
to another import or an old direct entity selection is made.

FBC still has its seasonal safeguard: at/below the minimum SOC it can publish a
maximum-power plan without PV data. Otherwise an invalid input invalidates the
plan. Setup always requires a working imported forecast, including when the
seasonal safeguard is active.

## Input matrix: implemented modules

| Input | How it is supplied | FBC v1.0 |
| --- | --- | --- |
| Actual battery SOC in % | Existing HA entity, selected directly in FBC | Required |
| Seasonal minimum SOC in % | BEMS entity creator or existing HA entity | Required |
| PV generation forecast | Prepared HA entity registered in BEMS entity importer | Required at setup; seasonal runtime exception above |
| Non-flexible building load | BEMS entity creator; external import not yet implemented | Not used |
| Electricity import price forecast | Future importer type | Not used |
| Electricity export price forecast | Future importer type | Not used |
| Outdoor temperature forecast | Future importer type | Not used |
| Upstream thermal operating plans | Future module outputs | Not used |

Battery capacity, efficiency, target SOC and power limits are module parameters,
not shared forecast entities. The generated load and seasonal SOC helpers are
input providers, not consumers of the imported PV forecast.

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

Forecast types can share timezones, interval boundaries, source timestamps,
availability and horizon rules. Their value semantics remain type-specific:
PV uses interval-average W; future prices need an explicit currency, price per
kWh, import/export meaning and treatment of taxes/fees. Negative electricity
prices must remain representable; PV's nonnegative-power rule must not be reused
for prices. Price horizon/resolution requirements will be defined when those
modules are implemented, rather than assuming 72-hour tariffs exist everywhere.
