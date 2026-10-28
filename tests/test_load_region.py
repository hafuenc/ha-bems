"""Holiday region setup saves country-compatible codes and preserves options."""

from types import SimpleNamespace
import unittest

from tests.test_importer import HABEMSConfigFlow, HABEMSOptionsFlow, Hass


def region_field(result):
    return next(iter(result['data_schema'].schema.items()))


class LoadRegionTests(unittest.IsolatedAsyncioTestCase):
    async def test_country_specific_choices_save_region_codes(self):
        for country, region, label, count in [
            ('AT', '6', 'Steiermark', 10), ('DE', 'BY', 'Bayern', 17),
        ]:
            with self.subTest(country=country):
                flow = HABEMSConfigFlow()
                flow.hass = Hass()
                result = await flow.async_step_load_profile({
                    'name': 'Building load', 'annual_consumption_kwh': 3500,
                    'load_profile': 'H0', 'holiday_country': country,
                })
                self.assertEqual(result['step_id'], 'load_region')
                _, field = region_field(result)
                choices = field.config['options']
                self.assertEqual(len(choices), count)
                self.assertIn({'value': region, 'label': label}, choices)
                self.assertIn({'value': '', 'label': 'National holidays only'}, choices)
                result = await flow.async_step_load_region({'holiday_subdivision': region})
                self.assertEqual(result['type'], 'create_entry')
                self.assertEqual(result['data']['holiday_subdivision'], region)
                self.assertEqual(result['data']['holiday_country'], country)

    async def test_national_holidays_and_reject_wrong_country_region(self):
        flow = HABEMSConfigFlow()
        flow.hass = Hass()
        await flow.async_step_load_profile({
            'name': 'Building load', 'annual_consumption_kwh': 3500,
            'load_profile': 'H25', 'holiday_country': 'AT',
        })
        result = await flow.async_step_load_region({'holiday_subdivision': 'BY'})
        self.assertEqual(result['errors']['holiday_subdivision'], 'invalid_load_profile')
        result = await flow.async_step_load_region({'holiday_subdivision': ''})
        self.assertEqual(result['data']['holiday_subdivision'], '')

    async def test_options_preserve_legacy_name_and_reset_on_country_change(self):
        flow = HABEMSOptionsFlow()
        flow.hass = Hass()
        flow.config_entry = SimpleNamespace(data={
            'module': 'non_flexible_load', 'annual_consumption_kwh': 3500,
            'load_profile': 'H0', 'holiday_country': 'AT',
            'holiday_subdivision': 'Steiermark',
        }, options={})
        result = await flow.async_step_init()
        self.assertEqual(result['step_id'], 'load_profile')
        self.assertNotIn('holiday_subdivision', {str(k) for k in result['data_schema'].schema})
        result = await flow.async_step_load_profile({
            'annual_consumption_kwh': 4000, 'load_profile': 'H0', 'holiday_country': 'AT',
        })
        key, _ = region_field(result)
        self.assertEqual(key.default(), '6')
        result = await flow.async_step_load_profile({
            'annual_consumption_kwh': 4000, 'load_profile': 'H0', 'holiday_country': 'DE',
        })
        key, _ = region_field(result)
        self.assertEqual(key.default(), '')
        # Weekends-only needs no region form and clears the previous region.
        result = await flow.async_step_load_profile({
            'annual_consumption_kwh': 4000, 'load_profile': 'H0', 'holiday_country': '',
        })
        self.assertEqual(result['type'], 'create_entry')
        self.assertEqual(result['data']['holiday_subdivision'], '')

    async def test_supported_legacy_city_calendar_stays_editable(self):
        flow = HABEMSOptionsFlow()
        flow.hass = Hass()
        flow.config_entry = SimpleNamespace(data={
            'module': 'non_flexible_load', 'annual_consumption_kwh': 3500,
            'load_profile': 'H0', 'holiday_country': 'DE',
            'holiday_subdivision': 'Augsburg',
        }, options={})
        result = await flow.async_step_load_region()
        key, field = region_field(result)
        self.assertEqual(key.default(), 'Augsburg')
        self.assertIn({'value': 'Augsburg', 'label': 'Augsburg'}, field.config['options'])
        result = await flow.async_step_load_region({'holiday_subdivision': 'Augsburg'})
        self.assertEqual(result['data']['holiday_subdivision'], 'Augsburg')
