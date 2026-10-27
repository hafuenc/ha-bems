"""HA-BEMS: reusable entities and operating plans for user automations."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from homeassistant.const import Platform
    from homeassistant.core import callback
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from homeassistant.helpers.event import async_track_state_change_event

    from .const import CONF_MODULE, MODULE_PV_IMPORT
    from .import_registry import coordinators, import_signal

    config = {**entry.data, **entry.options}
    if config.get(CONF_MODULE) == MODULE_PV_IMPORT:
        from .input_coordinator import PVImportCoordinator

        coordinator = PVImportCoordinator(hass, entry)
        coordinators(hass)[entry.entry_id] = coordinator

        @callback
        def notify():
            async_dispatcher_send(hass, import_signal(entry.entry_id))

        @callback
        def cleanup():
            if coordinators(hass).get(entry.entry_id) is coordinator:
                coordinators(hass).pop(entry.entry_id)
                notify()

        @callback
        def refresh(_event):
            hass.async_create_task(coordinator.async_request_refresh())

        entry.async_on_unload(cleanup)
        entry.async_on_unload(coordinator.async_add_listener(notify))
        entry.async_on_unload(async_track_state_change_event(
            hass, [config["forecast_entity"]], refresh,
        ))
        # Invalid sources still get a visible status and editable options.
        await coordinator.async_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from homeassistant.const import Platform
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from .import_registry import coordinators, import_signal

    unloaded = await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])
    if unloaded and coordinators(hass).pop(entry.entry_id, None) is not None:
        # Consumers resolve the registration again, so disable/delete/reload
        # cannot leave them attached to an old coordinator or stale snapshot.
        async_dispatcher_send(hass, import_signal(entry.entry_id))
    return unloaded
