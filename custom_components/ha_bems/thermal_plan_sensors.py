"""Storage percentage, future transitions and compact planner diagnostics."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .forecast import parse_timestamp
from .thermal_plan_coordinator import projected_state
from .thermal_plan_entity import ThermalPlanEntity


def thermal_plan_sensors(coordinator, entry):
    return [ThermalStorageLevelSensor(coordinator, entry),
            ThermalTransitionSensor(coordinator, entry, 1),
            ThermalTransitionSensor(coordinator, entry, 0),
            ThermalPlannerStatusSensor(coordinator, entry),
            ThermalSolverSensor(coordinator, entry, "solver_seconds"),
            ThermalSolverSensor(coordinator, entry, "solver_gap")]


class ThermalStorageLevelSensor(ThermalPlanEntity, SensorEntity):
    _attr_name = "Storage level"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = "measurement"
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:storage-tank"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_storage_level"

    def percentage(self, value):
        data = self.coordinator.data
        low, high = data["storage_min_h"], data["storage_max_h"]
        return max(0, min(100, 100 * (value - low) / (high - low)))

    @property
    def native_value(self):
        if not self.available:
            return None
        return self.percentage(projected_state(self.coordinator.data, dt_util.now())[0])

    @property
    def extra_state_attributes(self):
        attributes = self.plan_attributes
        attributes.update({"forecast_value_key": "storage_level_percent", "forecast_unit": "%",
                           "percentage_basis": "configured_minimum_to_maximum"})
        if "forecast" in attributes:
            attributes["forecast"] = [{"start": row["start"], "end": row["end"],
                "effective_start": row["effective_start"],
                "storage_level_percent": self.percentage(row["storage_start_h"]),
                "storage_level_end_percent": self.percentage(row["storage_end_h"])}
                for row in attributes["forecast"]]
        return attributes


class ThermalTransitionSensor(ThermalPlanEntity, SensorEntity):
    _attr_device_class = "timestamp"

    def __init__(self, coordinator, entry, operation):
        super().__init__(coordinator, entry)
        self.operation = operation
        suffix = "next_start" if operation else "next_stop"
        self._attr_name = "Next start" if operation else "Next stop"
        self._attr_icon = "mdi:play-circle-outline" if operation else "mdi:stop-circle-outline"
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"

    @property
    def native_value(self):
        if not self.available:
            return None
        now = dt_util.now()
        previous = self.current_row["operation"]
        for row in self.coordinator.data["forecast"]:
            start = parse_timestamp(row["effective_start"])
            if start <= now:
                continue
            if row["operation"] != previous and row["operation"] == self.operation:
                return start
            previous = row["operation"]
        return None

    @property
    def extra_state_attributes(self):
        return {"generated_at": (self.coordinator.data or {}).get("generated_at"),
                "status": "scheduled" if self.native_value else
                          "no_transition_in_forecast" if self.available else "unavailable"}


class ThermalPlannerStatusSensor(ThermalPlanEntity, SensorEntity):
    _attr_name = "Planner status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = "enum"
    _attr_options = ["optimal", "feasible_time_limit", "waiting_for_inputs", "infeasible", "error", "expired"]
    _attr_icon = "mdi:list-status"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_planner_status"

    @property
    def available(self):
        # Failure must remain readable rather than hiding this diagnostic.
        return True

    @property
    def native_value(self):
        if self.coordinator.last_update_success and self.coordinator.data and self.current_row is None:
            return "expired"
        return self.coordinator.plan_status

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        valid = self.coordinator.last_update_success and self.current_row is not None
        return {"plan_available": valid,
                "reason": str(self.coordinator.last_exception) if self.coordinator.last_exception else
                          "Forecast no longer covers the current time" if self.native_value == "expired" else None,
                "last_successful_plan_at": data.get("generated_at"),
                "calculation_interval_minutes": 15, "calculation_schedule": "quarter_hour_clock",
                "solver_time_limit_s": self.coordinator.config.get("solver_time_limit_s", 20),
                "solver_relative_gap_limit": .01}


class ThermalSolverSensor(ThermalPlanEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator, entry, key):
        super().__init__(coordinator, entry)
        self.key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = "Solver time" if key == "solver_seconds" else "Solver optimality gap"
        self._attr_native_unit_of_measurement = "s" if key == "solver_seconds" else "%"
        self._attr_icon = "mdi:timer-outline" if key == "solver_seconds" else "mdi:percent"

    @property
    def native_value(self):
        value = (self.coordinator.data or {}).get(self.key) if self.available else None
        return value * 100 if value is not None and self.key == "solver_gap" else value
