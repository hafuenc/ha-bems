"""Entry splitting preserves identity and can resume after interrupted moves."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_flat_layout import FlatHarness
from tests.test_importer import entry, HABEMSConfigFlow, HABEMSOptionsFlow
from custom_components.ha_bems.const import DOMAIN, MONTHS
from custom_components.ha_bems.entries import module_entries


class SplitHarness(FlatHarness):
    def __init__(self):
        self.root = entry('root',layout='flat_devices',items={
            'load':{'title':'House load','data':{'module':'non_flexible_load','enabled':False}},
            'pv':{'title':'Roof AC','data':{'module':'pv_forecast_import','forecast_entity':'sensor.external'}},
            'battery':{'title':'Battery 1','data':{'module':'battery_fbc','pv_import_entry_id':'pv',
                'load_forecast_input':'generated:load'}},
            'heat':{'title':'DHW','data':{'module':'thermal_storage_milp','cost_entity':'sensor.cost'}}})
        super().__init__([self.root])
        self.root.unique_id='ha_bems_installation'
        self.entities.clear();self.devices.clear()
        for group in ('entity_creator','entity_importer'):
            self.async_get_or_create(config_entry_id='root',identifiers={(DOMAIN,'service:'+group)},
                name=group,config_subentry_id=None)
        for identifier in self.root.data['items']:
            if identifier in ('battery','heat'):
                device=self.async_get_or_create(config_entry_id='root',identifiers={(DOMAIN,identifier)},
                    name=identifier,config_subentry_id=None)
            else:
                device=self.devices['service-service:'+('entity_creator' if identifier=='load' else 'entity_importer')]
            device.area_id='control-room'
            self.entities['sensor.'+identifier]=SimpleNamespace(entity_id='sensor.'+identifier,
                platform=DOMAIN,unique_id=identifier+'_unchanged',config_entry_id='root',
                config_subentry_id=None,device_id=device.id,area_id=None,name='User '+identifier,
                disabled_by='user' if identifier=='load' else None)
        self.reloads=[]
        self.hass.config_entries.async_unload=self.unload
        self.hass.config_entries.async_reload=self.reload
        self.hass.config_entries.flow=SimpleNamespace(async_init=self.create)

    async def unload(self, identifier):return True
    async def reload(self, identifier):self.reloads.append(identifier);return True

    async def create(self, domain, *, context, data):
        assert context=={'source':'import'}
        destination=entry('new-'+str(len(self.hass.entries)),**data['data'])
        destination.unique_id=data['split_unique_id'];destination.title=data['title']
        destination.disabled_by=None;destination.version=1;destination.subentries={}
        self.hass.entries.append(destination)
        return {'type':'create_entry','result':destination}

    def async_update_device(self, identifier, **values):
        if 'area_id' in values:
            self.devices[identifier].area_id=values['area_id'];return
        return super().async_update_device(identifier,**values)

    async def split(self):
        with patch.dict('sys.modules',self.injected):
            from custom_components.ha_bems.split_migration import async_split_entry
            await async_split_entry(self.hass,self.root)


class SplitMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_entries_devices_references_and_entity_identities_survive(self):
        harness=SplitHarness()
        previous=deepcopy(harness.entities)
        await harness.split()
        self.assertEqual(len(harness.hass.entries),4)
        self.assertEqual(harness.root.title,'BEMS entity creator')
        self.assertEqual(set(harness.root.data['items']),{'load'})
        self.assertFalse(harness.root.data['items']['load']['data']['enabled'])
        self.assertNotIn('entry_split_pending',harness.root.data)
        self.assertEqual(len(harness.devices),4)
        self.assertEqual(harness.reloads[1],'root')  # Importer then creator then planners.
        for identifier,entity in harness.entities.items():
            old=previous[identifier]
            self.assertEqual(entity.entity_id,old.entity_id)
            self.assertEqual(entity.unique_id,old.unique_id)
            self.assertEqual(entity.name,old.name)
            self.assertEqual(entity.disabled_by,old.disabled_by)
            device=harness.devices[entity.device_id]
            self.assertEqual(device.config_entry_id,entity.config_entry_id)
            self.assertEqual(device.area_id,'control-room')
        self.assertEqual(harness.entities['sensor.battery'].device_id,previous['sensor.battery'].device_id)
        battery=next(item for root in harness.hass.entries for item in module_entries(root) if item.entry_id=='battery')
        self.assertEqual(battery.data['pv_import_entry_id'],'pv')
        self.assertEqual(battery.data['load_forecast_input'],'generated:load')

    async def test_interrupted_registry_move_resumes_without_duplicate_entries(self):
        harness=SplitHarness()
        real_update=harness.async_update_entity
        counter=0
        def interrupted(identifier,**values):
            nonlocal counter
            counter+=1
            if counter==3:raise RuntimeError('Power loss')
            return real_update(identifier,**values)
        with patch.object(harness,'async_update_entity',interrupted):
            with self.assertRaisesRegex(RuntimeError,'Power loss'):await harness.split()
        self.assertIn('entry_split_pending',harness.root.data)
        count=len(harness.hass.entries)
        await harness.split()
        self.assertEqual(len(harness.hass.entries),count)
        self.assertNotIn('entry_split_pending',harness.root.data)
        self.assertTrue(all(not item.data.get('entry_split_staging') for item in harness.hass.entries))

    async def test_unknown_modules_are_rejected_before_any_change(self):
        harness=SplitHarness();harness.root.data['items']['pv']['data']['module']='future_module'
        before=deepcopy(harness.root.data)
        with self.assertRaisesRegex(ValueError,'Unsupported'):await harness.split()
        self.assertEqual(harness.root.data,before)
        self.assertEqual(len(harness.hass.entries),1)


class SplitFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_menus_retain_layout_and_block_wrong_module(self):
        harness=SplitHarness();await harness.split()
        flow=HABEMSOptionsFlow();flow.hass=harness.hass;flow.config_entry=harness.root
        with patch.dict('sys.modules',harness.injected):
            menu=await flow.async_step_init()
            self.assertEqual(menu['menu_options'],['add_helper','edit_item','remove_item'])
            await flow.async_step_add_helper();await flow.async_step_soc({'name':'Extra SOC'})
            await flow.async_step_monthly({month:40 for month in MONTHS})
            self.assertEqual(harness.root.data['layout'],'separate_entries')
            self.assertEqual(harness.root.data['group'],'entity_creator')
            self.assertEqual(len(harness.root.data['items']),2)
            flow._draft={'name':'Invalid','module':'pv_forecast_import'}
            self.assertEqual(flow._finish('pv_forecast_import')['reason'],'wrong_group')

    async def test_each_new_planner_gets_its_own_entry(self):
        harness=SplitHarness()
        for name in ('One','Two'):
            flow=HABEMSConfigFlow();flow.hass=harness.hass
            await flow.async_step_user();await flow.async_step_add_planner()
            flow._draft={'name':name}
            result=flow._finish('battery_fbc')
            self.assertEqual(result['title'],name)
            self.assertEqual(result['data']['layout'],'separate_entries')
            self.assertEqual(len(result['data']['items']),1)
            self.assertNotIn('group',result['data'])

    async def test_editing_one_planner_preserves_its_item_identity_and_other_entries(self):
        harness=SplitHarness();await harness.split()
        planner=next(root for root in harness.hass.entries if root.title=='Battery 1')
        originals={root.entry_id:deepcopy(root.data) for root in harness.hass.entries if root!=planner}
        flow=HABEMSOptionsFlow();flow.hass=harness.hass;flow.config_entry=planner
        self.assertEqual((await flow.async_step_init())['step_id'],'item_settings')
        await flow.async_step_item_settings({'name':'Renamed battery','enabled':False})
        self.assertEqual(planner.title,'Renamed battery')
        self.assertEqual(set(planner.data['items']),{'battery'})
        self.assertEqual(planner.data['layout'],'separate_entries')
        self.assertFalse(planner.data['items']['battery']['data']['enabled'])
        for root in harness.hass.entries:
            if root!=planner:self.assertEqual(root.data,originals[root.entry_id])
