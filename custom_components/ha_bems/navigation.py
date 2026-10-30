"""Read-only navigation metadata; source forecasts stay with their providers."""

from urllib.parse import urlencode, urlsplit

from .const import DOMAIN, FORECAST_IMPORT_MODULES, GROUP_IMPORTER
from .entries import all_module_entries, item_enabled, module_type

OVERVIEW_PATH = "ha-bems-forecasts"
OVERVIEW_URL = f"homeassistant://{OVERVIEW_PATH}"
INTEGRATION_PATH = "/config/integrations/integration/ha_bems"


def source_entity_path(entity_id):
    """A same-origin deep link which also works when opened in a new tab."""
    return f"/{OVERVIEW_PATH}?{urlencode({'more-info-entity-id': entity_id})}"


def absolute_source_url(base_url, entity_id):
    """Only HTTP(S) attributes are clickable in HA's standard entity details."""
    if not base_url or urlsplit(base_url).scheme not in ("http", "https"):
        return None
    return base_url.rstrip("/") + source_entity_path(entity_id)


def source_entity_url(hass, entity_id):
    from homeassistant.helpers.network import NoURLAvailableError, get_url

    try:
        # Prefer a configured external/cloud URL so the attribute also works
        # away from the home network. The overview itself uses same-origin links.
        base = get_url(hass, prefer_external=True)
    except NoURLAvailableError:
        return None
    return absolute_source_url(base, entity_id)


def forecast_source_rows(hass):
    """Return lightweight identifiers, never forecast lists or source values."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    from .devices import service_identifier

    registry = er.async_get(hass)
    inputs = []
    importer_device_id = None
    devices = dr.async_get(hass)
    for item in all_module_entries(hass):
        if module_type(item) not in FORECAST_IMPORT_MODULES:
            continue
        config = {**item.data, **item.options}
        inputs.append({"item_id": item.entry_id, "name": item.title,
                       "input_type": module_type(item), "pv_power_type": config.get("pv_power_type"),
                       "enabled": item_enabled(item), "source_entity": config["forecast_entity"],
                       "status_entity": registry.async_get_entity_id("sensor", DOMAIN, f"{item.entry_id}_input_status")})
        parent_id = getattr(item, "parent", item).entry_id
        if importer_device_id is None:
            importer_device_id = next((device.id for device in dr.async_entries_for_config_entry(devices, parent_id)
                if service_identifier(GROUP_IMPORTER) in device.identifiers), None)
    inputs.sort(key=lambda item: (item["name"].casefold(), item["item_id"]))
    return {"inputs": inputs, "importer_device_id": importer_device_id}
