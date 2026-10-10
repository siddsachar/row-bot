# Release Process

This is the end-to-end release checklist for Row-Bot. `scripts/run_test_matrix.py`
is the source of truth for the test tiers named here; the workflows under
`.github/workflows/` run the same tiers.

## Versioning

Row-Bot uses semantic versioning:

- Patch: `5.0.1` for bug fixes
- Minor: `5.1.0` for new backwards-compatible features
- Major: `5.0.0` for breaking changes (5.0.0 removed the NiceGUI interface)
- Beta/RC: `5.1.0-beta.1`, `5.1.0-rc.1`

## What CI already proves

- **Every pull request and every push to `main`** (`ci.yml`): the `quality`
  tier, the client checks (`client-foundation`), one sharded deterministic
  pytest pass with the runtime dependency and packaged client asset checks, the
  strict app smoke, the `platform` tier on Windows (Python 3.13, as shipped)
  and macOS, and the Chromium browser smoke (not yet part of `ci-ok`).
  `CI / ci-ok` is the one required check; every commit on `main` keeps its own
  completed run.
- **Nightly** (`nightly.yml`): the whole deterministic suite with the slow tests
  on Linux, Windows and macOS, the browser nightly set at desktop and phone
  width, a Linux package build and install smoke, and the docs reference check.
  Weekly (or `weekly: true` on a manual run): Installer Verify on Windows and
  macOS and the browser smoke on Firefox and WebKit.
- **Release** (`release.yml`): the `release-gate` job checks the lock files,
  checks that the requested version matches `src/row_bot/version.py` and
  `installer/row_bot_setup.iss`, and refuses a commit without a green
  `CI / ci-ok`. It runs the nightly suite once only if that commit has no green
  nightly run. Nothing else is re-tested; each build install-smokes its own
  package.
- **Installer Verify** (`installer-verify.yml`): the unsigned dry run of all
  three packages for any branch, with the same install smokes as a release.

Every packaging job builds the React client first (`.github/actions/build-client`,
Node 24.15.0, `npm ci --ignore-scripts`), and the build scripts stage it into
`src/row_bot/static/client-v2` inside the package and verify it strictly.

## Before release

1. Merge every feature and fix PR for the release into `main`.
2. Cut a release-prep branch:

   ```bash
   git checkout main
   git pull --ff-only
   git checkout -b chore/release-vX.Y.Z
   ```

3. Bump the version:

   ```bash
   uv run python scripts/cut_release.py X.Y.Z
   ```

   This rewrites `src/row_bot/version.py`, `installer/row_bot_setup.iss`, the
   `Start Row-Bot.command` fallback, the `release.yml` default, the macOS
   template `Info.plist`, the bug report version placeholder and the docs
   screenshot revision (`docs-site/src/components/Screenshot.tsx`). The Linux
   and macOS build scripts, the generated docs reference pages and the release
   gate read `src/row_bot/version.py`, so they need no edit.

   Then sweep the human-facing text: `RELEASE_NOTES.md`, `README.md`, the
   version sentence on `docs-site/docs/index.mdx`, and regenerate the docs
   reference pages. Leave historical release notes alone. The landing page's
   download links move only after the release is published (step 10 of Build
   and publish).
4. Run the checks locally:

   ```bash
   uv lock --check
   python scripts/export_locked_requirements.py --check
   uv sync --locked --all-extras --group test
   uv run python scripts/verify_runtime_dependencies.py all
   uv run python scripts/run_test_matrix.py pr
   uv run python scripts/run_test_matrix.py installer-contracts
   uv run python scripts/run_test_matrix.py platform
   uv run python scripts/run_test_matrix.py browser-smoke
   ```

   Run `platform` on your own OS (CI covers Windows and macOS). For docs
   changes, also run `uv run python scripts/run_test_matrix.py docs`,
   `npm audit --omit=dev` in `docs-site/`, and review the build-only advisory
   note in [`docs-site/README.md`](../docs-site/README.md). Do not force a
   dependency rewrite to hide an advisory with no compatible published fix.
   `uv run python scripts/run_test_matrix.py nightly` runs the nightly Python
   set locally when a change needs it before merging.
5. Confirm new shipped runtime files are covered by platform packaging:
   `scripts/app_payload_manifest.py` (the single list the Linux and macOS
   scripts copy), Windows `installer/row_bot_setup.iss`, macOS
   `installer/build_mac_app.sh`, Linux `installer/build_linux_app.sh`, the Linux
   bootstrapper `installer/install-linux.sh`, and the payload notes in
   `installer/README.md`. Non-Python data under `src/row_bot` (catalog JSON, the
   MCP Registry snapshot) also needs a `[tool.setuptools.package-data]` entry in
   `pyproject.toml` for the wheel the Docker image installs. The source-layout
   and payload contract is summarized in
   [`docs/SOURCE_LAYOUT.md`](SOURCE_LAYOUT.md). For server or deployment
   changes, also review `deploy/docker/Dockerfile`,
   `deploy/docker/compose.yaml`, the reverse-proxy and systemd examples under
   `deploy/`, `.dockerignore`, and `.github/workflows/container.yml`. For
   Computer Use releases, confirm the pinned Cua manifest is packaged while the
   third-party executable remains an explicit post-install download.
6. Smoke-test first-run behaviour against a clean data directory before
   building artifacts: the "How should Row-Bot think?" first run and Setup
   Center, migration imports, provider config defaults, and Custom/Self-hosted
   endpoint setup. Exercise automatic Agent delegation and a durable document
   upload through completion, cancellation, and restart recovery. Start
   `row-bot serve` with an isolated data directory and confirm loopback is
   still gated until an explicit owner invitation is redeemed; do not copy
   one-time invitation URLs into logs or artifacts. Confirm Computer Use
   remains off by default and does not download or invoke Cua before its
   disclosure and an explicit Install or Repair action. Confirm Browser
   readiness is read-only at startup, a missing or mismatched managed Chromium
   stays unavailable until an explicit Browser Install or Repair action, and a
   supported installed Chrome or Edge channel remains selectable. Exercise
   filter-aware conversation selection and deletion with active work, a linked
   design, and a Developer worktree or sandbox containing unimported changes;
   retained recovery paths must be reported. On native Windows or macOS, tear
   Buddy off into its desktop overlay, send and stop a turn, review a simple
   approval, reopen the full thread, and recover the overlay from the tray.
7. Dry-run the installers on the release-prep branch (always for dependency,
   payload, installer or workflow changes):

   ```bash
   gh workflow run installer-verify.yml --ref chore/release-vX.Y.Z
   ```

   Windows, Linux, and macOS should all pass unless a skipped platform is
   documented. Each job builds the client, builds the package, installs it into
   a temporary folder, verifies the installed runtime and client assets, and
   starts the installed launcher (and `serve` on Linux) against `/healthz`,
   `/readyz` and `/`, which must redirect to the React client at `/app-v2/`.
8. Open the release-prep PR, merge it once `CI / ci-ok` passes, and wait for CI
   on the merge commit. To avoid running the nightly suite inside the release,
   dispatch `gh workflow run nightly.yml --ref main` on that commit first (or
   wait for the scheduled run).

## Build and publish

1. Tag the merge commit:

   ```bash
   git checkout main
   git pull --ff-only
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

2. Run GitHub Actions -> `Release - Build & Sign Installers` from the tag:

   ```bash
   gh workflow run release.yml --ref vX.Y.Z -f version=X.Y.Z
   ```

   It produces the workflow artifacts `Row-Bot-Windows` (unsigned
   `Row-Bot-X.Y.Z-Windows-x64.exe`), `Row-Bot-Linux`
   (`Row-Bot-X.Y.Z-Linux-x86_64.tar.gz`), `Row-Bot-macOS` (signed
   `Row-Bot-X.Y.Z-macOS-arm64.dmg`), `Row-Bot-macOS-pkg`, and
   `Row-Bot-release-sha256` (the checksums of everything built). Final release
   assets are uploaded manually after signing and smoke testing. Keep the run
   ID for notarization.
3. Download the Windows setup exe from the workflow artifact and sign it
   locally with the Certum certificate. Windows signing is intentionally not
   done in CI:

   ```powershell
   $signtool = "C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
   $exe = "dist\Row-Bot-X.Y.Z-Windows-x64.exe"
   & $signtool sign /sha1 2341B4B36A21DF948E538A88BB194FAE4D1CAE51 /fd SHA256 /tr http://time.certum.pl /td SHA256 /d "Row-Bot" /du "https://row-bot.ai" $exe
   & $signtool verify /pa /v $exe
   ```

4. Notarize the macOS DMG manually, after testing the signed DMG:
   - `gh workflow run notarize-submit.yml -f build_run_id=<release run ID>`;
     copy the Apple submission ID from its log.
   - `gh workflow run notarize-check.yml -f submission_id=<ID> -f build_run_id=<release run ID>`;
     re-run it until Apple reports `Accepted`, then download the
     `Row-Bot-macOS-stapled` artifact. Only the stapled DMG is a release asset.
5. Download the Linux `Row-Bot-X.Y.Z-Linux-x86_64.tar.gz` artifact, extract it on
   a clean Linux VM, run `./install.sh`, and confirm `~/.local/bin/row-bot` opens
   the React client in the browser and `~/.local/bin/row-bot serve --port 8092`
   answers `/healthz` and `/readyz`.
6. Upload the signed exe, the stapled DMG, and the Linux tarball to the draft
   GitHub Release (Release Drafter keeps one up to date). Use the notes from
   `RELEASE_NOTES.md`; remove any unsigned, unstapled, or wrong-version asset.
7. Smoke-test the final Windows, macOS, and Linux assets on clean or
   representative machines. For Windows, include repair/upgrade over an
   existing install and confirm the bundled `python\`, `app\src\` and
   `app\static\` folders are replaced while Row-Bot user data is preserved. If a
   broken optional package such as TorchCodec was present in the old embedded
   runtime, confirm it is removed or the startup log contains a clear recovery
   hint. Also run the packaged launcher recovery commands against a disposable
   data directory: `--reset-tasks-db`, `--reset-db`, and `--restore-data`.
   Confirm they print the resolved data paths and that task DB reset backs up
   `tasks.db`, `tasks.db-wal`, and `tasks.db-shm`.
   On Windows and macOS, also exercise Computer Use setup, telemetry consent,
   pinned-runtime verification, one target-window action, Stop, Take over, and
   permission recovery. Confirm screenshots and typed content do not appear in
   logs. On Linux, confirm Computer Use reports unsupported without attempting
   a driver download. With a disposable xAI API account, refresh the live image
   catalog and run one supported image generation; confirm an endpoint-rejected
   optional quality value is retried once without that field while timeouts and
   other failures do not submit a duplicate generation.
8. Publish the GitHub Release.
9. Confirm the automation that publishing starts:
   - `.github/workflows/update-manifest.yml` appends the
     `<!-- row-bot-update-manifest -->` SHA256 block for the Windows exe, macOS
     DMG and Linux tarball to the release body. If an asset is missing, attach
     it and re-run `gh workflow run update-manifest.yml -f tag=vX.Y.Z`.
   - `.github/workflows/container.yml` builds and smokes native `linux/amd64`
     and `linux/arm64` images, publishes the versioned multi-arch manifest to
     `ghcr.io/siddsachar/row-bot:X.Y.Z`, and updates `latest` for a stable
     release. Inspect the workflow summary for the release, architecture, and
     manifest digests; pull and smoke both platforms from a logged-out GHCR
     client to confirm the package is public.
10. Move the landing page to the new release, now that its assets exist: the
    download links in `docs/site.js`, the version and `softwareVersion` in
    `docs/index.html`, and `tests/docs/test_landing_page.py`, then publish the
    docs site.
11. Test the packaged updater from the previous stable version on each
    platform.

## 5.0.0 Upgrade Note

Row-Bot 5.0.0 removes the NiceGUI interface. The React client is the only UI:
the server is plain FastAPI/uvicorn, `/` redirects to `/app-v2/`, and the
`--legacy-ui` and `--client-v2` launcher flags are accepted but do nothing (they
log that they are deprecated and open the React client). NiceGUI and its
dependencies are no longer installed. What React took over is recorded in
[`docs/NICEGUI_RETIREMENT.md`](NICEGUI_RETIREMENT.md).

- In-app updates from 4.x keep working: the asset names and the SHA256
  manifest marker are unchanged.
- The Windows installer deletes `{app}\python`, `{app}\app\src` and
  `{app}\app\static` before copying, so the removed NiceGUI modules and assets do
  not survive an in-place upgrade. The macOS app bundle and the Linux release
  folder are replaced whole.
- User data in `~/.row-bot` (or `ROW_BOT_DATA_DIR`) is untouched.
- For this release the version bump and notes are part of the feature PR
  (#372) rather than a separate release-prep PR.

## v4 Rebrand Upgrade Note

Row-Bot v4 uses Row-Bot release asset names and the Row-Bot SHA256 manifest
marker. Pre-v4 in-app updaters recognize only the old 3.x artifact and manifest
contract, so do not upload duplicate legacy-named v4 assets to bridge that gap.
For the v4 jump, direct existing users to download and run a Row-Bot v4
installer manually. Current Row-Bot releases no longer run the old automatic
Thoth-to-Row-Bot startup migration; users still on Thoth or an early Row-Bot
build should first install and launch a previous migration-capable Row-Bot
release, then upgrade to the current release. Future Row-Bot releases are
discoverable by the Row-Bot updater using the v4 asset contract.

## Linux Release Notes

Linux is shipped as a one-line installer backed by a self-contained XDG
user-install tarball, not as a root package. The supported baseline launches
Row-Bot in the system browser and avoids requiring pywebview, GTK/Qt,
AppIndicator, or tray backends. Native window and tray mode can still be tested
manually with `row-bot --native` or `row-bot --tray` on desktops with the required
libraries.

The user-facing install command is:

```bash
curl -fsSL https://raw.githubusercontent.com/siddsachar/row-bot/main/installer/install-linux.sh | bash
```

The bootstrapper resolves the latest GitHub Release, downloads the matching
`Row-Bot-X.Y.Z-Linux-ARCH.tar.gz`, verifies its SHA256 from the release manifest,
and then runs the tarball's bundled `install.sh`.

For unreleased Linux hotfix validation from a checkout, use the build script,
not the one-line bootstrapper. The bootstrapper always resolves published
GitHub Release assets. The build script stages the client from
`frontend/dist`, so build it first (Node 24.15.0). From the repository root:

```bash
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run build
bash installer/build_linux_app.sh X.Y.Z
tar -xzf dist/Row-Bot-X.Y.Z-Linux-*.tar.gz
cd Row-Bot-X.Y.Z-Linux-*
./install.sh
~/.local/bin/row-bot
```

The root-level `build_linux_app.sh` wrapper delegates to
`installer/build_linux_app.sh` so support snippets run from the checkout root do
not fail with a missing-script error.

If packaged Linux startup fails after printing `Row-Bot server started`, collect:

```bash
tail -200 ~/.row-bot/row_bot_app.log
tail -200 ~/.row-bot/row_bot_app.log.prev
uname -a
cat /etc/os-release
~/.local/bin/row-bot serve --port 8092 --no-ollama
```

The launcher prints the selected port, child-process exit code when available,
and the tail of `~/.row-bot/row_bot_app.log` on readiness failure. For slow machines
or first-run package initialization, increase the wait with
`ROW_BOT_STARTUP_TIMEOUT=180 ~/.local/bin/row-bot`.

The tarball installs under `~/.local/share/row-bot/releases/<version>`, updates
`~/.local/share/row-bot/current`, creates `~/.local/bin/row-bot`, and installs a
freedesktop `.desktop` file plus icon into user XDG locations. In-app updates
download the next Linux tarball, verify SHA256 through the release manifest,
install the new release under the same user-owned tree, flip the `current`
symlink, and restart through `~/.local/bin/row-bot`.

Manual Linux smoke matrix before publishing:

- Ubuntu 22.04 or 24.04 GNOME Wayland
- Debian 12
- Fedora current
- Headless Ubuntu server mode

Minimum smoke checks:

- Fresh tarball install and desktop launcher
- Default installed command: `~/.local/bin/row-bot`
- One-line installer after the GitHub Release is published
- `~/.local/bin/row-bot serve --port 8092` plus `/healthz` and `/readyz`, and
  `/` redirecting an unpaired browser to Connect
- Owner invitation redemption, authenticated refresh, revocation, and a second
  browser being rejected while the first owner session is active
- Docker Compose startup with persistent `/data`, loopback-only publishing,
  container health, restart persistence, and a logged-out pull of the published
  versioned GHCR image on both supported architectures
- First run ("How should Row-Bot think?") with Providers and Custom/Self-hosted
  paths
- Ollama local model when `ollama` is installed and in `PATH`
- Browser tool with a supported installed browser channel or after the explicit
  managed Playwright browser/dependency install; startup alone must not download
  it
- Computer Use remains unavailable without attempting a Cua download
- A conversation's Design panel export and the vault/open-folder actions
- Update from the previous Linux tarball to the new tarball

Camera/screenshot capture is optional on Linux. Missing OpenCV/MSS native
dependencies should disable those capture paths without preventing the app from
serving `/healthz`.

## Post-release

- Post release notes and announcement.
- Open a tracking issue for the next patch/minor release.
- Label any follow-up bugs with the released version.
