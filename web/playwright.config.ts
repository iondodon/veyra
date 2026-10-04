import { defineConfig } from '@playwright/test';

const externalUrl = process.env.VEYRA_TEST_URL;
export default defineConfig({
  testDir: './tests',
  use: { baseURL: externalUrl || 'http://localhost:3000', viewport: { width: 1280, height: 800 } },
  webServer: externalUrl ? undefined : {
    command: 'npm run dev',
    url: 'http://localhost:3000',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
