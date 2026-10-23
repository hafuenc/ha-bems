"""Configure a battery's shared seasonal minimum SOC threshold."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_ENTITY, CONF_MONTHLY, CONF_SOURCE, DEFAULT_MONTHLY_MIN_SOC,
    DOMAIN, MONTHS, SOURCE_ENTITY, SOURCE_MONTHLY,
)
from .seasonal_soc import resolve_threshold


def _source_schema(values, include_name=False):
    fields = {}
    if include_name:
        fields[vol.Required("name", default="Battery")] = str
    fields[vol.Required(CONF_SOURCE, default=values.get(CONF_SOURCE, SOURCE_MONTHLY))] = (
        selector.SelectSelector(selector.SelectSelectorConfig(
            options=[
                {"value": SOURCE_MONTHLY, "label": "Monthly schedule"},
                {"value": SOURCE_ENTITY, "label": "Existing entity"},
            ],
            mode=selector.SelectSelectorMode.LIST,
        ))
    )
    return vol.Schema(fields)


class _ThresholdSteps:
    """Share source selection and validation between setup and options."""

    async def async_step_monthly(self, user_input=None):
        errors = {}
        if user_input is not None:
            values = [user_input[month] for month in MONTHS]
            try:
                resolve_threshold({CONF_MONTHLY: values}, 1)
            except ValueError:
                errors["base"] = "invalid_threshold"
            else:
                return self._finish({CONF_SOURCE: SOURCE_MONTHLY, CONF_MONTHLY: values})
        values = self._values().get(CONF_MONTHLY, DEFAULT_MONTHLY_MIN_SOC)
        return self.async_show_form(
            step_id="monthly", errors=errors,
            data_schema=vol.Schema({
                vol.Required(month, default=(user_input or {}).get(month, values[index])):
                    selector.NumberSelector(selector.NumberSelectorConfig(
                        min=0, max=100, step=1, unit_of_measurement="%",
                        mode=selector.NumberSelectorMode.BOX,
                    ))
                for index, month in enumerate(MONTHS)
            }),
        )

    async def async_step_existing_entity(self, user_input=None):
        errors = {}
        if user_input is not None:
            entity_id = user_input[CONF_ENTITY]
            state = self.hass.states.get(entity_id)
            registered = er.async_get(self.hass).async_get(entity_id)
            own_entry = getattr(self, "config_entry", None)
            if own_entry and registered and registered.config_entry_id == own_entry.entry_id:
                errors[CONF_ENTITY] = "self_reference"
            else:
                try:
                    resolve_threshold(
                        {CONF_SOURCE: SOURCE_ENTITY, CONF_ENTITY: entity_id}, 1,
                        state.state if state else None,
                        state.attributes.get("unit_of_measurement") if state else None,
                    )
                except ValueError:
                    errors[CONF_ENTITY] = "invalid_entity"
                else:
                    return self._finish({
                        CONF_SOURCE: SOURCE_ENTITY, CONF_ENTITY: entity_id,
                        CONF_MONTHLY: self._values().get(CONF_MONTHLY, list(DEFAULT_MONTHLY_MIN_SOC)),
                    })
        field = vol.Required(CONF_ENTITY)
        entity_id = (user_input or self._values()).get(CONF_ENTITY)
        if entity_id:
            field = vol.Required(CONF_ENTITY, default=entity_id)
        return self.async_show_form(
            step_id="existing_entity", errors=errors,
            data_schema=vol.Schema({field: selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["sensor", "number", "input_number"])
            )}),
        )


class HABEMSConfigFlow(_ThresholdSteps, config_entries.ConfigFlow, domain=DOMAIN):
    """Create one shared configuration per battery."""

    VERSION = 1

    def _values(self):
        return {}

    def _finish(self, values):
        return self.async_create_entry(title=self._battery_name, data=values)

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._battery_name = user_input["name"].strip() or "Battery"
            if user_input[CONF_SOURCE] == SOURCE_ENTITY:
                return await self.async_step_existing_entity()
            return await self.async_step_monthly()
        return self.async_show_form(step_id="user", data_schema=_source_schema({}, True))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HABEMSOptionsFlow()


class HABEMSOptionsFlow(_ThresholdSteps, config_entries.OptionsFlow):
    """Edit the schedule or switch to an existing entity later."""

    def _values(self):
        return {**self.config_entry.data, **self.config_entry.options}

    def _finish(self, values):
        return self.async_create_entry(title="", data=values)

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            if user_input[CONF_SOURCE] == SOURCE_ENTITY:
                return await self.async_step_existing_entity()
            return await self.async_step_monthly()
        return self.async_show_form(step_id="init", data_schema=_source_schema(self._values()))
