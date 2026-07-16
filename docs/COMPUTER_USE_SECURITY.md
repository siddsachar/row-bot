# Computer Use Beta: architecture and security decision

Status: accepted for implementation on `feat/computer-use`
Canonical implementation plan: `.local/plans/computer-use-end-to-end-implementation-plan.md`

## Decision

Row-Bot will expose an opt-in, provider-neutral `computer_use` tool for native
desktop applications. Browser remains the preferred and separate DOM-based
engine for web tasks. Computer Use is restricted to an interactive local UI,
one exclusive task-scoped session, target-window capture, and an allowlisted
subset of the unchanged upstream Cua Driver MCP surface.

The integration does not add Row-Bot telemetry, a Cua fork, unattended host
automation, provider-native computer protocols, a generic multimodal MCP
layer, personal-browser attachment, desktop replay, or a VM backend.

## Reviewed upstream dependency

- Project: Cua Driver Rust, MIT license
- Version/tag: `0.7.1` / `cua-driver-rs-v0.7.1`
- Release commit: `7caf72b`
- Release: <https://github.com/trycua/cua/releases/tag/cua-driver-rs-v0.7.1>
- Windows x86_64 archive: `cua-driver-rs-0.7.1-windows-x86_64.zip`
  (`00dfa76c5008db20c55ed0cc951388b0f25d1221f6995e5f131dcd6bc4fc5aab`)
- Windows ARM64 archive: `cua-driver-rs-0.7.1-windows-arm64.zip`
  (`43601a32a1ce9eec5fbbe98803718ad2ca3a3450c499b354b05fedc3a1cc5526`)
- macOS universal app archive: `cua-driver-rs-0.7.1-darwin-universal.tar.gz`
  (`3bd574f162bf293089ca9d28653c8ac2b869f1577a15b92ff95203c6279a08a1`)

Row-Bot downloads only the exact selected asset after a separate explicit
Install action, verifies SHA-256 before safe extraction, and keeps the runtime
private. It never runs the upstream installer or updater. Cua update checks are
disabled with `CUA_DRIVER_RS_UPDATE_CHECK=0`; Cua telemetry is deliberately
left at its disclosed upstream default.

## Telemetry acceptance

Before any Cua executable invocation, Row-Bot shows a mandatory Continue or
Cancel disclosure. Reviewed source sends a stable random Cua installation ID,
Cua version, OS name/version, architecture, CI flag, event category, and
timestamp to `https://eu.i.posthog.com/capture/`. It explicitly excludes
usernames, file paths, command arguments, tool arguments, and typed content.
Row-Bot additionally prevents screenshots and its own prompt, memory, secret,
tool-argument, and channel data from reaching Cua telemetry.

## Driver allowlist

The private client permits only:

| Cua tool | Row-Bot use | Policy class |
|---|---|---|
| `list_apps`, `list_windows` | private discovery | observation |
| `get_window_state` | target-window tree and screenshot | observation |
| `launch_app` | allowlisted display-name launch | routine or consequential |
| `bring_to_front` | explicit foreground escalation | always confirm |
| `click`, `double_click`, `right_click` | token-first selection | routine or consequential |
| `type_text`, `press_key`, `hotkey` | non-secret text/input | routine, consequential, or handoff |
| `scroll`, `drag` | bounded target-window input | routine |
| `set_config` | hidden session-only window/image limits | internal only |
| `health_report`, `check_permissions` | readiness after disclosure | internal only |
| `start_session`, `end_session` | private lifecycle | internal only |

All recording, desktop capture, browser-page/CDP, arbitrary config, update,
installer, autostart, telemetry mutation, skill, FFmpeg, kill-process, and
maintenance surfaces are forbidden and never model-visible.

## Security invariants

- Computer Use is off by default and unavailable to schedules, channels,
  background workflows, child agents, and headless/server callers.
- The exclusive lease covers discovery, capture, Vision fallback, and input.
- Stop and Take over cancel queued work before mutation is exposed. Take over
  retains a paused lease; Resume requires a fresh observation.
- Target IDs and element tokens are opaque and generation-bound. Target drift,
  reconnects, approval waits, and takeover invalidate them.
- Credentials, OTP, CAPTCHA, biometric, UAC/TCC, terminals, password managers,
  Row-Bot itself, secure desktops, and elevation are handed off or blocked.
- Every mutation is followed by a new target-window observation.
- Screenshot bytes are ephemeral. Typed values are excluded from logs,
  histories, checkpoints, approval payloads, memory, and durable media.
- Consequential actions require point-of-risk confirmation even in Auto mode.
- UI text and accessibility content are untrusted tool output and cannot grant
  new scope, recipients, secrets, or authority.

## Upstream contract deviation

No supported Cua 0.7.1 environment variable for a private config/home path is
documented. Row-Bot therefore does not invent one. It relies on the documented
MCP behavior where `set_config` applies an in-memory, session-scoped override,
forcing `capture_scope=window` and `max_image_dimension=1456` immediately after
connection. This is the deviation explicitly permitted by Phase 0 of the
canonical plan.
