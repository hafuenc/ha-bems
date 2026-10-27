"""Shared input acceptance, expiry, options and setup blocking tests."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.ha_stubs import modules, UpdateFailed
from tests.test_forecast import records_for
from tests.test_forecast_input import NOW

with patch.dict('sys.modules', modules()):
    from custom_components.ha_bems.config_flow import HABEMSConfigFlow, HABEMSOptionsFlow
    from custom_components.ha_bems.input_coordinator import PVImportCoordinator
    from custom_components.ha_bems.import_registry import coordinators, read_pv_import


def entry(identifier, module='pv_forecast_import', **config):
    return SimpleNamespace(entry_id=identifier, title=identifier, domain='ha_bems',
                           data={'module': module, **config}, options={})


class Hass:
    def __init__(self):
        self.data = {}
        self.entries = [entry('pv-main', forecast_entity='sensor.pv', forecast_max_age_minutes=180)]
        self.config_entries = SimpleNamespace(async_entries=lambda domain: self.entries)
        self.state = SimpleNamespace(entity_id='sensor.pv', state='0', attributes={
            'schema_version': 1, 'unit_of_measurement': 'W', 'forecast': records_for(NOW),
            'source_updated_at': (NOW - timedelta(minutes=60)).isoformat()})
        self.states = SimpleNamespace(get=lambda entity: self.state)

    async def async_add_executor_job(self, func, *args):
        return func(*args)

    async def register_snapshot(self):
        coordinator = PVImportCoordinator(self, self.entries[0])
        coordinator.data = await coordinator._async_update_data()
        coordinator.last_update_success = True
        coordinator.last_exception = None
        coordinators(self)['pv-main'] = coordinator
        return coordinator


class SharedInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = Hass()
        self.coordinator = await self.hass.register_snapshot()
        self.config = {'pv_import_entry_id': 'pv-main'}

    async def test_multiple_consumers_use_same_registered_input(self):
        rows_a, details_a = read_pv_import(self.hass, self.config, NOW)
        rows_b, details_b = read_pv_import(self.hass, dict(self.config), NOW)
        self.assertEqual(details_a['pv_import_entry_id'], details_b['pv_import_entry_id'])
        self.assertIs(rows_a[0], rows_b[0])
        self.assertEqual(len(rows_a), 289)

    async def test_source_age_does_not_reject_cache_but_horizon_still_expires(self):
        self.coordinator.config['forecast_max_age_minutes'] = 60
        self.assertEqual(len(read_pv_import(self.hass, self.config, NOW + timedelta(seconds=1))[0]), 289)
        with self.assertRaisesRegex(ValueError, '72 hours'):
            read_pv_import(self.hass, self.config, NOW + timedelta(minutes=8))

    async def test_disable_delete_reload_and_invalid_import_never_use_old_data(self):
        self.coordinator.last_update_success = False
        self.coordinator.input_status = 'invalid'
        self.coordinator.last_exception = ValueError('gap')
        with self.assertRaisesRegex(ValueError, 'invalid'):
            read_pv_import(self.hass, self.config, NOW)
        coordinators(self.hass).clear()
        with self.assertRaisesRegex(ValueError, 'not loaded'):
            read_pv_import(self.hass, self.config, NOW)
        replacement = await self.hass.register_snapshot()
        self.assertIsNot(replacement, self.coordinator)
        self.assertEqual(len(read_pv_import(self.hass, self.config, NOW)[0]), 289)
        self.hass.entries.clear()
        with self.assertRaisesRegex(ValueError, 'Register a PV'):
            read_pv_import(self.hass, self.config, NOW)

    async def test_legacy_direct_source_has_no_silent_fallback(self):
        with self.assertRaisesRegex(ValueError, 'Register a PV'):
            read_pv_import(self.hass, {'forecast_entity': 'sensor.pv'}, NOW)

    async def test_coordinator_distinguishes_health_and_recovers(self):
        attributes = deepcopy(self.hass.state.attributes)
        for expected, state, attrs in [
            ('unavailable', 'unavailable', attributes),
            ('invalid', '0', {**attributes, 'forecast': attributes['forecast'][:288]})]:
            self.hass.state.state = state
            self.hass.state.attributes = attrs
            with self.assertRaises(UpdateFailed):
                await self.coordinator._async_update_data()
            self.assertEqual(self.coordinator.input_status, expected)
        self.hass.state.state = '0'
        self.hass.state.attributes = attributes
        self.assertEqual(len((await self.coordinator._async_update_data())['forecast']), 289)
        self.assertEqual(self.coordinator.input_status, 'ready')

    async def test_changed_failure_status_is_not_suppressed_by_ha(self):
        seen = []
        self.coordinator.async_add_listener(lambda: seen.append(self.coordinator.input_status))
        attrs = deepcopy(self.hass.state.attributes)
        self.hass.state.state = 'unavailable'
        await self.coordinator.async_refresh()
        self.hass.state.state = '0'
        self.hass.state.attributes['forecast'] = attrs['forecast'][:288]
        await self.coordinator.async_refresh()
        self.hass.state.attributes = {**attrs, 'source_updated_at': '2020-01-01T00:00:00Z'}
        await self.coordinator.async_refresh()
        self.assertEqual(seen, ['unavailable', 'invalid', 'ready'])


class ImporterFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = Hass()
        await self.hass.register_snapshot()
        self.flow = HABEMSConfigFlow()
        self.flow.hass = self.hass

    async def test_import_test_preview_then_registration(self):
        result = await self.flow.async_step_import_pv({'name': 'Shared solar',
            'forecast_entity': 'sensor.pv', 'forecast_max_age_minutes': 180})
        self.assertEqual(result['step_id'], 'import_preview')
        result = await self.flow.async_step_import_preview({})
        self.assertEqual(result['type'], 'create_entry')
        self.assertEqual(result['data']['module'], 'pv_forecast_import')
        self.assertEqual(result['title'], 'Shared solar')

    async def test_source_changed_during_preview_blocks_save(self):
        await self.flow.async_step_import_pv({'name': 'Shared solar',
            'forecast_entity': 'sensor.pv', 'forecast_max_age_minutes': 180})
        self.hass.state.attributes['forecast'] = self.hass.state.attributes['forecast'][:288]
        result = await self.flow.async_step_import_preview({})
        self.assertEqual(result['step_id'], 'import_pv')
        self.assertEqual(result['errors']['base'], 'invalid_forecast')

    async def test_fbc_uses_import_picker_and_blocks_missing_or_invalid_input(self):
        result = await self.flow.async_step_fbc_forecast({'pv_import_entry_id': 'pv-main'})
        self.assertEqual(result['step_id'], 'fbc_preview')
        coordinators(self.hass)['pv-main'].last_update_success = False
        result = await self.flow.async_step_fbc_forecast({'pv_import_entry_id': 'pv-main'})
        self.assertEqual(result['errors']['base'], 'invalid_forecast')
        self.hass.entries.clear()
        result = await self.flow.async_step_fbc_forecast()
        self.assertEqual(result['errors']['base'], 'no_pv_import')

    async def test_import_options_preserve_registration_id_and_consumers(self):
        options = HABEMSOptionsFlow()
        options.hass = self.hass
        options.config_entry = self.hass.entries[0]
        result = await options.async_step_import_pv({'forecast_entity': 'sensor.pv',
                                                    'forecast_max_age_minutes': 120})
        self.assertEqual(result['step_id'], 'import_preview')
        result = await options.async_step_import_preview({})
        self.assertNotIn('forecast_max_age_minutes', result['data'])
        self.assertEqual(options.config_entry.entry_id, 'pv-main')


if __name__ == '__main__':
    unittest.main()
