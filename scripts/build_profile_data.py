"""Extract H0/H25 numeric tables from the original BDEW workbooks.

Development only: requires openpyxl and xlrd. Original downloads stay in ignored
local_data/profile_sources. The installed integration needs neither Excel reader.
"""

import hashlib
import json
from pathlib import Path

import openpyxl
import xlrd

sources = Path("local_data/profile_sources")
h0 = xlrd.open_workbook(str(sources / "1999/Haushalt-Lastprofil.xls")).sheet_by_name("Profil")
h25 = openpyxl.load_workbook(sources / "bdew_2025.xlsx", read_only=True, data_only=True)["H25"]
rows = list(h25.values)
daytypes = ("saturday", "sunday", "workday")
profiles = {"H0": {}, "H25": {}}
for season_index, season in enumerate(("winter", "summer", "transition")):
    profiles["H0"][season] = {
        day: [h0.cell_value(row, 1 + season_index * 3 + day_index) for row in range(3, 99)]
        for day_index, day in enumerate(daytypes)
    }
for month in range(1, 13):
    # H25 is kWh/quarter-hour for 1,000,000 kWh/year. Convert to W for
    # 1,000 kWh/year, matching H0: value * 4000 / 1000 = value * 4.
    profiles["H25"][str(month)] = {
        day: [round(rows[row][2 + (month - 1) * 3 + day_index] * 4, 6) for row in range(4, 100)]
        for day_index, day in enumerate(daytypes)
    }
for periods in profiles.values():
    for days in periods.values():
        for values in days.values():
            assert len(values) == 96 and all(value >= 0 for value in values)
result = {
    "source": "BDEW/VDEW, original 1999 H0 and 2025 H25 workbooks",
    "unit": "W per 1000 kWh/year before dynamisation and annual normalisation",
    "sha256": {name: hashlib.sha256((sources / name).read_bytes()).hexdigest()
               for name in ("bdew_1999.zip", "bdew_2025.xlsx")},
    "profiles": profiles,
}
target = Path("custom_components/ha_bems/data/load_profiles.json")
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(result, separators=(",", ":")) + "\n", encoding="utf-8")
print(f"Extracted H0 and H25 to {target}")
