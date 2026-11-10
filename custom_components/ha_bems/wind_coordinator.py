"""Shared seven-day Open-Meteo cache and locally rolling EEG energy forecasts."""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .forecast import parse_timestamp
from .wind_forecast import validate_weather, wind_energy_forecast

_LOGGER = logging.getLogger(__name__)
API_URL = "https://api.open-meteo.com/v1/forecast"
REFRESH = timedelta(hours=3)


class WindWeatherCache:
    def __init__(self, hass, latitude, longitude):
        from homeassistant.helpers.storage import Store

        self.hass = hass
        self.params = {"latitude": latitude, "longitude": longitude,
            "hourly": "wind_speed_10m,wind_speed_80m,wind_speed_120m,wind_speed_180m",
            "wind_speed_unit": "ms", "timezone": "UTC", "timeformat": "unixtime",
            "forecast_days": 7, "cell_selection": "land"}
        identity = sha256(f"{latitude},{longitude}".encode()).hexdigest()[:24]
        self.store = Store(hass, 1, f"{DOMAIN}.wind_weather.{identity}")
        self.lock = asyncio.Lock()
        self.loaded = False
        self.weather = None
        self.fetched_at = None
        self.next_attempt = None
        self.failures = 0
        self.error = None
        self.storage_error = None

    async def _request(self):
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        async with asyncio.timeout(30):
            async with async_get_clientsession(self.hass).get(API_URL, params=self.params) as response:
                response.raise_for_status()
                return await response.json()

    async def async_get(self, now):
        """Serialize requests across entities and preserve a valid cache on failure."""
        from aiohttp import ClientError

        async with self.lock:
            if not self.loaded:
                try:
                    saved = await self.store.async_load()
                    if saved:
                        self.weather = validate_weather(saved["payload"])
                        self.fetched_at = parse_timestamp(saved["fetched_at"])
                        if self.fetched_at > now.astimezone(UTC) + timedelta(minutes=5):
                            raise ValueError("Saved weather timestamp is in the future")
                        self.next_attempt = self.fetched_at + REFRESH
                except (OSError, ValueError, TypeError, KeyError, OverflowError):
                    self.weather = self.fetched_at = self.next_attempt = None
                self.loaded = True
            if self.next_attempt is None or now >= self.next_attempt:
                try:
                    payload = await self._request()
                    weather = validate_weather(payload)
                    # Reject truncated responses before overwriting the backup.
                    first_quarter = int(now.timestamp()) // 900 * 900
                    if weather["time"][0] > first_quarter or weather["time"][-1] < first_quarter + 72 * 3600 + 900:
                        raise ValueError("Weather response does not cover the next 72 hours")
                except (ClientError, TimeoutError, ValueError, TypeError, KeyError, OverflowError) as err:
                    # Fixed, credential-free reason; no provider response bodies in entity attributes.
                    self.error = f"Open-Meteo refresh failed ({type(err).__name__})"
                    self.failures += 1
                    self.next_attempt = now + timedelta(minutes=min(15 * 2 ** min(self.failures - 1, 4), 180))
                else:
                    self.weather, self.fetched_at = weather, now
                    self.next_attempt = now + REFRESH
                    self.error, self.failures = None, 0
                    try:
                        await self.store.async_save({"payload": payload, "fetched_at": now.isoformat()})
                    except OSError:
                        self.storage_error = "Weather backup could not be saved; current data remain available in memory"
                    else:
                        self.storage_error = None
            if self.weather is None:
                raise ValueError(self.error or "No cached Open-Meteo forecast is available")
            return self.weather, {
                "source_updated_at": self.fetched_at.isoformat(),
                "source_age_minutes": round((now - self.fetched_at).total_seconds() / 60, 1),
                "using_cached_backup": self.error is not None,
                "weather_refresh_error": self.error, "cache_storage_error": self.storage_error,
                "next_weather_request_at": self.next_attempt.isoformat(),
                "weather_cache_end": datetime.fromtimestamp(self.weather["time"][-1], UTC).isoformat(),
                "weather_provider": "Open-Meteo", "weather_attribution": "Weather data by Open-Meteo (CC BY 4.0); wind converted to estimated power by HA-BEMS",
                "weather_source_url": "https://open-meteo.com/", "weather_model": "best_match",
            }


def weather_cache(hass, config):
    latitude, longitude = float(config["latitude"]), float(config["longitude"])
    caches = hass.data.setdefault(DOMAIN, {}).setdefault("wind_weather", {})
    key = (latitude, longitude)
    if key not in caches:
        caches[key] = WindWeatherCache(hass, latitude, longitude)
    return caches[key]


class WindEnergyCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, parent=None):
        super().__init__(hass, _LOGGER, name=entry.title, config_entry=parent or entry, update_interval=None)
        self.config = {**entry.data, **entry.options}
        self.cache = weather_cache(hass, self.config)
        self.input_status = "unavailable"

    async def _async_update_data(self):
        now = dt_util.now()
        try:
            weather, metadata = await self.cache.async_get(now)
            rows = await self.hass.async_add_executor_job(wind_energy_forecast, self.config, weather, now)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
            self.input_status = "unavailable"
            raise UpdateFailed(str(err)) from err
        self.input_status = "cached_backup" if metadata["using_cached_backup"] else "ready"
        return {"value": rows[0]["power_kw"], "forecast": rows,
                "schema_version": 1, "forecast_value_key": "power_kw", "forecast_unit": "kW",
                "interval_minutes": 15, "generated_at": now.isoformat(),
                "input_validated_at": now.isoformat(), "status": self.input_status,
                "hub_height_m": self.config["hub_height_m"], "share_percent": self.config["share_percent"],
                "shutdown_above_ms": self.config["wind_power_points"][-1][0], **metadata}
