"""Commercial profile scaling, calendars, DST and household compatibility."""

from datetime import UTC, datetime, timedelta
import json
import hashlib
import unittest
from zoneinfo import ZoneInfo

from custom_components.ha_bems.load_profile import _tables, load_forecast, year_shape
from custom_components.ha_bems.forecast import validate_forecast
from tests.test_importer import HABEMSConfigFlow, HABEMSOptionsFlow, Hass
from types import SimpleNamespace


class LoadProfileTests(unittest.TestCase):
    def test_existing_tables_unchanged(self):
        # Fingerprints of the unchanged v0.13.3 household tables.
        for profile, expected in {
            'H0': '238f5f225e6fd63239fd6531294a0a2124e5465db3dcd4028eda586cd9807dad',
            'H25': '8d731a5334fd7760a9f1b4a40a37d5f74491df857b5a05ffe7ae0fa22b34f545',
        }.items():
            actual = hashlib.sha256(json.dumps(_tables()[profile], sort_keys=True).encode()).hexdigest()
            self.assertEqual(actual, expected)

    def test_g25_source_values_and_dimensions(self):
        tables = _tables()['G25']
        self.assertEqual(len(tables), 12)
        for days in tables.values():
            self.assertEqual(set(days), {'saturday', 'sunday', 'workday'})
            for values in days.values():
                self.assertEqual(len(values), 96)
                self.assertTrue(all(value >= 0 for value in values))
        # Original workbook, G25!C5:E5: 15.045, 14.658, 14.832 kWh.
        self.assertEqual([tables['1'][day][0] for day in ('saturday', 'sunday', 'workday')],
                         [60.18, 58.632, 59.328])

    def test_annual_energy_normalisation_and_leap_year(self):
        for year, days in ((2026, 365), (2028, 366)):
            for profile in ('H0', 'H25', 'G25', 'constant'):
                with self.subTest(year=year, profile=profile):
                    _, weights = year_shape(year, 'Europe/Vienna', profile, 'AT', '6')
                    self.assertEqual(len(weights), days * 96)
                    self.assertAlmostEqual(sum(weights) * 12345, 12345, places=7)

    def test_g25_month_day_types_holidays_and_no_dynamisation(self):
        zone = ZoneInfo('Europe/Vienna')
        first, weights = year_shape(2026, zone.key, 'G25', 'AT', '6')
        def weight(month, day):
            local = datetime(2026, month, day, tzinfo=zone)
            return weights[int((local.astimezone(UTC) - first) / timedelta(minutes=15))]
        # Both are January workdays: no day-of-year household multiplier.
        self.assertEqual(weight(1, 7), weight(1, 8))
        base = weight(1, 7) / _tables()['G25']['1']['workday'][0]
        for month, day, kind in ((1, 1, 'sunday'), (1, 3, 'saturday'),
                                  (1, 4, 'sunday'), (6, 2, 'workday'),
                                  (12, 24, 'saturday'), (12, 31, 'saturday')):
            self.assertAlmostEqual(weight(month, day), base * _tables()['G25'][str(month)][kind][0])

    def test_forecast_contract_across_dst_and_year_boundary(self):
        zone = ZoneInfo('Europe/Vienna')
        config = dict(annual_consumption_kwh=12345, load_profile='G25',
                      holiday_country='AT', holiday_subdivision='6')
        for now in (datetime(2026, 3, 29, tzinfo=zone),
                    datetime(2026, 10, 25, tzinfo=zone),
                    datetime(2026, 12, 31, 23, 55, tzinfo=zone)):
            rows = load_forecast(config, now, zone.key)
            self.assertEqual(len(rows), 289)
            self.assertEqual(validate_forecast(rows, now), rows)
            for row in rows:
                self.assertAlmostEqual(row['power_w'] / 4000, row['energy_kwh'], places=8)
        now = datetime(2026, 10, 25, tzinfo=zone)
        repeated = [row['power_w'] for row in load_forecast(config, now, zone.key)
                    if datetime.fromisoformat(row['start']).astimezone(zone).hour == 2
                    and datetime.fromisoformat(row['start']).astimezone(zone).day == 25]
        self.assertEqual(len(repeated), 8)
        self.assertEqual(repeated[:4], repeated[4:])


class G25FlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_and_options_offer_and_save_g25(self):
        for flow in (HABEMSConfigFlow(), HABEMSOptionsFlow()):
            flow.hass = Hass()
            if isinstance(flow, HABEMSOptionsFlow):
                flow.config_entry = SimpleNamespace(data={
                    'module': 'non_flexible_load', 'annual_consumption_kwh': 12345,
                    'load_profile': 'G25', 'holiday_country': '',
                }, options={})
            form = await flow.async_step_load_profile()
            key, selector = next((key, value) for key, value in form['data_schema'].schema.items()
                                 if str(key) == 'load_profile')
            self.assertIn('G25', [option['value'] for option in selector.config['options']])
            if isinstance(flow, HABEMSOptionsFlow):
                self.assertEqual(key.default(), 'G25')
            result = await flow.async_step_load_profile(dict(
                name='Commercial load', annual_consumption_kwh=12345,
                load_profile='G25', holiday_country=''))
            self.assertEqual(result['type'], 'create_entry')
            self.assertEqual(result['data']['load_profile'], 'G25')
