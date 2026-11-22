"""A delete with Undo takes effect at once on the server, and Undo restores it (B343)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform as platform, submit
from tests.helpers.client_platform_fakes import ScriptedAgentStream, fixture_id


@pytest.fixture
def timers(monkeypatch):
    from row_bot import thread_cleanup

    started: list[float] = []
    monkeypatch.setattr(thread_cleanup, "_start_purge_timer", started.append)
    return started


def _run(service, kind: str, label: str, payload: dict | None = None) -> dict:
    return service.execute(owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"),
                           command=command(kind, label, payload), target="conversation-a")


def _listed(service) -> list[str]:
    return [item["id"] for item in service.list_conversations()["items"]]


def _later() -> datetime:
    from row_bot.thread_cleanup import UNDO_DELETE_SECONDS

    return datetime.now(timezone.utc) + timedelta(seconds=UNDO_DELETE_SECONDS + 5)


def test_a_held_delete_leaves_the_lists_at_once_and_takes_no_turns(platform, timers):
    from row_bot.application.client_platform import ClientPlatformError

    receipt = _run(platform, "conversation.delete", "delete", {"undo": True})
    assert receipt["status"] == "DeleteCompleted"
    # A page that closes now has nothing left to send: the delete has happened.
    assert "conversation-a" not in _listed(platform)
    assert "conversation-b" in _listed(platform)
    assert timers, "the removal is scheduled for when Undo ends"
    blocked = ScriptedAgentStream((("done", "Must never dispatch"),))
    with pytest.raises(ClientPlatformError, match="conversation_deleting"):
        submit(platform, blocked, "after-delete")
    assert not blocked.calls


def test_undo_inside_the_window_brings_the_conversation_back_as_it_was(platform, timers):
    from row_bot.runtime import admissions
    from row_bot.thread_cleanup import finish_held_deletions

    _run(platform, "conversation.delete", "delete", {"undo": True})
    assert _run(platform, "conversation.restore", "restore")["status"] == "completed"
    assert "conversation-a" in _listed(platform)
    # Nothing is removed when the window would have ended.
    assert finish_held_deletions(now=_later()) == 0
    assert "conversation-a" in _listed(platform)
    # It takes turns again.
    assert admissions.deletion_state("conversation-a") == "active"


def test_the_conversation_is_removed_when_the_window_ends_and_undo_is_then_too_late(platform, timers):
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.thread_cleanup import finish_held_deletions

    _run(platform, "conversation.delete", "delete", {"undo": True})
    # Not before the window ends.
    assert finish_held_deletions() == 0
    assert finish_held_deletions(now=_later()) == 1
    with pytest.raises(ClientPlatformError) as missing:
        platform.get_conversation("conversation-a")
    assert missing.value.code == "not_found"
    with pytest.raises(ClientPlatformError, match="restore_expired"):
        _run(platform, "conversation.restore", "late-restore")


def test_a_restart_finishes_the_deletes_it_held(platform, timers):
    from row_bot.thread_cleanup import finish_held_deletions, resume_held_deletions

    _run(platform, "conversation.delete", "delete", {"undo": True})
    timers.clear()
    resume_held_deletions()
    assert timers, "start-up schedules the removal"
    assert finish_held_deletions(now=_later()) == 1
    assert "conversation-a" not in _listed(platform)


def test_a_delete_without_undo_removes_the_conversation_at_once(platform, timers):
    receipt = _run(platform, "conversation.delete", "delete")
    assert receipt["status"] == "DeleteCompleted"
    assert not timers
    assert "conversation-a" not in _listed(platform)


def test_a_conversation_removed_while_the_list_is_read_is_left_out_not_the_whole_list(platform, monkeypatch):
    """A workflow's conversation goes with its workflow: one removed between the list's read of its rows and
    of each conversation is left out, and the rest still list (the page never fails with not found)."""
    import sqlite3
    from contextlib import closing

    from row_bot import linked_conversations, threads

    read = linked_conversations.started_from

    def gone_meanwhile(conversation_ids):
        with closing(sqlite3.connect(threads.DB_PATH)) as conn, conn:
            conn.execute("DELETE FROM thread_meta WHERE thread_id='conversation-a'")
        return read(conversation_ids)

    monkeypatch.setattr(linked_conversations, "started_from", gone_meanwhile)
    listed = _listed(platform)
    assert "conversation-a" not in listed and "conversation-b" in listed
