"""FBC retains its engine while optionally consuming shared building load."""

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests.ha_stubs import modules
from tests.test_importer import HABEMSConfigFlow, HABEMSOptionsFlow, Hass, entry
from tests.test_forecast import records_for
from tests.test_forecast_input import NOW

with patch.dict('sys.modules', modules()):
    from custom_components.ha_bems.coordinator import BEMSCoordinator
    from custom_components.ha_bems.import_registry import coordinators, planners, read_load_input
from custom_components.ha_bems.fbc import calculate_fbc, FBCSettings
from custom_components.ha_bems.forecast import daily_forecast, surplus_forecast, validate_forecast


class LoadAwareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = Hass()
        self.hass.config = SimpleNamespace(time_zone='Europe/Vienna')
        self.pv = await self.hass.register_snapshot()
        self.rows = records_for(NOW)
        for i, row in enumerate(self.rows):
            row['power_w'] = [4000, 2000, 1000][i] if i < 3 else 0
        self.pv.data['forecast'] = self.rows
        self.parameters = dict(capacity_kwh=5, target_soc_percent=100,
                               charge_efficiency=1, max_charge_power_w=5000)
        self.config = {'pv_import_entry_id': 'pv-main', 'soc_entity': 'sensor.soc',
                       'seasonal_soc_entity': 'sensor.minimum', **self.parameters}
        self.states = {
            'sensor.soc': SimpleNamespace(state='80', attributes={'unit_of_measurement': '%'}),
            'sensor.minimum': SimpleNamespace(state='40', attributes={'unit_of_measurement': '%'}),
        }
        self.hass.states = SimpleNamespace(get=self.states.get)

    def plan(self, **config):
        return BEMSCoordinator(self.hass, entry('battery', module='battery_fbc',
                                               **{**self.config, **config}), 'battery_fbc')

    def add_load(self, kind='imported', powers=(500, 1500, 2000)):
        module = 'load_forecast_import' if kind == 'imported' else 'non_flexible_load'
        registered = entry('load-main', module=module)
        self.hass.entries.append(registered)
        rows = deepcopy(self.rows)
        for i, row in enumerate(rows):
            row['power_w'] = powers[i] if i < len(powers) else 0
        provider = SimpleNamespace(last_update_success=True, last_exception=None, data={
            'forecast': rows, 'source_updated_at': NOW.isoformat(), 'schema_version': 1,
        })
        (coordinators(self.hass) if kind == 'imported' else planners(self.hass))['load-main'] = provider
        return provider

    async def test_without_load_matches_unchanged_v1_engine(self):
        plan = self.plan()
        await plan.async_set_limitation_enabled(True)
        rows = daily_forecast(self.rows, NOW, ZoneInfo('Europe/Vienna'))
        expected = calculate_fbc([row['power_w'] for row in rows], step_hours=.25,
                                 current_soc_percent=80, current_month=10,
                                 seasonal_soc_min_threshold_percent=40,
                                 settings=FBCSettings(**self.parameters))
        self.assertTrue(plan.last_update_success)
        self.assertEqual(plan.data['value'], expected.charge_limit_w)
        self.assertEqual([row['power_w'] for row in plan.data['forecast']], list(expected.charge_profile_w))
        self.assertFalse(plan.data['load_consideration_active'])
        self.assertEqual(plan.data['calculation_input'], 'pv_generation')

    async def test_generated_and_imported_load_use_surplus_and_keep_negative_balance_zero(self):
        for kind in ('generated', 'imported'):
            with self.subTest(kind=kind):
                provider = self.add_load(kind)
                plan = self.plan(load_forecast_input=f'{kind}:load-main')
                await plan.async_set_limitation_enabled(True)
                surplus = surplus_forecast(self.rows, provider.data['forecast'])
                self.assertEqual([row['power_w'] for row in surplus[:3]], [3500, 500, 0])
                self.assertEqual(self.rows[2]['power_w'], 1000, 'Source PV mutated')
                self.assertEqual(plan.data['value'], 5000)  # All surplus is needed.
                self.assertEqual(plan.data['synthetic_limit_w'], 0)
                self.assertEqual(plan.data['required_energy_kwh'], 1)
                self.assertTrue(plan.data['load_consideration_active'])
                self.assertEqual(plan.data['calculation_input'], 'pv_surplus')
                self.assertIn('first_charge_equals_first_surplus', plan.data['override_reasons'])
                self.hass.entries.pop()

    async def test_selected_load_failure_and_removal_never_fall_back_to_pv_only(self):
        provider = self.add_load()
        plan = self.plan(load_forecast_input='imported:load-main')
        await plan.async_set_limitation_enabled(True)
        provider.last_update_success = False
        await plan.async_refresh()
        self.assertFalse(plan.last_update_success)
        self.assertIn('Building load', str(plan.last_exception))
        provider.last_update_success = True
        await plan.async_refresh()
        self.assertTrue(plan.last_update_success)
        coordinators(self.hass).pop('load-main')
        with self.assertRaisesRegex(ValueError, 'disabled'):
            read_load_input(self.hass, plan.config, NOW)
        self.hass.entries.pop()
        await plan.async_refresh()
        self.assertFalse(plan.last_update_success)
        self.assertIn('no longer exists', str(plan.last_exception))

    async def test_switch_off_bypasses_all_sources_and_on_requires_them_again(self):
        self.hass.states = SimpleNamespace(get=lambda entity: None)
        coordinators(self.hass).clear()
        plan = self.plan(load_forecast_input='imported:missing')
        await plan.async_set_limitation_enabled(False)
        self.assertTrue(plan.last_update_success)
        self.assertEqual(plan.data['value'], 5000)
        self.assertTrue(all(row['power_w'] == 5000 for row in plan.data['forecast']))
        self.assertEqual(plan.data['override_reasons'], ['disabled_by_switch'])
        self.assertFalse(plan.data['charging_limitation_enabled'])
        self.assertFalse(plan.data['load_consideration_active'])
        self.assertEqual(plan.data['forecast'][-1]['end'], '2026-10-27T23:00:00+00:00')
        await plan.async_set_limitation_enabled(True)
        self.assertFalse(plan.last_update_success)

    async def test_seasonal_safeguard_still_bypasses_missing_load_and_pv(self):
        self.states['sensor.soc'].state = '40'
        coordinators(self.hass).clear()
        plan = self.plan(load_forecast_input='imported:missing')
        await plan.async_set_limitation_enabled(True)
        self.assertEqual(plan.data['value'], 5000)
        self.assertEqual(plan.data['override_reasons'], ['at_or_below_seasonal_soc_min_threshold'])
        self.assertTrue(plan.data['charging_limitation_enabled'])

    async def test_running_calculation_cannot_overwrite_switch_off(self):
        plan = self.plan()
        original_executor = self.hass.async_add_executor_job
        async def switch_during_calculation(func, *args):
            result = await original_executor(func, *args)
            plan.limitation_enabled = False
            return result
        self.hass.async_add_executor_job = switch_during_calculation
        await plan.async_set_limitation_enabled(True)
        self.assertEqual(plan.data['value'], 5000)
        self.assertEqual(plan.data['override_reasons'], ['disabled_by_switch'])

    async def test_generated_load_covers_rolling_72_hours_and_is_directly_reusable(self):
        registered = entry('generated', module='non_flexible_load', annual_consumption_kwh=3500,
                           load_profile='constant', holiday_country='', holiday_subdivision='')
        self.hass.entries.append(registered)
        generator = BEMSCoordinator(self.hass, registered, 'non_flexible_load')
        planners(self.hass)['generated'] = generator
        await generator.async_refresh()
        self.assertTrue(generator.last_update_success, str(generator.last_exception))
        self.assertEqual(len(validate_forecast(generator.data['forecast'], NOW)), 289)
        rows, details = read_load_input(self.hass, {'load_forecast_input': 'generated:generated'}, NOW)
        self.assertEqual(len(rows), 289)
        self.assertTrue(details['load_consideration_active'])
        self.assertEqual(generator.data['schema_version'], 1)

    async def test_import_load_preview_rechecks_type_and_format_before_save(self):
        self.hass.states = SimpleNamespace(get=lambda entity: self.hass.state)
        flow = HABEMSConfigFlow()
        flow.hass = self.hass
        result = await flow.async_step_import_load({'name': 'Predicted demand', 'forecast_entity': 'sensor.load'})
        self.assertEqual(result['step_id'], 'import_preview')
        self.hass.state.attributes['forecast'] = self.hass.state.attributes['forecast'][:288]
        result = await flow.async_step_import_preview({})
        self.assertEqual(result['step_id'], 'import_load')
        self.assertEqual(result['errors']['base'], 'invalid_forecast')
        self.hass.state.attributes['forecast'] = records_for(NOW)
        await flow.async_step_import_load({'name': 'Predicted demand', 'forecast_entity': 'sensor.load'})
        result = await flow.async_step_import_preview({})
        self.assertEqual(result['data']['module'], 'load_forecast_import')
        imported = entry('load-options', module='load_forecast_import', forecast_entity='sensor.load')
        options = HABEMSOptionsFlow()
        options.hass = self.hass
        options.config_entry = imported
        self.assertEqual((await options.async_step_init())['step_id'], 'import_load')

    async def test_fbc_has_no_version_picker_and_previews_both_load_types(self):
        self.add_load('generated')
        self.add_load('imported')
        flow = HABEMSConfigFlow()
        flow.hass = self.hass
        result = await flow.async_step_fbc()
        self.assertNotIn('battery_strategy', {str(key) for key in result['data_schema'].schema})
        result = await flow.async_step_fbc_forecast()
        selectors = {str(key): value for key, value in result['data_schema'].schema.items()}
        references = {option['value'] for option in selectors['load_forecast_input'].config['options']}
        self.assertEqual(references, {'', 'generated:load-main', 'imported:load-main'})
        for kind in ('generated', 'imported'):
            result = await flow.async_step_fbc_forecast({
                'pv_import_entry_id': 'pv-main', 'load_forecast_input': f'{kind}:load-main'})
            self.assertEqual(result['step_id'], 'fbc_preview')
            self.assertEqual(result['description_placeholders']['load'], 'load-main')
        coordinators(self.hass)['load-main'].last_update_success = False
        result = await flow.async_step_fbc_battery(self.parameters)
        self.assertEqual(result['step_id'], 'fbc_forecast')
        self.assertEqual(result['errors']['base'], 'invalid_forecast')

    async def test_misaligned_load_intervals_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'match'):
            surplus_forecast(self.rows[:3], self.rows[1:3])
