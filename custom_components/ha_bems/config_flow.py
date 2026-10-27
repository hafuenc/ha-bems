"""Modular setup: reusable entities, electrical storage and thermal storage."""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENTITY, CONF_MODULE, CONF_MONTHLY, CONF_SOURCE, DEFAULT_MONTHLY_MIN_SOC,
    DOMAIN, MODULE_FBC, MODULE_LOAD, MODULE_SOC, MODULE_PV_IMPORT, CONF_PV_IMPORT,
    MONTHS, SOURCE_ENTITY, SOURCE_MONTHLY,
)
from .seasonal_soc import resolve_threshold
from .seasonal_soc import validate_threshold
from .forecast_input import async_read_pv_forecast
from .import_registry import pv_imports, read_pv_import


def _number(minimum=0, maximum=100, unit=None, step=1):
    config = {"min": minimum, "max": maximum, "step": step, "mode": "box"}
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


def _choice(options):
    return selector.SelectSelector({"options": options, "mode": "dropdown"})


def _entity():
    return selector.EntitySelector({"domain": ["sensor", "number", "input_number"]})


def _percentage_entity():
    return selector.EntitySelector({"filter": [{
        "domain": ["sensor", "number", "input_number"], "unit_of_measurement": "%",
    }]})


class _ModuleSteps:
    """Share configuration steps between initial setup and later options."""

    def _values(self):
        if isinstance(self, config_entries.OptionsFlow):
            return {**self.config_entry.data, **self.config_entry.options, **getattr(self, "_draft", {})}
        return getattr(self, "_draft", {})

    def _remember(self, values):
        self._draft = {**self._values(), **values}

    def _name_fields(self, default):
        if isinstance(self, config_entries.OptionsFlow):
            return {}
        return {vol.Required("name", default=self._values().get("name", default)): str}

    def _finish(self, module):
        values = {**self._values(), CONF_MODULE: module}
        # Retired setting is ignored at runtime and dropped on options save.
        values.pop("forecast_max_age_minutes", None)
        if module == MODULE_FBC:
            for key in ("forecast_source", "forecast_template", "forecast_attribute",
                        "timestamp_key", "end_key", "value_key", "forecast_unit", "forecast_interval_minutes",
                        "forecast_entity"):
                values.pop(key, None)
        if isinstance(self, config_entries.OptionsFlow):
            return self.async_create_entry(title="", data=values)
        name = values.pop("name").strip()
        return self.async_create_entry(title=name or "HA-BEMS", data=values)

    async def async_step_soc(self, user_input=None):
        if user_input is not None:
            self._remember(user_input)
            if user_input[CONF_SOURCE] == SOURCE_ENTITY:
                return await self.async_step_existing_entity()
            return await self.async_step_monthly()
        fields = self._name_fields("Battery settings")
        fields[vol.Required(CONF_SOURCE, default=self._values().get(CONF_SOURCE, SOURCE_MONTHLY))] = _choice([
            {"value": SOURCE_MONTHLY, "label": "Monthly schedule"},
            {"value": SOURCE_ENTITY, "label": "Existing entity"},
        ])
        return self.async_show_form(step_id="soc", data_schema=vol.Schema(fields))

    async def async_step_monthly(self, user_input=None):
        errors = {}
        if user_input is not None:
            values = [user_input[month] for month in MONTHS]
            try:
                resolve_threshold({CONF_MONTHLY: values}, 1)
            except ValueError:
                errors["base"] = "invalid_threshold"
            else:
                self._remember({CONF_SOURCE: SOURCE_MONTHLY, CONF_MONTHLY: values})
                return self._finish(MODULE_SOC)
        values = self._values().get(CONF_MONTHLY, DEFAULT_MONTHLY_MIN_SOC)
        return self.async_show_form(step_id="monthly", errors=errors, data_schema=vol.Schema({
            vol.Required(month, default=(user_input or {}).get(month, values[index])): _number(unit="%")
            for index, month in enumerate(MONTHS)
        }))

    async def async_step_existing_entity(self, user_input=None):
        errors = {}
        if user_input is not None:
            entity_id = user_input[CONF_ENTITY]
            state = self.hass.states.get(entity_id)
            registered = er.async_get(self.hass).async_get(entity_id)
            own_entry = self.config_entry if isinstance(self, config_entries.OptionsFlow) else None
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
                    self._remember({CONF_SOURCE: SOURCE_ENTITY, CONF_ENTITY: entity_id,
                                    CONF_MONTHLY: self._values().get(CONF_MONTHLY, list(DEFAULT_MONTHLY_MIN_SOC))})
                    return self._finish(MODULE_SOC)
        field = vol.Required(CONF_ENTITY)
        if entity_id := (user_input or self._values()).get(CONF_ENTITY):
            field = vol.Required(CONF_ENTITY, default=entity_id)
        return self.async_show_form(step_id="existing_entity", errors=errors,
                                    data_schema=vol.Schema({field: _entity()}))

    async def async_step_load_profile(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                import holidays
                if user_input.get("holiday_country"):
                    await self.hass.async_add_executor_job(
                        lambda: holidays.country_holidays(user_input["holiday_country"],
                            subdiv=user_input.get("holiday_subdivision") or None, years=dt_util.now().year)
                    )
            except (ValueError, KeyError, NotImplementedError):
                errors["base"] = "invalid_load_profile"
            else:
                self._remember(user_input)
                return self._finish(MODULE_LOAD)
        values = {**self._values(), **(user_input or {})}
        fields = self._name_fields("Non-flexible building load")
        annual = vol.Required("annual_consumption_kwh", default=values["annual_consumption_kwh"]) if "annual_consumption_kwh" in values else vol.Required("annual_consumption_kwh")
        fields[annual] = _number(0, 10000000, "kWh/year", "any")
        fields[vol.Required("load_profile", default=values.get("load_profile", "H0"))] = _choice([
            {"value": "H0", "label": "H0"},
            {"value": "H25", "label": "H25"},
            {"value": "constant", "label": "Constant throughout the year"},
        ])
        fields[vol.Required("holiday_country", default=values.get("holiday_country", "AT"))] = _choice([
            {"value": "AT", "label": "Austria"}, {"value": "DE", "label": "Germany"},
            {"value": "", "label": "Weekends only (no public holidays)"},
        ])
        fields[vol.Optional("holiday_subdivision", default=values.get("holiday_subdivision", ""))] = str
        return self.async_show_form(step_id="load_profile", errors=errors, data_schema=vol.Schema(fields))

    async def async_step_fbc(self, user_input=None):
        errors = {}
        if user_input is not None:
            for key in ("soc_entity", CONF_ENTITY):
                state = self.hass.states.get(user_input[key])
                try:
                    if state is None or state.attributes.get("unit_of_measurement") != "%":
                        raise ValueError
                    validate_threshold(state.state)
                except ValueError:
                    errors[key] = "invalid_entity"
            if not errors:
                self._remember(user_input)
                return await self.async_step_fbc_forecast()
        values = {**self._values(), **(user_input or {})}
        fields = self._name_fields("FBC Battery 1")
        fields[vol.Required("battery_strategy", default="fbc_legacy_dc")] = _choice([
            {"value": "fbc_legacy_dc", "label": "FBC v1.0 - only PV generation considered"},
        ])
        for key in ("soc_entity", CONF_ENTITY):
            field = vol.Required(key, default=values[key]) if key in values else vol.Required(key)
            fields[field] = _percentage_entity()
        return self.async_show_form(step_id="fbc", errors=errors, data_schema=vol.Schema(fields))

    async def async_step_import_pv(self, user_input=None):
        errors, placeholders = {}, {
            "reason": "",
            "helper_docs_url": "https://github.com/hafuenc/ha-bems/blob/main/docs/pv_forecast_helper.md",
        }
        if user_input is not None:
            self._remember(user_input)
            try:
                intervals, details = await async_read_pv_forecast(self.hass, self._values())
            except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
                errors["base"] = "invalid_forecast"
                placeholders["reason"] = str(err)
            else:
                self._forecast_preview = {
                    "count": str(len(intervals)),
                    "start": intervals[0]["start"], "end": intervals[-1]["end"],
                    "peak": str(round(max(row["power_w"] for row in intervals), 1)),
                    "energy": str(round(sum(row["power_w"] * 0.25 / 1000 for row in intervals), 3)),
                    "age": str(details["source_age_minutes"]),
                }
                return await self.async_step_import_preview()
        values = self._values()
        field = vol.Required("forecast_entity", default=values["forecast_entity"]) if "forecast_entity" in values else vol.Required("forecast_entity")
        fields = self._name_fields("PV generation forecast")
        fields.update({
                field: selector.EntitySelector({"filter": [{"domain": "sensor", "unit_of_measurement": "W"}]}),
            })
        return self.async_show_form(step_id="import_pv", errors=errors,
            description_placeholders=placeholders, data_schema=vol.Schema(fields))

    async def async_step_import_preview(self, user_input=None):
        if user_input is not None:
            # Test again before saving: the source may have changed while
            # the preview was open. A failure returns to the import form.
            try:
                await async_read_pv_forecast(self.hass, self._values())
            except (ValueError, TypeError, KeyError, IndexError, OverflowError):
                return await self.async_step_import_pv(self._values())
            return self._finish(MODULE_PV_IMPORT)
        return self.async_show_form(step_id="import_preview", data_schema=vol.Schema({}),
                                    description_placeholders=self._forecast_preview)

    async def async_step_fbc_forecast(self, user_input=None):
        errors, placeholders = {}, {"reason": ""}
        entries = pv_imports(self.hass)
        if not entries:
            errors["base"] = "no_pv_import"
            return self.async_show_form(step_id="fbc_forecast", errors=errors,
                description_placeholders=placeholders, data_schema=vol.Schema({}))
        if user_input is not None:
            self._remember(user_input)
            try:
                intervals, details = read_pv_import(self.hass, self._values(), dt_util.now())
            except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
                errors["base"] = "invalid_forecast"
                placeholders["reason"] = str(err)
            else:
                self._forecast_preview = {
                    "count": str(len(intervals)),
                    "start": intervals[0]["start"], "end": intervals[-1]["end"],
                    "peak": str(round(max(row["power_w"] for row in intervals), 1)),
                    "energy": str(round(sum(row["power_w"] * 0.25 / 1000 for row in intervals), 3)),
                    "age": str(details["source_age_minutes"]),
                }
                return await self.async_step_fbc_preview()
        values = self._values()
        valid_ids = {entry.entry_id for entry in entries}
        field = vol.Required(CONF_PV_IMPORT, default=values[CONF_PV_IMPORT]) if values.get(CONF_PV_IMPORT) in valid_ids else vol.Required(CONF_PV_IMPORT)
        return self.async_show_form(step_id="fbc_forecast", errors=errors,
            description_placeholders=placeholders, data_schema=vol.Schema({
                field: _choice([{"value": entry.entry_id, "label": entry.title} for entry in entries]),
            }))

    async def async_step_fbc_preview(self, user_input=None):
        if user_input is not None:
            return await self.async_step_fbc_battery()
        return self.async_show_form(step_id="fbc_preview", data_schema=vol.Schema({}),
                                    description_placeholders=self._forecast_preview)

    async def async_step_fbc_battery(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input["output_limit_w"] < user_input["max_charge_power_w"]:
                errors["output_limit_w"] = "output_limit_below_maximum"
            else:
                self._remember(user_input)
                try:
                    read_pv_import(self.hass, self._values(), dt_util.now())
                except (ValueError, TypeError, KeyError, IndexError, OverflowError):
                    return await self.async_step_fbc_forecast({CONF_PV_IMPORT: self._values().get(CONF_PV_IMPORT)})
                return self._finish(MODULE_FBC)
        values = {**self._values(), **(user_input or {})}
        capacity = vol.Required("capacity_kwh", default=values["capacity_kwh"]) if "capacity_kwh" in values else vol.Required("capacity_kwh")
        fields = {capacity: _number(0.01, 10000, "kWh", "any")}
        for key, default, minimum, maximum, unit, step in (
            ("target_soc_percent", 100, 0, 100, "%", 1),
            ("charge_efficiency", 0.95, 0.01, 1, None, "any"),
            ("max_charge_power_w", 5000, 0, 1000000, "W", "any"),
            ("output_limit_w", 5000, 0, 1000000, "W", "any"),
        ):
            fields[vol.Required(key, default=values.get(key, default))] = _number(minimum, maximum, unit, step)
        return self.async_show_form(step_id="fbc_battery", errors=errors, data_schema=vol.Schema(fields))


class HABEMSConfigFlow(_ModuleSteps, config_entries.ConfigFlow, domain=DOMAIN):
    """Each helper or device module gets an independent configuration entry."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["entity_creator", "entity_importer", "electrical_storage", "thermal_storage"])

    async def async_step_entity_creator(self, user_input=None):
        return self.async_show_menu(step_id="entity_creator", menu_options=["soc", "load_profile"])

    async def async_step_entity_importer(self, user_input=None):
        return self.async_show_menu(step_id="entity_importer", menu_options=["import_pv", "future_imports"])

    async def async_step_future_imports(self, user_input=None):
        if user_input is not None:
            return await self.async_step_entity_importer()
        return self.async_show_form(step_id="future_imports", data_schema=vol.Schema({}))

    async def async_step_electrical_storage(self, user_input=None):
        return self.async_show_menu(step_id="electrical_storage", menu_options=["fbc"])

    async def async_step_thermal_storage(self, user_input=None):
        if user_input is not None:
            return await self.async_step_user()
        return self.async_show_form(step_id="thermal_storage", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HABEMSOptionsFlow()


class HABEMSOptionsFlow(_ModuleSteps, config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        module = self._values().get(CONF_MODULE, MODULE_SOC)
        if module == MODULE_LOAD:
            return await self.async_step_load_profile()
        if module == MODULE_FBC:
            return await self.async_step_fbc()
        if module == MODULE_PV_IMPORT:
            return await self.async_step_import_pv()
        return await self.async_step_soc()
