# NiceGUI retirement

NiceGUI was retired in favour of the React client and removed completely in
this release. The React client in `frontend/` is the only UI, served at
`/app-v2/`. `GET /` redirects to `/app-v2/` and keeps the query string; a
remote browser without a session goes to `/connect` first. A plain FastAPI app
(`row_bot.server`), run by uvicorn from `row_bot.app`, replaced NiceGUI's
server plumbing: the start-up and shutdown hooks, route registration, static
mounts and GZip. `launcher.py --legacy-ui` and `--client-v2` are deprecated
no-ops: they log a warning and the app opens React.

## Capability parity

React took over every job NiceGUI did, without cloning NiceGUI's screens. Each
capability below, which React lacked or had only in part, was **kept** (the
same job, done the React way), **simplified** (the same job with fewer,
simpler controls) or **dropped** (covered elsewhere or not worth a control),
always with a reason.

53 rows: 19 keep, 28 simplify, 6 drop. Capabilities that were already at full
parity (attachments by picker, slash palette, approvals, pickers, workflows,
settings pages, knowledge, documents, designer and developer basics, and so
on) are not listed.

Status: **Shipped** (with the phase that shipped it) or **Closed** (covered by
something that already exists; nothing to build).

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

## What the removal did

- [x] The app is a plain FastAPI app in `row_bot.server`, run by uvicorn, with no WebSocket support: the client uses HTTP and server-sent events.
- [x] `app.py` owns the start-up and shutdown sequence; `application/startup_state` holds the ready flag, status and warnings for `/readyz` and `/api/startup-state`.
- [x] Routes register on the FastAPI app; the SMS webhook and plugin webhooks use `add_late_route`.
- [x] `/static`, `/_buddy`, `/published` and `/_fonts/cache` stay mounted; `/_media` (NiceGUI video embeds) is gone.
- [x] Idle checks, conversation deletion, browser-tab eviction and the status tool read the one generation registry in `runtime.executions`.
- [x] The filesystem tool's file extensions come from `row_bot.file_context`.
- [x] Backend functions that only the NiceGUI pages called are deleted.
- [x] `app.py` owns the one voice coordinator and binds it to the client platform.
- [x] The toast queue is gone; notices reach React through `application/app_notices`.
- [x] The NiceGUI UI at `/` and every NiceGUI module are deleted; React no longer links to a previous application.
- [x] `--legacy-ui` and `--client-v2` are deprecated no-ops; the legacy window bridge methods are gone.
- [x] `nicegui` is no longer a dependency; uvicorn and markdown2 are declared directly.
- [x] The boundary check forbids `nicegui`, `row_bot.ui` and `plugins.ui_*` in `src`; the plugin loader still refuses plugins that import nicegui.
- [x] The launch smoke check requires `/` to lead to React; the browser fixture app mounts on `row_bot.server.app`.
- [x] The NiceGUI tests and the NiceGUI/React parity harnesses are deleted.
- [x] The Windows installer deletes `{app}\app\src` before copying a new version, so an upgrade leaves no removed module behind.
- [x] The NiceGUI desktop Buddy overlay was retired in Phase 7; the torn-off Buddy is the React overlay at `/app-v2/buddy-overlay`.
