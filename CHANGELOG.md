# Changelog

## 0.4.0

- Replace all PV source adapters, template editors and field mappings with one
  prepared forecast sensor using the documented shared schema.
- Block setup until the sensor passes format, original-source freshness and
  complete 72-hour coverage checks at 15-minute resolution. Show a successful
  test preview and repeat validation during runtime.
- Document helper design and provide a reusable Open-Meteo concatenation example.
- Preserve FBC v1.0's remaining-day calculation and seasonal SOC override.

Existing FBC entries: use Configure to select a compatible helper such as
`sensor.pv_generation_forecast_72h`. Old PV input modes are removed; no automatic
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
