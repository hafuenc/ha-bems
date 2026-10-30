# Changelog

## 0.9.1

- Fix selecting No operation constraint in thermal planner setup. Use an explicit
  nonempty dropdown value and normalize it to no constraint when saving. Existing
  empty or omitted selections remain supported.

## 0.9.0

- Add independently named thermal storage MILP planners under Add operation
  planner, using existing EUR/timestep forecast entities and HiGHS via SciPy.
- Support constant or outdoor-temperature-dependent runtime demand in h/24h,
  a lookup table with runtime limits and optional heating cutoff, storage
  duration bounds, optional initial level, and minimum on/off times.
- Publish native on/off operation-plan entities and estimated storage-duration
  entities, both with 72-hour forecast attributes. Persist the feed-forward
  model and carry operating commitments across replanning without feedback.
- Add operation-constraint forecast imports with direct or inverted binary
  semantics. Support a one-way DHW → UFH hierarchy, reject cycles, and enforce
  imported zero permissions as hard operating blocks.
- Validate solver results, report infeasibility and missing inputs explicitly,
  and provide an explicit model restart after expired saved plans.

## 0.8.1

- Remove imported-forecast entity links, the custom forecast overview and its
  frontend dependencies. Restore the importer device's Visit link to the
  HA-BEMS integration page. Forecast registrations, diagnostic statuses and
  thermal runtime-cost calculations are retained.

## 0.8.0

- Enter outdoor-temperature/COP lookup tables in one multiline text field,
  prefilled to the selected 2–10 reference point count. Accept decimal dots or
  commas with semicolon-separated pairs. Default to four reference points:
  -7/2.32, 2/3.12, 7/4.11 and 12/4.61. Preserve saved values when editing and
  validate row count, unique temperatures and positive finite COPs.
- Simplify thermal runtime costs to nominal thermal output divided by COP;
  remove additional electrical power from setup and calculation. Older saved
  values are ignored and dropped on save. Clarify nominal heat-pump output,
  constant COP examples and the outdoor temperature dependent COP lookup table
  with 2–10 reference points.
- Open a forecast-source overview from the importer device's Visit link. Show
  each source's live value, BEMS validation status and coverage, with links to
  HA entity details. Include disabled inputs and validation errors.
- Add source entity navigation URLs to imported-input status attributes.
  Forecast sources retain their original integration and device ownership.
- Use BEMS forecast importer and BEMS entity creator consistently in Configure,
  clarify feed-in limitation at the grid connection point, and remove
  development references from user-facing descriptions and guides.

## 0.7.0

- Flatten HA-BEMS into one configuration entry with shared BEMS forecast importer
  and BEMS entity creator service devices. Forecast statuses and generated helper
  entities no longer create a separate device or category entry for every item.
- Add a central Configure menu to add, edit, rename, enable/disable and remove
  individual forecasts, helpers and operation planners. Planners retain their
  individual devices; thermal operation planning remains open.
- Migrate standalone and grouped installations while retaining item settings,
  sensor/switch entity IDs, shared-input references and battery device IDs.
  Consolidate old importer/helper devices and retain their areas on the entities.
  Automations targeting a removed per-helper device should use its preserved
  entity IDs or the new shared device instead.

- Move the generic thermal runtime-cost generator to BEMS entity creator. Use
  the same procedure for every named instance, with independently configured COP
  and other system settings; no DHW/UFH setup modes are introduced.
- Migrate v0.6.0 cost setups from Thermal storage management while preserving
  their names, settings, entity IDs and references. Retain per-item disabled
  state and support resuming interrupted ownership moves.
- Reserve Thermal storage management for a future generic operation planner.
  Its detailed configuration and implementation remain open.

## 0.6.0

- Add independently named thermal runtime-cost setups under Thermal storage
  management, for example DHW and UFH. Publish current EUR/timestep and 289
  quarter-hour forecast records covering the rolling next 72 hours. Keep demand,
  storage duration and the future MILP engine separate from cost preparation.
- Support constant COP or 2–10 freely defined temperature/COP anchors with linear
  interpolation/extrapolation and a visible configured COP fallback. Include
  nominal thermal output and optional concurrent auxiliary electrical power.
- Match the MATLAB grid/PV/export-limit cost split using registered PV AC,
  import/export prices and optional non-flexible load. Support otherwise curtailed
  PV pricing and negative costs. Cost setups independently evaluate PV; they do
  not reserve shared PV or allocate it jointly between devices.
- Validate and preview inputs before saving, recalculate on source changes and
  each minute, and remove published forecasts on essential-input failure.

- Group the integration page into BEMS forecast importer, BEMS entity creator,
  Electrical storage management and Thermal storage management category cards.
  Add/configure/remove individual named items through native subentries.
- Migrate existing standalone entries at startup, preserving entity IDs, device
  names/areas, FBC PV/load selections and per-item disabled state. Retire old
  entry shells after startup; interrupted registry moves can be resumed.
- Keep FBC enable-switch restoration and settings independent for multiple
  batteries in one electrical-storage category.
- Require Home Assistant 2026.10+ for the grouped implementation and migration.

- Rename BEMS entity importer to BEMS forecast importer in setup and documentation.
- Add explicit PV AC and PV DC registrations. FBC lists both with their type and
  uses the user's chosen source without conversion; existing generic PV entries
  retain their IDs, source and operation until explicitly classified in Configure.
- Add electricity import price, export price and outdoor air temperature imports,
  including typed units/fields, negative values, setup previews, status sensors,
  source subscriptions and shared snapshots for future planning modules.
- Retain the existing non-flexible load importer and generated load profiles.
  All forecast imports require current-quarter and rolling next-72-hour coverage
  with contiguous 15-minute intervals. Missing future prices are never invented.


## 0.5.0

- Link the original H0 (VDEW 1999/2000) and H25 (BDEW 2025) profile tables and
  BDEW application notes directly in load-profile setup and Configure.

- Add an FBC charging limitation enabled switch per battery virtual device.
  Restore its state across reloads/restarts; off publishes the configured maximum
  for the current output and full remaining plan without needing source inputs.
- Make building load optional in FBC: select a configured HA-BEMS load profile
  or a shared imported building load forecast. Feed max(0, PV minus load) into
  the unchanged v1 engine; leaving load empty retains PV-only behaviour.
- Add a building load forecast importer with the same strict 15-minute/72-hour
  power forecast contract, setup preview, runtime health and consumer lifecycle.
  Never silently fall back when a selected load fails, except that seasonal and
  user-switch maximum-power overrides do not require forecast inputs.
- Remove the FBC algorithm/version selector. Report input mode and override
  reasons in output attributes; retain existing FBC sensor IDs and battery settings.
- Extend generated load forecasts to 289 intervals for full rolling 72-hour
  coverage, expose schema/source metadata and display load power in whole W.

- Replace the load profile's free-text holiday region with country-specific
  Bundesland dropdowns for Austria and Germany, including National holidays only.
  Preserve existing region selections in Configure and skip the region step for
  weekends-only calendars.

- Keep minute-by-minute PV health validation while notifying FBC only of changed
  forecast values/coverage or input health. Validation-only timestamps and source
  age no longer cause redundant planning; SOC and quarter-hour triggers remain.

- Simplify the seasonal SOC creator to a name and editable monthly schedule.
  Select existing percentage entities directly in FBC; they are not forecast
  imports. Existing wrapper entries remain supported and editable.

Update through HACS and restart Home Assistant. Existing FBC entries keep their
PV-only behaviour until a building load is explicitly selected in Configure.
The new FBC charging limitation enabled switch defaults on and remembers its state.
HA-BEMS continues to publish plans only; no physical device commands are sent.

## 0.4.1

- Fix imported PV notifications running outside HA's event loop, which prevented
  FBC from recovering after quarter-hour forecast updates. Register the decorated
  callback directly and add a thread-aware notification regression test.

- Label target SOC explicitly in % and explain the day-end target and alignment
  with the battery system's own maximum SOC. Label usable capacity in kWh.
- Merge charging power and output cap into one Maximum charging power in W
  parameter. Preserve the configured maximum charging power for existing entries
  and discard the old cap; the runtime output limit uses that maximum directly.

- Clarify usable (net) battery capacity as the energy corresponding to the
  selected SOC sensor's full 0–100% range, in setup and options.

- Remove the maximum original source update age parameter and all age-based
  rejection in PV import setup and FBC runtime. Previously saved limits are ignored.
- Keep source timestamps/age as information; retain timestamp format/clock checks
  and complete, gap-free 72-hour forecast validation.
- Update setup wording, helper documentation and copy-and-paste AI prompts.

Update through HACS and restart Home Assistant to load the corrected notification
handler. Existing FBC entries keep their configured maximum charging power as
the single output limit; no device commands are sent by HA-BEMS.

## 0.4.0

- Add BEMS entity importer as a separate setup category, initially for shared PV
  generation forecasts. Electricity prices and other forecasts are marked coming soon.
- Let FBC select a registered input; reuse one validated snapshot across modules.
- Expose input health without duplicating forecast attributes. Propagate updates,
  failure, disable/delete and reload to consumers; recheck current age/coverage.
- Add the generator/importer strategy and implemented/future module input matrices.

- Replace all PV source adapters, template editors and field mappings with one
  prepared forecast sensor registered with the shared importer.
- Block setup until the sensor passes format, original-source freshness and
  complete 72-hour coverage checks at 15-minute resolution. Show a successful
  test preview and repeat validation during runtime.
- Document helper design and provide a reusable Open-Meteo concatenation example.
- Preserve FBC v1.0's remaining-day calculation and seasonal SOC override.

Existing FBC entries: register a compatible helper such as
`sensor.pv_generation_forecast_72h` in BEMS entity importer, then use FBC Configure
to select that named import. Old PV input modes are removed; no automatic
conversion or fallback is performed. Battery parameters, names and entity IDs
are retained. HA-BEMS continues to publish plans only.

## 0.3.0

- Fix the non-flexible load-profile dropdown preventing the setup form from opening.
- Add a prepared Open-Meteo Solar Forecast input using its Energy Production
  Today sensor, plus an editable custom Jinja forecast template and forecast preview.
- Retain the previous interval-record mapping as an advanced input for existing entries.
- Clarify virtual-device names, percentage SOC selection and seasonal-helper creation.
- Name the algorithm "FBC v1.0 - only PV generation considered"; default the new
  virtual device to "FBC Battery 1", type "Battery control strategy".
- Use maximum planned charging power when SOC is **at or below** the seasonal
  minimum, including when a PV forecast is unavailable. Reject an output cap
  below that configured maximum.
- Explain one constant PV forecast-to-battery storage efficiency and the editable
  Central European seasonal defaults.
- Establish the permanent plans-only architecture: user-defined HA automations
  apply results to equipment. No direct device commands are sent by HA-BEMS.
- Publish a regular GitHub release for native HACS update suggestions.

Existing entry titles, device identifiers and entity IDs are preserved. The
seasonal comparison changes from `<` to `<=`; the status changes from
`observation_only` to `plan_ready`, with `application: user_automation`. If existing
automations inspect these attributes, adapt them accordingly. Existing maximum
power/output-cap combinations must satisfy cap >= maximum; use Configure to fix
an inconsistent pair before relying on that plan.

## 0.2.0

First modular HACS version with seasonal SOC and load-profile helpers,
the generic forecast-record adapter, and the initial Python FBC recommendations.
