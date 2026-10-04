import { expect, test, type Page } from '@playwright/test';
import sharp from 'sharp';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

let frameServer: Server;
let frameOrigin: string;
let activeStreams = 0;
test.beforeAll(async () => {
  // An actual ongoing multipart response, not a single static image. Never
  // capture the owner's desktop in browser tests.
  frameServer = createServer(async (request, response) => {
    const url = new URL(request.url!, 'http://localhost');
    const width = url.searchParams.get('width') || '640';
    const height = url.searchParams.get('height') || '400';
    const frames = await Promise.all(['green', 'blue'].map((background) => sharp({ create: { width: Number(width), height: Number(height), channels: 3, background } }).jpeg().toBuffer()));
    if (response.destroyed) return;
    response.writeHead(200, { 'Content-Type': url.searchParams.get('transport') === 'fetch' ? 'application/octet-stream' : 'multipart/x-mixed-replace; boundary=veyraframe', 'Access-Control-Allow-Origin': '*' });
    activeStreams++;
    let count = 0;
    const timer = setInterval(() => {
      const frame = frames[count++ % 2];
      response.write(`--veyraframe\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.length}\r\n\r\n`);
      response.write(frame); response.write('\r\n');
      if (url.searchParams.has('closeAfter') && count >= Number(url.searchParams.get('closeAfter'))) response.end();
    }, 125);
    response.on('close', () => { clearInterval(timer); activeStreams--; });
  });
  await new Promise<void>((resolve) => frameServer.listen(0, '127.0.0.1', resolve));
  frameOrigin = `http://127.0.0.1:${(frameServer.address() as AddressInfo).port}`;
});
test.afterAll(async () => {
  frameServer.closeAllConnections();
  await new Promise<void>((resolve) => frameServer.close(() => resolve()));
});

async function watchDesktop(page: Page, width = 640, height = 400) {
  await page.route('**/veyra-screen.mjpeg?*', (route) => route.fulfill({
    status: 307, headers: { Location: `${frameOrigin}/?width=${width}&height=${height}&transport=fetch` },
  }));
  await page.route('**/veyra-screen.json', (route) => route.fulfill({ json: {
    schema_version: 1, available: true, tool: 'test', stream_path: '/veyra-screen.mjpeg',
    settings: { fps: 4, quality: 70, scale: 0.75 }, viewers: 1, frames: 1,
    last_frame_at: new Date().toISOString(), error: null,
  } }));
  await page.goto('/#desktop');
  await page.getByRole('button', { name: 'Start watching' }).click();
  await expect(page.locator('.stream-status')).toHaveText('Live view');
}

async function expectMaximized(page: Page) {
  const viewer = page.getByRole('region', { name: 'Desktop viewer' });
  await expect(viewer).toHaveClass(/expanded/);
  await expect(page.getByRole('button', { name: 'Exit desktop fullscreen' })).toBeVisible();
  const bounds = await viewer.boundingBox();
  expect(bounds).toEqual({ x: 0, y: 0, width: 1280, height: 800 });
  const frame = await page.locator('.screen-stage').boundingBox();
  expect(frame!.height).toBeGreaterThan(700);
  expect(frame!.y + frame!.height).toBeLessThanOrEqual(800);
}

test('native fullscreen includes an exit button and returns to the inline viewer', async ({ page }) => {
  await watchDesktop(page);
  await page.getByRole('button', { name: 'View desktop fullscreen' }).click();
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(true);
  await expectMaximized(page);
  await page.getByRole('button', { name: 'Exit desktop fullscreen' }).click();
  await expect.poll(() => page.evaluate(() => !!document.fullscreenElement)).toBe(false);
  await expect(page.getByRole('region', { name: 'Desktop viewer' })).not.toHaveClass(/expanded/);
});

for (const mode of ['missing', 'denied'] as const) {
  test(`fullscreen ${mode}: maximize falls back and Escape restores the page`, async ({ page }) => {
    await page.addInitScript((mode) => {
      Object.defineProperty(Element.prototype, 'requestFullscreen', { configurable: true,
        value: mode === 'missing' ? undefined : () => Promise.reject(new TypeError('Fullscreen denied')) });
    }, mode);
    await watchDesktop(page);
    await page.getByRole('button', { name: 'View desktop fullscreen' }).click();
    await expectMaximized(page);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: 'View desktop fullscreen' })).toBeFocused();
    await expect(page.getByRole('region', { name: 'Desktop viewer' })).not.toHaveClass(/expanded/);
    expect(await page.evaluate(() => document.body.style.overflow)).not.toBe('hidden');
    // Unmounting an expanded viewer must also restore scrolling.
    await page.getByRole('button', { name: 'View desktop fullscreen' }).click();
    await page.evaluate(() => { window.location.hash = 'conversation'; });
    await expect(page.getByRole('region', { name: 'Desktop viewer' })).toHaveCount(0);
    expect(await page.evaluate(() => document.body.style.overflow)).not.toBe('hidden');
  });
}

test('maximize is usable while a stream is connecting, not gated on MJPEG load events', async ({ page }) => {
  await page.route('**/veyra-screen.mjpeg?*', () => {});
  await page.route('**/veyra-screen.json', (route) => route.fulfill({ json: {
    schema_version: 1, available: true, tool: 'test', stream_path: '/veyra-screen.mjpeg',
    settings: { fps: 4, quality: 70, scale: 0.75 }, viewers: 1, frames: 0,
    last_frame_at: null, error: null,
  } }));
  await page.goto('/#desktop');
  await page.getByRole('button', { name: 'Start watching' }).click();
  await expect(page.locator('.stream-status')).toHaveText('Connecting');
  await page.getByRole('button', { name: 'View desktop fullscreen' }).click();
  await expectMaximized(page);
});

for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }, { width: 844, height: 390 }]) {
  for (const [width, height] of [[3840, 2160], [1080, 1920], [5120, 1440]]) {
    test(`inline viewer fits ${width}x${height} at ${viewport.width}x${viewport.height}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await watchDesktop(page, width, height);
      const stage = page.locator('.screen-stage');
      await stage.scrollIntoViewIfNeeded();
      const bounds = (await stage.boundingBox())!;
      const image = (await page.getByAltText('Live local desktop').boundingBox())!;
      // A definite, viewport-bounded box keeps the percentage-height image from
      // reverting to its intrinsic size and getting clipped by the panel.
      expect(bounds.height).toBeLessThanOrEqual(viewport.height / 2 + 1);
      expect(bounds.y).toBeGreaterThanOrEqual(0);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height + 1);
      expect(image).toEqual(bounds);
      await expect(page.getByAltText('Live local desktop')).toHaveCSS('object-fit', 'contain');
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
    });
  }
}

for (const mode of ['native', 'fallback'] as const) {
  test(`frames keep changing before, during and after ${mode} maximize`, async ({ page }) => {
    if (mode === 'fallback') await page.addInitScript(() => {
      Object.defineProperty(Element.prototype, 'requestFullscreen', { configurable: true, value: undefined });
    });
    let connections = 0;
    page.on('request', (request) => { if (request.url().includes('/veyra-screen.mjpeg')) connections++; });
    await watchDesktop(page);
    const frame = page.getByAltText('Live local desktop');
    const expectMotion = async () => {
      // Verify displayed pixels actually change, not merely server frame counts.
      const pixel = () => frame.evaluate((img: HTMLImageElement) => {
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
        const context = canvas.getContext('2d')!;
        context.drawImage(img, 0, 0, 1, 1);
        return Array.from(context.getImageData(0, 0, 1, 1).data).join(',');
      });
      const before = await pixel();
      await expect.poll(pixel, { intervals: [50] }).not.toBe(before);
      await expect(page.locator('.stream-status')).toHaveText('Live view');
    };
    await expectMotion();
    await page.getByRole('button', { name: 'View desktop fullscreen' }).click();
    await expectMaximized(page);
    await expectMotion();
    await page.getByRole('button', { name: 'Exit desktop fullscreen' }).click();
    await expectMotion();
    expect(connections).toBe(1);
  });
}

test('leaving an expanded viewer cancels capture', async ({ page }) => {
  await expect.poll(() => activeStreams).toBe(0);
  await watchDesktop(page);
  await page.getByRole('button', { name: 'View desktop fullscreen' }).click();
  await expect.poll(() => activeStreams).toBe(1);
  await page.evaluate(() => { window.location.hash = 'conversation'; });
  await expect(page.getByRole('region', { name: 'Desktop viewer' })).toHaveCount(0);
  await expect.poll(() => activeStreams).toBe(0);
});

test('a closed frame stream is reported even when global status says live; reconnect resumes motion', async ({ page }) => {
  await watchDesktop(page);
  // End the next connection after a few real frames, while status remains healthy.
  await page.route('**/veyra-screen.mjpeg?*', (route) => route.fulfill({
    status: 307, headers: { Location: `${frameOrigin}/?closeAfter=3&transport=fetch` },
  }));
  await page.getByRole('button', { name: 'Stop watching' }).click();
  await page.getByRole('button', { name: 'Start watching' }).click();
  await expect(page.locator('.stream-status')).toHaveText('Stream stopped');
  await expect(page.getByText('The desktop stream closed. Reconnect to try again.')).toBeVisible();
  await page.unroute('**/veyra-screen.mjpeg?*');
  await page.route('**/veyra-screen.mjpeg?*', (route) => route.fulfill({
    status: 307, headers: { Location: `${frameOrigin}/?transport=fetch` },
  }));
  await page.getByRole('button', { name: 'Reconnect', exact: true }).click();
  await expect(page.locator('.stream-status')).toHaveText('Live view');
});

test('dashboard requests binary framing for Safari-compatible fetch', async ({ page }) => {
  let transport: string | null = null;
  page.on('request', (request) => {
    if (request.url().includes('/veyra-screen.mjpeg')) transport = new URL(request.url()).searchParams.get('transport');
  });
  await watchDesktop(page);
  expect(transport).toBe('fetch');
});
