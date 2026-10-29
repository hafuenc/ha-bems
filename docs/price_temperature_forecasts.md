# Electricity price and outdoor air temperature imports

Register prepared sensors under **HA-BEMS → BEMS forecast importer**, choosing
**Electricity import price forecast**, **Electricity export price forecast** or
**Outdoor air temperature forecast**. Each is an independent reusable input.
These imports prepare data for future thermal/battery optimization; FBC itself
does not use electricity prices or outdoor temperature.

| Type | Sensor unit | Field in each forecast record | Meaning |
| --- | --- | --- | --- |
| Import price | `EUR/kWh` | `price_eur_per_kwh` | Full marginal electricity import tariff, including applicable grid fees, taxes and VAT |
| Export price | `EUR/kWh` | `price_eur_per_kwh` | Actual remuneration for exported electricity |
| Outdoor air temperature | `°C` | `temperature_c` | Interval-average outdoor air temperature for COP and heating-demand estimates |

The sensor state must be a finite numeric value for the current forecast
interval, in the specified unit. Negative prices, negative export remuneration
and subzero temperatures are valid. Missing values, booleans, strings in record
value fields, NaN and infinity are invalid. No automatic unit conversion occurs.

Required attributes:

- `schema_version`: integer `1`.
- `source_updated_at`: original source issue/update time as an ISO 8601 string
  with timezone. For combined sources, use the oldest contributing update.
  Source age is informational; timestamps more than five minutes in the future
  are rejected. Do not reset this timestamp to now on every helper regeneration.
- `forecast`: native list of records, each containing `start`, `end` and the
  type-specific numeric value field above. Start/end are timezone-aware ISO
  8601 strings, exactly 15 minutes apart and aligned to the quarter-hour.

Records must be ordered and contiguous without overlaps, duplicates or gaps,
including the current quarter-hour through at least the current instant + 72
hours. A rolling helper normally needs 289 intervals. Step in UTC across DST.
Historical records are permitted and discarded before consumption. At most
10,000 records are accepted.

Illustrative price attributes (two records only; a real source needs the full horizon):

```yaml
schema_version: 1
source_updated_at: "2026-10-29T10:00:00+00:00"
forecast:
  - start: "2026-10-29T10:00:00+00:00"
    end: "2026-10-29T10:15:00+00:00"
    price_eur_per_kwh: -0.02
  - start: "2026-10-29T10:15:00+00:00"
    end: "2026-10-29T10:30:00+00:00"
    price_eur_per_kwh: 0.18
```

Temperature uses the same envelope and timestamps, replacing the price field
with, for example, `temperature_c: -4.5` and setting the sensor unit to `°C`.

Prepare provider-specific data outside HA-BEMS. An hourly tariff can be repeated
over its four constituent quarters when it applies throughout that hour. A
constant tariff can be repeated across the horizon when it genuinely applies
there. Convert EUR/MWh or cents/kWh explicitly in the helper. For point weather
forecasts, document the interpolation and interval averaging used by the helper.

Published day-ahead prices alone generally do not cover a rolling 72-hour
horizon. If predictions supplement published prices, that forecast method and
provenance belong in the source helper. HA-BEMS never extends a missing tariff,
zero-fills a gap or generates temperature/price predictions during import.

Setup validates the source, then previews interval count, UTC coverage, minimum,
maximum and source age. It revalidates before saving. Configure can change the
source while retaining the registration ID. A diagnostic Input status sensor
reports `ready`, `unavailable` or `invalid` and the reason for failure, without
duplicating the forecast list. Source updates and a one-minute check maintain
the shared snapshot. Disabled, removed or invalid inputs cannot supply a retained
successful snapshot to consumers.

PV AC/DC and non-flexible load continue to use the separate nonnegative
`power_w`/W contract; see [PV](pv_forecast_helper.md) and
[building load](load_forecast_helper.md).
