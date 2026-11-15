"""Where the setup code lives at each start of ``row-bot serve`` (H1): the console, never at rest."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys

import pytest

from row_bot.access.service import AccessService
from row_bot.api_keys import child_environment
from row_bot.access.setup_code import (
    SETUP_CODE_ENV,
    SetupCodes,
    SetupOutcome,
    prepare_setup_code_at_start,
)
from row_bot.access.store import AccessStore

CODE_LINE = re.compile(r"Row-Bot setup code: ([0-9A-Z-]+)")


@pytest.fixture
def persisted_log(tmp_path):
    """Row-Bot's own log files hang off the root logger; this one stands for them all."""
    path = tmp_path / "row-bot.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    yield path
    root.removeHandler(handler)
    handler.close()


def _printed(capsys) -> list[str]:
    return CODE_LINE.findall(capsys.readouterr().err)


def test_the_code_is_printed_to_the_console_and_never_to_a_log_file_or_the_database(
    tmp_path, capsys, caplog, persisted_log
) -> None:
    store = AccessStore(tmp_path / "mobile.db")
    caplog.set_level(logging.DEBUG)

    prepare_setup_code_at_start({SETUP_CODE_ENV: "hostcode-7Q2M-K9VX"}, store=store)
    host_printed = _printed(capsys)
    prepare_setup_code_at_start({}, store=store)
    generated = _printed(capsys)
    codes = SetupCodes(AccessService(store))
    codes.sign_in("WRNG-WRNG-WRNG", client="198.51.100.7")
    signed_in = codes.sign_in(generated[0], client="198.51.100.7")

    assert host_printed == ["H0ST-C0DE-7Q2M-K9VX"]
    assert len(generated) == 1
    assert signed_in.outcome is SetupOutcome.SIGNED_IN
    persisted = (persisted_log.read_text(encoding="utf-8") + caplog.text).lower()
    stored = b"".join(path.read_bytes() for path in tmp_path.glob("mobile.db*"))
    for code in ("hostcode-7Q2M-K9VX", *host_printed, *generated):
        bare = code.replace("-", "")
        assert code.lower() not in persisted and bare.lower() not in persisted
        for value in (code, bare, bare.lower()):
            assert value.encode() not in stored


def test_no_code_is_printed_once_someone_owns_row_bot_not_even_a_recovery_code(
    tmp_path, capsys
) -> None:
    store = AccessStore(tmp_path / "mobile.db")
    prepare_setup_code_at_start({}, store=store)
    codes = SetupCodes(AccessService(store))
    assert codes.sign_in(_printed(capsys)[0], client="198.51.100.7").outcome is SetupOutcome.SIGNED_IN

    prepare_setup_code_at_start({}, store=store)
    prepare_setup_code_at_start({SETUP_CODE_ENV: "RECO-VERY-C0DE"}, store=store)

    assert _printed(capsys) == []
    recovered = codes.sign_in("RECO-VERY-C0DE", client="203.0.113.9")
    assert recovered.outcome is SetupOutcome.SIGNED_IN
    assert recovered.claim is not None and recovered.claim.recovered is True


def test_each_start_makes_a_new_code_and_the_last_one_stops_working(tmp_path, capsys) -> None:
    store = AccessStore(tmp_path / "mobile.db")
    prepare_setup_code_at_start({}, store=store)
    earlier = _printed(capsys)[0]
    prepare_setup_code_at_start({}, store=store)
    latest = _printed(capsys)[0]
    codes = SetupCodes(AccessService(store))

    assert earlier != latest
    assert codes.sign_in(earlier, client="198.51.100.7").outcome is SetupOutcome.WRONG
    assert codes.sign_in(latest, client="198.51.100.7").outcome is SetupOutcome.SIGNED_IN


def test_a_host_code_too_short_to_be_safe_is_ignored_without_logging_it(
    tmp_path, capsys, caplog
) -> None:
    store = AccessStore(tmp_path / "mobile.db")
    caplog.set_level(logging.DEBUG)

    prepare_setup_code_at_start({SETUP_CODE_ENV: "letmein"}, store=store)

    # Row-Bot made a code of its own instead, so a new pod is never left without one.
    assert len(_printed(capsys)) == 1
    assert "ROW_BOT_SETUP_CODE needs 12 to 64 letters or digits" in caplog.text
    assert "letmein" not in caplog.text.lower()
    codes = SetupCodes(AccessService(store))
    assert codes.sign_in("letmein", client="198.51.100.7").outcome is SetupOutcome.WRONG


def test_the_hosts_code_never_reaches_a_program_row_bot_starts(
    tmp_path, monkeypatch, capsys
) -> None:
    # Security-sensitive (H1): this stays in the pull-request lane although it starts processes.
    monkeypatch.setenv(SETUP_CODE_ENV, "ABCD-EFGH-JKMN")
    probe = [sys.executable, "-c", f"import os; print(os.environ.get({SETUP_CODE_ENV!r}, 'absent'))"]

    # A shell, Developer Studio command or MCP server started for the agent, even before the start.
    for_the_agent = child_environment()
    agent_child = subprocess.run(
        probe, env=for_the_agent, capture_output=True, text=True, check=True, timeout=60
    )
    prepare_setup_code_at_start(store=AccessStore(tmp_path / "mobile.db"))
    # Any program started the plain way after `row-bot serve` took the code.
    plain_child = subprocess.run(probe, capture_output=True, text=True, check=True, timeout=60)

    assert SETUP_CODE_ENV not in for_the_agent
    assert SETUP_CODE_ENV not in os.environ
    assert agent_child.stdout.strip() == plain_child.stdout.strip() == "absent"
    assert _printed(capsys) == ["ABCD-EFGH-JKMN"]
