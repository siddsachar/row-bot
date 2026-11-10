import path from 'node:path';
import { cloudflareTest } from '@cloudflare/vitest-plugin';
import { defineConfig } from 'vitest/config';

// The Worker runs in workerd (Miniflare). Every call it would make to Railway is answered by a mocked
// `fetch` in the tests, so nothing leaves this computer.
export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: path.join(import.meta.dirname, 'wrangler.toml') },
    }),
  ],
});
