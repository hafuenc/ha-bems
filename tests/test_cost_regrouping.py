"""Move existing v0.6.0 cost helpers without changing entity/module identity."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from tests.test_grouped_entries import MigrationHarness, parent, child
from custom_components.ha_bems.entries import module_entries, group_for_module
from custom_components.ha_bems.const import MODULE_THERMAL_COST


def harness_with_cost(creator_exists=True, disabled=False):
    source = parent('thermal_storage')
    entries = [source, parent('entity_creator')] if creator_exists else [source]
    harness = MigrationHarness(entries)
    source.disabled_by = 'user' if disabled else None
    cost = child('thermal_storage', MODULE_THERMAL_COST, 'original-cost',
                 thermal_power_kw=7, cop_model='constant', constant_cop=3,
                 pv_import_entry_id='roof', import_price_entry_id='import',
                 export_price_entry_id='export', enabled=True)
    source.subentries[cost.subentry_id] = cost
    for registry in (harness.entities, harness.devices):
        for record in registry.values():
            if record.config_entry_id == source.entry_id:
                record.config_subentry_id = cost.subentry_id
    return harness, source, cost


class CostRegroupingTests(unittest.IsolatedAsyncioTestCase):
    async def test_move_to_existing_creator_and_keep_other_items_untouched(self):
        harness, source, old = harness_with_cost()
        creator = harness.hass.entries[1]
        other = child('entity_creator', 'non_flexible_load', 'load-profile')
        creator.subentries[other.subentry_id] = other
        settings = deepcopy(dict(old.data))
        original_entity = deepcopy(harness.entities['sensor.thermal_storage'])
        original_device = deepcopy(harness.devices['device-thermal_storage'])
        await harness.migrate()
        cost = next(sub for sub in creator.subentries.values() if sub.data.get('item_id') == 'original-cost')
        self.assertEqual(dict(cost.data), settings)
        self.assertEqual(cost.title, old.title)
        self.assertEqual(cost.subentry_type, 'entity_creator')
        self.assertEqual(module_entries(creator)[-1].entry_id, 'original-cost')
        self.assertEqual(source.subentries, {})
        self.assertIs(creator.subentries[other.subentry_id], other)
        entity = harness.entities[original_entity.entity_id]
        device = harness.devices[original_device.id]
        self.assertEqual(entity.unique_id, original_entity.unique_id)
        self.assertEqual(entity.config_entry_id, creator.entry_id)
        self.assertEqual(entity.config_subentry_id, cost.subentry_id)
        self.assertEqual(device.config_entry_id, creator.entry_id)
        self.assertEqual(device.name_by_user, original_device.name_by_user)
        self.assertEqual(device.area_id, original_device.area_id)
        counts = [len(entry.subentries) for entry in harness.hass.entries]
        await harness.migrate()
        self.assertEqual(counts, [len(entry.subentries) for entry in harness.hass.entries])

    async def test_repurpose_cost_category_when_no_creator_exists_and_resume_interrupted_move(self):
        harness, source, old = harness_with_cost(creator_exists=False)
        remove = harness.remove_subentry
        def fail_once(_target, _identifier):
            raise RuntimeError('Interrupted before removing old subentry')
        harness.hass.config_entries.async_remove_subentry = fail_once
        with self.assertRaisesRegex(RuntimeError, 'Interrupted'):
            await harness.migrate()
        self.assertEqual(len(source.subentries), 2)
        harness.hass.config_entries.async_remove_subentry = remove
        await harness.migrate()
        self.assertEqual(source.data, {'group': 'entity_creator'})
        self.assertEqual(source.title, 'BEMS entity creator')
        self.assertEqual(len(source.subentries), 1)
        cost = next(iter(source.subentries.values()))
        self.assertEqual(cost.data['item_id'], 'original-cost')
        self.assertEqual(cost.subentry_type, 'entity_creator')
        self.assertEqual(harness.entities['sensor.thermal_storage'].config_subentry_id, cost.subentry_id)
        await harness.migrate()
        self.assertEqual(len(source.subentries), 1)

    async def test_disabled_thermal_category_does_not_enable_moved_cost(self):
        harness, source, _old = harness_with_cost(disabled=True)
        await harness.migrate()
        creator = harness.hass.entries[1]
        cost = next(iter(creator.subentries.values()))
        self.assertFalse(cost.data['enabled'])
        self.assertFalse(module_entries(creator)[0].enabled)
        self.assertEqual(source.subentries, {})

    async def test_generic_cost_creator_and_separate_thermal_planner_translations(self):
        self.assertEqual(group_for_module(MODULE_THERMAL_COST), 'entity_creator')
        root = Path(__file__).resolve().parents[1] / 'custom_components/ha_bems'
        strings = json.loads((root / 'strings.json').read_text(encoding='utf-8'))
        self.assertEqual(strings, json.loads((root / 'translations/en.json').read_text(encoding='utf-8')))
        creator = strings['config_subentries']['entity_creator']['step']
        self.assertIn('thermal_cost', creator['user']['menu_options'])
        self.assertIn('thermal_cop_points', creator)
        self.assertIn('thermal_plan', strings['config_subentries']['thermal_storage']['step']['user']['menu_options'])
        self.assertNotIn('DHW', creator['thermal_cost']['description'])
        self.assertNotIn('UFH', creator['thermal_cost']['description'])
