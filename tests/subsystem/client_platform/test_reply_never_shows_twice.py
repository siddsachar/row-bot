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


def test_an_answer_saved_before_it_starts_streaming_shows_once(platform):
    """The other order: a page reads the saved answer in before its live row starts (a tool step's refresh
    lands between the save and the first token). Twins were only retired when saved messages were read in,
    so every later read showed the answer twice until it was bound. Found in the browser smoke (21 Oct, #466):
    the reply after two reviewed tool steps."""
    from tests.helpers.client_platform_fakes import StreamBarrier

    text = "Saved the summary and committed the tidy-up."
    native_id = fixture_id("saved-before-streamed-answer")
    saved, streamed = StreamBarrier(), StreamBarrier()
    fake = ScriptedAgentStream((CheckpointCommit((AIMessage(content=text, id=native_id),)), saved,
                               ("token", text), streamed, ("done", text)))
    handle = platform.registry.get(submit(platform, fake, "saved-before-streamed")["execution_id"])
    try:
        assert saved.entered.wait(10)
        page = platform.snapshot("conversation-a")  # A page reads the saved answer in before it streams.
        saved.release.set()
        assert streamed.entered.wait(10)
        # The page applies each delta that follows to its own rows, as the client does, until a settle
        # or checkpoint sends it to read again.
        rows = {row["id"]: row for row in page["rows"]}
        for event in platform.events_since("conversation-a", page["cursor"])["events"]:
            if event["type"] in {"transcript.settled", "transcript.checkpoint"}:
                break
            if event["type"] == "transcript.delta":
                delta = event["payload"]
                live = rows.setdefault(delta["row_id"], {"id": delta["row_id"], "role": "assistant",
                                                         "blocks": [{"type": "text", "text": ""}]})
                live["blocks"][0]["text"] += delta["public_text_delta"]
        for view in (list(rows.values()), platform.snapshot("conversation-a")["rows"]):  # and when it reads again
            shown = [row for row in view if row["role"] == "assistant"
                     and "".join(block.get("text", "") for block in row.get("blocks", [])) == text]
            assert len(shown) == 1, [row["id"] for row in shown]
    finally:
        saved.release.set()
        streamed.release.set()
        assert handle.producer_done.wait(10)
    final = [row["id"] for row in platform.snapshot("conversation-a")["rows"] if row["role"] == "assistant"]
    assert final == ["assistant:checkpoint:" + native_id]


def test_a_retired_twin_never_comes_back_as_an_empty_paged_row(platform):
    """B401: a live row retired because its answer was already saved left its live text behind until the turn
    ended. Every read in between added an empty "Paged content" row for that text, and when the turn ended
    and the text was thrown away nothing sent the page to read again: the row stayed, and loading it said
    "That is no longer available." until a reload."""
    from tests.helpers.client_platform_fakes import StreamBarrier

    text = "Filed the receipts and sent the summary."
    native_id = fixture_id("retired-twin-answer")
    saved, streamed = StreamBarrier(), StreamBarrier()
    fake = ScriptedAgentStream((CheckpointCommit((AIMessage(content=text, id=native_id),)), saved,
                               ("token", text), streamed, ("done", text)))
    handle = platform.registry.get(submit(platform, fake, "retired-twin")["execution_id"])
    try:
        assert saved.entered.wait(10)
        saved.release.set()
        assert streamed.entered.wait(10)
        # The settle for the retired twin sends pages to read again, while the turn still runs.
        during = platform.snapshot("conversation-a")["rows"]
    finally:
        saved.release.set()
        streamed.release.set()
        assert handle.producer_done.wait(10)
    after = platform.snapshot("conversation-a")["rows"]
    for rows in (during, after):
        assert [row["id"] for row in rows if row.get("content_status") == "lazy"] == []
        assert [row["id"] for row in rows if row["role"] == "assistant"] == ["assistant:checkpoint:" + native_id]


def test_a_chat_opened_mid_run_shows_each_step_once(platform):
    """U12: the live row streams every step's text, and opening the chat mid-run also read in the steps saved
    so far: each step showed with its tool, then again in one block with the rest below them."""
    from langchain_core.messages import ToolMessage

    from tests.helpers.client_platform_fakes import StreamBarrier

    step_id, done_id = fixture_id("mid-run-step"), fixture_id("mid-run-step-result")
    call = {"name": "workspace_list", "args": {}, "id": "call-mid-run"}
    barrier = StreamBarrier()
    fake = ScriptedAgentStream((("token", "Let me look at the workspace."),
                               CheckpointCommit((AIMessage(content="Let me look at the workspace.", id=step_id,
                                                           tool_calls=[call]),
                                                 ToolMessage(content="3 files", tool_call_id="call-mid-run",
                                                             id=done_id))),
                               ("token", " It holds three files."), barrier, ("done", "It holds three files.")))
    handle = platform.registry.get(submit(platform, fake, "mid-run")["execution_id"])
    try:
        assert barrier.entered.wait(10)
        rows = platform.snapshot("conversation-a")["rows"]  # The chat is opened again now.
        shown = ["".join(block.get("text", "") for block in row.get("blocks", []))
                 for row in rows if row["role"] == "assistant"]
        assert shown == ["Let me look at the workspace.", "It holds three files."]
    finally:
        barrier.release.set()
        assert handle.producer_done.wait(10)
