"""Ordered real MILP runs, staging, failure isolation and manual scheduling."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_forecast_input import NOW
from tests.test_importer import entry, HABEMSOptionsFlow
from tests.ha_stubs import modules
from tests import test_thermal_planner_setup as fixture
from tests.test_thermal_milp import CONFIG
from custom_components.ha_bems.const import DOMAIN, MODULE_THERMAL_PLAN, MODULE_THERMAL_COST
from custom_components.ha_bems.optimization import OptimizationManager, ordered_planners, managed_result


class OptimizationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ThermalPlannerSetupTests.asyncSetUp
    add = fixture.ThermalPlannerSetupTests.add

    def setup_manager(self):
        control = OptimizationManager(self.hass)
        self.hass.data.setdefault(DOMAIN, {})['optimization_manager'] = control
        for key, coordinator in self.planners(self.hass).items():
            coordinator.identifier = key
            coordinator.optimization_module = MODULE_THERMAL_PLAN
            control.members[key] = coordinator
        self.control = control
        return control

    def pair(self):
        dhw = self.add('dhw', {**CONFIG, 'runtime_h_per_24h': 24, 'cost_entity': 'sensor.cost'})
        ufh = self.add('ufh', {**CONFIG, 'runtime_h_per_24h': 0, 'cost_entity': 'sensor.cost',
            'constraint_source': 'planner:dhw', 'constraint_mode': 'inverted'})
        self.setup_manager()
        return dhw, ufh

    async def test_real_milp_sequence_publishes_one_batch_and_manual_can_repeat(self):
        dhw, ufh = self.pair()
        publications = []
        for plan in (dhw, ufh):
            plan.async_add_listener(lambda: publications.append((dhw.data['optimization_run_id'], ufh.data['optimization_run_id'])))
        self.assertEqual(ordered_planners(self.hass), ['dhw', 'ufh'])
        self.assertTrue(await self.control.async_run('scheduled'), self.control.details)
        run_id = dhw.data['optimization_run_id']
        self.assertTrue(all(first == second == run_id for first, second in publications))
        self.assertTrue(all(row['allowed'] == 0 for row in ufh.data['forecast']))
        self.assertFalse(await self.control.async_run('scheduled'))
        await dhw.async_refresh()  # An autonomous refresh must not solve again.
        self.assertEqual(dhw.data['optimization_run_id'], run_id)
        self.assertTrue(await self.control.async_run('manual'))
        self.assertNotEqual(dhw.data['optimization_run_id'], run_id)
        self.assertEqual(dhw.data['forecast'][0]['effective_start'], NOW.isoformat())

    async def test_failed_downstream_restores_connected_plans_and_model_state(self):
        dhw, ufh = self.pair()
        independent = self.add('independent', {**CONFIG, 'runtime_h_per_24h': 0, 'cost_entity': 'sensor.cost'})
        self.setup_manager()
        self.assertTrue(await self.control.async_run())
        before = deepcopy(dhw._plan)
        independent_run = independent.data['optimization_run_id']
        with patch.object(ufh, '_async_update_data', side_effect=ValueError('Infeasible downstream')):
            self.assertFalse(await self.control.async_run())
        self.assertEqual(dhw._plan, before)
        self.assertEqual(dhw.data['optimization_run_id'], before['optimization_run_id'])
        self.assertTrue(dhw.data['using_previous_plan'])
        self.assertTrue(ufh.data['using_previous_plan'])
        self.assertNotEqual(independent.data['optimization_run_id'], independent_run)
        self.assertEqual(self.control.status, 'partially_completed')

    async def test_duplicate_presses_do_not_overlap_or_queue(self):
        dhw, _ = self.pair()
        entered, release = asyncio.Event(), asyncio.Event()
        original = dhw._async_update_data
        async def paused():
            entered.set()
            await release.wait()
            return await original()
        with patch.object(dhw, '_async_update_data', side_effect=paused):
            task = asyncio.create_task(self.control.async_run())
            await asyncio.wait_for(entered.wait(), 5)
            self.assertFalse(await self.control.async_run())
            self.assertFalse(await self.control.async_run('scheduled'))
            release.set()
            self.assertTrue(await task)

    async def test_time_budget_and_quarter_boundary_discard_incomplete_results(self):
        dhw, ufh = self.pair()
        self.assertTrue(await self.control.async_run())
        previous = dhw.data['optimization_run_id']
        with patch('custom_components.ha_bems.optimization.monotonic', side_effect=[0, 121, 122, 123]):
            self.assertFalse(await self.control.async_run())
        self.assertEqual(dhw.data['optimization_run_id'], previous)
        with patch('custom_components.ha_bems.optimization._now', side_effect=[NOW, NOW + timedelta(minutes=15)]):
            self.assertFalse(await self.control.async_run())
        self.assertEqual(ufh.data['optimization_run_id'], previous)

    async def test_reverse_sequence_cycles_and_disabled_dependencies_fail_closed(self):
        dhw, _ = self.pair()
        with self.assertRaisesRegex(ValueError, 'before'):
            ordered_planners(self.hass, ['ufh', 'dhw'])
        self.root.data['items']['dhw']['data']['constraint_source'] = 'planner:ufh'
        with self.assertRaisesRegex(ValueError, 'Circular'):
            ordered_planners(self.hass)
        self.root.data['items']['dhw']['data'].pop('constraint_source')
        self.root.data['items']['dhw']['data']['enabled'] = False
        self.assertFalse(await self.control.async_run())
        self.assertIsNone(dhw.data)

    async def test_new_configuration_and_unload_during_run_discard_results(self):
        dhw, _ = self.pair()
        original = dhw._async_update_data
        async def unload():
            result = await original()
            self.control.members.pop('dhw')
            return result
        with patch.object(dhw, '_async_update_data', side_effect=unload):
            self.assertFalse(await self.control.async_run())
        self.assertIsNone(dhw.data)
        self.assertIsNone(dhw._plan)

    async def test_staged_costs_are_used_before_publication(self):
        from custom_components.ha_bems.optimization import input_state
        from tests.test_thermal_cost import rows

        dhw = self.add('dhw', {**CONFIG, 'runtime_h_per_24h': 0, 'cost_entity': 'sensor.new_cost'})
        registry = self.injected['homeassistant.helpers'].entity_registry
        registry.async_get = lambda hass: SimpleNamespace(async_get=lambda entity: SimpleNamespace(
            platform=DOMAIN, unique_id='cost_thermal_runtime_cost') if entity == 'sensor.new_cost' else None)
        self.states['sensor.new_cost'] = SimpleNamespace(state='unavailable', attributes={})
        cost = SimpleNamespace(name='Cost', config={}, data=None, last_update_success=False,
            last_exception=None, identifier='cost', optimization_module=MODULE_THERMAL_COST,
            async_update_listeners=lambda: None)
        async def calculate():
            return {'value': .7, 'schema_version': 1, 'source_updated_at': NOW.isoformat(),
                    'generated_at': NOW.isoformat(), 'forecast': rows(.7, 'cost_eur_per_timestep')}
        cost._async_update_data = calculate
        self.planners(self.hass)['cost'] = cost
        self.setup_manager()
        cost.optimization_module = MODULE_THERMAL_COST
        self.assertTrue(await self.control.async_run(), self.control.details)
        self.assertEqual(dhw.data['optimization_run_id'], cost.data['optimization_run_id'])
        self.assertEqual(self.states['sensor.new_cost'].state, 'unavailable')
        self.assertIs(input_state(self.hass, 'sensor.new_cost'), self.states['sensor.new_cost'])

    async def test_manager_options_only_offer_dependency_ready_choices(self):
        self.pair()
        manager_entry = entry('manager', module='optimization_manager')
        self.hass.entries.append(manager_entry)
        flow = HABEMSOptionsFlow()
        flow.hass, flow.config_entry = self.hass, manager_entry
        form = await flow.async_step_optimization_manager({'planner_time_limit_s': 12,
            'total_time_limit_s': 90, 'change_sequence': True})
        self.assertEqual(form['step_id'], 'optimization_sequence')
        self.assertEqual(form['data_schema'].schema['next_planner'].config['options'], [{'value': 'dhw', 'label': 'dhw'}])
        form = await flow.async_step_optimization_sequence({'next_planner': 'dhw'})
        self.assertEqual(form['data_schema'].schema['next_planner'].config['options'], [{'value': 'ufh', 'label': 'ufh'}])
        result = await flow.async_step_optimization_sequence({'next_planner': 'ufh'})
        self.assertEqual(result['data']['planner_order'], ['dhw', 'ufh'])
        self.assertEqual(result['data']['planner_time_limit_s'], 12)

    async def test_real_cost_and_milp_chain_accounts_for_fresh_dhw_electrical_load(self):
        from tests.test_thermal_setup import thermal_hass, INPUTS
        from tests.test_thermal_cost import CONFIG as COST_CONFIG
        from custom_components.ha_bems.thermal_coordinator import ThermalCostCoordinator

        inputs = thermal_hass()
        self.hass.entries.extend(inputs.entries)
        self.imports(self.hass).update(self.imports(inputs))
        registry = self.injected['homeassistant.helpers'].entity_registry
        registry.async_get = lambda hass: SimpleNamespace(async_get=lambda entity: SimpleNamespace(
            platform=DOMAIN, unique_id=entity.removeprefix('sensor.')+'_thermal_runtime_cost')
            if entity in ('sensor.dhw_cost','sensor.ufh_cost') else None)
        costs = {}
        for key in ('dhw_cost', 'ufh_cost'):
            config = {**COST_CONFIG, **INPUTS}
            if key == 'ufh_cost':
                config['additional_load_inputs'] = ['planner:dhw']
            cost_entry = entry(key, module=MODULE_THERMAL_COST, **config)
            self.hass.entries.append(cost_entry)
            costs[key] = ThermalCostCoordinator(self.hass, cost_entry)
            self.planners(self.hass)[key] = costs[key]
        dhw = self.add('dhw', {**CONFIG, 'runtime_h_per_24h':24, 'cost_entity':'sensor.dhw_cost'})
        ufh = self.add('ufh', {**CONFIG, 'runtime_h_per_24h':0, 'cost_entity':'sensor.ufh_cost',
            'constraint_source':'planner:dhw','constraint_mode':'inverted'})
        self.setup_manager()
        for cost in costs.values():
            cost.optimization_module = MODULE_THERMAL_COST
        self.assertTrue(await self.control.async_run(), self.control.details)
        self.assertAlmostEqual(costs['dhw_cost'].data['value'], .10)
        self.assertAlmostEqual(costs['ufh_cost'].data['value'], .15)
        self.assertTrue(all(row['allowed'] == 0 for row in ufh.data['forecast']))
        self.assertEqual(len({plan.data['optimization_run_id'] for plan in (dhw, ufh, *costs.values())}), 1)

    async def test_restored_plan_waits_without_solving_and_retention_expires(self):
        dhw, _ = self.pair()
        self.assertTrue(await self.control.async_run())
        replacement = self.add('dhw', dhw.config)
        with patch('custom_components.ha_bems.thermal_plan_coordinator.solve_plan') as solve:
            await replacement.async_restore_plan()
            self.assertTrue(replacement.data['using_previous_plan'])
            solve.assert_not_called()
        with patch('custom_components.ha_bems.optimization._now', return_value=NOW + timedelta(hours=2)):
            with self.assertRaisesRegex(Exception, 'Waiting'):
                managed_result(dhw)


class BatteryManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        injected = modules()
        injected['homeassistant.helpers'].entity_registry.async_get = lambda hass: SimpleNamespace(async_get=lambda entity: None)
        context = patch.dict('sys.modules', injected)
        context.start()
        self.addCleanup(context.stop)
        from tests.test_fbc_load import LoadAwareTests
        await LoadAwareTests.asyncSetUp(self)
        from custom_components.ha_bems.coordinator import BEMSCoordinator

        self.hass.entries.append(entry('battery', module='battery_fbc', **self.config))
        self.battery = BEMSCoordinator(self.hass, self.hass.entries[-1], 'battery_fbc')
        self.battery.identifier = 'battery'
        self.battery.optimization_module = 'battery_fbc'
        self.battery.limitation_enabled = True
        self.control = OptimizationManager(self.hass)
        self.control.members['battery'] = self.battery
        self.planners = __import__('custom_components.ha_bems.import_registry', fromlist=['planners']).planners
        self.planners(self.hass)['battery'] = self.battery
        self.hass.data[DOMAIN]['optimization_manager'] = self.control
        for key, value in self.states.items():
            value.entity_id = key
        self.hass.states.async_all = lambda: [deepcopy(state) for state in self.states.values()]

    async def test_fbc_input_updates_wait_but_manual_bypass_and_seasonal_guard_are_immediate(self):
        with patch.object(self.battery, '_calculate_fbc', wraps=self.battery._calculate_fbc) as calculate:
            self.assertTrue(await self.control.async_run())
            self.states['sensor.soc'].state = '85'
            await self.battery.async_refresh()
            self.assertEqual(calculate.call_count, 1)
            await self.battery.async_set_limitation_enabled(False)
            self.assertEqual(self.battery.data['value'], 5000)
            await self.battery.async_set_limitation_enabled(True)
            self.assertEqual(calculate.call_count, 1)
            self.assertTrue(await self.control.async_run())
            self.assertEqual(calculate.call_count, 2)
            self.states['sensor.soc'].state = '30'
            await self.battery.async_refresh()
            self.assertEqual(calculate.call_count, 2)
            self.assertEqual(self.battery.data['value'], 5000)

    async def test_inflight_result_cannot_overwrite_manual_bypass(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.battery._async_update_data
        async def paused():
            result = await original()
            entered.set()
            await release.wait()
            return result
        with patch.object(self.battery, '_async_update_data', side_effect=paused):
            task = asyncio.create_task(self.control.async_run())
            await asyncio.wait_for(entered.wait(), 5)
            self.battery.limitation_enabled = False
            # Publish immediate bypass from a separate task context.
            self.battery.data = self.battery._maximum_charging_plan(None, NOW, 'disabled_by_switch')
            release.set()
            self.assertFalse(await task)
        self.assertEqual(self.battery.data['value'], 5000)
        self.assertEqual(self.battery.data['override_reasons'], ['disabled_by_switch'])
