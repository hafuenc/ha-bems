# Shared PV generation forecast entity

HA-BEMS accepts one prepared Home Assistant **sensor**. Its provider-specific
conversion belongs in a helper outside HA-BEMS. There are no provider selectors,
template editors or field-mapping options inside HA-BEMS. Register the prepared
entity once under **BEMS entity importer → PV generation forecast**; consuming
modules such as FBC select that registration. See the [input matrix](shared_inputs.md).

## Required format (schema version 1)

| Location | Required value |
| --- | --- |
| Sensor state | Current forecast interval's average power: finite, nonnegative numeric value |
| `unit_of_measurement` | `W` |
| `schema_version` attribute | Integer `1` |
| `source_updated_at` attribute | ISO 8601 timestamp with timezone: original input's last update |
| `forecast` attribute | Ordered list of records as shown below |
| Each record's `start` and `end` | ISO 8601 timestamps with timezone offsets; exactly 15 minutes apart |
| Each record's `power_w` | Finite, nonnegative JSON number: interval-average power in W |

Illustrative attributes (two records shown; a real sensor needs the full horizon):

```yaml
schema_version: 1
source_updated_at: "2026-10-27T12:27:46+00:00"
forecast:
  - start: "2026-10-27T13:15:00+00:00"
    end: "2026-10-27T13:30:00+00:00"
    power_w: 5963
  - start: "2026-10-27T13:30:00+00:00"
    end: "2026-10-27T13:45:00+00:00"
    power_w: 5720.5
```

Timestamps must be on the quarter-hour, with no gaps, overlaps, duplicates or
out-of-order records. The list must cover the **current quarter-hour through
at least the current instant + 72 hours**. Historical records are permitted
before that period. Nighttime must be represented explicitly with zero power;
missing or unavailable data must never be replaced with zero. Extra attributes
and record fields are allowed. At most 10,000 records are accepted.

A helper updated every 15 minutes should normally publish **289 intervals**
starting at the current quarter-hour. Exactly 288 intervals starting at the
quarter-hour will fall short of 72 hours when the quarter is partly elapsed.
Step forward in absolute time/UTC to preserve the correct intervals across
daylight-saving changes; do not assume each local day contains 96 quarters.

`source_updated_at` must follow the source, rather than being set to `now()` on
each helper regeneration. When combining sources, use the oldest update time
of the sources actually used. Use a provider's forecast issue timestamp when
available; otherwise its HA source-state update time is a documented proxy.
The default maximum age is 180 minutes and can be configured on the shared importer.
Timestamps more than five minutes in the future are rejected as clock errors.

## Example: Open-Meteo Solar Forecast

[Download the template block](../examples/pv_generation_forecast_72h.yaml).
It combines `sensor.energy_production_today`, `sensor.energy_production_tomorrow`
and `sensor.energy_production_d2` through `sensor.energy_production_d4` from
[ha-open-meteo-solar-forecast](https://github.com/rany2/ha-open-meteo-solar-forecast).
Adjust those entity IDs to match your installation. Additional calendar dates
are needed because “today + the next two days” is shorter than 72 hours ahead.

Save the block as `/config/pv_generation_forecast_72h.yaml` and include it under
your existing `template:` list in `configuration.yaml`:

```yaml
template:
  - !include pv_generation_forecast_72h.yaml
```

If `template:` already exists, add only the include item to that list. Do not
create a second top-level `template:` key or overwrite existing templates.
Check HA configuration, then reload Template entities. The helper starts on the
next quarter-hour or source update. For immediate initial calculation, fire
the event `ha_bems_pv_forecast_refresh` in Developer tools → Events.
No battery automation or device command is involved.

The example creates **PV generation forecast 72h**, normally
`sensor.pv_generation_forecast_72h`. It updates on source changes, each quarter
and HA startup. It becomes unavailable if any required endpoint is missing;
no synthetic nighttime or daytime data is inserted.

The example targets the integration's `watts` attribute: timestamped **point
power** samples. It averages adjacent samples to obtain interval-average W,
equivalent to linear interpolation and integration over each quarter. If a
different provider supplies exact interval Wh, convert with `Wh * 4` for a
15-minute interval instead. Do not treat point power as interval energy.
The example's `source_updated_at` uses the oldest contributing HA source-state
update; its separate `generated_at` is only the helper regeneration time.

Keep the source's PV peaks where available. A constant efficiency cannot
reconstruct peaks already lost through inverter clipping.

## Setup test and runtime checks

In **BEMS entity importer → PV generation forecast**, name the input,
select the prepared sensor and submit. Setup checks availability, W units,
schema version, source age, timestamp format, numeric power, quarter-hour
alignment, ordering, continuity, the current interval and the complete 72-hour
future horizon. A failure stays on the selection form and explains the reason.
A successful test shows interval count, first/last timestamps, peak power,
energy over the displayed period and original source update age.

The importer checks again before registration, on source changes and at least
once a minute. FBC validates the selected shared snapshot and its current age
and coverage before calculating; later gaps or stale data invalidate its recommendation. The existing seasonal SOC safeguard can
still recommend maximum charging without a PV forecast. Initial setup always
requires the forecast test to pass, even when that safeguard is active.

**FBC v1.0 still calculates only the remaining local day's charging plan.**
Requiring a reusable 72-hour input does not change its MATLAB-derived horizon.

## Existing HA-BEMS entries

After upgrading from 0.3, register the helper using **BEMS entity importer**.
Then open **Configure** on each existing FBC entry and select that registered input. Names, entity IDs and battery parameters remain.
The previous provider/template/mapping paths are removed and are not evaluated
as fallbacks. A raw provider sensor without this contract fails validation;
update any applying automation to handle an unavailable recommendation as usual.
