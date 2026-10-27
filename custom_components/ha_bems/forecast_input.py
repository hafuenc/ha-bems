"""Read HA forecast entities or render the user's sandboxed Jinja template."""

import json
from zoneinfo import ZoneInfo

from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .forecast import prepare_forecast
from .forecast_sources import (
    CUSTOM_TEMPLATE, GENERIC_RECORDS, OPEN_METEO, canonical_mapping, open_meteo_records,
)


async def async_read_pv_forecast(hass, config):
    now = dt_util.now()
    state = hass.states.get(config["forecast_entity"])
    if state is None or state.state in ("unknown", "unavailable"):
        raise ValueError("PV forecast source is unavailable")
    age = max(0.0, (now - state.last_updated).total_seconds() / 60)
    if age > config.get("forecast_max_age_minutes", 180):
        raise ValueError("PV forecast source is stale; check its update time")
    source = config.get("forecast_source", GENERIC_RECORDS)
    if source == OPEN_METEO:
        records, description = await hass.async_add_executor_job(open_meteo_records, state.attributes)
        mapping = canonical_mapping()
    elif source == CUSTOM_TEMPLATE:
        try:
            template = Template(config["forecast_template"], hass)
            template.ensure_valid()
            records = template.async_render({"source_entity": state.entity_id}, parse_result=True)
            if isinstance(records, str):
                records = json.loads(records)
        except (TemplateError, ValueError, TypeError) as err:
            raise ValueError(f"Forecast template could not be rendered: {err}") from err
        mapping = canonical_mapping()
        description = "Custom forecast template (interval-average W)"
    elif source == GENERIC_RECORDS:
        records = state.attributes.get(config.get("forecast_attribute", "forecast"))
        mapping = config
        description = "Existing interval records"
    else:
        raise ValueError("Unknown PV forecast source adapter")
    if not isinstance(records, list) or not 0 < len(records) <= 10000:
        raise ValueError("The forecast must return a nonempty list of at most 10000 intervals")
    intervals = await hass.async_add_executor_job(
        prepare_forecast, records, mapping, now, ZoneInfo(hass.config.time_zone),
    )
    return intervals, {
        "forecast_source": source, "forecast_adapter": description,
        "source_entity": state.entity_id, "source_age_minutes": round(age, 1),
        "source_last_updated": state.last_updated.isoformat(),
    }
