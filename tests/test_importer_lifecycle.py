"""Verify source subscriptions and consumer notifications across entry lifecycle."""

import asyncio
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from tests.ha_stubs import modules
from tests.test_importer import Hass, entry

from custom_components.ha_bems import async_setup_entry, async_unload_entry
with patch.dict('sys.modules', modules()):
    from custom_components.ha_bems.import_registry import coordinators, import_signal, read_pv_import
from tests.test_forecast_input import NOW


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_change_unload_and_reload_notify_same_registration(self):
        hass = Hass()
        registered = entry('pv-main', forecast_entity='sensor.pv')
        registered.callbacks = []
        registered.async_on_unload = registered.callbacks.append
        registered.add_update_listener = lambda fn: (lambda: None)
        hass.config_entries.async_forward_entry_setups = self.forward
        hass.config_entries.async_unload_platforms = self.unload
        tasks = []
        hass.async_create_task = lambda coro: tasks.append(asyncio.create_task(coro))
        notifications, watches = [], []
        injected = modules()
        const = ModuleType('homeassistant.const')
        const.Platform = SimpleNamespace(SENSOR='sensor')
        dispatcher = ModuleType('homeassistant.helpers.dispatcher')
        dispatcher.async_dispatcher_send = lambda _hass, signal: notifications.append(signal)
        events = ModuleType('homeassistant.helpers.event')

        def track(_hass, entities, callback):
            watch = (entities, callback)
            watches.append(watch)
            return lambda: watches.remove(watch)

        events.async_track_state_change_event = track
        injected.update({'homeassistant.const': const,
                         'homeassistant.helpers.dispatcher': dispatcher,
                         'homeassistant.helpers.event': events})
        with patch.dict('sys.modules', injected):
            self.assertTrue(await async_setup_entry(hass, registered))
            original = coordinators(hass)['pv-main']
            self.assertEqual(watches[0][0], ['sensor.pv'])
            self.assertEqual(notifications[-1], import_signal('pv-main'))
            hass.state.state = 'unavailable'
            watches[0][1](None)
            await asyncio.gather(*tasks)
            self.assertFalse(original.last_update_success)
            self.assertEqual(original.input_status, 'unavailable')
            with self.assertRaises(ValueError):
                read_pv_import(hass, {'pv_import_entry_id': 'pv-main'}, NOW)
            self.assertTrue(await async_unload_entry(hass, registered))
            self.assertNotIn('pv-main', coordinators(hass))
            for callback in registered.callbacks:
                callback()
            registered.callbacks.clear()
            self.assertEqual(watches, [])
            hass.state.state = '0'
            await async_setup_entry(hass, registered)
            self.assertIsNot(coordinators(hass)['pv-main'], original)
            self.assertEqual(len(read_pv_import(hass, {'pv_import_entry_id': 'pv-main'}, NOW)[0]), 289)
            self.assertGreaterEqual(notifications.count(import_signal('pv-main')), 4)

    async def test_failed_platform_setup_cleanup_removes_registration(self):
        hass = Hass()
        registered = entry('pv-main', forecast_entity='sensor.pv')
        registered.callbacks = []
        registered.async_on_unload = registered.callbacks.append
        async def broken_forward(*args):
            raise RuntimeError('platform failed')
        hass.config_entries.async_forward_entry_setups = broken_forward
        injected = modules()
        const = ModuleType('homeassistant.const'); const.Platform = SimpleNamespace(SENSOR='sensor')
        dispatcher = ModuleType('homeassistant.helpers.dispatcher')
        dispatcher.async_dispatcher_send = lambda *args: None
        events = ModuleType('homeassistant.helpers.event')
        events.async_track_state_change_event = lambda *args: (lambda: None)
        injected.update({'homeassistant.const': const, 'homeassistant.helpers.dispatcher': dispatcher,
                         'homeassistant.helpers.event': events})
        with patch.dict('sys.modules', injected):
            with self.assertRaises(RuntimeError):
                await async_setup_entry(hass, registered)
            # HA invokes registered unload callbacks on failed setup.
            for callback in registered.callbacks:
                callback()
            self.assertNotIn('pv-main', coordinators(hass))

    async def forward(self, *args):
        pass

    async def unload(self, *args):
        return True
