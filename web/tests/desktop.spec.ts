import { expect, test, type Page } from '@playwright/test';

async function watchDesktop(page: Page) {
  // Deterministic frame, with no capture of the owner's desktop during tests.
  await page.route('**/veyra-screen.mjpeg?*', (route) => route.fulfill({
    contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400"><rect width="640" height="400" fill="green"/></svg>',
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
