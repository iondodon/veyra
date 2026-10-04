import { expect, test, type Page } from '@playwright/test';
import sharp from 'sharp';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { encodeVideo } from '../server/video';

let frameServer: Server;
let frameOrigin: string;
let activeStreams = 0;
test.beforeAll(async () => {
  const frames = await Promise.all(['green', 'blue'].map((background) => sharp({ create: { width: 640, height: 400, channels: 3, background } }).jpeg().toBuffer()));
  frameServer = createServer((request, response) => {
    if (request.url?.includes('failFirst=1') && request.url.includes('attempt=0')) { response.writeHead(503); response.end('unavailable'); return; }
    response.writeHead(200, { 'Content-Type': 'video/mp4', 'Access-Control-Allow-Origin': '*' });
    const encoder = encodeVideo(response, 8);
    activeStreams++;
    let count = 0;
    const timer = setInterval(() => encoder.write(frames[Math.floor(count++ / 8) % 2]), 125);
    response.on('close', () => { clearInterval(timer); encoder.end(); activeStreams--; });
  });
  await new Promise<void>((resolve) => frameServer.listen(0, '127.0.0.1', resolve));
  frameOrigin = `http://127.0.0.1:${(frameServer.address() as AddressInfo).port}`;
});
test.afterAll(async () => {
  frameServer.closeAllConnections();
  await new Promise<void>((resolve) => frameServer.close(() => resolve()));
});

async function syntheticVideo(page: Page, failFirst = false) {
  // Redirecting an endless media response through Playwright interception can
  // buffer it forever. Fetch the stream directly from the fixture server.
  await page.addInitScript(({ origin, failFirst }) => {
    const original = window.fetch;
    window.fetch = (input, init) => {
      const value = typeof input === 'string' ? input : '';
      const source = value.startsWith('/veyra-screen.mp4?')
        ? `${origin}/?${value.split('?')[1]}&failFirst=${failFirst ? 1 : 0}` : input;
      return original(source, init);
    };
  }, { origin: frameOrigin, failFirst });
}

async function watch(page: Page) {
  // Feed synthetic frames through the SAME encoder used by the desktop endpoint.
  // Tests never capture the owner's screen.
  await syntheticVideo(page);
  await page.route('**/veyra-status.json', (route) => route.fulfill({ json: {
    schema_version: 1, updated_at: new Date().toISOString(),
    agent: { state: 'running', pid: 123 },
    revision: { commit: 'abcdef1234567890', short: 'abcdef123456', branch: 'main', message: 'Test version', committed_at: new Date().toISOString() },
    runtime: { provider: 'openai', model: 'test-model', recent_message_count: 0 }, versions: [],
  } }));
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.stream-status')).toHaveText('Live video', { timeout: 20000 });
}

async function motion(page: Page) {
  const video = page.getByLabel('Live local desktop');
  const pixel = () => video.evaluate((element: HTMLVideoElement) => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
    const context = canvas.getContext('2d')!;
    context.drawImage(element, 0, 0, 1, 1);
    return Array.from(context.getImageData(0, 0, 1, 1).data).join(',');
  });
  const before = await pixel();
  await expect.poll(pixel, { timeout: 10000 }).not.toBe(before);
}

test('one simple page shows version, active model and actual live video automatically', async ({ page }) => {
  await watch(page);
  await expect(page.getByRole('heading', { name: 'Veyra', exact: true })).toBeVisible();
  await expect(page.getByText('abcdef123456', { exact: true })).toBeVisible();
  await expect(page.getByText('test-model', { exact: true })).toBeVisible();
  await expect(page.getByRole('navigation')).toHaveCount(0);
  await expect(page.getByRole('link')).toHaveCount(0);
  await motion(page);
});

for (const mode of ['native', 'fallback'] as const) {
  test(`video keeps playing through ${mode} fullscreen and back without reconnecting`, async ({ page }) => {
    if (mode === 'fallback') await page.addInitScript(() => {
      Object.defineProperty(Element.prototype, 'requestFullscreen', { configurable: true, value: undefined });
    });
    let connections = 0;
    page.on('request', (request) => { if (request.url().startsWith(frameOrigin)) connections++; });
    await watch(page);
    await motion(page);
    await page.getByRole('button', { name: 'Maximize video' }).click();
    if (mode === 'native') await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true);
    const viewer = page.getByRole('region', { name: 'Desktop viewer' });
    await expect(viewer).toHaveClass(/expanded/);
    expect(await viewer.boundingBox()).toEqual({ x: 0, y: 0, width: 1280, height: 800 });
    await motion(page);
    await page.getByRole('button', { name: 'Exit video fullscreen' }).click();
    await expect(viewer).not.toHaveClass(/expanded/);
    await motion(page);
    expect(connections).toBe(1);
  });
}

for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
  test(`entire inline video fits at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await watch(page);
    const video = page.getByLabel('Live local desktop');
    await expect(video).toHaveCSS('object-fit', 'contain');
    const bounds = (await video.boundingBox())!;
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
  });
}

test('closing the page disconnects the encoder', async ({ page }) => {
  await expect.poll(() => activeStreams).toBe(0);
  await watch(page);
  await expect.poll(() => activeStreams).toBe(1);
  await page.goto('about:blank');
  await expect.poll(() => activeStreams).toBe(0);
});

test('load errors offer reconnect', async ({ page }) => {
  await syntheticVideo(page, true);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('button', { name: 'Reconnect' })).toBeVisible();
  await page.getByRole('button', { name: 'Reconnect' }).click();
  await expect(page.locator('.stream-status')).toHaveText('Live video', { timeout: 20000 });
});

test('iPhone-style video fullscreen uses the native video API', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(Element.prototype, 'requestFullscreen', { configurable: true, value: undefined });
    Object.defineProperty(HTMLVideoElement.prototype, 'webkitEnterFullscreen', { configurable: true, value() { this.dataset.nativeFullscreen = 'true'; } });
    Object.defineProperty(HTMLVideoElement.prototype, 'webkitExitFullscreen', { configurable: true, value() { this.dataset.nativeFullscreen = 'false'; } });
  });
  await watch(page);
  await page.getByRole('button', { name: 'Maximize video' }).click();
  await expect(page.getByLabel('Live local desktop')).toHaveAttribute('data-native-fullscreen', 'true');
  await page.getByRole('button', { name: 'Exit video fullscreen' }).click();
  await expect(page.getByLabel('Live local desktop')).toHaveAttribute('data-native-fullscreen', 'false');
});


test('ManagedMediaSource-only browsers play through the streaming API', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'ManagedMediaSource', { configurable: true, value: window.MediaSource });
    Object.defineProperty(window, 'MediaSource', { configurable: true, value: undefined });
  });
  await watch(page);
  await motion(page);
});
