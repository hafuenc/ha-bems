# Battery seasonal SOC min threshold

This is a shared battery setting. Configure it once per battery for use by FBC
and future battery modules. The user-facing name is **Battery seasonal SOC min
threshold**.

## Setup and later changes

Add HA-BEMS in Home Assistant's Devices & services and enter a battery name.
Choose one of these sources:

- **Monthly schedule:** twelve percentage fields, January to December. The
  editable defaults are Florian's values: `90, 75, 40, 30, 20, 20, 20, 20, 30,
  40, 75, 90`. They are suggestions to review for the user's installation.
- **Existing entity:** select a `sensor`, `number` or `input_number` whose
  current state is a number between 0 and 100 and whose unit is `%`. This entity
  supplies the active threshold, not the measured battery SOC. Its seasonal
  scheduling is managed by the user outside HA-BEMS. No monthly helpers are
  required by HA-BEMS.

Both modes are implemented from the first setup version. Configure the entry
later to edit the schedule or switch sources. A saved schedule is retained when
switching to an existing entity, so it can be selected again later.

HA-BEMS exposes the resolved value as a sensor attached to the named battery.
Monthly mode follows Home Assistant's local calendar month and refreshes at
local midnight. Entity mode follows changes of the selected source. Missing,
unavailable, nonnumeric, nonfinite, out-of-range or non-percent source values
make the sensor unavailable. There is no silent fallback to the schedule or zero.
The entry's own output cannot be selected as its source.

## Meaning for modules

For FBC, SOC strictly below this threshold disables the charging limitation,
allowing the configured maximum charging power. This preserves the MATLAB
comparison. It does not force charging or establish a discharge prohibition.

Future EEG/feed-in control may explicitly use this shared schedule as its export
reserve. That module and its reserve enforcement are not implemented yet.

The pure Python FBC function accepts the active value through
`seasonal_soc_min_threshold_percent`. When that argument is omitted, it uses the
monthly schedule in `FBCSettings`, which now defaults to Florian's values. A
future runtime caller must stop calculation when an entity source is unavailable;
it must not substitute `None`, which intentionally selects monthly mode in the
standalone calculation API.

## Current implementation limits

The initial integration now contains setup, options and the shared threshold
sensor. The FBC calculation is still independent: forecast ingestion, periodic
FBC calculation and battery writes are not connected. No changes have been made
to the running Home Assistant installation. The UI has not been exercised inside
Home Assistant yet.

For manual installation, copy `custom_components/ha_bems` to the Home Assistant
configuration directory's `custom_components` folder, restart Home Assistant,
then add HA-BEMS under Devices & services. This is a development version.
