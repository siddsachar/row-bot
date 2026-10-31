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
  width, and a Linux package build and install smoke.
  Weekly (or `weekly: true` on a manual run): Installer Verify on Windows and
  macOS and the browser smoke on Firefox and WebKit.
- **Pull requests that touch the image, and every release** (`container.yml`):
  native `linux/amd64` and `linux/arm64` images, each smoked in the Compose
  shape (`scripts/smoke_docker_server.py`) and as a hosting service runs it
  (`scripts/smoke_hosting_partner.py`: one container, one volume, the host's
  generated setup code and key, a fake model, a restart). The hosting smoke
  writes the container's memory high-water mark to the job summary. A release
  pushes an image only after both smokes pass.
- **Release** (`release.yml`): the `release-gate` job checks the lock files,
  checks that the requested version matches `src/row_bot/version.py` and
  `installer/row_bot_setup.iss`, refuses a run that is not from the tag
  `vX.Y.Z`, refuses release notes for the version that still say `TODO:`, and
  refuses a commit without a green `CI / ci-ok`. It runs the
  nightly suite once only if that commit has no green nightly run. Nothing else
  is re-tested; each build install-smokes its own package and rehearses a
  background update from the release's own update parts.
- **Installer Verify** (`installer-verify.yml`): the unsigned dry run of all
  three packages for any branch, with the same install smokes as a release, a
  background update rehearsal, and the 5.1 bridge: each package installed over
  the real 5.1.0 release the way 5.1's updater installs it.

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

   It also publishes the docs site (`scripts/docs/publish.py`): it regenerates
   the reference pages from the code, builds the site with its search index and
   syncs it into `docs/`, never touching the landing page. Pull requests between
   releases change only the docs' sources (`docs-site/docs`), so the published
   site describes the release people can install. `--no-docs` skips it; then
   run `python scripts/docs/publish.py` before opening the release-prep PR.

   It adds the version's section to `RELEASE_NOTES.md` when it has none, and a
   `### For Hosts` section at its end. Write there what changes for hosts that
   package the image (breaking changes, a new recommended size, new host
   settings), or "No changes for hosts." Hosts act on it: a breaking change for
   hosts must be flagged there and announced to them at least 7 days before the
   tag. The release refuses a section that still says `TODO:` (see the
   [Hosting checklist](#hosting-checklist)).

   Then sweep the human-facing text: `RELEASE_NOTES.md`, `README.md` and the
   version sentence on `docs-site/docs/index.mdx` (publish again if you change
   docs pages). Leave historical release notes alone. The landing page's
   download links move only after the release is published (step 7 of Build
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

### Hosting checklist

Hosts that package the image update their users' Row-Bots unattended, from
`latest` (stable releases only) or the version tag. Before tagging:

- [ ] The hosting smoke is green on amd64 and arm64: the "as a hosting service
      runs it" steps of `container.yml` on the release-prep pull request (it
      changes `src/`, so the workflow runs). The release runs it again before
      it pushes the image; if it fails there, no image is pushed: say so in the
      notes and tell the hosts.
- [ ] The version's `### For Hosts` section is written, or says "No changes for
      hosts." `release.yml` refuses a section that still says `TODO:`.
- [ ] Breaking changes for hosts (a new required setting, a migration that
      needs them, a larger recommended size) were announced to the hosts at
      least 7 days before the tag.
- [ ] The memory high-water mark in the hosting smoke's job summary is under
      3.2 GB on both architectures, or the recommended size in
      `docs-site/docs/operations/hosting.mdx` and the hosts' listings is
      updated.

## Build and publish

A release is built in CI from its tag and ends as a draft GitHub release with
every file except the Windows installer. One local command on the Windows
signing machine then signs and packs Windows, writes the SHA-256 block and
publishes. Nothing is visible to users until that command publishes.

| Step | Where | What |
|---|---|---|
| 1. Tag | your checkout | `vX.Y.Z` on the merge commit |
| 2. Build | `release.yml` | installers, update parts, `row-bot-update-X.Y.Z.json`, macOS notarized and stapled, the draft release |
| 3. macOS, if Apple was slow | `notarize-finish.yml` | finishes the notarization and attaches the stapled DMG to the draft |
| 4. Windows and publish | `scripts/release_windows.ps1` | signs `Row-Bot.exe`, packs and signs the installer, uploads it, writes the SHA-256 block, publishes after you confirm |
| 5. After publishing | `update-manifest.yml`, `container.yml` | the block is checked (never overwritten); the container images and their digests file |

### What a release publishes

| File | What it is for |
|---|---|
| `Row-Bot-X.Y.Z-Windows-x64.exe` | first install, and the update path from 5.1 (its updater runs it silently) |
| `Row-Bot-X.Y.Z-macOS-arm64.dmg` | first install (signed, notarized and stapled; so is the app inside) |
| `Row-Bot-X.Y.Z-Linux-x86_64.tar.gz` | first install and the one-line installer |
| `row-bot-code-X.Y.Z.tar.xz` | background updates: the app without `static/` and `sounds/` |
| `row-bot-assets-X.Y.Z.tar.xz` | background updates: `static/` and `sounds/`, downloaded only when they changed |
| `row-bot-pylock-X.Y.Z.toml` | background updates: the `uv.lock` export the client plans libraries from |
| `row-bot-update-X.Y.Z.json` | what the release offers a background update (`row_bot.updates.manifest`) |
| `row-bot-container-X.Y.Z.json` | the container image digests Docker and cloud installs pull by (added by `container.yml` after publishing) |

The update parts are built once, from the same staged payload the installers
ship (`scripts/build_update_parts.py`): LF line endings, no bytecode, sorted
tar members without times or owners. The release body's
`<!-- row-bot-update-manifest -->` block lists the SHA-256 of every file;
the updater trusts only files listed there.

### Prerequisites on the Windows signing machine

- **Inno Setup 6** (`ISCC.exe`): https://jrsoftware.org/isdl.php, at the version
  CI used (Chocolatey's `innosetup`; the stage records it). The script refuses
  another version, because CI smoke-tested an installer packed by that one;
  `-AllowInnoMismatch` overrides it.
- **Windows SDK `signtool.exe`**: the "Windows SDK Signing Tools for Desktop Apps"
  component (the script finds the newest under `Windows Kits\10\bin`).
- **SimplySign Desktop** with the Certum code signing certificate
  (thumbprint `2341B4B36A21DF948E538A88BB194FAE4D1CAE51`). The script asks you
  to log in (the code from the phone app) once, before the first signature, and
  signs both files in that session.
- **GitHub CLI** logged in with write access to the repository (`gh auth login`).
- **Python 3.11 or newer** on `PATH` (`python` or `py -3`); no packages needed.
- About 5 GB free in `%LOCALAPPDATA%\rbr\<run id>` (the installer stage is 1.9 GB
  unpacked; the folder name is short because the stage's deepest paths are as
  long as an installed version's).

Windows signing never moves to CI (D4): do not add Certum credentials to
GitHub Actions.

### Steps

1. Tag the merge commit:

   ```bash
   git checkout main
   git pull --ff-only
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

2. Run the release from the tag:

   ```bash
   gh workflow run release.yml --ref vX.Y.Z -f version=X.Y.Z
   ```

   - `build-parts` builds the update parts and compares the lock with the
     newest earlier release and the newest earlier stable one. If a changed
     library has no wheel for a supported platform, background updates cannot
     prepare it and everyone on that platform gets "Download the full
     installer" (D16). The job then fails; run the release again with
     `-f acknowledge_full_installer=true`, and the draft's notes say "This
     update needs the full installer".
   - Each build install-smokes its package and rehearses a background update
     from this release's own parts (the code archive with only the version
     changed, the assets archive and the lock export as they are). It fails if
     the installed assets differ from the assets archive or the lock export
     does not describe the installed runtime.
   - Windows uploads the installer stage (`Row-Bot-Windows-stage`: `python\`,
     the app, `version.json`, `Row-Bot.exe` and the Inno Setup script) and an
     unsigned installer, which was the one smoke-tested.
   - macOS submits the signed app to Apple as soon as it is built
     (`notarytool submit --no-wait`), smokes it while Apple checks it, staples
     it, builds and signs the DMG from it, then notarizes and staples the DMG.
     Each wait is bounded by `notarize_wait_minutes` (45). The run summary shows
     the submission ids and their state.
   - `checksums-and-manifest` assembles `row-bot-update-X.Y.Z.json`, checks it
     with the client's own parser, and records the checksums of every file.
   - `draft-release` creates the draft `vX.Y.Z` (title and notes from the
     version's section of `RELEASE_NOTES.md`; a pre-release for `-beta`/`-rc`)
     and attaches every file except the Windows installer. The DMG is attached
     only once it is stapled. Running the release again for the same tag
     reuses the draft and removes every file this run did not build, so an
     earlier run's files (a moved tag, an older build) are never published.

3. If macOS was still with Apple when the release finished, its job succeeded
   with a warning and uploaded `macos-notarization-pending-X.Y.Z-r<run id>`.
   `notarize-finish.yml` picks it up within the hour (or now:
   `gh workflow run notarize-finish.yml -f run_id=<release run ID>`; a
   dispatched run has its own concurrency group, so a scheduled scan never
   replaces it), staples, finishes the DMG, checks it (stapled, team
   33PQRTZLNW, the app's version) and attaches it to the draft, with a record
   of its SHA-256 for step 5. **macOS is ready when the DMG is on the draft.**
   - It attaches a DMG only for the newest successful release run of the
     tag's current commit, only while the release is a draft, and never over
     a DMG already there.
   - Anything that can never be attached (the release was published or
     deleted, the tag moved, a newer run exists, Apple rejected the
     submission) deletes the pending artifact instead of coming back every
     hour; the run summary says why. If Apple rejected it, the job prints
     Apple's log: fix the build and run the release again.

4. Review the draft (notes, files) and smoke-test the files on clean or
   representative machines, as before: Windows from the unsigned
   `Row-Bot-Windows-unsigned` artifact or after step 5, the stapled DMG
   (Gatekeeper first launch of a downloaded copy), and the Linux tarball
   (`./install.sh`, `~/.local/bin/row-bot`, `~/.local/bin/row-bot serve --port 8092`
   with `/healthz` and `/readyz`). Include repair/upgrade over an existing
   install, the packaged recovery commands (`--reset-tasks-db`, `--reset-db`,
   `--restore-data` against a disposable data directory), Computer Use setup on
   Windows and macOS, and one xAI image generation with a disposable account.

5. On the Windows signing machine, from a checkout of `main` (or the tag):

   ```powershell
   .\scripts\release_windows.ps1 -Version X.Y.Z
   ```

   - **The run.** It takes the newest successful release run of the tag (or
     `-RunId <release run ID>`) and refuses it unless it built the tag's
     current commit.
   - **The stage.** It downloads the stage (kept for 30 days after the run).
     A stage it unpacked before is reused only for the same run and commit,
     after recounting its files.
   - **Signing and packing.** It signs `Row-Bot.exe` (`signtool sign /sha1
     <thumbprint> /fd SHA256 /tr http://time.certum.pl /td SHA256`), runs ISCC,
     signs the installer and verifies both signatures.
   - **Checks.** Every file on the draft must be one this run built: CI's
     checksum, or notarize-finish's record for a DMG it attached later.
     Nothing else may be on the draft. Every installer and update part must
     be there and agree with `row-bot-update-X.Y.Z.json`. The stapled DMG must
     be on the draft. The notes must say when the full installer is needed.
   - **Upload and block.** It uploads the installer and writes the SHA-256
     block over all final files into the draft. It prints the signed
     installer's SHA-256.
   - **Publish.** It asks you to type `publish X.Y.Z`. With
     `-PauseBeforePublish` it first waits while you install the draft's
     installer on a test machine. After you confirm, it checks that the draft
     still holds exactly the files the block lists, publishes the release by
     its id, and checks again.
   - **Refusals and re-runs.** It refuses to publish while macOS is not
     stapled or a file is missing, foreign or changed. Every step can be run
     again: signed files and a built installer are kept, so a SimplySign
     session that ended halfway only needs a new login and the same command.

6. After publishing:
   - `update-manifest.yml` checks the block. It is a backstop: it writes a
     block when there is none and adds files the block does not list, but it
     never changes a listed hash (a listed file whose bytes changed fails the
     run instead) and never lists a file that is not one of the release's own.
   - `container.yml` builds and smokes native `linux/amd64` and `linux/arm64`
     images, publishes the versioned multi-arch manifest to
     `ghcr.io/siddsachar/row-bot:X.Y.Z` (and `latest` for a stable release),
     then attaches `row-bot-container-X.Y.Z.json` (the index digest and each
     platform's image digest) and adds it to the block (D12). Pull and smoke
     both platforms from a logged-out GHCR client to confirm the package is
     public.
7. Move the landing page to the new release, now that its assets exist: the
   download links in `docs/site.js`, the version and `softwareVersion` in
   `docs/index.html`, and `tests/docs/test_landing_page.py`. The landing page
   changes only by hand, in its own pull request with the `landing` label: the
   Docs workflow refuses any other pull request that touches it, so the live
   site at row-bot.ai never moves before its downloads exist.

To publish a docs fix between releases, run `python scripts/docs/publish.py`
and open a pull request with the result; the Docs workflow checks that the
committed site is exactly a fresh build.
8. Test the packaged updater from the previous version on each platform: a
   5.2 install prepares the update in the background and offers "Restart to
   update"; a 5.1 install downloads and runs the full installer (Windows and
   Linux) or opens the DMG (macOS).

### Dry run

Everything above except publishing, on any branch, with no tag:

```bash
gh workflow run release.yml --ref <branch> -f version=<src/row_bot/version.py> -f dry_run=true
```

It builds all three packages and the update parts, runs every smoke and
background update rehearsal, notarizes and staples the app once (the DMG is
signed but not submitted: one submission per dry run), and runs
`scripts/release_windows.ps1 -DryRun` on Windows: the stage is downloaded and
packed by ISCC, the release is checked and the SHA-256 block is printed, with
no signing, upload or publishing. The same works locally:
`.\scripts\release_windows.ps1 -Version <version> -RunId <dry run ID> -DryRun`.

### Pulling a bad release

A published release never changes: its update description and parts are what
installs have verified and staged, and the backstop refuses a block whose files
changed. Do not re-upload a file under the same name. Instead:

1. Publish a fixed version (`X.Y.Z+1`) as soon as possible. Installs on the bad
   version update to it the normal way.
2. Until then, stop new installs from taking the bad release: mark it a
   pre-release (`gh release edit vX.Y.Z --prerelease --latest=false`), so the
   stable channel goes back to the previous stable release, or delete a beta
   release outright. Installs that have not prepared it yet no longer see it.
3. Installs already on the bad version are never moved back automatically:
   Row-Bot never downgrades. People can use Settings › Updates › Repair ("Go
   back to the previous version", which is still on disk), or install the
   previous release's installer. Say so in the bad release's notes.
4. Docker and cloud installs pull by digest: point them at the previous
   release's `row-bot-container-<v>.json`.

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
prepare the next release in the background from its update parts (the code
archive, the assets archive when it changed, and the changed libraries) in
`releases/<version>`, verified against the release manifest, then switch
`current` when Row-Bot restarts. Row-Bot 5.1 instead downloads the next
tarball and installs it itself, so the tarball keeps `install_info.json`,
`bin/row-bot`, `app/` and `python/bin/python3` (installer-verify checks it).

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
