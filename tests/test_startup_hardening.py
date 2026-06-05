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
    assert "skipping Windows console splash fallback" in launcher_src
    assert "ROW_BOT_BATCH_START_OLLAMA" in batch_src
    assert 'goto :launch_app' in batch_src
