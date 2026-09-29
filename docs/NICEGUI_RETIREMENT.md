# NiceGUI retirement inventory

The default native app opens the React client at `/app-v2/`, and since Phase 7
the torn-off desktop Buddy is the React overlay at `/app-v2/buddy-overlay`.
NiceGUI is still loaded, though, and still provides parts of the process. This
is the checklist for removing it completely. Locations are as of the Phase 7
change; line numbers drift, so search for the names.

## Capability parity

**The rule.** React keeps every job NiceGUI does, but it does not clone
NiceGUI's screens. Each NiceGUI-only or partial capability below is **kept**
(the same job, done the React way), **simplified** (the same job with fewer,
simpler controls) or **dropped** (covered elsewhere or not worth a control),
always with a reason. NiceGUI is hidden (nothing in the app routes to it; only
`--legacy-ui` opens it) once every keep and simplify row has shipped, which is
planned for Phase 16 of the polish program; since Phase 15 no keep or simplify
row is open. Its code is removed in the next release.

53 rows: 19 keep, 28 simplify, 6 drop. Capabilities already at full parity
(attachments by picker, slash palette, approvals, pickers, workflows, settings
pages, knowledge, documents, designer and developer basics, and so on) are not
listed.

Status: **Open** (not yet shipped), **Shipped** (with the phase that shipped
it), **Closed** (covered by something that already exists; nothing to build).

| # | Area | Capability in NiceGUI | Verdict | What React gets | Phase | Status |
|---|---|---|---|---|---|---|
| 1 | Chat | Paste an image; drag and drop files into the chat | Keep | Paste and drag-and-drop into the composer, several files at once, limits stated | 11 | Shipped (Phase 11) |
| 2 | Chat | Export a conversation as plain text and PDF (Markdown exists) | Simplify | Export as Markdown and PDF; plain text dropped because Markdown already reads as text | 13 | Shipped (Phase 13) |
| 3 | Chat | Slash commands with arguments (`/goal`, `/profile`, `/agent`, `/reasoning`, `/noskill`) | Simplify | Every command the slash palette lists works with its argument; the niche `/noskill` leaves the palette | 11 | Shipped (Phase 11) |
| 4 | Chat | Goals continue on their own between turns | Keep | Goals continue after each turn up to their limit | 11 | Shipped (Phase 11) |
| 5 | Chat | Goal detail: verifier reason, event log, the goal's approvals, linked agents | Simplify | A goal card: "Turn 3 of 10", the verifier's latest reason, Pause, Stop (approvals stay in the transcript, agents in Agents) | 11 | Shipped (Phase 11) |
| 6 | Chat | Model route line: local or cloud, Chat only, unavailable with a reason | Simplify | Folded into the model pill: a local/cloud glyph, "Chat only" when tools are off, and the unavailable state with Reconnect / Choose another model | 10 | Shipped (Phase 10) |
| 7 | Chat | Per-turn notice "Provider default reasoning is active" | Drop | — | 9 | Closed: when a provider refuses a thinking level the saved choice resets and the thinking picker shows "Provider default" |
| 8 | Chat | Preflight notice "context window could not be determined" | Drop | — | 9 | Closed: the context meter shows "Context unavailable" when the capacity is unknown; real overflow errors are turn errors with New chat / Switch model |
| 9 | Agents | Child agents: Peek, Stop, Message, Resume, Replacement, Copy summary, Ask parent | Simplify | Open the child thread (exists), Stop, Message | 11 | Shipped (Phase 11) |
| 10 | Shell | Background notices (API errors, account health, Hatch, document jobs, memory policy, workflows) | Keep | Notices over the event stream through the app's notice primitive, coalesced; warnings and errors always, information only for jobs the person started | 9 | Shipped (Phase 9) |
| 11 | Shell | Start-up warnings shown once (plugin load failures, tunnel start failures, token warnings) | Keep | Merged with 10: shown once, and listed in Monitor | 9 | Shipped (Phase 9) |
| 12 | Shell | Update-available pill that opens the update dialog | Simplify | Merged with 13 into one sidebar-footer indicator that appears only when something needs attention (a problem, an update) | 15 | Shipped (Phase 15): "Update to X available" opens Updates; "Remind me later" leaves it out for a day on that device |
| 13 | Shell | Always-visible service health with click-through | Simplify | The same indicator as 12; quiet when everything is healthy | 15 | Shipped (Phase 15): "N things need attention" opens Monitor, which lists them first with the place to fix each |
| 14 | Native | Right-click Cut, Copy, Paste, Select All in the desktop window | Keep | Cut, Copy, Paste and Select All in the desktop window | 9 | Shipped (Phase 9) |
| 15 | Setup | Inline first-run model setup (local, API key, custom endpoint, validation, knowledge model, migration, priorities) | Simplify | First run: choose how Row-Bot thinks, pick a model, a quick test, then Home; vision follows the chat model; import offered only when detected; the rest stays in Setup Center | 10 | Shipped (Phase 10) |
| 16 | Setup | ChatGPT/Codex device code and xAI sign-in inside setup | Keep | Device code (Copy, automatic polling) and xAI sign-in inside Setup | 10 | Shipped (Phase 10) |
| 17 | Voice | Realtime voice diagnostics (latency, turn timing) | Drop | — | 10 | Closed (Phase 10): developer diagnostics, not a person's job; voice failures reach people through the error catalog with one fix |
| 18 | Workflows | Duplicate a workflow | Keep | Duplicate workflow | 13 | Shipped (Phase 13) |
| 19 | Workflows | Insert-variable menu (`{{date}}`, step outputs) | Simplify | Typing `{{` in a prompt suggests variables and step outputs | 13 | Shipped (Phase 13) |
| 20 | Workflows | Webhook URL shown in the workflow after saving | Keep | The webhook URL with Copy in the workflow's trigger | 13 | Shipped (Phase 13) |
| 21 | Workflows | External channels multi-select | Keep | A checklist of the configured channels | 13 | Shipped (Phase 13) |
| 22 | Designer | Duplicate a design | Keep | Duplicate in the Design panel's ⋯ menu (and ⌘K): a "(copy)" bound beside the original, opened in its own panel | 12 | Shipped (Phase 12) |
| 23 | Designer | Zero-state quick actions ("Draft 3 slides from a brief", …) | Drop | — | 12 | Closed by review: conversation-first creation and the welcome prompts replace them |
| 24 | Designer | A separate design palette (Ctrl/Cmd+K) and Review shortcut | Drop | — | 12 | Closed by review: the global ⌘K lists the open design's commands (Present, Export, Share, Add a slide, Duplicate, Review; shipped in Phase 12) |
| 25 | Designer | Review: apply all safe fixes, dismiss, re-scan | Simplify | Review checks by itself and again after every change; per-issue Fix (safe) or Ask Row-Bot, and "Fix all safe issues" | 12 | Shipped (Phase 12) |
| 26 | Designer | Share: Copy link, Open folder, QR | Simplify | Publish asks once, then the link with Copy link, Open, a QR code for remote links, and Unpublish | 12 | Shipped (Phase 12) |
| 27 | Designer | Export presets, "Exports save to…", Copy path, Open folder | Simplify | Pick a format (PDF, PNG, PowerPoint, HTML); on this computer it saves to the workspace's Exports folder with "Saved · Open · Show in folder"; other devices download | 12 | Shipped (Phase 12) |
| 28 | Designer | Add and delete pages (and screens) | Keep | The page menu adds a page after the one shown and deletes the one shown (Undo in place) | 12 | Shipped (Phase 12) |
| 29 | Designer | Canvas resize with formats, ratios and auto-fit | Simplify | A Size menu (16:9, 4:3, 1:1, A4, 9:16 · Phone) that re-fits every page; anything custom by asking | 12 | Shipped (Phase 12) |
| 30 | Designer | Brand from a website; logo height and inset | Simplify | Brand "From a website" (one guarded read of a public page, applied with Undo); logo size by asking | 12 | Shipped (Phase 12) |
| 31 | Designer | Project references list | Drop | — | 12 | Closed by review: the conversation's attachments are the design's references |
| 32 | Developer | Remove a code folder from recents | Keep | Remove a saved code folder from the Open saved list (files stay; Undo) | 12 | Shipped (Phase 12) |
| 33 | Developer | Custom tools list, new tool from a repository or folder, smoke test | Simplify | Settings › Tools › Custom tools: list, add from a folder (desktop app), Test, on/off, available in chat, remove; cloning happens in the conversation | 12 | Shipped (Phase 12) |
| 34 | Developer | "Run custom tool command once" approval dialog | Simplify | The standard approval card before a test command that needs approval runs once (Settings and the code panel's builder) | 12 | Shipped (Phase 12) |
| 35 | Developer | Stop all servers | Keep | Stop all processes in the Run tab | 12 | Shipped (Phase 12) |
| 36 | Developer | GitHub CLI install hint in the Developer panel | Simplify | The Connect GitHub card in the pull request section when gh is missing or signed out | 12 | Shipped (Phase 12) |
| 37 | Computer use | Active session card and live-control dock | Simplify | A computer-use card in the conversation: latest picture, Pause (you take over), Resume, Stop | 12 | Shipped (Phase 12) |
| 38 | Terminal | Interactive terminal (raw keys, Ctrl-C, Clear) | Simplify | The line terminal gains Stop (Ctrl+C), Clear and "Open in your terminal" | 12 | Shipped (Phase 12) |
| 39 | Plugins | Setup guide, changelog link, load log, declared sign-in | Simplify | A plugin connect sheet with setup steps, sign-in and the changelog link; load failures arrive as notices | 15 | Shipped (Phase 15): the plugin's README as setup notes, its declared sign-ins, settings, local test and turning it on, the changelog link; worker plugins get Prepare |
| 40 | Skills | Import a pasted SKILL.md | Keep | Skills "Import a skill" accepts pasted SKILL.md | 15 | Shipped (verified in Phase 15): a pasted SKILL.md with its frontmatter imports as a skill |
| 41 | Channels | Per-channel "Expose via tunnel" switch and tunnel URL | Simplify | A channel that needs a public address opens the tunnel itself and shows "Reachable at …" with Copy | 15 | Shipped (Phase 15) |
| 42 | Channels | Per-channel setup guide | Keep | Per-channel steps in the connect sheet | 15 | Shipped (Phase 15), with "Send a test message to me" |
| 43 | Channels | WhatsApp live QR, "Waiting for QR code…", Reset session | Keep | WhatsApp live QR and Reset session | 15 | Shipped (Phase 15): the owner on this computer sees the live code; Reset session asks first |
| 44 | Channels | "Save current" (import a secret from the environment) | Simplify | A field supplied by the environment says so; stored secrets migrate at start | 15 | Shipped (Phase 15): "Supplied by the environment"; older stored keys move to the channel keyring at start |
| 45 | Accounts | Google and X guided steps; GitHub setup guide | Keep | Google, X and GitHub steps in the connect sheet | 15 | Shipped (Phase 15), with X's callback address and Copy |
| 46 | Remote | Pairing QR, Refresh QR, custom pairing QR | Simplify | One QR that renews itself before its invitation expires, with Copy link; custom addresses in Advanced | 14 | Shipped (Phase 14) |
| 47 | Remote | Same-network setup instructions | Keep | Two short lines in the "Same Wi-Fi" option; "Allow on my network…" when Row-Bot listens on this computer only | 14 | Shipped (Phase 14) |
| 48 | Remote | Recent access activity log | Simplify | Folded into Your devices (last seen, where from in words) | 14 | Shipped (Phase 14) |
| 49 | Remote | Access sessions renew every 12 hours | Keep | Automatic session renewal in the React client | 14 | Shipped (Phase 14) |
| 50 | Remote | Tailscale consent link, terms link, review checkbox, private address | Simplify | One confirmation, Tailscale's own consent page when it asks, the address with Copy | 14 | Shipped (Phase 14) |
| 51 | Remote | Active tunnels list with Copy | Simplify | One "Public" status line with the address, Copy and Stop | 14 | Shipped (Phase 14) |
| 52 | Remote | "Expose task webhook" switch | Simplify | Moves to the workflow's webhook trigger: "Make reachable from the internet" | 13 | Shipped (Phase 13) |
| 53 | Data | Migration: Browse buttons, default source paths, Select all / Clear all | Simplify | The source is detected (Browse only if not found); Select all / Clear all kept | 13 | Shipped (Phase 13) |

### Dropped, and why

- **7. Per-turn reasoning notice.** The thinking picker already shows
  "Provider default" once a provider refuses a level.
- **8. Context preflight notice.** The context meter already says when the
  capacity is unknown, and real overflows are turn errors with a next step.
- **17. Realtime voice diagnostics.** Developer diagnostics, not a person's
  job; voice failures go through the error catalog, which gives each one a
  sentence and one fix. Closed in Phase 10.
- **23. Designer zero-state quick actions.** Asking in the conversation and
  the welcome prompts do the same.
- **24. A separate design palette.** One palette: ⌘K lists the design commands
  when a design is open.
- **31. Design references list.** The conversation's attachments are the
  design's references.

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
  the NiceGUI page. Since Phase 9 it keeps only the latest 32 and React gets
  the same notices from `application/app_notices`, so it goes with the
  NiceGUI page.
- [ ] **The legacy UI at `/`.** The whole NiceGUI UI (`@ui.page("/")` in
  `app.py`, `ui/*`, the NiceGUI modules under `designer/`, `developer/ui.py`,
  `skills_hub/ui.py`, `plugins/ui_*.py`, WhatsApp's `build_custom_ui`, the
  voice NiceGUI glue). The launcher opens it only with `--legacy-ui`, but the
  React client links to it as "Current application" from the navigation
  (`Navigation.tsx`) and the `index.html` noscript link. Connection problems
  no longer link to it (Phase 9: they offer Reconnect or Reload). Remove each
  remaining link when its surface is ported. Remote requests to `/` already redirect to `/app-v2/`;
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
