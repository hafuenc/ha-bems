"""Entity-boundary checks without requiring a complete HA installation."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_forecast import records_for


NOW = datetime(2026, 10, 27, 13, 22, 34, tzinfo=UTC)
dt_util = SimpleNamespace(now=lambda: NOW)
ha = ModuleType('homeassistant')
util = ModuleType('homeassistant.util')
util.dt = dt_util
spec = importlib.util.spec_from_file_location(
    'custom_components.ha_bems._test_forecast_input',
    Path(__file__).resolve().parents[1] / 'custom_components/ha_bems/forecast_input.py')
reader = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'homeassistant': ha, 'homeassistant.util': util}):
    spec.loader.exec_module(reader)


class FakeHass:
    def __init__(self, state):
        self.states = SimpleNamespace(get=lambda _entity: state)

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class ForecastEntityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.attributes = {'schema_version': 1, 'unit_of_measurement': 'W',
                           'source_updated_at': (NOW - timedelta(minutes=60)).isoformat(),
                           'forecast': records_for(NOW)}
        self.config = {'forecast_entity': 'sensor.pv_generation_forecast_72h'}

    async def read(self, attributes=None, state='0', present=True):
        source = SimpleNamespace(entity_id=self.config['forecast_entity'], state=state,
                                 attributes=self.attributes if attributes is None else attributes,
                                 last_updated=NOW)
        return await reader.async_read_pv_forecast(FakeHass(source if present else None), self.config)

    async def test_valid_sensor_and_source_metadata(self):
        rows, details = await self.read()
        self.assertEqual(len(rows), 289)
        self.assertEqual(details['source_age_minutes'], 60)
        self.assertEqual(details['input_forecast_end'], rows[-1]['end'])

    async def test_original_source_age_cannot_be_hidden_by_helper_regeneration(self):
        self.attributes['source_updated_at'] = (NOW - timedelta(hours=4)).isoformat()
        with self.assertRaisesRegex(ValueError, 'stale'):
            await self.read()
        self.config['forecast_max_age_minutes'] = 300
        self.assertEqual((await self.read())[1]['source_age_minutes'], 240)

    async def test_required_attributes_units_and_future_update(self):
        for key, value in [('schema_version', True), ('schema_version', 2),
                           ('schema_version', '1'), ('unit_of_measurement', 'kW'),
                           ('source_updated_at', None), ('source_updated_at', NOW.isoformat()[:19]),
                           ('source_updated_at', (NOW + timedelta(minutes=6)).isoformat())]:
            attrs = deepcopy(self.attributes)
            attrs[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                await self.read(attrs)
        for key in ['forecast', 'schema_version', 'source_updated_at']:
            attrs = deepcopy(self.attributes)
            del attrs[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                await self.read(attrs)

    async def test_unavailable_and_invalid_states(self):
        for state in ['unknown', 'unavailable', '-1', 'NaN', 'inf', 'text']:
            with self.subTest(state=state), self.assertRaises(ValueError):
                await self.read(state=state)
        with self.assertRaises(ValueError):
            await self.read(present=False)

    async def test_runtime_rejects_new_gaps_and_short_horizon(self):
        for rows in [self.attributes['forecast'][:288],
                     self.attributes['forecast'][:30] + self.attributes['forecast'][31:]]:
            attrs = {**self.attributes, 'forecast': rows}
            with self.assertRaises(ValueError):
                await self.read(attrs)


if __name__ == '__main__':
    unittest.main()
