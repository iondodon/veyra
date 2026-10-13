const {test} = require('node:test');
const assert = require('node:assert/strict');
const {normalizeServer, createClient, mergeSnapshot} = require('../client');
test('addresses cannot embed credentials or paths', () => {
  assert.equal(normalizeServer(' http://192.168.1.2:3000/ '), 'http://192.168.1.2:3000');
  for (const url of ['ftp://host', 'http://user:secret@host', 'http://host/foo', 'http://host?a=b', 'http://host/#secret']) assert.throws(() => normalizeServer(url));
});
test('native messages retain web authentication and origin checks', async () => {
  let called;
  const client = createClient('http://192.168.1.2:3000', 'päss', async (url, options) => {called = {url, options}; return {ok: true, json: async () => ({ok: true})};});
  await client('/api/messages', {text: '/approve', approval_id: 'active'});
  assert.equal(called.options.headers.Origin, 'http://192.168.1.2:3000');
  assert.equal(Buffer.from(called.options.headers.Authorization.slice(6), 'base64').toString(), 'veyra:päss');
  assert.equal(JSON.parse(called.options.body).approval_id, 'active');
});
test('failed sends are not silently retried', async () => {
  let calls = 0;
  const client = createClient('http://host', 'password', async () => {calls++; return {ok: false, status: 409, json: async () => ({error: 'Stale approval'})};});
  await assert.rejects(client('/api/messages', {text: '/approve'}), /Stale approval/);
  assert.equal(calls, 1);
});
test('unauthorized errors do not expose server details', async () => {
  const client = createClient('http://host', 'wrong', async () => ({ok: false, status: 401, json: async () => ({})}));
  await assert.rejects(client('/api/events'), /Incorrect Veyra network password/);
});
test('events deduplicate and restart replaces old approvals', () => {
  const previous = {events: [{id: 1, kind: 'approval'}], cursor: 1, status: {session_id: 'a'}};
  assert.equal(mergeSnapshot(previous, {events: [{id: 1}, {id: 2}], cursor: 2, status: {session_id: 'a'}}).events.length, 2);
  assert.deepEqual(mergeSnapshot(previous, {events: [{id: 3}], cursor: 3, status: {session_id: 'b'}}).events, [{id: 3}]);
});
