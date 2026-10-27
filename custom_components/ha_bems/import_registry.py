"""Shared input registrations and immutable-by-convention validated snapshots."""

from datetime import UTC, timedelta

from .const import CONF_MODULE, CONF_PV_IMPORT, DOMAIN, MODULE_PV_IMPORT
from .forecast import MIN_HORIZON, parse_timestamp
from .forecast_input import ForecastUnavailable, source_age


def pv_imports(hass):
    """List configured PV inputs, including temporarily invalid inputs."""
    return [entry for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.options.get(CONF_MODULE, entry.data.get(CONF_MODULE)) == MODULE_PV_IMPORT]


def import_signal(entry_id):
    return f"{DOMAIN}_input_changed_{entry_id}"


def coordinators(hass):
    return hass.data.setdefault(DOMAIN, {}).setdefault("imports", {})


def read_pv_import(hass, config, now):
    """Read one shared snapshot without triggering a refresh/notification loop.

    Recheck time-dependent coverage at consumption time; a successful
    validation in the past must never imply a valid current operating plan.
    """
    entry_id = config.get(CONF_PV_IMPORT)
    entry = next((entry for entry in pv_imports(hass) if entry.entry_id == entry_id), None)
    if entry is None:
        raise ForecastUnavailable("Register a PV forecast under BEMS entity importer, then select it in FBC Configure")
    coordinator = coordinators(hass).get(entry_id)
    if coordinator is None:
        raise ForecastUnavailable("The selected PV input importer is not loaded or has been disabled")
    if not coordinator.last_update_success or not coordinator.data:
        raise ForecastUnavailable(f"PV input is {coordinator.input_status}: {coordinator.last_exception}")
    snapshot = coordinator.data
    age, updated = source_age(snapshot["source_updated_at"], now)
    current = now.astimezone(UTC).replace(second=0, microsecond=0)
    current -= timedelta(minutes=current.minute % 15)
    rows = [row for row in snapshot["forecast"] if parse_timestamp(row["start"]) >= current]
    if not rows or parse_timestamp(rows[0]["start"]) != current:
        raise ValueError("PV input does not include the current 15-minute interval")
    if parse_timestamp(rows[-1]["end"]) < now.astimezone(UTC) + MIN_HORIZON:
        raise ValueError("PV input no longer covers the next 72 hours; update the source helper")
    details = {key: value for key, value in snapshot.items() if key != "forecast"}
    details.update({"pv_import_entry_id": entry_id, "pv_import_name": entry.title,
                    "source_age_minutes": round(age, 1), "source_updated_at": updated,
                    "input_forecast_start": rows[0]["start"],
                    "input_forecast_end": rows[-1]["end"], "input_forecast_intervals": len(rows)})
    return rows, details
