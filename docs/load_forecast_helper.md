# Shared building load forecast

Register a prepared sensor under **BEMS entity importer → Building load forecast**.
Select that registration as the optional building load input in FBC. An existing
HA-BEMS non-flexible load profile is selected directly, without an importer entry.

The power forecast contract is the same as the [PV contract](pv_forecast_helper.md),
with **building consumption** as its value meaning:

| Location | Required value |
| --- | --- |
| Sensor state | Current quarter-hour's mean demand; finite, nonnegative numeric W |
| Unit | `W` |
| `schema_version` | Integer `1` |
| `source_updated_at` | Original source issue/update time, ISO 8601 with timezone |
| `forecast` | Native ordered list of `{start, end, power_w}` records |
| Each interval | Exactly 900 seconds, aligned quarter-hours with timezone-aware timestamps |
| Coverage | Current quarter through at least now + 72 hours; normally 289 intervals |

Values represent demand before PV/battery balancing, not net grid import or
historical consumption. Exclude battery charging so it is not subtracted from
PV twice. A non-flexible forecast excludes separately scheduled flexible loads;
a total-building forecast may include their predicted demand. Document which
loads it includes. An annual consumption figure alone is not this forecast;
use the HA-BEMS profile creator if a standard-profile estimate is sufficient.

Example attributes (only two intervals shown; the real list needs full coverage):

```yaml
schema_version: 1
source_updated_at: "2026-10-28T10:00:00+00:00"
forecast:
  - start: "2026-10-28T10:15:00+00:00"
    end: "2026-10-28T10:30:00+00:00"
    power_w: 650
  - start: "2026-10-28T10:30:00+00:00"
    end: "2026-10-28T10:45:00+00:00"
    power_w: 820
```

Do not replace missing values with zero. Zero is valid only for predicted zero
demand. Preserve source timestamps when rolling the helper forward, and use UTC
stepping across DST. Source age is information only; timestamps over five minutes
in the future are rejected. The importer checks format/coverage before saving,
on source changes and at least once a minute. Its status sensor reports `ready`,
`unavailable` or `invalid`; it does not duplicate the source's forecast attribute.

A selected load's failure, expiry, disable or removal makes normal FBC unavailable;
there is no silent PV-only fallback. The seasonal SOC and user-switch maximum-power
overrides do not require forecasts. [FBC semantics](fbc.md).

## Copy-and-paste AI prompt

```text
Build a Home Assistant forecast helper for HA-BEMS's Building load forecast
importer. My sources are [ENTITY IDS / PROVIDER], timezone [TIMEZONE], HA version
[VERSION], and actual source states/attributes are [EXAMPLES]. Inspect the sources
or ask me about missing fields, units, timestamps and demand scope before guessing.

Output a sensor with a stable unique_id, unit W, and finite nonnegative numeric
current-quarter mean building demand as its state. Required attributes:
- schema_version: integer 1.
- source_updated_at: timezone-aware ISO 8601 original source issue/update time,
  not helper rendering time; use the oldest contributing timestamp if combining sources.
- forecast: a native list, not JSON text. Each record contains timezone-aware
  ISO 8601 start/end, exactly 900 seconds apart, and finite nonnegative numeric power_w.

Publish normally 289 contiguous, ordered, quarter-hour-aligned intervals starting
at the current quarter and ending at least now + 72 hours. Use UTC stepping across
DST. Convert interval energy to mean power using its actual duration. Do not fill
missing data with zero, extrapolate unsupported days or use net grid import as
building demand. Become unavailable if full coverage cannot be produced.

Demand is before PV/battery balancing and excludes battery charging. Explain
whether flexible loads are included and avoid double counting. Refresh on source
changes, startup and every quarter-hour. Preserve the original source timestamp
between source updates; there is no maximum-age rejection parameter.

Provide the helper configuration, safe installation steps preserving existing
templates, and a format/coverage verification. Do not change any physical devices
or existing control automations. If my source cannot supply 72 hours, explain
that limitation rather than inventing values.
```
