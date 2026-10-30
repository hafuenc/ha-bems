# HA-BEMS

**Home Assistant Building Energy Management System**

> **Development notice:** HA-BEMS is under active development and is not yet
> intended for public use of any kind. This public repository is provided for
> development transparency only. Features, interfaces and behaviour may change
> without notice.

Modular planning for flexible devices and energy storage. Version **0.9.3**
uses one HA-BEMS configuration entry with shared forecast-importer and
entity-creator service devices. Named runtime-cost forecasts, load helpers and
battery planners remain independently configurable.
**HA-BEMS always provides plans only. Your own Home Assistant automations apply
them to your equipment.** Direct device control is outside the scope of every
HA-BEMS module and future version.

Thermal runtime-cost setup uses an editable COP lookup table. The importer
shows diagnostic input-status entities; imported sources remain with their
original integrations. The **Visit** link opens the HA-BEMS integration page.
The **BEMS forecast importer** supports PV AC/DC, import/export prices and outdoor
air temperature. FBC users choose their PV source. Imported-input statuses share
one importer device, and generated helpers share one creator device. Existing
standalone or grouped entries migrate at startup. Thermal operating costs and a feed-forward thermal MILP operation planner are implemented. Runtime-cost
forecasts are created under BEMS entity creator.
Thermal MILP planners calculate on the fixed HH:00/15/30/45 clock; startup and
reloads can calculate immediately.
[Service-device setup and migration](docs/grouped_setup.md).
[Thermal runtime-cost setup and output](docs/thermal_runtime_cost.md).

## Install for the first manual check

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/hafuenc/ha-bems` with type **Integration**.
3. Download HA-BEMS and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → HA-BEMS**.
5. Create the single HA-BEMS installation, then open its **Configure** menu.
6. Choose **BEMS forecast importer**, **BEMS entity creator** or **Add operation
   planner**. Use **Configure an item** or **Remove an item** for existing items.

The repository is a HACS custom repository; it is not included in the default
HACS catalogue. Version 0.7.0 requires Home Assistant 2026.10 or newer.

## Updates

HACS supplies the HA-BEMS update entity and Home Assistant's update suggestion.
Each published version has a matching GitHub release, tag and manifest version.
Keep the HACS update entity enabled. For a just-published release, open HA-BEMS
in HACS and choose **Update information** from its menu if necessary. Custom
repositories may otherwise take up to the next scheduled refresh (normally
48 hours) to display a new release. A change on GitHub's main branch alone is
not treated as a new release. Install the offered version and restart Home
Assistant when ready; HA-BEMS never installs updates automatically itself.

For 0.7.0, existing FBC entries migrate into the single HA-BEMS installation while
retaining their entity IDs and selections. Entries stay PV-only unless a building load is explicitly
selected in **Configure**. Each battery gets an **FBC charging limitation enabled**
switch, initially on. Turning it off publishes maximum charging power; no device
commands are sent. The switch remembers its state across restarts. You do not
need to delete and recreate existing entries.

## Setup structure in this version

```text
HA-BEMS
├── BEMS forecast importer [shared service device]
│   ├── PV AC / DC input-status entities
│   ├── Building-load input-status entities
│   ├── Import / export price input-status entities
│   ├── Outdoor-temperature input-status entities
│   └── Operation-constraint input-status entities
├── BEMS entity creator [shared service device]
│   ├── Seasonal SOC threshold entities
│   ├── Non-flexible load forecast entities
│   └── Thermal runtime-cost forecast entities
└── Individually named operation-planner devices
    ├── FBC battery devices
    └── Thermal storage MILP planner devices
```

Create a seasonal threshold first, or select an existing percentage entity in
FBC setup. The load-profile helper is independently optional; select it in FBC
to consider expected building consumption, or leave load empty for PV-only behaviour.
Each helper/module is a named item managed through **HA-BEMS → Configure**.
Multiple instances are supported, including batteries with separate settings.
BEMS entity creator supports multiple runtime-cost setups using the same procedure,
with independent COP, thermal power and forecast selections.
Older entries migrate into this layout while retaining entity IDs and selections.

### BEMS entity creator

**Battery seasonal SOC min threshold** uses twelve editable default percentages:

| Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 90 | 75 | 40 | 30 | 20 | 20 | 20 | 20 | 30 | 40 | 75 | 90 |

These editable defaults are suggestions for Central European solar irradiation
conditions and should be reviewed for the user's battery.
If a threshold sensor, number or input_number already exists, select it directly
in FBC setup. The creator only creates monthly schedules. Existing wrapper entries
remain supported; missing or invalid source states make them unavailable without
a silent fallback. [Threshold setup and semantics](docs/seasonal_soc.md).

**Non-flexible load profile** scales annual non-flexible consumption in kWh to
15-minute intervals using bundled BDEW/VDEW H0, H25 or a constant curve. It exposes
current estimated power in W and a rolling 72-hour `forecast` attribute.
The full local calendar year's energy matches the annual input, including leap
years and DST. Holiday setup offers Austria/Germany and a country-specific
Bundesland dropdown, including National holidays only. These are
standard-profile estimates rather than measured or learned building demand.
[Load-profile calculation and sources](docs/load_profile.md).

Users can supply existing entities directly to modules. The entity creator will
initially supply only these two helpers. The importer registers prepared external
PV AC/DC, non-flexible building load, electricity import/export prices and outdoor
air temperature forecasts for shared use. [Price and temperature format](docs/price_temperature_forecasts.md).

### Electrical storage management: FBC

Configure measured SOC in %, the seasonal threshold source, battery parameters
and a PV AC or DC generation forecast selected by the user. Optionally select a configured HA-BEMS load profile
or imported building load forecast; FBC then uses `max(0, PV generation - load)`
as its calculation input. Leaving load empty preserves PV-only behaviour. The virtual
device's suggested name is **FBC Battery 1**, with type **Battery control strategy**.

First register one prepared **PV generation forecast entity** under **BEMS
forecast importer → PV AC generation forecast or PV DC generation forecast**. Its `forecast` attribute
must contain gap-free 15-minute records (`start`, `end`, `power_w`) covering the
current interval and at least the next 72 hours. Setup tests the format, coverage
then shows a successful preview. Source age is informational only. The former provider,
custom-template and field-mapping options are removed.
[Helper format and Open-Meteo example](docs/pv_forecast_helper.md).
Then select the named imported input in FBC. Several modules can share it. The
importer exposes an input-status sensor without duplicating the forecast.
Existing FBC entries must be configured to select a registered PV input after
upgrading; battery settings and entity IDs are retained.
[Shared input strategy and module dependency matrix](docs/shared_inputs.md).

FBC still plans only through local midnight. One constant **PV
forecast-to-battery storage efficiency** converts forecast energy into stored
battery energy. [FBC details](docs/fbc.md).

When **SOC is at or below the seasonal minimum**, the complete planned charging
profile equals the configured maximum charging power. Maximum charging
power in W is also the current output limit; no separate output-cap input is
needed. Target SOC in % should match the battery system's configured SOC limit. The seasonal
safeguard can produce the maximum-power plan even while forecasts are unavailable.

Each virtual device has an **FBC charging limitation enabled** switch, initially
on and restored across restarts. Turning it off publishes the maximum charging
power for the complete remaining plan, independently of input availability.
Users can operate it manually or with their own automations; it never sends
commands to the physical battery.

FBC accepts either registered AC or DC PV power without conversion. Choose
charging efficiency for that source. With load selected, keep PV and demand on a
consistent power basis, accounting for conversion losses externally. The
calculation does not reconstruct clipped PV peaks or simulate discharge during load deficits. Both inputs
need aligned 15-minute records with full rolling 72-hour coverage.
[Building load helper format and copy-and-paste AI prompt](docs/load_forecast_helper.md).

The **FBC recommended charging limit** sensor reports the current recommendation
in W and the remaining charging profile in its `forecast` attribute. It updates
on input changes and each quarter-hour. An unavailable or invalid input makes
the recommendation unavailable with a reason, unless the seasonal safeguard
or disabled enable switch already determines a maximum-power plan. A failed
selected load never silently falls back to PV-only behaviour. Users supply the automation that
applies the values to their own installation; HA-BEMS never writes setpoints.

## Build a forecast helper with an AI assistant

Copy this prompt and replace the placeholders with your actual source entity IDs
and attribute examples from HA Developer tools → States:

```text
Create a Home Assistant YAML template sensor accepted by the HA-BEMS PV entity
importer. My provider is [PROVIDER], my source entities are [ENTITY IDS], my
timezone is [TIMEZONE], and their actual state/attribute examples are [EXAMPLES].
Ask for missing source details rather than guessing fields, units or timestamps.

Required output: available sensor with numeric finite nonnegative current-quarter
mean power as state, unit_of_measurement W, integer attribute schema_version: 1,
source_updated_at (timezone-aware ISO 8601, original source update time), and
forecast (a native list, not a JSON string). Each record must contain start and
end (timezone-aware ISO 8601, exactly 900 seconds apart) and power_w (finite,
nonnegative numeric interval-average W).

Publish normally 289 contiguous aligned quarter-hour intervals from the current
quarter, covering at least now + 72 hours. Use UTC stepping across DST; no gaps,
duplicates or overlaps. Convert source units and point-power/interval-energy
semantics correctly. Include explicit source-backed nighttime zeros; never
invent missing data or future days. Make the sensor unavailable on missing data.
Preserve the original source update timestamp: use the oldest contributing source update,
not now() on helper regeneration. Source age is informational; there is no
maximum-age rejection. Timestamps >5 minutes in the future are rejected.

Return complete YAML with a unique_id, availability handling, source-change,
quarter-hour and startup updates, plus an initial refresh method. Explain safe
merging with existing templates, configuration checking, reload and verification
of all attributes and the full 72-hour horizon. Do not write device setpoints.
Then explain registering this sensor under HA-BEMS → BEMS forecast importer → PV
generation forecast and selecting that named input in FBC.
```

[Detailed prompts for any PV provider, Open-Meteo and troubleshooting](docs/helper_prompts.md)
include the full requirements and source-information placeholders. Electricity
price and outdoor temperature importers use their own value fields and units;
these PV prompts apply only to the PV power schema. [Exact input contract](docs/pv_forecast_helper.md).
For consumption forecasts, use the separate
[building load helper prompt](docs/load_forecast_helper.md#copy-and-paste-ai-prompt).

## Overall strategy and future modules

### Home battery / electrical storage energy management

Keep these simpler strategies available:

- **FBC:** the existing calculation engine with user-selected AC or DC PV input
  and optional non-flexible load.
- **Night feed-in for the energy community:** planned export scheduling while
  respecting the configured battery reserve.

Later, offer **Battery MILP** as an alternative strategy covering both use cases
and additional decisions such as importing cheap grid electricity or blocking
battery operation at selected prices. Its detailed price/block rules remain to
be defined. Users can keep the simpler strategies when they do not need MILP.
For any one battery, Battery MILP replaces the simpler active strategies; they
must not independently issue competing setpoints.

### Heat pump / thermal storage energy management

Under **Add operation planner → Thermal storage management (MILP)**, select a
[runtime-cost forecast](docs/thermal_runtime_cost.md), define constant or outdoor
temperature dependent demand in h/24h, storage duration and minimum on/off times.
HiGHS calculates a 72-hour feed-forward plan with native on/off history and an
estimated storage-duration forecast. Optional imported operation permissions
support a DHW → UFH hierarchy. There are no equipment-feedback inputs or device
commands. [Setup, output and hierarchy](docs/thermal_operation_planner.md).

### Electric vehicle

Reserve an additional category for later. Manufacturer/wallbox PV-surplus
charging is sufficient for the present scope. No EV controller is implemented.

### Hierarchy and shared planning core

The current separation of responsibilities is:

```text
Shared actual values, forecasts, prices and helper entities
    ↓
BEMS entity creator: reusable runtime-cost forecasts
    ↓
Thermal MILP operation planner + optional upstream operation permissions
    ↓
Operating recommendations applied by user automations
```

Use a common interval contract: timezone-aware start/end timestamps, explicit
units and interval-average powers (with interval energy where useful). Share
sources across modules, track input freshness and carry forward committed
operating windows and predicted loads. Every plan must state which input
snapshot and upstream plans it used. A failed or stale upstream plan must not
silently be interpreted as zero demand. Coordination with electrical storage
remains a future design task.

Keep calculations independent from the HA UI and source adapters. Coordinate
plans and battery energy budgets inside HA-BEMS; a user-defined automation is
the final authority applying them to each device. Thermal MILP uses HiGHS via
SciPy with a bounded solve time; FBC retains its independent calculation engine.

The current code implements one installation with shared service devices,
individually configurable items, reusable entity selection,
the common timestamped forecast shape, background calculation and hierarchical
thermal MILP planning. Joint PV/battery reservations and battery MILP remain
future design tasks.

## Development and reference status

Attribution and data provenance are documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No general source-code license
has been selected yet.

Credentials, private measurement data and the local project handover are excluded
from version control. Source syntax, profile-energy calculations and standalone
Python results are checked locally. The complete setup and live input mappings
still need their first manual check inside the user's Home Assistant instance.
