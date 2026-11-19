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

Actual package builds and installed-app smoke checks run in GitHub Actions.
Use the `Release - Build & Sign Installers` workflow for releases (from the
tag, or `dry_run: true` on any branch) and `Installer Verify` when you want to
smoke platform packages without publishing. Both build the Windows, Linux, and
macOS packages, launch the installed app with isolated data dirs, and rehearse
a background update; Installer Verify also installs each package over the real
5.1.0 release the way 5.1's updater does (the 5.1 bridge). The release also
builds the update parts (`scripts/build_update_parts.py`) and notarizes and
staples macOS automatically.

Windows code signing stays local-only: CI uploads the installer stage, and
`scripts/release_windows.ps1` signs `Row-Bot.exe`, packs and signs the
installer with Certum on the maintainer's machine, and publishes (see
[RELEASING.md](../docs/RELEASING.md)).

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
`install.sh`, an `uninstall.sh` (the three come from `installer/linux/`), a
freedesktop `.desktop` file, icon files, `version.json`, and `install_info.json`
(which 5.1's updater checks before it installs a tarball).

Manual tarball install flow:

```bash
tar -xzf Row-Bot-X.Y.Z-Linux-x86_64.tar.gz
cd Row-Bot-X.Y.Z-Linux-x86_64
./install.sh
row-bot
```

Linux installs to `~/.local/share/row-bot/releases/<version>`, updates
`~/.local/share/row-bot/current`, records the release in
`~/.local/share/row-bot/state.json` (the release it replaces stays as `previous`;
older releases are removed), creates `~/.local/bin/row-bot`, and installs the
desktop entry/icon into user XDG locations. It launches in browser/no-tray mode
by default. Native window and system tray support remain optional because Linux
desktop environments require distro-specific GTK/Qt/AppIndicator dependencies.

`bin/row-bot` starts the active release and starts it again, with the same
arguments, when it exits with code 75 (so `row-bot serve --port N` comes back as
`serve` on the same port); each relaunch runs the wrapper of the release
`current` points at. If a release that has not become ready since it was
switched to fails to start twice in a row, it goes back to the previous release
(when that one can start). A slow start is never stopped: it counts as
unverified until it answers `/readyz`. One wrapper supervises an install and
data folder at a time (`flock`); another `row-bot` only shows the running app.
Run by systemd as a unit's `ExecStart=` it starts the app once and leaves
restarts to the unit: use `Restart=always`, or `RestartForceExitStatus=75`.

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

The installer bundles the embedded Python runtime, pre-installed Python packages, and app source code. Python packages are installed from `requirements.txt`, which is a generated locked export from `pyproject.toml` and `uv.lock`. Repair and upgrade installs replace the embedded Python directory before copying the new payload so manually installed or corrupted packages cannot linger inside Row-Bot's bundled runtime. Kokoro TTS model files are auto-downloaded on first use. Ollama is optional because Row-Bot can run entirely with provider models. Playwright Chromium is not bundled, as on macOS and Linux: Browser Automation uses an installed Chrome or Edge, and Row-Bot's matching Chromium (which PDF, PNG and PowerPoint exports need) is downloaded into the data folder only when the user chooses **Install browser runtime**. The optional Cua Driver used by Computer Use is not bundled: Row-Bot downloads the pinned platform asset only after the user reads its telemetry disclosure and explicitly chooses Install or Repair, verifies SHA-256, and keeps it in the private Row-Bot data directory.

| Bundled in .exe | Downloaded or created outside install |
|----------------|--------------------------------------|
| Python 3.13 embeddable runtime | Ollama installer is optional for local models |
| App source code, authenticated access/server package, Remote Access UI, automatic Agent orchestration, Agent Profiles, Goal Mode, checkpointed Agent budgets and settings, child-agent runner, generation cancellation, provider-aware reasoning controls, exact context/compaction policy, native OpenCode routing, local Whisper and SenseVoice runtimes, durable document ingestion and sharded retrieval, Computer Use integration and pinned manifest, cache-only embedding fallback, responsive desktop/mobile owner UI, channel streaming, tools, providers, plugins, MCP client, Apps & Skills catalogs (curated apps, featured skills, and the compressed MCP Registry snapshot), migration wizard, the React client (built from `frontend/` at package time), Design and Developer panels, bundled skills/tool guides, static assets, and sounds | Kokoro TTS model + voices auto-download on first TTS use; SenseVoice Small is an explicit approximately 940 MB ModelScope download |
| Python packages from locked `requirements.txt` export (no Playwright Chromium; a local build can add it with `-BundlePlaywright`) | Row-Bot's Playwright-matched Chromium is downloaded into `~/.row-bot/runtimes/playwright-chrome/` only by the user's explicit Browser Automation Install or Repair action, and survives updates; Browser startup never downloads it |
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

5. **Visual Studio Build Tools with the C++ x64 tools (MSVC)** — the build compiles `Row-Bot.exe`
   from `installer/windows/row_bot_launcher.c` first; GitHub's Windows runners have them

6. **Icon file** — `row-bot.ico` in the project root
   If you don't have one, remove the `SetupIconFile` and `IconFilename` lines in `row_bot_setup.iss`.

### Build Steps

```powershell
# From the project root:
.\installer\build_installer.ps1
```

This will:
1. Build `Row-Bot.exe` (with its icon, version and manifest) with MSVC
2. Download the Python 3.13 embeddable package (~15 MB) and `get-pip.py` (~2.5 MB)
3. Bundle tkinter and Tcl/Tk from the matching system Python and check that they import
4. Install the locked packages from `requirements.txt` and verify the runtime dependencies
5. Stage the app payload (`scripts/app_payload_manifest.py --stage`) and the React client from
   `frontend/dist` into `installer\build\app` and verify the client strictly
6. Leave Playwright Chromium out (`-BundlePlaywright` or `BUNDLE_PLAYWRIGHT=1` bundles it for a local build); Browser Automation's Install action downloads it into the data folder
7. Trim the runtime with the rules all three builds share (`src/row_bot/payload_trim.json`:
   libraries' test suites, C++ headers, link libraries and debug symbols, unused Google API
   discovery documents, pip's console-script launchers), then compile deterministic bytecode
   (`unchecked-hash`, recorded relative to `installer\build`) for the app and for the runtime
   modules `verify_runtime_dependencies.py all` imports, and write `version.json`
8. Compile everything into `dist\Row-Bot-X.Y.Z-Windows-x64.exe`

For a release, `Row-Bot.exe` has to be signed before Inno Setup packs it. CI
therefore keeps the stage (`installer\build\{python,app,Row-Bot.exe,version.json}`
and `row_bot_setup.iss`) as the `Row-Bot-Windows-stage` artifact, and
`scripts/release_windows.ps1` signs `Row-Bot.exe`, runs ISCC on that stage
and signs the installer locally.

#### Options

```powershell
# Pin the embedded Python patch version (default 3.13.2; the system Python must be the same version):
.\installer\build_installer.ps1 -PythonVersion "3.13.2"

# Skip downloads if build/ already has the files:
.\installer\build_installer.ps1 -SkipDownloads
```

### What Gets Installed

On the end user's machine, per user and without administrator rights (the layout
contract is `src/row_bot/install_layout.py`):

```
%LOCALAPPDATA%\Programs\Row-Bot\       # Install root
├── Row-Bot.exe                         # Starts the active version; shortcuts point here
├── state.json                          # Active and previous version, update and rollback state
└── versions\
    ├── Row-Bot Server.exe              # The server program, its Python DLLs and python3XX._pth: copies
    │                                   # from the active version, at a path no update changes (B365)
    └── X.Y.Z\                          # One folder per version, never changed after install
        ├── version.json                # Version, Python version and uv.lock hash
        ├── python\                     # Embedded Python 3.13 runtime
        │   ├── python.exe
        │   ├── Lib\site-packages\      # Locked packages from requirements.txt, trimmed (payload_trim.json)
        │   ├── tcl\                    # Tcl/Tk for the splash screen and first-run chooser
        │   └── playwright-browsers\    # Only in a local -BundlePlaywright build (or a version that bundled it)
        └── app\
            ├── app.py, launcher.py     # Thin root wrappers
            ├── pyproject.toml, uv.lock, requirements.txt, row-bot.ico
            ├── scripts\verify_runtime_dependencies.py
            ├── src\row_bot\            # The application package: FastAPI server, agents, tools,
            │   │                       # providers, channels, MCP, plugins, Designer, Developer, ...
            │   └── static\client-v2\   # The React client, built from frontend/ at package time
            ├── static\                 # Vendored JS libraries, fonts, Buddy and Designer runtime assets
            ├── sounds\                 # Notification sounds
            ├── bundled_skills\         # Built-in skills
            └── tool_guides\            # Auto-activation tool guides

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

> **Note:** User data is stored in `~/.row-bot/`, never under the install root. Override the location by setting the `ROW_BOT_DATA_DIR` environment variable.

`Row-Bot.exe` sets the environment Python needs (`PYTHONNOUSERSITE`,
`PYTHONIOENCODING`, `PYTHONDONTWRITEBYTECODE`, Tcl/Tk, `PATH`,
`ROW_BOT_INSTALL_ROOT`, and `PLAYWRIGHT_BROWSERS_PATH`: the version's
`python\playwright-browsers` if it bundles Chromium, otherwise
`<data dir>\playwright-browsers`, as the macOS host and the Linux wrapper do) and starts `versions\<active>\python\python.exe
versions\<active>\app\launcher.py` with its own arguments in a hidden console.
The launcher runs the server as `versions\Row-Bot Server.exe`, which it keeps in
step with the active version (the version's signed program, its Python DLLs and
a `._pth` pointing at the version's folders). Windows Firewall ties its answer to
a program's path, so with phone or LAN access on it asks once, not after every
update. Without that copy the server runs as the version's own
`python\Row-Bot Server.exe`.
It starts it again when it exits with code 75. A version that has not become
ready since it was switched to and fails to start twice in a row is replaced by
the previous one (when that one can start), recorded as `last_rollback` in
`state.json`. A slow start is never stopped; the Python launcher logs one that
is not ready after 240 s and verifies it once it is. A second start while
Row-Bot runs only shows it. The rules are shared with the macOS tray host and
the Linux wrapper (`contracts/install-layout/launch-cases.json`). Its log is
`~/.row-bot/launcher-host.log`.


### Install Flow

The Inno Setup installer has no wizard pages except its progress page:

1. **Remove an install for all users** — a 5.x Row-Bot installed in `Program Files` is
   uninstalled first, with one administrator prompt, so Apps & features does not list
   Row-Bot twice. If the prompt is declined (or the user is not an administrator), the
   per-user install goes ahead and `state.json` records `legacy_install_pending`, so the
   app can offer to remove the old one later. User data in `%USERPROFILE%\.row-bot` is kept.
2. **Extract files** — `Row-Bot.exe` and `versions\X.Y.Z\{app,python,version.json}`.
   Reinstalling a version replaces its folder whole. A running `Row-Bot.exe` is renamed
   aside rather than replaced.
3. **Record the install** — `state.json` makes X.Y.Z active and keeps the version it
   replaces as `previous`; older versions are deleted.
4. **Create shortcuts** — Start menu, and desktop on the first install only (an upgrade
   never brings back a desktop shortcut the user removed), both with the AppUserModelID
   `ai.row-bot.desktop`.
5. **Start Row-Bot** — also after `/SILENT` or `/VERYSILENT`, which 5.1's updater uses.
   `/NOLAUNCH` skips it (the CI smokes use it).

Uninstall removes `%LOCALAPPDATA%\Programs\Row-Bot` only. User data in
`%USERPROFILE%\.row-bot` is not touched.

The app payload includes `pyproject.toml`, `uv.lock`, and generated `requirements.txt` so repair helpers and support diagnostics can identify the exact dependency set that produced the bundled runtime.

### End-User Experience

1. Run `Row-Bot-X.Y.Z-Windows-x64.exe`; it installs without questions or administrator rights and starts Row-Bot — the app payload is already bundled; optional model/runtime assets download only when a feature needs them
2. Later, launch Row-Bot from the Start menu or Desktop shortcut
3. If a 5.x Row-Bot is installed for all users, Windows asks once for administrator approval to remove it; declining keeps it, and Row-Bot is installed for the user anyway
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
- **Uninstall**: Registered with Windows Apps & features for the current user. The uninstaller removes the install root (every version, `state.json` and `Row-Bot.exe`) but does **not** delete user data in `~/.row-bot/` — users can remove it manually if desired.
- **macOS layout**: the app bundle's `Contents/Resources/{app,python}` is the seed version. Updates stage versions in `~/Library/Application Support/Row-Bot/versions/<version>` and name the active one in `state.json` there. The tray host uses the bundle when it is newer than the active version (a newer DMG dragged in) or when there is no valid `state.json`, applies the same relaunch and rollback rules as `Row-Bot.exe`, and offers to move itself to Applications when it runs from a download or a disk image (never when it is already in `/Applications` or `~/Applications`, even if macOS translocated it). The move copies the app to a hidden name in Applications, checks its bundle id, version and signature, swaps it in with one rename and only then trashes the app it replaced; on any failure Applications is left as it was.
- **macOS 14 or newer**: the bundle declares `LSMinimumSystemVersion` 14.0 and the tray host is built for macOS 14 (`-mmacosx-version-min=14.0`), because several bundled wheels (av, faiss-cpu, onnxruntime) need macOS 14.
