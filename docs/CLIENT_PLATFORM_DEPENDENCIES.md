# Client dependency review

The frontend is one private application with exact direct versions in
`frontend/package.json` and a single npm lockfile. Node 24.15.0 is build/test
time only. Install with `npm ci --ignore-scripts`; `.npmrc` disables lifecycle
scripts, automatic audit, funding and update notifications. Dependencies and
fonts are never fetched by the production browser. Python dependencies remain
authoritative in pyproject.toml, uv.lock and its generated export.

Reviewed on 2026-09-09 using published npm package metadata, upstream docs,
installed distribution source and an explicit npm audit. React/React DOM,
React Router, Radix primitives and react-resizable-panels are MIT; Lucide is
ISC. TypeScript/Playwright are Apache-2.0, axe is MPL-2.0, other direct
development tools are MIT. The lockfile records every transitive version and
integrity hash; the local gate includes exact inventory and audit results.

| Candidate | Decision and owned adaptation |
| --- | --- |
| Radix dialog/menu/popover/tabs/tooltip/toast | Selected individually, avoiding unused components. Upstream supplies ARIA patterns, focus trap/return, Escape, outside interaction and scroll locking. Native HTML selects retain browser behavior. One Radix modal scope supplies dialogs, sheets and alert-dialog semantics; confirmation suspends mounted task content and defaults focus to Cancel. Integrated browser evidence remains required. |
| react-resizable-panels 4.12.4 | Selected for pointer capture, touch, separator semantics and size constraints. Row-Bot owns persisted geometry and the specified 16/48px keyboard increments; conversation children retain identity. No floating window manager. |
| React Router | One basename `/app-v2/`, lazy secondary surfaces and explicit unknown-route recovery. No capability-specific app packages. |
| Lucide | Bundled SVG icons with text/accessible names. No icon/font CDN. |
| shiki 4.4.3 | Reviewed 2026-09-25 for the polish program (pre-approved syntax highlighter). MIT, no install scripts; it brings 43 transitive packages, all MIT or ISC (`@shikijs/*`, the `oniguruma-to-es`/`regex` family, and small unified/hast utilities). Only the fine-grained build is used: `shiki/core` with the JavaScript regex engine (`shiki/engine/javascript`), so no WebAssembly is shipped or compiled, plus 32 grammars imported one by one in `src/features/shell/syntax.ts`. The highlighter and each grammar are dynamic imports: `vite.config.ts` keeps them out of the startup `vendor` chunk (core in a lazy `syntax` chunk, each grammar in its own hashed `assets/` file), so nothing loads until a code block needs it and the browser only ever requests same-origin assets. Tokens come back as data (`codeToTokens`) and React renders them as text spans; no HTML string is injected. Colours use Shiki's CSS-variable theme mapped onto the app's syntax tokens, so light and dark need no re-highlighting. No telemetry or network access. |
| sigma 3.0.3, graphology 0.26.0, graphology-layout-forceatlas2 0.10.1 (graphology-types 0.24.8 for types only) | Reviewed 2026-09-26 for the polish program (pre-approved WebGL graph renderer) and used only by Home › Knowledge, where it replaces the packaged vis-network runtime. All MIT, no install scripts; together they bring two transitive packages, graphology-utils 2.5.2 (MIT) and events 3.3.0 (MIT, the EventEmitter polyfill that sigma and graphology extend). The installed distributions were searched for `fetch`, `XMLHttpRequest`, `sendBeacon`, `WebSocket` and worker creation: the only URLs are source comments, and nothing opens a connection. graphology-layout-forceatlas2 is imported through its synchronous entry only; its optional Web Worker supervisor (which would build a `blob:` worker) is never imported, so the CSP is unchanged. The graph is laid out from positions seeded by each memory's id, so the same data always draws the same picture. `vite.config.ts` keeps the four packages in a lazy `graph` chunk that the Knowledge tab imports on first use; startup and every other surface are unaffected. Rendering is local WebGL on canvases inside the page; if WebGL is unavailable the tab shows the list view. No telemetry or network access. |
| uqr 0.1.3 | Reviewed 2026-09-29 for the polish program (Phase 12: the QR code beside a design's published remote link; Phase 14 reuses it for device pairing). MIT (Project Nayuki's QR Code generator, adapted by Anthony Fu), no dependencies and no install scripts (its package scripts are build/lint/test only); one 27 KB ESM file. The installed distribution was searched for `fetch`, `XMLHttpRequest`, `sendBeacon`, `WebSocket`, `eval`, `Function(`, dynamic `import()` and `require(`: none. Only `encode()` is used, by `src/ui/QrCode.tsx`, which draws the returned module matrix as one SVG `<path>` in React (dark modules on a white quiet zone so phones read it in either theme); `renderSVG` is not used, so no markup string is injected. The link is encoded entirely in the page; nothing is sent anywhere and the CSP is unchanged. No telemetry or network access. |
| @fontsource-variable/geist 5.3.0, @fontsource-variable/geist-mono 5.3.0 | Reviewed 2026-09-25 for the polish program. SIL OFL 1.1 variable fonts (Geist by Vercel) with no dependencies and no install scripts. Only the woff2 files are consumed: `src/ui/styles/fonts.css` declares the Latin and Latin Extended subsets with `font-display: swap`, and Vite copies them into hashed same-origin `assets/` files that the asset manifest inventories (about 84 KB). The package CSS is not imported and nothing is fetched at runtime; the CSP `font-src 'self'` is unchanged. The system stack stays the fallback for other scripts. |

[Radix accessibility](https://www.radix-ui.com/primitives/docs/overview/accessibility),
[dialog behavior](https://www.radix-ui.com/primitives/docs/components/dialog),
[pane API](https://github.com/bvaughn/react-resizable-panels/blob/main/README.md),
and [Vite requirements](https://vite.dev/guide/) inform the review; upstream
claims do not replace Row-Bot browser tests. Exact versions are frozen in the
lockfile rather than inferred from these moving documentation pages.

The first resolution refused TypeScript 7 because typescript-eslint requires
TypeScript below 6.1. The compatible compiler is 6.0.3; no peer checks were
overridden. Vitest 4.1.11 is the selected test runner, with Vite 7.3.6 and
plugin-react 5.2.0. It fixes the
[redirect mock advisory](https://github.com/vitest-dev/vitest/security/advisories/GHSA-82fw-gwwq-j7x9).
The temporary 3.2.7 resolution is superseded, and its audit is historical evidence.

Vitest includes optional OpenTelemetry instrumentation. Its package archive and
`Traces` constructor were reviewed before execution: SDK/API imports are gated
by `enabled`. The user explicitly accepted this dependency on 2026-09-09 with
telemetry disabled. `vitest.config.ts` sets `experimental.openTelemetry.enabled`
to false, provides no SDK path, and disables API, browser and watch modes.
No OpenTelemetry API, SDK or exporter package is installed. No Row-Bot prompts,
files, memories, secrets, screenshots, tool arguments or channel content may be
sent through dependency telemetry. This is third-party test instrumentation,
not Row-Bot telemetry, and it stays disabled. Future upgrades must repeat the
review before execution. Enabling instrumentation is outside this acceptance.

Production dependencies render locally and do not contact an external service.
The one protocol adapter uses authenticated same-origin requests. Vite HMR is
development-only and binds loopback; Playwright browser installation and npm
audits are explicit developer operations, never application startup actions.
axe runs locally in the test browser without an external reporter. No remote
font, analytics, error-reporting or CDN service is enabled.

Build/install scripts stay disabled: published compiled JS and platform-specific
optional binaries are consumed directly. Do not run dependency `prepare`,
`postinstall`, browser download or update commands implicitly. CI provisions its
declared browser engines explicitly. Bundle size and reproducibility are measured
at the phase gate; package size alone is not performance acceptance. Removing a
dependency requires replacing its concrete consumer and rerunning the relevant
keyboard, theme, layout, overlay and browser evidence.
