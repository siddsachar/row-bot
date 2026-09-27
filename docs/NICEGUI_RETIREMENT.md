# NiceGUI retirement inventory

The default native app opens the React client at `/app-v2/`, and since Phase 7
the torn-off desktop Buddy is the React overlay at `/app-v2/buddy-overlay`.
NiceGUI is still loaded, though, and still provides parts of the process. This
is the checklist for removing it completely. Locations are as of the Phase 7
change; line numbers drift, so search for the names.

## What NiceGUI still provides to the default app

- [ ] **The ASGI application and server.** `app.py` builds on `nicegui.app`
  (a FastAPI subclass with an orjson default response and NiceGUI's lifespan)
  and starts it with `ui.run`, which also adds GZip and prefix-redirect
  middleware and a `/favicon.ico` route. uvicorn is only a transitive
  dependency today. The headless factory `create_client_platform_app`
  (`api/v1/routes.py`) is the starting point for a plain FastAPI app.
- [ ] **Start-up and shutdown.** `@app.on_startup` runs the whole start-up
  sequence and writes `ui.state` start-up status, ready flag and warnings,
  which `/api/startup-state` and `/readyz` read; `@app.on_shutdown` stops the
  runtime. Both need a plain lifespan owner.
- [ ] **Routes registered through NiceGUI.** `app.add_route` in `app.py`:
  `/api/launcher-ping`, `/api/startup-state`, `/api/launcher-shutdown`,
  `/api/webhook/{task_id}`, `/api/client-error` (NiceGUI page only),
  `/healthz`, `/readyz`; the access and mobile routes; `channels/sms.py`
  (`/sms`) and `plugins/webhooks.py` (plugin webhooks) add their own.
- [ ] **Static mounts.** Keep `/static` (access policy glyph, mobile PWA
  icons, offline page, service worker, favicon) and `/published` (API
  sharing). `/_buddy` (Buddy looks: also used by `mobile/routes.py`),
  `/_media` (NiceGUI video embeds) and `/_fonts/cache` (NiceGUI Designer
  preview) serve NiceGUI surfaces only.
- [ ] **Shared state read by the API.** `ui.state` (`AppState`,
  `_active_generations`) is still read by `thread_cleanup` (API delete),
  `ui.streaming.request_generation_stop`, `client_monitor` → Dream Cycle
  idle checks, `memory_extraction.is_app_idle`, thread checkpoint cleanup,
  the browser service and the `row_bot_status` tool; `ui.constants` by the
  filesystem tool and Designer references. Move these to
  `runtime.executions` (the generation registry) and a non-UI constants
  module. Several imports are `try/except`, so they would silently become
  no-ops without NiceGUI: replace them, do not just delete them.
- [ ] **The voice coordinator.** `ui/state.AppState` builds the voice
  coordinator (TTS, vision) that `client_platform` binds, so React Talk and
  Dictation depend on it. It needs its own owner.
- [ ] **The toast queue.** `notifications._toast_queue` is drained only by
  the NiceGUI page; without it the queue grows without bound.
- [ ] **The legacy UI at `/`.** The whole NiceGUI UI (`@ui.page("/")` in
  `app.py`, `ui/*`, the NiceGUI modules under `designer/`, `developer/ui.py`,
  `skills_hub/ui.py`, `plugins/ui_*.py`, WhatsApp's `build_custom_ui`, the
  voice NiceGUI glue). The launcher opens it only with `--legacy-ui`, but the
  React client links to it as "Current application" for settings and
  surfaces it has not ported (`Navigation.tsx`, `SettingRoute.tsx`,
  `Workspace.tsx`, the `index.html` noscript link). Remove each link when its
  surface is ported. Remote requests to `/` already redirect to `/app-v2/`;
  loopback desktop requests do not.
- [ ] **Launcher legacy branches.** `--legacy-ui` (and the no-op
  `--client-v2` alias), `_client_url_for_port`, the window script's legacy
  main-window `js_api`, and the legacy-only `_JsApi` methods (`open_url`,
  `choose_file`, `choose_folder`, `open_window`, `close_window`). The Buddy
  `_JsApi` methods now only delegate to the React overlay's host.
- [ ] **Packaging and checks.** `pyproject.toml` depends on `nicegui`
  (pulling python-socketio and orjson); uvicorn, markdown2 and lxml are only
  transitive and must be declared directly. `scripts/verify_runtime_dependencies.py`
  lists nicegui, `smoke_app.py` expects `GET /` to return the NiceGUI page,
  and the browser fixture apps (`client_platform`, `client_workspace`,
  `core_surface_parity`) run on NiceGUI. About 37 test files reference
  nicegui and 82 import `row_bot.ui.*`.

## Retired in Phase 7

- The `/buddy-overlay` page, `build_buddy_overlay_page` and its CSS.
- `static/buddy/runtime/buddy.js`. The legacy NiceGUI sidebar Buddy shows the
  active look's still image.
- The overlay-only projections in `buddy/overlay.py` (thread snapshot,
  approval projection, plain-text projection), the NiceGUI-era native
  lifecycle, and the launcher's foreground-app tracker with
  `get_foreground_target` / `restore_foreground_target` (the NiceGUI overlay
  returned focus to the previous app after Send; the React overlay does not).
