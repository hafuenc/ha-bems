"""Register the importer overview using HA's supported custom-panel API."""

from pathlib import Path

import voluptuous as vol

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import callback

from .const import DOMAIN
from .navigation import OVERVIEW_PATH, forecast_source_rows

ASSET_URL = "/ha_bems/forecast-overview.js"


@websocket_api.websocket_command({vol.Required("type"): "ha_bems/forecast_sources"})
@websocket_api.require_admin
@callback
def websocket_forecast_sources(hass, connection, message):
    """Match the admin access used by the integration/device configuration UI."""
    connection.send_result(message["id"], forecast_source_rows(hass))


async def async_setup_frontend(hass):
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("forecast_overview_registered"):
        return
    await hass.http.async_register_static_paths([
        StaticPathConfig(ASSET_URL, str(Path(__file__).parent / "frontend" / "forecast-overview.js"), True),
    ])
    websocket_api.async_register_command(hass, websocket_forecast_sources)
    await panel_custom.async_register_panel(hass, frontend_url_path=OVERVIEW_PATH,
        webcomponent_name="ha-bems-forecast-overview", module_url=f"{ASSET_URL}?v=0.8.0",
        require_admin=True, sidebar_title=None, config_panel_domain=DOMAIN)
    data["forecast_overview_registered"] = True
