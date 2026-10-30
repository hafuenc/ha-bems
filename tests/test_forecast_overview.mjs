import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
import test from 'node:test';

globalThis.HTMLElement = class {};
globalThis.customElements = { define() {} };
const code = await readFile(new URL('../custom_components/ha_bems/frontend/forecast-overview.js', import.meta.url), 'utf8');
const { inputState, entityPath } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

test('source and status identities stay separate, including disabled and missing inputs', () => {
  const input = { enabled: true, source_entity: 'sensor.pv', status_entity: 'sensor.renamed_status' };
  const states = { 'sensor.pv': { state: '1200' }, 'sensor.renamed_status': { state: 'ready' } };
  assert.equal(inputState(input, states).source.state, '1200');
  assert.equal(inputState(input, states).label, 'ready');
  assert.equal(inputState({ ...input, enabled: false }, states).label, 'Disabled');
  assert.equal(inputState(input, {}).label, 'Unavailable');
});

test('new-tab source links retain HA entity-dialog deep links', () => {
  const url = new URL(entityPath('sensor.a&b'), 'https://ha.example');
  assert.equal(url.origin, 'https://ha.example');
  assert.equal(url.searchParams.get('more-info-entity-id'), 'sensor.a&b');
});
