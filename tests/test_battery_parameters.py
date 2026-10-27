"""Single charging-power parameter and legacy-entry migration."""

from types import SimpleNamespace
import unittest

from custom_components.ha_bems import async_migrate_entry
from custom_components.ha_bems.fbc import FBCSettings, calculate_fbc


class BatteryPowerTests(unittest.TestCase):
    def test_current_output_uses_the_single_maximum(self):
        settings=FBCSettings(capacity_kwh=6, target_soc_percent=100,
                             charge_efficiency=1, max_charge_power_w=3000)
        result=calculate_fbc([10000,10000],step_hours=0.25,
                             current_soc_percent=50,current_month=3,settings=settings)
        self.assertEqual(result.charge_limit_w,3000)
        self.assertFalse(result.limitation_disabled)

    def test_override_above_5000_has_no_hidden_default_output_cap(self):
        settings=FBCSettings(capacity_kwh=6, target_soc_percent=100,
                             charge_efficiency=1, max_charge_power_w=7500)
        result=calculate_fbc([10000,10000],step_hours=0.25,
                             current_soc_percent=10,current_month=3,settings=settings)
        self.assertEqual(result.charge_limit_w,7500)
        self.assertEqual(result.charge_profile_w,(7500,7500))


class BatteryMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_maximum_becomes_output_limit_regardless_of_old_cap(self):
        for maximum,cap,expected in [(5000,5000,5000),(5000,7500,5000),(7500,5000,7500)]:
            with self.subTest(maximum=maximum,cap=cap):
                entry=SimpleNamespace(version=1,minor_version=1,
                    data={'module':'battery_fbc','max_charge_power_w':1000,'soc_entity':'sensor.battery'},
                    options={'max_charge_power_w':maximum,'output_limit_w':cap})
                updates=[]
                hass=SimpleNamespace(config_entries=SimpleNamespace(
                    async_update_entry=lambda entry,**kwargs: updates.append(kwargs)))
                self.assertTrue(await async_migrate_entry(hass,entry))
                self.assertEqual(updates[0]['data']['max_charge_power_w'],expected)
                self.assertNotIn('output_limit_w',updates[0]['data'])
                self.assertEqual(updates[0]['data']['soc_entity'],'sensor.battery')
                self.assertEqual(updates[0]['options'],{})
                self.assertEqual(updates[0]['minor_version'],2)

    async def test_other_modules_keep_their_data_and_options(self):
        updates=[]
        entry=SimpleNamespace(version=1,minor_version=1,data={'module':'pv_forecast_import'},options={'forecast_entity':'sensor.pv'})
        hass=SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=lambda entry,**kwargs: updates.append(kwargs)))
        self.assertTrue(await async_migrate_entry(hass,entry))
        self.assertEqual(updates,[{'minor_version':2}])
