from __future__ import annotations

from types import SimpleNamespace

from row_bot.computer_use import readiness as readiness_module
from row_bot.computer_use.readiness import ReadinessCode, acknowledge_disclosure, cancel_disclosure, configure_system_cua, disclosure_acknowledged, readiness, verify_system_cua
from row_bot.mcp_client import requirements


def test_disclosure_acknowledgement_is_local_and_versioned(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    cancel_disclosure()
    assert disclosure_acknowledged() is False
    assert readiness(enabled=True).code is ReadinessCode.DISCLOSURE_REQUIRED
    acknowledge_disclosure()
    assert disclosure_acknowledged() is True


def test_linux_is_unavailable_without_import_or_process_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    acknowledge_disclosure()
    monkeypatch.setattr(readiness_module.platform, "system", lambda: "Linux")
    assert readiness(enabled=True).code is ReadinessCode.UNSUPPORTED


def test_system_override_version_check_cannot_run_before_disclosure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    binary = tmp_path / "cua-driver.exe"
    binary.write_bytes(b"fake")
    configure_system_cua(str(binary), enabled=True)
    cancel_disclosure()
    called = []
    monkeypatch.setattr(readiness_module.subprocess, "run", lambda *_args, **_kwargs: called.append(True))
    try:
        verify_system_cua()
    except PermissionError:
        pass
    else:
        raise AssertionError("disclosure was not enforced")
    assert called == []


def test_verified_system_override_must_match_exact_reviewed_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(readiness_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(readiness_module.platform, "machine", lambda: "AMD64")
    binary = tmp_path / "cua-driver.exe"
    binary.write_bytes(b"fake")
    configure_system_cua(str(binary), enabled=True)
    acknowledge_disclosure()
    class _Completed:
        returncode = 0
        stdout = "cua-driver 0.7.1"
        stderr = ""
    monkeypatch.setattr(readiness_module.subprocess, "run", lambda *_args, **_kwargs: _Completed())
    assert verify_system_cua().code is ReadinessCode.READY
    assert readiness(enabled=True).code is ReadinessCode.DEGRADED


def test_failed_managed_doctor_rolls_back_to_retained_known_good(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(readiness_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(readiness_module.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(requirements, "RUNTIMES_DIR", tmp_path / "runtimes")
    acknowledge_disclosure()
    asset = readiness_module.selected_asset()
    assert asset is not None
    runtime_root = requirements.RUNTIMES_DIR / "cua-driver"
    old_root = runtime_root / "0.7.0"
    new_root = runtime_root / "0.7.1"
    old_root.mkdir(parents=True)
    new_root.mkdir()
    old_executable = old_root / "cua-driver.exe"
    new_executable = new_root / "cua-driver.exe"
    old_executable.write_bytes(b"old")
    new_executable.write_bytes(b"new")
    requirements._write_manifest("cua-driver", {
        "installed": True,
        "version": "0.7.1",
        "archive_sha256": asset["sha256"],
        "root": str(new_root),
        "executable_path": str(new_executable),
        "previous_manifest": {
            "installed": True,
            "version": "0.7.0",
            "archive_sha256": "old",
            "root": str(old_root),
            "executable_path": str(old_executable),
            "doctor_ok": True,
        },
    })

    class _FailingDoctor:
        def __init__(self, _executable):
            pass

        def start(self):
            return None

        def call_internal(self, _name):
            return SimpleNamespace(structured={"schema_version": "1", "overall": "failed", "checks": []})

        def close(self):
            return None

    import row_bot.computer_use.client as client_module

    monkeypatch.setattr(client_module, "CuaClient", _FailingDoctor)
    result = readiness_module.run_cua_diagnostics()
    assert result.code is ReadinessCode.FAILED
    assert requirements._read_manifest("cua-driver")["version"] == "0.7.0"
    assert old_root.exists()
    assert not new_root.exists()
