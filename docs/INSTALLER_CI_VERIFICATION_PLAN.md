# Installer and CI Verification Plan

This is the closeout plan for dependency and supply-chain discipline changes.
It keeps the immediate lint path low risk while proving the real installer
payloads in GitHub Actions.

## Closure Gates

1. Local pre-push checks:
   - `uv lock --check`
   - `python scripts/export_locked_requirements.py --check`
   - `uv run ruff check . --select E9,F63,F7,F82 --output-format=github`
   - workflow YAML parse check
   - `git diff --check`
2. Pull request checks:
   - `CI`
   - `UV Lockfile Check`
   - `OSV Scanner`
   - `Lint (safety + advisory)`
   - Existing OSV findings are baselined in `osv-scanner.toml` with
     package/version-specific expiry dates; new vulnerable packages or changed
     vulnerable versions should still fail the scan.
3. Manual GitHub installer verification:
   - Run **Actions -> Installer Verify -> Run workflow** on the branch or tag to verify.
   - Leave Windows, Linux, and macOS enabled unless the change is platform-specific and the skipped platform is documented.
4. Final release artifact build:
   - Run **Actions -> Release - Build & Sign Installers -> Run workflow** from the release tag when preparing a release.
   - Use the release workflow artifacts only after signing, notarization, and final manual smoke testing.

## Lint Strategy

The current lint gate blocks only on high-signal Ruff failures:

```bash
uv run ruff check . --select E9,F63,F7,F82 --output-format=github
```

That catches syntax errors, undefined names, and related serious failures without
forcing a repo-wide formatting cleanup in this change. Full `ruff check .` and
`ruff format --check .` still run as advisory steps so the existing lint debt is
visible in GitHub without blocking dependency or installer work.

## Installer Verify Workflow

The manual `Installer Verify` workflow uses the real platform build scripts:

- Windows builds the Inno Setup installer, silently installs it into a temporary
  directory, verifies required payload files, runs the installed runtime
  dependency verifier with the embedded Python, and smoke-tests `launcher.py` in
  server mode.
- Linux builds the self-contained tarball, installs it into temporary XDG paths,
  then smokes both the default launcher and explicit server mode.
- macOS builds an unsigned app/pkg with the same macOS builder. It keeps
  `BUNDLE_PLAYWRIGHT=0` to match the signed release policy, smokes the assembled
  bundle with bundled Python, installs the pkg into the ephemeral runner, and
  smokes the installed app layout.

Artifacts from this workflow are retained briefly for diagnosis. They are
verification artifacts, not release assets.

## Deferred Gate

The clean-machine manual install matrix remains deferred until the broader test
cleanup is done. When that work resumes, use fresh Windows, macOS, and Linux VMs
to cover first launch, upgrade over an existing install, repair/recovery
commands, provider setup, Designer export, browser tooling, and updater flow.
