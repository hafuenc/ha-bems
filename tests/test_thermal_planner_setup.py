"""Planner setup, restart projection and hard one-way forecast permissions."""

from copy import deepcopy
from datetime import datetime, timedelta
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import scipy.optimize  # Load compiled dependencies outside temporary sys.modules mocks.

from tests.ha_stubs import modules
from tests.test_importer import Hass, entry, HABEMSOptionsFlow
from tests.test_thermal_cost import rows
from tests.test_forecast_input import NOW
from custom_components.ha_bems.const import MODULE_OPERATION_IMPORT, MODULE_THERMAL_PLAN
from custom_components.ha_bems.operation_constraints import permission_rows, validate_hierarchy
from tests.test_thermal_milp import CONFIG


class MemoryStore:
    saved = {}

    def __init__(self, hass, version, key): self.key = key
    async def async_load(self): return deepcopy(self.saved.get(self.key))
    async def async_save(self, value): self.saved[self.key] = deepcopy(value)


class ThermalPlannerSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        MemoryStore.saved = {}
        self.injected = modules()
        storage = ModuleType('homeassistant.helpers.storage')
        storage.Store = MemoryStore
        self.injected[storage.__name__] = storage
        components = ModuleType('homeassistant.components')
        components.persistent_notification = SimpleNamespace(async_create=lambda *a, **k: None, async_dismiss=lambda *a, **k: None)
        self.injected[components.__name__] = components
        self.injected['homeassistant.helpers'].entity_registry.async_get = lambda hass: SimpleNamespace(async_get=lambda entity: None)
        self.context = patch.dict('sys.modules', self.injected)
        self.context.start()
        self.addCleanup(self.context.stop)
        from custom_components.ha_bems.thermal_plan_coordinator import ThermalPlanCoordinator
        from custom_components.ha_bems.import_registry import planners, coordinators
        self.Coordinator, self.planners, self.imports = ThermalPlanCoordinator, planners, coordinators
        self.hass = Hass()
        self.root = entry('root', layout='flat_devices', items={})
        self.root.disabled_by = None
        self.hass.entries = [self.root]
        self.states = {'sensor.cost': SimpleNamespace(entity_id='sensor.cost', state='.2', attributes={
            'unit_of_measurement':'EUR/timestep','schema_version':1,'source_updated_at':NOW.isoformat(),
            'forecast':rows(.2,'cost_eur_per_timestep')})}
        self.hass.states = SimpleNamespace(get=self.states.get)

    def add(self, identifier, config):
        self.root.data['items'][identifier] = {'title':identifier, 'data':{'module':MODULE_THERMAL_PLAN, **config}}
        from custom_components.ha_bems.entries import module_entries
        item = next(item for item in module_entries(self.root) if item.entry_id == identifier)
        coordinator = self.Coordinator(self.hass, item, self.root)
        coordinator.name = identifier
        self.planners(self.hass)[identifier] = coordinator
        return coordinator

    async def test_constant_runtime_setup_saves_two_output_planner_without_feedback(self):
        self.hass.config_entries.async_update_entry = lambda target, **values: setattr(target,'data',values['data'])
        flow = HABEMSOptionsFlow()
        flow.hass, flow.config_entry = self.hass, self.root
        self.assertIn('thermal_plan', (await flow.async_step_add_planner())['menu_options'])
        self.assertEqual((await flow.async_step_thermal_plan({'name':'DHW','cost_entity':'sensor.cost','runtime_model':'constant'}))['step_id'], 'runtime_constant')
        form = await flow.async_step_runtime_constant({'runtime_h_per_24h':0})
        self.assertEqual(form['data_schema']({})['constraint_entry_id'],'none')
        constraint_selector = form['data_schema'].schema['constraint_entry_id']
        self.assertTrue(all(option['value'] for option in constraint_selector.config['options']))
        result = await flow.async_step_thermal_storage({**CONFIG,'runtime_h_per_24h':0,'constraint_entry_id':'none','initial_storage_h':None})
        self.assertEqual(result['step_id'],'thermal_plan_preview',result)
        await flow.async_step_thermal_plan_preview({})
        saved = next(iter(self.root.data['items'].values()))['data']
        self.assertEqual(saved['module'], MODULE_THERMAL_PLAN)
        self.assertNotIn('soc_entity', saved)
        self.assertNotIn('runtime_table_text', saved)
        self.assertIsNone(saved['constraint_entry_id'])

    async def test_restart_preserves_feedforward_storage_and_current_operation(self):
        config = {**CONFIG,'runtime_h_per_24h':0,'cost_entity':'sensor.cost'}
        original = self.add('dhw', config)
        await original.async_refresh()
        self.assertTrue(original.last_update_success, original.last_exception)
        self.assertEqual(original.data['value'], .5)
        self.assertEqual(len(original.data['forecast']),289)
        self.assertFalse(original.data['feedback_used'])
        stored = deepcopy(MemoryStore.saved)
        from custom_components.ha_bems.thermal_plan_coordinator import projected_state
        self.assertAlmostEqual(projected_state(original.data,NOW+timedelta(minutes=1))[0],.5)
        restarted = self.add('dhw', config)
        await restarted.async_refresh()
        self.assertTrue(restarted.last_update_success, restarted.last_exception)
        self.assertEqual(restarted.data['value'], .5)
        self.assertTrue(stored)

    async def test_restart_advances_nonconstant_storage_along_the_previous_plan(self):
        config={**CONFIG,'runtime_h_per_24h':12,'cost_entity':'sensor.cost'}
        original=self.add('dhw',config)
        await original.async_refresh()
        self.assertTrue(original.last_update_success,original.last_exception)
        from custom_components.ha_bems.thermal_plan_coordinator import dt_util, projected_state
        future=NOW+timedelta(seconds=30)
        expected=projected_state(original.data,future)[0]
        self.assertNotAlmostEqual(expected,.5)
        restarted=self.add('dhw',config)
        with patch.object(dt_util,'now',lambda:future):
            await restarted.async_refresh()
        self.assertTrue(restarted.last_update_success,restarted.last_exception)
        self.assertAlmostEqual(restarted.data['value'],expected)

    async def test_native_binary_source_is_inverted_and_cycle_is_rejected(self):
        dhw = self.add('dhw', {**CONFIG,'runtime_h_per_24h':0,'cost_entity':'sensor.cost'})
        await dhw.async_refresh()
        self.states['binary_sensor.dhw_plan'] = SimpleNamespace(entity_id='binary_sensor.dhw_plan',state='off',attributes={'planner_item_id':'dhw'})
        self.root.data['items']['permissions'] = {'title':'DHW blocks UFH','data':{'module':MODULE_OPERATION_IMPORT,
            'forecast_entity':'binary_sensor.dhw_plan','operation_meaning':'running'}}
        from custom_components.ha_bems.entries import module_entries
        from custom_components.ha_bems.input_coordinator import ForecastImportCoordinator
        item = next(item for item in module_entries(self.root) if item.entry_id=='permissions')
        importer = ForecastImportCoordinator(self.hass,item,self.root)
        self.imports(self.hass)['permissions'] = importer
        await importer.async_refresh()
        self.assertTrue(importer.last_update_success, importer.last_exception)
        self.assertEqual(importer.data['forecast'][0]['allowed'],1)
        ufh = self.add('ufh', {**CONFIG,'runtime_h_per_24h':0,'cost_entity':'sensor.cost','constraint_entry_id':'permissions'})
        await ufh.async_refresh()
        self.assertTrue(ufh.last_update_success, ufh.last_exception)
        # An invalid upstream cost input cannot become unrestricted permission.
        self.states['sensor.cost'].state='unavailable'
        await ufh.async_refresh()
        self.assertTrue(ufh.last_update_success)  # Existing slot stays committed.
        from custom_components.ha_bems.thermal_plan_coordinator import dt_util
        with patch.object(dt_util,'now',lambda:NOW+timedelta(minutes=15)):
            await ufh.async_refresh()
        self.assertFalse(ufh.last_update_success)
        self.root.data['items']['dhw']['data']['constraint_entry_id']='permissions'
        with self.assertRaisesRegex(ValueError,'Circular'):
            validate_hierarchy(self.hass,'ufh',ufh.config)

    async def test_only_one_solve_per_clock_slot_and_full_intervals_on_scheduled_run(self):
        from custom_components.ha_bems.thermal_plan_coordinator import dt_util, solve_plan
        planner=self.add('dhw',{**CONFIG,'runtime_h_per_24h':0,'cost_entity':'sensor.cost'})
        with patch('custom_components.ha_bems.thermal_plan_coordinator.solve_plan',wraps=solve_plan) as solver:
            await planner.async_refresh()
            first_generated=planner.data['generated_at']
            self.states['sensor.cost'].attributes['forecast']=rows(.4,'cost_eur_per_timestep')
            await planner.async_refresh()
            await planner.async_request_refresh()  # e.g. an upstream dependency request
            self.assertEqual(solver.call_count,1)
            self.assertEqual(planner.data['generated_at'],first_generated)
            boundary=NOW.replace(minute=30,second=0,microsecond=0)
            future=boundary+timedelta(milliseconds=100)
            self.states['sensor.cost'].attributes['forecast']=rows(.4,'cost_eur_per_timestep',future)
            with patch.object(dt_util,'now',lambda:future):
                await planner.async_refresh()
                await planner.async_refresh()
            self.assertTrue(planner.last_update_success,planner.last_exception)
            self.assertEqual(solver.call_count,2)
            self.assertEqual(planner.data['forecast'][0]['effective_start'],boundary.isoformat())
            self.assertEqual(solver.call_args.args[2][0],.25)
            self.assertIsNone(solver.call_args.args[-2])  # No old-plan lock on new slot.

    async def test_startup_waits_for_inputs_but_does_not_repeat_a_failed_solver_attempt(self):
        from custom_components.ha_bems.thermal_plan_coordinator import solve_plan
        planner=self.add('dhw',{**CONFIG,'runtime_h_per_24h':0,'cost_entity':'sensor.cost'})
        self.states['sensor.cost'].state='unavailable'
        await planner.async_refresh()
        self.assertTrue(planner.awaiting_inputs)
        self.assertEqual(planner.plan_status,'waiting_for_inputs')
        changed=Mock()
        planner.async_add_listener(changed)
        self.states['sensor.cost'].state='.2'
        from custom_components.ha_bems.thermal_milp import InfeasiblePlanError
        with patch('custom_components.ha_bems.thermal_plan_coordinator.solve_plan',side_effect=InfeasiblePlanError('Infeasible')) as solver:
            await planner.async_refresh()
            self.assertFalse(planner.awaiting_inputs)
            await planner.async_refresh()
            self.assertEqual(solver.call_count,1)
            self.assertFalse(planner.last_update_success)
            self.assertEqual(planner.plan_status,'infeasible')
            changed.assert_called_once()  # Changed reason survives consecutive failures.

    async def test_import_permission_values_are_strict_and_can_be_used_directly(self):
        source = rows(1,'operation')
        self.assertEqual(permission_rows(source, {'operation_meaning':'running'},NOW)[0]['allowed'],0)
        self.assertEqual(permission_rows(source, {'operation_meaning':'allowed'},NOW)[0]['allowed'],1)
        source[0]['operation']=.5
        with self.assertRaises(ValueError): permission_rows(source,{},NOW)

    async def test_partial_plan_projection_and_expiry_never_reset_storage_silently(self):
        from custom_components.ha_bems.thermal_plan_coordinator import projected_state
        row = rows(1,'operation')[0]
        start = datetime.fromisoformat(row['start'])
        plan = {'initial_previous_operation':0,'initial_previous_elapsed_h':72,'forecast':[
            {**row, 'effective_start':start.isoformat(), 'storage_start_h':1,'storage_end_h':1.125,'runtime_h_per_24h':12}]}
        value, previous, elapsed, fixed = projected_state(plan,start+timedelta(minutes=6))
        self.assertAlmostEqual(value,1.05)
        self.assertEqual((previous,fixed),(1,1))
        self.assertAlmostEqual(elapsed,.1)
        with self.assertRaisesRegex(ValueError,'expired'):
            projected_state(plan,start+timedelta(minutes=16))

    async def test_runtime_lookup_form_accepts_zero_and_keeps_selected_count(self):
        from custom_components.ha_bems.import_registry import coordinators
        self.root.data['items']['temperature']={'title':'Outdoor','data':{'module':'outdoor_temperature_forecast','forecast_entity':'sensor.outdoor'}}
        coordinators(self.hass)['temperature']=SimpleNamespace(last_update_success=True,data={
            'source_updated_at':NOW.isoformat(),'forecast':rows(12,'temperature_c')})
        flow=HABEMSOptionsFlow();flow.hass,flow.config_entry=self.hass,self.root
        await flow.async_step_add_planner()
        await flow.async_step_thermal_plan({'name':'UFH','cost_entity':'sensor.cost','runtime_model':'lookup'})
        result=await flow.async_step_runtime_table({'runtime_temperature_entry_id':'temperature','runtime_point_count':2,
            'runtime_min_h':0,'runtime_max_h':24,'heating_cutoff_enabled':True,'heating_cutoff_c':20})
        self.assertEqual(len(result['data_schema']({})['runtime_table_text'].splitlines()),2)
        result=await flow.async_step_runtime_points({'runtime_table_text':'-12,1;18\n12;0'})
        self.assertEqual(result['step_id'],'thermal_storage')
        self.assertEqual(flow._values()['runtime_points'][-1]['runtime_h_per_24h'],0)
        result=await flow.async_step_thermal_storage({**CONFIG,'runtime_model':'lookup','constraint_entry_id':''})
        self.assertEqual(result['step_id'],'thermal_plan_preview',result)

    async def test_platform_lifecycle_publishes_native_binary_and_duration_forecasts(self):
        import importlib
        from tests.test_fbc_notifications import Entity, CoordinatorEntity
        from custom_components.ha_bems import async_setup_entry, async_unload_entry
        self.add('dhw',{**CONFIG,'runtime_h_per_24h':0,'cost_entity':'sensor.cost'})
        sensor=ModuleType('homeassistant.components.sensor');sensor.SensorEntity=Entity
        binary=ModuleType('homeassistant.components.binary_sensor');binary.BinarySensorEntity=Entity
        entity=ModuleType('homeassistant.helpers.entity')
        entity.DeviceInfo=lambda **kwargs:kwargs
        entity.EntityCategory=SimpleNamespace(DIAGNOSTIC='diagnostic')
        platform=ModuleType('homeassistant.const')
        platform.Platform=SimpleNamespace(SENSOR='sensor',BINARY_SENSOR='binary_sensor',SWITCH='switch')
        dispatcher=ModuleType('homeassistant.helpers.dispatcher')
        dispatcher.async_dispatcher_send=lambda *a:None
        dispatcher.async_dispatcher_connect=lambda *a:(lambda:None)
        events=ModuleType('homeassistant.helpers.event')
        clock=Mock(return_value=lambda:None)
        source=Mock(return_value=lambda:None)
        events.async_track_time_change=clock
        events.async_track_state_change_event=source
        self.injected['homeassistant.helpers.update_coordinator'].CoordinatorEntity=CoordinatorEntity
        injected={module.__name__:module for module in (sensor,binary,entity,platform,dispatcher,events)}
        # Device registry boundary for the flat installation's shared services.
        devices=ModuleType('homeassistant.helpers.device_registry')
        devices.DeviceEntryType=SimpleNamespace(SERVICE='service')
        devices.async_get=lambda _:SimpleNamespace(async_get_or_create=lambda **kw:None)
        injected[devices.__name__]=devices
        self.injected['homeassistant.helpers'].device_registry=devices
        added=[]
        async def forward(root, platforms):
            self.assertEqual(platforms,['sensor','binary_sensor'])
            for name in platforms:
                module=importlib.import_module('custom_components.ha_bems.'+name)
                await module.async_setup_entry(self.hass,root,added.extend)
        async def unload(root,platforms):return True
        self.hass.config_entries.async_forward_entry_setups=forward
        self.hass.config_entries.async_unload_platforms=unload
        self.root.callbacks=[]
        self.root.async_on_unload=self.root.callbacks.append
        self.root.add_update_listener=lambda _:lambda:None
        with patch.dict('sys.modules',injected):
            self.assertTrue(await async_setup_entry(self.hass,self.root))
            self.assertEqual(len(added),8)
            operation=next(item for item in added if hasattr(item,'is_on'))
            duration=next(item for item in added if hasattr(item,'native_value'))
            self.assertFalse(operation.is_on)
            self.assertTrue(operation.available)
            self.assertEqual(operation.extra_state_attributes['forecast_value_key'],'operation')
            self.assertEqual(duration.native_value,.5)
            self.assertEqual(duration.extra_state_attributes['forecast'][0]['duration_end_h'],.5)
            clock.assert_called_once()
            self.assertEqual(clock.call_args.kwargs,{'minute':[0,15,30,45],'second':0})
            self.assertEqual(source.call_args.args[1],['sensor.cost'])
            coordinator=self.planners(self.hass)['dhw']
            outputs={item._attr_unique_id:item for item in added}
            level=outputs['dhw_storage_level']
            status=outputs['dhw_planner_status']
            start=outputs['dhw_next_start']
            stop=outputs['dhw_next_stop']
            self.assertEqual(level.native_value,50)
            self.assertEqual(level.extra_state_attributes['forecast'][0]['storage_level_percent'],50)
            self.assertEqual(status.native_value,'optimal')
            self.assertTrue(status.extra_state_attributes['plan_available'])
            self.assertIsNone(start.native_value)  # All off, no invented transition.
            self.assertIsNone(stop.native_value)
            self.assertGreaterEqual(outputs['dhw_solver_seconds'].native_value,0)
            coordinator.data['solver_gap']=.012
            self.assertEqual(outputs['dhw_solver_gap'].native_value,1.2)
            # Percentages use the usable range, including a nonzero minimum.
            coordinator.data.update(storage_min_h=1,storage_max_h=3)
            timeline=[]
            for i,decision in enumerate([0,1,1,0]):
                begin=NOW+timedelta(minutes=15*i)
                timeline.append({'start':begin.isoformat(),'effective_start':begin.isoformat(),
                    'end':(begin+timedelta(minutes=15)).isoformat(),'operation':decision,
                    'storage_start_h':2,'storage_end_h':2,'runtime_h_per_24h':0})
            coordinator.data['forecast']=timeline
            self.assertEqual(level.native_value,50)
            self.assertEqual(start.native_value,NOW+timedelta(minutes=15))
            self.assertEqual(stop.native_value,NOW+timedelta(minutes=45))
            # Currently on: skip the ongoing start and find the next future one.
            timeline[0]['operation']=1
            timeline[1]['operation']=0
            self.assertEqual(start.native_value,NOW+timedelta(minutes=30))
            self.assertEqual(stop.native_value,NOW+timedelta(minutes=15))
            coordinator.last_update_success=False
            coordinator.last_exception=ValueError('Test missing costs')
            coordinator.plan_status='waiting_for_inputs'
            self.assertTrue(status.available)
            self.assertEqual(status.native_value,'waiting_for_inputs')
            self.assertIn('missing costs',status.extra_state_attributes['reason'])
            self.assertFalse(status.extra_state_attributes['plan_available'])
            self.assertFalse(level.available)
            self.assertNotIn('forecast',level.extra_state_attributes)
            self.assertFalse(start.available)
            self.assertIsNone(outputs['dhw_solver_gap'].native_value)
            with patch.object(self.hass,'async_create_task',create=True) as schedule:
                source.call_args.args[2](None)
                schedule.assert_not_called()
                coordinator.awaiting_inputs=True
                with patch.object(coordinator,'async_request_refresh',new=Mock(return_value=None)):
                    source.call_args.args[2](None)
                    schedule.assert_called_once()
            self.assertTrue(await async_unload_entry(self.hass,self.root))
        self.assertNotIn('dhw',self.planners(self.hass))
