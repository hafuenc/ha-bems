"""Consumer-side electrical loads from forecasts and unchanged MILP plans."""

from .const import (CONF_ADDITIONAL_LOADS, CONF_LOAD_INPUT, MODULE_THERMAL_PLAN,
                    MODULE_THERMAL_COST)
from .entries import item_enabled
from .forecast import FILL_MARKER, parse_timestamp, validate_forecast
from .forecast_input import ForecastConfigurationError, ForecastUnavailable, source_age
from .import_registry import input_entries, load_inputs, planners, read_load_input, snapshot_usable
from .thermal_cost import number


def additional_load_choices(hass):
    return load_inputs(hass) + [(f"planner:{entry.entry_id}", f"{entry.title} (MILP electrical load plan)")
                               for entry in input_entries(hass, MODULE_THERMAL_PLAN)]


def validate_load_hierarchy(hass, config, identifier=None):
    """Reject feedback loops between costs and higher-priority plans."""
    if not any(ref.startswith("planner:") for ref in config.get(CONF_ADDITIONAL_LOADS, [])):
        return
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    costs = {entry.entry_id: {**entry.data, **entry.options}
             for entry in input_entries(hass, MODULE_THERMAL_COST)}
    if identifier:
        costs[identifier] = config
    plans = {entry.entry_id: {**entry.data, **entry.options}
             for entry in input_entries(hass, MODULE_THERMAL_PLAN)}

    def visit(identifier, path):
        if identifier in path:
            raise ForecastConfigurationError("Circular load priority: select only higher-priority devices")
        record = registry.async_get(plans.get(identifier, {}).get("cost_entity", ""))
        if record and record.platform == "ha_bems":
            cost_id = record.unique_id.removesuffix("_thermal_runtime_cost")
            for ref in costs.get(cost_id, {}).get(CONF_ADDITIONAL_LOADS, []):
                if ref.startswith("planner:"):
                    visit(ref.partition(":")[2], path | {identifier})

    for ref in config.get(CONF_ADDITIONAL_LOADS, []):
        if ref.startswith("planner:"):
            visit(ref.partition(":")[2], set())


def read_additional_loads(hass, config, now):
    validate_load_hierarchy(hass, config)
    forecasts, details = [], []
    for reference in config.get(CONF_ADDITIONAL_LOADS, []):
        if not reference.startswith("planner:"):
            rows, info = read_load_input(hass, {CONF_LOAD_INPUT: reference}, now)
            forecasts.append(rows)
            details.append({"reference": reference, "name": info["load_input_name"],
                            "source_updated_at": info["load_source_updated_at"]})
            continue
        identifier = reference.partition(":")[2]
        entry = next((entry for entry in input_entries(hass, MODULE_THERMAL_PLAN)
                      if entry.entry_id == identifier), None)
        coordinator = planners(hass).get(identifier)
        if entry is None or not item_enabled(entry) or coordinator is None:
            raise ForecastConfigurationError("The selected higher-priority plan was removed, disabled or is not loaded")
        if not snapshot_usable(coordinator, now):
            raise ForecastUnavailable("The selected higher-priority plan is unavailable")
        plan = coordinator.data
        operations = validate_forecast(plan.get("forecast"), now, "operation", True)
        cost_entity = ({**entry.data, **entry.options}).get("cost_entity")
        state = hass.states.get(cost_entity)
        if state is None or state.state in ("unknown", "unavailable"):
            raise ForecastUnavailable("Electrical power forecast for the higher-priority plan is unavailable")
        if state.attributes.get("unit_of_measurement") != "EUR/timestep" or type(state.attributes.get("schema_version")) is not int or state.attributes["schema_version"] != 1:
            raise ValueError("Higher-priority runtime cost requires EUR/timestep and schema_version 1")
        source_age(state.attributes.get("source_updated_at"), now)
        powers = validate_forecast(state.attributes.get("forecast"), now, "electrical_power_w", True)
        indexed = {row["start"]: row for row in powers}
        rows = []
        for operation in operations:
            power = indexed.get(operation["start"])
            if power is None or power["end"] != operation["end"]:
                raise ValueError("Higher-priority operation and electrical power forecasts must match")
            if operation["operation"] not in (0, 1):
                raise ValueError("Higher-priority plan operation must be 0 or 1")
            value = number(power["electrical_power_w"], "Higher-priority electrical power", nonnegative=True)
            rows.append({"start": operation["start"], "end": operation["end"],
                         "power_w": value * operation["operation"],
                         **({FILL_MARKER: True} if operation.get(FILL_MARKER) or power.get(FILL_MARKER) else {})})
        forecasts.append(rows)
        details.append({"reference": reference, "name": entry.title,
                        "source_updated_at": min(plan["source_updated_at"],
                            state.attributes["source_updated_at"], key=parse_timestamp)})
    return forecasts, details
