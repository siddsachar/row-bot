import { resolve } from 'node:path';
import { defineConfig, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';
import { bootstrapTheme, TOKENS } from './src/ui/theme-model';

const backend = new URL(
  process.env.ROW_BOT_DEV_BACKEND ?? 'http://127.0.0.1:8080',
);
if (
  backend.protocol !== 'http:' ||
  !['127.0.0.1', '[::1]', 'localhost'].includes(backend.hostname) ||
  backend.username ||
  backend.password ||
  backend.pathname !== '/' ||
  backend.search ||
  backend.hash
) {
  throw new Error('ROW_BOT_DEV_BACKEND must be a plain HTTP loopback origin');
}

// Shiki's core, regex engine and their HTML helpers (lazy `syntax` chunk).
const SYNTAX_MODULES =
  /\/node_modules\/(?:shiki|@shikijs|oniguruma-to-es|oniguruma-parser|regex|regex-recursion|regex-utilities|hast-util-to-html|hast-util-whitespace|html-void-elements|mdast-util-to-hast|micromark-util-[\w-]+|unist-util-[\w-]+|vfile|vfile-message|property-information|space-separated-tokens|comma-separated-tokens|ccount|character-entities-html4|character-entities-legacy|stringify-entities|zwitch|trim-lines|devlop|@ungap\/structured-clone)\//;

// The knowledge graph renderer (sigma.js, graphology, the ForceAtlas2 layout
// and sigma's EventEmitter polyfill): a lazy `graph` chunk loaded by Home ›
// Knowledge only.
const GRAPH_MODULES =
  /\/node_modules\/(?:sigma|graphology|graphology-[\w-]+|events)\//;

// Dev-only same-loopback proxy. Production access policy is untouched.
const loopbackProxy: ProxyOptions = {
  target: backend.origin,
  changeOrigin: true,
  configure(proxy) {
    proxy.on('proxyReq', (request) => {
      request.setHeader('Origin', backend.origin);
    });
  },
};

export default defineConfig({
  base: '/app-v2/',
  plugins: [
    react(),
    {
      // Dev only: serve the desktop Buddy document at the route the native
      // host opens (/app-v2/buddy-overlay), as the backend does when built.
      name: 'row-bot-buddy-overlay-route',
      configureServer(server) {
        server.middlewares.use((request, _response, next) => {
          const url = request.url ?? '';
          if (/^\/app-v2\/buddy-overlay\/?(?:\?|$)/.test(url))
            request.url = url.replace(
              /^\/app-v2\/buddy-overlay\/?/,
              '/app-v2/buddy-overlay.html',
            );
          next();
        });
      },
    },
    {
      name: 'row-bot-theme-bootstrap',
      transformIndexHtml(html) {
        return html.replace(
          '<!-- row-bot-theme-bootstrap -->',
          `<script>(${bootstrapTheme.toString()})(${JSON.stringify(TOKENS)})</script>`,
        );
      },
    },
  ],
  server: {
    host: '127.0.0.1',
    strictPort: true,
    port: 5173,
    proxy: {
      '/api/v1': loopbackProxy,
      // Remote access (connection links, Tailscale, sessions) lives beside
      // /api/v1; without this the Access page reads 404s in dev.
      '/api/access': loopbackProxy,
      // Packaged renderer runtimes (vis-network, Mermaid, Plotly) are served
      // by the backend, not Vite; without this they are "unavailable" in dev.
      '/app-v2/runtime/': loopbackProxy,
    },
  },
  build: {
    manifest: true,
    sourcemap: false,
    target: 'es2022',
    emptyOutDir: false,
    rollupOptions: {
      // The workspace shell and the desktop Buddy (a 380×230 native window
      // that must not load the shell) are separate documents.
      input: {
        index: resolve(import.meta.dirname, 'index.html'),
        'buddy-overlay': resolve(import.meta.dirname, 'buddy-overlay.html'),
      },
      output: {
        manualChunks(id) {
          // Syntax highlighting loads on first use: each grammar is its own
          // chunk and the highlighter core stays out of the startup vendor.
          if (
            /\/node_modules\/(?:@shikijs\/langs|shiki\/dist\/langs)\//.test(id)
          )
            return undefined;
          if (SYNTAX_MODULES.test(id)) return 'syntax';
          if (GRAPH_MODULES.test(id)) return 'graph';
          if (id.includes('/node_modules/')) return 'vendor';
          if (id.includes('/contracts/client-platform/')) return 'protocol';
        },
      },
    },
  },
});
