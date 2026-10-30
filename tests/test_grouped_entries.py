"""Grouping, identity-preserving migration and subentry lifecycle checks."""

from copy import deepcopy
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from tests.ha_stubs import modules, ConfigSubentry
from tests.test_importer import Hass, entry, HABEMSConfigFlow, HABEMSItemFlow
from tests.test_forecast_input import NOW
from custom_components.ha_bems.const import GROUP_TITLES
from custom_components.ha_bems.entries import module_entries


def parent(group, *children):
    result = entry(group, group=group)
    result.subentries = {child.subentry_id: child for child in children}
    result.callbacks = []
    result.async_on_unload = result.callbacks.append
    result.add_update_listener = lambda fn: (lambda: None)
    result.disabled_by = None
    return result


def child(group, module, identifier, **values):
    return ConfigSubentry(subentry_type=group, title=identifier, unique_id=identifier,
                          data={'module': module, 'item_id': identifier, **values})


class GroupFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_installation_and_duplicate_prevention(self):
        hass = Hass()
        hass.entries = []
        flow = HABEMSConfigFlow()
        flow.hass = hass
        self.assertEqual((await flow.async_step_user())['type'], 'form')
        result = await flow.async_step_user({})
        self.assertEqual(result['title'], 'HA-BEMS')
        self.assertEqual(result['data'], {'layout': 'flat_devices', 'items': {}})
        installation = entry('installation', **result['data'])
        hass.entries.append(installation)
        self.assertEqual((await flow.async_step_user())['reason'], 'already_configured')
        self.assertEqual(flow.async_get_supported_subentry_types(installation), {})
        self.assertTrue(flow.async_supports_options_flow(installation))

    async def test_add_multiple_helpers_then_rename_and_disable_one(self):
        hass = Hass()
        grouped = parent('entity_creator')
        hass.entries = [grouped]
        first_id = None
        for name in ('House A minimum SOC', 'House B minimum SOC'):
            flow = HABEMSItemFlow()
            flow.hass = hass
            flow.parent = grouped
            menu = await flow.async_step_user()
            self.assertEqual(menu['menu_options'], ['soc', 'load_profile', 'thermal_cost'])
            await flow.async_step_soc({'name': name})
            from custom_components.ha_bems.const import MONTHS
            result = await flow.async_step_monthly({month: 40 for month in MONTHS})
            subentry = ConfigSubentry(subentry_type='entity_creator', title=result['title'],
                                      unique_id=result['unique_id'], data=result['data'])
            grouped.subentries[subentry.subentry_id] = subentry
            self.assertEqual(result['data']['item_id'], result['unique_id'])
            if first_id is None:
                first_id = result['unique_id']
            else:
                self.assertNotEqual(first_id, result['unique_id'])
        first, second = list(grouped.subentries.values())
        saved_second = deepcopy(second.data)
        updates = []
        def update(_parent, subentry, **kwargs):
            updates.append(kwargs)
            subentry.title = kwargs['title']
            subentry.data = kwargs['data']
        hass.config_entries.async_update_subentry = update
        edit = HABEMSItemFlow()
        edit.hass = hass
        edit.parent = grouped
        edit.subentry = first
        self.assertEqual((await edit.async_step_reconfigure())['step_id'], 'reconfigure')
        result = await edit.async_step_reconfigure({'name': 'Renamed minimum', 'enabled': False})
        self.assertEqual(result['reason'], 'reconfigure_successful')
        self.assertEqual(first.title, 'Renamed minimum')
        self.assertEqual(first.data['item_id'], first_id)
        self.assertFalse(first.data['enabled'])
        self.assertEqual(second.data, saved_second)

    async def test_new_forecast_and_storage_subentries_stay_in_their_groups(self):
        hass = Hass()
        flow = HABEMSItemFlow()
        flow.hass = hass
        flow.parent = parent('entity_importer')
        self.assertIn('import_pv_dc', (await flow.async_step_user())['menu_options'])
        result = await flow.async_step_import_pv_ac({'name': 'AC roof', 'forecast_entity': 'sensor.pv'})
        self.assertEqual(result['step_id'], 'import_preview')
        result = await flow.async_step_import_preview({})
        self.assertEqual(result['data']['pv_power_type'], 'ac')
        self.assertEqual(result['data']['module'], 'pv_forecast_import')
        # Even an invalid direct flow invocation cannot save to another category.
        flow.parent = parent('electrical_storage')
        self.assertEqual(flow._finish('pv_forecast_import')['reason'], 'wrong_group')
        menu = await flow.async_step_user()
        self.assertEqual(menu['menu_options'], ['fbc'])


class MigrationHarness:
    def __init__(self, entries):
        self.hass = Hass()
        self.hass.entries = entries
        self.starts = []
        self.removed = []
        self.entities = {}
        self.devices = {}
        self.moves = []
        for item in entries:
            item.version = 1
            item.minor_version = 2
            item.disabled_by = None
            item.subentries = {}
            self.entities['sensor.' + item.entry_id] = SimpleNamespace(
                entity_id='sensor.' + item.entry_id, unique_id=item.entry_id + '_unchanged',
                config_entry_id=item.entry_id, config_subentry_id=None, device_id='device-' + item.entry_id)
            self.devices['device-' + item.entry_id] = SimpleNamespace(
                id='device-' + item.entry_id, config_entry_id=item.entry_id, config_subentry_id=None,
                area_id='control-room', name_by_user='Custom ' + item.title)
        manager = self.hass.config_entries
        manager.async_update_entry = self.update_entry
        manager.async_add_subentry = self.add_subentry
        manager.async_remove_subentry = self.remove_subentry
        manager.async_remove = self.remove_entry
        self.injected = modules()
        devices = ModuleType('homeassistant.helpers.device_registry')
        devices.async_get = lambda hass: self
        devices.async_entries_for_config_entry = lambda registry, identifier: [
            device for device in self.devices.values() if device.config_entry_id == identifier]
        entities = SimpleNamespace(
            async_get=lambda hass: self,
            async_entries_for_config_entry=lambda registry, identifier: [
                entity for entity in self.entities.values() if entity.config_entry_id == identifier])
        self.injected['homeassistant.helpers'].device_registry = devices
        self.injected['homeassistant.helpers'].entity_registry = entities
        start = ModuleType('homeassistant.helpers.start')
        start.async_at_started = lambda hass, callback: self.starts.append(callback)
        self.injected.update({'homeassistant.helpers.start': start,
                              'homeassistant.helpers.device_registry': devices})

    def update_entry(self, target, *, data, options, title=None, unique_id=None, minor_version):
        values = {'data': data, 'options': options, 'minor_version': minor_version}
        if title is not None:
            values['title'] = title
        if unique_id is not None:
            values['unique_id'] = unique_id
        for key, value in values.items():
            setattr(target, key, value)

    def add_subentry(self, target, subentry):
        assert subentry.unique_id is None or not any(
            existing.unique_id == subentry.unique_id for existing in target.subentries.values())
        target.subentries[subentry.subentry_id] = subentry

    def remove_subentry(self, target, identifier):
        # HA clears the old subentry's registrations during removal. The move
        # must already have changed ownership or the entities would be deleted.
        assert not any(entity.config_entry_id == target.entry_id and entity.config_subentry_id == identifier
                       for entity in self.entities.values())
        assert not any(device.config_entry_id == target.entry_id and device.config_subentry_id == identifier
                       for device in self.devices.values())
        target.subentries.pop(identifier)

    def async_update_entity(self, identifier, **values):
        self.moves.append(('entity', identifier))
        for key, value in values.items():
            setattr(self.entities[identifier], key, value)

    def async_update_device(self, identifier, *, new_config_entry_id, new_config_subentry_id):
        # Model HA's queued device-owner cleanup: entities must already belong
        # to the destination, or the move could cause their deletion.
        for entity in self.entities.values():
            if entity.device_id == identifier:
                assert entity.config_entry_id == new_config_entry_id
                assert entity.config_subentry_id == new_config_subentry_id
        self.moves.append(('device', identifier))
        self.devices[identifier].config_entry_id = new_config_entry_id
        self.devices[identifier].config_subentry_id = new_config_subentry_id

    async def remove_entry(self, identifier):
        assert not any(entity.config_entry_id == identifier for entity in self.entities.values())
        assert not any(device.config_entry_id == identifier for device in self.devices.values())
        self.hass.entries[:] = [item for item in self.hass.entries if item.entry_id != identifier]
        self.removed.append(identifier)
        return True

    async def migrate(self):
        with patch.dict('sys.modules', self.injected):
            from custom_components.ha_bems.migration import async_group_legacy_entries
            await async_group_legacy_entries(self.hass)


class GroupMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_identity_references_names_areas_and_disabled_state(self):
        old = [entry('minimum', module='seasonal_soc', monthly_min_soc_percent=[40] * 12),
               entry('load', module='non_flexible_load', annual_consumption_kwh=3500),
               entry('pv', forecast_entity='sensor.roof'),
               entry('battery-a', module='battery_fbc', pv_import_entry_id='pv',
                     load_forecast_input='generated:load', max_charge_power_w=5000),
               entry('battery-b', module='battery_fbc', pv_import_entry_id='pv', max_charge_power_w=7000)]
        harness = MigrationHarness(old)
        old[0].disabled_by = 'user'
        old[3].options = {'max_charge_power_w': 6500, 'output_limit_w': 1000}
        await harness.migrate()
        parents = {item.data['group']: item for item in harness.hass.entries if 'group' in item.data}
        self.assertEqual(set(parents), {'entity_creator', 'entity_importer', 'electrical_storage'})
        self.assertEqual({group: item.title for group, item in parents.items()}, {
            group: GROUP_TITLES[group] for group in parents})
        self.assertEqual(len(parents['electrical_storage'].subentries), 2)
        items = {item.entry_id: item for grouped in parents.values() for item in module_entries(grouped)}
        self.assertEqual(set(items), {'minimum', 'load', 'pv', 'battery-a', 'battery-b'})
        self.assertFalse(items['minimum'].data['enabled'])
        self.assertIsNone(parents['entity_creator'].disabled_by)
        self.assertEqual(items['battery-a'].data['max_charge_power_w'], 6500)
        self.assertNotIn('output_limit_w', items['battery-a'].data)
        self.assertEqual(items['battery-a'].data['pv_import_entry_id'], 'pv')
        self.assertEqual(items['battery-a'].data['load_forecast_input'], 'generated:load')
        for identifier, entity in harness.entities.items():
            self.assertEqual(entity.unique_id, identifier.removeprefix('sensor.') + '_unchanged')
            self.assertIsNotNone(entity.config_subentry_id)
        for device in harness.devices.values():
            self.assertEqual(device.area_id, 'control-room')
            self.assertTrue(device.name_by_user.startswith('Custom '))
        counts = {group: len(item.subentries) for group, item in parents.items()}
        await harness.migrate()  # Interrupted/repeated startup must not duplicate items.
        self.assertEqual(counts, {group: len(item.subentries) for group, item in parents.items()})
        self.assertEqual(harness.removed, [], 'Retired entries removed before startup completed')
        for callback in harness.starts:
            await callback(harness.hass)
        self.assertEqual(len(harness.hass.entries), 3)
        with patch.dict('sys.modules', harness.injected):
            from custom_components.ha_bems.import_registry import load_inputs, pv_imports
            self.assertEqual(pv_imports(harness.hass)[0].entry_id, 'pv')
            self.assertEqual(load_inputs(harness.hass)[0][0], 'generated:load')

    async def test_interrupted_registry_move_is_resumable(self):
        harness = MigrationHarness([entry('pv', forecast_entity='sensor.roof')])
        move = harness.async_update_device
        def fail_once(*args, **kwargs):
            harness.async_update_device = move
            raise RuntimeError('interrupted registry move')
        harness.async_update_device = fail_once
        with self.assertRaisesRegex(RuntimeError, 'interrupted'):
            await harness.migrate()
        self.assertEqual(harness.hass.entries[0].data['forecast_entity'], 'sensor.roof')
        await harness.migrate()
        grouped = harness.hass.entries[0]
        self.assertEqual(grouped.data['group'], 'entity_importer')
        self.assertEqual(len(grouped.subentries), 1)
        self.assertEqual(module_entries(grouped)[0].entry_id, 'pv')

    async def test_unsupported_version_leaves_every_entry_unchanged(self):
        harness = MigrationHarness([entry('pv', forecast_entity='sensor.roof')])
        harness.hass.entries[0].version = 2
        with self.assertRaises(ValueError):
            await harness.migrate()
        self.assertEqual(harness.hass.entries[0].subentries, {})
        self.assertEqual(harness.moves, [])

    async def test_entirely_disabled_category_retains_its_disabled_state(self):
        harness = MigrationHarness([entry('pv', forecast_entity='sensor.roof')])
        harness.hass.entries[0].disabled_by = 'user'
        await harness.migrate()
        grouped = harness.hass.entries[0]
        self.assertEqual(grouped.data['group'], 'entity_importer')
        self.assertEqual(grouped.disabled_by, 'user')
        self.assertFalse(module_entries(grouped)[0].enabled)


class GroupRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_platform_entities_are_attached_to_individual_subentries(self):
        import importlib
        from tests.test_fbc_notifications import Entity, CoordinatorEntity
        from tests.test_fbc_switch import SwitchEntity, RestoreEntity
        injected = modules()
        sensor = ModuleType('homeassistant.components.sensor')
        sensor.SensorEntity = Entity
        switches = ModuleType('homeassistant.components.switch')
        switches.SwitchEntity = SwitchEntity
        restore = ModuleType('homeassistant.helpers.restore_state')
        restore.RestoreEntity = RestoreEntity
        entity = ModuleType('homeassistant.helpers.entity')
        entity.DeviceInfo = lambda **kwargs: kwargs
        entity.EntityCategory = SimpleNamespace(DIAGNOSTIC='diagnostic')
        dispatcher = ModuleType('homeassistant.helpers.dispatcher')
        dispatcher.async_dispatcher_connect = lambda *args: (lambda: None)
        events = ModuleType('homeassistant.helpers.event')
        events.async_track_state_change_event = lambda *args, **kwargs: (lambda: None)
        events.async_track_time_change = lambda *args, **kwargs: (lambda: None)
        injected['homeassistant.helpers.update_coordinator'].CoordinatorEntity = CoordinatorEntity
        injected.update({'homeassistant.components': ModuleType('homeassistant.components'),
                         'homeassistant.components.sensor': sensor, 'homeassistant.components.switch': switches,
                         'homeassistant.helpers.restore_state': restore, 'homeassistant.helpers.entity': entity,
                         'homeassistant.helpers.dispatcher': dispatcher, 'homeassistant.helpers.event': events})
        hass = Hass()
        with patch.dict('sys.modules', injected):
            sensors = importlib.import_module('custom_components.ha_bems.sensor')
            switch = importlib.import_module('custom_components.ha_bems.switch')
            from custom_components.ha_bems.import_registry import planners
            children = [child('entity_creator', 'seasonal_soc', identifier) for identifier in ('minimum-a', 'minimum-b')]
            grouped = parent('entity_creator', *children)
            calls = []
            def add(entities, *, config_subentry_id):
                calls.append((entities[0], config_subentry_id))
            await sensors.async_setup_entry(hass, grouped, add)
            self.assertEqual({identifier for _, identifier in calls}, set(grouped.subentries))
            self.assertEqual({item._attr_unique_id for item, _ in calls}, {
                'minimum-a_seasonal_soc_min_threshold', 'minimum-b_seasonal_soc_min_threshold'})
            children = [child('electrical_storage', 'battery_fbc', identifier) for identifier in ('battery-a', 'battery-b')]
            grouped = parent('electrical_storage', *children)
            planners(hass).update({'battery-a': SimpleNamespace(), 'battery-b': SimpleNamespace()})
            calls.clear()
            await switch.async_setup_entry(hass, grouped, add)
            self.assertEqual({identifier for _, identifier in calls}, set(grouped.subentries))
            self.assertEqual({item._attr_unique_id for item, _ in calls}, {
                'battery-a_fbc_limitation_enabled', 'battery-b_fbc_limitation_enabled'})

    async def test_multiple_batteries_restore_before_plans_and_unload_after_last_removal(self):
        hass = Hass()
        hass.config = SimpleNamespace(time_zone='Europe/Vienna')
        children = [child('electrical_storage', 'battery_fbc', identifier, max_charge_power_w=5000)
                    for identifier in ('battery-a', 'battery-b')]
        grouped = parent('electrical_storage', *children)
        hass.entries = [grouped]
        calls = []
        injected = modules()
        platform = ModuleType('homeassistant.const')
        platform.Platform = SimpleNamespace(SENSOR='sensor', SWITCH='switch')
        dispatcher = ModuleType('homeassistant.helpers.dispatcher')
        dispatcher.async_dispatcher_send = lambda *args: None
        events = ModuleType('homeassistant.helpers.event')
        events.async_track_state_change_event = lambda *args: (lambda: None)
        injected.update({'homeassistant.const': platform, 'homeassistant.helpers.dispatcher': dispatcher,
                         'homeassistant.helpers.event': events})
        async def forward(_entry, platforms):
            from custom_components.ha_bems.import_registry import planners
            calls.append(platforms)
            for plan in planners(hass).values():
                if platforms == ['switch']:
                    plan.limitation_enabled = False
                else:
                    self.assertFalse(plan.limitation_enabled)
                    await plan.async_refresh()
        async def unload(_entry, platforms):
            self.assertEqual(set(platforms), {'sensor', 'switch'})
            return True
        hass.config_entries.async_forward_entry_setups = forward
        hass.config_entries.async_unload_platforms = unload
        with patch.dict('sys.modules', injected):
            from custom_components.ha_bems import async_setup_entry, async_unload_entry
            from custom_components.ha_bems.import_registry import planners
            await async_setup_entry(hass, grouped)
            self.assertEqual(calls, [['switch'], ['sensor']])
            self.assertEqual(set(planners(hass)), {'battery-a', 'battery-b'})
            grouped.subentries.clear()  # Deleting the last item still unloads its switch platform.
            self.assertTrue(await async_unload_entry(hass, grouped))
            for callback in grouped.callbacks:
                callback()
            self.assertEqual(planners(hass), {})

    async def test_disabled_input_rejects_retained_snapshot_before_reload(self):
        hass = Hass()
        original = await hass.register_snapshot()
        source = child('entity_importer', 'pv_forecast_import', 'pv-main', forecast_entity='sensor.pv')
        grouped = parent('entity_importer', source)
        hass.entries = [grouped]
        from tests.test_importer import read_pv_import
        self.assertEqual(len(read_pv_import(hass, {'pv_import_entry_id': 'pv-main'}, NOW)[0]), 289)
        source.data['enabled'] = False
        self.assertTrue(original.last_update_success)
        with self.assertRaisesRegex(ValueError, 'disabled'):
            read_pv_import(hass, {'pv_import_entry_id': 'pv-main'}, NOW)
