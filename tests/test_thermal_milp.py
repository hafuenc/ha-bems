"""Independent enumeration checks storage physics, commitment and optimal costs."""

from itertools import product
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from custom_components.ha_bems.thermal_milp import InfeasiblePlanError, required_runtimes, solve_plan

CONFIG = {"runtime_model": "constant", "runtime_h_per_24h": 12,
          "storage_duration_h": 1, "storage_min_h": 0, "min_on_h": .5, "min_off_h": .25,
          "solver_time_limit_s": 5}


def feasible(sequence, runtimes, durations, initial, config, previous, elapsed, allowed, terminal):
    level = initial
    for operation, runtime, duration, permission in zip(sequence, runtimes, durations, allowed):
        minimum = config['min_on_h'] if previous else config['min_off_h']
        if operation != previous:
            if elapsed + 1e-8 < minimum: return False
            previous, elapsed = operation, 0
        if operation > permission: return False
        elapsed += duration
        level += (operation - runtime / 24) * duration
        if not config['storage_min_h'] - 1e-8 <= level <= config['storage_duration_h'] + 1e-8:
            return False
    return level >= terminal - 1e-8


class ThermalMILPTests(unittest.TestCase):
    def test_matches_exhaustive_binary_search_including_history_and_blocked_slots(self):
        costs = [.9, -.2, .1, .8, .3, .2, .7, -.1]
        runtimes, durations = [12] * 8, [.25] * 8
        for previous, elapsed, allowed in ((0, 72, [1]*8), (1, .25, [1]*8),
                                           (0, 0, [1]*8), (0, 72, [1, 0, 1, 1, 0, 1, 1, 1])):
            candidates = [sum(c*x for c,x in zip(costs, seq)) for seq in product((0,1), repeat=8)
                          if feasible(seq, runtimes, durations, .5, CONFIG, previous, elapsed, allowed, .5)]
            result = solve_plan(costs, runtimes, durations, .5, CONFIG, previous, elapsed, allowed, terminal_min=.5)
            self.assertAlmostEqual(result['total_cost_eur'], min(candidates))
            self.assertEqual(len(result['storage']), 9)
            self.assertTrue(feasible(result['operations'], runtimes, durations, .5, CONFIG, previous, elapsed, allowed, .5))

    def test_zero_and_full_demand_final_storage_and_partial_first_timestep(self):
        for demand, expected in ((0, [0]*8), (24, [1]*8)):
            result = solve_plan([.2]*8, [demand]*8, [.1, *[.25]*7], .5,
                {**CONFIG, 'min_on_h': 0, 'min_off_h': 0}, terminal_min=.5)
            self.assertEqual(result['operations'], expected)
            self.assertAlmostEqual(result['storage'][-1], .5)
            if demand == 24:
                self.assertAlmostEqual(result['total_cost_eur'], .2*(7+.1/.25))

    def test_infeasibility_is_explicit(self):
        with self.assertRaisesRegex(InfeasiblePlanError, 'No feasible'):
            solve_plan([.2]*8, [24]*8, [.25]*8, 0, CONFIG, allowed=[0]*8, terminal_min=0)

    def test_retired_minimum_storage_setting_cannot_raise_the_zero_lower_bound(self):
        from custom_components.ha_bems.thermal_milp import validate_plan_settings
        config={**CONFIG,'storage_min_h':.75,'initial_storage_h':0}
        validate_plan_settings(config)
        result=solve_plan([.2]*8,[0]*8,[.25]*8,0,config,terminal_min=0)
        self.assertEqual(result['operations'],[0]*8)
        self.assertEqual(result['storage'],[0]*9)

    def test_time_limit_accepts_only_verified_feasible_binary_results(self):
        import numpy as np
        config={**CONFIG,'min_on_h':0,'min_off_h':0}
        candidate=SimpleNamespace(status=1,x=np.zeros(6),message='Time limit',mip_gap=.2)
        with patch('scipy.optimize.milp',return_value=candidate):
            result=solve_plan([.2]*2,[0]*2,[.25]*2,.5,config,terminal_min=.5)
            self.assertEqual(result['solver_status'],'feasible_time_limit')
            candidate.x[0]=.5
            with self.assertRaisesRegex(ValueError,'verification'):
                solve_plan([.2]*2,[0]*2,[.25]*2,.5,config,terminal_min=.5)

    def test_lookup_clamping_and_cutoff_override(self):
        config = {**CONFIG, 'runtime_model':'lookup', 'runtime_min_h':2, 'runtime_max_h':18,
                  'runtime_points':[{'temperature_c':-12.1,'runtime_h_per_24h':18},
                                    {'temperature_c':12,'runtime_h_per_24h':0}],
                  'heating_cutoff_enabled':True,'heating_cutoff_c':20}
        values = required_runtimes(config, [-30, -12.1, -.05, 12, 20, 20.01], 6)
        for value, expected in zip(values, [18,18,9,2,2,0]):
            self.assertAlmostEqual(value, expected)

    def test_seventy_two_hour_chain_has_no_simultaneous_operation(self):
        n = 289
        prices = [.10 if i % 96 < 32 else .30 for i in range(n)]
        dhw = solve_plan(prices, [2]*n, [.25]*n, .5,
            {**CONFIG, 'storage_duration_h':1, 'min_on_h':.5}, terminal_min=.5)
        allowed = [1-x for x in dhw['operations']]
        ufh = solve_plan(prices, [12]*n, [.25]*n, 3.2,
            {**CONFIG,'storage_duration_h':6.4,'min_on_h':.75}, allowed=allowed, terminal_min=3.2)
        self.assertTrue(all(a+b<=1 for a,b in zip(dhw['operations'], ufh['operations'])))
        self.assertGreaterEqual(ufh['storage'][-1], 3.2-1e-7)
