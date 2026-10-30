"""One config entry, shared service devices, central item management and migration."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_grouped_entries import MigrationHarness, parent, child
from tests.test_importer import Hass, entry, HABEMSOptionsFlow
from tests.ha_stubs import modules
from custom_components.ha_bems.const import DOMAIN, MONTHS
from custom_components.ha_bems.entries import module_entries, add_item_entities


class FlatHarness(MigrationHarness):
    def __init__(self, entries):
        super().__init__(entries)
        for record in self.entities.values():
            record.area_id = None
        for record in self.devices.values():
            record.identifiers = {(DOMAIN, record.config_entry_id)}
        device_api = self.injected['homeassistant.helpers.device_registry']
        device_api.DeviceEntryType = SimpleNamespace(SERVICE='service')
        self.injected['homeassistant.helpers'].entity_registry.async_entries_for_device = (
            lambda registry, identifier: [record for record in self.entities.values() if record.device_id == identifier])

    def update_entry(self, target, *, data=None, options=None, title=None, unique_id=None, minor_version=None):
        for key, value in dict(data=data, options=options, title=title, unique_id=unique_id, minor_version=minor_version).items():
            if value is not None:
                setattr(target, key, value)

    def async_get(self, identifier):
        return self.entities.get(identifier) or self.devices.get(identifier)

    def async_get_or_create(self, *, config_entry_id, identifiers, **values):
        for record in self.devices.values():
            if record.config_entry_id == config_entry_id and record.identifiers & identifiers:
                return record
        identifier = 'service-' + next(iter(identifiers))[1]
        device = SimpleNamespace(id=identifier, config_entry_id=config_entry_id,
            identifiers=identifiers, area_id=None, name_by_user=None, **values)
        self.devices[identifier] = device
        return device

    def async_remove_device(self, identifier):
        assert not any(entity.device_id == identifier for entity in self.entities.values())
        self.devices.pop(identifier)

    def async_remove(self, identifier):
        self.entities.pop(identifier)

    async def flatten(self):
        with patch.dict('sys.modules', self.injected):
            from custom_components.ha_bems.migration import async_flatten_entries
            await async_flatten_entries(self.hass)


class FlatMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_conflicting_installations_and_forward_versions_are_not_overwritten(self):
        for failure in ('duplicate', 'version'):
            one = parent('entity_creator')
            one.data = {'layout': 'flat_devices', 'items': {}}
            two = parent('entity_importer')
            two.data = {'layout': 'flat_devices', 'items': {}}
            harness = FlatHarness([one, two] if failure == 'duplicate' else [one])
            if failure == 'version':
                one.version = 2
            original = deepcopy(one.data)
            with self.assertRaisesRegex(ValueError, 'consolidation was not started'):
                await harness.flatten()
            self.assertEqual(one.data, original)

    async def test_standalone_settings_entity_ids_references_and_battery_device_survive(self):
        old = [entry('soc', module='seasonal_soc', monthly_min_soc_percent=[40] * 12),
               entry('load', module='non_flexible_load', annual_consumption_kwh=3000),
               entry('pv', forecast_entity='sensor.roof', pv_power_type='ac'),
               entry('battery', module='battery_fbc', pv_import_entry_id='pv',
                     load_forecast_input='generated:load', max_charge_power_w=5000)]
        harness = FlatHarness(old)
        old[1].disabled_by = 'user'
        entities = deepcopy(harness.entities)
        await harness.flatten()
        root = next(item for item in harness.hass.entries if item.data.get('layout') == 'flat_devices')
        self.assertEqual(set(root.data['items']), {'soc', 'load', 'pv', 'battery'})
        self.assertFalse(root.data['items']['load']['data']['enabled'])
        self.assertEqual(root.data['items']['battery']['data']['pv_import_entry_id'], 'pv')
        self.assertEqual(root.data['items']['battery']['data']['load_forecast_input'], 'generated:load')
        self.assertEqual(root.subentries, {})
        for identifier, record in harness.entities.items():
            self.assertEqual(record.unique_id, entities[identifier].unique_id)
            self.assertEqual(record.config_entry_id, root.entry_id)
            self.assertIsNone(record.config_subentry_id)
        self.assertEqual(harness.entities['sensor.pv'].device_id, 'service-service:entity_importer')
        self.assertEqual(harness.entities['sensor.soc'].device_id, 'service-service:entity_creator')
        self.assertEqual(harness.entities['sensor.soc'].area_id, 'control-room')
        self.assertEqual(harness.entities['sensor.battery'].device_id, 'device-battery')
        self.assertEqual(harness.devices['device-battery'].config_entry_id, root.entry_id)
        snapshot = deepcopy(root.data)
        await harness.flatten()
        self.assertEqual(root.data, snapshot)
        for callback in harness.starts:
            await callback(harness.hass)
        self.assertEqual(harness.hass.entries, [root])

    async def test_grouped_cost_setup_and_interrupted_device_move_resume(self):
        cost = child('thermal_storage', 'thermal_runtime_cost', 'cost', cop_model='constant', constant_cop=3)
        source = parent('thermal_storage')
        harness = FlatHarness([source])
        source.subentries[cost.subentry_id] = cost
        record = harness.entities['sensor.thermal_storage']
        record.unique_id = 'cost_thermal_runtime_cost'
        record.config_subentry_id = cost.subentry_id
        device = harness.devices['device-thermal_storage']
        device.identifiers = {(DOMAIN, 'cost')}
        device.config_subentry_id = cost.subentry_id
        with patch.dict('sys.modules', harness.injected):
            from custom_components.ha_bems.flat_migration import async_consolidate_entries
            # The old regrouping step already has independent regression coverage.
            with patch('custom_components.ha_bems.flat_migration.ensure_service_devices', side_effect=RuntimeError('Interrupted')):
                with self.assertRaisesRegex(RuntimeError, 'Interrupted'):
                    await async_consolidate_entries(harness.hass)
            self.assertIn('device_migration_pending', source.data)
            await async_consolidate_entries(harness.hass)
        self.assertNotIn('device_migration_pending', source.data)
        self.assertEqual(source.data['items']['cost']['data']['constant_cop'], 3)
        self.assertEqual(record.unique_id, 'cost_thermal_runtime_cost')
        self.assertEqual(record.device_id, 'service-service:entity_creator')
        self.assertEqual(source.subentries, {})


class FlatOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = entry('installation', layout='flat_devices', items={})
        self.harness = FlatHarness([])
        self.harness.hass.entries = [self.root]
        self.hass = self.harness.hass
        context = patch.dict('sys.modules', self.harness.injected)
        context.start()
        self.addCleanup(context.stop)

    def flow(self):
        flow = HABEMSOptionsFlow()
        flow.hass, flow.config_entry = self.hass, self.root
        return flow

    async def test_add_two_helpers_edit_disable_and_confirm_remove_independently(self):
        self.assertEqual((await self.flow().async_step_init())['menu_options'], ['add_import', 'add_helper', 'add_planner'])
        for name in ('Minimum A', 'Minimum B'):
            flow = self.flow()
            await flow.async_step_add_helper()
            self.assertIn('name', [str(key) for key in (await flow.async_step_soc())['data_schema'].schema])
            await flow.async_step_soc({'name': name})
            await flow.async_step_monthly({month: 40 for month in MONTHS})
        self.assertEqual(len(self.root.data['items']), 2)
        first, second = list(self.root.data['items'])
        other = deepcopy(self.root.data['items'][second])
        edit = self.flow()
        await edit.async_step_edit_item({'item_id': first})
        await edit.async_step_item_settings({'name': 'Renamed', 'enabled': False})
        self.assertEqual(self.root.data['items'][first]['title'], 'Renamed')
        self.assertFalse(self.root.data['items'][first]['data']['enabled'])
        self.assertEqual(self.root.data['items'][second], other)
        remove = self.flow()
        self.assertEqual((await remove.async_step_remove_item({'item_id': first}))['step_id'], 'remove_confirm')
        self.assertIn(first, self.root.data['items'])
        self.root.subentries = {}
        await remove.async_step_remove_confirm({})
        self.assertEqual(set(self.root.data['items']), {second})
        items = module_entries(self.root)
        self.assertEqual(items[0].entry_id, second)
        self.assertIsNone(items[0].config_subentry_id)
        calls = []
        add_item_entities(lambda entities, **kwargs: calls.append(kwargs), [], items[0])
        self.assertEqual(calls, [{}])

    async def test_shared_device_identifiers_and_named_sensor_outputs(self):
        from custom_components.ha_bems.devices import shared_device_info, ensure_service_devices
        self.root.data['items'] = {'one': {'title': 'One', 'data': {'module': 'seasonal_soc'}},
                                   'two': {'title': 'Two', 'data': {'module': 'seasonal_soc'}}}
        devices = ensure_service_devices(self.hass, self.root)
        self.assertEqual(len(devices), 2)
        first, second = module_entries(self.root)
        self.assertEqual(shared_device_info(first), shared_device_info(second))
        self.assertEqual(shared_device_info(first)['entry_type'], 'service')
        import importlib
        from types import ModuleType
        from tests.test_fbc_notifications import Entity, CoordinatorEntity
        sensor = ModuleType('homeassistant.components.sensor')
        sensor.SensorEntity = Entity
        entity = ModuleType('homeassistant.helpers.entity')
        entity.DeviceInfo = lambda **kw: kw
        entity.EntityCategory = SimpleNamespace(DIAGNOSTIC='diagnostic')
        dispatcher = ModuleType('homeassistant.helpers.dispatcher')
        dispatcher.async_dispatcher_connect = lambda *args: (lambda: None)
        events = ModuleType('homeassistant.helpers.event')
        events.async_track_state_change_event = lambda *args, **kw: (lambda: None)
        events.async_track_time_change = lambda *args, **kw: (lambda: None)
        self.harness.injected['homeassistant.helpers.update_coordinator'].CoordinatorEntity = CoordinatorEntity
        with patch.dict('sys.modules', {**self.harness.injected,
                'homeassistant.components': ModuleType('homeassistant.components'),
                'homeassistant.components.sensor': sensor, 'homeassistant.helpers.entity': entity,
                'homeassistant.helpers.dispatcher': dispatcher, 'homeassistant.helpers.event': events}):
            module = importlib.import_module('custom_components.ha_bems.sensor')
            sensors = [module.SeasonalSOCSensor(item) for item in (first, second)]
            self.assertEqual(sensors[0]._attr_device_info, sensors[1]._attr_device_info)
            self.assertEqual(sensors[0]._attr_unique_id, 'one_seasonal_soc_min_threshold')
            self.assertEqual(sensors[1]._attr_unique_id, 'two_seasonal_soc_min_threshold')
            self.assertNotEqual(sensors[0]._attr_name, sensors[1]._attr_name)

    async def test_imports_and_multiple_runtime_costs_use_flat_shared_references(self):
        from tests.test_forecast import records_for
        from tests.test_forecast_input import NOW
        from custom_components.ha_bems.import_registry import coordinators
        from custom_components.ha_bems.input_coordinator import ForecastImportCoordinator
        identifiers = {}
        for name, step, unit, field, value in (
            ('PV', 'import_pv_ac', 'W', 'power_w', 1000),
            ('Import', 'import_price', 'EUR/kWh', 'price_eur_per_kwh', .3),
            ('Export', 'export_price', 'EUR/kWh', 'price_eur_per_kwh', .1),
            ('Outdoor', 'import_temperature', '°C', 'temperature_c', 2),
        ):
            self.hass.state.attributes.update(unit_of_measurement=unit, forecast=[
                {'start': row['start'], 'end': row['end'], field: value} for row in records_for(NOW)])
            self.hass.state.state = str(value)
            flow = self.flow()
            await flow.async_step_add_import()
            result = await getattr(flow, 'async_step_' + step)({'name': name, 'forecast_entity': 'sensor.pv'})
            self.assertIn(result['step_id'], ('import_preview', 'import_value_preview'))
            await flow.async_step_import_preview({})
            item = next(item for item in module_entries(self.root) if item.title == name)
            identifiers[name] = item.entry_id
            coordinator = ForecastImportCoordinator(self.hass, item, self.root)
            await coordinator.async_refresh()
            self.assertTrue(coordinator.last_update_success)
            coordinators(self.hass)[item.entry_id] = coordinator
        for name, cop in [('System A', 2), ('System B', 4)]:
            flow = self.flow()
            await flow.async_step_add_helper()
            await flow.async_step_thermal_cost({'name': name, 'thermal_power_kw': 7, 'auxiliary_power_w': 0, 'cop_model': 'constant'})
            await flow.async_step_thermal_cop_constant({'constant_cop': cop})
            result = await flow.async_step_thermal_cost_inputs({
                'pv_import_entry_id': identifiers['PV'], 'import_price_entry_id': identifiers['Import'],
                'export_price_entry_id': identifiers['Export'], 'load_forecast_input': '',
                'feed_in_limit_enabled': False, 'feed_in_limit_w': 0, 'curtailed_price_eur_per_kwh': 0})
            self.assertEqual(result['step_id'], 'thermal_cost_preview')
            await flow.async_step_thermal_cost_preview({})
        self.assertEqual(len(self.root.data['items']), 6)
        self.assertEqual({record['title'] for record in self.root.data['items'].values()},
                         {'PV', 'Import', 'Export', 'Outdoor', 'System A', 'System B'})
