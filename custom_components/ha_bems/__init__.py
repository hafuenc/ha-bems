"""HA-BEMS: reusable entities and operating plans for user automations."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


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
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from homeassistant.helpers.event import async_track_state_change_event

    from .const import CONF_MODULE, MODULE_FBC, MODULE_LOAD, MODULE_LOAD_IMPORT, MODULE_PV_IMPORT, MODULE_SOC
    from .import_registry import coordinators, import_signal, planners

    config = {**entry.data, **entry.options}
    module = config.get(CONF_MODULE, MODULE_SOC)
    registry = None
    if module in (MODULE_PV_IMPORT, MODULE_LOAD_IMPORT):
        from .input_coordinator import ForecastImportCoordinator

        coordinator = ForecastImportCoordinator(hass, entry)
        registry = coordinators(hass)
    elif module in (MODULE_LOAD, MODULE_FBC):
        from .coordinator import BEMSCoordinator

        coordinator = BEMSCoordinator(hass, entry, module)
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

        entry.async_on_unload(cleanup)
        if module != MODULE_FBC:
            entry.async_on_unload(coordinator.async_add_listener(notify))
        if module in (MODULE_PV_IMPORT, MODULE_LOAD_IMPORT):
            entry.async_on_unload(async_track_state_change_event(
                hass, [config["forecast_entity"]], refresh,
            ))
            # Invalid sources still get a visible status and editable options.
            await coordinator.async_refresh()

    if module == MODULE_FBC:
        # Restore the user's bypass choice before publishing the first plan.
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.SWITCH])
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from homeassistant.const import Platform
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from .const import CONF_MODULE, MODULE_FBC
    from .import_registry import coordinators, import_signal, planners

    platforms = [Platform.SENSOR]
    if entry.options.get(CONF_MODULE, entry.data.get(CONF_MODULE)) == MODULE_FBC:
        platforms.append(Platform.SWITCH)
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        removed = coordinators(hass).pop(entry.entry_id, None)
        removed_plan = planners(hass).pop(entry.entry_id, None)
        # Consumers resolve the registration again, so disable/delete/reload
        # cannot leave them attached to an old coordinator or stale snapshot.
        if removed is not None or removed_plan is not None:
            async_dispatcher_send(hass, import_signal(entry.entry_id))
    return unloaded
