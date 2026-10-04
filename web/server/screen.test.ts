import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer, type RequestListener } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { ViteDevServer } from 'vite';
import { veyraScreenStream } from './screen.ts';

test('fetch gets binary content type while direct links retain native MJPEG', async () => {
  // Never capture the owner's desktop, even if the loop is already resolving
  // a utility when this synthetic viewer disconnects.
  const previousPath = process.env.PATH;
  process.env.PATH = '';
  // Drop the viewer synchronously before the loop can spawn.
  let middleware: RequestListener;
  const server = createServer((request, response) => {
    middleware(request, response);
    response.end();
    response.emit('close');
  });
  const plugin = veyraScreenStream();
  const configure = plugin.configureServer as (server: ViteDevServer) => void;
  configure({ middlewares: { use(handler: RequestListener) { middleware = handler; } }, httpServer: server } as unknown as ViteDevServer);
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    for (const [query, type] of [['?transport=fetch', 'application/octet-stream'], ['', 'multipart/x-mixed-replace; boundary=veyraframe']]) {
      const response = await fetch(`${origin}/veyra-screen.mjpeg${query}`);
      assert.equal(response.headers.get('content-type'), type);
      assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
      assert.match(response.headers.get('cache-control')!, /no-store/);
      await response.arrayBuffer();
    }
  } finally {
    if (previousPath === undefined) delete process.env.PATH;
    else process.env.PATH = previousPath;
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
