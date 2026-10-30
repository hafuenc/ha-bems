"""Shared availability and output metadata for thermal planner entities."""

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .forecast import parse_timestamp


class ThermalPlanEntity(CoordinatorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"forecast"})

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title,
            "manufacturer": "HA-BEMS", "model": "Thermal storage management — MILP"}

    @property
    def current_row(self):
        now = dt_util.now()
        return next((row for row in (self.coordinator.data or {}).get("forecast", [])
                     if parse_timestamp(row["start"]) <= now < parse_timestamp(row["end"])), None)

    @property
    def available(self):
        return self.coordinator.last_update_success and self.current_row is not None

    @property
    def plan_attributes(self):
        attributes = {key: value for key, value in (self.coordinator.data or {}).items()
                      if key not in ("value", "initial_previous_operation", "initial_previous_elapsed_h")}
        attributes["status"] = attributes.get("solver_status", "unavailable") if self.available else "unavailable"
        if not self.available:
            attributes.pop("forecast", None)
            attributes["reason"] = str(self.coordinator.last_exception or "No current plan")
        return attributes
