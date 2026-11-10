"""Wind energy physics, persistent shared weather cache and private setup."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4
import unittest
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from custom_components.ha_bems.wind_forecast import (
    DEFAULT_WIND_POINTS, curve_text, interval_energy, parse_curve, power_at,
    validate_weather, validate_wind_settings, wind_energy_forecast,
)
from custom_components.ha_bems.wind_access import async_unlock, password_record, verify_password
from custom_components.ha_bems.const import MODULE_EEG_ENERGY, MODULE_EEG_WIND
from tests.ha_stubs import modules, UpdateFailed
from tests.test_importer import HABEMSConfigFlow, HABEMSOptionsFlow, HABEMSItemFlow, Hass, entry

with patch.dict('sys.modules', modules()):
    from custom_components.ha_bems import wind_coordinator as wc

NOW = datetime(2026, 11, 10, 13, 7, tzinfo=UTC)
SETTINGS = {'latitude': 47.2446, 'longitude': 15.8003, 'hub_height_m': 85,
            'share_percent': 100, 'wind_power_points': [list(p) for p in DEFAULT_WIND_POINTS]}


def payload(speed=8):
    first = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    return {'hourly': {'time': [(first + timedelta(hours=i)).timestamp() for i in range(168)],
                       **{f'wind_speed_{height}m': [speed] * 168 for height in (10, 80, 120, 180)}},
            'hourly_units': {'time': 'unixtime', **{f'wind_speed_{h}m': 'm/s' for h in (10,80,120,180)}}}


class WindModelTests(unittest.TestCase):
    def test_default_curve_interpolation_and_shutdown(self):
        points = parse_curve(curve_text())
        self.assertEqual(points, [list(p) for p in DEFAULT_WIND_POINTS])
        self.assertEqual(power_at(7.5, points), 513)
        self.assertEqual(power_at(.9, points), 0)
        self.assertEqual(power_at(25, points), 2310)
        self.assertEqual(power_at(25.001, points), 0)
        self.assertAlmostEqual(interval_energy(24, 26, points), 2310 * .125)
        self.assertAlmostEqual(interval_energy(26, 24, points), 2310 * .125)
        self.assertAlmostEqual(interval_energy(.5, 1.5, points), .0625)
        self.assertEqual(parse_curve('0;0\n2,5;10,2'), [[0,0],[2.5,10.2]])

    def test_invalid_curves_coordinates_share_and_heights_fail(self):
        for text in ('', '0;0', '1;1\n1;2', '2;1\n1;2', '-1;0\n1;1', '0;-2\n1;1',
                     '0;0\n1;nan', '0;0\ninf;1', '0;0;1\n1;1'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_curve(text)
        for key, value in [('latitude',91),('longitude',181),('hub_height_m',9),('hub_height_m',181),
                           ('share_percent',101),('share_percent',-1),('share_percent',float('nan'))]:
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                validate_wind_settings({**SETTINGS,key:value})

    def test_height_scaling_and_consumer_contract(self):
        weather = payload(7)
        weather['hourly']['wind_speed_120m'] = [11]*168
        # At 85 m: 7.5 m/s, giving 513 kW.
        weather = validate_weather(weather)
        rows = wind_energy_forecast(SETTINGS,weather,NOW)
        self.assertEqual(len(rows),289)
        self.assertEqual(rows[0]['start'],'2026-11-10T13:00:00+00:00')
        self.assertTrue(all(row['energy_kwh']==128.25 for row in rows))
        one_percent = wind_energy_forecast({**SETTINGS,'share_percent':1},weather,NOW)
        self.assertAlmostEqual(one_percent[0]['energy_kwh'],1.2825)
        zero = wind_energy_forecast({**SETTINGS,'share_percent':0},weather,NOW)
        self.assertTrue(all(r['energy_kwh']==0 for r in zero))
        from custom_components.ha_bems.forecast import validate_forecast
        self.assertEqual(validate_forecast(rows,NOW,'energy_kwh'),rows)

    def test_dst_uses_absolute_quarter_hours(self):
        weather = validate_weather(payload())
        # Shift fixture to the night clocks move backwards.
        shift = datetime(2026,10,25,tzinfo=UTC).timestamp()-weather['time'][0]
        weather['time'] = [t+shift for t in weather['time']]
        now = datetime(2026,10,25,2,30,tzinfo=ZoneInfo('Europe/Vienna'),fold=1)
        rows = wind_energy_forecast(SETTINGS,weather,now)
        self.assertEqual(len({r['start'] for r in rows}),289)
        self.assertEqual(rows[0]['start'],'2026-10-25T01:30:00+00:00')

    def test_missing_weather_and_exhausted_horizon_are_not_filled(self):
        weather = validate_weather(payload())
        with self.assertRaises(ValueError):
            wind_energy_forecast(SETTINGS,weather,NOW+timedelta(days=5))
        for mutate in (lambda p:p['hourly']['wind_speed_80m'].__setitem__(10,None),
                       lambda p:p['hourly']['wind_speed_120m'].pop(),
                       lambda p:p['hourly']['time'].__setitem__(10,0),
                       lambda p:p['hourly_units'].__setitem__('wind_speed_80m','km/h')):
            source=payload(); mutate(source)
            with self.assertRaises((ValueError,TypeError)):
                validate_weather(source)


class FakeStore:
    values = {}
    def __init__(self,hass,version,key): self.key=key
    async def async_load(self): return deepcopy(self.values.get(self.key))
    async def async_save(self,value): self.values[self.key]=deepcopy(value)


class WeatherCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeStore.values={}
        storage=ModuleType('homeassistant.helpers.storage');storage.Store=FakeStore
        aiohttp=ModuleType('aiohttp');aiohttp.ClientError=type('ClientError',(Exception,),{})
        self.patcher=patch.dict('sys.modules',{'homeassistant.helpers.storage':storage,'aiohttp':aiohttp})
        self.patcher.start();self.addCleanup(self.patcher.stop)
        self.hass=Hass()

    async def test_shared_cache_only_fetches_every_three_hours_and_restores_on_restart(self):
        cache=wc.weather_cache(self.hass,SETTINGS)
        self.assertIs(cache,wc.weather_cache(self.hass,{**SETTINGS,'hub_height_m':120,'share_percent':2}))
        cache._request=AsyncMock(return_value=payload())
        await asyncio.gather(cache.async_get(NOW),cache.async_get(NOW))
        await cache.async_get(NOW+timedelta(minutes=15))
        self.assertEqual(cache._request.await_count,1)
        restored=wc.WindWeatherCache(self.hass,SETTINGS['latitude'],SETTINGS['longitude'])
        restored._request=AsyncMock(return_value=payload(9))
        weather,metadata=await restored.async_get(NOW+timedelta(hours=2))
        restored._request.assert_not_awaited()
        self.assertEqual(weather['wind_speed_80m'][0],8)
        self.assertEqual(metadata['source_age_minutes'],120)
        await restored.async_get(NOW+timedelta(hours=3))
        restored._request.assert_awaited_once()
        self.assertEqual(restored.params['forecast_days'],7)
        self.assertNotIn('models',restored.params)

    async def test_failure_keeps_backup_and_backs_off_without_corrupting_saved_data(self):
        cache=wc.weather_cache(self.hass,SETTINGS)
        cache._request=AsyncMock(return_value=payload())
        weather,_=await cache.async_get(NOW)
        saved=deepcopy(FakeStore.values)
        cache._request=AsyncMock(side_effect=TimeoutError())
        backup,metadata=await cache.async_get(NOW+timedelta(hours=3))
        self.assertEqual(backup,weather)
        self.assertTrue(metadata['using_cached_backup'])
        self.assertEqual(metadata['source_age_minutes'],180)
        await cache.async_get(NOW+timedelta(hours=3,minutes=1))
        self.assertEqual(cache._request.await_count,1)
        await cache.async_get(NOW+timedelta(hours=3,minutes=15))
        self.assertEqual(cache._request.await_count,2)
        self.assertEqual(FakeStore.values,saved)
        broken=payload();broken['hourly']['wind_speed_80m'][20]=None
        cache._request=AsyncMock(return_value=broken)
        await cache.async_get(NOW+timedelta(hours=4))
        self.assertEqual(cache.weather,weather)
        self.assertEqual(FakeStore.values,saved)

    async def test_generator_rolls_locally_and_fails_closed_when_backup_is_exhausted(self):
        e=entry('wind',MODULE_EEG_WIND,**SETTINGS)
        coordinator=wc.WindEnergyCoordinator(self.hass,e)
        coordinator.cache._request=AsyncMock(return_value=payload())
        with patch.object(wc.dt_util,'now',return_value=NOW):
            initial=await coordinator._async_update_data()
        with patch.object(wc.dt_util,'now',return_value=NOW+timedelta(minutes=15)):
            moved=await coordinator._async_update_data()
        self.assertNotEqual(initial['forecast'][0]['start'],moved['forecast'][0]['start'])
        coordinator.cache._request.assert_awaited_once()
        coordinator.cache._request=AsyncMock(side_effect=TimeoutError())
        with patch.object(wc.dt_util,'now',return_value=NOW+timedelta(days=2)):
            backup=await coordinator._async_update_data()
        self.assertEqual(backup['status'],'cached_backup')
        with patch.object(wc.dt_util,'now',return_value=NOW+timedelta(days=5)):
            with self.assertRaises(UpdateFailed): await coordinator._async_update_data()

    async def test_no_cache_and_corrupt_cache_recover_without_fabricated_energy(self):
        cache=wc.weather_cache(self.hass,SETTINGS)
        FakeStore.values[cache.store.key]={'payload':{'bad':'data'},'fetched_at':'bad'}
        cache._request=AsyncMock(side_effect=TimeoutError())
        with self.assertRaises(ValueError): await cache.async_get(NOW)
        cache._request=AsyncMock(return_value=payload())
        weather,_=await cache.async_get(NOW+timedelta(minutes=15))
        self.assertEqual(weather['wind_speed_80m'][0],8)

    async def test_generated_input_is_available_to_runtime_cost_without_importer(self):
        with patch.dict('sys.modules',modules()):
            from custom_components.ha_bems.import_registry import coordinators,input_entries,read_forecast_import
        self.hass.entries=[entry('wind',MODULE_EEG_WIND,**SETTINGS)]
        e=self.hass.entries[0]
        coordinator=wc.WindEnergyCoordinator(self.hass,e)
        coordinator.cache._request=AsyncMock(return_value=payload())
        with patch.object(wc.dt_util,'now',return_value=NOW):
            coordinator.data=await coordinator._async_update_data()
        coordinators(self.hass)['wind']=coordinator
        self.assertEqual(input_entries(self.hass,MODULE_EEG_ENERGY),[e])
        rows,details=read_forecast_import(self.hass,'wind',MODULE_EEG_ENERGY,NOW)
        self.assertEqual(len(rows),289)
        self.assertEqual(rows[0]['energy_kwh'],156.5)
        self.assertIn('weather_attribution',details)
        coordinator.last_update_success=False
        with self.assertRaises(ValueError): read_forecast_import(self.hass,'wind',MODULE_EEG_ENERGY,NOW)
        coordinator.last_update_success=True
        with self.assertRaises(ValueError): read_forecast_import(self.hass,'wind',MODULE_EEG_ENERGY,NOW+timedelta(days=5))

    async def test_platform_startup_clock_sensor_and_unload(self):
        import importlib
        from tests.test_fbc_notifications import Entity, CoordinatorEntity
        from custom_components.ha_bems import async_setup_entry, async_unload_entry
        e=entry('wind',MODULE_EEG_WIND,**SETTINGS)
        self.hass.entries=[e]
        e.callbacks=[];e.async_on_unload=e.callbacks.append
        e.add_update_listener=lambda callback:lambda:None
        tasks=[]
        self.hass.async_create_task=lambda coro:tasks.append(asyncio.create_task(coro))
        cache=wc.weather_cache(self.hass,SETTINGS)
        cache._request=AsyncMock(return_value=payload())
        injected=modules()
        injected['custom_components.ha_bems.wind_coordinator']=wc
        injected['homeassistant.const'].Platform=SimpleNamespace(SENSOR='sensor')
        injected['homeassistant.helpers.update_coordinator'].CoordinatorEntity=CoordinatorEntity
        component=ModuleType('homeassistant.components')
        sensor=ModuleType('homeassistant.components.sensor');sensor.SensorEntity=Entity
        entity=ModuleType('homeassistant.helpers.entity')
        entity.DeviceInfo=lambda **kwargs:kwargs
        entity.EntityCategory=SimpleNamespace(DIAGNOSTIC='diagnostic')
        dispatch=ModuleType('homeassistant.helpers.dispatcher')
        dispatch.async_dispatcher_send=Mock()
        dispatch.async_dispatcher_connect=lambda *args:lambda:None
        events=ModuleType('homeassistant.helpers.event')
        clocks=[]
        def track(hass,callback,**kwargs):
            self.assertEqual(kwargs,{'minute':[0,15,30,45],'second':0})
            clocks.append(callback)
            return lambda:clocks.remove(callback)
        events.async_track_time_change=track
        events.async_track_state_change_event=lambda *args,**kwargs:lambda:None
        injected.update({m.__name__:m for m in [component,sensor,entity,dispatch,events]})
        added=[]
        async def forward(root,platforms):
            self.assertEqual(platforms,['sensor'])
            sensors=importlib.import_module('custom_components.ha_bems.sensor')
            await sensors.async_setup_entry(self.hass,root,added.extend)
        self.hass.config_entries.async_forward_entry_setups=forward
        self.hass.config_entries.async_unload_platforms=AsyncMock(return_value=True)
        with patch.dict('sys.modules',injected),patch.object(wc.dt_util,'now',return_value=NOW):
            self.assertTrue(await async_setup_entry(self.hass,e))
            cache._request.assert_awaited_once()
            self.assertEqual(len(clocks),1)
            self.assertEqual(len(added),1)
            output=added[0]
            self.assertTrue(output.coordinator.last_update_success,str(output.coordinator.last_exception))
            self.assertEqual(output.native_value,156.5)
            self.assertEqual(len(output.extra_state_attributes['forecast']),289)
            self.assertEqual(output._attr_native_unit_of_measurement,'kWh/timestep')
            clocks[0](None)
            await asyncio.gather(*tasks)
            cache._request.assert_awaited_once()
            self.assertTrue(await async_unload_entry(self.hass,e))
            for callback in e.callbacks:callback()
            self.assertEqual(clocks,[])
            output.coordinator.last_update_success=False
            output.coordinator.last_exception=ValueError('Weather exhausted')
            self.assertIsNone(output.native_value)
            self.assertNotIn('forecast',output.extra_state_attributes)

    async def test_backup_persistence_error_does_not_discard_fresh_weather(self):
        cache=wc.weather_cache(self.hass,SETTINGS)
        cache._request=AsyncMock(return_value=payload())
        cache.store.async_save=AsyncMock(side_effect=OSError('disk full'))
        weather,metadata=await cache.async_get(NOW)
        self.assertEqual(weather['wind_speed_80m'][0],8)
        self.assertIsNotNone(metadata['cache_storage_error'])
        self.assertFalse(metadata['using_cached_backup'])


class PrivateWindSetupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp_root=Path(__file__).resolve().parents[1]/'local_data'
        temp_root.mkdir(exist_ok=True)
        self.directory=temp_root/f'wind-access-test-{uuid4().hex}'
        self.directory.mkdir()
        assert self.directory.resolve().is_relative_to(temp_root.resolve())
        self.addCleanup(self.directory.rmdir)
        self.hass=Hass()
        self.hass.config=SimpleNamespace(latitude=47.2,longitude=15.8,
            path=lambda filename:str(self.directory/filename))
        self.password='private-test-password'
        self.path=self.directory/'release_password.json'
        self.addCleanup(lambda:self.path.unlink(missing_ok=True))
        self.record=password_record(self.password)
        self.path.write_text(json.dumps(self.record),encoding='utf-8')
        # HA boundary fixtures can reload modules; patch the globals belonging
        # to the exact functions retained by the already-imported flow class.
        for target,values in (
            (verify_password.__globals__,{'PASSWORD_RECORD_FILE':self.path}),
            (async_unlock.__globals__,{'verify_password':verify_password}),
            (HABEMSConfigFlow.async_step_eeg_wind.__globals__,{'async_unlock':async_unlock}),
        ):
            context=patch.dict(target,values)
            context.start();self.addCleanup(context.stop)

    async def test_password_hash_and_missing_file_fail_closed(self):
        self.assertNotIn(self.password,self.path.read_text())
        self.assertIsNone(verify_password(self.password))
        self.assertEqual(verify_password('wrong'),'invalid_wind_password')
        self.path.unlink()
        self.assertEqual(verify_password(self.password),'wind_setup_disabled')

    async def test_same_release_password_on_all_installations_no_local_override(self):
        other=Hass()
        # No password provisioning or configuration path is needed on either HA.
        other.config=SimpleNamespace(latitude=0,longitude=0)
        self.assertIsNone(await async_unlock(self.hass,self.password))
        self.assertIsNone(await async_unlock(other,self.password))
        # A password set by an installation owner cannot replace the release key.
        local=self.directory/'ha_bems_eeg_setup_password.json'
        local.write_text(json.dumps(password_record('different-local-password')),encoding='utf-8')
        self.addCleanup(lambda:local.unlink(missing_ok=True))
        self.hass.config.path=lambda filename:str(local)
        self.assertEqual(await async_unlock(self.hass,'different-local-password'),'invalid_wind_password')
        self.assertIsNone(await async_unlock(self.hass,self.password))

    async def test_gate_rate_limits_across_different_flows(self):
        for _ in range(4): self.assertEqual(await async_unlock(self.hass,'wrong'),'invalid_wind_password')
        self.assertEqual(await async_unlock(self.hass,'wrong'),'wind_password_locked')
        # A fresh flow and even a correct password cannot bypass the cooldown.
        other=HABEMSConfigFlow();other.hass=self.hass
        form=await other.async_step_eeg_wind({'password':self.password})
        self.assertEqual(form['errors']['base'],'wind_password_locked')

    async def test_setup_cannot_be_bypassed_and_never_saves_password(self):
        flow=HABEMSConfigFlow();flow.hass=self.hass
        form=await flow.async_step_eeg_wind_settings({'name':'Bypass',**SETTINGS,'wind_curve_text':curve_text()})
        self.assertEqual(form['step_id'],'eeg_wind')
        self.assertEqual(flow._finish(MODULE_EEG_WIND)['reason'],'wind_setup_locked')
        form=await flow.async_step_eeg_wind({'password':self.password})
        self.assertEqual(form['step_id'],'eeg_wind_settings')
        self.assertNotIn('password',flow._values())
        # Exact user-facing settings, not the internal curve list.
        values={key:value for key,value in SETTINGS.items() if key!='wind_power_points'}
        result=await flow.async_step_eeg_wind_settings({'name':'My wind forecast',**values,'wind_curve_text':curve_text()})
        self.assertEqual(result['type'],'create_entry')
        self.assertEqual(result['data']['module'],MODULE_EEG_WIND)
        self.assertNotIn('password',json.dumps(result))
        self.assertNotIn('wind_curve_text',result['data'])
        self.assertEqual(result['data']['wind_power_points'],SETTINGS['wind_power_points'])

    async def test_options_reconfiguration_requires_unlock_and_invalid_curve_stays_editable(self):
        flow=HABEMSOptionsFlow();flow.hass=self.hass
        flow.config_entry=entry('wind',MODULE_EEG_WIND,**SETTINGS)
        self.assertEqual((await flow.async_step_init())['step_id'],'eeg_wind')
        await flow.async_step_eeg_wind({'password':self.password})
        result=await flow.async_step_eeg_wind_settings({**SETTINGS,'wind_curve_text':'1;0\n1;2'})
        self.assertEqual(result['errors']['base'],'invalid_wind_settings')
        self.assertEqual(result['step_id'],'eeg_wind_settings')
