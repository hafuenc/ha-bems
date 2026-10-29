"""Verify source subscriptions and consumer notifications across entry lifecycle."""

import asyncio
from datetime import timedelta
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from tests.ha_stubs import modules
from tests.test_importer import Hass, entry

from custom_components.ha_bems import async_setup_entry, async_unload_entry
with patch.dict('sys.modules', modules()):
    from custom_components.ha_bems.import_registry import coordinators, import_signal, planners, read_load_input, read_pv_import, read_forecast_import
from custom_components.ha_bems.forecast_types import forecast_type
from tests.test_forecast_input import NOW


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_change_unload_and_reload_notify_same_registration(self):
        await self.check_import_lifecycle('pv_forecast_import')

    async def test_load_import_change_unload_and_reload_notify_same_registration(self):
        await self.check_import_lifecycle('load_forecast_import')

    async def test_price_and_temperature_import_lifecycle(self):
        for module in ('electricity_import_price_forecast', 'electricity_export_price_forecast',
                       'outdoor_temperature_forecast'):
            with self.subTest(module=module):
                await self.check_import_lifecycle(module)

    async def check_import_lifecycle(self, module):
        hass = Hass()
        _label, unit, value_key, _nonnegative = forecast_type({'module': module})
        hass.state.attributes['unit_of_measurement'] = unit
        def source_rows(now):
            from tests.test_forecast import records_for
            return [{'start': row['start'], 'end': row['end'], value_key: row['power_w']}
                    for row in records_for(now)]
        hass.state.attributes['forecast'] = source_rows(NOW)
        registered = entry('pv-main', module=module, forecast_entity='sensor.pv')
        hass.entries = [registered]
        def read_import():
            if module == 'load_forecast_import':
                return read_load_input(hass, {'load_forecast_input': 'imported:pv-main'}, NOW)
            return read_forecast_import(hass, 'pv-main', module, NOW)
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
            count = len(notifications)
            validated = original.data['input_validated_at']
            injected['homeassistant.util'].dt.now = lambda: NOW + timedelta(seconds=30)
            await original.async_refresh()
            self.assertNotEqual(original.data['input_validated_at'], validated)
            self.assertEqual(len(notifications), count, 'Validation-only update triggered a consumer')
            # A newly issued source timestamp alone is informational too.
            hass.state.attributes['source_updated_at'] = NOW.isoformat()
            await original.async_refresh()
            self.assertEqual(len(notifications), count)
            # A changed power value must still notify immediately.
            hass.state.attributes['forecast'][0][value_key] = 123
            await original.async_refresh()
            self.assertEqual(len(notifications), count + 1)
            count = len(notifications)
            # Rolling to the next quarter changes the coverage window.
            injected['homeassistant.util'].dt.now = lambda: NOW + timedelta(minutes=15)
            hass.state.attributes['forecast'] = source_rows(NOW + timedelta(minutes=15))
            await original.async_refresh()
            self.assertEqual(len(notifications), count + 1)
            hass.state.state = 'unavailable'
            watches[0][1](None)
            await asyncio.gather(*tasks)
            self.assertFalse(original.last_update_success)
            self.assertEqual(original.input_status, 'unavailable')
            count = len(notifications)
            await original.async_refresh()
            self.assertEqual(len(notifications), count, 'Unchanged failure retriggered a consumer')
            hass.state.state = '0'
            await original.async_refresh()
            self.assertEqual(len(notifications), count + 1, 'Recovery did not notify consumers')
            hass.state.state = 'unavailable'
            await original.async_refresh()
            with self.assertRaises(ValueError):
                read_import()
            self.assertTrue(await async_unload_entry(hass, registered))
            self.assertNotIn('pv-main', coordinators(hass))
            for callback in registered.callbacks:
                callback()
            registered.callbacks.clear()
            self.assertEqual(watches, [])
            injected['homeassistant.util'].dt.now = lambda: NOW
            hass.state.attributes['forecast'] = source_rows(NOW)
            hass.state.state = '0'
            await async_setup_entry(hass, registered)
            self.assertIsNot(coordinators(hass)['pv-main'], original)
            self.assertEqual(len(read_import()[0]), 289)
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

    async def test_fbc_restores_switch_before_sensor_and_unloads_both_platforms(self):
        hass = Hass()
        hass.config = SimpleNamespace(time_zone='Europe/Vienna')
        registered = entry('battery', module='battery_fbc', max_charge_power_w=5000)
        registered.callbacks = []
        registered.async_on_unload = registered.callbacks.append
        registered.add_update_listener = lambda fn: (lambda: None)
        calls = []
        async def forward(_entry, platforms):
            calls.append(platforms)
            plan = planners(hass)['battery']
            if platforms == ['switch']:
                # The switch platform restores the saved off choice.
                plan.limitation_enabled = False
            else:
                self.assertIs(plan.limitation_enabled, False)
                await plan.async_refresh()
        async def unload(_entry, platforms):
            self.assertEqual(set(platforms), {'sensor', 'switch'})
            return True
        hass.config_entries.async_forward_entry_setups = forward
        hass.config_entries.async_unload_platforms = unload
        injected = modules()
        const = ModuleType('homeassistant.const')
        const.Platform = SimpleNamespace(SENSOR='sensor', SWITCH='switch')
        dispatcher = ModuleType('homeassistant.helpers.dispatcher')
        dispatcher.async_dispatcher_send = lambda *args: None
        events = ModuleType('homeassistant.helpers.event')
        events.async_track_state_change_event = lambda *args: (lambda: None)
        injected.update({'homeassistant.const': const,
                         'homeassistant.helpers.dispatcher': dispatcher,
                         'homeassistant.helpers.event': events})
        with patch.dict('sys.modules', injected):
            await async_setup_entry(hass, registered)
            self.assertEqual(calls, [['switch'], ['sensor']])
            self.assertEqual(planners(hass)['battery'].data['override_reasons'], ['disabled_by_switch'])
            self.assertTrue(await async_unload_entry(hass, registered))
            self.assertNotIn('battery', planners(hass))

    async def forward(self, *args):
        pass

    async def unload(self, *args):
        return True
