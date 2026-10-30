"""Persistent, feed-forward storage state and hierarchical thermal planning."""

from datetime import UTC
import json
import logging

from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MODULE_OUTDOOR_TEMP
from .forecast import parse_timestamp
from .forecast_input import _async_read_forecast
from .import_registry import coordinators, planners, read_forecast_import
from .operation_constraints import constraint_dependency, constraint_selection, read_constraint_forecast, validate_hierarchy
from .thermal_milp import InfeasiblePlanError, required_runtimes, solve_plan, validate_plan_settings

_LOGGER = logging.getLogger(__name__)


def quarter_slot(now):
    """Use UTC slot IDs so repeated local hours at DST remain distinct."""
    return int(now.timestamp()) // 900


def model_key(config):
    keys = ("storage_duration_h", "storage_min_h", "storage_model_revision", "initial_storage_h",
            "runtime_model", "runtime_h_per_24h", "runtime_points", "runtime_min_h", "runtime_max_h",
            "runtime_temperature_entry_id", "heating_cutoff_enabled", "heating_cutoff_c")
    return json.dumps({key: 0 if key == "storage_min_h" else config.get(key) for key in keys}, sort_keys=True)


def aligned_values(reference, other, key):
    index = {row["start"]: row for row in other}
    result = []
    for row in reference:
        matching = index.get(row["start"])
        if matching is None or matching["end"] != row["end"]:
            raise ValueError("All planner input forecasts must match the same 15-minute intervals")
        result.append(matching[key])
    return result


def projected_state(plan, now):
    """Advance only along the published plan, never by equipment feedback."""
    previous = plan["initial_previous_operation"]
    elapsed = plan["initial_previous_elapsed_h"]
    for row in plan["forecast"]:
        start = parse_timestamp(row["effective_start"])
        end = parse_timestamp(row["end"])
        if now < start:
            break
        if now == start:
            return row["storage_start_h"], previous, elapsed, None
        if row["operation"] != previous:
            previous, elapsed = row["operation"], 0
        consumed = (min(now, end) - start).total_seconds() / 3600
        if consumed < 0:
            raise ValueError("Stored plan timestamps are inconsistent")
        elapsed += consumed
        if now < end:
            level = row["storage_start_h"] + (row["operation"] - row["runtime_h_per_24h"] / 24) * consumed
            return level, previous, elapsed, previous
        if now == end:
            return row["storage_end_h"], previous, elapsed, None
    raise ValueError("Feed-forward plan has expired; edit the initial storage level to restart the model")


async def read_plan_inputs(hass, config, now):
    rows, info = await _async_read_forecast(hass, {"forecast_entity": config["cost_entity"]},
        "Thermal runtime cost", "EUR/timestep", "cost_eur_per_timestep", False)
    rows = rows[:289]
    temperatures = None
    if config["runtime_model"] == "lookup":
        other, _ = read_forecast_import(hass, config.get("runtime_temperature_entry_id"), MODULE_OUTDOOR_TEMP, now)
        temperatures = aligned_values(rows, other, "temperature_c")
    allowed = [1] * len(rows)
    if (other := read_constraint_forecast(hass, config, now)) is not None:
        allowed = aligned_values(rows, other, "allowed")
    return rows, required_runtimes(config, temperatures, len(rows)), allowed, info


class ThermalPlanCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, parent=None):
        super().__init__(hass, _LOGGER, name=entry.title, config_entry=parent or entry,
                         update_interval=None)
        self.config = {**entry.data, **entry.options}
        self.config.pop("storage_min_h", None)
        self.identifier = entry.entry_id
        self._store = Store(hass, 1, f"{DOMAIN}.thermal_plan.{self.identifier}")
        self._loaded = False
        self._plan = None
        self._last_solve_slot = None
        self._slot_error = None
        self.awaiting_inputs = True
        self.plan_status = "waiting_for_inputs"
        self._previous_health = None
        self._failure_notified = False
        # Changes to the storage/demand model explicitly restart its estimate.
        self._model_key = model_key(self.config)

    @callback
    def _async_refresh_finished(self):
        # Consecutive failed refreshes normally suppress coordinator updates.
        # Keep changed diagnostic states/reasons visible without extra solves.
        health = (self.last_update_success, self.plan_status, str(self.last_exception))
        if (not self.last_update_success and self._previous_health is not None
                and not self._previous_health[0] and health != self._previous_health):
            self.async_update_listeners()
        self._previous_health = health

    async def _async_update_data(self):
        now = dt_util.now().astimezone(UTC)
        if self._last_solve_slot == quarter_slot(now):
            if self._slot_error:
                raise UpdateFailed(self._slot_error)
            # Parent requests, duplicate startup callbacks and input updates
            # must not launch a second solve in an already calculated slot.
            return {**self._plan, "value": projected_state(self._plan, now)[0]}
        try:
            result = await self._calculate()
        except (ValueError, TypeError, KeyError, IndexError, OverflowError, OSError) as err:
            self.plan_status = ("infeasible" if isinstance(err, InfeasiblePlanError)
                                else "waiting_for_inputs" if self.awaiting_inputs else "error")
            if self._last_solve_slot == quarter_slot(now):
                self._slot_error = str(err)
            if not self._failure_notified:
                from homeassistant.components import persistent_notification

                persistent_notification.async_create(self.hass,
                    f"Thermal operation planner {self.name}: {err}. Check inputs and constraints; no valid new recommendation is available.",
                    title="BEMS thermal planning needs attention", notification_id=f"{DOMAIN}_thermal_{self.identifier}")
                self._failure_notified = True
            raise UpdateFailed(str(err)) from err
        self.plan_status = result["solver_status"]
        if self._failure_notified:
            from homeassistant.components import persistent_notification

            persistent_notification.async_dismiss(self.hass, f"{DOMAIN}_thermal_{self.identifier}")
            self._failure_notified = False
        return result

    async def _calculate(self):
        now = dt_util.now().astimezone(UTC)
        self.awaiting_inputs = False
        validate_plan_settings(self.config)
        validate_hierarchy(self.hass, self.identifier, self.config)
        # Readiness retries may finish a startup/scheduled run whose inputs
        # were still updating. They cannot repeat an actual solver attempt.
        self.awaiting_inputs = True
        if upstream := constraint_dependency(self.hass, self.config):
            coordinator = planners(self.hass).get(upstream)
            if coordinator is None:
                raise ValueError("The upstream thermal planner is not loaded")
            await coordinator.async_request_refresh()
            if not coordinator.last_update_success:
                raise ValueError("The upstream thermal planner has no valid plan")
        source, mode = constraint_selection(self.hass, self.config)
        if source and source.startswith("imported:"):
            reference = source.split(":", 1)[1]
            coordinator = coordinators(self.hass).get(reference)
            if coordinator is None:
                raise ValueError("The selected operation constraint import is not loaded")
            await coordinator.async_request_refresh()
        rows, runtimes, allowed, info = await read_plan_inputs(self.hass, self.config, now)
        self.awaiting_inputs = False
        # Regular runs model the complete clock-aligned quarter-hour, even
        # though input validation/solver dispatch happens milliseconds later.
        # Only an initial startup/reload solve uses a partial first interval.
        planning_at = now
        if self._last_solve_slot is not None:
            planning_at = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        if not self._loaded:
            saved = await self._store.async_load()
            if saved and saved.get("model_key") == self._model_key:
                self._plan = saved["plan"]
            self._loaded = True
        low, high = 0, self.config["storage_duration_h"]
        if self._plan:
            level, previous, elapsed, fixed = projected_state(self._plan, planning_at)
            if not low - 1e-7 <= level <= high + 1e-7:
                raise ValueError("Projected storage level is outside its limits")
            terminal = self._plan["terminal_storage_min_h"]
        else:
            level = self.config.get("initial_storage_h")
            if level is None:
                level = low + (high - low) / 2
            previous, elapsed, fixed = 0, 72, None
            terminal = level
        durations = [(parse_timestamp(row["end"]) - max(planning_at, parse_timestamp(row["start"]))).total_seconds() / 3600 for row in rows]
        self._last_solve_slot = quarter_slot(now)
        self._slot_error = None
        result = await self.hass.async_add_executor_job(solve_plan,
            [row["cost_eur_per_timestep"] for row in rows], runtimes, durations, level, self.config,
            previous, elapsed, allowed, fixed, terminal)
        if dt_util.now() >= parse_timestamp(rows[0]["end"]):
            raise ValueError("A timestep elapsed during optimization; waiting for refreshed forecasts")
        forecast = [{"start": row["start"], "end": row["end"],
            "effective_start": max(planning_at, parse_timestamp(row["start"])).isoformat(),
            "operation": operation, "storage_start_h": result["storage"][i],
            "storage_end_h": result["storage"][i + 1], "runtime_h_per_24h": runtimes[i], "allowed": allowed[i]}
            for i, (row, operation) in enumerate(zip(rows, result["operations"]))]
        plan = {"value": level, "forecast": forecast, "schema_version": 1, "interval_minutes": 15,
            "planner_item_id": self.identifier, "module": "thermal_storage_milp",
            "generated_at": now.isoformat(), "source_updated_at": now.isoformat(),
            "calculation_interval_minutes": 15, "calculation_schedule": "quarter_hour_clock",
            "state_basis": "feed_forward_assuming_plan_is_followed", "feedback_used": False,
            "initial_previous_operation": previous, "initial_previous_elapsed_h": elapsed,
            "terminal_storage_min_h": terminal, "storage_min_h": low, "storage_max_h": high,
            "cost_entity": self.config["cost_entity"], "constraint_source": source, "constraint_mode": mode,
            "runtime_model": self.config["runtime_model"], "input_source_updated_at": info["source_updated_at"],
            **{key: value for key, value in result.items() if key not in ("storage", "operations")}}
        await self._store.async_save({"model_key": self._model_key, "plan": plan})
        self._plan = plan
        return plan
