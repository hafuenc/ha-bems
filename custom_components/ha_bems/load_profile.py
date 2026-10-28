"""Local 15-minute estimates of non-flexible consumption from annual kWh."""

from datetime import UTC, datetime, timedelta
from functools import lru_cache
import json
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays

STEP = timedelta(minutes=15)


@lru_cache(maxsize=1)
def _tables():
    return json.loads((Path(__file__).parent / "data/load_profiles.json").read_text(encoding="utf-8"))["profiles"]


@lru_cache(maxsize=12)
def year_shape(year: int, timezone: str, profile: str, country: str, subdivision: str):
    """Return UTC-aligned slots; normalise across actual local-year duration.

    Compute off the HA event loop. UTC stepping explicitly preserves both
    autumn repeated hours and omits nonexistent spring hours.
    """
    if profile not in ("H0", "H25", "constant"):
        raise ValueError("Unknown load profile")
    tz = ZoneInfo(timezone)
    first = datetime(year, 1, 1, tzinfo=tz).astimezone(UTC)
    end = datetime(year + 1, 1, 1, tzinfo=tz).astimezone(UTC)
    calendar = holidays.country_holidays(country, subdiv=subdivision or None, years=year) if country else {}
    tables = _tables() if profile != "constant" else None
    weights = []
    cursor = first
    while cursor < end:
        local = cursor.astimezone(tz)
        if profile == "constant":
            value = 1.0
        else:
            date = local.date()
            if date.weekday() == 6 or date in calendar:
                day = "sunday"
            elif date.weekday() == 5 or (date.month == 12 and date.day in (24, 31)):
                day = "saturday"
            else:
                day = "workday"
            if profile == "H25":
                period = str(date.month)
            elif (5, 15) <= (date.month, date.day) <= (9, 14):
                period = "summer"
            elif (date.month, date.day) >= (11, 1) or (date.month, date.day) <= (3, 20):
                period = "winter"
            else:
                period = "transition"
            day_number = date.timetuple().tm_yday
            dynamic = (-3.92e-10 * day_number**4 + 3.2e-7 * day_number**3
                       - 7.02e-5 * day_number**2 + 2.1e-3 * day_number + 1.24)
            value = tables[profile][period][day][local.hour * 4 + local.minute // 15] * dynamic
        weights.append(value)
        cursor += STEP
    # A sum of normalised slot energies of 1.0 represents the annual total.
    total = sum(weights)
    return first, tuple(value / total for value in weights)


def load_forecast(config: dict, now: datetime, timezone: str, hours=72) -> list[dict]:
    annual = float(config["annual_consumption_kwh"])
    if not isfinite(annual) or annual < 0:
        raise ValueError("Annual consumption must be finite and nonnegative")
    tz = ZoneInfo(timezone)
    cursor = now.astimezone(UTC).replace(second=0, microsecond=0)
    cursor -= timedelta(minutes=cursor.minute % 15)
    records = []
    # Include the partly elapsed current quarter plus a full 72 hours ahead.
    for _ in range(hours * 4 + 1):
        year = cursor.astimezone(tz).year
        first, weights = year_shape(year, timezone, config["load_profile"],
                                    config.get("holiday_country", "AT"),
                                    config.get("holiday_subdivision", ""))
        energy = weights[int((cursor - first) / STEP)] * annual
        records.append({
            "start": cursor.isoformat(), "end": (cursor + STEP).isoformat(),
            "power_w": round(energy * 4000, 6), "energy_kwh": round(energy, 9),
        })
        cursor += STEP
    return records
