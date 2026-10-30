"""Multiple thermal subentries, validated shared inputs, edits and failures."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.ha_stubs import modules, ConfigSubentry
from tests.test_importer import Hass, entry, HABEMSItemFlow
from tests.test_forecast_input import NOW, FakeHass, reader, dt_util
from tests.test_grouped_entries import parent
from tests.test_thermal_cost import CONFIG, rows
from custom_components.ha_bems.const import (
    CONF_PV_IMPORT, CONF_IMPORT_PRICE, CONF_EXPORT_PRICE, CONF_TEMPERATURE,
    MODULE_THERMAL_COST, MODULE_IMPORT_PRICE, MODULE_EXPORT_PRICE, MODULE_OUTDOOR_TEMP,
)
with patch.dict('sys.modules', modules()):
    from custom_components.ha_bems.import_registry import coordinators
    from custom_components.ha_bems.thermal_coordinator import ThermalCostCoordinator, thermal_references


def thermal_hass():
    hass = Hass()
    hass.entries[0].data['pv_power_type'] = 'ac'
    for identifier, module, key, value in (
        ('pv-main', 'pv_forecast_import', 'power_w', 1000),
        ('import', MODULE_IMPORT_PRICE, 'price_eur_per_kwh', .3),
        ('export', MODULE_EXPORT_PRICE, 'price_eur_per_kwh', .1),
        ('temperature', MODULE_OUTDOOR_TEMP, 'temperature_c', 2),
    ):
        if identifier != 'pv-main':
            hass.entries.append(entry(identifier, module=module, forecast_entity='sensor.' + identifier))
        coordinators(hass)[identifier] = SimpleNamespace(last_update_success=True, last_exception=None,
            input_status='ready', data={'forecast': rows(value, key),
            'source_updated_at': (NOW - timedelta(hours=2 if identifier == 'export' else 1)).isoformat()})
    return hass


INPUTS = {CONF_PV_IMPORT: 'pv-main', CONF_IMPORT_PRICE: 'import', CONF_EXPORT_PRICE: 'export',
          'load_forecast_input': '', 'feed_in_limit_enabled': False,
          'feed_in_limit_w': 0, 'curtailed_price_eur_per_kwh': 0}


class ThermalSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        context = patch.dict('sys.modules', modules())
        context.start()
        self.addCleanup(context.stop)
        self.hass = thermal_hass()
        self.group = parent('entity_creator')
        self.hass.entries.append(self.group)

    def flow(self):
        flow = HABEMSItemFlow()
        flow.hass, flow.parent = self.hass, self.group
        return flow

    async def test_two_named_setups_publish_independent_entities_and_readable_contract(self):
        for name, cop in [('DHW', 2), ('UFH', 4)]:
            flow = self.flow()
            menu = await flow.async_step_user()
            self.assertEqual(menu['menu_options'], ['soc', 'load_profile', 'thermal_cost'])
            result = await flow.async_step_thermal_cost({'name': name, **CONFIG, 'constant_cop': cop})
            self.assertEqual(result['step_id'], 'thermal_cop_constant')
            result = await flow.async_step_thermal_cop_constant({'constant_cop': cop})
            self.assertEqual(result['step_id'], 'thermal_cost_inputs')
            result = await flow.async_step_thermal_cost_inputs(INPUTS)
            self.assertEqual(result['step_id'], 'thermal_cost_preview')
            result = await flow.async_step_thermal_cost_preview({})
            self.assertEqual(result['title'], name)
            item = ConfigSubentry(data=result['data'], title=name, subentry_type='entity_creator', unique_id=result['unique_id'])
            self.group.subentries[item.subentry_id] = item
        from custom_components.ha_bems.entries import module_entries
        items = module_entries(self.group)
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[0].entry_id, items[1].entry_id)
        plans = [ThermalCostCoordinator(self.hass, item, self.group) for item in items]
        for plan in plans:
            await plan.async_refresh()
            self.assertTrue(plan.last_update_success, str(plan.last_exception))
            self.assertEqual(plan.data['source_updated_at'], (NOW - timedelta(hours=2)).isoformat())
            source = SimpleNamespace(entity_id='sensor.runtime_cost', state=str(plan.data['value']),
                attributes={**plan.data, 'unit_of_measurement': 'EUR/timestep'})
            with patch.object(dt_util, 'now', lambda: NOW):
                intervals, _ = await reader._async_read_forecast(FakeHass(source),
                    {'forecast_entity': source.entity_id}, 'Runtime cost', 'EUR/timestep', 'cost_eur_per_timestep', False)
            self.assertEqual(len(intervals), 289)
        self.assertNotEqual(plans[0].data['value'], plans[1].data['value'])
        original = deepcopy(items[1].data)
        self.hass.config_entries.async_update_subentry = lambda _parent, subentry, **kw: (
            setattr(subentry, 'title', kw['title']), setattr(subentry, 'data', kw['data']))
        edit = self.flow()
        edit.subentry = items[0].subentry
        identifier = items[0].entry_id
        result = await edit.async_step_reconfigure({'name': 'DHW disabled', 'enabled': False})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        self.assertEqual(items[0].entry_id, identifier)
        self.assertFalse(items[0].enabled)
        self.assertEqual(items[1].data, original)

    async def test_lookup_form_points_are_sorted_and_temporary_fields_not_saved(self):
        flow = self.flow()
        form = await flow.async_step_thermal_cost()
        self.assertNotIn('auxiliary_power_w', form['data_schema'].schema)
        # Simulate editing a setup saved before auxiliary power was retired.
        flow._remember({'auxiliary_power_w': 500})
        await flow.async_step_thermal_cost({'name': 'UFH', **CONFIG, 'cop_model': 'lookup'})
        result = await flow.async_step_thermal_cop_table({CONF_TEMPERATURE: 'temperature', 'cop_point_count': 2, 'fallback_cop': 3})
        self.assertEqual(result['step_id'], 'thermal_cop_points')
        self.assertEqual(len(result['data_schema']({})['cop_table_text'].splitlines()), 2)
        bad = await flow.async_step_thermal_cop_points({'cop_table_text': '2;3\n2;4'})
        self.assertEqual(bad['errors']['base'], 'invalid_cop_points')
        self.assertEqual(bad['data_schema']({})['cop_table_text'], '2;3\n2;4')
        result = await flow.async_step_thermal_cop_points({'cop_table_text': '12;4,61\n-7;2.32'})
        self.assertEqual(result['step_id'], 'thermal_cost_inputs')
        await flow.async_step_thermal_cost_inputs(INPUTS)
        result = await flow.async_step_thermal_cost_preview({})
        self.assertEqual(result['data']['cop_points'][0]['temperature_c'], -7)
        self.assertEqual(result['data']['cop_points'][0]['cop'], 2.32)
        self.assertNotIn('cop_table_text', result['data'])
        self.assertNotIn('temperature_1', result['data'])
        self.assertNotIn('constant_cop', result['data'])
        self.assertNotIn('auxiliary_power_w', result['data'])
        self.assertEqual(result['data'][CONF_TEMPERATURE], 'temperature')
        self.assertIn('temperature', thermal_references(result['data']))
        self.assertNotIn('temperature', thermal_references({**result['data'], 'cop_model': 'constant'}))

    async def test_input_failure_blocks_preview_revalidates_save_and_recovers(self):
        flow = self.flow()
        await flow.async_step_thermal_cost({'name': 'DHW', **CONFIG})
        await flow.async_step_thermal_cop_constant({'constant_cop': 3.5})
        self.hass.entries[0].data['pv_power_type'] = 'dc'
        result = await flow.async_step_thermal_cost_inputs(INPUTS)
        self.assertIn('PV AC', result['description_placeholders']['reason'])
        self.hass.entries[0].data['pv_power_type'] = 'ac'
        await flow.async_step_thermal_cost_inputs(INPUTS)
        coordinators(self.hass)['import'].last_update_success = False
        result = await flow.async_step_thermal_cost_preview({})
        self.assertEqual(result['step_id'], 'thermal_cost_inputs')
        plan = ThermalCostCoordinator(self.hass, entry('DHW', module=MODULE_THERMAL_COST, **CONFIG, **INPUTS))
        await plan.async_refresh()
        self.assertFalse(plan.last_update_success)
        coordinators(self.hass)['import'].last_update_success = True
        await plan.async_refresh()
        self.assertTrue(plan.last_update_success)
        # Disabling an input invalidates its retained successful snapshot.
        self.hass.entries[1].data['enabled'] = False
        await plan.async_refresh()
        self.assertFalse(plan.last_update_success)

    async def test_temperature_failure_only_uses_configured_cop_fallback(self):
        config = {**CONFIG, **INPUTS, 'cop_model': 'lookup', CONF_TEMPERATURE: 'temperature',
                  'fallback_cop': 2.5, 'cop_points': [{'temperature_c': -8, 'cop': 2}, {'temperature_c': 12, 'cop': 4}]}
        plan = ThermalCostCoordinator(self.hass, entry('UFH', module=MODULE_THERMAL_COST, **config))
        await plan.async_refresh()
        self.assertFalse(plan.data['cop_fallback_active'])
        coordinators(self.hass)['temperature'].last_update_success = False
        await plan.async_refresh()
        self.assertTrue(plan.last_update_success)
        self.assertEqual(plan.data['cop_fallback_intervals'], 289)
        self.assertEqual(plan.data['current_cop'], 2.5)
        self.assertTrue(plan.data['temperature_input_error'])
        coordinators(self.hass)['temperature'].last_update_success = True
        await plan.async_refresh()
        self.assertFalse(plan.data['cop_fallback_active'])

    async def test_optional_load_is_subtracted_and_stale_price_horizon_fails(self):
        self.hass.entries.append(entry('load', module='load_forecast_import'))
        coordinators(self.hass)['load'] = SimpleNamespace(last_update_success=True,
            data={'forecast': rows(500), 'source_updated_at': (NOW - timedelta(hours=3)).isoformat()})
        config = {**CONFIG, **INPUTS, 'load_forecast_input': 'imported:load'}
        plan = ThermalCostCoordinator(self.hass, entry('DHW', module=MODULE_THERMAL_COST, **config))
        await plan.async_refresh()
        self.assertTrue(plan.last_update_success)
        self.assertAlmostEqual(plan.data['value'], .125)
        self.assertEqual(plan.data['source_updated_at'], (NOW - timedelta(hours=3)).isoformat())
        with patch.object(plan._async_update_data.__globals__['dt_util'], 'now', lambda: NOW + timedelta(minutes=8)):
            await plan.async_refresh()
        self.assertFalse(plan.last_update_success)

    async def test_sensor_subscriptions_refresh_both_setups_and_cleanup_on_unload(self):
        import asyncio
        import importlib
        from types import ModuleType
        from tests.test_fbc_notifications import Entity, CoordinatorEntity
        from custom_components.ha_bems import async_setup_entry, async_unload_entry
        from custom_components.ha_bems.import_registry import planners, import_signal
        from tests.test_grouped_entries import child
        for name in ('DHW', 'UFH'):
            item = child('entity_creator', MODULE_THERMAL_COST, name, **CONFIG, **INPUTS)
            self.group.subentries[item.subentry_id] = item
        injected = modules()
        sensor = ModuleType('homeassistant.components.sensor')
        sensor.SensorEntity = Entity
        entity = ModuleType('homeassistant.helpers.entity')
        entity.DeviceInfo = lambda **kwargs: kwargs
        entity.EntityCategory = SimpleNamespace(DIAGNOSTIC='diagnostic')
        component = ModuleType('homeassistant.components')
        platform = ModuleType('homeassistant.const')
        platform.Platform = SimpleNamespace(SENSOR='sensor')
        dispatcher = ModuleType('homeassistant.helpers.dispatcher')
        callbacks = {}
        def connect(_hass, signal, handler):
            callbacks.setdefault(signal, []).append(handler)
            return lambda: callbacks[signal].remove(handler)
        dispatcher.async_dispatcher_connect = connect
        dispatcher.async_dispatcher_send = lambda _hass, signal: [fn() for fn in callbacks.get(signal, [])]
        events = ModuleType('homeassistant.helpers.event')
        events.async_track_state_change_event = lambda *args, **kw: (lambda: None)
        events.async_track_time_change = lambda *args, **kw: (lambda: None)
        injected['homeassistant.helpers.update_coordinator'].CoordinatorEntity = CoordinatorEntity
        injected.update({'homeassistant.components': component, 'homeassistant.components.sensor': sensor,
            'homeassistant.helpers.entity': entity, 'homeassistant.const': platform,
            'homeassistant.helpers.dispatcher': dispatcher, 'homeassistant.helpers.event': events})
        tasks, sensors = [], []
        self.hass.async_create_task = lambda coroutine: tasks.append(asyncio.create_task(coroutine))
        async def forward(group, platforms):
            self.assertEqual(platforms, ['sensor'])
            module = importlib.import_module('custom_components.ha_bems.sensor')
            def add(items, *, config_subentry_id):
                self.assertIn(config_subentry_id, group.subentries)
                sensors.extend(items)
            await module.async_setup_entry(self.hass, group, add)
            for item in sensors:
                item.hass = self.hass
                await item.async_added_to_hass()
        async def unload(group, platforms):
            for item in sensors:
                for callback in item.removals:
                    callback()
            return True
        self.hass.config_entries.async_forward_entry_setups = forward
        self.hass.config_entries.async_unload_platforms = unload
        with patch.dict('sys.modules', injected):
            await async_setup_entry(self.hass, self.group)
            await asyncio.gather(*tasks)
            tasks.clear()
            self.assertEqual(len(sensors), 2)
            self.assertEqual(len({item._attr_unique_id for item in sensors}), 2)
            self.assertTrue(all(item._attr_native_unit_of_measurement == 'EUR/timestep' for item in sensors))
            original = sensors[0].native_value
            for row in coordinators(self.hass)['import'].data['forecast']:
                row['price_eur_per_kwh'] = .6
            dispatcher.async_dispatcher_send(self.hass, import_signal('import'))
            await asyncio.gather(*tasks)
            tasks.clear()
            self.assertGreater(sensors[0].native_value, original)
            self.assertEqual(sensors[0].native_value, sensors[1].native_value)
            coordinators(self.hass)['import'].last_update_success = False
            dispatcher.async_dispatcher_send(self.hass, import_signal('import'))
            await asyncio.gather(*tasks)
            self.assertTrue(all('forecast' not in item.extra_state_attributes for item in sensors))
            self.assertTrue(await async_unload_entry(self.hass, self.group))
            for callback in self.group.callbacks:
                callback()
            self.assertFalse(any(callbacks.values()))
            self.assertNotIn('DHW', planners(self.hass))
            self.assertNotIn('UFH', planners(self.hass))
