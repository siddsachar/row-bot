from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from row_bot.browser import runtime


CONTRACT = runtime.PlaywrightContract("1.62.0", "1234", "151.0.7922.34")


def _browser_path(browsers: Path) -> Path:
    system = runtime._platform_id()
    relative = {
        "windows": "chromium-1234/chrome-win/chrome.exe",
        "darwin": "chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "linux": "chromium-1234/chrome-linux/chrome",
    }[system]
    return browsers / relative


def _successful_runner(commands: list[list[str]]):
    def run(command, *, env, cwd):
        commands.append(list(command))
        executable = _browser_path(Path(env["PLAYWRIGHT_BROWSERS_PATH"]))
        executable.parent.mkdir(parents=True)
        executable.write_text("fake", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "installed", "")
    return run


def test_candidate_records_exact_python_package_and_chromium_revision(tmp_path) -> None:
    commands: list[list[str]] = []
    result = runtime.install_managed_browser_runtime(
        tmp_path,
        contract=CONTRACT,
        runner=_successful_runner(commands),
        smoke_validator=lambda executable, browsers: None,
    )
    assert result.ok
    assert commands == [[sys.executable, "-m", "playwright", "install", "chromium"]]
    manifest = runtime.read_runtime_manifest(tmp_path)
    assert manifest["owner"] == "row-bot-python-playwright"
    assert manifest["package_version"] == "1.62.0"
    assert manifest["chromium_revision"] == "1234"
    assert manifest["chromium_version"] == "151.0.7922.34"
    assert "npx" not in manifest["source"]
    assert runtime.check_managed_browser_runtime(tmp_path, contract=CONTRACT).ready


def test_install_action_puts_chromium_in_the_data_folder_never_a_version_folder(
    tmp_path, monkeypatch, reload_for_data_dir
) -> None:
    # A packaged install bundles no Chromium: the native launchers point Playwright
    # at <data dir>/playwright-browsers, and Settings' Install action (browser.install)
    # is the only thing that downloads one. Version folders are never written.
    data = tmp_path / "data"
    version = tmp_path / "Row-Bot" / "versions" / "5.2.0"
    (version / "python").mkdir(parents=True)
    (requirements,) = reload_for_data_dir(data, "row_bot.mcp_client.config", "row_bot.mcp_client.requirements")[1:]
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(data / "playwright-browsers"))
    commands: list[list[str]] = []
    monkeypatch.setattr(runtime, "installed_playwright_contract", lambda: CONTRACT)
    monkeypatch.setattr(runtime, "_default_runner", _successful_runner(commands))  # the faked download
    monkeypatch.setattr(runtime, "_default_smoke", lambda executable, browsers: None)
    assert runtime.check_managed_browser_runtime().code == "missing"

    result = requirements.install_managed_runtime("playwright-chrome")

    assert result.ok, result.message
    assert commands == [[sys.executable, "-m", "playwright", "install", "chromium"]]
    readiness = runtime.check_managed_browser_runtime()
    assert readiness.ready
    managed = (data / "runtimes" / "playwright-chrome").resolve()
    assert Path(readiness.executable_path).resolve().is_relative_to(managed)
    assert Path(readiness.browsers_dir).resolve().is_relative_to(managed)
    assert not any((version / "python").iterdir())


def test_failed_candidate_preserves_previous_known_good_manifest(tmp_path) -> None:
    commands: list[list[str]] = []
    first = runtime.install_managed_browser_runtime(
        tmp_path,
        contract=CONTRACT,
        runner=_successful_runner(commands),
        smoke_validator=lambda executable, browsers: None,
    )
    before = runtime.manifest_path(tmp_path).read_bytes()
    failed = runtime.install_managed_browser_runtime(
        tmp_path,
        contract=CONTRACT,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "secret", "private path"),
        smoke_validator=lambda executable, browsers: None,
    )
    assert first.ok and not failed.ok
    assert runtime.manifest_path(tmp_path).read_bytes() == before
    assert "secret" not in failed.message and "private path" not in failed.message


def test_mismatch_and_unsafe_paths_fail_read_only_readiness(tmp_path) -> None:
    commands: list[list[str]] = []
    runtime.install_managed_browser_runtime(
        tmp_path,
        contract=CONTRACT,
        runner=_successful_runner(commands),
        smoke_validator=lambda executable, browsers: None,
    )
    assert runtime.check_managed_browser_runtime(
        tmp_path,
        contract=runtime.PlaywrightContract("1.62.1", "1234", "151.0.7922.34"),
    ).code == "runtime_mismatch"
    manifest = runtime.read_runtime_manifest(tmp_path)
    outside = tmp_path.parent / "outside-browser"
    outside.write_text("fake", encoding="utf-8")
    manifest["executable_path"] = str(outside)
    runtime._atomic_json(runtime.manifest_path(tmp_path), manifest)
    assert runtime.check_managed_browser_runtime(tmp_path, contract=CONTRACT).code == "runtime_mismatch"


def test_profile_binding_preserves_same_engine_and_rejects_engine_change(tmp_path) -> None:
    runtime.ensure_profile_engine(tmp_path, "chromium")
    marker = tmp_path / ".row-bot-browser-engine.json"
    before = marker.read_bytes()
    runtime.ensure_profile_engine(tmp_path, "chromium")
    assert marker.read_bytes() == before
    try:
        runtime.ensure_profile_engine(tmp_path, "webkit")
    except RuntimeError as exc:
        assert "different browser engine" in str(exc)
    else:
        raise AssertionError("engine change should fail closed")


def test_installed_contract_matches_locked_playwright_revision() -> None:
    contract = runtime.installed_playwright_contract()
    assert contract.package_version == "1.62.0"
    assert contract.chromium_revision == "1234"
    assert contract.chromium_version == "151.0.7922.34"


def test_packaged_runtime_requires_exact_python_playwright_revision(tmp_path) -> None:
    executable = _browser_path(tmp_path)
    executable.parent.mkdir(parents=True)
    executable.write_text("fake", encoding="utf-8")
    ready = runtime.check_packaged_browser_runtime(
        {"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)}, contract=CONTRACT
    )
    mismatch = runtime.check_packaged_browser_runtime(
        {"PLAYWRIGHT_BROWSERS_PATH": str(tmp_path)},
        contract=runtime.PlaywrightContract("1.62.0", "9999", "other"),
    )
    assert ready.ready
    assert not mismatch.ready and mismatch.code == "runtime_mismatch"


def test_render_launch_options_require_the_reviewed_matching_runtime(monkeypatch) -> None:
    missing = runtime.BrowserRuntimeReadiness(False, "missing", "missing")
    ready = runtime.BrowserRuntimeReadiness(
        True,
        "ready",
        "ready",
        executable_path="C:/synthetic/managed/chrome.exe",
    )
    monkeypatch.setattr(runtime, "check_packaged_browser_runtime", lambda: missing)
    monkeypatch.setattr(runtime, "check_managed_browser_runtime", lambda: ready)

    assert runtime.playwright_chromium_launch_options() == {
        "headless": True,
        "executable_path": "C:/synthetic/managed/chrome.exe",
    }

    monkeypatch.setattr(runtime, "check_managed_browser_runtime", lambda: missing)
    with pytest.raises(RuntimeError, match="Browser Automation"):
        runtime.playwright_chromium_launch_options()


def _shell_path(browsers: Path) -> Path:
    relative = {
        "windows": "chrome-headless-shell-win64/chrome-headless-shell.exe",
        "darwin": "chrome-headless-shell-mac-arm64/chrome-headless-shell",
        "linux": "chrome-headless-shell-linux64/chrome-headless-shell",
    }[runtime._platform_id()]
    return browsers / "chromium_headless_shell-1234" / relative


def test_what_runs_without_a_window_starts_the_headless_shell_never_the_mac_app(tmp_path, monkeypatch) -> None:
    """U30: on a Mac, starting Row-Bot's browser started the "Google Chrome for Testing" app, and macOS asked
    the person to allow that app's notifications. ``playwright install chromium`` installs a headless shell
    beside it, a plain program that never asks: Row-Bot's browser, the install's check and headless exports
    start that; Chromium stays for what needs a window."""
    import types

    def install(command, *, env, cwd):
        browsers = Path(env["PLAYWRIGHT_BROWSERS_PATH"])
        for executable in (_browser_path(browsers), _shell_path(browsers)):
            executable.parent.mkdir(parents=True)
            executable.write_text("fake", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "installed", "")

    started: list[str] = []

    class Playwright:
        def __enter__(self):
            launch = lambda executable_path, headless: started.append(executable_path) or browser  # noqa: E731
            return types.SimpleNamespace(chromium=types.SimpleNamespace(launch=launch))

        def __exit__(self, *exc):
            return False

    page = types.SimpleNamespace(goto=lambda *args, **kwargs: None, title=lambda: "Row-Bot runtime check")
    browser = types.SimpleNamespace(new_page=lambda: page, close=lambda: None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", types.SimpleNamespace(sync_playwright=Playwright))

    assert runtime.install_managed_browser_runtime(tmp_path, contract=CONTRACT, runner=install).ok
    readiness = runtime.check_managed_browser_runtime(tmp_path, contract=CONTRACT)
    shell = str(_shell_path(Path(readiness.browsers_dir)))
    assert started == [shell], "the install's check starts the shell"
    assert runtime.headless_executable(readiness) == shell
    assert readiness.executable_path != shell  # Chromium itself stays recorded, for a window

    monkeypatch.setattr(runtime, "check_packaged_browser_runtime", lambda: readiness)
    assert runtime.playwright_chromium_launch_options(headless=True)["executable_path"] == shell
    assert runtime.playwright_chromium_launch_options(headless=False)["executable_path"] == readiness.executable_path

    from row_bot.browser import service

    monkeypatch.setattr(service, "_installed_channel", lambda: "")
    monkeypatch.setattr(service, "check_packaged_browser_runtime", lambda: readiness)
    assert service.BrowserEngine._attempts(object()) == [{"executable_path": shell}]


def test_a_runtime_without_the_shell_keeps_starting_chromium(tmp_path) -> None:
    runtime.install_managed_browser_runtime(tmp_path, contract=CONTRACT, runner=_successful_runner([]),
                                            smoke_validator=lambda executable, browsers: None)
    assert runtime.headless_executable(runtime.check_managed_browser_runtime(tmp_path, contract=CONTRACT)) == ""
