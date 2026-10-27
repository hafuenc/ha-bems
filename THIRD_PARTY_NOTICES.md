# Third-party references and data

## PVprog reference

The MATLAB FBC reference attributes the original algorithm to J. Bergner,
J. Weniger and T. Tjaden, *PVprog-Algorithmus*, version 1.1, HTW Berlin, 2016.
Florian Wenig's adaptations use full DC PV production and a monthly SOC
override. Attribution is retained in the MATLAB reference and Python port.

## Standard load profile tables

`custom_components/ha_bems/data/load_profiles.json` contains numeric household
profile tables extracted from the original BDEW/VDEW downloads. It includes
SHA-256 hashes of the downloaded source archives/workbooks.

- H0: `Haushalt-Lastprofil.xls`, sheet `Profil`, from
  [VDEW Profile.zip](https://www.bdew.de/media/documents/Profile.zip), 1999/2000.
- H25: sheet `H25`, from
  [BDEW 2025 workbook](https://www.bdew.de/media/documents/Kopie_von_Repr%C3%A4sentative_Profile_BDEW_H25_G25_L25_P25_S25_Ver%C3%B6ffentlichung.xlsx).
- [BDEW source and application notes](https://www.bdew.de/energie/standardlastprofile-strom/).

The packaged table stores W per 1,000 kWh/year before dynamisation and annual
normalisation. H25 source values (kWh/quarter-hour for 1,000,000 kWh/year) were
multiplied by four to obtain these units. H0 already uses the target units.
The original workbooks and publications are not bundled or relicensed.

To reproduce the extraction, place the original downloads under the ignored
`local_data/profile_sources` folder, extract the 1999 workbook into `1999`, and
run `scripts/build_profile_data.py` with openpyxl and xlrd installed. These
spreadsheet readers are development tools, not HA runtime dependencies.

## Runtime dependency

[holidays](https://github.com/vacanza/holidays) supplies national/regional public
holiday dates under its own MIT license. It is installed as a dependency rather
than copied into this repository.
