"""Modular setup: reusable entities, electrical storage and thermal storage."""

from zoneinfo import ZoneInfo
from uuid import uuid4
from types import SimpleNamespace

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENTITY, CONF_LOAD_INPUT, CONF_MODULE, CONF_MONTHLY, CONF_SOURCE, DEFAULT_MONTHLY_MIN_SOC,
    DOMAIN, MODULE_FBC, MODULE_LOAD, MODULE_LOAD_IMPORT, MODULE_SOC, MODULE_PV_IMPORT, CONF_PV_IMPORT,
    HOLIDAY_REGIONS, MONTHS, SOURCE_ENTITY, SOURCE_MONTHLY,
    CONF_PV_POWER_TYPE, MODULE_IMPORT_PRICE, MODULE_EXPORT_PRICE, MODULE_OUTDOOR_TEMP,
    CONF_GROUP, CONF_ITEM_ID, CONF_ENABLED, GROUP_TITLES, GROUP_MODULES,
    MODULE_THERMAL_COST, MODULE_THERMAL_PLAN, MODULE_OPERATION_IMPORT, CONF_IMPORT_PRICE, CONF_EXPORT_PRICE, CONF_TEMPERATURE,
    CONF_ITEMS, CONF_LAYOUT, LAYOUT_FLAT,
)
from .entries import group_for_module, is_flat
from .seasonal_soc import resolve_threshold
from .seasonal_soc import validate_threshold
from .forecast_input import async_read_forecast
from .forecast_types import forecast_type, pv_input_label
from .forecast import daily_forecast, surplus_forecast
from .import_registry import input_entries, load_inputs, pv_imports, read_load_input, read_pv_import
from .thermal_cost import validate_settings
from .cop_table import default_table_text, parse_table_text
from .thermal_plan_flow import ThermalPlanSteps


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


class _ModuleSteps(ThermalPlanSteps):
    """Share configuration steps between initial setup and later options."""

    def _values(self):
        if subentry := getattr(self, "_subentry", None):
            return {**subentry.data, "name": subentry.title, **getattr(self, "_draft", {})}
        if getattr(self, "_flat_mode", None):
            return getattr(self, "_draft", {})
        if isinstance(self, config_entries.OptionsFlow):
            return {**self.config_entry.data, **self.config_entry.options, **getattr(self, "_draft", {})}
        return getattr(self, "_draft", {})

    def _remember(self, values):
        self._draft = {**self._values(), **values}

    def _name_fields(self, default):
        if isinstance(self, config_entries.OptionsFlow) and getattr(self, "_flat_mode", None) != "add":
            return {}
        return {vol.Required("name", default=self._values().get("name", default)): str}

    def _finish(self, module):
        values = {**self._values(), CONF_MODULE: module}
        # Retired setting is ignored at runtime and dropped on options save.
        values.pop("forecast_max_age_minutes", None)
        if module == MODULE_FBC:
            values.pop("battery_strategy", None)
            for key in ("forecast_source", "forecast_template", "forecast_attribute",
                        "timestamp_key", "end_key", "value_key", "forecast_unit", "forecast_interval_minutes",
                        "forecast_entity"):
                values.pop(key, None)
        if module == MODULE_THERMAL_COST:
            values.pop("auxiliary_power_w", None)
            values.pop("cop_table_text", None)
            for index in range(1, 11):
                values.pop(f"temperature_{index}", None)
                values.pop(f"cop_{index}", None)
            retired = (CONF_TEMPERATURE, "cop_points", "cop_point_count", "fallback_cop") if values["cop_model"] == "constant" else ("constant_cop",)
            for key in retired:
                values.pop(key, None)
        if module == MODULE_THERMAL_PLAN:
            values.pop("runtime_table_text", None)
            if values.pop("restart_storage_model", False):
                values["storage_model_revision"] = uuid4().hex
            if values["runtime_model"] == "constant":
                for key in ("runtime_points", "runtime_point_count", "runtime_temperature_entry_id",
                            "runtime_min_h", "runtime_max_h", "heating_cutoff_enabled", "heating_cutoff_c"):
                    values.pop(key, None)
            else:
                values.pop("runtime_h_per_24h", None)
        if getattr(self, "_flat_mode", None):
            name = values.pop("name", "HA-BEMS").strip() or "HA-BEMS"
            identifier = getattr(self, "_flat_item_id", None) or uuid4().hex
            values.update({CONF_ITEM_ID: identifier, CONF_ENABLED: values.get(CONF_ENABLED, True)})
            items = dict(self.config_entry.data.get(CONF_ITEMS, {}))
            items[identifier] = {"title": name, "data": values}
            self.hass.config_entries.async_update_entry(self.config_entry,
                data={CONF_LAYOUT: LAYOUT_FLAT, CONF_ITEMS: items}, options={})
            return self.async_create_entry(title="", data={})
        if isinstance(self, config_entries.ConfigSubentryFlow):
            parent = self._get_entry()
            if group_for_module(module) != parent.data[CONF_GROUP]:
                return self.async_abort(reason="wrong_group")
            name = values.pop("name", "HA-BEMS").strip() or "HA-BEMS"
            if subentry := getattr(self, "_subentry", None):
                if module != subentry.data[CONF_MODULE]:
                    return self.async_abort(reason="wrong_group")
                # The parent's update listener reloads on add/edit/remove.
                return self.async_update_and_abort(parent, subentry, title=name, data=values)
            values.setdefault(CONF_ITEM_ID, uuid4().hex)
            values.setdefault(CONF_ENABLED, True)
            return self.async_create_entry(title=name, data=values, unique_id=values[CONF_ITEM_ID])
        if isinstance(self, config_entries.OptionsFlow):
            return self.async_create_entry(title="", data=values)
        name = values.pop("name").strip()
        return self.async_create_entry(title=name or "HA-BEMS", data=values)

    async def async_step_soc(self, user_input=None):
        if (isinstance(self, config_entries.OptionsFlow) and getattr(self, "_flat_mode", None) != "add") or getattr(self, "_subentry", None):
            # Existing wrappers remain editable, but are no longer offered
            # when creating a new seasonal SOC helper.
            if self._values().get(CONF_SOURCE) == SOURCE_ENTITY:
                return await self.async_step_existing_entity()
            return await self.async_step_monthly()
        if user_input is not None:
            self._remember({**user_input, CONF_SOURCE: SOURCE_MONTHLY})
            return await self.async_step_monthly()
        fields = self._name_fields("Battery settings")
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
            own_subentry = getattr(self, "_subentry", None)
            flat_self = getattr(self, "_flat_item_id", None)
            if registered and ((flat_self and registered.unique_id.startswith(f"{flat_self}_"))
                               or (own_entry and not is_flat(own_entry) and registered.config_entry_id == own_entry.entry_id)
                               or (own_subentry and not getattr(self, "_flat_mode", None)
                                   and registered.config_subentry_id == own_subentry.subentry_id)):
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
            country = user_input.get("holiday_country", "")
            if country not in ("", *HOLIDAY_REGIONS):
                errors["base"] = "invalid_load_profile"
            else:
                if country != self._values().get("holiday_country", "AT") or not country:
                    # Never carry an Austrian region into the German calendar or vice versa.
                    self._remember({"holiday_subdivision": ""})
                self._remember(user_input)
                if not country:
                    return self._finish(MODULE_LOAD)
                return await self.async_step_load_region()
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
        return self.async_show_form(step_id="load_profile", errors=errors, data_schema=vol.Schema(fields),
            description_placeholders={
                "h0_source_url": "https://www.bdew.de/media/documents/Profile.zip",
                "h25_source_url": "https://www.bdew.de/media/documents/Kopie_von_Repr%C3%A4sentative_Profile_BDEW_H25_G25_L25_P25_S25_Ver%C3%B6ffentlichung.xlsx",
                "profile_docs_url": "https://www.bdew.de/energie/standardlastprofile-strom/",
            })

    async def async_step_load_region(self, user_input=None):
        """Offer only the selected country's Bundesländer, in setup and options."""
        import holidays

        country = self._values()["holiday_country"]
        regions = dict(HOLIDAY_REGIONS[country])
        calendar = await self.hass.async_add_executor_job(
            lambda: holidays.country_holidays(country)
        )
        saved = self._values().get("holiday_subdivision") or ""
        saved = calendar.subdivisions_aliases.get(saved, saved)
        # Retain a supported legacy city calendar (e.g. Augsburg), if already selected.
        if saved in calendar.subdivisions and saved not in regions:
            regions[saved] = saved
        errors = {}
        if user_input is not None:
            region = user_input.get("holiday_subdivision", "")
            try:
                if region and region not in regions:
                    raise ValueError("Region does not belong to the selected country")
                await self.hass.async_add_executor_job(
                    lambda: holidays.country_holidays(country, subdiv=region or None,
                                                     years=dt_util.now().year)
                )
            except (ValueError, KeyError, NotImplementedError):
                errors["holiday_subdivision"] = "invalid_load_profile"
            else:
                self._remember({"holiday_subdivision": region})
                return self._finish(MODULE_LOAD)
        default = saved if saved in regions else ""
        return self.async_show_form(step_id="load_region", errors=errors, data_schema=vol.Schema({
            vol.Required("holiday_subdivision", default=default): _choice([
                {"value": "", "label": "National holidays only"},
                *({"value": code, "label": name} for code, name in regions.items()),
            ]),
        }))

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
        for key in ("soc_entity", CONF_ENTITY):
            field = vol.Required(key, default=values[key]) if key in values else vol.Required(key)
            fields[field] = _percentage_entity()
        return self.async_show_form(step_id="fbc", errors=errors, data_schema=vol.Schema(fields))

    async def async_step_import_pv(self, user_input=None):
        self._import_type = MODULE_PV_IMPORT
        return await self._async_step_import(user_input)

    async def async_step_import_load(self, user_input=None):
        self._import_type = MODULE_LOAD_IMPORT
        return await self._async_step_import(user_input)

    async def async_step_import_pv_ac(self, user_input=None):
        self._remember({CONF_PV_POWER_TYPE: "ac"})
        return await self.async_step_import_pv(user_input)

    async def async_step_import_pv_dc(self, user_input=None):
        self._remember({CONF_PV_POWER_TYPE: "dc"})
        return await self.async_step_import_pv(user_input)

    async def async_step_import_price(self, user_input=None):
        self._import_type = MODULE_IMPORT_PRICE
        return await self._async_step_import(user_input)

    async def async_step_export_price(self, user_input=None):
        self._import_type = MODULE_EXPORT_PRICE
        return await self._async_step_import(user_input)

    async def async_step_import_temperature(self, user_input=None):
        self._import_type = MODULE_OUTDOOR_TEMP
        return await self._async_step_import(user_input)

    async def async_step_import_operation(self, user_input=None):
        self._import_type = MODULE_OPERATION_IMPORT
        return await self._async_step_import(user_input)

    def _import_step(self):
        return {
            MODULE_PV_IMPORT: "import_pv", MODULE_LOAD_IMPORT: "import_load",
            MODULE_IMPORT_PRICE: "import_price", MODULE_EXPORT_PRICE: "export_price",
            MODULE_OUTDOOR_TEMP: "import_temperature",
            MODULE_OPERATION_IMPORT: "import_operation",
        }[self._import_type]

    async def _async_read_import(self):
        return await async_read_forecast(self.hass, {**self._values(), CONF_MODULE: self._import_type})

    async def _async_step_import(self, user_input=None):
        is_load = self._import_type == MODULE_LOAD_IMPORT
        is_pv = self._import_type == MODULE_PV_IMPORT
        is_operation = self._import_type == MODULE_OPERATION_IMPORT
        label, unit, value_key, _nonnegative = forecast_type({**self._values(), CONF_MODULE: self._import_type})
        errors, placeholders = {}, {
            "reason": "",
            "helper_docs_url": "https://github.com/hafuenc/ha-bems/blob/main/docs/"
                               + ("load_forecast_helper.md" if is_load else "pv_forecast_helper.md" if is_pv else "price_temperature_forecasts.md"),
        }
        if user_input is not None:
            self._remember(user_input)
            try:
                if is_pv and self._values().get(CONF_PV_POWER_TYPE) not in ("ac", "dc"):
                    raise ValueError("Choose AC or DC PV power")
                intervals, details = await self._async_read_import()
            except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
                errors["base"] = "invalid_forecast"
                placeholders["reason"] = str(err)
            else:
                self._import_preview_step = "import_preview" if value_key == "power_w" else "import_value_preview"
                self._forecast_preview = {
                    "count": str(len(intervals)),
                    "start": intervals[0]["start"], "end": intervals[-1]["end"],
                    "age": str(details["source_age_minutes"]),
                }
                if value_key == "power_w":
                    self._forecast_preview.update({
                        "peak": str(round(max(row[value_key] for row in intervals), 1)),
                        "energy": str(round(sum(row[value_key] * 0.25 / 1000 for row in intervals), 3)),
                    })
                else:
                    self._forecast_preview.update({"unit": unit,
                        "minimum": str(round(min(row[value_key] for row in intervals), 4)),
                        "maximum": str(round(max(row[value_key] for row in intervals), 4))})
                return await self.async_step_import_preview()
        values = self._values()
        field = vol.Required("forecast_entity", default=values["forecast_entity"]) if "forecast_entity" in values else vol.Required("forecast_entity")
        fields = self._name_fields(f"{label} forecast")
        if is_pv:
            basis = values.get(CONF_PV_POWER_TYPE)
            basis_field = vol.Required(CONF_PV_POWER_TYPE, default=basis) if basis in ("ac", "dc") else vol.Required(CONF_PV_POWER_TYPE)
            fields[basis_field] = _choice([{"value": "ac", "label": "AC"}, {"value": "dc", "label": "DC"}])
        fields.update({
                field: selector.EntitySelector({"domain": ["sensor", "binary_sensor"]}) if is_operation else
                    selector.EntitySelector({"filter": [{"domain": "sensor", "unit_of_measurement": unit}]}),
            })
        if is_operation:
            fields[vol.Required("operation_meaning", default=values.get("operation_meaning", "running"))] = _choice([
                {"value": "running", "label": "1 = another system running — invert to block this planner"},
                {"value": "allowed", "label": "1 = operation allowed — use directly"}])
        return self.async_show_form(step_id=self._import_step(), errors=errors,
            description_placeholders=placeholders, data_schema=vol.Schema(fields))

    async def async_step_import_preview(self, user_input=None):
        if user_input is not None:
            # Test again before saving: the source may have changed while
            # the preview was open. A failure returns to the import form.
            try:
                await self._async_read_import()
            except (ValueError, TypeError, KeyError, IndexError, OverflowError):
                return await self._async_step_import(self._values())
            return self._finish(self._import_type)
        return self.async_show_form(step_id=self._import_preview_step, data_schema=vol.Schema({}),
                                    description_placeholders=self._forecast_preview)

    async def async_step_import_value_preview(self, user_input=None):
        return await self.async_step_import_preview(user_input)

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
                intervals, details, load_info = await self._async_validate_fbc_inputs()
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
                    "load": load_info.get("load_input_name") or "None — PV generation only",
                    "pv_type": details[CONF_PV_POWER_TYPE].upper(),
                }
                return await self.async_step_fbc_preview()
        values = self._values()
        valid_ids = {entry.entry_id for entry in entries}
        field = vol.Required(CONF_PV_IMPORT, default=values[CONF_PV_IMPORT]) if values.get(CONF_PV_IMPORT) in valid_ids else vol.Required(CONF_PV_IMPORT)
        loads = load_inputs(self.hass)
        selected_load = values.get(CONF_LOAD_INPUT) or ""
        if selected_load and selected_load not in {reference for reference, _name in loads}:
            loads.append((selected_load, "Missing load input — select another input or PV only"))
        return self.async_show_form(step_id="fbc_forecast", errors=errors,
            description_placeholders=placeholders, data_schema=vol.Schema({
                field: _choice([{"value": entry.entry_id, "label": pv_input_label(entry)} for entry in entries]),
                vol.Optional(CONF_LOAD_INPUT, default=selected_load): _choice([
                    {"value": "", "label": "None — use PV generation only"},
                    *({"value": reference, "label": name} for reference, name in loads),
                ]),
            }))

    async def _async_validate_fbc_inputs(self):
        now = dt_util.now()
        rows, details = read_pv_import(self.hass, self._values(), now)
        load_rows, load_info = read_load_input(self.hass, self._values(), now)
        if load_rows is not None:
            day = daily_forecast(rows, now, ZoneInfo(self.hass.config.time_zone))
            await self.hass.async_add_executor_job(surplus_forecast, day, load_rows)
        return rows, details, load_info

    async def async_step_fbc_preview(self, user_input=None):
        if user_input is not None:
            return await self.async_step_fbc_battery()
        return self.async_show_form(step_id="fbc_preview", data_schema=vol.Schema({}),
                                    description_placeholders=self._forecast_preview)

    async def async_step_fbc_battery(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._remember(user_input)
            try:
                await self._async_validate_fbc_inputs()
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
        ):
            fields[vol.Required(key, default=values.get(key, default))] = _number(minimum, maximum, unit, step)
        return self.async_show_form(step_id="fbc_battery", errors=errors, data_schema=vol.Schema(fields))

    async def async_step_thermal_cost(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._remember(user_input)
            from .thermal_cost import number
            try:
                number(user_input["thermal_power_kw"], "Thermal power", positive=True)
                if user_input["cop_model"] not in ("constant", "lookup"):
                    raise ValueError("Choose a COP model")
            except (ValueError, KeyError):
                errors["base"] = "invalid_thermal_settings"
            else:
                return await (self.async_step_thermal_cop_table() if user_input["cop_model"] == "lookup"
                              else self.async_step_thermal_cop_constant())
        values = self._values()
        fields = self._name_fields("Thermal system")
        fields.update({
            vol.Required("thermal_power_kw", default=values.get("thermal_power_kw", 7)): _number(0.001, 10000, "kW", "any"),
            vol.Required("cop_model", default=values.get("cop_model", "constant")): _choice([
                {"value": "constant", "label": "Constant COP"},
                {"value": "lookup", "label": "Outdoor temperature dependent COP"}]),
        })
        return self.async_show_form(step_id="thermal_cost", errors=errors, data_schema=vol.Schema(fields))

    async def async_step_thermal_cop_constant(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._remember(user_input)
            try:
                validate_settings(self._values())
            except ValueError:
                errors["base"] = "invalid_thermal_settings"
            else:
                return await self.async_step_thermal_cost_inputs()
        return self.async_show_form(step_id="thermal_cop_constant", errors=errors, data_schema=vol.Schema({
            vol.Required("constant_cop", default=self._values().get("constant_cop", 3)): _number(0.01, 100, step="any"),
        }))

    async def async_step_thermal_cop_table(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._remember(user_input)
            count = user_input.get("cop_point_count")
            from .thermal_cost import number
            try:
                if isinstance(count, bool) or not isinstance(count, (int, float)) or not 2 <= count <= 10 or int(count) != count:
                    raise ValueError("Define 2–10 points")
                number(user_input.get("fallback_cop"), "Fallback COP", positive=True)
                if user_input.get(CONF_TEMPERATURE) not in {entry.entry_id for entry in input_entries(self.hass, MODULE_OUTDOOR_TEMP)}:
                    raise ValueError("Select an outdoor temperature input")
            except ValueError:
                errors["base"] = "invalid_thermal_settings"
            else:
                self._draft.pop("cop_table_text", None)
                return await self.async_step_thermal_cop_points()
        values = self._values()
        choices = self._thermal_choices(MODULE_OUTDOOR_TEMP, CONF_TEMPERATURE)
        return self.async_show_form(step_id="thermal_cop_table", errors=errors, data_schema=vol.Schema({
            self._thermal_field(CONF_TEMPERATURE): _choice(choices),
            vol.Required("cop_point_count", default=values.get("cop_point_count", len(values.get("cop_points", [])) or 4)): _number(2, 10),
            vol.Required("fallback_cop", default=values.get("fallback_cop", 3)): _number(0.01, 100, step="any"),
        }))

    async def async_step_thermal_cop_points(self, user_input=None):
        errors = {}
        count = int(self._values()["cop_point_count"])
        if user_input is not None:
            self._remember(user_input)
            try:
                points = parse_table_text(user_input["cop_table_text"], count)
            except (ValueError, KeyError):
                errors["base"] = "invalid_cop_points"
            else:
                self._remember({"cop_points": points})
                return await self.async_step_thermal_cost_inputs()
        values = self._values()
        text = values.get("cop_table_text")
        if text is None:
            text = default_table_text(count, values.get("cop_points"))
        return self.async_show_form(step_id="thermal_cop_points", errors=errors, data_schema=vol.Schema({
            vol.Required("cop_table_text", default=text): selector.TextSelector({"multiline": True}),
        }))

    def _thermal_field(self, key):
        value = self._values().get(key)
        return vol.Required(key, default=value) if value else vol.Required(key)

    def _thermal_choices(self, module, key):
        entries = input_entries(self.hass, module)
        if module == MODULE_PV_IMPORT:
            entries = [entry for entry in entries
                       if entry.options.get(CONF_PV_POWER_TYPE, entry.data.get(CONF_PV_POWER_TYPE)) == "ac"]
        choices = [{"value": entry.entry_id, "label": entry.title} for entry in entries]
        saved = self._values().get(key)
        if saved and saved not in {choice["value"] for choice in choices}:
            choices.append({"value": saved, "label": "Missing input — choose another registration"})
        return choices

    async def async_step_thermal_cost_inputs(self, user_input=None):
        errors, placeholders = {}, {"reason": ""}
        if user_input is not None:
            self._remember(user_input)
            try:
                result = await self._async_thermal_preview()
            except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
                errors["base"] = "invalid_thermal_inputs"
                placeholders["reason"] = str(err)
            else:
                self._thermal_result = result
                return await self.async_step_thermal_cost_preview()
        fields = {}
        for key, module in ((CONF_PV_IMPORT, MODULE_PV_IMPORT), (CONF_IMPORT_PRICE, MODULE_IMPORT_PRICE),
                            (CONF_EXPORT_PRICE, MODULE_EXPORT_PRICE)):
            fields[self._thermal_field(key)] = _choice(self._thermal_choices(module, key))
        values = self._values()
        loads = load_inputs(self.hass)
        if values.get(CONF_LOAD_INPUT) and values[CONF_LOAD_INPUT] not in {ref for ref, _ in loads}:
            loads.append((values[CONF_LOAD_INPUT], "Missing load input — choose another or explicitly choose no load"))
        fields.update({
            vol.Optional(CONF_LOAD_INPUT, default=values.get(CONF_LOAD_INPUT, "")): _choice([
                {"value": "", "label": "None — all PV is available to this setup"},
                *({"value": ref, "label": name} for ref, name in loads)]),
            vol.Required("feed_in_limit_enabled", default=values.get("feed_in_limit_enabled", False)): bool,
            vol.Required("feed_in_limit_w", default=values.get("feed_in_limit_w", 0)): _number(0, 10000000, "W", "any"),
            vol.Required("curtailed_price_eur_per_kwh", default=values.get("curtailed_price_eur_per_kwh", 0)): _number(-100, 100, "EUR/kWh", "any"),
        })
        if any(not self._thermal_choices(module, key) for key, module in (
                (CONF_PV_IMPORT, MODULE_PV_IMPORT), (CONF_IMPORT_PRICE, MODULE_IMPORT_PRICE),
                (CONF_EXPORT_PRICE, MODULE_EXPORT_PRICE))):
            placeholders["reason"] = "First register PV AC, import prices and export prices under BEMS forecast importer."
        return self.async_show_form(step_id="thermal_cost_inputs", errors=errors,
                                    description_placeholders=placeholders, data_schema=vol.Schema(fields))

    async def _async_thermal_preview(self):
        from .thermal_coordinator import async_calculate_cost

        return await async_calculate_cost(self.hass, self._values())

    async def async_step_thermal_cost_preview(self, user_input=None):
        if user_input is not None:
            try:
                await self._async_thermal_preview()
            except (ValueError, TypeError, KeyError, IndexError, OverflowError):
                return await self.async_step_thermal_cost_inputs(self._values())
            return self._finish(MODULE_THERMAL_COST)
        result = self._thermal_result
        costs = [row["cost_eur_per_timestep"] for row in result["forecast"]]
        return self.async_show_form(step_id="thermal_cost_preview", data_schema=vol.Schema({}),
            description_placeholders={"count": str(len(costs)), "start": result["forecast"][0]["start"],
                "end": result["forecast"][-1]["end"], "current": f"{costs[0]:.6f}",
                "minimum": f"{min(costs):.6f}", "maximum": f"{max(costs):.6f}",
                "cop": f"{result['current_cop']:.4f}", "fallback": str(result["cop_fallback_intervals"]),
                "reason": result["cop_fallback_reason"] or "None"})


class HABEMSConfigFlow(_ModuleSteps, config_entries.ConfigFlow, domain=DOMAIN):
    """Create one installation; manage individual items in its options menu."""

    VERSION = 1
    MINOR_VERSION = 4

    async def async_step_user(self, user_input=None):
        if any(not entry.data.get("migrated_to") for entry in self.hass.config_entries.async_entries(DOMAIN)):
            return self.async_abort(reason="already_configured")
        await self.async_set_unique_id("ha_bems_installation")
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="HA-BEMS", data={CONF_LAYOUT: LAYOUT_FLAT, CONF_ITEMS: {}})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        if is_flat(config_entry):
            return {}
        group = config_entry.data.get(CONF_GROUP)
        if group in GROUP_MODULES and GROUP_MODULES[group]:
            return {group: HABEMSItemFlow}
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return HABEMSOptionsFlow()

    @classmethod
    @callback
    def async_supports_options_flow(cls, config_entry):
        return not config_entry.data.get("migrated_to") and (is_flat(config_entry) or CONF_GROUP not in config_entry.data)


class HABEMSOptionsFlow(_ModuleSteps, config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if is_flat(self.config_entry):
            options = ["add_import", "add_helper", "add_planner"]
            if self.config_entry.data.get(CONF_ITEMS):
                options += ["edit_item", "remove_item"]
            return self.async_show_menu(step_id="init", menu_options=options)
        module = self._values().get(CONF_MODULE, MODULE_SOC)
        if module == MODULE_LOAD:
            return await self.async_step_load_profile()
        if module == MODULE_FBC:
            return await self.async_step_fbc()
        if module == MODULE_PV_IMPORT:
            return await self.async_step_import_pv()
        if module == MODULE_LOAD_IMPORT:
            return await self.async_step_import_load()
        if module == MODULE_IMPORT_PRICE:
            return await self.async_step_import_price()
        if module == MODULE_EXPORT_PRICE:
            return await self.async_step_export_price()
        if module == MODULE_OUTDOOR_TEMP:
            return await self.async_step_import_temperature()
        if module == MODULE_THERMAL_COST:
            return await self.async_step_thermal_cost()
        if module == MODULE_THERMAL_PLAN:
            return await self.async_step_thermal_plan()
        if module == MODULE_OPERATION_IMPORT:
            return await self.async_step_import_operation()
        return await self.async_step_soc()

    def _start_flat_add(self):
        self._flat_mode, self._flat_item_id = "add", None
        self._draft = {}
        self._subentry = None

    async def async_step_add_import(self, user_input=None):
        self._start_flat_add()
        return self.async_show_menu(step_id="add_import", menu_options=[
            "import_pv_ac", "import_pv_dc", "import_load", "import_price", "export_price", "import_temperature", "import_operation"])

    async def async_step_add_helper(self, user_input=None):
        self._start_flat_add()
        return self.async_show_menu(step_id="add_helper", menu_options=["soc", "load_profile", "thermal_cost"])

    async def async_step_add_planner(self, user_input=None):
        self._start_flat_add()
        return self.async_show_menu(step_id="add_planner", menu_options=["fbc", "thermal_plan"])

    async def _select_flat_item(self, step, user_input):
        items = self.config_entry.data.get(CONF_ITEMS, {})
        if user_input is not None:
            identifier = user_input.get(CONF_ITEM_ID)
            if identifier in items:
                self._flat_item_id = identifier
                self._subentry = SimpleNamespace(title=items[identifier]["title"], data=items[identifier]["data"])
                self._draft = {}
                self._flat_mode = "edit" if step == "edit_item" else "remove"
                return await (self.async_step_item_settings() if step == "edit_item" else self.async_step_remove_confirm())
        return self.async_show_form(step_id=step, data_schema=vol.Schema({
            vol.Required(CONF_ITEM_ID): _choice([{"value": key,
                "label": f"{record['title']} — {GROUP_TITLES[group_for_module(record['data'][CONF_MODULE])]}"}
                for key, record in items.items()])}))

    async def async_step_edit_item(self, user_input=None):
        return await self._select_flat_item("edit_item", user_input)

    async def async_step_remove_item(self, user_input=None):
        return await self._select_flat_item("remove_item", user_input)

    async def async_step_item_settings(self, user_input=None):
        if user_input is not None:
            self._remember(user_input)
            if not self._values().get(CONF_ENABLED, True):
                return self._finish(self._values()[CONF_MODULE])
            return await HABEMSItemFlow._async_edit_module(self)
        return self.async_show_form(step_id="item_settings", data_schema=vol.Schema({
            vol.Required("name", default=self._subentry.title): str,
            vol.Required(CONF_ENABLED, default=self._values().get(CONF_ENABLED, True)): bool,
        }))

    async def async_step_remove_confirm(self, user_input=None):
        if user_input is not None:
            identifier = self._flat_item_id
            items = dict(self.config_entry.data.get(CONF_ITEMS, {}))
            items.pop(identifier, None)
            self.hass.config_entries.async_update_entry(self.config_entry,
                data={CONF_LAYOUT: LAYOUT_FLAT, CONF_ITEMS: items}, options={})
            from .flat_migration import remove_item_registrations

            remove_item_registrations(self.hass, self.config_entry, identifier)
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="remove_confirm", data_schema=vol.Schema({}),
            description_placeholders={"name": self._subentry.title})


class HABEMSItemFlow(_ModuleSteps, config_entries.ConfigSubentryFlow):
    """Add/reconfigure one named item inside its category card."""

    async def async_step_user(self, user_input=None):
        group = self._get_entry().data[CONF_GROUP]
        options = {
            "entity_creator": ["soc", "load_profile", "thermal_cost"],
            "entity_importer": ["import_pv_ac", "import_pv_dc", "import_load", "import_price", "export_price", "import_temperature", "import_operation"],
            "electrical_storage": ["fbc"],
            "thermal_storage": ["thermal_plan"],
        }
        if group not in options:
            return self.async_abort(reason="not_implemented")
        return self.async_show_menu(step_id="user", menu_options=options[group])

    async def async_step_reconfigure(self, user_input=None):
        self._subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            self._remember(user_input)
            if not self._values().get(CONF_ENABLED, True):
                # A broken source must not prevent the user disabling its item.
                return self._finish(self._values()[CONF_MODULE])
            return await self._async_edit_module()
        return self.async_show_form(step_id="reconfigure", data_schema=vol.Schema({
            vol.Required("name", default=self._subentry.title): str,
            vol.Required(CONF_ENABLED, default=self._subentry.data.get(CONF_ENABLED, True)): bool,
        }))

    async def _async_edit_module(self):
        method = {
            MODULE_SOC: self.async_step_soc, MODULE_LOAD: self.async_step_load_profile,
            MODULE_FBC: self.async_step_fbc, MODULE_PV_IMPORT: self.async_step_import_pv,
            MODULE_LOAD_IMPORT: self.async_step_import_load, MODULE_IMPORT_PRICE: self.async_step_import_price,
            MODULE_EXPORT_PRICE: self.async_step_export_price, MODULE_OUTDOOR_TEMP: self.async_step_import_temperature,
            MODULE_THERMAL_COST: self.async_step_thermal_cost,
            MODULE_THERMAL_PLAN: self.async_step_thermal_plan, MODULE_OPERATION_IMPORT: self.async_step_import_operation,
        }[self._values()[CONF_MODULE]]
        return await method()
