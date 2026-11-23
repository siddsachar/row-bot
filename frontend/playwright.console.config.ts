import { defineConfig } from '@playwright/test';

// The admin console's browser spec: the built console (npm run
// console:build) against a fake Company Server API, all through route
// interception, so nothing listens and nothing leaves the browser.
export default defineConfig({
  testDir: './src/console/browser',
  testMatch: /\.spec\.ts$/,
  outputDir: '../.tmp/console-browser',
  workers: 1,
  reporter: 'list',
  use: {
    ...(process.env.ROW_BOT_BROWSER_CHANNEL
      ? { channel: process.env.ROW_BOT_BROWSER_CHANNEL }
      : {}),
    viewport: { width: 1440, height: 900 },
  },
});
