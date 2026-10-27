"""Regression: imported-input notifications must execute on the HA event loop."""

import asyncio
import importlib
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from tests.ha_stubs import modules


class Entity:
    def async_on_remove(self, callback):
        self.removals.append(callback)


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator=coordinator
        self.removals=[]

    async def async_added_to_hass(self):
        pass


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_snapshot_recovers_fbc_on_event_loop(self):
        injected=modules()
        def callback(function):
            function._hass_callback=True
            return function
        injected['homeassistant.core'].callback=callback
        component=ModuleType('homeassistant.components')
        sensor=ModuleType('homeassistant.components.sensor');sensor.SensorEntity=Entity
        entity=ModuleType('homeassistant.helpers.entity')
        entity.DeviceInfo=lambda **kwargs: kwargs
        entity.EntityCategory=SimpleNamespace(DIAGNOSTIC='diagnostic')
        handlers=[]
        dispatcher=ModuleType('homeassistant.helpers.dispatcher')
        def connect(hass, signal, target):
            handlers.append(target)
            return lambda: handlers.remove(target)
        dispatcher.async_dispatcher_connect=connect
        events=ModuleType('homeassistant.helpers.event')
        events.async_track_time_change=lambda *args,**kwargs: (lambda: None)
        events.async_track_state_change_event=lambda *args,**kwargs: (lambda: None)
        injected['homeassistant.helpers.update_coordinator'].CoordinatorEntity=CoordinatorEntity
        injected.update({'homeassistant.components':component,'homeassistant.components.sensor':sensor,
            'homeassistant.helpers.entity':entity,'homeassistant.helpers.dispatcher':dispatcher,
            'homeassistant.helpers.event':events,'holidays':ModuleType('holidays')})
        owner=threading.get_ident()
        tasks=[]
        def create_task(coroutine):
            if threading.get_ident()!=owner:
                coroutine.close()
                raise RuntimeError('async_create_task called outside event loop')
            tasks.append(asyncio.create_task(coroutine))
        class Plan:
            config={'soc_entity':'sensor.soc','seasonal_soc_entity':'sensor.minimum',
                    'pv_import_entry_id':'shared-pv'}
            snapshot_ready=False
            state='unavailable'
            async def async_request_refresh(self):
                self.state='ready' if self.snapshot_ready else 'unavailable'
        with patch.dict('sys.modules',injected):
            module=importlib.import_module('custom_components.ha_bems.sensor')
            coordinator=Plan()
            item=module.BEMSForecastSensor(coordinator,SimpleNamespace(entry_id='battery',title='Battery'),'battery_fbc')
            item.hass=SimpleNamespace(async_create_task=create_task)
            await item.async_added_to_hass()
            await asyncio.gather(*tasks);tasks.clear()
            self.assertEqual(coordinator.state,'unavailable')
            coordinator.snapshot_ready=True
            # HA dispatches callbacks on the loop; plain functions/lambdas go
            # to its executor. Exercise this distinction instead of assuming
            # every registered function is safe to run directly.
            handler=handlers[0]
            if getattr(handler,'_hass_callback',False):
                handler()
            else:
                await asyncio.to_thread(handler)
            await asyncio.gather(*tasks)
            self.assertEqual(coordinator.state,'ready')
            self.assertTrue(getattr(handler,'_hass_callback',False))
