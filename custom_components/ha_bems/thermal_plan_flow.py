"""Setup steps for independent thermal MILP planners and runtime demand."""

import voluptuous as vol

from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from .const import MODULE_OPERATION_IMPORT, MODULE_OUTDOOR_TEMP, MODULE_THERMAL_PLAN
from .thermal_milp import solve_plan, validate_plan_settings


def number_selector(low, high, unit=None):
    config = {"min": low, "max": high, "step": "any", "mode": "box"}
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


class ThermalPlanSteps:
    async def async_step_thermal_plan(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._remember(user_input)
            if user_input.get("runtime_model") in ("constant", "lookup"):
                return await (self.async_step_runtime_constant() if user_input["runtime_model"] == "constant"
                              else self.async_step_runtime_table())
            errors["base"] = "invalid_thermal_plan"
        values = self._values()
        fields = self._name_fields("Thermal storage planner")
        cost_field = vol.Required("cost_entity", default=values["cost_entity"]) if values.get("cost_entity") else vol.Required("cost_entity")
        fields.update({cost_field: selector.EntitySelector({"filter": [{"domain": "sensor", "unit_of_measurement": "EUR/timestep"}]}),
            vol.Required("runtime_model", default=values.get("runtime_model", "constant")):
                selector.SelectSelector({"mode": "dropdown", "options": [
                    {"value": "constant", "label": "Constant runtime requirement"},
                    {"value": "lookup", "label": "Outdoor temperature dependent runtime requirement"}]})})
        return self.async_show_form(step_id="thermal_plan", errors=errors, data_schema=vol.Schema(fields))

    async def async_step_runtime_constant(self, user_input=None):
        if user_input is not None:
            self._remember(user_input)
            value = user_input.get("runtime_h_per_24h")
            if type(value) in (int, float) and 0 <= value <= 24:
                return await self.async_step_thermal_storage()
        return self.async_show_form(step_id="runtime_constant", errors={"base": "invalid_thermal_plan"} if user_input else {},
            data_schema=vol.Schema({vol.Required("runtime_h_per_24h", default=self._values().get("runtime_h_per_24h", 2)):
                number_selector(0, 24, "h/24h")}))

    async def async_step_runtime_table(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._remember(user_input)
            values = self._values()
            try:
                count = values["runtime_point_count"]
                if type(count) not in (int, float) or int(count) != count or not 2 <= count <= 10:
                    raise ValueError("Select 2–10 reference points")
                if values.get("runtime_temperature_entry_id") not in {choice["value"] for choice in self._thermal_choices(MODULE_OUTDOOR_TEMP, "runtime_temperature_entry_id")}:
                    raise ValueError("Select an outdoor temperature input")
                if not 0 <= values["runtime_min_h"] <= values["runtime_max_h"] <= 24:
                    raise ValueError("Invalid runtime limits")
            except (ValueError, KeyError, TypeError):
                errors["base"] = "invalid_thermal_plan"
            else:
                self._draft.pop("runtime_table_text", None)
                return await self.async_step_runtime_points()
        values = self._values()
        return self.async_show_form(step_id="runtime_table", errors=errors, data_schema=vol.Schema({
            self._thermal_field("runtime_temperature_entry_id"): selector.SelectSelector({"mode": "dropdown", "options": self._thermal_choices(MODULE_OUTDOOR_TEMP, "runtime_temperature_entry_id")}),
            vol.Required("runtime_point_count", default=values.get("runtime_point_count", 2)):
                selector.NumberSelector({"min": 2, "max": 10, "step": 1, "mode": "box"}),
            vol.Required("runtime_min_h", default=values.get("runtime_min_h", 2)): number_selector(0, 24, "h/24h"),
            vol.Required("runtime_max_h", default=values.get("runtime_max_h", 24)): number_selector(0, 24, "h/24h"),
            vol.Required("heating_cutoff_enabled", default=values.get("heating_cutoff_enabled", True)): bool,
            vol.Required("heating_cutoff_c", default=values.get("heating_cutoff_c", 20)): number_selector(-100, 100, "°C"),
        }))

    async def async_step_runtime_points(self, user_input=None):
        from .thermal_cost import cop_points, interpolate_cop, number

        errors = {}
        count = int(self._values()["runtime_point_count"])
        if user_input is not None:
            self._remember(user_input)
            try:
                lines = [line.strip() for line in user_input["runtime_table_text"].splitlines() if line.strip()]
                if len(lines) != count:
                    raise ValueError("Row count must match the selected number")
                points = []
                for line in lines:
                    temperature, runtime = (float(part.strip().replace(",", ".")) for part in line.split(";"))
                    number(temperature, "Outdoor temperature")
                    if not 0 <= number(runtime, "Runtime") <= 24:
                        raise ValueError("Runtime must be between 0 and 24 h/24h")
                    points.append({"temperature_c": temperature, "runtime_h_per_24h": runtime})
                cop_points([{"temperature_c": p["temperature_c"], "cop": 1} for p in points])
                self._remember({"runtime_points": sorted(points, key=lambda p: p["temperature_c"])})
            except (ValueError, KeyError, TypeError):
                errors["base"] = "invalid_runtime_points"
            else:
                return await self.async_step_thermal_storage()
        values = self._values()
        points = values.get("runtime_points", [{"temperature_c": -12.1, "runtime_h_per_24h": 18},
                                               {"temperature_c": 12, "runtime_h_per_24h": 0}])
        pairs = sorted((p["temperature_c"], p["runtime_h_per_24h"]) for p in points)
        if len(pairs) != count:
            first, last = pairs[0][0], pairs[-1][0]
            pairs = [(first + (last - first) * i / (count - 1),
                      interpolate_cop(first + (last - first) * i / (count - 1), pairs)) for i in range(count)]
        default = "\n".join(f"{t:g};{r:g}" for t, r in pairs)
        return self.async_show_form(step_id="runtime_points", errors=errors, data_schema=vol.Schema({
            vol.Required("runtime_table_text", default=values.get("runtime_table_text", default)): selector.TextSelector({"multiline": True})}))

    async def async_step_thermal_storage(self, user_input=None):
        errors, placeholders = {}, {"reason": ""}
        if user_input is not None:
            reference = user_input.get("constraint_entry_id")
            self._remember({**user_input, "initial_storage_h": user_input.get("initial_storage_h"),
                            "constraint_entry_id": None if reference in (None, "", "none") else reference})
            try:
                validate_plan_settings(self._values())
                self._plan_preview = await self._async_plan_preview()
            except (ValueError, TypeError, KeyError, IndexError, OverflowError) as err:
                errors["base"] = "invalid_thermal_plan"
                placeholders["reason"] = str(err)
            else:
                return await self.async_step_thermal_plan_preview()
        values = self._values()
        fields = {}
        for key, default, low, high in (("storage_duration_h", 6.4, .01, 1000), ("storage_min_h", 0, 0, 1000),
                ("min_on_h", .75, 0, 72), ("min_off_h", .25, 0, 72)):
            fields[vol.Required(key, default=values.get(key, default))] = number_selector(low, high, "h")
        initial = values.get("initial_storage_h")
        fields[vol.Optional("initial_storage_h", default=initial) if initial is not None else vol.Optional("initial_storage_h")] = number_selector(0, 1000, "h")
        fields[vol.Required("restart_storage_model", default=False)] = bool
        fields[vol.Required("constraint_entry_id", default=values.get("constraint_entry_id") or "none")] = selector.SelectSelector({"mode": "dropdown", "options": [
            {"value": "none", "label": "No operation constraint"}, *self._thermal_choices(MODULE_OPERATION_IMPORT, "constraint_entry_id")]})
        fields[vol.Required("solver_time_limit_s", default=values.get("solver_time_limit_s", 20))] = number_selector(1, 120, "s")
        return self.async_show_form(step_id="thermal_storage", errors=errors, description_placeholders=placeholders, data_schema=vol.Schema(fields))

    async def _async_plan_preview(self):
        from .operation_constraints import validate_hierarchy
        from .thermal_plan_coordinator import model_key, projected_state, read_plan_inputs
        from .import_registry import planners
        from .forecast import parse_timestamp

        config = self._values()
        identifier = getattr(self, "_flat_item_id", None) or config.get("item_id", "new_planner")
        validate_hierarchy(self.hass, identifier, config)
        now = dt_util.now()
        rows, runtimes, allowed, _ = await read_plan_inputs(self.hass, config, now)
        low, high = config.get("storage_min_h", 0), config["storage_duration_h"]
        initial = config.get("initial_storage_h")
        if initial is None:
            initial = low + (high - low) / 2
        previous, elapsed, fixed, terminal = 0, 72, None, initial
        existing = planners(self.hass).get(identifier)
        if (existing and existing._plan and not config.get("restart_storage_model")
                and existing._model_key == model_key(config)):
            initial, previous, elapsed, fixed = projected_state(existing._plan, now)
            terminal = existing._plan["terminal_storage_min_h"]
        durations = [(parse_timestamp(row["end"]) - max(now, parse_timestamp(row["start"]))).total_seconds() / 3600 for row in rows]
        result = await self.hass.async_add_executor_job(solve_plan, [row["cost_eur_per_timestep"] for row in rows],
            runtimes, durations, initial, config, previous, elapsed, allowed, fixed, terminal)
        return {"cost": f"{result['total_cost_eur']:.4f}", "initial": f"{initial:.3f}",
                "count": str(len(rows)), "status": result["solver_status"]}

    async def async_step_thermal_plan_preview(self, user_input=None):
        if user_input is not None:
            try:
                await self._async_plan_preview()
            except (ValueError, TypeError, KeyError, IndexError, OverflowError):
                return await self.async_step_thermal_storage(self._values())
            return self._finish(MODULE_THERMAL_PLAN)
        return self.async_show_form(step_id="thermal_plan_preview", data_schema=vol.Schema({}), description_placeholders=self._plan_preview)
