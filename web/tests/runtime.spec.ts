import { expect, test } from '@playwright/test';

test('React modules are fetched fresh across dashboard reloads without hook errors', async ({ page }) => {
  const errors: string[] = [];
  const reactChunks = new Set<string>();
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('response', (response) => {
    if (/\/node_modules\/\.vite-veyra\/deps\/react-(?!dom|server-dom)[^/]+\.js\?/.test(response.url())) {
      reactChunks.add(response.url());
    }
  });

  await page.route('**/veyra-screen.mp4?*', (route) => route.fulfill({ status: 503, body: 'Synthetic unavailable video' }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Veyra', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Maximize video' })).toBeVisible();
  // Vite's dependency URLs can survive a restart even when their transitive
  // module graph changes. Never retain an immutable renderer from an old graph.
  await expect(page.getByRole('button', { name: 'Reconnect' })).toBeVisible();
  const dependencies = await page.evaluate(() => performance.getEntriesByType('resource')
    .map((entry) => entry.name)
    .filter((url) => /\/node_modules\/\.vite-veyra\/deps\/react(?:-dom_client)?\.js\?/.test(url)));
  expect(dependencies.length).toBeGreaterThanOrEqual(2);
  for (const url of dependencies) {
    const response = await page.request.get(url);
    expect(response.headers()['cache-control']).toContain('no-store');
  }

  for (let i = 0; i < 3; i++) {
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Veyra', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Maximize video' })).toBeVisible();
  }
  expect(errors).toEqual([]);
  // The renderer and hooks must share one React singleton, including its URL.
  expect(reactChunks.size).toBe(1);
});
