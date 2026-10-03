import assert from 'node:assert/strict';
import { test } from 'node:test';
import { isConversationContext, isRuntimeStale, isRuntimeStatus, isScreenStatus, type RuntimeStatus } from './veyra.ts';

const status: RuntimeStatus = {
  schema_version: 1, updated_at: '2026-10-03T10:00:00Z',
  agent: { state: 'running', pid: 123 },
  revision: { commit: 'abcdef012345', short: 'abcdef0', branch: 'main', message: 'Version', committed_at: null },
  runtime: { provider: 'openai', model: 'example', recent_message_count: 0 }, versions: [],
};

test('invalid local state is rejected without throwing in the workspace', () => {
  assert.equal(isRuntimeStatus(status), true);
  for (const value of [null, [], {}, { ...status, versions: [null] }, { ...status, runtime: { provider: {} } }, { ...status, agent: { state: 'running', pid: '123' } }]) {
    assert.equal(isRuntimeStatus(value), false);
  }
  assert.equal(isConversationContext({ schema_version: 1, limit: 20, messages: [{ role: 'user', content: 'Hello' }] }), true);
  assert.equal(isConversationContext({ schema_version: 1, limit: 20, messages: [null] }), false);
  assert.equal(isConversationContext({ schema_version: 1, limit: 20, messages: [{ role: 'system', content: 'Not a chat message' }] }), false);
  assert.equal(isScreenStatus({ schema_version: 1, available: true, settings: null }), false);
});

test('an old running snapshot is stale rather than evidence of an online agent', () => {
  const start = Date.parse(status.updated_at);
  assert.equal(isRuntimeStale(status, start + 10_000), false);
  assert.equal(isRuntimeStale(status, start + 16_000), true);
});
