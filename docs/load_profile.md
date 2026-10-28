# Non-flexible load profile

Add **HA-BEMS → BEMS entity creator → Non-flexible load profile**. Enter the
annual non-flexible consumption in kWh and choose H0, H25 or constant. Settings
remain editable through the entry's Configure action. Setup and Configure both
link the original H0/H25 profile tables and BDEW background/application notes.

The annual input represents behind-the-meter demand before PV/battery balancing.
Exclude flexible loads that will be separately scheduled (heat pump, EV) to
avoid counting their energy twice. Do not use annual net grid import as an
automatic substitute for total non-flexible demand.

## Data and calculation

- H0 uses the original VDEW household table (1999), three seasons and three day
  types, with the household day-of-year dynamisation polynomial.
- H25 uses BDEW's 2025 household table, twelve calendar months and three day
  types, with the published household dynamisation polynomial.
- Constant distributes the annual energy uniformly over every real quarter-hour
  in the local calendar year. Leap years therefore have a lower constant power
  for the same annual kWh.

Original numeric tables are bundled locally; no online profile service is used.
All three profiles are normalised over the actual selected calendar year so the
integral equals the annual input. This annual normalisation is an HA-BEMS
adaptation for demand estimation, not a claim of certified settlement profiles.
The dynamisation polynomial is applied without intermediate rounding before
annual normalisation. H25 includes some average PV self-consumption influence
in its source population and is not a measurement of this building's gross load.

Choose Austria, Germany or weekends-only for day classification. For Austria
and Germany, the next setup step offers a dropdown of the country's Bundesländer
by name, plus **National holidays only** (the default). Weekends-only skips the
region step. Configure uses the same dropdown; existing region codes and names
are retained, while changing country resets the region to national holidays.
The selected region supplies regional holidays using the `holidays` library.
Sundays and public
holidays use the Sunday curve; 24 and 31 December use Saturday unless already
classified as Sunday/holiday. This is a configurable consumption estimate, not
a BDEW regulatory holiday calendar implementation.

UTC stepping preserves the repeated autumn hour and omits the nonexistent spring
hour. Calendar month/day classification uses Home Assistant's configured local
timezone. Both repeated local hours use their respective local-time curve values,
and full-year normalisation preserves annual energy.

## Entity contract

The sensor state is current estimated interval-average power in **W**. Its
`forecast` attribute holds a rolling **72-hour** horizon at **15-minute**
resolution, refreshed each quarter-hour. It publishes 289 intervals (the current
quarter plus 72 hours ahead), along with integer `schema_version: 1` and
`source_updated_at` equal to profile generation time:

```json
{"start": "2026-10-27T11:00:00+00:00", "end": "2026-10-27T11:15:00+00:00", "power_w": 450.0, "energy_kwh": 0.1125}
```

The first record is the quarter-hour containing now. This is a deterministic
standard-profile estimate, not measured consumption or a learned forecast.
It can be selected directly as FBC's optional building load input, and reused by
future modules. Leaving load unselected retains the original PV-only calculation.
The rolling `forecast` attribute is excluded from recorder history; the current
numeric sensor value remains recordable.

Sources: [BDEW profile downloads](https://www.bdew.de/energie/standardlastprofile-strom/),
[2025 application notes](https://www.bdew.de/media/documents/2025-03-17_AWH_Aktualisierte_SLP_Strom_2025_Ver%C3%B6ffentlichung.pdf).
See `THIRD_PARTY_NOTICES.md` for table provenance and reproducibility.
