const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');

test('screen viewer is opt-in, pauses when hidden, resumes and clears on stop', async () => {
  const elements = {};
  for (const id of ['screen-toggle', 'screen-status', 'host-screen']) elements[id] = {
    hidden: true, events: {}, addEventListener(name, fn) { this.events[name] = fn; },
    removeAttribute(name) { delete this[name]; }
  };
  const events = {};
  const document = {hidden: false, querySelector: selector => elements[selector.slice(1)], addEventListener: (name, fn) => events[name] = fn};
  let calls = 0, revoked = 0, timer;
  vm.runInNewContext(fs.readFileSync('agent/screen.js', 'utf8'), {
    document, window: {addEventListener: (name, fn) => events[name] = fn}, AbortController, Date,
    URL: {createObjectURL: () => 'blob:screen', revokeObjectURL: () => revoked++},
    setTimeout: fn => {timer = fn; return 1;}, clearTimeout: () => {timer = null;},
    fetch: async (path, options) => {calls++; assert.equal(path, '/api/screen'); assert.equal(options.method, 'POST'); return {ok: true, blob: async () => ({})};}
  });
  const tick = () => new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 0);
  elements['screen-toggle'].events.click(); await tick();
  assert.equal(calls, 1); assert.equal(elements['host-screen'].hidden, false);
  assert.equal(typeof timer, 'function');
  document.hidden = true; events.visibilitychange();
  assert.equal(elements['host-screen'].hidden, true); assert.equal(timer, null); assert.equal(revoked, 1);
  document.hidden = false; events.visibilitychange(); await tick();
  assert.equal(calls, 2);
  elements['screen-toggle'].events.click();
  assert.equal(elements['host-screen'].hidden, true); assert.equal(timer, null); assert.equal(revoked, 2);
  assert.equal(elements['screen-status'].textContent, 'Not sharing.');
});
