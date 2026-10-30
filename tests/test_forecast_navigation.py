"""Navigation retains provider ownership and resolves renamed status entities."""

from importlib import import_module
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tests.ha_stubs import modules
from tests.test_importer import Hass, entry
from custom_components.ha_bems.navigation import (
    absolute_source_url, forecast_source_rows, source_entity_path, source_entity_url,
)


class ForecastNavigationTests(unittest.TestCase):
    def test_links_encode_entity_ids_and_only_use_web_urls(self):
        self.assertEqual(source_entity_path('sensor.pv'), '/ha-bems-forecasts?more-info-entity-id=sensor.pv')
        self.assertIn('%26', source_entity_path('sensor.a&b'))
        self.assertEqual(absolute_source_url('https://ha.example/', 'sensor.pv'),
                         'https://ha.example/ha-bems-forecasts?more-info-entity-id=sensor.pv')
        for base in (None, '', 'javascript:alert(1)', 'homeassistant://config'):
            self.assertIsNone(absolute_source_url(base, 'sensor.pv'))

    def test_source_link_uses_configured_url_and_handles_no_url(self):
        network = ModuleType('homeassistant.helpers.network')
        network.NoURLAvailableError = type('NoURLAvailableError', (Exception,), {})
        network.get_url = Mock(return_value='https://ha.example')
        injected = modules()
        injected[network.__name__] = network
        with patch.dict('sys.modules', injected):
            self.assertTrue(source_entity_url(object(), 'sensor.pv').startswith('https://ha.example/'))
            network.get_url.assert_called_once_with(unittest.mock.ANY, prefer_external=True)
            network.get_url.side_effect = network.NoURLAvailableError
            self.assertIsNone(source_entity_url(object(), 'sensor.pv'))

    def test_overview_filters_creators_retains_disabled_and_resolves_status_unique_id(self):
        hass = Hass()
        root = entry('installation', layout='flat_devices', items={
            'pv': {'title': 'Roof AC', 'data': {'module': 'pv_forecast_import', 'forecast_entity': 'sensor.roof', 'pv_power_type': 'ac'}},
            'price': {'title': 'Import price', 'data': {'module': 'electricity_import_price_forecast', 'forecast_entity': 'sensor.cs_import', 'enabled': False}},
            'helper': {'title': 'Helper', 'data': {'module': 'non_flexible_load'}},
        })
        hass.entries = [root]
        registry = SimpleNamespace(async_get_entity_id=Mock(side_effect=lambda domain, integration, unique: 'sensor.renamed_' + unique))
        devices = SimpleNamespace()
        injected = modules()
        er = ModuleType('homeassistant.helpers.entity_registry')
        er.async_get = lambda _: registry
        dr = ModuleType('homeassistant.helpers.device_registry')
        dr.async_get = lambda _: devices
        dr.async_entries_for_config_entry = lambda _, entry_id: [SimpleNamespace(id='importer-device', identifiers={('ha_bems', 'service:entity_importer')})]
        injected[er.__name__] = er
        injected[dr.__name__] = dr
        injected['homeassistant.helpers'].entity_registry = er
        injected['homeassistant.helpers'].device_registry = dr
        with patch.dict('sys.modules', injected):
            result = forecast_source_rows(hass)
        self.assertEqual(result['importer_device_id'], 'importer-device')
        self.assertEqual([row['item_id'] for row in result['inputs']], ['price', 'pv'])
        self.assertFalse(result['inputs'][0]['enabled'])
        self.assertEqual(result['inputs'][1]['status_entity'], 'sensor.renamed_pv_input_status')
        self.assertEqual(result['inputs'][1]['source_entity'], 'sensor.roof')
        self.assertTrue(all('forecast' not in row for row in result['inputs']))


class ForecastPanelSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_and_read_only_endpoint_register_once_with_admin_access(self):
        injected = modules()
        injected['homeassistant.components'] = ModuleType('homeassistant.components')
        panel = ModuleType('homeassistant.components.panel_custom')
        panel.async_register_panel = AsyncMock()
        ws = ModuleType('homeassistant.components.websocket_api')
        ws.websocket_command = lambda schema: lambda func: func
        ws.require_admin = Mock(side_effect=lambda func: func)
        ws.async_register_command = Mock()
        http = ModuleType('homeassistant.components.http')
        http.StaticPathConfig = lambda *args: args
        for module in (panel, ws, http):
            injected[module.__name__] = module
            setattr(injected['homeassistant.components'], module.__name__.split('.')[-1], module)
        hass = SimpleNamespace(data={}, http=SimpleNamespace(async_register_static_paths=AsyncMock()))
        with patch.dict('sys.modules', injected):
            # Reload with the isolated HA boundaries rather than retaining stubs.
            with patch.dict('sys.modules'):
                import sys
                sys.modules.pop('custom_components.ha_bems.frontend', None)
                frontend = import_module('custom_components.ha_bems.frontend')
                await frontend.async_setup_frontend(hass)
                await frontend.async_setup_frontend(hass)
                with patch.object(frontend, 'forecast_source_rows', return_value={'inputs': []}):
                    connection = SimpleNamespace(send_result=Mock())
                    frontend.websocket_forecast_sources(hass, connection, {'id': 7})
                    connection.send_result.assert_called_once_with(7, {'inputs': []})
        panel.async_register_panel.assert_awaited_once()
        self.assertTrue(panel.async_register_panel.call_args.kwargs['require_admin'])
        self.assertIsNone(panel.async_register_panel.call_args.kwargs['sidebar_title'])
        ws.require_admin.assert_called_once()
        ws.async_register_command.assert_called_once()
        hass.http.async_register_static_paths.assert_awaited_once()
