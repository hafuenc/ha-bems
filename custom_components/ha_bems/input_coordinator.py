"""Validate imported forecasts once and share the resulting snapshot."""

from datetime import timedelta
import logging

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .forecast_input import ForecastUnavailable, async_read_forecast

_LOGGER = logging.getLogger(__name__)


class ForecastImportCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, parent=None):
        super().__init__(hass, _LOGGER, name=entry.title, config_entry=parent or entry,
                         update_interval=timedelta(minutes=1))
        self.config = {**entry.data, **entry.options}
        self.input_status = "unavailable"
        self._previous_health = None

    @callback
    def _async_refresh_finished(self):
        # HA normally suppresses notifications for consecutive failed refreshes.
        # Our always-readable status must still show a *changed* failure type
        # or reason, for example unavailable -> invalid.
        health = (self.input_status, str(self.last_exception))
        if (not self.last_update_success and self._previous_health is not None
                and self._previous_health[0] != "ready" and health != self._previous_health):
            self.async_update_listeners()
        self._previous_health = health

    async def _async_update_data(self):
        try:
            rows, details = await async_read_forecast(self.hass, self.config)
        except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
            if isinstance(err, ForecastUnavailable):
                self.input_status = "unavailable"
            else:
                self.input_status = "invalid"
            raise UpdateFailed(str(err)) from err
        self.input_status = "ready"
        return {"forecast": rows, **details, "input_validated_at": dt_util.now().isoformat()}
