import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The Company Server's admin console (src/console): its own documents and
// bundle in dist-console/, served at /console/ from ROW_BOT_ORG_CONSOLE_DIR.
// The app's build (vite.config.ts) never includes it. The server's CSP allows
// only the console's own files, so nothing is inlined (no data: URLs, no
// inline scripts) and nothing comes from public/.
export default defineConfig({
  base: '/console/',
  publicDir: false,
  plugins: [react()],
  build: {
    outDir: 'dist-console',
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2022',
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
    rollupOptions: {
      input: {
        console: resolve(import.meta.dirname, 'console.html'),
        'console-not-admin': resolve(
          import.meta.dirname,
          'console-not-admin.html',
        ),
      },
    },
  },
});
