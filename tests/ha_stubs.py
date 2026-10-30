"""Small HA boundaries for exercising integration logic without an HA server."""

from types import ModuleType, SimpleNamespace
from dataclasses import dataclass, field
from uuid import uuid4
from tests.test_forecast_input import NOW


class Flow:
    def __init_subclass__(cls, **kwargs):
        pass

    def async_show_form(self, **kwargs):
        return {'type': 'form', **kwargs}

    def async_show_menu(self, **kwargs):
        return {'type': 'menu', **kwargs}

    def async_create_entry(self, **kwargs):
        return {'type': 'create_entry', **kwargs}

    def async_abort(self, **kwargs):
        return {'type': 'abort', **kwargs}

    async def async_set_unique_id(self, value):
        self.unique_id = value

    def _abort_if_unique_id_configured(self):
        pass


class OptionsFlow(Flow):
    pass


class ConfigSubentryFlow(Flow):
    def _get_entry(self):
        return self.parent

    def _get_reconfigure_subentry(self):
        return self.subentry

    def async_update_and_abort(self, parent, subentry, **kwargs):
        self.hass.config_entries.async_update_subentry(parent, subentry, **kwargs)
        return self.async_abort(reason='reconfigure_successful')


@dataclass
class ConfigSubentry:
    data: object
    title: str
    subentry_type: str
    unique_id: str | None
    subentry_id: str = field(default_factory=lambda: uuid4().hex)


class Selector:
    def __init__(self, config):
        self.config = config

    def __call__(self, value):
        return value


class Coordinator:
    def __init__(self, hass, logger, **kwargs):
        self.hass = hass
        self.name = kwargs.get('name')
        self.data = None
        self.last_update_success = True
        self.last_exception = None
        self.listeners = []

    def async_add_listener(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    async def async_refresh(self):
        previous_success = self.last_update_success
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
            self.last_exception = None
        except UpdateFailed as err:
            self.last_update_success = False
            self.last_exception = err
        if hasattr(self, '_async_refresh_finished'):
            self._async_refresh_finished()
        if not self.last_update_success and not previous_success:
            return
        self.async_update_listeners()

    def async_update_listeners(self):
        for listener in list(self.listeners):
            listener()

    async def async_request_refresh(self):
        await self.async_refresh()


class UpdateFailed(Exception):
    pass


def modules():
    ha = ModuleType('homeassistant')
    entries = ModuleType('homeassistant.config_entries')
    entries.ConfigFlow = Flow
    entries.OptionsFlow = OptionsFlow
    entries.ConfigSubentryFlow = ConfigSubentryFlow
    entries.ConfigSubentry = ConfigSubentry
    ha.config_entries = entries
    core = ModuleType('homeassistant.core')
    core.callback = lambda function: function
    helpers = ModuleType('homeassistant.helpers')
    helpers.entity_registry = SimpleNamespace()
    helpers.selector = SimpleNamespace(**{name: Selector for name in [
        'NumberSelector', 'SelectSelector', 'EntitySelector', 'TextSelector']})
    util = ModuleType('homeassistant.util')
    util.dt = SimpleNamespace(now=lambda: NOW)
    update = ModuleType('homeassistant.helpers.update_coordinator')
    update.DataUpdateCoordinator = Coordinator
    update.UpdateFailed = UpdateFailed
    return {'homeassistant': ha, 'homeassistant.config_entries': entries,
            'homeassistant.core': core, 'homeassistant.helpers': helpers,
            'homeassistant.util': util, 'homeassistant.helpers.update_coordinator': update}
