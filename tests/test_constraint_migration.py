"""Preserve legacy permission interpretation when moving it into MILP setup."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from tests.test_flat_layout import FlatHarness
from tests.test_grouped_entries import parent, child
from tests.test_importer import entry
from custom_components.ha_bems.constraint_migration import migrate_constraint_settings


class ConstraintMigrationTests(unittest.TestCase):
    def make_split(self):
        imports=entry('imports',layout='separate_entries',group='entity_importer',items={
            'busy':{'title':'Busy','data':{'module':'operation_constraint_import','operation_meaning':'running'}},
            'permit':{'title':'Permitted','data':{'module':'operation_constraint_import','operation_meaning':'allowed'}}})
        planners=[]
        for identifier,reference in [('ufh','busy'),('other','permit')]:
            planners.append(entry(identifier,layout='separate_entries',items={identifier:{'title':identifier,
                'data':{'module':'thermal_storage_milp','constraint_entry_id':reference,'storage_model_revision':'keep-me'}}}))
        harness=FlatHarness([imports,*planners])
        return harness,imports,planners

    def test_split_entries_preserve_inversion_per_consumer_and_registry_identity(self):
        harness,imports,planners=self.make_split()
        previous=deepcopy(harness.entities)
        with patch.dict('sys.modules',harness.injected):
            migrate_constraint_settings(harness.hass)
        for planner,reference,mode in zip(planners,['busy','permit'],['inverted','direct']):
            data=planner.data['items'][planner.entry_id]['data']
            self.assertEqual(data['constraint_source'],'imported:'+reference)
            self.assertEqual(data['constraint_mode'],mode)
            self.assertNotIn('constraint_entry_id',data)
            self.assertEqual(data['storage_model_revision'],'keep-me')
        for record in imports.data['items'].values():
            self.assertNotIn('operation_meaning',record['data'])
        self.assertEqual(harness.entities,previous)
        snapshot=[deepcopy(item.data) for item in harness.hass.entries]
        with patch.dict('sys.modules',harness.injected):
            migrate_constraint_settings(harness.hass)
        self.assertEqual([item.data for item in harness.hass.entries],snapshot)

    def test_interruption_before_importer_update_does_not_lose_the_old_inversion(self):
        harness,imports,planners=self.make_split()
        real_update=harness.hass.config_entries.async_update_entry
        def interrupt(target,**values):
            if target is imports:raise RuntimeError('Power loss')
            return real_update(target,**values)
        with patch.dict('sys.modules',harness.injected):
            with patch.object(harness.hass.config_entries,'async_update_entry',interrupt):
                with self.assertRaisesRegex(RuntimeError,'Power loss'):
                    migrate_constraint_settings(harness.hass)
            self.assertEqual(planners[0].data['items']['ufh']['data']['constraint_mode'],'inverted')
            self.assertIn('operation_meaning',imports.data['items']['busy']['data'])
            migrate_constraint_settings(harness.hass)
        self.assertEqual(planners[0].data['items']['ufh']['data']['constraint_mode'],'inverted')

    def test_flat_pending_journal_and_grouped_and_standalone_layouts_are_normalized(self):
        imports=entry('busy',module='operation_constraint_import',operation_meaning='running')
        flat=entry('flat',layout='flat_devices',items={
            'ufh':{'title':'UFH','data':{'module':'thermal_storage_milp','constraint_entry_id':'busy'}}})
        flat.data['entry_split_pending']=deepcopy(flat.data['items'])
        grouped=parent('thermal_storage')
        harness=FlatHarness([imports,flat,grouped])
        sub=child('thermal_storage','thermal_storage_milp','sub',constraint_entry_id='busy')
        grouped.subentries[sub.subentry_id]=sub
        harness.hass.config_entries.async_update_subentry=lambda owner,item,**kwargs:setattr(item,'data',kwargs['data'])
        with patch.dict('sys.modules',harness.injected):
            migrate_constraint_settings(harness.hass)
        for key in ('items','entry_split_pending'):
            self.assertEqual(flat.data[key]['ufh']['data']['constraint_mode'],'inverted')
        self.assertEqual(sub.data['constraint_source'],'imported:busy')
        self.assertEqual(sub.data['constraint_mode'],'inverted')
        self.assertNotIn('operation_meaning',imports.data)
