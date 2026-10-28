"""New seasonal helpers create monthly schedules; existing wrappers still work."""

from types import SimpleNamespace
import unittest

from tests.test_importer import HABEMSConfigFlow, HABEMSOptionsFlow, Hass
from custom_components.ha_bems.const import MONTHS


class SeasonalCreatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_setup_has_no_source_picker_and_creates_monthly_helper(self):
        flow=HABEMSConfigFlow();flow.hass=Hass()
        result=await flow.async_step_soc()
        self.assertEqual({str(key) for key in result['data_schema'].schema},{'name'})
        result=await flow.async_step_soc({'name':'Battery settings'})
        self.assertEqual(result['step_id'],'monthly')
        result=await flow.async_step_monthly({month:40 for month in MONTHS})
        self.assertEqual(result['type'],'create_entry')
        self.assertEqual(result['data']['seasonal_soc_source'],'monthly')
        self.assertEqual(result['data']['monthly_min_soc_percent'],[40]*12)

    async def test_existing_monthly_options_open_months_directly(self):
        flow=HABEMSOptionsFlow();flow.hass=Hass()
        flow.config_entry=SimpleNamespace(data={'module':'seasonal_soc','monthly_min_soc_percent':[35]*12},options={})
        result=await flow.async_step_init()
        self.assertEqual(result['step_id'],'monthly')

    async def test_existing_wrapper_options_still_allow_editing_source(self):
        flow=HABEMSOptionsFlow();flow.hass=Hass()
        flow.config_entry=SimpleNamespace(data={'module':'seasonal_soc','seasonal_soc_source':'entity',
            'seasonal_soc_entity':'sensor.existing_minimum'},options={})
        result=await flow.async_step_init()
        self.assertEqual(result['step_id'],'existing_entity')
        self.assertEqual(flow._values()['seasonal_soc_entity'],'sensor.existing_minimum')
