# Building Row-Bot Installers

This guide explains how to build distributable Row-Bot installers and packages.

For version bumps, CI release workflow expectations, signing, tagging, and publish
order, use the canonical [release process](../docs/RELEASING.md). This file only
covers the installer payload and local build flow.

## Release Verification

Fast installer and release contracts run in the normal test matrix:

```bash
uv run python scripts/run_test_matrix.py installer-contracts
uv run python scripts/run_test_matrix.py platform
```

Actual package builds and installed-app smoke checks are manual release work.
Use the GitHub `Release - Build & Sign Installers` workflow for release
candidates and `Installer Verify` when you want to smoke platform packages
without publishing. Those workflows build the Windows, Linux, and macOS
artifacts, launch the installed app with isolated data dirs, and upload
checksums/manifests for review.

Windows code signing remains local-only unless the project signing policy
changes. macOS notarization remains an explicit manual workflow after the
signed artifact has been reviewed.

## Linux Tarball

Linux users should normally install with the one-line bootstrapper:

```bash
curl -fsSL https://raw.githubusercontent.com/siddsachar/row-bot/main/installer/install-linux.sh | bash
```

The bootstrapper resolves the latest GitHub Release, downloads the matching
Linux tarball for the current architecture, verifies its SHA256 from the release
manifest, and then runs the tarball's bundled `install.sh`. For a pinned
version, pass it as an argument:

```bash
curl -fsSL https://raw.githubusercontent.com/siddsachar/row-bot/main/installer/install-linux.sh | bash -s -- X.Y.Z
```

The bootstrapper installs published GitHub Release assets. It is not a way to
test an unreleased checkout from `main` or a hotfix branch before a release is
published.

Linux release tarballs are built with `installer/build_linux_app.sh`. The script mirrors
the macOS python-build-standalone approach, but emits a user-installable XDG
tarball instead of a native app bundle. It stages the React client from
`frontend/dist`, so build that first with Node 24.15.0 (release and Installer
Verify jobs do this through `.github/actions/build-client`):

```bash
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run build
./installer/build_linux_app.sh
./installer/build_linux_app.sh X.Y.Z
```

From a source checkout, this root-level wrapper is also supported for support
snippets and maintainer hotfixes:

```bash
bash build_linux_app.sh X.Y.Z
```

To test an unreleased Linux fix locally, build the tarball from the checkout and
install the tarball it produced:

```bash
bash installer/build_linux_app.sh X.Y.Z
tar -xzf dist/Row-Bot-X.Y.Z-Linux-*.tar.gz
cd Row-Bot-X.Y.Z-Linux-*
./install.sh
~/.local/bin/row-bot
```

Also smoke the server path explicitly. Start it in one terminal:

```bash
ROW_BOT_DATA_DIR="$(mktemp -d)" ~/.local/bin/row-bot serve --port 8092
```

Probe it from another terminal:

```bash
curl -fsS http://127.0.0.1:8092/healthz
curl -fsS http://127.0.0.1:8092/readyz
```

Use an isolated data directory for package smoke tests and remove it after the
process exits. A fresh server does not create an owner session automatically;
bootstrap a real acceptance browser only with an explicit
`row-bot access invite --layout desktop --origin ...` command. Do not record
that command's one-time link in CI logs or package artifacts.

If the launcher starts a process but the app never becomes ready, inspect
`~/.row-bot/row_bot_app.log` and `~/.row-bot/row_bot_app.log.prev`. The launcher prints
the app log tail and targeted recovery hints for common native dependency
failures, and `ROW_BOT_STARTUP_TIMEOUT=180 ~/.local/bin/row-bot` can be used on
slow first-run systems.

The output is `dist/Row-Bot-X.Y.Z-Linux-x86_64.tar.gz` on x86_64 runners. It
contains bundled Python, installed Python packages, app source, `bin/row-bot`, an
`install.sh`, an `uninstall.sh`, a freedesktop `.desktop` file, icon files, and
`install_info.json` for updater/dev-install detection.

Manual tarball install flow:

```bash
tar -xzf Row-Bot-X.Y.Z-Linux-x86_64.tar.gz
cd Row-Bot-X.Y.Z-Linux-x86_64
./install.sh
row-bot
```

Linux installs to `~/.local/share/row-bot/releases/<version>`, updates
`~/.local/share/row-bot/current`, creates `~/.local/bin/row-bot`, and installs the
desktop entry/icon into user XDG locations. It launches in browser/no-tray mode
by default. Native window and system tray support remain optional because Linux
desktop environments require distro-specific GTK/Qt/AppIndicator dependencies.

Provider secrets use the system keyring when Linux Secret Service/KWallet is
available. Headless Linux and WSL installs without a keyring still start cleanly;
new secrets fall back to session-only storage rather than plaintext files.

Browser automation uses Playwright's normal Linux dependency flow. The tarball
does not install system packages; users should follow Playwright's printed
dependency command if Chromium reports missing libraries.

Camera and screenshot capture are optional. If OpenCV or MSS cannot import due
to missing Linux native libraries, Row-Bot should still start; those capture tools
report unavailable until the platform libraries are installed.

## Windows Installer

### Architecture

The installer bundles the embedded Python runtime, pre-installed Python packages, and app source code. Python packages are installed from `requirements.txt`, which is a generated locked export from `pyproject.toml` and `uv.lock`. Repair and upgrade installs replace the embedded Python directory before copying the new payload so manually installed or corrupted packages cannot linger inside Row-Bot's bundled runtime. Kokoro TTS model files are auto-downloaded on first use. Ollama and Playwright Chromium are handled by the build/runtime flow, and Ollama is optional because Row-Bot can run entirely with provider models. The optional Cua Driver used by Computer Use is not bundled: Row-Bot downloads the pinned platform asset only after the user reads its telemetry disclosure and explicitly chooses Install or Repair, verifies SHA-256, and keeps it in the private Row-Bot data directory.

| Bundled in .exe | Downloaded or created outside install |
|----------------|--------------------------------------|
| Python 3.13 embeddable runtime | Ollama installer is optional for local models |
| App source code, authenticated access/server package, Remote Access UI, automatic Agent orchestration, Agent Profiles, Goal Mode, checkpointed Agent budgets and settings, child-agent runner, generation cancellation, provider-aware reasoning controls, exact context/compaction policy, native OpenCode routing, local Whisper and SenseVoice runtimes, durable document ingestion and sharded retrieval, Computer Use integration and pinned manifest, cache-only embedding fallback, responsive desktop/mobile owner UI, channel streaming, tools, providers, plugins, MCP client, Apps & Skills catalogs (curated apps, featured skills, and the compressed MCP Registry snapshot), migration wizard, the React client (built from `frontend/` at package time), Design and Developer panels, bundled skills/tool guides, static assets, and sounds | Kokoro TTS model + voices auto-download on first TTS use; SenseVoice Small is an explicit approximately 940 MB ModelScope download |
| Python packages from locked `requirements.txt` export and its matching Playwright Chromium when the platform build supports bundling | A missing source-install Browser runtime is installed only after the user's explicit Browser Automation install/repair action; Browser startup never downloads it |
| Computer Use policy, private client, installer metadata, and platform checks | Pinned Cua Driver 0.20.0 is downloaded only after the version-2 disclosure and explicit user consent |

### Prerequisites

1. **Inno Setup 6** — free installer compiler
   Download: https://jrsoftware.org/isdl.php
   Ensure `ISCC.exe` is installed (default: `C:\Program Files (x86)\Inno Setup 6\`)

2. **Internet connection** — the build script downloads Python embeddable and get-pip.py

3. **Python 3.13.2 with tkinter** on `PATH` as `python` — the build copies Tcl/Tk from it, so its
   version must match `-PythonVersion` exactly

4. **Node 24.15.0 and a built client** — run `npm --prefix frontend ci --ignore-scripts` and
   `npm --prefix frontend run build` first; the build script stages and verifies `frontend/dist`

5. **Icon file** — `row-bot.ico` in the project root
   If you don't have one, remove the `SetupIconFile` and `IconFilename` lines in `row_bot_setup.iss`.

### Build Steps

```powershell
# From the project root:
.\installer\build_installer.ps1
```

This will:
1. Download the Python 3.13 embeddable package (~15 MB) and `get-pip.py` (~2.5 MB)
2. Bundle tkinter and Tcl/Tk from the matching system Python and check that they import
3. Install the locked packages from `requirements.txt` and verify the runtime dependencies
4. Stage the React client from `frontend/dist` into a fresh folder and verify it strictly
5. Install Playwright Chromium when it can (otherwise Browser Automation waits for the user's Install or Repair)
6. Compile everything into `dist\Row-Bot-X.Y.Z-Windows-x64.exe`

#### Options

```powershell
# Pin the embedded Python patch version (default 3.13.2; the system Python must be the same version):
.\installer\build_installer.ps1 -PythonVersion "3.13.2"

# Skip downloads if build/ already has the files:
.\installer\build_installer.ps1 -SkipDownloads
```

### What Gets Installed

On the end user's machine:

```
C:\Program Files\Row-Bot\              # Installation directory
├── launch_row_bot.bat                  # Sets up the bundled runtime and starts launcher.py
├── launch_row_bot.vbs                  # Hidden-console wrapper (shortcuts point here)
├── python\                             # Embedded Python 3.13 runtime
│   ├── python.exe
│   ├── Lib\site-packages\              # Locked packages from requirements.txt
│   ├── tcl\                            # Tcl/Tk for the splash screen and first-run chooser
│   └── playwright-browsers\            # Bundled Chromium, when the build could install it
└── app\
    ├── app.py, launcher.py             # Thin root wrappers
    ├── pyproject.toml, uv.lock, requirements.txt, row-bot.ico
    ├── scripts\verify_runtime_dependencies.py
    ├── src\row_bot\                    # The application package: FastAPI server, agents, tools,
    │   │                               # providers, channels, MCP, plugins, Designer, Developer, ...
    │   └── static\client-v2\           # The React client, built from frontend/ at package time
    ├── static\                         # Vendored JS libraries, fonts, Buddy and Designer runtime assets
    ├── sounds\                         # Notification sounds
    ├── bundled_skills\                 # Built-in skills
    └── tool_guides\                    # Auto-activation tool guides

%USERPROFILE%\.row-bot\               # User data directory (auto-created at runtime)
├── threads.db                      # Conversation history & checkpoints
├── memory.db                       # Long-term memories (knowledge graph entities & relations)
├── memory_vectors/                 # FAISS index for semantic memory search
├── memory_extraction_state.json    # Tracks last extraction run
├── dream_journal.json              # Dream Cycle operation log
├── api_keys.json                   # API key metadata only; raw keys use the OS credential store when available
├── plugin_secrets.json             # Plugin API-key metadata only; raw keys use the OS credential store when available
├── providers.json                  # Provider metadata, status, Quick Choices, and masked fingerprints
├── model_catalog_cache.json         # Cached provider/Ollama model catalog rows
├── embedding_config.json            # Selected local/cloud embedding provider
├── cloud_config.json               # Legacy cloud model favorites/settings compatibility
├── app_config.json                 # Onboarding / first-run state
├── tools_config.json               # Tool enable/disable state
├── model_settings.json             # Selected model & context size
├── tts_settings.json               # Selected TTS voice
├── vision_settings.json            # Vision model & camera selection
├── voice_settings.json             # Talk, Dictate, local STT, and speech-output preferences
├── processed_files.json            # Tracked indexed documents
├── tasks.db                        # Task definitions, schedules, run history & delivery config
├── channels_config.json            # Channel settings
├── mobile.db                       # Hashed mobile pairing/device credentials, scopes, revocation, access events
├── channel_secrets.json             # Channel credential metadata only; raw secrets use OS keyring when available
├── plugin_state.json               # Installed plugin state & settings
├── shell_history.json              # Shell command history per thread
├── skills_config.json              # Skill enable/disable state
├── user_config.json                # Avatar emoji & ring color preferences
├── row_bot_app.log                   # Application log
├── developer/                       # Developer workspace links, Custom Tools, drafts, sandboxes
├── vector_store/                   # FAISS index for uploaded documents
│   └── embedding_metadata.json      # Vector-index embedding provider metadata
├── gmail/                          # Earlier Google sign-in and client files, moved into the OS keychain at start
├── calendar/                       # Earlier Calendar sign-in file, removed once the keychain holds the same grant
├── catalogs/                       # Local MCP Registry index built from the shipped snapshot, catalog state
├── browser_profile/                # Playwright persistent browser profile
├── wiki/                           # Obsidian-compatible markdown vault export
├── cache/sensevoice/               # Explicitly installed verified SenseVoice snapshot
└── kokoro/                         # Kokoro TTS model & voice data (auto-downloaded)
```

Ollama is installed system-wide via its official installer.

> **Note:** User data is stored outside `Program Files` in `~/.row-bot/` to avoid write-permission issues. Override the location by setting the `ROW_BOT_DATA_DIR` environment variable.

### Install Flow

The Inno Setup installer runs these steps:

1. **Extract files** — embedded Python, pre-installed packages, app source, assets, and launch scripts
2. **Create shortcuts** — Start Menu and optionally Desktop
3. **Optionally launch Row-Bot**

On repair/upgrade, Inno Setup deletes `{app}\python`, `{app}\app\src` and `{app}\app\static` before extraction, so no module a release removed stays behind. User data in `%USERPROFILE%\.row-bot` is not touched.

The app payload includes `pyproject.toml`, `uv.lock`, and generated `requirements.txt` so repair helpers and support diagnostics can identify the exact dependency set that produced the bundled runtime.

### End-User Experience

1. Run `Row-Bot-X.Y.Z-Windows-x64.exe`
2. Follow the wizard — the app payload is already bundled; optional model/runtime assets download only when a feature needs them
3. Launch Row-Bot from Start Menu or Desktop shortcut
4. The system tray icon appears; the React app opens on the first available local port, normally `http://localhost:8080/app-v2/`
5. First launch asks **How should Row-Bot think?** — **On this computer** (an Ollama model), **With my subscription** (ChatGPT, Claude or Grok), or **With an API key** — and Setup Center lists what is left to set up. OpenAI-compatible endpoints such as LM Studio are added under Settings › Providers › Custom endpoints

## Notes

- **CPU-only PyTorch**: `pyproject.toml` maps `torch` to the PyTorch CPU index and the generated `requirements.txt` preserves that installer policy. Users with NVIDIA GPUs can upgrade to CUDA torch after install.
- **Ollama is optional**: Row-Bot works with API-key provider models (OpenAI, Anthropic, Google AI, xAI, MiniMax, OpenCode Zen/Go, OpenRouter, Atlas Cloud, Requesty, and Ollama Cloud), ChatGPT / Codex subscription models after in-app ChatGPT sign-in, xAI Grok OAuth after in-app Grok sign-in, and Claude Subscription models after Row-Bot-owned Claude OAuth or setup-token import. Installed local Ollama chat models appear in Settings -> Models even when their family is newer than Row-Bot's curated capability lists; Vision stays conservative and requires known Vision metadata/families.
- **Reasoning and context controls**: supported provider-qualified models expose only their valid Provider default, effort, On/Off, or budget choices, saved independently per thread and model. Local Ollama Auto targets 64K, custom endpoints require detected or declared capacity, and custom caps limit Row-Bot planning without reconfiguring the server.
- **Agent orchestration**: the packaged app includes Agent Profiles, Goal Mode, child-agent delegation to existing local folders, profile/tool allowlists, profile-first workflow agents, Agent-run workflow promotion, checkpointed parent continuation and recovery, folder-scoped writer locks, work-round budgets, repeat-stall protection, and application-wide nesting, concurrency, and timeout settings. These records live in Row-Bot's local data directory alongside workflow state.
- **Durable document ingestion**: uploads are staged under configured per-file,
  batch, and total-byte limits, then processed by a lease-backed SQLite queue.
  Parsed sources, resumable extraction checkpoints, per-document vector shards,
  and graph provenance survive restart; queue controls and health repair remain
  scoped to the local Row-Bot data directory.
- **Computer Use beta**: Windows and macOS packages include Row-Bot's provider-neutral native-app tool, target-window safety policy, private Cua client, and pinned runtime manifest, but not the third-party executable. Computer Use is off by default, interactive-desktop only, and requires a separate telemetry acknowledgement and verified Cua Driver install. Linux, server, schedule, channel, workflow, and child-agent callers cannot acquire it.
- **Local embeddings**: first-run setup clearly discloses and selects the recommended private knowledge model download by default (about 700 MB), with an opt-out. Normal startup and recall remain cache-only; missing or corrupted models stay repairable through explicit Settings actions, while memory and graph search fall back quickly instead of triggering a surprise recall-time download.
- **Remote Access and server mode**: recursive `src/row_bot` packaging includes
  the versioned access store, migration bridge for the existing `mobile.db`,
  request/origin/proxy policy, single-owner session enforcement, invitation
  routes with desktop or compact layouts, Remote Access settings, `row-bot serve`, and
  `row-bot access invite/list/revoke/revoke-all/doctor`. Desktop mode remains
  local-first. Server mode requires an authenticated session even over
  loopback and runs with one worker, no tray/splash/browser, and no automatic
  Ollama startup by default.
- **Responsive mobile owner UI**: the phone shell, PWA routes,
  workflow/activity surfaces, and complete phone-safe Settings use the same
  owner authority as desktop. Original legacy mobile sessions retain owner
  access. Explicitly restricted companion sessions from the previous schema
  are revoked during migration and must be paired again. Packaging must not
  introduce a second mobile-only credential or policy store.
- **Tailscale boundary**: Tailscale remains an optional host dependency.
  Packaged Row-Bot does not bundle or install Tailscale, sign the user in,
  enable Funnel, reset Serve, alter firewall rules, or overwrite an unrelated
  Serve route. Reachability still requires a Row-Bot invitation and revocable
  session. After Row-Bot verifies a route it created, the launcher restarts the
  child so startup can trust only the local loopback proxy for that exact app
  port and allow only the verified owned `.ts.net` origin. A missing launcher
  produces an explicit manual-restart instruction.
- **Deployment artifacts**: source checkouts include hardened Docker/Compose,
  Caddy, and systemd examples under `deploy/`. The Docker image contains no
  invitations, sessions, or provider credentials and publishes only on host
  loopback by default. The normal image includes all canonical server extras,
  matching headless Chromium, native media libraries, `uv`/`uvx`, and pinned
  Node.js tooling; the recommended embedding model is a disclosed,
  checked-by-default first-run download, while voice and other model assets
  remain explicit persistent downloads. Browser-local voice uses the remote browser's microphone and
  speaker and requires HTTPS away from localhost. Review
  [`../deploy/docker/README.md`](../deploy/docker/README.md) before building or
  exposing a server image.
- **Cancellation and channel streaming**: provider cancellation transports, subprocess cancellation, shared channel streaming/finalization, interactive approvals, and durable channel notification modules are part of the recursive runtime payload.
- **Optional SenseVoice STT**: packaged voice dependencies include the CPU FunASR/PyTorch runtime, but the SenseVoice Small model is not bundled. Installation from Voice settings is an explicit approximately 940 MB ModelScope download; Row-Bot verifies the local snapshot, disables update checks during inference, and never downloads it during startup or ordinary transcription. Intel macOS is unsupported and continues to use local Whisper.
- **Developer Studio**: the packaged app includes the Developer workspace UI, repo-scoped tools, Git helpers, durable worktree allocation, optional Docker shadow sandbox, and Custom Tool builder. Docker and GitHub CLI are optional external tools; when missing, the UI reports clear setup guidance instead of blocking normal chat.
- **Plugin System v2**: the packaged app includes Plugin Center (a plugin's Advanced settings in Settings › Apps), marketplace install/update flows, manifest validation, native tools, plugin-packaged MCP tools, bundled plugin skills, plugin-owned channels, and plugin templates. Plugins install disabled by default and must pass review/configuration before contributing runtime tools or channels.
- **Apps & Skills**: the packaged app ships the curated app and featured-skill catalogs and a compressed snapshot of the MCP Registry, indexed into `catalogs/` in the data directory at start. Searching reads that index; no catalog is contacted until the user searches online, updates a catalog, or turns on the catalog schedule (off by default). MCP packages are prepared only after the user reviews their exact lock, using the managed or system Node and uv (Docker must already be installed for container images), and nothing is installed globally. Connection keys and Google/X sign-ins live in the OS keychain.
- **Model picker behavior**: Settings -> Models pickers show pinned catalog Quick Choices plus the current default. Pin Brain or Vision catalog rows before expecting them in the everyday pickers; ChatGPT / Codex, Claude Subscription, xAI Grok OAuth, and Atlas Cloud Vision pins keep their provider-specific image-input capability metadata during refresh. Atlas Cloud image-generation and video-generation catalog rows are intentionally not exposed as chat, agent, or Vision models in this phase, and Grok Imagine rows stay scoped to Image and Video surfaces.
- **Custom/self-hosted endpoints**: first-run setup can connect to OpenAI-compatible endpoints such as LM Studio, vLLM, LocalAI, or private gateways. LM Studio's local server commonly uses `http://127.0.0.1:1234/v1`; load the selected model with a larger context window, such as `32768`, so Row-Bot's agent prompt and enabled tools fit.
- **Codex credential boundary**: external Codex CLI auth files are metadata/reference only. Direct ChatGPT / Codex runtime in the packaged app requires the in-app ChatGPT sign-in and stores Row-Bot-owned tokens in the OS credential store.
- **Optional native package recovery**: built-in TTS uses Kokoro ONNX and does not require TorchCodec. If a user-approved shell command installs a broken optional native package into the embedded Python runtime, startup diagnostics and the launcher log emit recovery hints, and repair/upgrade replaces the embedded runtime.
- **Task DB recovery**: `launcher.py --reset-tasks-db` backs up `tasks.db`, `tasks.db-wal`, and `tasks.db-shm` under the resolved Row-Bot data directory, recreates a clean task schema, and prints the exact paths. `launcher.py --reset-db` backs up known local SQLite stores (`tasks.db`, `memory.db`, `threads.db` families). `launcher.py --restore-data [backup-dir]` restores known SQLite files from a recovery backup or from the latest backup when no directory is supplied.
- **Launcher**: Uses `launcher.py` (system tray icon + native window + splash screen) instead of running the server directly. The tray icon shows app status (running/stopped) and provides graceful shutdown.
- **Uninstall**: Registered with Windows Add/Remove Programs. The uninstaller removes the installation directory but does **not** delete user data in `~/.row-bot/` — users can remove it manually if desired.
