"""Row-Bot's console in its own image (F2): INFO and below on stdout, WARNING and above on stderr.

A host shows the container's output as its log, and Railway colours every stderr line as an error.
Outside the image (the desktop, ``row-bot serve`` on a computer) everything stays on stderr.
"""

from __future__ import annotations

import logging
import re

import pytest

from row_bot.access.setup_code import prepare_setup_code_at_start
from row_bot.access.store import AccessStore
from row_bot.console_logging import console_handlers

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


def test_in_the_image_the_setup_code_is_on_stdout_too(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_CONTAINERIZED", "1")

    prepare_setup_code_at_start({}, store=AccessStore(tmp_path / "mobile.db"))

    out, err = capsys.readouterr()
    assert re.search(r"^Row-Bot setup code: [0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4} ", out, re.MULTILINE)
    assert "setup code" not in err
