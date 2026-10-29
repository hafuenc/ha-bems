"""Activation-cost arithmetic, COP models and the independent output contract."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import math
import random
import unittest

from custom_components.ha_bems.thermal_cost import (
    calculate_cost_forecast, cop_points, interpolate_cop, validate_settings,
)
from custom_components.ha_bems.forecast import validate_forecast
from tests.test_forecast import records_for
from tests.test_forecast_input import NOW


CONFIG = {"thermal_power_kw": 7, "auxiliary_power_w": 0,
          "cop_model": "constant", "constant_cop": 3.5}


def rows(value, key="power_w", now=NOW):
    return [{"start": row["start"], "end": row["end"], key: value} for row in records_for(now)]


def calculate(config=None, pv=0, load=0, import_price=.30, export_price=.10, temperature=None, now=NOW):
    return calculate_cost_forecast(config or CONFIG, now, rows(pv, now=now),
        rows(import_price, "price_eur_per_kwh", now), rows(export_price, "price_eur_per_kwh", now),
        rows(load, now=now), rows(temperature, "temperature_c", now) if temperature is not None else None)


class ThermalArithmeticTests(unittest.TestCase):
    def test_grid_pv_split_auxiliaries_and_curtailment(self):
        self.assertAlmostEqual(calculate()["value"], .15)
        self.assertAlmostEqual(calculate(pv=1000)["value"], .10)
        self.assertAlmostEqual(calculate(pv=3000, load=1000)["value"], .05)
        self.assertAlmostEqual(calculate(pv=0, load=5000)["value"], .15)
        limited = {**CONFIG, "feed_in_limit_enabled": True, "feed_in_limit_w": 4000,
                   "curtailed_price_eur_per_kwh": 0}
        result = calculate(limited, pv=6000, load=1000)
        self.assertAlmostEqual(result["value"], .025)
        self.assertEqual(result["forecast"][0]["curtailed_pv_power_w"], 1000)
        self.assertAlmostEqual(calculate({**limited, "auxiliary_power_w": 500}, pv=6000, load=1000)["value"], .0375)
        self.assertAlmostEqual(calculate({**limited, "feed_in_limit_w": 0}, pv=6000)["value"], 0)
        self.assertAlmostEqual(calculate({**CONFIG, "auxiliary_power_w": 500})["value"], .1875)

    def test_matlab_share_equivalence_over_varied_inputs(self):
        rng = random.Random(2026)
        for _ in range(25):
            pv, load, limit = rng.uniform(0, 12000), rng.uniform(0, 6000), rng.uniform(0, 8000)
            cop, thermal = rng.uniform(.5, 8), rng.uniform(.1, 15)
            imp, exp, curtailed = rng.uniform(-.2, .8), rng.uniform(-.2, .4), rng.uniform(-.1, .1)
            power = thermal * 1000 / cop
            net = max(power - max(pv - load, 0), 0) / power
            lim = 1 - max(power - max(pv - load - limit, 0), 0) / power
            pvs = 1 - net - lim
            expected = (net * imp + pvs * exp + lim * curtailed) / cop * thermal * .25
            config = {**CONFIG, "thermal_power_kw": thermal, "constant_cop": cop,
                      "feed_in_limit_enabled": True, "feed_in_limit_w": limit,
                      "curtailed_price_eur_per_kwh": curtailed}
            self.assertAlmostEqual(calculate(config, pv=pv, load=load, import_price=imp, export_price=exp)["value"], expected)

    def test_negative_prices_and_no_power_allocation_between_setups(self):
        self.assertLess(calculate(import_price=-.3)["value"], 0)
        self.assertLess(calculate(pv=5000, export_price=-.1)["value"], 0)
        dhw = calculate(pv=1000)
        ufh = calculate({**CONFIG, "constant_cop": 7}, pv=1000)
        self.assertAlmostEqual(dhw["value"], .10)
        self.assertAlmostEqual(ufh["value"], .025)
        self.assertEqual(dhw["pv_allocation"], "independent_per_setup_without_reservations")

    def test_contract_rolls_in_utc_across_dst_and_month_change(self):
        for now in (NOW, datetime(2026, 10, 24, 12, 7, tzinfo=UTC),
                    datetime(2026, 3, 28, 12, 7, tzinfo=UTC), datetime(2026, 12, 31, 23, 59, tzinfo=UTC)):
            result = calculate(now=now)
            validated = validate_forecast(result["forecast"], now, "cost_eur_per_timestep", False)
            self.assertEqual(len(validated), 289)
            self.assertEqual(validated[0]["cost_eur_per_timestep"], result["value"])
            self.assertEqual(result["forecast_unit"], "EUR/timestep")
            self.assertEqual(result["schema_version"], 1)

    def test_bad_prices_load_and_coverage_are_not_silently_filled(self):
        good = rows(.3, "price_eur_per_kwh")
        for bad in (good[:-1], good[1:], [good[0], *good], good[:5] + good[6:]):
            with self.assertRaises(ValueError):
                calculate_cost_forecast(CONFIG, NOW, rows(0), bad, good)
        with self.assertRaises(ValueError):
            calculate_cost_forecast(CONFIG, NOW, rows(0), good, good, rows(-1))
        for key, value in (("constant_cop", 0), ("constant_cop", True),
                           ("thermal_power_kw", -1), ("auxiliary_power_w", math.inf)):
            with self.assertRaises(ValueError):
                validate_settings({**CONFIG, key: value})


class COPTests(unittest.TestCase):
    def test_two_to_ten_anchors_sort_and_linear_extrapolation(self):
        points = cop_points([{"temperature_c": 12, "cop": 4}, {"temperature_c": -8, "cop": 2}])
        self.assertEqual(points, [(-8, 2), (12, 4)])
        for temperature, expected in ((-18, 1), (-8, 2), (2, 3), (12, 4), (22, 5)):
            self.assertAlmostEqual(interpolate_cop(temperature, points), expected)
        self.assertEqual(len(cop_points([{"temperature_c": i, "cop": i + 1} for i in range(10)])), 10)
        for invalid in ([], [{"temperature_c": 0, "cop": 3}],
                        [{"temperature_c": i, "cop": 3} for i in range(11)],
                        [{"temperature_c": 0, "cop": 2}, {"temperature_c": 0, "cop": 4}],
                        [{"temperature_c": 0, "cop": -1}, {"temperature_c": 10, "cop": 4}],
                        [{"temperature_c": math.nan, "cop": 2}, {"temperature_c": 10, "cop": 4}]):
            with self.assertRaises(ValueError):
                cop_points(invalid)

    def test_nonpositive_cop_and_missing_temperature_use_explicit_fallback(self):
        config = {**CONFIG, "cop_model": "lookup", "fallback_cop": 3,
                  "cop_points": [{"temperature_c": -8, "cop": 2}, {"temperature_c": 12, "cop": 4}]}
        self.assertAlmostEqual(calculate(config, temperature=2)["current_cop"], 3)
        for temperature in (-50, None):
            result = calculate(config, temperature=temperature)
            self.assertTrue(result["cop_fallback_active"])
            self.assertEqual(result["cop_fallback_intervals"], 289)
            self.assertEqual(result["current_cop"], 3)
            self.assertTrue(result["cop_fallback_reason"])
        temperature = rows(2, "temperature_c")
        temperature[0]["temperature_c"] = -50
        result = calculate_cost_forecast(config, NOW, rows(0), rows(.3, "price_eur_per_kwh"),
                                        rows(.1, "price_eur_per_kwh"), temperatures=temperature)
        self.assertEqual(result["cop_fallback_intervals"], 1)
        self.assertTrue(result["forecast"][0]["cop_fallback"])
        self.assertFalse(result["forecast"][1]["cop_fallback"])
        self.assertFalse(calculate()["cop_fallback_active"])
