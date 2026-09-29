"""Actual application admissions settle a proven failed OS start."""
from __future__ import annotations

import threading

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform, submit  # noqa: F401
from tests.helpers.client_platform_fakes import ScriptedAgentStream


def _failed_start(_worker):
    raise RuntimeError("cannot start new thread")


def test_headless_failed_start_settles_durable_admission_and_exact_retry(platform, monkeypatch):
    from row_bot.application.client_platform import ClientPlatformError
    fake = ScriptedAgentStream((("done", "Must not dispatch"),))
    monkeypatch.setattr(threading.Thread, "start", _failed_start)
    for _ in range(2):
        with pytest.raises(ClientPlatformError, match="generation_failed"):
            submit(platform, fake, "failed-start")
    assert fake.calls == []
    assert not platform.registry.active()
    assert platform.snapshot("conversation-a")["generation"]["status"] == "interrupted"
    from row_bot.runtime import admissions
    with admissions.transaction() as connection:
        rows = connection.execute("SELECT state FROM generation_passes WHERE conversation_id='conversation-a'").fetchall()
    assert len(rows) == 1 and rows[0][0] == "interrupted"
