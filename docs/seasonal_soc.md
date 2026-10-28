# Battery seasonal SOC min threshold

This is a shared battery setting. Configure it once per battery for use by FBC
and future battery modules. The user-facing name is **Battery seasonal SOC min
threshold**.

## Setup and later changes

Add HA-BEMS in Home Assistant's Devices & services, choose **BEMS entity creator
→ Battery seasonal SOC min threshold**, and enter a name.
Then enter twelve percentages, January to December. The editable defaults are
`90, 75, 40, 30, 20, 20, 20, 20, 30, 40, 75, 90`. They are suggestions for Central
European solar irradiation conditions, to be reviewed for the user's installation.
Configure the entry later to edit the monthly values.

HA-BEMS exposes the active value as a sensor attached to the named virtual device.
It follows Home Assistant's local calendar month and refreshes at local midnight.

If you already have a threshold entity, select it directly in FBC setup. It must
be a `sensor`, `number` or `input_number` with a numeric value from 0 to 100 and
unit `%`. No duplicate entity needs to be created. This is a current percentage
input, not a forecast; it does not belong in the entity importer.

### Existing wrapper entries

Previously created helpers that forward an external percentage entity remain
supported. Their Configure dialog opens the existing-source selection directly;
new helper setup no longer offers this mode. Their entity IDs and saved sources
are retained. Missing, unavailable, nonnumeric, nonfinite, out-of-range or
non-percent values make such a wrapper unavailable, without a fallback to the
monthly schedule or zero. The wrapper cannot select its own output as its source.

## Meaning for modules

For FBC, SOC **at or below** this threshold produces a complete plan at the
configured maximum charging power. This is the FBC meaning of leaving battery
operation to its standard behavior. It deliberately changes the original MATLAB
strict `<` comparison to `<=`.

Electricity consumption varies unpredictably, and forecasts inevitably contain
errors. This threshold acts as a safeguard: it reduces the flexibility available
to HA-BEMS while lowering the risk of undesirable battery operation.

HA-BEMS only publishes the plan. The user's automation applies it to the actual
battery; HA-BEMS never restores a device mode itself. The maximum-power plan does
not force charging and can be calculated even if the PV forecast is unavailable.

Future EEG/feed-in control may explicitly use this shared schedule as its export
reserve. That module and its reserve enforcement are not implemented yet.

The pure Python FBC function accepts the active value through
`seasonal_soc_min_threshold_percent`. When that argument is omitted, it uses the
monthly schedule in `FBCSettings`, which now defaults to Florian's values. A
runtime caller must stop calculation when an entity source is unavailable;
it must not substitute `None`, which intentionally selects monthly mode in the
standalone calculation API.

## Current implementation limits

The integration includes setup, options and the shared threshold sensor. FBC can
select this sensor or any existing valid percentage entity and calculate
recommendations. Direct device writes are permanently outside HA-BEMS's scope.
The next manual setup review is performed by the user after the HACS update.

For manual installation, copy `custom_components/ha_bems` to the Home Assistant
configuration directory's `custom_components` folder, restart Home Assistant,
then add HA-BEMS under Devices & services. This is a development version.
