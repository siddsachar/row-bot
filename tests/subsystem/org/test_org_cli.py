"""``row-bot-org``: check-config's exit codes, serve's wiring, and its JSON logs on stdout."""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from row_bot_org import cli
from row_bot_org.logging import configure_logging


@pytest.fixture(autouse=True)
def org_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("ROW_BOT_ORG_") or name == "ROW_BOT_SECRETS_DIR":
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def root_logging():
    """``serve`` takes over the root logger, as it should in its own process; put pytest's back."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    calls: dict[str, object] = {}

    def run(app, **options):
        calls["app"] = app
        calls.update(options)

    monkeypatch.setattr(uvicorn, "run", run)
    return calls


def test_check_config_exits_0_for_valid_settings(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_PORT", "9000")

    assert cli.main(["check-config"]) == 0
    assert capsys.readouterr().out == (
        "Row-Bot Org settings are valid: host 127.0.0.1, port 9000, log level INFO, OpenAPI off.\n"
    )


def test_check_config_exits_1_and_names_the_bad_setting_only(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_PORT", "marker-value-7f3a")

    assert cli.main(["check-config"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "ROW_BOT_ORG_PORT must be a port number from 1 to 65535" in output.err
    assert "marker-value-7f3a" not in output.err


def test_serve_listens_on_loopback_by_default_and_answers_healthz(served) -> None:
    assert cli.main(["serve"]) == 0

    assert (served["host"], served["port"]) == ("127.0.0.1", 8100)
    assert served["access_log"] is False and served["log_config"] is None and served["server_header"] is False
    response = TestClient(served["app"]).get("/healthz")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


def test_serve_options_override_the_variables(monkeypatch: pytest.MonkeyPatch, served) -> None:
    monkeypatch.setenv("ROW_BOT_ORG_PORT", "9000")

    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "9001"]) == 0
    assert (served["host"], served["port"]) == ("0.0.0.0", 9001)


def test_serve_refuses_to_start_with_bad_settings(served, capsys) -> None:
    assert cli.main(["serve", "--port", "99999"]) == 1

    assert served == {}
    assert "ROW_BOT_ORG_PORT must be a port number from 1 to 65535" in capsys.readouterr().err


def test_logs_are_json_lines() -> None:
    stream = io.StringIO()
    configure_logging("INFO", stream)

    logging.getLogger("row_bot_org.test").info("started on %s", "127.0.0.1")
    logging.getLogger("row_bot_org.test").debug("below the level")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("row_bot_org.test").exception("failed")

    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [(line["level"], line["logger"], line["msg"]) for line in lines] == [
        ("INFO", "row_bot_org.test", "started on 127.0.0.1"),
        ("ERROR", "row_bot_org.test", "failed"),
    ]
    assert "RuntimeError: boom" in lines[1]["exc"]
    assert all(line["ts"].endswith("+00:00") for line in lines)


def _free_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.slow
def test_the_installed_command_serves_healthz_on_loopback(tmp_path: Path) -> None:
    command = shutil.which("row-bot-org", path=str(Path(sys.executable).parent))
    assert command, "uv sync installs the row-bot-org command next to the interpreter"
    port = _free_loopback_port()
    env = {
        **{name: value for name, value in os.environ.items() if not name.startswith("ROW_BOT_ORG_")},
        "ROW_BOT_DATA_DIR": str(tmp_path / "data"),
        "PYTHONUNBUFFERED": "1",
    }
    server = subprocess.Popen([command, "serve", "--port", str(port)], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, encoding="utf-8", env=env)
    try:
        lines = []
        for line in server.stdout:
            lines.append(json.loads(line))
            if lines[-1]["msg"].startswith("Uvicorn running on"):
                break
        assert lines and f"http://127.0.0.1:{port}" in lines[-1]["msg"], lines
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with direct.open(f"http://127.0.0.1:{port}/healthz", timeout=10) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers.get("Server") is None
    finally:
        server.terminate()
        server.wait(timeout=30)
        server.stdout.close()
