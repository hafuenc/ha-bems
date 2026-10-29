# Copy-and-paste AI prompts for forecast helpers

Copy a complete prompt below into your AI assistant. Replace the square-bracket
placeholders with your source entity IDs and a small sample of their actual
attributes from Home Assistant Developer tools → States. Include source units
and timestamp meaning if known. The assistant should request missing details
rather than guess your provider's format.

These prompts describe the **PV importer, schema version 1**. For consumption
forecasts, use the dedicated [building load helper prompt](load_forecast_helper.md#copy-and-paste-ai-prompt),
which shares the power format but defines demand before PV/battery balancing.
Electricity import/export price importers are not implemented yet; their accepted
formats will be documented when they become available. The PV schema must not
be used to represent prices.

See the [authoritative input contract](pv_forecast_helper.md),
[working Open-Meteo YAML example](../examples/pv_generation_forecast_72h.yaml)
and [module input matrix](shared_inputs.md).

## Prompt 1: build a helper for any PV forecast provider

```text
Create a Home Assistant YAML template sensor that converts my existing PV
forecasts into an entity accepted by the HA-BEMS PV forecast importer, schema 1.

My inputs:
- Home Assistant version: [VERSION]
- Timezone: [TIMEZONE, e.g. Europe/Vienna]
- Forecast integration/provider: [NAME]
- Source entity IDs, including all days needed for at least 72 hours ahead:
  [ENTITY IDS]
- Samples of the actual states and attributes, with timestamps and values:
  [PASTE SOURCE EXAMPLES HERE]
- Source value unit and meaning, if known (point power, interval-average power,
  or energy per interval): [DETAILS]
- Existing template/include configuration, if relevant: [DETAILS OR UNKNOWN]

Ask me for missing source information before inventing field names or timestamp
semantics. If you can access my HA, inspect the source entities first.

Required output entity:
- Suggested name: PV generation forecast 72h; add a stable unique_id.
- State: numeric, finite, nonnegative mean PV power for the current quarter-hour.
- unit_of_measurement: W; device_class: power.
- Required attributes:
  schema_version: the integer 1, not the string "1" or a boolean.
  source_updated_at: ISO 8601 string with timezone, representing the original
    contributing source update, not the time this helper was rendered.
  forecast: a native list of objects, not a JSON string. Every object has:
    start: ISO 8601 string with timezone.
    end: ISO 8601 string with timezone, exactly 900 seconds after start.
    power_w: finite nonnegative number, not a string; mean power in W over
      [start, end).

Coverage and conversion:
- Start at the beginning of the current quarter-hour and normally publish 289
  consecutive 15-minute intervals, so the final end remains at least 72 hours
  after the current instant between updates. A partly elapsed current quarter
  plus 287 following quarters is insufficient. At most 10,000 rows are accepted.
- Align interval boundaries to :00, :15, :30 and :45. Sort/merge source data before
  output; the final list must have no gaps, duplicates, overlaps or disorder.
- Use UTC/absolute-time stepping so daylight-saving changes work correctly.
- Include explicit nighttime zero intervals supported by source data. Never turn
  missing, unknown, unavailable or malformed data into zero, and never invent
  extra forecast days. If the source cannot cover 72 hours, explain the limitation
  and what additional source data is needed.
- Determine what the source values mean before converting. kW * 1000 gives W;
  interval Wh / interval_hours gives mean W; kWh also needs a factor of 1000.
  For exact 15-minute Wh, mean W = Wh * 4. Point-power samples require integration
  across each interval; they must not be treated as interval energy. State any
  interpolation assumptions. Do not reconstruct PV peaks lost through clipping.
- Prefer the provider's actual forecast issue/update timestamp. Otherwise use
  contributing source states' last_updated as a documented proxy. For multiple
  contributing sources use the oldest update time. Never use the helper's own
  last_updated or now() to claim the source is fresh. An optional generated_at
  may separately record helper regeneration.
- Make the sensor unavailable if required source data is missing or invalid;
  do not retain an old forecast as a valid current input.

Use supported Home Assistant template syntax. Update on relevant source changes,
every quarter-hour and HA startup. Provide a way to calculate it immediately
after reloading templates. Include an availability template and preserve native
types for the attributes. The helper must not write device setpoints.

Return complete copy-and-paste YAML, identify what I must adapt, and explain how
to merge it into my existing template configuration without duplicate top-level
template keys or overwriting other sensors. Include configuration checking and
template reload steps.

Also provide a read-only verification template or procedure checking the native
attribute types, current interval, exact 900-second durations, timezone-aware
timestamps, continuity and final end >= now + 72 hours. Check source_updated_at:
Source age is informational only: do not add a maximum-age input or an
age-based availability cutoff. HA-BEMS still rejects timestamps more than five
minutes in the future.
Explain how to register the resulting entity under HA-BEMS → BEMS forecast importer
→ PV AC generation forecast or PV DC generation forecast, pass its test, then select the named import in FBC.
```

## Prompt 2: combine Open-Meteo daily PV forecasts

Use this when your provider is `ha-open-meteo-solar-forecast`. Entity IDs and
available attributes differ between installations and integration versions.

```text
Build a Home Assistant template sensor named "PV generation forecast 72h" for
HA-BEMS's PV forecast importer. I use ha-open-meteo-solar-forecast.

My source entities are:
- Today: [TODAY ENTITY, commonly sensor.energy_production_today]
- Tomorrow: [TOMORROW ENTITY, commonly sensor.energy_production_tomorrow]
- Day +2: [D2 ENTITY, commonly sensor.energy_production_d2]
- Day +3: [D3 ENTITY, commonly sensor.energy_production_d3]
- Day +4: [D4 ENTITY, commonly sensor.energy_production_d4]
Timezone: [TIMEZONE]
HA version: [VERSION]
Actual example attributes, including watts or interval-energy data:
[PASTE SOURCE ATTRIBUTE SAMPLES HERE]

First inspect or ask for the real attribute format. Merge the daily forecasts
across midnight; today plus tomorrow plus day +2 alone cannot provide a rolling
72-hour future horizon. Use additional available dates where necessary.

The output must have a stable unique_id, numeric nonnegative current-interval
mean power as its state, unit_of_measurement W, and these required attributes:
- schema_version: integer 1.
- source_updated_at: timezone-aware ISO 8601 timestamp from the original sources;
  use the oldest timestamp among sources actually contributing to the output.
  Prefer a real provider issue timestamp; source last_updated is a disclosed
  fallback proxy. Do not replace it with the helper regeneration time.
- forecast: a native ordered list of 289 contiguous 15-minute records, starting
  at the current quarter-hour. Each record is an object with start and end
  (timezone-aware ISO 8601 strings exactly 900 seconds apart) and power_w
  (finite nonnegative numeric interval-average W). No JSON-encoded list string.

The final end must be >= now + 72 hours. Step in UTC/absolute time across DST.
Include source-backed nighttime zeros, but never zero-fill missing data or
extrapolate missing future days. Make the sensor unavailable if the required
forecast cannot be assembled completely.

If the source supplies exact interval energy such as wh_period_15m, confirm its
unit and timestamp semantics; 15-minute Wh * 4 gives interval-average W. If it
supplies watts point samples, integrate adjacent points into each interval;
the mean of its two endpoint values is valid for linear interpolation over one
15-minute interval. Do not bridge missing samples silently. Obtain the following
midnight's endpoint from the next day's data when needed. Do not treat hourly
energy records as quarter-hour energy or claim interpolation is exact model data.

Update on source changes, every quarter-hour and HA startup. Supply an initial
refresh method after template reload. Return complete YAML with availability
handling and explain installation without overwriting existing templates. Use
the repository example as a reference if accessible:
https://github.com/hafuenc/ha-bems/blob/main/examples/pv_generation_forecast_72h.yaml

Verify native attribute types, timezone-aware timestamps, quarter-hour alignment,
no gaps/overlaps/duplicates, current-interval coverage, >=72-hour future coverage
and source timestamp format. Report source age for information only; do not
introduce a maximum-age setting or age-based cutoff. Future timestamp tolerance
is five minutes.
Explain registering the sensor under HA-BEMS → BEMS forecast importer → PV
generation forecast and selecting that registered input in FBC. Do not change
the provider's PV model, battery controls or any device setpoints.
```

## Prompt 3: fix an importer test failure

```text
My forecast helper fails the HA-BEMS PV forecast importer test. Diagnose the
specific cause and propose the smallest helper correction, preserving its
entity ID and unrelated Home Assistant configuration.

Importer error: [PASTE EXACT ERROR]
Current HA time and timezone: [TIME AND TIMEZONE]
Helper YAML: [PASTE YAML]
Helper state, unit and required attributes: [PASTE DETAILS]
First/last forecast records and records near any reported gap: [PASTE RECORDS]
Upstream entity IDs and relevant raw attribute samples: [PASTE DETAILS]

Check the schema-1 contract: available sensor with finite nonnegative numeric
power state in W; integer schema_version 1; original-source source_updated_at
as timezone-aware ISO 8601; and forecast as a native ordered list of objects
with timezone-aware start/end and numeric nonnegative interval-average power_w.
Intervals must be aligned, exactly 900 seconds long, contiguous and duplicate-
free, including the current quarter and extending at least 72 hours from now.
Normally a rolling helper needs 289 intervals. Source age is informational,
without a maximum-age limit; timestamps >5 minutes in the future are rejected.

Distinguish missing source data from conversion/template errors. Do not fix a
failure by inventing forecast data, zero-filling gaps, refreshing source_updated_at
with now(), or weakening HA-BEMS validation. Ask for missing source information.
Give corrected YAML and a verification procedure, then explain rerunning the
importer test. Do not apply battery commands or restart HA without my instruction.
```
