import assert from 'node:assert/strict';
import { test } from 'node:test';
import { jpegFrames } from './mjpeg.ts';

function stream(chunks: Uint8Array[]) {
  return new ReadableStream<Uint8Array>({ start(controller) {
    for (const chunk of chunks) controller.enqueue(chunk);
    controller.close();
  } });
}
const wire = new TextEncoder().encode('--veyraframe\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n\r\nabc\r\n--veyraframe\r\nContent-Length: 2\r\n\r\nde\r\n');
for (const chunkSize of [1, 7, wire.length]) {
  test(`decodes multiple frames with ${chunkSize}-byte chunks`, async () => {
    const chunks: Uint8Array[] = [];
    for (let i = 0; i < wire.length; i += chunkSize) chunks.push(wire.slice(i, i + chunkSize));
    const received: string[] = [];
    await assert.rejects(async () => {
      for await (const frame of jpegFrames(stream(chunks))) received.push(new TextDecoder().decode(frame));
    }, /stream closed/);
    assert.deepEqual(received, ['abc', 'de']);
  });
}
test('rejects invalid or unbounded frame lengths and headers', async () => {
  for (const input of ['Content-Length: 0\r\n\r\n', 'Content-Length: 999999999\r\n\r\n', 'Content-Type: image/jpeg\r\n\r\n', 'a'.repeat(5000)]) {
    await assert.rejects(async () => { for await (const frame of jpegFrames(stream([new TextEncoder().encode(input)]))) void frame; }, /Invalid desktop frame/);
  }
});
test('cancels the underlying stream when the viewer stops', async () => {
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(wire); }, cancel() { cancelled = true; } });
  for await (const frame of jpegFrames(body)) { assert.equal(frame.length, 3); break; }
  assert.equal(cancelled, true);
});
