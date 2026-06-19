import importlib
import logging
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import builtins
from types import SimpleNamespace

import row_bot.launcher as launcher
import row_bot.startup_diagnostics as startup_diagnostics


def test_preflight_reports_broken_torchcodec(monkeypatch, caplog):
    def fake_find_spec(package):
        return object() if package == "torchcodec" else None

    def fake_import_module(package):
        if package == "torchcodec":
            raise OSError("libtorchcodec_core4.dll could not load")
        raise AssertionError(package)

    patched = []

    monkeypatch.setattr(startup_diagnostics.importlib.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(startup_diagnostics.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(startup_diagnostics, "_disable_transformers_torchcodec", lambda log: patched.append(log))

    with caplog.at_level(logging.WARNING):
        issues = startup_diagnostics.preflight_optional_native_packages()

    assert len(issues) == 1
    assert issues[0].package == "torchcodec"
    assert "libtorchcodec_core4.dll" in issues[0].error
    assert "pip uninstall -y torchcodec" in issues[0].recovery_hint
    assert patched
    assert "Optional package 'torchcodec' is installed but cannot be imported" in caplog.text


def test_launcher_hints_for_torchcodec_dll_failure():
    hints = launcher._startup_failure_hints(
        "OSError: Could not load this library: E:\\Row-Bot\\Row-Bot\\python\\Lib\\site-packages\\torchcodec\\libtorchcodec_core4.dll",
        python_executable="E:\\Row-Bot\\Row-Bot\\python\\python.exe",
    )

    assert any("broken optional TorchCodec" in hint for hint in hints)
    assert any('"E:\\Row-Bot\\Row-Bot\\python\\python.exe" -m pip uninstall -y torchcodec' in hint for hint in hints)


def test_launcher_hints_for_linux_opencv_native_failure():
    hints = launcher._startup_failure_hints(
        "ImportError: libGL.so.1: cannot open shared object file while importing cv2"
    )

    assert any("OpenCV/Linux native dependency" in hint for hint in hints)
    assert any("libgl1" in hint for hint in hints)


def test_launcher_hints_for_numpy_x86_v2_failure():
    hints = launcher._startup_failure_hints(
        "RuntimeError: NumPy was built with baseline optimizations: "
        "(X86_V2) but your machine doesn't support: (X86_V2)."
    )

    assert any("NumPy/native wheel startup failure" in hint for hint in hints)
    assert any("x86-64-v2" in hint for hint in hints)


def test_launcher_logs_app_tail_on_startup_failure(tmp_path, caplog):
    log_path = tmp_path / "row_bot_app.log"
    log_path.write_text("line one\nTraceback\nImportError: libGL.so.1 missing\n", encoding="utf-8")
    server = launcher._RowBotProcess(port=8123)
    server._log_file = log_path
    server._proc = None

    with caplog.at_level(logging.ERROR):
        launcher._log_startup_failure_context(server, 8123, "app process exited before readiness")

    assert "failed to become ready on port 8123" in caplog.text
    assert "ImportError: libGL.so.1 missing" in caplog.text
    assert "OpenCV/Linux native dependency" in caplog.text


def test_startup_timeout_env(monkeypatch):
    monkeypatch.setenv("ROW_BOT_STARTUP_TIMEOUT", "180")
    assert launcher._startup_timeout() == 180

    monkeypatch.setenv("ROW_BOT_STARTUP_TIMEOUT", "not-a-number")
    assert launcher._startup_timeout() == 120


def test_preflight_handles_real_broken_optional_package_subprocess(tmp_path):
    package_dir = tmp_path / "torchcodec"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text(
        'raise OSError("Could not load this library: libtorchcodec_core4.dll")\n',
        encoding="utf-8",
    )

    code = textwrap.dedent(
        """
        import logging
        import row_bot.startup_diagnostics as startup_diagnostics

        logging.basicConfig(level=logging.WARNING, format="%(message)s")
        issues = startup_diagnostics.preflight_optional_native_packages()
        print(len(issues))
        print(issues[0].package)
        print(issues[0].recovery_hint)
        """
    )
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(tmp_path), str(root / "src"), str(root), env.get("PYTHONPATH", "")) if part
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "1" in result.stdout
    assert "torchcodec" in result.stdout
    assert "pip uninstall -y torchcodec" in result.stdout
    assert "Optional package 'torchcodec' is installed but cannot be imported" in result.stderr


def test_app_imports_with_startup_preflight():
    app_module = importlib.import_module("row_bot.app")

    assert hasattr(app_module, "_APP_PORT")


def test_app_import_survives_broken_cv2_module(tmp_path):
    fake_cv2 = tmp_path / "cv2.py"
    fake_cv2.write_text('raise OSError("libGL.so.1: cannot open shared object file")\n', encoding="utf-8")
    code = "import row_bot.app; print('app-import-ok')"
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(tmp_path), str(root / "src"), str(root), env.get("PYTHONPATH", "")) if part
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "app-import-ok" in result.stdout


def test_vision_degrades_when_cv2_native_import_fails(monkeypatch):
    import row_bot.vision as vision

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "cv2":
            raise OSError("libGL.so.1: cannot open shared object file")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(vision, "_cv2_mod", None)
    monkeypatch.setattr(vision, "_cv2_error", None)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    svc = vision.VisionService()

    assert svc.model
    assert vision.list_cameras() == []
    assert vision.capture_frame() is None
    status = vision.native_backend_status()
    assert status["opencv_available"] is False
    assert "libGL.so.1" in str(status["opencv_error"])


def test_windows_installer_replaces_embedded_python_on_install():
    iss = Path("installer/row_bot_setup.iss").read_text(encoding="utf-8")

    assert "[InstallDelete]" in iss
    assert 'Type: filesandordirs; Name: "{app}\\python"' in iss
    assert 'Source: "..\\src\\row_bot\\*"' in iss
    assert Path("src/row_bot/startup_diagnostics.py").is_file()


def test_windows_installer_build_verifies_tk_runtime():
    build_script = Path("installer/build_installer.ps1").read_text(encoding="utf-8")
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert 'WINDOWS_PYTHON_VERSION: "3.13.2"' in release_workflow
    assert 'python-version: ${{ env.WINDOWS_PYTHON_VERSION }}' in release_workflow
    assert '-PythonVersion "${{ env.WINDOWS_PYTHON_VERSION }}"' in release_workflow
    assert '$SysPyVersion -ne $PythonVersion' in build_script
    assert 'Resolve-TkSourceFile "_tkinter.pyd"' in build_script
    assert 'Resolve-TkSourceFile "zlib1.dll"' in build_script
    assert '$env:PATH = (Join-Path $PythonDir "Scripts") + ";" + $PythonDir + ";" + $env:PATH' in build_script
    assert 'os.add_dll_directory(py_dir)' in build_script
    assert 'import _tkinter' in build_script
    assert 'import tkinter' in build_script
    assert 'Embedded tkinter verified' in build_script


def test_windows_update_install_starts_handoff_before_quit(tmp_path, monkeypatch):
    import row_bot.updater as updater

    installer = tmp_path / "Row-Bot-test.exe"
    installer.write_bytes(b"installer")
    calls = []

    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    monkeypatch.setattr(updater, "verify_os_signature", lambda _path: (True, "ok"))
    monkeypatch.setattr(updater, "_launch_windows_update_handoff", lambda _path: calls.append("handoff"))
    monkeypatch.setitem(
        sys.modules,
        "row_bot.launcher",
        SimpleNamespace(quit_for_update=lambda: calls.append("quit")),
    )

    updater.install_and_restart(installer)

    assert calls == ["handoff", "quit"]


def test_windows_update_handoff_helper_command_is_detached(tmp_path, monkeypatch):
    import row_bot.updater as updater

    installer = tmp_path / "Row-Bot-test.exe"
    installer.write_bytes(b"installer")
    calls = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            calls.append((cmd, kwargs))

    monkeypatch.setenv("ROW_BOT_PORT", "8123")
    monkeypatch.setattr(updater.subprocess, "Popen", FakePopen)

    updater._launch_windows_update_handoff(installer)

    cmd, kwargs = calls[0]
    assert cmd[:3] == [sys.executable, "-m", "row_bot.update_handoff"]
    assert "--installer" in cmd
    assert "--app-pid" in cmd
    assert "--launcher-pid" in cmd
    assert "--port" in cmd and "8123" in cmd
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_splash_helper_immediate_failure_returns_quickly(tmp_path, monkeypatch, caplog):
    class FakePopen:
        pid = 101

        def __init__(self, cmd, **kwargs):  # noqa: ARG002
            kwargs["stdout"].write("ImportError: DLL load failed while importing _tkinter\n")

        def poll(self):
            return 1

    monkeypatch.setattr(launcher, "_row_bot_data_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.delenv("ROW_BOT_SPLASH_CONSOLE_FALLBACK", raising=False)
    monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)

    with caplog.at_level(logging.WARNING):
        assert launcher._show_splash(port=8123, timeout=1.0) is None

    assert "splash_tk helper exited before ready" in caplog.text
    assert "DLL load failed" in caplog.text


def test_window_mode_picker_windows_defaults_without_console(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "_row_bot_data_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.delenv("ROW_BOT_WINDOW_MODE_CONSOLE_FALLBACK", raising=False)
    monkeypatch.setattr(launcher, "_start_launcher_helper", lambda **kwargs: None)
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("console fallback should not start")),
    )
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("console fallback should not run")),
    )

    assert launcher._ask_window_mode() == "native"


def test_window_mode_picker_uses_gui_result(tmp_path, monkeypatch):
    class FakeProc:
        pid = 202

        def wait(self, timeout=None):  # noqa: ARG002
            return 0

        def poll(self):
            return None

    def fake_start_helper(**kwargs):
        Path(kwargs["args"][3]).write_text("browser\n", encoding="utf-8")
        Path(kwargs["ready_marker"]).write_text("ready\n", encoding="utf-8")
        return FakeProc()

    monkeypatch.setattr(launcher, "_row_bot_data_dir", lambda: tmp_path)
    monkeypatch.setattr(launcher, "_start_launcher_helper", fake_start_helper)

    assert launcher._ask_window_mode() == "browser"


def test_main_requests_early_splash_before_migration(monkeypatch):
    calls = []

    class FakeSplashProc:
        def poll(self):
            return None

    class FakeTray:
        def __init__(self, **kwargs):
            calls.append(("tray_init", kwargs))

        def run(self):
            calls.append(("tray_run", launcher._claim_early_splash() is not None))

    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setattr(launcher, "_show_splash", lambda port: calls.append(("splash", port)) or FakeSplashProc())
    monkeypatch.setattr(launcher, "_ensure_rebrand_migration", lambda: calls.append(("migration", None)))
    monkeypatch.setattr(launcher, "_has_display_server", lambda: True)
    monkeypatch.setattr(launcher, "RowBotTray", FakeTray)
    monkeypatch.setattr(launcher, "_ACTIVE_TRAY", None)
    monkeypatch.setattr(launcher, "_EARLY_SPLASH_PROC", None)

    launcher.main(["--no-ollama"])

    assert calls[0][0] == "splash"
    assert calls[1][0] == "migration"
    assert ("tray_run", True) in calls


def test_macos_tray_run_loop_starts_before_launcher_startup(monkeypatch):
    events = []

    class FakeMenu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    class FakeMenuItem:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class FakeIcon:
        def __init__(self, **kwargs):
            events.append("icon_init")
            self.kwargs = kwargs

        def run(self, setup=None):
            events.append("icon_run")
            assert setup is not None
            setup(self)

        def stop(self):
            events.append("icon_stop")

    fake_pystray = SimpleNamespace(Menu=FakeMenu, MenuItem=FakeMenuItem, Icon=FakeIcon)
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "_get_icon", lambda state: object())
    monkeypatch.setattr(launcher, "_launch_event", lambda *args, **kwargs: None)

    tray = launcher.RowBotTray(no_ollama=True)
    monkeypatch.setattr(tray, "_run_startup_sequence", lambda: events.append("startup"))

    tray.run()

    assert events[:2] == ["icon_init", "icon_run"]
    assert events.index("icon_run") < events.index("startup")


def test_update_handoff_helper_is_targeted_and_logged():
    source = Path("src/row_bot/update_handoff.py").read_text(encoding="utf-8")

    assert "update-handoff.log" in source
    assert '["taskkill", "/PID", str(pid), "/T", "/F"]' in source
    assert "/SILENT" in source
    assert "/CLOSEAPPLICATIONS" in source
    assert "/RESTARTAPPLICATIONS" in source
    assert "row_bot" not in source.lower().split("taskkill", 1)[1].split("]", 1)[0]


def test_launcher_splash_and_batch_startup_are_hardened():
    launcher_src = Path("src/row_bot/launcher.py").read_text(encoding="utf-8")
    batch_src = Path("installer/launch_row_bot.bat").read_text(encoding="utf-8")

    assert "launcher.log" in launcher_src
    assert "ROW_BOT_LAUNCH_TRACE" in launcher_src
    assert "splash_tk_exited" in launcher_src
    assert "ROW_BOT_SPLASH_CONSOLE_FALLBACK" in launcher_src
    assert "ROW_BOT_WINDOW_MODE_CONSOLE_FALLBACK" in launcher_src
    assert "Skipping Windows console window mode fallback" in launcher_src
    assert "helper_ready_timeout" in launcher_src
    assert "early_splash_requested" in launcher_src
    assert "skipping Windows console splash fallback" in launcher_src
    assert "ROW_BOT_BATCH_START_OLLAMA" in batch_src
    assert 'goto :launch_app' in batch_src
