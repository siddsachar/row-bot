"""The Library and the phone's Chats tab open fast on a large profile.

A page of the conversation list costs the same few reads whatever its size, and never reads a checkpoint: a
chat with thousands of messages lists as cheaply as an empty one. Real stores in an isolated data folder.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401

pytestmark = pytest.mark.subsystem


def _library(threads, count: int) -> None:
    """Chats of every shape: pinned, designs, folders, and a few long ones with big checkpoints."""
    for index in range(count):
        identity = f"library-{index:03d}"
        threads.create_thread(f"Chat {index:03d}", thread_id=identity, seed_default_skills=False,
                              project_id="deck-a" if index % 7 == 0 else "")
        with closing(sqlite3.connect(threads.DB_PATH)) as conn, conn:
            conn.execute("UPDATE thread_meta SET updated_at=?,pinned_at=? WHERE thread_id=?",
                         (f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}",
                          "2026-02-01T00:00:00" if index % 25 == 0 else "", identity))
            if index % 5 == 0:
                conn.execute("UPDATE thread_meta SET resource_bindings_json=? WHERE thread_id=?", (json.dumps([
                    {"binding_id": f"binding-{index}", "kind": "workspace", "resource_id": f"folder-{index}",
                     "role": "context", "revision": "1"}]), identity))
        if index < 3:
            threads.append_checkpoint_messages(identity, [
                (HumanMessage if turn % 2 == 0 else AIMessage)(id=f"{identity}-{turn}", content="words " * 200)
                for turn in range(400)])


def test_a_page_of_the_list_takes_the_same_reads_for_200_chats_as_for_20_and_reads_no_checkpoint(
        platform, monkeypatch):  # noqa: F811
    from row_bot import threads

    _library(threads, 220)

    def refuse(*_args, **_kwargs):
        raise AssertionError("The conversation list read a checkpoint")

    monkeypatch.setattr(threads.checkpointer, "get_tuple", refuse)
    monkeypatch.setattr(threads, "get_latest_checkpoint_messages", refuse)
    opened = []
    connect = sqlite3.connect

    def counted(*args, **kwargs):
        opened.append(args[0] if args else kwargs.get("database"))
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", counted)
    platform.list_conversations(limit=20)  # The first read sets up the stores it reads.
    opened.clear()
    small = platform.list_conversations(limit=20)
    reads_for_small = len(opened)
    opened.clear()
    large = platform.list_conversations(limit=200)

    assert len(small["items"]) == 20 and len(large["items"]) == 200
    assert len(opened) == reads_for_small, "Each conversation on the page opened its own reads"
    # The rows are whole: pinned first, newest next, each with its bindings and type.
    assert [row["id"] for row in large["items"][:9]] == [f"library-{n:03d}" for n in (200, 175, 150, 125, 100,
                                                                                      75, 50, 25, 0)]
    shown = {row["id"]: row for row in large["items"]}
    assert shown["library-200"]["pinned"] and shown["library-200"]["category"] == "chat"
    assert [binding["resource_id"] for binding in shown["library-195"]["resource_bindings"]] == ["folder-195"]
    assert shown["library-196"]["category"] == "designer"
    assert [binding["kind"] for binding in shown["library-196"]["resource_bindings"]] == ["artifact"]
