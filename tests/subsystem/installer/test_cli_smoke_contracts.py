from __future__ import annotations

import sys

import pytest


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]


def test_smoke_app_skips_live_launch_when_port_is_already_in_use(monkeypatch, tmp_path) -> None:
    import scripts.smoke_app as smoke_app

    monkeypatch.setattr(smoke_app, "_port_open", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        smoke_app.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Popen should not be called when port is already open"),
    )

    result = smoke_app.run_app_smoke(cwd=tmp_path, port=8123, timeout=0.1)

    assert result.ok is True
    assert result.messages == [("WARN", "port 8123 already in use; skipping live launch")]


def test_smoke_app_main_parses_command_and_returns_status(monkeypatch, capsys) -> None:
    import scripts.smoke_app as smoke_app

    captured = {}

    def fake_run_app_smoke(**kwargs):
        captured.update(kwargs)
        result = smoke_app.SmokeResult(ok=True, port=kwargs["port"])
        result.add("PASS", "fake smoke")
        return result

    monkeypatch.setattr(smoke_app, "run_app_smoke", fake_run_app_smoke)
    monkeypatch.setattr(
        sys,
        "argv",
        ["smoke_app.py", "--port", "8124", "--timeout", "3", "--cwd", ".", "--no-root-check", "--", "python", "app.py"],
    )

    assert smoke_app.main() == 0
    assert captured["port"] == 8124
    assert captured["timeout"] == 3
    assert captured["check_root"] is False
    assert captured["command"] == ["python", "app.py"]
    assert "[PASS] fake smoke" in capsys.readouterr().out
