"""EEG allocation, typed imports and consumer-side load priorities."""

from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.ha_stubs import modules
from tests.test_importer import Hass, entry, HABEMSConfigFlow, HABEMSOptionsFlow
from tests.test_forecast_input import NOW
from tests.test_forecast_types import scalar_source
from tests.test_thermal_cost import CONFIG, rows
from tests.test_thermal_setup import INPUTS, thermal_hass
from custom_components.ha_bems.const import (
    MODULE_EEG_PRICE, MODULE_EEG_ENERGY, CONF_EEG_PRICE, CONF_EEG_ENERGY,
    CONF_ADDITIONAL_LOADS, MODULE_THERMAL_PLAN, MODULE_THERMAL_COST,
)
from custom_components.ha_bems.thermal_cost import calculate_cost_forecast
with patch.dict('sys.modules', modules()):
    from custom_components.ha_bems.thermal_coordinator import ThermalCostCoordinator, thermal_references
    from custom_components.ha_bems.import_registry import coordinators, planners
    from custom_components.ha_bems.thermal_loads import read_additional_loads, validate_load_hierarchy


def eeg_cost(pv=0, load=0, energy=.25, price=.4, additional=None, **settings):
    return calculate_cost_forecast({**CONFIG, 'eeg_enabled': True, **settings}, NOW,
        rows(pv), rows(.2, 'price_eur_per_kwh'), rows(.1, 'price_eur_per_kwh'), rows(load),
        eeg_prices=rows(price, 'price_eur_per_kwh'), eeg_energy=rows(energy, 'energy_kwh'),
        additional_loads=additional)


class EEGArithmeticTests(unittest.TestCase):
    def test_expensive_eeg_is_mandatory_and_zero_is_valid(self):
        result = eeg_cost()
        self.assertAlmostEqual(result['value'], .15)  # .25 kWh EEG + .25 kWh grid
        row = result['forecast'][0]
        self.assertEqual(row['eeg_power_w'], 1000)
        self.assertEqual(row['grid_import_power_w'], 1000)
        self.assertAlmostEqual(row['eeg_cost_eur_per_timestep'], .1)
        self.assertAlmostEqual(eeg_cost(energy=0)['value'], .1)
        self.assertAlmostEqual(eeg_cost(energy=5)['value'], .2)
        self.assertAlmostEqual(eeg_cost(energy=5, price=-.2)['value'], -.1)

    def test_pv_then_background_load_then_eeg_and_additional_loads(self):
        # Discussion example: base 1 kWh, PV .6 kWh, EEG .7 kWh, device .5 kWh.
        result = eeg_cost(pv=2400, load=4000, energy=.7, price=.3)
        self.assertAlmostEqual(result['value'], .13)
        self.assertAlmostEqual(result['forecast'][0]['eeg_energy_kwh'], .3)
        priority = eeg_cost(pv=2400, load=3000, additional=[rows(1000)], energy=.7, price=.3)
        self.assertEqual(priority['forecast'], result['forecast'])
        pv_only = eeg_cost(pv=5000, load=1000, energy=3)
        self.assertAlmostEqual(pv_only['value'], .05)
        self.assertEqual(pv_only['forecast'][0]['eeg_power_w'], 0)
        self.assertAlmostEqual(eeg_cost(pv=1000, energy=1)['value'], .125)
        self.assertEqual(eeg_cost(load=6000, energy=.5)['forecast'][0]['eeg_power_w'], 0)

    def test_export_limit_still_values_curtailed_pv_first(self):
        result = eeg_cost(pv=5000, load=1000, energy=1,
                          feed_in_limit_enabled=True, feed_in_limit_w=3000)
        self.assertAlmostEqual(result['value'], .025)
        self.assertEqual(result['forecast'][0]['eeg_power_w'], 0)

    def test_missing_invalid_eeg_and_duplicate_loads_fail(self):
        for energy in (-1, float('nan'), True):
            with self.assertRaises(ValueError):
                eeg_cost(energy=energy)
        with self.assertRaises(ValueError):
            calculate_cost_forecast({**CONFIG, 'eeg_enabled': True}, NOW,
                rows(0), rows(.2, 'price_eur_per_kwh'), rows(.1, 'price_eur_per_kwh'))
        for settings in ({CONF_ADDITIONAL_LOADS: ['imported:a', 'imported:a']},
                         {CONF_ADDITIONAL_LOADS: ['imported:a'], 'load_forecast_input': 'imported:a'}):
            with self.assertRaisesRegex(ValueError, 'only once'):
                eeg_cost(**settings)
        with self.assertRaises(ValueError):
            eeg_cost(additional=[rows(-10)])

    def test_combined_fill_budget_includes_eeg_and_additional_loads(self):
        prices, energy, extra = rows(.4, 'price_eur_per_kwh'), rows(.5, 'energy_kwh'), rows(100)
        for forecast, indices in ((prices, [3, 4]), (energy, [7, 8]), (extra, [11])):
            for i in indices:
                forecast[i]['filled_previous'] = True
        with self.assertRaisesRegex(ValueError, 'four filled'):
            calculate_cost_forecast({**CONFIG, 'eeg_enabled': True}, NOW,
                rows(0), rows(.2, 'price_eur_per_kwh'), rows(.1, 'price_eur_per_kwh'),
                eeg_prices=prices, eeg_energy=energy, additional_loads=[extra])


class EEGIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        context = patch.dict('sys.modules', modules())
        context.start()
        self.addCleanup(context.stop)
        from homeassistant.helpers import entity_registry as er
        self.registry = {}
        registry = patch.object(er, 'async_get', lambda hass: SimpleNamespace(async_get=self.registry.get), create=True)
        registry.start()
        self.addCleanup(registry.stop)
        self.hass = thermal_hass()

    async def test_import_setup_edit_validation_and_units(self):
        for module, step, unit, key, value in (
            (MODULE_EEG_PRICE, 'import_eeg_price', 'EUR/kWh', 'price_eur_per_kwh', -.1),
            (MODULE_EEG_ENERGY, 'import_eeg_energy', 'kWh/timestep', 'energy_kwh', .5),
        ):
            hass = Hass()
            scalar_source(hass, unit, key, value)
            flow = HABEMSConfigFlow()
            flow.hass = hass
            preview = await getattr(flow, 'async_step_' + step)({'name': 'EEG', 'forecast_entity': 'sensor.pv'})
            self.assertEqual(preview['step_id'], 'import_value_preview')
            saved = await flow.async_step_import_value_preview({})
            self.assertEqual(saved['data']['module'], module)
            edit = HABEMSOptionsFlow()
            edit.hass, edit.config_entry = hass, entry('eeg', module=module, forecast_entity='sensor.pv')
            self.assertEqual((await edit.async_step_init())['step_id'], step)
            hass.state.attributes['unit_of_measurement'] = 'kWh'
            self.assertIn('base', (await getattr(flow, 'async_step_' + step)({'forecast_entity': 'sensor.pv'}))['errors'])
            if module == MODULE_EEG_ENERGY:
                hass.state.attributes['unit_of_measurement'] = unit
                hass.state.attributes['forecast'][0][key] = -1
                self.assertIn('base', (await flow.async_step_import_eeg_energy({'forecast_entity': 'sensor.pv'}))['errors'])

    def register_eeg(self):
        for identifier, module, key, value in (
            ('eeg-price', MODULE_EEG_PRICE, 'price_eur_per_kwh', .4),
            ('eeg-energy', MODULE_EEG_ENERGY, 'energy_kwh', .25)):
            self.hass.entries.append(entry(identifier, module=module))
            coordinators(self.hass)[identifier] = SimpleNamespace(last_update_success=True,
                data={'forecast': rows(value, key), 'source_updated_at': NOW.isoformat()})
        return {**CONFIG, **INPUTS, 'eeg_enabled': True,
                CONF_EEG_PRICE: 'eeg-price', CONF_EEG_ENERGY: 'eeg-energy'}

    async def test_coordinator_refresh_failure_retention_disable_and_opt_out(self):
        config = self.register_eeg()
        plan = ThermalCostCoordinator(self.hass, entry('cost', module=MODULE_THERMAL_COST, **config))
        await plan.async_refresh()
        self.assertTrue(plan.last_update_success, str(plan.last_exception))
        self.assertAlmostEqual(plan.data['value'], .125)  # 1 kW PV + 1 kW EEG
        coordinators(self.hass)['eeg-price'].data['forecast'] = rows(.8, 'price_eur_per_kwh')
        await plan.async_refresh()
        self.assertAlmostEqual(plan.data['value'], .225)
        coordinators(self.hass)['eeg-energy'].last_update_success = False
        coordinators(self.hass)['eeg-energy'].input_status = 'unavailable'
        coordinators(self.hass)['eeg-energy'].last_exception = ValueError('refresh failure')
        coordinators(self.hass)['eeg-energy'].data['input_validated_at'] = (NOW-timedelta(hours=2)).isoformat()
        await plan.async_refresh()
        self.assertTrue(plan.data['using_previous_forecast'])
        self.hass.entries[-1].data['enabled'] = False
        await plan.async_refresh()
        self.assertFalse(plan.last_update_success)
        plan.config['eeg_enabled'] = False
        await plan.async_refresh()
        self.assertTrue(plan.last_update_success)
        self.assertAlmostEqual(plan.data['value'], .10)
        refs = thermal_references(config)
        self.assertIn('eeg-price', refs)
        self.assertIn('eeg-energy', refs)

    def register_priority(self):
        upstream = entry('priority', module=MODULE_THERMAL_PLAN, cost_entity='sensor.priority_cost')
        self.hass.entries.append(upstream)
        operations = rows(1, 'operation')
        operations[1]['operation'] = 0
        planners(self.hass)['priority'] = SimpleNamespace(last_update_success=True,
            data={'forecast': operations, 'source_updated_at': NOW.isoformat()})
        attributes = {'forecast': rows(1500, 'electrical_power_w'), 'source_updated_at': NOW.isoformat(),
                      'unit_of_measurement': 'EUR/timestep', 'schema_version': 1}
        self.hass.states.get = lambda entity: SimpleNamespace(state='0.1', attributes=attributes)
        self.registry['sensor.priority_cost'] = SimpleNamespace(platform='ha_bems', unique_id='priority-cost_thermal_runtime_cost')
        return attributes

    async def test_existing_plan_and_multiple_imported_loads_in_cost_setup(self):
        self.register_priority()
        for identifier, power in [('base', 100), ('extra', 400)]:
            self.hass.entries.append(entry(identifier, module='load_forecast_import'))
            coordinators(self.hass)[identifier] = SimpleNamespace(last_update_success=True,
                data={'forecast': rows(power), 'source_updated_at': NOW.isoformat()})
        config = {**CONFIG, **INPUTS, 'load_forecast_input': 'imported:base',
                  CONF_ADDITIONAL_LOADS: ['planner:priority', 'imported:extra']}
        plan = ThermalCostCoordinator(self.hass, entry('cost', module=MODULE_THERMAL_COST, **config))
        await plan.async_refresh()
        self.assertTrue(plan.last_update_success, str(plan.last_exception))
        self.assertEqual(plan.data['forecast'][0]['considered_load_power_w'], 2000)
        self.assertEqual(plan.data['forecast'][1]['considered_load_power_w'], 500)
        flow = HABEMSConfigFlow()
        flow.hass = self.hass
        flow._draft = {**CONFIG, **INPUTS}
        form = await flow.async_step_thermal_cost_inputs()
        fields = {str(key): value for key, value in form['data_schema'].schema.items()}
        self.assertTrue(fields[CONF_ADDITIONAL_LOADS].config['multiple'])
        self.assertIn('planner:priority', {choice['value'] for choice in fields[CONF_ADDITIONAL_LOADS].config['options']})
        saved = await flow.async_step_thermal_cost_inputs(config)
        self.assertEqual(saved['step_id'], 'thermal_cost_preview')
        self.assertIn('extra', thermal_references(config))
        self.hass.entries[-3].data['enabled'] = False  # priority plan
        await plan.async_refresh()
        self.assertFalse(plan.last_update_success)

    def test_plan_fill_provenance_invalid_operation_and_missing_power(self):
        attributes = self.register_priority()
        config = {CONF_ADDITIONAL_LOADS: ['planner:priority']}
        plan = planners(self.hass)['priority'].data['forecast']
        plan[5]['filled_previous'] = True
        attributes['forecast'][8]['filled_previous'] = True
        result, _ = read_additional_loads(self.hass, config, NOW)
        self.assertTrue(result[0][5]['filled_previous'])
        self.assertTrue(result[0][8]['filled_previous'])
        plan[2]['operation'] = .5
        with self.assertRaisesRegex(ValueError, '0 or 1'):
            read_additional_loads(self.hass, config, NOW)
        plan[2]['operation'] = 1
        del attributes['forecast'][0]['electrical_power_w']
        with self.assertRaises(ValueError):
            read_additional_loads(self.hass, config, NOW)

    def test_direct_indirect_and_unsaved_priority_cycles_rejected(self):
        self.register_priority()
        cost = entry('priority-cost', module=MODULE_THERMAL_COST,
                     additional_load_inputs=['planner:priority'])
        self.hass.entries.append(cost)
        with self.assertRaisesRegex(ValueError, 'Circular'):
            validate_load_hierarchy(self.hass, {CONF_ADDITIONAL_LOADS: ['planner:priority']})
        cost.data[CONF_ADDITIONAL_LOADS] = []
        validate_load_hierarchy(self.hass, {CONF_ADDITIONAL_LOADS: ['planner:priority']})
        with self.assertRaisesRegex(ValueError, 'Circular'):
            validate_load_hierarchy(self.hass, {CONF_ADDITIONAL_LOADS: ['planner:priority']}, 'priority-cost')
        cost.data[CONF_ADDITIONAL_LOADS] = ['planner:second']
        self.hass.entries.extend([entry('second', module=MODULE_THERMAL_PLAN, cost_entity='sensor.second_cost'),
                                  entry('second-cost', module=MODULE_THERMAL_COST,
                                        additional_load_inputs=['planner:priority'])])
        self.registry['sensor.second_cost'] = SimpleNamespace(platform='ha_bems', unique_id='second-cost_thermal_runtime_cost')
        with self.assertRaisesRegex(ValueError, 'Circular'):
            validate_load_hierarchy(self.hass, {CONF_ADDITIONAL_LOADS: ['planner:priority']})

    async def test_cost_sensor_refreshes_on_priority_plan_and_power_changes(self):
        import asyncio
        import importlib
        from types import ModuleType
        from tests.test_fbc_notifications import Entity, CoordinatorEntity

        self.register_priority()
        sensor_api = ModuleType('homeassistant.components.sensor')
        sensor_api.SensorEntity = Entity
        entity_api = ModuleType('homeassistant.helpers.entity')
        entity_api.DeviceInfo = lambda **kwargs: kwargs
        entity_api.EntityCategory = SimpleNamespace(DIAGNOSTIC='diagnostic')
        dispatcher = ModuleType('homeassistant.helpers.dispatcher')
        dispatcher.async_dispatcher_connect = lambda *args: lambda: None
        events = ModuleType('homeassistant.helpers.event')
        handlers = []
        def track(hass, selected, callback):
            handlers.append(callback)
            return lambda: None
        events.async_track_state_change_event = track
        events.async_track_time_change = lambda *args, **kwargs: lambda: None
        from homeassistant.helpers import update_coordinator
        update_coordinator.CoordinatorEntity = CoordinatorEntity
        injected = {module.__name__: module for module in (sensor_api, entity_api, dispatcher, events)}
        with patch.dict('sys.modules', injected):
            module = importlib.import_module('custom_components.ha_bems.sensor')
            tasks, calls = [], []
            async def refresh():
                calls.append(True)
            def create_task(coro):
                tasks.append(asyncio.create_task(coro))
            self.hass.async_create_task = create_task
            coordinator = SimpleNamespace(config={CONF_ADDITIONAL_LOADS: ['planner:priority']},
                                          async_request_refresh=refresh)
            item = module.BEMSForecastSensor(coordinator, entry('cost', module=MODULE_THERMAL_COST), MODULE_THERMAL_COST)
            item.hass = self.hass
            item.async_on_remove = lambda callback: None
            # Patch globals too when another test has already imported this platform.
            with patch.object(module, 'async_track_state_change_event', track), patch.object(module, 'async_track_time_change', events.async_track_time_change):
                await item.async_added_to_hass()
                await asyncio.gather(*tasks)
                self.assertEqual(len(calls), 1)
                handlers[0](SimpleNamespace(data={'entity_id': 'binary_sensor.priority',
                    'new_state': SimpleNamespace(attributes={'planner_item_id': 'priority'})}))
                await asyncio.gather(*tasks)
                self.assertEqual(len(calls), 2)
                handlers[0](SimpleNamespace(data={'entity_id': 'sensor.priority_cost'}))
                await asyncio.gather(*tasks)
                self.assertEqual(len(calls), 3)
                handlers[0](SimpleNamespace(data={'entity_id': 'sensor.unrelated'}))
                await asyncio.gather(*tasks)
                self.assertEqual(len(calls), 3)
