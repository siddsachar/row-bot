"""A reply never shows twice while its turn settles.

The streamed answer is a live row until Row-Bot binds it to the saved message. Binding read the saved
messages in (now holding the answer) and then retired the live row, as two steps: a page reading the
conversation between them got the answer twice, once saved and once live. Found in the browser smoke
(15 Oct, "what you send leaves the composer at once"): an instant reply showed twice for a moment.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from tests.contracts.client_platform.test_headless_lifecycle import platform, submit  # noqa: F401
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, fixture_id

pytestmark = pytest.mark.subsystem


def test_every_read_of_a_settling_turn_shows_the_reply_once(platform, monkeypatch):
    reads: list[list[str]] = []
    projection = platform.projection
    notify = projection._notify

    def read_when_pages_are_told(conversation_id, revision):
        # Each change wakes the pages following the conversation; they read it then.
        if conversation_id == "conversation-a":
            reads.append([str(row.get("message_id") or row["id"]) + ":" + "".join(
                block.get("text", "") for block in row.get("blocks", []))
                for row in projection.snapshot(conversation_id)["rows"] if row["role"] == "assistant"])
        return notify(conversation_id, revision)

    monkeypatch.setattr(projection, "_notify", read_when_pages_are_told)
    native_id = fixture_id("instant-native")
    fake = ScriptedAgentStream((("token", "Here it is, straight away."),
                               CheckpointCommit((AIMessage(content="Here it is, straight away.", id=native_id),),
                                                native_id),
                               ("done", "Here it is, straight away.")))
    handle = platform.registry.get(submit(platform, fake, "instant")["execution_id"])
    assert handle.producer_done.wait(10)

    replies = [[row for row in read if row.endswith(":Here it is, straight away.")] for read in reads]
    assert any(replies), "the reply was never read"
    assert all(len(found) <= 1 for found in replies), replies
    final = platform.snapshot("conversation-a")["rows"]
    assert [row["id"] for row in final if row["role"] == "assistant"] == ["assistant:checkpoint:" + native_id]


def test_a_page_reading_after_the_answer_is_saved_but_before_it_is_bound_sees_it_once(platform):
    """The saved answer can reach the checkpoint a moment before Row-Bot binds it to its live row. A page
    reading the conversation in that moment (after any reset) read the saved messages in and got both. Found
    in the browser smoke (15 Oct, #459/#461): a reply after a tool step showed twice for a moment."""
    from tests.helpers.client_platform_fakes import StreamBarrier

    text = "I can't record your screen yet."
    native_id = fixture_id("saved-before-bound-answer")
    barrier = StreamBarrier()
    fake = ScriptedAgentStream((("token", text), CheckpointCommit((AIMessage(content=text, id=native_id),)),
                               barrier, ("done", text)))
    handle = platform.registry.get(submit(platform, fake, "saved-before-bound")["execution_id"])
    try:
        assert barrier.entered.wait(10)
        rows = platform.snapshot("conversation-a")["rows"]  # A page reads the conversation now.
        shown = [row for row in rows if row["role"] == "assistant"
                 and "".join(block.get("text", "") for block in row.get("blocks", [])) == text]
        assert len(shown) == 1, [row["id"] for row in shown]
    finally:
        barrier.release.set()
        assert handle.producer_done.wait(10)
    final = [row["id"] for row in platform.snapshot("conversation-a")["rows"] if row["role"] == "assistant"]
    assert final == ["assistant:checkpoint:" + native_id]
