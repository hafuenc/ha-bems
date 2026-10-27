# HA-BEMS

**Home Assistant Building Energy Management System**

Modular planning for flexible devices and energy storage. Version **0.4.0**
creates reusable BEMS entities and forecast-based battery charging plans.
**HA-BEMS always provides plans only. Your own Home Assistant automations apply
them to your equipment.** Direct device control is outside the scope of every
HA-BEMS module and future version.

## Install for the first manual check

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/hafuenc/ha-bems` with type **Integration**.
3. Download HA-BEMS and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → HA-BEMS**.
5. Choose a category from the setup menu below. Add HA-BEMS again to create
   another helper or device module. Existing modules remain configured.

The repository is a HACS custom repository; it is not included in the default
HACS catalogue. The integration requires Home Assistant 2025.1 or newer.

## Updates

HACS supplies the HA-BEMS update entity and Home Assistant's update suggestion.
Each published version has a matching GitHub release, tag and manifest version.
Keep the HACS update entity enabled. For a just-published release, open HA-BEMS
in HACS and choose **Update information** from its menu if necessary. Custom
repositories may otherwise take up to the next scheduled refresh (normally
48 hours) to display a new release. A change on GitHub's main branch alone is
not treated as a new release. Install the offered version and restart Home
Assistant when ready; HA-BEMS never installs updates automatically itself.

## Setup structure in this version

```text
HA-BEMS
├── BEMS entity creator
│   ├── Battery seasonal SOC min threshold
│   │   ├── Editable monthly schedule
│   │   └── Existing percentage entity
│   └── Non-flexible load profile
│       ├── H0
│       ├── H25
│       └── Constant throughout the year
├── BEMS entity importer
│   ├── PV generation forecast
│   └── Electricity prices and additional forecasts — coming soon
├── Electrical storage management
│   └── Forecast-based battery charging (FBC)
└── Thermal storage management
    └── Coming soon
```

Create a seasonal threshold first, or select an existing percentage entity in
FBC setup. The load-profile helper is independently optional; the original FBC
algorithm does not use household-demand forecasts. Each helper/module has its
own named entry and can be edited later using **Configure**. Multiple instances
are supported, for example two batteries with separate sources and settings.
Entries created by version 0.1 remain seasonal-threshold entries.

### BEMS entity creator

**Battery seasonal SOC min threshold** uses twelve editable default percentages:

| Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 90 | 75 | 40 | 30 | 20 | 20 | 20 | 20 | 30 | 40 | 75 | 90 |

These editable defaults are suggestions for Central European solar irradiation
conditions and should be reviewed for the user's battery.
An existing sensor, number or input_number can instead supply the active
percentage. Missing or invalid source states make the result unavailable; there
is no silent fallback. [Threshold setup and semantics](docs/seasonal_soc.md).

**Non-flexible load profile** scales annual non-flexible consumption in kWh to
15-minute intervals using bundled BDEW/VDEW H0, H25 or a constant curve. It exposes
current estimated power in W and a rolling 72-hour `forecast` attribute.
The full local calendar year's energy matches the annual input, including leap
years and DST. Public-holiday country/region can be configured. These are
standard-profile estimates rather than measured or learned building demand.
[Load-profile calculation and sources](docs/load_profile.md).

Users can supply existing entities directly to modules. The entity creator will
initially supply only these two helpers. The importer registers prepared external
PV forecasts for shared use; electricity import/export prices and further forecast
input types will follow as their consuming modules are implemented.

### Electrical storage management: first FBC strategy

The selectable strategy **FBC v1.0 - only PV generation considered** is based on
the existing MATLAB algorithm. Configure measured SOC in %, the seasonal
threshold source, battery parameters and a PV generation forecast. The virtual
device's suggested name is **FBC Battery 1**, with type **Battery control strategy**.

First register one prepared **PV generation forecast entity** under **BEMS
entity importer → PV generation forecast**. Its `forecast` attribute
must contain gap-free 15-minute records (`start`, `end`, `power_w`) covering the
current interval and at least the next 72 hours. Setup tests the format, coverage
and original source age, then shows a successful preview. The former provider,
custom-template and field-mapping options are removed.
[Helper format and Open-Meteo example](docs/pv_forecast_helper.md).
Then select the named imported input in FBC. Several modules can share it. The
importer exposes an input-status sensor without duplicating the forecast.
Existing FBC entries must be configured to select a registered PV input after
upgrading; battery settings and entity IDs are retained.
[Shared input strategy and module dependency matrix](docs/shared_inputs.md).

FBC v1.0 still plans only through local midnight. One constant **PV
forecast-to-battery storage efficiency** converts forecast energy into stored
battery energy. [FBC details](docs/fbc.md).

When **SOC is at or below the seasonal minimum**, the complete planned charging
profile equals the configured maximum charging power. This inclusive comparison
is a deliberate change from the original MATLAB `<` rule. The device output cap
must be at least this maximum; inconsistent settings are rejected. The seasonal
safeguard can produce the maximum-power plan even while a PV forecast is unavailable.

The **FBC recommended charging limit** sensor reports the current recommendation
in W and the remaining charging profile in its `forecast` attribute. It updates
on input changes and each quarter-hour. An unavailable or invalid input makes
the recommendation unavailable with a reason, unless the seasonal safeguard
already determines a maximum-power plan. Users supply the automation that
applies the values to their own installation; HA-BEMS never writes setpoints.

## Overall strategy and future modules

### Home battery / electrical storage energy management

Keep these simpler strategies available:

- **FBC:** selectable algorithm variants; only the existing DC variant is
  implemented initially.
- **Night feed-in for the energy community:** planned export scheduling while
  respecting the configured battery reserve.

Later, offer **Battery MILP** as an alternative strategy covering both use cases
and additional decisions such as importing cheap grid electricity or blocking
battery operation at selected prices. Its detailed price/block rules remain to
be defined. Users can keep the simpler strategies when they do not need MILP.
For any one battery, Battery MILP replaces the simpler active strategies; they
must not independently issue competing setpoints.

### Heat pump / thermal storage energy management

Plan a simple local feed-forward MILP using freely available solvers. A few
explicitly tunable building/heat-pump parameters determine energy demand and
storage flexibility. The first approach uses no indoor-temperature feedback,
historical model training or automatic parameter identification.

- **Domestic hot water:** largely temperature-independent daily demand.
- **Building heating:** outdoor-temperature-driven energy demand.
- Shift operation in time within configured thermal bounds, taking PV
  availability, electricity import prices, a simple COP approximation and
  equipment operating constraints into account.

Where DHW and underfloor heating share a heat pump, run **DHW first**, reserve its
operating windows, then optimise **building heating** with those windows blocked.
These are future modules; the current setup shows only “Coming soon”.

### Electric vehicle

Reserve an additional category for later. Manufacturer/wallbox PV-surplus
charging is sufficient for the present scope. No EV controller is implemented.

### Hierarchy and shared planning core

The intended final planning order is:

```text
Shared actual values, forecasts, prices and helper entities
    ↓
Domestic hot water optimisation
    ↓  reserves heat-pump operating windows and predicts electrical load
Building-heating optimisation
    ↓  respects DHW windows and predicts additional electrical load
Battery MILP
    ↓  sees non-flexible demand + scheduled thermal electrical demand
One coordinated set of device recommendations
```

Use a common interval contract: timezone-aware start/end timestamps, explicit
units and interval-average powers (with interval energy where useful). Share
sources across modules, track input freshness and carry forward committed
operating windows and predicted loads. Every plan must state which input
snapshot and upstream plans it used. A failed or stale upstream plan must not
silently be interpreted as zero demand. Sequential optimisation is the intended
hierarchy; it is not a claim of a globally joint optimum.

Keep calculations independent from the HA UI and source adapters. Coordinate
plans and battery energy budgets inside HA-BEMS; a user-defined automation is
the final authority applying them to each device. Future solver selection and runtime limits will be based on
actual local-hardware measurements; no solver package is needed for this FBC
release. HiGHS is a candidate for the later open-source MILP implementation.

The current code implements separate module entries, reusable entity selection,
the common timestamped forecast shape and background calculation. The future
thermal orchestration, reservations and battery MILP described above are design
requirements, not implemented features.

## Development and reference status

The original MATLAB references are preserved under `matlab_reference`:
`fbc_original_excerpt.m` for current FBC and `fcn_solver_gurobi.m` for later thermal
optimisation work. Attribution and data provenance are documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No general source-code license
has been selected yet.

Credentials, private measurement data and the local project handover are excluded
from version control. Source syntax, profile-energy calculations and standalone
Python results are checked locally. The complete setup and live input mappings
still need their first manual check inside the user's Home Assistant instance.
