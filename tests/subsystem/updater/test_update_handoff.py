"""The Windows update handoff ends only its own processes, then starts the installer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from row_bot import update_handoff as handoff


pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def seams(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(handoff, "os", SimpleNamespace(name="nt"))  # the Windows taskkill branch on every OS
    monkeypatch.setattr(handoff, "_wait_for_port_stop", lambda port, timeout: True)
    monkeypatch.setattr(handoff, "_port_responds", lambda port: False)
    runs, launches = [], []
    monkeypatch.setattr(handoff.subprocess, "run", lambda argv, **kwargs: runs.append(argv))
    monkeypatch.setattr(handoff.subprocess, "Popen", lambda argv, **kwargs: launches.append(argv))
    installer = tmp_path / "Row-Bot-9.9.9-Windows-x64.exe"
    installer.write_bytes(b"installer")
    return SimpleNamespace(runs=runs, launches=launches, installer=installer,
                           log=tmp_path / "data" / "update-handoff.log")


def test_a_hung_app_is_ended_by_pid_only_then_the_installer_starts(seams, monkeypatch) -> None:
    monkeypatch.setattr(handoff, "_wait_for_pid", lambda pid, timeout: False)

    assert handoff.run_handoff(seams.installer, app_pid=111, launcher_pid=222, port=8080, timeout=0) == 0

    assert seams.runs == [["taskkill", "/PID", "111", "/T", "/F"], ["taskkill", "/PID", "222", "/T", "/F"]]
    assert seams.launches == [[str(seams.installer), "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"]]
    assert "handoff complete" in seams.log.read_text(encoding="utf-8")


def test_an_app_that_exits_is_never_killed(seams, monkeypatch) -> None:
    monkeypatch.setattr(handoff, "_wait_for_pid", lambda pid, timeout: True)

    assert handoff.run_handoff(seams.installer, app_pid=111, launcher_pid=222, port=8080, timeout=0) == 0

    assert seams.runs == []
    assert len(seams.launches) == 1


def test_a_missing_installer_starts_nothing(seams, monkeypatch) -> None:
    monkeypatch.setattr(handoff, "_wait_for_pid", lambda pid, timeout: pytest.fail("no wait without an installer"))

    missing = seams.installer.with_name("gone.exe")
    assert handoff.run_handoff(missing, app_pid=111, launcher_pid=222, port=0, timeout=0) == 2

    assert seams.runs == []
    assert seams.launches == []
    assert f"installer missing: {missing}" in seams.log.read_text(encoding="utf-8")
