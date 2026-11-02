"""Serve the optional, read-only Lovelace characteristic display."""

from pathlib import Path

from .const import DOMAIN


async def async_register_characteristic_frontend(hass):
    if not getattr(hass, "http", None):
        return
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("characteristic_frontend_registered"):
        return
    from homeassistant.components.http import StaticPathConfig

    await hass.http.async_register_static_paths([
        StaticPathConfig("/ha_bems/characteristic-card.js",
                         str(Path(__file__).parent / "frontend" / "characteristic-card.js"), True)])
    data["characteristic_frontend_registered"] = True
