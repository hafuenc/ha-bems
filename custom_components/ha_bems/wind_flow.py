"""Password-gated wind helper setup, shared by all creator entry layouts."""

import voluptuous as vol
from homeassistant.helpers import selector

from .const import MODULE_EEG_WIND
from .wind_access import async_unlock
from .wind_forecast import DEFAULT_WIND_POINTS, curve_text, parse_curve, validate_wind_settings


class WindSetupSteps:
    async def async_step_eeg_wind(self, user_input=None):
        errors = {}
        if user_input is not None:
            error = await async_unlock(self.hass, user_input.get("password"))
            if error:
                errors["base"] = error
            else:
                self._wind_unlocked = True
                return await self.async_step_eeg_wind_settings()
        return self.async_show_form(step_id="eeg_wind", errors=errors, data_schema=vol.Schema({
            vol.Required("password"): selector.TextSelector({"type": "password"})}))

    async def async_step_eeg_wind_settings(self, user_input=None):
        if not getattr(self, "_wind_unlocked", False):
            return await self.async_step_eeg_wind()
        errors = {}
        if user_input is not None:
            try:
                points = parse_curve(user_input["wind_curve_text"])
                values = {key: user_input[key] for key in
                          ("name", "latitude", "longitude", "hub_height_m", "share_percent") if key in user_input}
                values["wind_power_points"] = points
                validate_wind_settings(values)
            except (ValueError, TypeError, KeyError, OverflowError):
                errors["base"] = "invalid_wind_settings"
            else:
                self._remember(values)
                return self._finish(MODULE_EEG_WIND)
        values = {**self._values(), **(user_input or {})}
        fields = self._name_fields("EEG wind energy forecast")
        for key, default, minimum, maximum, unit in (
            ("latitude", self.hass.config.latitude, -90, 90, None),
            ("longitude", self.hass.config.longitude, -180, 180, None),
            ("hub_height_m", 85, 10, 180, "m"), ("share_percent", 100, 0, 100, "%")):
            config = {"min": minimum, "max": maximum, "step": "any", "mode": "box"}
            if unit:
                config["unit_of_measurement"] = unit
            fields[vol.Required(key, default=values.get(key, default))] = selector.NumberSelector(config)
        text = values.get("wind_curve_text", curve_text(values.get("wind_power_points", DEFAULT_WIND_POINTS)))
        fields[vol.Required("wind_curve_text", default=text)] = selector.TextSelector({"multiline": True})
        return self.async_show_form(step_id="eeg_wind_settings", errors=errors, data_schema=vol.Schema(fields),
            description_placeholders={"weather_source_url": "https://open-meteo.com/"})
