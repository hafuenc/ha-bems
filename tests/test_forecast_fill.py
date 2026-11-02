"""Bounded previous-value filling, including chained BEMS output forecasts."""
from copy import deepcopy
from datetime import timedelta
import unittest

from tests.test_forecast import records_for
from tests.test_forecast_input import NOW
from tests.test_thermal_cost import CONFIG, rows
from custom_components.ha_bems.forecast import validate_forecast, ForecastCoverageError
from custom_components.ha_bems.thermal_cost import calculate_cost_forecast
from custom_components.ha_bems.operation_constraints import permission_rows


class PreviousFillTests(unittest.TestCase):
    def test_tail_one_to_four_and_no_mutation(self):
        for count in range(1,5):
            source=records_for(NOW)[:-count]
            original=deepcopy(source)
            filled=validate_forecast(source,NOW)
            self.assertEqual(source,original)
            self.assertEqual(len(filled),289)
            self.assertEqual(sum(r.get('filled_previous',False) for r in filled),count)
            self.assertTrue(all(r['power_w']==source[-1]['power_w'] for r in filled[-count:]))
        with self.assertRaises(ForecastCoverageError):
            validate_forecast(records_for(NOW)[:-5],NOW)

    def test_internal_and_tail_share_one_four_slot_budget(self):
        source=records_for(NOW)
        filled=validate_forecast(source[:10]+source[12:-2],NOW)
        self.assertEqual(filled[10]['power_w'],source[9]['power_w'])
        self.assertEqual(filled[11]['power_w'],source[9]['power_w'])
        self.assertEqual(sum(r.get('filled_previous',False) for r in filled),4)
        with self.assertRaises(ForecastCoverageError):
            validate_forecast(source[:10]+source[13:-2],NOW)
        with self.assertRaises(ValueError):
            validate_forecast(source[1:],NOW)

    def test_chaining_does_not_reset_fill_budget(self):
        normalized=validate_forecast(records_for(NOW)[:-4],NOW)
        self.assertEqual(validate_forecast(normalized,NOW),normalized)
        with self.assertRaises(ForecastCoverageError):
            validate_forecast(normalized,NOW+timedelta(minutes=15))

    def test_cost_output_keeps_source_fill_provenance(self):
        result=calculate_cost_forecast(CONFIG,NOW,rows(0)[:-4],
            rows(.3,'price_eur_per_kwh'),rows(.1,'price_eur_per_kwh'))
        self.assertEqual(result['filled_previous_intervals'],4)
        with self.assertRaises(ForecastCoverageError):
            validate_forecast(result['forecast'],NOW+timedelta(minutes=15),'cost_eur_per_timestep',False)

    def test_binary_permissions_hold_previous_and_keep_provenance(self):
        source=[{'start':r['start'],'end':r['end'],'operation':i%2} for i,r in enumerate(records_for(NOW))]
        filled=permission_rows(source[:-4],{},NOW)
        self.assertTrue(all(r['allowed']==source[-5]['operation'] for r in filled[-4:]))
        self.assertTrue(all(r['filled_previous'] for r in filled[-4:]))

    def test_nonfinite_and_unordered_values_are_not_repaired(self):
        source=records_for(NOW)
        source[20]['power_w']=float('nan')
        with self.assertRaises(ValueError):validate_forecast(source,NOW)
        source=records_for(NOW)
        source[20],source[21]=source[21],source[20]
        with self.assertRaises(ValueError):validate_forecast(source,NOW)
