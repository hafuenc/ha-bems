"""Acceptance tests for forecast coverage and FBC's preserved daily horizon."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from custom_components.ha_bems.forecast import daily_forecast, validate_forecast


def records_for(now, count=289):
    start = now.astimezone(UTC).replace(second=0, microsecond=0)
    start -= timedelta(minutes=start.minute % 15)
    return [{"start": (start + timedelta(minutes=i * 15)).isoformat(),
             "end": (start + timedelta(minutes=(i + 1) * 15)).isoformat(),
             "power_w": 0 if i % 96 < 24 else 1500.5} for i in range(count)]


class ForecastContractTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 13, 22, 34, tzinfo=UTC)
        self.rows = records_for(self.now)

    def test_full_rolling_forecast_and_explicit_nighttime_zero(self):
        self.assertEqual(validate_forecast(self.rows, self.now), self.rows)

    def test_288_quarters_are_too_short_when_current_quarter_is_partial(self):
        with self.assertRaisesRegex(ValueError, "72 hours"):
            validate_forecast(self.rows[:288], self.now)

    def test_exact_72_hours_is_accepted_at_quarter_boundary(self):
        now = self.now.replace(minute=15, second=0)
        self.assertEqual(len(validate_forecast(records_for(now, 288), now)), 288)

    def test_bad_records_are_rejected(self):
        cases = {}
        for field, value in [('start', '2026-10-27T13:15:00'),
                             ('start', 'not-a-date'), ('power_w', -1),
                             ('power_w', float('nan')), ('power_w', float('inf')),
                             ('power_w', True), ('power_w', '1000'),
                             ('end', '2026-10-27T13:45:00+00:00'),
                             ('start', '2026-10-27T13:16:00+00:00')]:
            rows = deepcopy(self.rows)
            rows[0][field] = value
            cases[str((field, value))] = rows
        rows = deepcopy(self.rows)
        del rows[0]['end']
        cases['missing_end'] = rows
        cases['gap'] = self.rows[:30] + self.rows[31:]
        cases['duplicate'] = self.rows[:30] + [self.rows[29]] + self.rows[30:]
        cases['out_of_order'] = [self.rows[1], self.rows[0]] + self.rows[2:]
        cases['missing_current_quarter'] = self.rows[1:]
        cases['empty'] = []
        cases['not_a_list'] = {'forecast': self.rows}
        cases['invalid_row'] = [None] + self.rows[1:]
        for name, records in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_forecast(records, self.now)

    def test_historical_rows_do_not_enter_current_plan(self):
        earlier = self.now - timedelta(minutes=30)
        rows = records_for(earlier, 291)
        self.assertEqual(validate_forecast(rows, self.now), rows[2:])

    def test_daylight_saving_days_have_correct_daily_quarter_counts(self):
        zone = ZoneInfo('Europe/Vienna')
        for date, quarters in [((2026, 3, 29), 92), ((2026, 10, 25), 100)]:
            with self.subTest(date=date):
                now = datetime(*date, tzinfo=zone)
                rows = validate_forecast(records_for(now), now)
                self.assertEqual(len(daily_forecast(rows, now, zone)), quarters)

    def test_fbc_keeps_only_the_remaining_local_day(self):
        rows = validate_forecast(self.rows, self.now)
        result = daily_forecast(rows, self.now, ZoneInfo('Europe/Vienna'))
        self.assertEqual(len(result), 39)
        self.assertEqual(result[-1]['end'], '2026-10-27T23:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
