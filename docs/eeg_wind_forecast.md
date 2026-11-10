# Private EEG wind energy forecast

In **BEMS entity creator → Add helper → EEG wind energy forecast (private setup)**,
create a forecast that can be selected directly as the EEG available-energy input
of a thermal runtime-cost helper. No separate forecast importer is needed.

## Private setup password

Every installation uses the **same release password**, chosen by the HA-BEMS
maintainer. Enter this password when opening the EEG wind helper setup. Users
cannot choose their own unlocking password in HA or through a local password file.
The integration bundles only a salted PBKDF2-SHA256 hash in
`custom_components/ha_bems/wind_access_password.json`; the plaintext password
is not stored in the integration, settings or entity attributes.

Unlocking is required separately for each creation or edit of an enabled wind
helper. Five failed attempts block further attempts for five minutes across setup
flows on that HA process. Home Assistant's own authenticated configuration UI
remains the outer access boundary. This is a feature gate in the unmodified
integration, not tamper-proof licensing: because the source code is public,
someone can modify the integration to remove the check or replace the bundled hash.
Disabling or removing an existing helper remains possible without unlocking.
Automatic weather requests do not require the password after configuration.
No per-installation password provisioning is required. Previous
`ha_bems_eeg_setup_password.json` files in the HA configuration directory are
ignored. The setup remains locked if the bundled hash file is missing or invalid.

Maintainers can set a new shared password before publishing an update:

```sh
python scripts/set_eeg_release_password.py
```

Run this from a source checkout. The tool prompts twice without echoing the
password, requires at least 12 characters, and updates only the bundled hash.
Include that file in the release; do not publish the plaintext password in source,
documentation or tests. Changing the hash affects installations after they install
that integration update. Existing configured helpers keep updating automatically.

## Inputs

* Turbine latitude and longitude; the defaults are the HA installation coordinates.
* Hub height: **85 m** by default, supported range **10–180 m above ground**.
  Values outside the available weather heights are rejected rather than extrapolated.
* Constant share: **100%** by default, between 0 and 100%. This is the user's
  assumed share of one representative turbine, not a prediction of dynamic EEG allocation.
* Power curve: one `wind speed in m/s;electrical power in kW` pair per line.
  Decimal dots and decimal commas are accepted. Use 2–200 points, strictly
  increasing nonnegative speeds, and finite nonnegative powers.

The default is the calculated ENERCON E-70 E4 2.3 MW curve with an added zero
point. Its tabulated plateau is **2,310 kW**. Power is interpolated linearly;
below the first point and **strictly above the last point** it is zero. At the
last point itself, its tabulated power applies. With the default curve this
means **v > 25 m/s → 0 kW**. This is the requested simplified shutdown model,
not ENERCON's actual storm-control behaviour. No extra loss, density or turbine-count
factors are applied.

## Weather requests and cached backup

Open-Meteo's public forecast endpoint is requested with these parameters:

```text
hourly=wind_speed_10m,wind_speed_80m,wind_speed_120m,wind_speed_180m
wind_speed_unit=ms
timezone=UTC
timeformat=unixtime
forecast_days=7
cell_selection=land
```

`latitude` and `longitude` come from the helper. No `models` parameter is sent,
so Open-Meteo uses Best Match. Ground elevation is determined automatically.
Weather is shared for identical coordinates, independent of curve, height or
share; multiple helpers at the same location cause only one request per refresh.

The initial request runs when the configured helper loads. Successful requests
are persisted using Home Assistant storage. Further requests are due three hours
after the last successful fetch. On restart, a saved cache younger than three
hours is reused without a request. Failed requests preserve the last valid
cache and retry after 15, 30, 60, 120, then at most every 180 minutes, evaluated
on the quarter-hour clock. Malformed, incomplete or wrongly unit-labelled
responses do not replace a working cache.

Every quarter-hour, the helper selects a moving 72-hour horizon from cached hourly
wind data, linearly interpolates height and time, and integrates the piecewise
linear power curve over each 15-minute interval. Intervals crossing curve knots
or shutdown boundaries are split for integration. This finer output does not
add meteorological detail to hourly weather data.

The integration's existing forecast contract includes the **current quarter-hour
plus 288 future quarter-hours (289 records)**. This keeps direct runtime-cost and
planner inputs aligned and covers a full 72 hours even between clock boundaries.
Each public record contains timezone-aware UTC `start`, `end`, and `power_kw`.
The power is the interval-average integrated curve output scaled by share / 100.
The weather interpolation and power-curve integration are unchanged.

The sensor's value is current-interval mean power, unit `kW`, and its
`forecast` attribute contains the records. `source_updated_at` records the last
successful fetch, separately from local `generated_at`. Attributes also include
`source_age_minutes`, `using_cached_backup`, `weather_refresh_error`,
`next_weather_request_at`, `weather_cache_end`, and attribution.

Since 0.12.0 the public wind profile uses `forecast_value_key: power_kw` and
`forecast_unit: kW`. Existing entity IDs and shares are preserved. Runtime costs
convert generated wind power to interval energy exactly once (`kW × 0.25 h`)
at their input boundary. External EEG energy imports still use `energy_kwh` and
`kWh/timestep`. Dashboards spanning the update must respect the recorded unit:
old `kWh/timestep` wind history is multiplied by four to display it as kW;
new kW history is already power.

A failed request continues producing a usable forecast from the backup while
the saved weather covers the whole horizon. Once it cannot, the sensor becomes
unavailable, its published forecast is removed, and runtime-cost consumers reject
the input. Missing weather is never replaced with fabricated zero production or
an extended tail. Seven requested calendar days do not guarantee seven further
days of backup from any fetch time; consult `weather_cache_end`.

The helper owns its clock even if its display entity is disabled. Disabling the
helper item stops its updates; unused shared caches do not poll autonomously.

## Data and usage

Weather data: [Open-Meteo](https://open-meteo.com/), under
[CC BY 4.0](https://open-meteo.com/en/licence). HA-BEMS derives estimated energy
by interpolation, power-curve mapping and integration; this is not measured production.
Power curve: [ENERCON product overview, July 2010, E-70 E4](https://docs.wind-watch.org/Enercon.pdf).

Password protection does not determine API licence eligibility. Check
[Open-Meteo terms](https://open-meteo.com/en/terms) for the actual use case.
The free endpoint is for non-commercial use, including personal home automation
and public research at public institutions as specified there. Commercial use
requires the appropriate service arrangement; this initial private helper uses
the public endpoint and does not configure commercial credentials.
