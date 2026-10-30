"""HA-BEMS: reusable entities and operating plans for user automations."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def _config_schema(config):
    """Declare UI-only setup while keeping pure calculation imports independent."""
    from homeassistant.helpers import config_validation as cv

    from .const import DOMAIN

    return cv.config_entry_only_config_schema(DOMAIN)(config)


CONFIG_SCHEMA = _config_schema


async def async_setup(hass, config):
    """Consolidate old standalone entries before any module platforms load."""
    from .migration import async_flatten_entries
    from .constraint_migration import migrate_constraint_settings

    migrate_constraint_settings(hass)
    await async_flatten_entries(hass)
    return True


async def async_migrate_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Use configured maximum charging power as the single output limit."""
    from .const import CONF_MODULE, MODULE_FBC

    if entry.version != 1:
        return False
    if entry.minor_version < 2:
        values = {**entry.data, **entry.options}
        if values.get(CONF_MODULE) == MODULE_FBC:
            values.pop("output_limit_w", None)
            hass.config_entries.async_update_entry(entry, data=values, options={}, minor_version=2)
        else:
            hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from homeassistant.const import Platform

    from .const import DOMAIN, MODULE_FBC, MODULE_THERMAL_PLAN
    from .entries import enabled_module_entries, module_type

    if entry.data.get("migrated_to") or "entry_split_pending" in entry.data or entry.data.get("entry_split_staging"):
        return True
    from .devices import ensure_service_devices
    from .entries import is_flat, is_split

    if is_flat(entry) and not is_split(entry):
        ensure_service_devices(hass, entry)
    items = enabled_module_entries(entry)
    for item in items:
        await _async_setup_module(hass, item, entry)
    platforms = [Platform.SENSOR]
    if any(module_type(item) == MODULE_THERMAL_PLAN for item in items):
        platforms.append(Platform.BINARY_SENSOR)
    sensor_platforms = list(platforms)
    if any(module_type(item) == MODULE_FBC for item in items):
        # Restore every battery's bypass switch before publishing its first plan.
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.SWITCH])
        platforms.append(Platform.SWITCH)
    await hass.config_entries.async_forward_entry_setups(entry, sensor_platforms)
    hass.data.setdefault(DOMAIN, {}).setdefault("platforms", {})[entry.entry_id] = platforms
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_setup_module(hass, entry, parent):
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from homeassistant.helpers.event import async_track_state_change_event

    from .const import CONF_MODULE, MODULE_FBC, MODULE_LOAD, MODULE_SOC, MODULE_THERMAL_COST, MODULE_THERMAL_PLAN, FORECAST_IMPORT_MODULES
    from .import_registry import coordinators, import_signal, planners

    config = {**entry.data, **entry.options}
    module = config.get(CONF_MODULE, MODULE_SOC)
    registry = None
    if module in FORECAST_IMPORT_MODULES:
        from .input_coordinator import ForecastImportCoordinator

        coordinator = ForecastImportCoordinator(hass, entry, parent)
        registry = coordinators(hass)
    elif module in (MODULE_LOAD, MODULE_FBC):
        from .coordinator import BEMSCoordinator

        coordinator = BEMSCoordinator(hass, entry, module)
        registry = planners(hass)
    elif module == MODULE_THERMAL_COST:
        from .thermal_coordinator import ThermalCostCoordinator

        coordinator = ThermalCostCoordinator(hass, entry, parent)
        registry = planners(hass)
    elif module == MODULE_THERMAL_PLAN:
        from .thermal_plan_coordinator import ThermalPlanCoordinator

        coordinator = ThermalPlanCoordinator(hass, entry, parent)
        registry = planners(hass)
    if registry is not None:
        registry[entry.entry_id] = coordinator
        previous_input = None

        @callback
        def notify():
            nonlocal previous_input
            # The status sensor still receives every validation update. Device
            # planners only need a new forecast or changed input health, not
            # a new validation timestamp or informational source age.
            if coordinator.last_update_success and coordinator.data:
                current_input = ("ready", coordinator.data["forecast"])
            else:
                current_input = (getattr(coordinator, "input_status", "unavailable"), str(coordinator.last_exception))
            if current_input != previous_input:
                previous_input = current_input
                async_dispatcher_send(hass, import_signal(entry.entry_id))

        @callback
        def cleanup():
            if registry.get(entry.entry_id) is coordinator:
                registry.pop(entry.entry_id)
                # Removal must always invalidate consumers, even when the
                # last successfully validated forecast has not changed.
                async_dispatcher_send(hass, import_signal(entry.entry_id))

        @callback
        def refresh(_event):
            hass.async_create_task(coordinator.async_request_refresh())

        if module == MODULE_THERMAL_PLAN:
            from homeassistant.helpers.dispatcher import async_dispatcher_connect
            from homeassistant.helpers.event import async_track_time_change

            @callback
            def input_ready(_event=None):
                if coordinator.awaiting_inputs:
                    refresh(None)

            # The planner owns its clock, independent of whether either
            # output entity is enabled in the entity registry.
            entry.async_on_unload(async_track_time_change(
                hass, refresh, minute=[0, 15, 30, 45], second=0))
            entry.async_on_unload(async_track_state_change_event(
                hass, [config["cost_entity"]], input_ready))
            from .operation_constraints import constraint_selection

            source, _ = constraint_selection(hass, config)
            references = [config.get("runtime_temperature_entry_id")]
            if source:
                references.append(source.split(":", 1)[1])
            for reference in references:
                if reference:
                    entry.async_on_unload(async_dispatcher_connect(
                        hass, import_signal(reference), input_ready))

        entry.async_on_unload(cleanup)
        if module != MODULE_FBC:
            entry.async_on_unload(coordinator.async_add_listener(notify))
        if module in FORECAST_IMPORT_MODULES:
            entry.async_on_unload(async_track_state_change_event(
                hass, [config["forecast_entity"]], refresh,
            ))
            # Invalid sources still get a visible status and editable options.
            await coordinator.async_refresh()


async def _async_options_updated(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    if "entry_split_pending" in entry.data or entry.data.get("entry_split_staging"):
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from homeassistant.const import Platform
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from .const import DOMAIN
    from .entries import module_entries
    from .import_registry import coordinators, import_signal, planners

    if entry.data.get("migrated_to"):
        return True
    items = module_entries(entry)
    loaded_platforms = hass.data.setdefault(DOMAIN, {}).setdefault("platforms", {})
    platforms = loaded_platforms.get(entry.entry_id, [Platform.SENSOR])
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        loaded_platforms.pop(entry.entry_id, None)
        for item in items:
            removed = coordinators(hass).pop(item.entry_id, None)
            removed_plan = planners(hass).pop(item.entry_id, None)
            if removed is not None or removed_plan is not None:
                async_dispatcher_send(hass, import_signal(item.entry_id))
    return unloaded
