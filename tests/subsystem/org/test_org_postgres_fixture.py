"""The ``postgres`` fixture waits out a cold WSL start (F-15) instead of failing every Org test with it.

No Postgres and no WSL here: ``subprocess.run`` answers as a cold ``wsl.exe`` would.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.fixtures import postgres as fixture


@pytest.fixture
def wsl(monkeypatch: pytest.MonkeyPatch):
    """Answers each ``wsl.exe ... --exec <command>`` from ``replies`` in turn; records the commands."""
    commands: list[str] = []
    replies: list[object] = []

    def run(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv[4])
        reply = replies.pop(0) if replies else 1
        if reply == "hang":
            raise subprocess.TimeoutExpired(argv, options["timeout"])
        return subprocess.CompletedProcess(argv, reply, "", "WSL is starting")

    monkeypatch.setattr(fixture.subprocess, "run", run)
    monkeypatch.setattr(fixture.sys, "platform", "win32")
    monkeypatch.setattr(fixture, "_WSL_RETRY_PAUSE", 0.0)
    return commands, replies


def test_a_cold_wsl_distro_is_waited_for_before_postgres_is_looked_for(wsl) -> None:
    commands, replies = wsl
    replies.extend(["hang", 1, 0, 0])

    programs = fixture.find_postgres({fixture.PG_WSL_DISTRO_ENV: "Test-Distro"})

    assert programs.distro == "Test-Distro"
    assert commands == ["true", "true", "true", "test"]


def test_a_wsl_distro_that_never_starts_is_reported_once_the_wait_is_over(wsl, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fixture, "_WSL_START_TIMEOUT", 0.2)

    with pytest.raises(fixture.PostgresUnavailable, match="didn't start within 0.2 s"):
        fixture.find_postgres({fixture.PG_WSL_DISTRO_ENV: "Test-Distro"})
    assert "test" not in wsl[0]


def test_a_started_distro_without_postgres_is_reported_at_once(wsl) -> None:
    commands, replies = wsl
    replies.extend([0, 1])

    with pytest.raises(fixture.PostgresUnavailable, match="has no Postgres"):
        fixture.find_postgres({fixture.PG_WSL_DISTRO_ENV: "Test-Distro"})
    assert commands == ["true", "test"]
