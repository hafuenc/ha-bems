"""Thermal cost updates follow inputs/quarters without duplicate publications."""

from datetime import timedelta
import unittest
from unittest.mock import Mock, patch

from tests.ha_stubs import modules
from tests.test_forecast_input import NOW
from tests.test_importer import entry
from tests.test_thermal_cost import CONFIG, calculate_cost_forecast
from tests.test_thermal_setup import INPUTS, ThermalCostCoordinator, coordinators, thermal_hass


class ThermalRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        context = patch.dict('sys.modules', modules())
        context.start()
        self.addCleanup(context.stop)
        self.hass = thermal_hass()
        self.plan = ThermalCostCoordinator(self.hass,
            entry('DHW', module='thermal_runtime_cost', **CONFIG, **INPUTS))
        self.now = NOW
        clock = patch.object(self.plan._async_update_data.__globals__['dt_util'],
                             'now', lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        calculation = patch.dict(self.plan._async_update_data.__globals__,
                                 {'calculate_cost_forecast': Mock(wraps=calculate_cost_forecast)})
        calculation.start()
        self.addCleanup(calculation.stop)
        self.calculate = self.plan._async_update_data.__globals__['calculate_cost_forecast']
        self.publish = Mock()
        self.plan.async_add_listener(self.publish)

    async def test_no_minute_poll_and_same_slot_validation_does_not_recalculate_or_publish(self):
        self.assertIsNone(self.plan.update_interval)
        self.assertFalse(self.plan.always_update)
        await self.plan.async_refresh()
        original = self.plan.data
        self.now += timedelta(minutes=1)
        # Informational import metadata may change without any new forecast.
        for importer in coordinators(self.hass).values():
            importer.data['input_validated_at'] = self.now.isoformat()
        await self.plan.async_refresh()
        self.assertIs(self.plan.data, original)
        self.assertEqual(self.calculate.call_count, 1)
        self.assertEqual(self.publish.call_count, 1)
        self.assertIs(self.plan._last_good_cost, original)

    async def test_changed_future_input_recalculates_even_when_current_value_matches(self):
        await self.plan.async_refresh()
        previous = self.plan.data
        coordinators(self.hass)['pv-main'].data['forecast'][10]['power_w'] = 1500
        self.now += timedelta(minutes=1)
        await self.plan.async_refresh()
        self.assertEqual(self.calculate.call_count, 2)
        self.assertEqual(self.publish.call_count, 2)
        self.assertEqual(self.plan.data['value'], previous['value'])
        self.assertNotEqual(self.plan.data['forecast'][10], previous['forecast'][10])

    async def test_new_quarter_recalculates_and_rolls_forecast(self):
        await self.plan.async_refresh()
        previous = self.plan.data
        self.now += timedelta(minutes=15)
        await self.plan.async_refresh()
        self.assertTrue(self.plan.last_update_success, self.plan.last_exception)
        self.assertEqual(self.calculate.call_count, 2)
        self.assertEqual(self.publish.call_count, 2)
        self.assertEqual(self.plan.data['forecast'][0]['start'], previous['forecast'][1]['start'])
        self.assertEqual(self.plan.data['generated_at'], self.now.isoformat())

    async def test_changed_inputs_with_identical_output_do_not_republish_or_renew_retention(self):
        pv = coordinators(self.hass)['pv-main'].data['forecast']
        for row in pv:
            row['power_w'] = 3000  # Already covers the complete electrical demand.
        await self.plan.async_refresh()
        previous = self.plan.data
        for row in pv:
            row['power_w'] = 4000
        self.now += timedelta(minutes=1)
        await self.plan.async_refresh()
        self.assertEqual(self.calculate.call_count, 2)
        self.assertEqual(self.publish.call_count, 1)
        self.assertIs(self.plan.data, previous)
        self.assertIs(self.plan._last_good_cost, previous)
        await self.plan.async_refresh()
        self.assertEqual(self.calculate.call_count, 2)
        self.assertEqual(self.publish.call_count, 1)

    async def test_disabled_input_is_not_hidden_by_cache_and_recovery_notifies(self):
        await self.plan.async_refresh()
        source = next(item for item in self.hass.entries if item.entry_id == 'import')
        source.data['enabled'] = False
        await self.plan.async_refresh()
        self.assertFalse(self.plan.last_update_success)
        self.assertEqual(self.calculate.call_count, 1)
        source.data['enabled'] = True
        await self.plan.async_refresh()
        self.assertTrue(self.plan.last_update_success)
        self.assertEqual(self.calculate.call_count, 1)
        self.assertEqual(self.publish.call_count, 3)

    async def test_source_freshness_updates_without_repeating_cost_arithmetic(self):
        await self.plan.async_refresh()
        previous = self.plan.data['source_updated_at']
        source = coordinators(self.hass)['export'].data
        source['source_updated_at'] = (NOW - timedelta(minutes=119)).isoformat()
        self.now += timedelta(minutes=1)
        await self.plan.async_refresh()
        self.assertEqual(self.calculate.call_count, 1)
        self.assertEqual(self.publish.call_count, 2)
        self.assertNotEqual(self.plan.data['source_updated_at'], previous)
