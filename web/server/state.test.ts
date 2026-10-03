import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { test } from 'node:test';
import { readConversation, veyraLocalState } from './state.ts';

test('conversation endpoint bounds and sanitizes persisted memory', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'veyra-memory-'));
  const path = join(directory, 'recent_messages.json');
  try {
    assert.deepEqual(await readConversation(path), { schema_version: 1, limit: 20, messages: [] });
    await writeFile(path, JSON.stringify({ version: 1, limit: 2, messages: [
      { role: 'user', content: 'old' },
      { role: 'system', content: 'hidden' },
      null,
      { role: 'assistant', content: 'retained', commit: 'abc123', private_field: 'hidden' },
      { role: 'user', content: 'new', commit: 123 },
    ] }));
    assert.deepEqual(await readConversation(path), { schema_version: 1, limit: 2, messages: [
      { role: 'assistant', content: 'retained', commit: 'abc123' },
      { role: 'user', content: 'new' },
    ] });
    await writeFile(path, '{broken');
    await assert.rejects(readConversation(path), SyntaxError);
    await writeFile(path, JSON.stringify({ version: 2, messages: [] }));
    await assert.rejects(readConversation(path), /Unsupported/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('state endpoints reject writes and do not alter local conversation', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'veyra-endpoint-'));
  await mkdir(join(directory, 'memory'));
  const path = join(directory, 'memory/recent_messages.json');
  const before = JSON.stringify({ version: 1, messages: [{ role: 'user', content: 'Preserve me' }] });
  await writeFile(path, before);
  let handler: (...args: unknown[]) => Promise<void>;
  const plugin = veyraLocalState(pathToFileURL(`${directory}/`));
  const configure = plugin.configureServer as (server: unknown) => void;
  configure({ middlewares: { use(value: typeof handler) { handler = value; } } });
  const headers: Record<string, string> = {};
  const response = {
    statusCode: 200,
    setHeader(key: string, value: string) { headers[key] = value; },
    end() {},
  };
  try {
    await handler!({ url: '/veyra-context.json', method: 'POST' }, response, () => assert.fail('route skipped'));
    assert.equal(response.statusCode, 405);
    assert.equal(headers.Allow, 'GET, HEAD');
    assert.equal(await readFile(path, 'utf8'), before);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
