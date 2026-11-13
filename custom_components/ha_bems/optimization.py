"""One clock, immutable run inputs and ordered, staged BEMS planning."""

from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, timedelta
import logging
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

from .const import (CONF_ADDITIONAL_LOADS, DOMAIN, MODULE_FBC,
                    MODULE_OPTIMIZATION_MANAGER, MODULE_THERMAL_COST, MODULE_THERMAL_PLAN)
from .entries import all_module_entries, item_enabled, module_type
from .forecast import parse_timestamp

_LOGGER = logging.getLogger(__name__)
_RUN = ContextVar("bems_optimization_run", default=None)
UNMANAGED = object()
DEFAULT_SETTINGS = {"planner_order": [], "planner_time_limit_s": 20, "total_time_limit_s": 120}


def _now():
    from homeassistant.util import dt as dt_util

    return dt_util.now()


def current_run(hass):
    run = _RUN.get()
    return run if run and run.hass is hass else None


def manager(hass):
    return hass.data.get(DOMAIN, {}).get("optimization_manager")


def cost_owner(hass, entity_id):
    """Resolve an entity to its stable cost item, including startup/unavailable entities."""
    if not entity_id:
        return None
    from homeassistant.helpers import entity_registry as er

    record = er.async_get(hass).async_get(entity_id)
    if record and record.platform == DOMAIN and record.unique_id.endswith("_thermal_runtime_cost"):
        return record.unique_id.removesuffix("_thermal_runtime_cost")
    return None


def planning_graph(hass, overrides=None):
    overrides = overrides or {}
    items = {item.entry_id: item for item in all_module_entries(hass)
             if module_type(item) in (MODULE_FBC, MODULE_THERMAL_PLAN)}
    costs = {item.entry_id: {**item.data, **item.options} for item in all_module_entries(hass)
             if module_type(item) == MODULE_THERMAL_COST}
    for identifier, config in overrides.items():
        if config.get("module") == MODULE_THERMAL_COST:
            costs[identifier] = config
        elif config.get("module") in (MODULE_FBC, MODULE_THERMAL_PLAN):
            items[identifier] = SimpleNamespace(entry_id=identifier, data=config, options={},
                title=config.get("name", items[identifier].title if identifier in items else "New planner"))
    dependencies = {}
    from .operation_constraints import constraint_dependency

    for identifier, item in items.items():
        config = {**item.data, **item.options}
        upstream = constraint_dependency(hass, config) if module_type(item) == MODULE_THERMAL_PLAN else None
        refs = set([upstream] if upstream else [])
        cost = costs.get(cost_owner(hass, config.get("cost_entity", "")), {})
        refs.update(ref.partition(":")[2] for ref in cost.get(CONF_ADDITIONAL_LOADS, [])
                    if ref.startswith("planner:"))
        dependencies[identifier] = refs
    return items, dependencies


def ordered_planners(hass, saved_order=None, overrides=None):
    """Preserve explicit user order; append new modules at a valid position."""
    items, dependencies = planning_graph(hass, overrides)
    saved = [key for key in (saved_order or []) if key in items]
    if len(saved) != len(set(saved)):
        raise ValueError("Each planner must appear only once in the optimization sequence")
    positions = {key: i for i, key in enumerate(saved)}
    for key, refs in dependencies.items():
        for ref in refs:
            if ref not in items:
                raise ValueError(f"{items[key].title}: upstream planner is missing")
            if key in positions and ref in positions and positions[ref] >= positions[key]:
                raise ValueError(f"{items[ref].title} must run before {items[key].title}")
    for previous, following in zip(saved, saved[1:]):
        dependencies[following].add(previous)
    pending = sorted(items, key=lambda key: (positions.get(key, len(saved)),
        module_type(items[key]) == MODULE_FBC, items[key].title.casefold(), key))
    result = []
    while pending:
        key = next((key for key in pending if dependencies[key] <= set(result)), None)
        if key is None:
            raise ValueError("Circular optimization dependency or conflicting saved sequence")
        result.append(key)
        pending.remove(key)
    return result


def managed_result(coordinator):
    """Autonomous refreshes may expose existing plans, but cannot launch work."""
    control = manager(coordinator.hass)
    identifier = getattr(coordinator, "identifier", None)
    if control is None or identifier not in control.members:
        return UNMANAGED
    run = current_run(coordinator.hass)
    if run and run.target == identifier:
        return UNMANAGED
    from homeassistant.helpers.update_coordinator import UpdateFailed

    if coordinator.data:
        stamp = coordinator.data.get("generated_at")
        if stamp and _now() - parse_timestamp(stamp) <= timedelta(hours=1):
            result = coordinator.data
            if getattr(coordinator, "optimization_module", None) == MODULE_THERMAL_PLAN:
                from .thermal_plan_coordinator import projected_state
                try:
                    return {**result, "value": projected_state(result, _now())[0]}
                except ValueError as err:
                    raise UpdateFailed(str(err)) from err
            return result
    raise UpdateFailed("Waiting for the next Optimization Manager run")


def plan_snapshot(hass, identifier):
    run = current_run(hass)
    if run:
        if identifier not in run.staged:
            raise ValueError("Upstream planner has no result in this optimization run")
        return run.staged[identifier]
    coordinator = hass.data.get(DOMAIN, {}).get("planners", {}).get(identifier)
    return coordinator.data if coordinator else None


def provider_snapshot(hass, identifier, fallback):
    run = current_run(hass)
    return run.providers.get(identifier, fallback) if run else fallback


def input_state(hass, entity_id):
    """Internal consumers see staged costs; published entities keep the previous batch."""
    run = current_run(hass)
    if run:
        identifier = cost_owner(hass, entity_id)
        if identifier:
            data = plan_snapshot(hass, identifier)
            return SimpleNamespace(state=str(data["value"]), entity_id=entity_id,
                attributes={**data, "unit_of_measurement": "EUR/timestep"})
        if run.states is not None:
            return run.states.get(entity_id)
    return hass.states.get(entity_id)


def validate_settings(config):
    for key, low, high in (("planner_time_limit_s", 1, 120), ("total_time_limit_s", 1, 840)):
        value = config.get(key, DEFAULT_SETTINGS[key])
        if type(value) not in (int, float) or not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high} seconds")


class OptimizationManager:
    """Stage the complete sequence, then publish in one event-loop turn."""

    def __init__(self, hass):
        self.hass = hass
        self.members = {}
        self.outputs = {}
        self.listeners = []
        self.busy = False
        self._last_slot = None
        self.last_successful_run = None
        self.status = "waiting"
        self.details = {}
        self._unsubs = []

    def settings(self):
        entries = [item for item in all_module_entries(self.hass)
                   if module_type(item) == MODULE_OPTIMIZATION_MANAGER]
        if len(entries) > 1:
            raise ValueError("Only one Optimization Manager is supported")
        if entries and not item_enabled(entries[0]):
            raise ValueError("Optimization Manager is disabled")
        return {**DEFAULT_SETTINGS, **({**entries[0].data, **entries[0].options} if entries else {})}

    def restore_output(self, coordinator):
        data = self.outputs.get(coordinator.identifier)
        if not coordinator.data and data and _now() - parse_timestamp(data["generated_at"]) <= timedelta(hours=1):
            coordinator.data = data
            coordinator.last_update_success = True
            if hasattr(coordinator, "plan_status"):
                coordinator.plan_status = data.get("solver_status", "waiting_for_inputs")

    def listen(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    def notify(self):
        for listener in list(self.listeners):
            listener()

    def retain(self, keys, reason, now):
        from homeassistant.helpers.update_coordinator import UpdateFailed

        for key in keys:
            coordinator = self.members.get(key)
            if coordinator is None:
                continue
            data = coordinator.data
            stamp = data.get("generated_at") if data else None
            if stamp and now - parse_timestamp(stamp) <= timedelta(hours=1):
                flag = "using_previous_forecast" if coordinator.optimization_module == MODULE_THERMAL_COST else "using_previous_plan"
                coordinator.data = {**data, flag: True, "refresh_error": reason}
            else:
                coordinator.last_update_success = False
                coordinator.last_exception = UpdateFailed(reason)
                if hasattr(coordinator, "plan_status"):
                    coordinator.plan_status = "error"

    def start(self):
        from homeassistant.core import callback
        from homeassistant.helpers.event import async_track_time_change

        @callback
        def tick(_event):
            self.hass.async_create_task(self.async_run("scheduled"))

        @callback
        def stop(_event):
            for unsubscribe in self._unsubs:
                unsubscribe()
            self._unsubs.clear()

        self._unsubs.append(async_track_time_change(self.hass, tick, minute=[0, 15, 30, 45], second=0))
        self._unsubs.append(self.hass.bus.async_listen_once("homeassistant_stop", stop))

    async def async_run(self, reason="manual"):
        from homeassistant.helpers.update_coordinator import UpdateFailed
        from .import_registry import coordinators, planners

        if self.busy:
            return False  # Repeated presses never queue another run.
        now = _now().astimezone(UTC)
        slot = int(now.timestamp()) // 900
        if reason != "manual" and self._last_slot == slot:
            return False
        self.busy = True
        self._last_slot = slot
        started = monotonic()
        self.status = "running"
        self.details = {"run_id": uuid4().hex, "trigger": reason, "started_at": now.isoformat(),
                        "current_planner": None, "error": None}
        self.notify()
        snapshots = {}
        active = {}
        token = None
        try:
            settings = deepcopy(self.settings())
            validate_settings(settings)
            order = ordered_planners(self.hass, settings["planner_order"])
            items, dependencies = planning_graph(self.hass)
            order = [key for key in order if item_enabled(items[key])]
            active = dict(self.members)
            fbc_modes = {key: value.limitation_enabled for key, value in active.items()
                         if getattr(value, "optimization_module", None) == MODULE_FBC}
            registry = planners(self.hass)
            for key in order:
                if key not in active or registry.get(key) is not active[key]:
                    raise ValueError(f"{items[key].title} is not loaded")
            run = SimpleNamespace(hass=self.hass, staged={}, target=None, now=now,
                reason=reason, solver_limit=settings["planner_time_limit_s"],
                providers={key: SimpleNamespace(data=deepcopy(value.data),
                    last_update_success=value.last_update_success, last_exception=value.last_exception,
                    input_status=getattr(value, "input_status", "ready"))
                    for key, value in {**coordinators(self.hass), **{
                        key: value for key, value in registry.items() if key not in active}}.items()},
                states={state.entity_id: state for state in self.hass.states.async_all()}
                    if hasattr(self.hass.states, "async_all") else None)
            token = _RUN.set(run)
            steps = []
            for key in order:
                cost = cost_owner(self.hass, active[key].config.get("cost_entity", ""))
                if cost and cost not in steps:
                    steps.append(cost)
                steps.append(key)
            # Keep unconsumed cost helpers current too, after their upstream plans.
            steps.extend(key for key, value in active.items()
                         if getattr(value, "optimization_module", None) == MODULE_THERMAL_COST and key not in steps)
            step_dependencies = {}
            for key in steps:
                coordinator = active.get(key)
                if coordinator is None:
                    raise ValueError("A required runtime-cost module is not loaded")
                if coordinator.optimization_module == MODULE_THERMAL_COST:
                    refs = {ref.partition(":")[2] for ref in coordinator.config.get(CONF_ADDITIONAL_LOADS, [])
                            if ref.startswith("planner:")}
                else:
                    refs = set(dependencies.get(key, set()))
                    cost = cost_owner(self.hass, coordinator.config.get("cost_entity", ""))
                    if cost:
                        refs.add(cost)
                step_dependencies[key] = refs
            failures = {}
            self.details["sequence"] = [items[key].title for key in order]
            for index, key in enumerate(steps):
                coordinator = active.get(key)
                if coordinator is None or registry.get(key) is not coordinator:
                    raise ValueError("A required runtime-cost module is not loaded")
                if not step_dependencies[key] <= run.staged.keys():
                    failures[key] = "Upstream module has no new result in this run"
                    continue
                remaining = settings["total_time_limit_s"] - (monotonic() - started)
                if remaining <= 0:
                    failures[key] = "Total optimization time budget exhausted"
                    continue
                remaining_solvers = sum(getattr(active.get(k), "optimization_module", None)
                    == MODULE_THERMAL_PLAN for k in steps[index:])
                run.solver_limit = min(settings["planner_time_limit_s"], remaining / max(1, remaining_solvers))
                if remaining_solvers and run.solver_limit < 1:
                    failures[key] = "Insufficient remaining time for the next solver"
                    continue
                run.target = key
                self.details["current_planner"] = coordinator.name
                self.notify()
                # Restore all model/cost caches if the batch cannot be committed.
                fields = ("_plan", "_last_solve_slot", "_slot_error", "_loaded", "_last_good_cost",
                          "_calculation_key", "_calculation_result", "_temperature_failure_since",
                          "plan_status", "awaiting_inputs")
                snapshots[key] = {field: deepcopy(getattr(coordinator, field))
                                  for field in fields if hasattr(coordinator, field)}
                try:
                    result = await coordinator._async_update_data()
                    if result.get("using_previous_plan") or result.get("using_previous_forecast"):
                        raise ValueError(f"Fresh result unavailable: {result.get('refresh_error')}")
                except Exception as err:
                    _LOGGER.debug("Optimization module %s failed", coordinator.name, exc_info=True)
                    failures[key] = str(err)
                    continue
                result = {**result, "optimization_run_id": self.details["run_id"],
                          "optimization_slot": slot, "optimization_trigger": reason}
                if getattr(coordinator, "_plan", None):
                    coordinator._plan = result
                run.staged[key] = result
                if monotonic() - started > settings["total_time_limit_s"]:
                    failures[key] = "Total optimization time budget exhausted"
            if int(_now().timestamp()) // 900 != slot:
                raise ValueError("Optimization crossed a quarter-hour boundary; results discarded")
            if self.settings() != settings or any(self.members.get(key) is not active[key] for key in steps):
                raise ValueError("Configuration changed during optimization; results discarded")
            if any(active[key].limitation_enabled != mode for key, mode in fbc_modes.items()):
                raise ValueError("FBC bypass changed during optimization; results discarded")
            if run.states is not None:
                for key in fbc_modes:
                    def safeguard(read):
                        states = [read(active[key].config.get(field))
                                  for field in ("soc_entity", "seasonal_soc_entity")]
                        try:
                            if any(state is None or state.attributes.get("unit_of_measurement") != "%" for state in states):
                                return "unavailable"
                            values = [float(state.state) for state in states]
                            if any(not 0 <= value <= 100 for value in values):
                                return "unavailable"
                            return values[0] <= values[1]
                        except (ValueError, TypeError):
                            return "unavailable"
                    if safeguard(run.states.get) != safeguard(self.hass.states.get):
                        raise ValueError("Battery safeguard changed during optimization; results discarded")
            # No await between assigning results and notifying all output entities.
            _RUN.reset(token)
            token = None
            # A connected dependency group is published together. Independent
            # groups can succeed even if a separate planner/helper fails.
            failed_group = set(failures)
            while True:
                expanded = failed_group | {key for key, refs in step_dependencies.items()
                                           if key in failed_group or refs & failed_group}
                expanded |= {ref for key, refs in step_dependencies.items() if key in expanded for ref in refs}
                if expanded == failed_group:
                    break
                failed_group = expanded
            for key in failed_group:
                run.staged.pop(key, None)
                for field, value in snapshots.pop(key, {}).items():
                    setattr(active[key], field, value)
            error = "; ".join(f"{active[key].name}: {value}" for key, value in failures.items())
            self.retain(failed_group, error, now)
            for key, data in run.staged.items():
                coordinator = active[key]
                coordinator.data = data
                coordinator.last_update_success = True
                coordinator.last_exception = None
            for key in set(run.staged) | (failed_group & active.keys()):
                active[key].async_update_listeners()
            self.status = "partially_completed" if failures and run.staged else "failed" if failures else "completed"
            self.details["error"] = error or None
            self.details["module_errors"] = failures
            if not failures:
                self.last_successful_run = now.isoformat()
            snapshots.clear()
            for key in run.staged:
                coordinator = active[key]
                if getattr(coordinator, "_plan", None):
                    await coordinator._store.async_save({"model_key": coordinator._model_key,
                                                         "plan": coordinator._plan})
            return not failures
        except Exception as err:
            self.status = "failed"
            self.details["error"] = str(err)
            _LOGGER.warning("Optimization Manager run failed: %s", err)
            for key, fields in snapshots.items():
                for field, value in fields.items():
                    setattr(active[key], field, value)
            snapshots.clear()
            self.retain(self.members, str(err), now)
            for coordinator in self.members.values():
                coordinator.async_update_listeners()
            return False
        finally:
            if token is not None:
                _RUN.reset(token)
            for key, fields in snapshots.items():
                for field, value in fields.items():
                    setattr(active[key], field, value)
            self.busy = False
            self.details["current_planner"] = None
            self.details["duration_s"] = round(monotonic() - started, 3)
            self.details["last_successful_run"] = self.last_successful_run
            self.notify()


def ensure_runtime(hass):
    data = hass.data.setdefault(DOMAIN, {})
    if "optimization_manager" not in data:
        data["optimization_manager"] = OptimizationManager(hass)
        data["optimization_manager"].start()
    return data["optimization_manager"]


async def ensure_manager_entry(hass):
    """Existing installations receive one visible manager without changing item IDs."""
    if any(module_type(item) == MODULE_OPTIMIZATION_MANAGER for item in all_module_entries(hass)):
        return
    from .const import CONF_LAYOUT, CONF_ITEMS, LAYOUT_SPLIT

    await hass.config_entries.flow.async_init(DOMAIN, context={"source": "import"}, data={
        "split_unique_id": "optimization_manager", "title": "Optimization Manager",
        "data": {CONF_LAYOUT: LAYOUT_SPLIT, CONF_ITEMS: {"optimization_manager": {
            "title": "Optimization Manager", "data": {"module": MODULE_OPTIMIZATION_MANAGER,
                "item_id": "optimization_manager", **DEFAULT_SETTINGS}}}}})
