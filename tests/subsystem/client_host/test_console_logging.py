"""Row-Bot's console in its own image (F2): INFO and below on stdout, WARNING and above on stderr.

A host shows the container's output as its log, and Railway colours every stderr line as an error.
Only the server, ``row-bot serve``, splits it that way: every other command (``row-bot access doctor
--json``) keeps stdout for its own output. Outside the image everything stays on stderr.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys

import pytest

from row_bot.access.setup_code import prepare_setup_code_at_start
from row_bot.access.store import AccessStore
from row_bot.console_logging import console_handlers, route_console_by_level

pytestmark = pytest.mark.subsystem

EVERY_LEVEL = [
    "DEBUG: checking the volume",
    "INFO: Row-Bot is running at http://127.0.0.1:8080",
    "WARNING: the secret-store key is on the volume",
    "ERROR: a channel didn't start",
    "CRITICAL: Row-Bot didn't start",
]


def _say_each_level(environ: dict[str, str]) -> None:
    console = logging.getLogger("row_bot.tests.console")
    console.propagate = False
    console.setLevel(logging.DEBUG)
    handlers = console_handlers(environ)
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        console.addHandler(handler)
    try:
        for line in EVERY_LEVEL:
            level, message = line.split(": ", 1)
            console.log(logging.getLevelName(level), message)
    finally:
        for handler in handlers:
            console.removeHandler(handler)


def test_in_row_bots_image_info_and_below_go_to_stdout_and_warnings_to_stderr(capsys) -> None:
    _say_each_level({"ROW_BOT_CONTAINERIZED": "1"})

    out, err = capsys.readouterr()
    assert out.splitlines() == EVERY_LEVEL[:2]
    assert err.splitlines() == EVERY_LEVEL[2:]


@pytest.mark.parametrize("environ", [{}, {"ROW_BOT_CONTAINERIZED": "0"}], ids=["desktop", "not-the-image"])
def test_outside_the_image_everything_stays_on_stderr(capsys, environ) -> None:
    _say_each_level(environ)

    out, err = capsys.readouterr()
    assert out == ""
    assert err.splitlines() == EVERY_LEVEL


def _row_bot_in_the_image(tmp_path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "ROW_BOT_CONTAINERIZED": "1",
        "ROW_BOT_DEPLOYMENT_MODE": "server",
        "ROW_BOT_DATA_DIR": str(tmp_path / "data"),
        "ROW_BOT_INSTALL_COUNT": "0",
        "ROW_BOT_TEST_MODE": "1",
    }
    return subprocess.run([sys.executable, "-c", script, *args], env=environment,
                          capture_output=True, text=True, timeout=120)


def test_in_the_image_a_json_command_keeps_stdout_to_itself(tmp_path) -> None:
    result = _row_bot_in_the_image(
        tmp_path, "import sys; from row_bot.launcher import main; main(sys.argv[1:])", "access", "doctor", "--json"
    )

    assert result.stdout.startswith("{"), result.stdout + result.stderr
    assert json.loads(result.stdout)["checks"]
    assert "INFO: launcher.process_start" in result.stderr


# `row-bot serve` up to the point it would hold the data folder's lock and start the server.
_SERVE_UNTIL_THE_LOCK = """
from row_bot import launcher
def stop():
    raise SystemExit(0)
launcher._claim_instance = stop
launcher.main(["serve"])
"""


def test_in_the_image_serve_sends_info_to_stdout(tmp_path) -> None:
    result = _row_bot_in_the_image(tmp_path, _SERVE_UNTIL_THE_LOCK)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "INFO: Row-Bot server mode listening on http://127.0.0.1:8080" in result.stdout.splitlines()
    assert "server mode listening" not in result.stderr


@pytest.mark.parametrize("environ", [{}, {"ROW_BOT_CONTAINERIZED": "1"}], ids=["desktop", "image"])
def test_the_switch_leaves_log_files_alone_and_the_desktop_on_stderr(tmp_path, capsys, environ) -> None:
    server = logging.getLogger("row_bot.tests.serve")
    server.propagate = False
    server.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    log_file = logging.FileHandler(tmp_path / "launcher.log", encoding="utf-8")
    log_file.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    server.addHandler(console)
    server.addHandler(log_file)
    try:
        route_console_by_level(server, environ)
        for line in EVERY_LEVEL:
            level, message = line.split(": ", 1)
            server.log(logging.getLevelName(level), message)
    finally:
        for handler in server.handlers[:]:
            server.removeHandler(handler)
            handler.close()

    out, err = capsys.readouterr()
    assert (out.splitlines(), err.splitlines()) == (
        (EVERY_LEVEL[:2], EVERY_LEVEL[2:]) if environ else ([], EVERY_LEVEL)
    )
    assert (tmp_path / "launcher.log").read_text(encoding="utf-8").splitlines() == EVERY_LEVEL


def test_in_the_image_the_setup_code_is_on_stdout_too(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_CONTAINERIZED", "1")

    prepare_setup_code_at_start({}, store=AccessStore(tmp_path / "mobile.db"))

    out, err = capsys.readouterr()
    assert re.search(r"^Row-Bot setup code: [0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4} ", out, re.MULTILINE)
    assert "setup code" not in err
