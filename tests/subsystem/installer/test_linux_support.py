import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import row_bot.launcher as launcher
import pytest
import row_bot.updater as updater
from scripts import app_payload_manifest
from scripts import check_linux_native_baseline


pytestmark = pytest.mark.platform

REQUIRED_RUNTIME_PACKAGES = (
    "voice",
    "buddy",
    "migration",
    "mobile",
    "providers",
    "mcp_client",
    "plugins",
    "skills_hub",
    "tools",
    "channels",
    "designer",
    "developer",
    "utils",
)


def _linux_launcher_template() -> str:
    script = Path("installer/build_linux_app.sh").read_text(encoding="utf-8")
    start = script.index("cat > \"$PACKAGE_ROOT/bin/row-bot\" <<'LAUNCHER'")
    launcher_start = script.index("#!/usr/bin/env bash", start)
    launcher_end = script.index("\nLAUNCHER", launcher_start)
    return script[launcher_start:launcher_end]


def _win_source_path(relative_path: str) -> str:
    return "..\\" + relative_path.replace("/", "\\")


def _windows_installer_sources() -> set[str]:
    iss = Path("installer/row_bot_setup.iss").read_text(encoding="utf-8")
    return set(re.findall(r'Source:\s+"([^"]+)"', iss))


def _windows_source_covers_dir(sources: set[str], directory: str) -> bool:
    prefix = _win_source_path(directory + "/")
    return any(source.startswith(prefix) for source in sources)


def test_linux_asset_selection(monkeypatch):
    monkeypatch.setattr(updater.platform, "system", lambda: "Linux")
    monkeypatch.setattr(updater.platform, "machine", lambda: "x86_64")
    body = (
        "Notes\n\n<!-- row-bot-update-manifest -->\n"
        "```manifest\nschema: 1\nfiles:\n"
        "  Row-Bot-3.21.0-Linux-x86_64.tar.gz: sha256=" + "a" * 64 + "\n"
        "```\n"
    )
    release = {
        "tag_name": "v3.21.0",
        "prerelease": False,
        "published_at": "2026-05-04T12:00:00Z",
        "html_url": "https://github.com/siddsachar/row-bot/releases/tag/v3.21.0",
        "body": body,
        "assets": [{
            "name": "Row-Bot-3.21.0-Linux-x86_64.tar.gz",
            "size": 123,
            "browser_download_url": "https://github.com/siddsachar/row-bot/releases/download/v3.21.0/Row-Bot-3.21.0-Linux-x86_64.tar.gz",
        }],
    }

    info = updater._parse_release(release, "stable")

    assert info is not None
    assert info.asset_name == "Row-Bot-3.21.0-Linux-x86_64.tar.gz"
    assert info.sha256 == "a" * 64


def test_windows_asset_selection_accepts_hyphenated_installer(monkeypatch):
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    body = (
        "Notes\n\n<!-- row-bot-update-manifest -->\n"
        "```manifest\nschema: 1\nfiles:\n"
        "  Row-Bot-3.21.0-Windows-x64.exe: sha256=" + "c" * 64 + "\n"
        "```\n"
    )
    release = {
        "tag_name": "v3.21.0",
        "prerelease": False,
        "published_at": "2026-05-04T12:00:00Z",
        "html_url": "https://github.com/siddsachar/row-bot/releases/tag/v3.21.0",
        "body": body,
        "assets": [{
            "name": "Row-Bot-3.21.0-Windows-x64.exe",
            "size": 123,
            "browser_download_url": "https://github.com/siddsachar/row-bot/releases/download/v3.21.0/Row-Bot-3.21.0-Windows-x64.exe",
        }],
    }

    info = updater._parse_release(release, "stable")

    assert info is not None
    assert info.asset_name == "Row-Bot-3.21.0-Windows-x64.exe"
    assert info.sha256 == "c" * 64


def test_windows_asset_selection_accepts_legacy_setup_name(monkeypatch):
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    body = (
        "Notes\n\n<!-- row-bot-update-manifest -->\n"
        "```manifest\nschema: 1\nfiles:\n"
        "  RowBotSetup_3.21.0.exe: sha256=" + "d" * 64 + "\n"
        "```\n"
    )
    release = {
        "tag_name": "v3.21.0",
        "prerelease": False,
        "published_at": "2026-05-04T12:00:00Z",
        "html_url": "https://github.com/siddsachar/row-bot/releases/tag/v3.21.0",
        "body": body,
        "assets": [{
            "name": "RowBotSetup_3.21.0.exe",
            "size": 123,
            "browser_download_url": "https://github.com/siddsachar/row-bot/releases/download/v3.21.0/RowBotSetup_3.21.0.exe",
        }],
    }

    info = updater._parse_release(release, "stable")

    assert info is not None
    assert info.asset_name == "RowBotSetup_3.21.0.exe"
    assert info.sha256 == "d" * 64


def test_app_payload_manifest_declares_required_runtime_payload():
    manifest = app_payload_manifest.build_manifest(Path("."))
    payload_dirs = set(manifest["payload_dirs"])
    asset_dirs = set(manifest["asset_dirs"])

    assert "src/row_bot" in payload_dirs
    for package in REQUIRED_RUNTIME_PACKAGES:
        assert Path("src/row_bot", package).is_dir(), f"runtime package missing: {package}"
    assert {"static", "sounds", "tool_guides", "bundled_skills"} <= asset_dirs
    assert "pyproject.toml" in manifest["root_files"]
    assert "uv.lock" in manifest["root_files"]
    assert "requirements.txt" in manifest["root_files"]
    assert "row-bot.ico" in manifest["root_files"]
    assert "scripts/verify_runtime_dependencies.py" in manifest["runtime_script_files"]
    assert "docs/row_bot_glyph_256.png" in manifest["linux_icon_candidates"]
    assert "docs/row_bot_glyph.png" in manifest["mac_icon_source_candidates"]
    assert "debug_tools.py" not in manifest["root_python_files"]

    for relative_path in app_payload_manifest.app_payload_paths(Path(".")):
        assert Path(relative_path).exists(), f"manifest path missing: {relative_path}"


def test_windows_installer_payload_matches_app_manifest_contract():
    manifest = app_payload_manifest.build_manifest(Path("."))
    sources = _windows_installer_sources()

    expected_files = (
        manifest["root_python_files"]
        + manifest["root_files"]
        + manifest["runtime_script_files"]
    )
    missing_files = [path for path in expected_files if _win_source_path(path) not in sources]
    assert not missing_files, f"Windows installer is missing manifest files: {missing_files}"

    expected_dirs = manifest["payload_dirs"] + manifest["asset_dirs"]
    missing_dirs = [path for path in expected_dirs if not _windows_source_covers_dir(sources, path)]
    assert not missing_dirs, f"Windows installer is missing manifest directories: {missing_dirs}"


def test_linux_install_marker_is_not_dev_install(monkeypatch, tmp_path):
    app_root = tmp_path / "current" / "app"
    app_root.mkdir(parents=True)
    marker = tmp_path / "current" / "install_info.json"
    marker.write_text(json.dumps({
        "platform": "linux",
        "install_kind": "xdg-user-tarball",
        "version": "3.21.0",
    }), encoding="utf-8")

    monkeypatch.setattr(updater.platform, "system", lambda: "Linux")

    assert updater._linux_install_root(app_root) == tmp_path / "current"


def test_linux_safe_tar_extraction_rejects_traversal(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("bad", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(payload, arcname="../payload.txt")

    try:
        updater._safe_extract_tar(archive, tmp_path / "out")
    except updater.UpdateError as exc:
        assert "unsafe path" in str(exc)
    else:
        raise AssertionError("expected unsafe tar path to be rejected")


def test_linux_tarball_installs_into_xdg_tree(monkeypatch, tmp_path):
    if os.name == "nt":
        pytest.skip("Linux tarball installer uses POSIX symlinks")

    package_root = tmp_path / "Row-Bot-3.21.0-Linux-x86_64"
    (package_root / "bin").mkdir(parents=True)
    (package_root / "app").mkdir()
    (package_root / "python" / "bin").mkdir(parents=True)
    (package_root / "share" / "applications").mkdir(parents=True)
    (package_root / "share" / "icons" / "hicolor" / "256x256" / "apps").mkdir(parents=True)
    (package_root / "bin" / "row-bot").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (package_root / "python" / "bin" / "python3").write_text("", encoding="utf-8")
    (package_root / "share" / "applications" / "ai.row-bot.RowBot.desktop").write_text(
        "[Desktop Entry]\nExec=row-bot\n", encoding="utf-8"
    )
    (package_root / "share" / "icons" / "hicolor" / "256x256" / "apps" / "row-bot.png").write_bytes(b"png")
    (package_root / "install_info.json").write_text(json.dumps({
        "platform": "linux",
        "install_kind": "xdg-user-tarball",
        "version": "3.21.0",
    }), encoding="utf-8")
    archive = tmp_path / "Row-Bot-3.21.0-Linux-x86_64.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(package_root, arcname=package_root.name)

    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(updater.platform, "system", lambda: "Linux")
    monkeypatch.setattr(updater.shutil, "which", lambda _cmd: None)

    launcher_path = updater._install_linux_tarball(archive)

    assert launcher_path == home / ".local" / "bin" / "row-bot"
    assert launcher_path.is_symlink()
    assert (xdg / "row-bot" / "current").is_symlink()
    assert (xdg / "row-bot" / "releases" / "3.21.0" / "install_info.json").exists()
    desktop_text = (xdg / "applications" / "ai.row-bot.RowBot.desktop").read_text(encoding="utf-8")
    assert f"Exec={launcher_path}" in desktop_text


def test_linux_native_baseline_check_blocks_x86_v2_metadata():
    blocked = check_linux_native_baseline._blocked_x86_baselines(["SSE", "SSE2", "X86_V2"])

    assert blocked == ["X86_V2"]


def test_linux_native_baseline_check_allows_legacy_x86_metadata():
    blocked = check_linux_native_baseline._blocked_x86_baselines(["SSE", "SSE2"])

    assert blocked == []


def test_linux_native_baseline_check_blocks_readelf_x86_v3_output():
    output = "Properties: x86 ISA needed: x86-64-baseline, x86-64-v3"

    blocked = check_linux_native_baseline._blocked_readelf_baselines(output)

    assert blocked == ["X86_V3"]


def test_linux_native_baseline_check_allows_readelf_baseline_output():
    output = "Properties: x86 ISA needed: x86-64-baseline"

    blocked = check_linux_native_baseline._blocked_readelf_baselines(output)

    assert blocked == []


def test_linux_root_build_wrapper_delegates_to_installer_script():
    script = Path("build_linux_app.sh").read_text(encoding="utf-8")

    assert "installer/build_linux_app.sh" in script
    assert 'exec "$SCRIPT_DIR/installer/build_linux_app.sh" "$@"' in script


@pytest.mark.slow
def test_linux_launcher_resolves_installed_symlink_chain(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX symlink execution is covered by Linux CI")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is required to execute the generated launcher")

    home = tmp_path / "home"
    release_root = home / ".local" / "share" / "row-bot" / "releases" / "3.21.0"
    bin_home = home / ".local" / "bin"
    app_dir = release_root / "app"
    python_dir = release_root / "python" / "bin"
    (release_root / "bin").mkdir(parents=True)
    app_dir.mkdir(parents=True)
    python_dir.mkdir(parents=True)
    bin_home.mkdir(parents=True)

    launcher = release_root / "bin" / "row-bot"
    launcher.write_text(_linux_launcher_template(), encoding="utf-8")
    launcher.chmod(0o755)
    fake_python = python_dir / "python3"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'cwd=%s\\n' \"$PWD\"\n"
        "printf 'install_root=%s\\n' \"${ROW_BOT_INSTALL_ROOT:-}\"\n"
        "printf 'args=%s\\n' \"$*\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (app_dir / "launcher.py").write_text("# fake launcher\n", encoding="utf-8")

    current = home / ".local" / "share" / "row-bot" / "current"
    current.symlink_to(Path("releases") / "3.21.0", target_is_directory=True)
    user_launcher = bin_home / "row-bot"
    user_launcher.symlink_to(current / "bin" / "row-bot")

    result = subprocess.run(
        [str(user_launcher), "--server", "--no-open"],
        env={**os.environ, "HOME": str(home), "ROW_BOT_DATA_DIR": str(home / ".row-bot")},
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )

    assert f"cwd={app_dir}" in result.stdout
    assert f"install_root={release_root}" in result.stdout
    assert "args=launcher.py --server --no-open" in result.stdout

    default_result = subprocess.run(
        [str(user_launcher)],
        env={**os.environ, "HOME": str(home), "ROW_BOT_DATA_DIR": str(home / ".row-bot")},
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )

    assert "args=launcher.py --browser --no-tray" in default_result.stdout


def _linux_uninstaller() -> str:
    script = Path("installer/build_linux_app.sh").read_text(encoding="utf-8")
    start = script.index("<<'UNINSTALL'\n") + len("<<'UNINSTALL'\n")
    return script[start:script.index("\nUNINSTALL\n", start)] + "\n"


# Never on Windows: bash there can be WSL's, which ignores this env and would act on a real home.
# Kept in the PR lane (well under a second on Linux): it guards the profile on uninstall.
@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="runs the generated POSIX uninstaller")
def test_the_linux_uninstaller_removes_the_app_and_leaves_the_profile(tmp_path):
    home, xdg = tmp_path / "home", tmp_path / "xdg"
    profile = home / ".row-bot"
    profile.mkdir(parents=True)
    (profile / "threads.db").write_text("keep", encoding="utf-8")
    (xdg / "row-bot" / "releases" / "1.0.0").mkdir(parents=True)
    (xdg / "applications").mkdir(parents=True)
    (xdg / "applications" / "ai.row-bot.RowBot.desktop").write_text("x", encoding="utf-8")
    (xdg / "other-app").mkdir()
    (xdg / "other-app" / "data").write_text("keep", encoding="utf-8")
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "row-bot").write_text("x", encoding="utf-8")
    script = tmp_path / "uninstall.sh"
    script.write_text(_linux_uninstaller(), encoding="utf-8")

    result = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=20,
                            env={"HOME": str(home), "XDG_DATA_HOME": str(xdg), "PATH": "/usr/bin:/bin"})

    assert result.returncode == 0, result.stderr
    assert (profile / "threads.db").read_text(encoding="utf-8") == "keep"
    assert (xdg / "other-app" / "data").read_text(encoding="utf-8") == "keep"
    assert not (xdg / "row-bot").exists()
    assert not (home / ".local" / "bin" / "row-bot").exists()
    assert not (xdg / "applications" / "ai.row-bot.RowBot.desktop").exists()


_FAKE_CURL = """#!/usr/bin/env bash
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2;;
    -H|--retry|--connect-timeout) shift 2;;
    -*) shift;;
    *) url="$1"; shift;;
  esac
done
case "$url" in
  https://api.github.com/*) cp "$FIXTURE/release.json" "$out";;
  https://example.invalid/*) cp "$FIXTURE/${url##*/}" "$out";;
  *) exit 22;;
esac
"""


# Kept in the PR lane (well under a second on Linux): it guards the release checksum check.
@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("sha256sum"), reason="runs install-linux.sh")
@pytest.mark.parametrize("digest_ok", [True, False])
def test_the_one_line_installer_installs_only_a_verified_package(tmp_path, digest_ok):
    import hashlib
    import io
    import platform

    arch = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}[platform.machine().lower()]
    name = f"Row-Bot-9.9.9-Linux-{arch}"
    fixture, bin_dir, home = tmp_path / "fixture", tmp_path / "bin", tmp_path / "home"
    for folder in (fixture, bin_dir, home):
        folder.mkdir()
    marker = tmp_path / "installed"
    tarball = fixture / f"{name}.tar.gz"
    with tarfile.open(tarball, "w:gz") as archive:
        data = b'#!/usr/bin/env bash\ntouch "$MARKER"\n'
        info = tarfile.TarInfo(f"{name}/install.sh")
        info.size = len(data)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest() if digest_ok else "0" * 64
    (fixture / "release.json").write_text(json.dumps({
        "tag_name": "v9.9.9",
        "assets": [{"name": tarball.name, "browser_download_url": f"https://example.invalid/{tarball.name}"}],
        "body": f"<!-- row-bot-update-manifest -->\n```manifest\n{tarball.name}: sha256={digest}\n```\n",
    }), encoding="utf-8")
    curl = bin_dir / "curl"
    curl.write_text(_FAKE_CURL, encoding="utf-8")
    curl.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home), "TMPDIR": str(tmp_path),
           "FIXTURE": str(fixture), "MARKER": str(marker)}

    result = subprocess.run(["bash", "installer/install-linux.sh", "9.9.9"], env=env,
                            capture_output=True, text=True, timeout=60)

    assert (result.returncode == 0) is digest_ok, result.stdout + result.stderr
    assert marker.exists() is digest_ok


def test_thread_list_initializes_missing_thread_meta(monkeypatch, tmp_path):
    import sqlite3
    import row_bot.threads as threads

    db_path = tmp_path / "threads.db"
    monkeypatch.setattr(threads, "DB_PATH", str(db_path))

    assert threads._list_threads() == []

    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(thread_meta)").fetchall()}
    finally:
        conn.close()

    assert {"thread_id", "name", "updated_at", "model_override", "project_id"} <= cols


def test_launcher_linux_default_is_direct_browser(monkeypatch):
    called = {}

    def fake_run_direct(args):
        called["browser"] = args.browser
        called["no_tray"] = args.no_tray

    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher, "_run_direct", fake_run_direct)

    launcher.main([])

    assert called == {"browser": True, "no_tray": False}


def test_the_linux_package_smoke_never_touches_a_real_profile():
    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    verify = Path(".github/workflows/installer-verify.yml").read_text(encoding="utf-8")
    manifest = Path(".github/workflows/update-manifest.yml").read_text(encoding="utf-8")
    smoke = Path(".github/actions/smoke-linux-package/action.yml").read_text(encoding="utf-8")

    assert "uses: ./.github/actions/smoke-linux-package" in release
    assert "uses: ./.github/actions/smoke-linux-package" in verify
    # The package installs into a throwaway HOME and XDG data folder.
    assert smoke.index('export HOME="$RUNNER_TEMP/row-bot-linux-home"') < smoke.index('install.sh"')
    assert smoke.index('export XDG_DATA_HOME="$RUNNER_TEMP/row-bot-linux-xdg"') < smoke.index('install.sh"')
    # The update manifest lists checksums for what the release uploads.
    for artifact in ("Row-Bot-*-Linux-*.tar.gz", "Row-Bot-*-Windows-*.exe"):
        assert artifact in release and artifact in manifest


@pytest.mark.slow
def test_release_manifest_script_uses_brand_contract():
    from row_bot.brand import APP_REPOSITORY, UPDATE_MANIFEST_MARKER, UPDATER_USER_AGENT
    from scripts import append_sha_manifest

    block = append_sha_manifest.build_manifest_block({"Row-Bot-4.0.0-Windows-x64.exe": "e" * 64})

    assert f"<!-- {UPDATE_MANIFEST_MARKER} -->" in block
    assert "Row-Bot-4.0.0-Windows-x64.exe: sha256=" + "e" * 64 in block
    assert append_sha_manifest.APP_REPOSITORY == APP_REPOSITORY
    assert append_sha_manifest.UPDATER_USER_AGENT == UPDATER_USER_AGENT

    help_result = subprocess.run(
        [sys.executable, "scripts/append_sha_manifest.py", "--help"],
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    assert "--repo" in help_result.stdout


def test_v4_is_newer_than_latest_v3_for_update_checks():
    assert updater.compare_versions("3.23.1", "4.0.0") > 0
