"""Shared input registrations and immutable-by-convention validated snapshots."""

from datetime import UTC, timedelta

from .const import (
    CONF_LOAD_INPUT, CONF_MODULE, CONF_PV_IMPORT, DOMAIN, MODULE_LOAD,
    MODULE_LOAD_IMPORT, MODULE_PV_IMPORT, CONF_PV_POWER_TYPE, FORECAST_IMPORT_MODULES,
)
from .forecast import MIN_HORIZON, parse_timestamp
from .forecast_input import ForecastUnavailable, source_age
from .entries import all_module_entries, item_enabled, module_type


def pv_imports(hass):
    """List configured PV inputs, including temporarily invalid inputs."""
    return input_entries(hass, MODULE_PV_IMPORT)


def input_entries(hass, module):
    return [entry for entry in all_module_entries(hass) if module_type(entry) == module]


def load_inputs(hass):
    """Use stable entry references, independent of generated sensor entity IDs."""
    return [(f"generated:{entry.entry_id}", f"{entry.title} (HA-BEMS load profile)")
            for entry in input_entries(hass, MODULE_LOAD)] + [
        (f"imported:{entry.entry_id}", f"{entry.title} (imported load forecast)")
        for entry in input_entries(hass, MODULE_LOAD_IMPORT)]


def import_signal(entry_id):
    return f"{DOMAIN}_input_changed_{entry_id}"


def coordinators(hass):
    return hass.data.setdefault(DOMAIN, {}).setdefault("imports", {})


def planners(hass):
    return hass.data.setdefault(DOMAIN, {}).setdefault("planners", {})


def read_pv_import(hass, config, now):
    """Read one shared snapshot without triggering a refresh/notification loop.

    Recheck time-dependent coverage at consumption time; a successful
    validation in the past must never imply a valid current operating plan.
    """
    entry_id = config.get(CONF_PV_IMPORT)
    entry = next((entry for entry in pv_imports(hass) if entry.entry_id == entry_id), None)
    if entry is None:
        raise ForecastUnavailable("Register a PV forecast under BEMS forecast importer, then select it in the module's Configure settings")
    if not item_enabled(entry):
        raise ForecastUnavailable("The selected PV input has been disabled")
    coordinator = coordinators(hass).get(entry_id)
    if coordinator is None:
        raise ForecastUnavailable("The selected PV input importer is not loaded or has been disabled")
    if not coordinator.last_update_success or not coordinator.data:
        raise ForecastUnavailable(f"PV input is {coordinator.input_status}: {coordinator.last_exception}")
    rows, details = _read_snapshot(coordinator.data, now, "PV")
    details.update({"pv_import_entry_id": entry_id, "pv_import_name": entry.title,
                    CONF_PV_POWER_TYPE: entry.options.get(CONF_PV_POWER_TYPE, entry.data.get(CONF_PV_POWER_TYPE, "unspecified"))})
    return rows, details


def read_forecast_import(hass, entry_id, module, now):
    """Read an explicitly typed shared forecast for future planning modules."""
    if module not in FORECAST_IMPORT_MODULES:
        raise ValueError("Unsupported forecast import type")
    entry = next((entry for entry in input_entries(hass, module) if entry.entry_id == entry_id), None)
    if entry is None:
        raise ForecastUnavailable("The selected forecast input no longer exists or has a different type")
    if not item_enabled(entry):
        raise ForecastUnavailable("The selected forecast input has been disabled")
    coordinator = coordinators(hass).get(entry_id)
    if coordinator is None:
        raise ForecastUnavailable("The selected forecast importer is not loaded or has been disabled")
    if not coordinator.last_update_success or not coordinator.data:
        raise ForecastUnavailable(f"Forecast input is {coordinator.input_status}: {coordinator.last_exception}")
    return _read_snapshot(coordinator.data, now, entry.title)


def read_load_input(hass, config, now):
    """Read an explicitly selected load; never fall back to PV-only on failure."""
    reference = config.get(CONF_LOAD_INPUT)
    if not reference:
        return None, {"load_consideration_active": False, "load_input_name": None}
    kind, separator, entry_id = reference.partition(":")
    module = {"generated": MODULE_LOAD, "imported": MODULE_LOAD_IMPORT}.get(kind)
    if not separator or module is None:
        raise ForecastUnavailable("Select a configured HA-BEMS load profile or imported building load forecast")
    entry = next((entry for entry in input_entries(hass, module) if entry.entry_id == entry_id), None)
    if entry is None:
        raise ForecastUnavailable("The selected building load input no longer exists; select another or explicitly choose PV only")
    if not item_enabled(entry):
        raise ForecastUnavailable("The selected building load input has been disabled")
    registry = planners(hass) if kind == "generated" else coordinators(hass)
    coordinator = registry.get(entry_id)
    if coordinator is None:
        raise ForecastUnavailable("The selected building load input is not loaded or has been disabled")
    if not coordinator.last_update_success or not coordinator.data:
        raise ForecastUnavailable(f"Building load input is unavailable: {coordinator.last_exception}")
    rows, details = _read_snapshot(coordinator.data, now, "Building load")
    return rows, {
        "load_consideration_active": True, "load_input_name": entry.title,
        "load_forecast_input": reference,
        **{f"load_{key}": value for key, value in details.items()},
    }


def _read_snapshot(snapshot, now, label):
    """Provider snapshots are structurally validated; recheck rolling coverage."""
    age, updated = source_age(snapshot["source_updated_at"], now)
    current = now.astimezone(UTC).replace(second=0, microsecond=0)
    current -= timedelta(minutes=current.minute % 15)
    rows = [row for row in snapshot["forecast"] if parse_timestamp(row["start"]) >= current]
    if not rows or parse_timestamp(rows[0]["start"]) != current:
        raise ValueError(f"{label} input does not include the current 15-minute interval")
    if parse_timestamp(rows[-1]["end"]) < now.astimezone(UTC) + MIN_HORIZON:
        raise ValueError(f"{label} input no longer covers the next 72 hours; update the source helper")
    details = {key: value for key, value in snapshot.items() if key not in ("forecast", "value", "status")}
    details.update({"source_age_minutes": round(age, 1), "source_updated_at": updated,
                    "input_forecast_start": rows[0]["start"],
                    "input_forecast_end": rows[-1]["end"], "input_forecast_intervals": len(rows)})
    return rows, details
