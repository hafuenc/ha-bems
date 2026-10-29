"""Typed imports preserve price/temperature semantics and FBC source choice."""

from copy import deepcopy
from datetime import timedelta
import unittest
from unittest.mock import patch

from tests.ha_stubs import modules
from tests.test_importer import Hass, entry, HABEMSConfigFlow, HABEMSOptionsFlow
from tests.test_forecast_input import NOW
from tests import test_fbc_load
from custom_components.ha_bems.const import (
    MODULE_IMPORT_PRICE, MODULE_EXPORT_PRICE, MODULE_OUTDOOR_TEMP,
)
with patch.dict('sys.modules', modules()):
    from custom_components.ha_bems.forecast_input import async_read_forecast
    from custom_components.ha_bems.input_coordinator import ForecastImportCoordinator
    from custom_components.ha_bems.import_registry import coordinators, read_forecast_import, read_pv_import


TYPES = (
    (MODULE_IMPORT_PRICE, 'import_price', 'EUR/kWh', 'price_eur_per_kwh', -0.12),
    (MODULE_EXPORT_PRICE, 'export_price', 'EUR/kWh', 'price_eur_per_kwh', -0.04),
    (MODULE_OUTDOOR_TEMP, 'import_temperature', '°C', 'temperature_c', -12.5),
)


def scalar_source(hass, unit, key, value):
    hass.state.state = str(value)
    hass.state.attributes['unit_of_measurement'] = unit
    hass.state.attributes['forecast'] = [
        {'start': row['start'], 'end': row['end'], key: value}
        for row in hass.state.attributes['forecast']
    ]


class TypedImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_scalar_type_accepts_negative_values_and_correct_preview(self):
        for module, step, unit, key, value in TYPES:
            with self.subTest(module=module):
                hass = Hass()
                scalar_source(hass, unit, key, value)
                flow = HABEMSConfigFlow()
                flow.hass = hass
                result = await getattr(flow, 'async_step_' + step)({
                    'name': step, 'forecast_entity': 'sensor.pv'})
                self.assertEqual(result['step_id'], 'import_value_preview')
                self.assertEqual(result['description_placeholders']['unit'], unit)
                self.assertNotIn('energy', result['description_placeholders'])
                result = await flow.async_step_import_value_preview({})
                self.assertEqual(result['data']['module'], module)
                registered = entry(step, module=module, forecast_entity='sensor.pv')
                hass.entries.append(registered)
                coordinator = ForecastImportCoordinator(hass, registered)
                await coordinator.async_refresh()
                coordinators(hass)[step] = coordinator
                rows, details = read_forecast_import(hass, step, module, NOW)
                self.assertEqual(rows[0][key], value)
                self.assertEqual(details['forecast_unit'], unit)
                with self.assertRaisesRegex(ValueError, '72 hours'):
                    read_forecast_import(hass, step, module, NOW + timedelta(minutes=8))
                # A retained good snapshot must not survive a source failure.
                hass.state.state = 'unavailable'
                await coordinator.async_refresh()
                with self.assertRaisesRegex(ValueError, 'unavailable'):
                    read_forecast_import(hass, step, module, NOW)

    async def test_wrong_units_nonfinite_values_and_missing_fields_rejected(self):
        for module, _step, unit, key, value in TYPES:
            hass = Hass()
            scalar_source(hass, unit, key, value)
            good = deepcopy(hass.state.attributes)
            config = {'module': module, 'forecast_entity': 'sensor.pv'}
            for bad in ('wrong_unit', 'nan', 'bool', 'missing', 'gap', 'short'):
                with self.subTest(module=module, failure=bad):
                    hass.state.attributes = deepcopy(good)
                    if bad == 'wrong_unit':
                        hass.state.attributes['unit_of_measurement'] = 'kWh'
                    elif bad == 'missing':
                        del hass.state.attributes['forecast'][0][key]
                    elif bad == 'gap':
                        del hass.state.attributes['forecast'][20]
                    elif bad == 'short':
                        hass.state.attributes['forecast'].pop()
                    else:
                        hass.state.attributes['forecast'][0][key] = float('nan') if bad == 'nan' else True
                    with self.assertRaises(ValueError):
                        await async_read_forecast(hass, config)

    async def test_preview_revalidation_and_options_for_each_scalar_type(self):
        for module, step, unit, key, value in TYPES:
            hass = Hass()
            scalar_source(hass, unit, key, value)
            options = HABEMSOptionsFlow()
            options.hass = hass
            options.config_entry = entry(step, module=module, forecast_entity='sensor.pv')
            self.assertEqual((await options.async_step_init())['step_id'], step)
            await getattr(options, 'async_step_' + step)({'forecast_entity': 'sensor.pv'})
            hass.state.attributes['forecast'].pop()
            result = await options.async_step_import_value_preview({})
            self.assertEqual(result['step_id'], step)
            self.assertEqual(result['errors']['base'], 'invalid_forecast')

    async def test_explicit_pv_types_menu_options_and_legacy_registration(self):
        hass = Hass()
        await hass.register_snapshot()
        flow = HABEMSConfigFlow()
        flow.hass = hass
        from tests.test_importer import HABEMSItemFlow
        child = HABEMSItemFlow()
        child.parent = entry('imports', group='entity_importer')
        menu = await child.async_step_user()
        self.assertEqual(set(menu['menu_options']), {
            'import_pv_ac', 'import_pv_dc', 'import_load', 'import_price',
            'export_price', 'import_temperature'})
        for basis in ('ac', 'dc'):
            result = await getattr(flow, 'async_step_import_pv_' + basis)({
                'name': 'PV ' + basis, 'forecast_entity': 'sensor.pv'})
            self.assertEqual(result['step_id'], 'import_preview')
            result = await flow.async_step_import_preview({})
            self.assertEqual(result['data']['pv_power_type'], basis)
            hass.entries[0].options['pv_power_type'] = basis
            rows, details = read_pv_import(hass, {'pv_import_entry_id': 'pv-main'}, NOW)
            self.assertEqual(details['pv_power_type'], basis)
            self.assertEqual(len(rows), 289)
        hass.entries[0].options.clear()
        self.assertEqual(read_pv_import(hass, {'pv_import_entry_id': 'pv-main'}, NOW)[1]['pv_power_type'], 'unspecified')
        options = HABEMSOptionsFlow()
        options.hass = hass
        options.config_entry = hass.entries[0]
        result = await options.async_step_init()
        fields = {str(key): value for key, value in result['data_schema'].schema.items()}
        self.assertIn('pv_power_type', fields)
        flow = HABEMSConfigFlow()
        flow.hass = hass
        result = await flow.async_step_import_pv({'name': 'PV', 'forecast_entity': 'sensor.pv'})
        self.assertEqual(result['errors']['base'], 'invalid_forecast')


class PVChoiceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_fbc_load.LoadAwareTests.asyncSetUp
    plan = test_fbc_load.LoadAwareTests.plan

    async def test_fbc_uses_selected_ac_or_dc_values_and_reports_basis(self):
        dc_entry = entry('pv-dc', forecast_entity='sensor.dc', pv_power_type='dc')
        self.hass.entries.append(dc_entry)
        self.hass.entries[0].options['pv_power_type'] = 'ac'
        dc = deepcopy(self.pv.data)
        for row in dc['forecast']:
            row['power_w'] *= 2
        from types import SimpleNamespace
        coordinators(self.hass)['pv-dc'] = SimpleNamespace(
            data=dc, last_update_success=True, last_exception=None)
        limits = []
        for identifier, basis in [('pv-main', 'ac'), ('pv-dc', 'dc')]:
            plan = self.plan(pv_import_entry_id=identifier)
            await plan.async_set_limitation_enabled(True)
            self.assertTrue(plan.last_update_success, str(plan.last_exception))
            self.assertEqual(plan.data['pv_power_type'], basis)
            limits.append(plan.data['synthetic_limit_w'])
        self.assertNotEqual(*limits)
        flow = HABEMSConfigFlow()
        flow.hass = self.hass
        form = await flow.async_step_fbc_forecast()
        fields = {str(key): value for key, value in form['data_schema'].schema.items()}
        labels = [item['label'] for item in fields['pv_import_entry_id'].config['options']]
        self.assertIn('pv-main (AC)', labels)
        self.assertIn('pv-dc (DC)', labels)
