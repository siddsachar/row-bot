# Computer Use: architecture and security decision

Status: accepted for Row-Bot 4.5.0; rebuilt for 5.3 (N12-B)
Implementation: `src/row_bot/computer_use/` and
`src/row_bot/tools/computer_use_tool.py`

## Decision

Row-Bot exposes an opt-in, provider-neutral `computer` tool (and
`computer_record` for screen videos) for apps on this computer. Row-Bot's own
browser remains the preferred engine for websites. Computer use runs only in
an interactive local chat, one session at a time, in view: Row-Bot brings the
chosen window to the front and acts on it with real mouse and keyboard input
through an allowlisted subset of the unchanged upstream Cua Driver MCP surface,
while a Stop bar shows on screen.

The 5.3 rebuild removed Row-Bot's verdict layer above Cua (take over, verify
fresh state, escalate, routes, leases, opaque per-capture tokens, background-
first delivery and the separate Vision step). Safety now comes from
visibility (the bar, the live picture in the chat), interruption (Esc, Stop,
and the person's own input pausing Row-Bot), and approvals at the moments
that matter. The Cua Driver integration, its consent and its telemetry are
unchanged.

The integration does not add Row-Bot telemetry, a Cua fork, unattended host
automation, provider-native computer protocols, a generic multimodal MCP
layer, desktop replay, or a VM backend.

## Reviewed upstream dependency

- Project: Cua Driver Rust, MIT license
- Version/tag: `0.20.0` / `cua-driver-rs-v0.20.0`
- Signed tag commit: `bb8c86049cad1bf0853c6d25c03c14875d0d047f`
- Release: <https://github.com/trycua/cua/releases/tag/cua-driver-rs-v0.20.0>
- Windows x86_64 full archive: `cua-driver-rs-0.20.0-windows-x86_64.zip`
  (`bd27528e0d81bf78c03cdd77be28a3ea31899a370eaf06938ad21edac73290bd`)
- Windows ARM64 full archive: `cua-driver-rs-0.20.0-windows-arm64.zip`
  (`a01686a90725d9c902d558c053a0dd95bd181faff0418d9acb495da63f04a6a1`)
- macOS universal full app archive: `cua-driver-rs-0.20.0-darwin-universal.tar.gz`
  (`d5e61fecebd9a620e50c2b8b608c8e7e8141f74c6faebc2ae9ef5d0d96cce7b8`)

Row-Bot downloads only the exact selected asset after a separate explicit
Install action, verifies SHA-256 before safe extraction, and keeps the runtime
private. It never runs the upstream installer or updater. Cua update checks are
disabled with `CUA_DRIVER_RS_UPDATE_CHECK=0`; Cua telemetry is deliberately
left at its disclosed upstream default.

Ordinary Row-Bot sessions and diagnostics use the same private launch profile:
Windows starts the executable with exactly `mcp`, while macOS starts it with
exactly `mcp --direct`. Row-Bot sets neither `CUA_DRIVER_EMBEDDED` nor
`CUA_DRIVER_PARENT_LIVENESS_STDIN`. On macOS the packaged Row-Bot host owns the
Accessibility and Screen Recording permission relationship; Row-Bot does not
install a daemon, LaunchAgent, global socket, `/Applications` helper copy, or
embedded SDK host.

## Telemetry acceptance

Before any Cua executable invocation, Row-Bot shows a mandatory Continue or
Cancel disclosure. Notice version 2 invalidates the older 0.7.1 acknowledgement,
so an upgrade requires explicit consent again.

The reviewed 0.20.0 tagged source sends content-free product events to
`https://eu.i.posthog.com/capture/`. They can include pseudonymous random
installation and process-session identifiers; product/platform/architecture/
transport versions; bounded client, provider, model, and agent categories;
tool and operation categories; success, bounded refusal/error class, duration
and output-size buckets; output type; aggregate session counts and bounded
window/desktop modality, capture-scope/browser/cursor/recording/config usage
flags; permission-gate state; and install/update lifecycle events. The 0.20.0
delta adds only those aggregate modality flags; it does not add a new
content-bearing telemetry category, so the unreleased notice remains version 2.

The event builders do not receive prompts, tool arguments or results, typed
text, screenshots, accessibility trees, application/window names, URLs,
filenames/paths, raw cursor/config values, or raw errors. Row-Bot does not
disable or rebrand this upstream telemetry. It keeps Cua update checks off and
does not expose Cua browser, desktop-wide, updater, autostart, clipboard, or
arbitrary execution tools, so those features cannot be invoked through this
integration. Screen recording (5.3, F2) is the one recording use: a
service-only path, after the person's approval, that records video into a
private folder (see below); the tagged telemetry still receives only its
bounded recording-usage flag, never the folder path or the video.

## Driver allowlist

The private client permits only:

| Cua tool | Row-Bot use |
|---|---|
| `list_apps`, `list_windows` | the windows a person works in, and the names people know their apps by |
| `get_window_state` | one window's picture and controls |
| `launch_app` | open an app by its name (never a path, an address or arguments), after asking |
| `bring_to_front` | bring the chosen window forward before real input |
| `click`, `double_click`, `right_click`, `drag`, `scroll` | real pointer input on that window, at a point in its picture, with `delivery_mode: foreground` |
| `type_text`, `press_key`, `hotkey` | real keyboard input on that window, `delivery_mode: foreground` |
| `health_report`, `check_permissions` | readiness after disclosure (internal) |
| `start_session`, `end_session` | the private session lifecycle (internal) |
| `start_recording`, `stop_recording`, `get_recording_state` | screen recording (F2), only through `CuaClient.call_recording` on a client of its own |

Screen recording never goes through the model's action schema or the generic
call paths (they keep refusing the recording family). `start_recording` always
gets exactly one absolute output folder under Row-Bot's data and
`record_video: true`; nothing else is accepted. The recording runs on its own
Cua session, so stopping a computer use session never ends it by
accident and computer use actions aren't written into its folder. Once the
video is saved to the conversation (25 MB at most), the whole folder,
including Cua's per-step pictures and action records, is removed; a folder a
crash left behind is removed on the next start. On Windows, Cua's video needs
ffmpeg on PATH: Row-Bot passes Cua's recording session (only) a PATH that starts
with the folder holding the ffmpeg it downloaded after asking (F3: a pinned,
fingerprint-checked PyPI wheel; only the program is kept). Row-Bot never calls
`install_ffmpeg`.

All other recording, desktop capture, browser-page/CDP, arbitrary config,
update, installer, autostart, telemetry mutation, skill, FFmpeg, kill-process,
and maintenance surfaces are forbidden and never model-visible.

## Security invariants

- Computer use is off by default and runs only on Windows and macOS. A chat,
  a workflow (scheduled or run by hand), a goal or a helper agent may use it
  (5.3.1 B4), each under its approval level; a channel chat (Telegram, Slack
  and the like) never can, since the person is usually away from the screen,
  and nothing without a conversation can. Its picture
  and Stop in the conversation are on this computer's desktop app only; the
  chat's Stop works from any signed-in device.
- One session at a time: it belongs to one chat's turn and ends with it (a turn
  waiting on the person keeps it). Another chat is told the computer is busy.
- Input is real and foreground: the window comes to the front, then Cua's
  foreground delivery acts on it. Cua confirms the target is in front before it
  sends keyboard input; when Windows keeps another window in front (a locked
  screen), no input is sent and the result says so.
- The Stop bar runs in its own helper process, always on top, never activated.
  Esc or its Stop end the turn at once (as the chat's Stop does). The person's
  own mouse and keyboard between actions (Windows tells injected input apart
  exactly; macOS by timing, unverified on a Mac) make the next action ask
  "Carry on?" on a question card; the action it was about to do is never
  replayed, and the model gets a fresh picture.
- Asking first (D19), through the shared approval levels (Ask: the person;
  Auto: the reviewer may approve what clearly matches the request, otherwise
  the person; Hands-off: runs; Block: refused; a workflow's "Always ask me":
  the person): the first use of each app in a chat, and paying, buying, booking
  with a charge, sending, posting, deleting files and system settings. An
  action is judged by the control under the pointer (its accessible name and
  role, as the browser judges a page), the app, and what a key does in that
  kind of app. The card shows the window with a ring where Row-Bot will click.
- Row-Bot never types into a password, one-time code or card field (by the
  field's role and name; also when its label was clicked), and covers those
  fields in the model's picture. Terminals, password managers, Row-Bot itself,
  protected system screens, and the keys that open other apps or the secure
  screen (the Windows key, Spotlight, Ctrl+Alt+Delete) are refused.
- Pictures are ephemeral: in memory only, never written to media, checkpoints,
  logs or command receipts. Typed values are excluded from logs, histories,
  approval payloads, memory, and durable state.
- UI text and accessibility content are untrusted tool output (fenced as
  external content) and cannot grant scope, recipients, secrets, or authority.
- Residual risks: a click where the accessibility tree has no control (canvas,
  custom-drawn surfaces) can't be classified, so a payment drawn as pixels
  wouldn't ask; a password field the app doesn't name or mark as secure isn't
  recognised; real input can be seen by whatever window is truly in front if
  Windows' foreground state changes between Row-Bot's check and its input.

## Upstream contract binding

Row-Bot uses the tagged 0.20.0 lifecycle session while every capture remains
bound to the exact PID/window target. It never calls desktop capture or
escalates to desktop scope. The runtime
installer uses only the exact full archives above, performs traversal-safe
staged extraction, and retains a prior known-good managed runtime until the new
candidate passes diagnostics.
