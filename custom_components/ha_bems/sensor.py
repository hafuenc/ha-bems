"""Expose a shared threshold for FBC and future battery modules."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.util import dt as dt_util

from .const import CONF_ENTITY, CONF_SOURCE, DOMAIN, SEASONAL_SOC_NAME, SOURCE_ENTITY, SOURCE_MONTHLY
from .seasonal_soc import resolve_threshold


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SeasonalSOCSensor(entry)])


class SeasonalSOCSensor(SensorEntity):
    _attr_name = SEASONAL_SOC_NAME
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:battery-lock"
    _attr_should_poll = False

    def __init__(self, entry):
        self._config = {**entry.data, **entry.options}
        self._attr_unique_id = f"{entry.entry_id}_seasonal_soc_min_threshold"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
            manufacturer="HA-BEMS", model="Battery settings",
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self._config.get(CONF_SOURCE) == SOURCE_ENTITY:
            self.async_on_remove(async_track_state_change_event(
                self.hass, [self._config[CONF_ENTITY]], self._async_refresh,
            ))
        else:
            # Local midnight also handles the first day of each calendar month.
            self.async_on_remove(async_track_time_change(
                self.hass, self._async_refresh, hour=0, minute=0, second=0,
            ))
        self._refresh()

    @callback
    def _async_refresh(self, _event):
        self._refresh()
        self.async_write_ha_state()

    def _refresh(self):
        source_entity = self._config.get(CONF_ENTITY)
        state = self.hass.states.get(source_entity) if source_entity else None
        try:
            value = resolve_threshold(
                self._config, dt_util.now().month,
                state.state if state else None,
                state.attributes.get("unit_of_measurement") if state else None,
            )
        except ValueError:
            self._attr_available = False
            self._attr_native_value = None
        else:
            self._attr_available = True
            self._attr_native_value = value
        self._attr_extra_state_attributes = {
            "source": self._config.get(CONF_SOURCE, SOURCE_MONTHLY),
            "source_entity": self._config.get(CONF_ENTITY)
                if self._config.get(CONF_SOURCE) == SOURCE_ENTITY else None,
        }
