"""The user bypass restores across restarts and is independent of source health."""

import importlib
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from tests.ha_stubs import modules


class Entity:
    async def async_added_to_hass(self):
        pass

    def async_write_ha_state(self):
        pass


class SwitchEntity(Entity):
    pass


class RestoreEntity(Entity):
    async def async_get_last_state(self):
        return self.restored_state


class SwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_on_restore_off_and_user_toggle(self):
        injected = modules()
        component = ModuleType('homeassistant.components')
        switches = ModuleType('homeassistant.components.switch')
        switches.SwitchEntity = SwitchEntity
        restore = ModuleType('homeassistant.helpers.restore_state')
        restore.RestoreEntity = RestoreEntity
        entity = ModuleType('homeassistant.helpers.entity')
        entity.DeviceInfo = lambda **kwargs: kwargs
        injected.update({'homeassistant.components': component,
                         'homeassistant.components.switch': switches,
                         'homeassistant.helpers.restore_state': restore,
                         'homeassistant.helpers.entity': entity})
        choices = []
        async def set_enabled(value):
            choices.append(value)
        plan = SimpleNamespace(async_set_limitation_enabled=set_enabled)
        with patch.dict('sys.modules', injected):
            module = importlib.import_module('custom_components.ha_bems.switch')
            for old, expected in [(None, True), ('off', False), ('on', True), ('unavailable', True)]:
                item = module.FBCEnabledSwitch(plan, SimpleNamespace(entry_id='battery', title='Battery'))
                item.restored_state = None if old is None else SimpleNamespace(state=old)
                await item.async_added_to_hass()
                self.assertEqual(item._attr_is_on, expected)
                self.assertEqual(choices[-1], expected)
            await item.async_turn_off()
            self.assertFalse(item._attr_is_on)
            self.assertFalse(choices[-1])
            await item.async_turn_on()
            self.assertTrue(item._attr_is_on)
            self.assertTrue(choices[-1])
