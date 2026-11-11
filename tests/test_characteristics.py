"""Diagnostic curves match configured operational functions and survive outages."""
import json
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from tests.ha_stubs import modules
from tests.test_fbc_notifications import Entity, CoordinatorEntity
from tests.test_thermal_cost import CONFIG as COST_CONFIG
from tests.test_thermal_milp import CONFIG as PLAN_CONFIG
from custom_components.ha_bems.characteristics import build_characteristic, characteristic_values

COP={**COST_CONFIG,'cop_model':'lookup','fallback_cop':3,
     'cop_points':[{'temperature_c':0,'cop':2},{'temperature_c':10,'cop':4}]}
RUNTIME={**PLAN_CONFIG,'runtime_model':'lookup',
    'runtime_points':[{'temperature_c':-10,'runtime_h_per_24h':20},{'temperature_c':10,'runtime_h_per_24h':0}],
    'runtime_min_h':2,'runtime_max_h':18,'heating_cutoff_enabled':True,'heating_cutoff_c':15}


class CharacteristicTests(unittest.TestCase):
    def test_reference_interpolation_extrapolation_and_fallback(self):
        result=build_characteristic(COP,'cop')
        points={p['temperature_c']:p for p in result['curve_points']}
        self.assertEqual(points[5]['value'],3)
        self.assertEqual(points[15]['value'],5)
        self.assertEqual(points[15]['region'],'extrapolation')
        self.assertEqual(points[-10]['value'],3)
        self.assertTrue(points[-10]['fallback_active'])
        self.assertEqual(points[-10]['raw_value'],0)
        self.assertEqual(len(result['reference_points']),2)
        json.dumps(result,allow_nan=False)

    def test_runtime_limits_and_exact_heating_cutoff_discontinuity(self):
        result=build_characteristic(RUNTIME,'runtime_requirement')
        points={p['temperature_c']:p for p in result['curve_points']}
        self.assertEqual(points[-20]['value'],18)
        self.assertEqual(points[0]['value'],10)
        self.assertEqual(points[8]['value'],2)  # Exact lower-clipping kink.
        self.assertEqual(points[-8]['value'],18)
        self.assertEqual(points[15]['value'],2)  # Strictly above cutoff switches off.
        self.assertEqual(points[15+1e-6]['value'],0)
        self.assertEqual(points[20]['value'],0)
        self.assertEqual(result['output_unit'],'h/24h')

    def test_constant_models_without_temperature(self):
        for config,function,value in [(COST_CONFIG,'cop',3.5),(PLAN_CONFIG,'runtime_requirement',12)]:
            curve=build_characteristic(config,function)
            self.assertEqual(curve['reference_points'],[])
            self.assertEqual(curve['constant_value'],value)
            self.assertEqual(curve['curve_step_c'],1)
            self.assertEqual(len(curve['curve_points']),66)  # -25 through +40 °C.
            self.assertTrue(all(p['value']==value and p['region']=='constant' for p in curve['curve_points']))

    def test_curve_range_includes_extreme_reference_points(self):
        config={**COP,'cop_points':[{'temperature_c':-50,'cop':2},{'temperature_c':70,'cop':5}]}
        result=build_characteristic(config,'cop')
        self.assertEqual(result['display_range_c'],[-55,75])
        self.assertEqual(result['curve_points'][0]['temperature_c'],-55)
        self.assertEqual(result['curve_points'][-1]['temperature_c'],75)

    def test_sensor_keeps_static_curve_when_temperature_input_fails(self):
        injected=modules()
        sensor=ModuleType('homeassistant.components.sensor');sensor.SensorEntity=Entity
        entity=ModuleType('homeassistant.helpers.entity');entity.EntityCategory=SimpleNamespace(DIAGNOSTIC='diagnostic')
        injected.update({sensor.__name__:sensor,entity.__name__:entity})
        injected['homeassistant.helpers.update_coordinator'].CoordinatorEntity=CoordinatorEntity
        with patch.dict('sys.modules',injected):
            from custom_components.ha_bems.characteristic_sensor import ThermalCharacteristicSensor
            config={**COP,'temperature_entry_id':'temperature'}
            coordinator=SimpleNamespace(config=config,data=None,last_update_success=False)
            item=SimpleNamespace(entry_id='test-cop',title='DHW')
            curve=ThermalCharacteristicSensor(coordinator,item,'cop');curve.hass=SimpleNamespace()
            with patch.dict(curve._current.__globals__,{'read_forecast_import':lambda *a: (_ for _ in ()).throw(ValueError('Temperature unavailable'))}):
                self.assertTrue(curve.available)
                self.assertIsNone(curve.native_value)
                attributes=curve.extra_state_attributes
                self.assertEqual(attributes['status'],'input_unavailable')
                self.assertEqual(attributes['curve_points'],build_characteristic(config,'cop')['curve_points'])
                self.assertEqual(attributes['reference_points'][0]['value'],2)
            with patch.dict(curve._current.__globals__,{'read_forecast_import':lambda *a: ([{'temperature_c':5}],{'source_entity':'sensor.outdoor'})}):
                self.assertEqual(curve.native_value,3)
                self.assertEqual(curve.extra_state_attributes['current_temperature_c'],5)
                self.assertFalse(curve.extra_state_attributes['fallback_active'])


class CharacteristicFrontendTests(unittest.IsolatedAsyncioTestCase):
    async def test_static_resource_registration_uses_ha_api_once(self):
        from unittest.mock import AsyncMock
        from custom_components.ha_bems.frontend import async_register_characteristic_frontend
        http=ModuleType('homeassistant.components.http')
        http.StaticPathConfig=lambda *args:args
        hass=SimpleNamespace(data={},http=SimpleNamespace(async_register_static_paths=AsyncMock()))
        with patch.dict('sys.modules',{'homeassistant.components.http':http}):
            await async_register_characteristic_frontend(hass)
            await async_register_characteristic_frontend(hass)
        hass.http.async_register_static_paths.assert_awaited_once()
        path=hass.http.async_register_static_paths.call_args.args[0][0]
        self.assertEqual(path[0],'/ha_bems/characteristic-card.js')
        from pathlib import Path
        self.assertTrue(Path(path[1]).is_file())
