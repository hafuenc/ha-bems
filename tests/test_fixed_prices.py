"""Fixed and forecast tariff combinations use identical allocation arithmetic."""

from datetime import timedelta
from itertools import product
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tests.ha_stubs import modules
from tests.test_importer import entry, HABEMSConfigFlow
from tests.test_forecast_input import NOW
from tests.test_thermal_cost import CONFIG, rows
from tests.test_thermal_setup import INPUTS, thermal_hass
from custom_components.ha_bems.const import (
    CONF_EEG_PRICE, CONF_EEG_ENERGY, MODULE_EEG_PRICE, MODULE_EEG_ENERGY,
    MODULE_THERMAL_COST, THERMAL_PRICE_INPUTS,
)
from custom_components.ha_bems.thermal_cost import validate_settings
with patch.dict('sys.modules', modules()):
    from custom_components.ha_bems.thermal_coordinator import (
        ThermalCostCoordinator, thermal_references, read_cost_inputs,
    )
    from custom_components.ha_bems.import_registry import coordinators


class FixedPriceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        context = patch.dict('sys.modules', modules())
        context.start()
        self.addCleanup(context.stop)
        self.hass = thermal_hass()
        self.config = {**CONFIG, **INPUTS, 'eeg_enabled': True,
                       CONF_EEG_PRICE: 'eeg-price', CONF_EEG_ENERGY: 'eeg-energy'}
        for identifier, module, key, value in (
            ('eeg-price', MODULE_EEG_PRICE, 'price_eur_per_kwh', .4),
            ('eeg-energy', MODULE_EEG_ENERGY, 'energy_kwh', .125)):
            self.hass.entries.append(entry(identifier, module=module))
            coordinators(self.hass)[identifier] = SimpleNamespace(last_update_success=True,
                data={'forecast': rows(value, key), 'source_updated_at': NOW.isoformat()})

    async def calculate(self, config):
        plan = ThermalCostCoordinator(self.hass, entry('cost', module=MODULE_THERMAL_COST, **config))
        await plan.async_refresh()
        self.assertTrue(plan.last_update_success, str(plan.last_exception))
        return plan

    async def test_all_eight_combinations_match_forecast_only_results(self):
        baseline = await self.calculate(self.config)
        for modes in product(('constant', 'forecast'), repeat=3):
            with self.subTest(modes=modes):
                config = dict(self.config)
                for (prefix, key, _module), mode, price in zip(THERMAL_PRICE_INPUTS, modes, (.3, .1, .4)):
                    config[f'{prefix}_price_source'] = mode
                    config[f'{prefix}_price_eur_per_kwh'] = price if mode == 'constant' else 'ignored'
                    if mode == 'constant':
                        config[key] = 'removed-and-ignored'
                result = await self.calculate(config)
                self.assertEqual(result.data['forecast'], baseline.data['forecast'])
                self.assertAlmostEqual(result.data['value'], .1125)
                for prefix, key, _module in THERMAL_PRICE_INPUTS:
                    self.assertEqual(result.data[f'{prefix}_price_source'], config[f'{prefix}_price_source'])
                    if config[f'{prefix}_price_source'] == 'constant':
                        self.assertIsNone(result.data[key])
                        self.assertNotIn('removed-and-ignored', thermal_references(config))

    async def test_fixed_tariffs_need_no_price_imports_or_entry_ids(self):
        config = dict(self.config)
        for (prefix, key, _module), price in zip(THERMAL_PRICE_INPUTS, (.3, .1, .4)):
            config[f'{prefix}_price_source'] = 'constant'
            config[f'{prefix}_price_eur_per_kwh'] = price
            del config[key]
        self.hass.entries = [item for item in self.hass.entries if item.entry_id in ('pv-main', 'eeg-energy')]
        for identifier in ('import', 'export', 'eeg-price'):
            coordinators(self.hass).pop(identifier)
        result = await self.calculate(config)
        self.assertEqual(result.data['source_updated_at'], (NOW-timedelta(hours=1)).isoformat())
        self.assertEqual(set(thermal_references(config)), {'pv-main', 'eeg-energy'})
        inputs, _ = read_cost_inputs(self.hass, config, NOW)
        for forecast, price in ((inputs[1], .3), (inputs[2], .1), (inputs[6], .4)):
            self.assertEqual(len(forecast), 289)
            self.assertTrue(all(row['price_eur_per_kwh'] == price for row in forecast))
            self.assertEqual(forecast[-1]['end'], inputs[0][-1]['end'])

    async def test_zero_and_negative_fixed_prices_are_preserved(self):
        config = {**self.config, 'import_price_source': 'constant', 'export_price_source': 'constant',
                  'eeg_price_source': 'constant', 'import_price_eur_per_kwh': 0,
                  'export_price_eur_per_kwh': 0, 'eeg_price_eur_per_kwh': 0}
        self.assertEqual((await self.calculate(config)).data['value'], 0)
        for prefix in ('import', 'export', 'eeg'):
            negative = {**config, f'{prefix}_price_eur_per_kwh': -.2}
            self.assertLess((await self.calculate(negative)).data['value'], 0)

    async def test_invalid_or_missing_fixed_prices_rejected_and_inactive_values_ignored(self):
        for prefix, _key, _module in THERMAL_PRICE_INPUTS:
            for value in (None, True, float('inf'), float('nan'), '0.3'):
                with self.subTest(prefix=prefix, value=value), self.assertRaises(ValueError):
                    validate_settings({**self.config, f'{prefix}_price_source': 'constant',
                                       f'{prefix}_price_eur_per_kwh': value})
            with self.assertRaises(ValueError):
                validate_settings({**self.config, f'{prefix}_price_source': 'constant'})
            with self.assertRaises(ValueError):
                validate_settings({**self.config, f'{prefix}_price_source': 'unexpected'})
        config = {**CONFIG, **INPUTS, 'eeg_price_source': 'unexpected', 'eeg_price_eur_per_kwh': 'ignored',
                  'import_price_eur_per_kwh': float('nan')}
        await self.calculate(config)

    async def test_switch_back_to_forecast_requires_valid_forecast(self):
        config = {**self.config, 'import_price_source': 'constant', 'import_price_eur_per_kwh': .3,
                  'import_price_entry_id': 'missing'}
        plan = await self.calculate(config)
        plan.config['import_price_source'] = 'forecast'
        await plan.async_refresh()
        self.assertFalse(plan.last_update_success)
        plan.config['import_price_entry_id'] = 'import'
        await plan.async_refresh()
        self.assertTrue(plan.last_update_success)
        self.assertEqual(plan.data['source_updated_at'], (NOW-timedelta(hours=2)).isoformat())

    async def test_setup_preview_save_and_reconfigure_preserve_source_modes(self):
        config = {**self.config, 'name': 'Fixed tariffs'}
        for (prefix, key, _module), price in zip(THERMAL_PRICE_INPUTS, (.3, .1, .4)):
            config[f'{prefix}_price_source'] = 'constant'
            config[f'{prefix}_price_eur_per_kwh'] = price
            config.pop(key)
        flow = HABEMSConfigFlow()
        flow.hass = self.hass
        flow._draft = config
        form = await flow.async_step_thermal_cost_inputs()
        fields = {str(key) for key in form['data_schema'].schema}
        form['data_schema']({key: value for key, value in config.items() if key in fields})
        for prefix, key, _module in THERMAL_PRICE_INPUTS:
            self.assertIn(f'{prefix}_price_source', fields)
            self.assertIn(f'{prefix}_price_eur_per_kwh', fields)
        preview = await flow.async_step_thermal_cost_inputs(config)
        self.assertEqual(preview['step_id'], 'thermal_cost_preview')
        saved = await flow.async_step_thermal_cost_preview({})
        for prefix, _key, _module in THERMAL_PRICE_INPUTS:
            self.assertEqual(saved['data'][f'{prefix}_price_source'], 'constant')
        self.hass.entries.append(entry('cost', module=MODULE_THERMAL_COST, **{key: value for key,value in config.items() if key != 'name'}))
        from tests.test_importer import HABEMSOptionsFlow
        edit = HABEMSOptionsFlow()
        edit.hass, edit.config_entry = self.hass, self.hass.entries[-1]
        # Re-enter the input step, as reached after the unchanged COP setup.
        edit_form = await edit.async_step_thermal_cost_inputs()
        schema = edit_form['data_schema'].schema
        defaults = {str(key): key.default() for key in schema if str(key).endswith('_price_source')}
        self.assertEqual(defaults, {f'{prefix}_price_source': 'constant' for prefix, _, _ in THERMAL_PRICE_INPUTS})
