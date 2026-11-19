"""A streamed answer in a long chat: each token costs its own text, and the journal keeps runs, not tokens."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from row_bot.projection import conversation
from row_bot.projection.conversation import ConversationProjection

pytestmark = pytest.mark.subsystem

PASS, SEGMENT = "stream-pass", "stream-segment"
ROW = f"assistant:live:{PASS}:{SEGMENT}"


def _long_chat(projection: ConversationProjection, chat: str) -> None:
    """A saved transcript of 100 rows, about 200 KB of JSON, ending with the person's new message."""
    rows = [{"id": f"assistant:checkpoint:saved-{index}", "message_id": f"saved-{index}", "role": "assistant",
             "blocks": [{"type": "text", "text": f"Saved answer {index}. " + "words " * 330}],
             "tool_call_ids": [], "tool_call_id": ""} for index in range(99)]
    rows.append({"id": "user:submission:asked", "message_id": "asked", "role": "user",
                 "blocks": [{"type": "text", "text": "Tell me more."}], "tool_call_ids": [], "tool_call_id": ""})
    projection.install_rows(chat, "checkpoint-1", rows)


def _token(projection: ConversationProjection, chat: str, text: str) -> dict:
    """Publish one token as a running turn does."""
    return projection.publish(chat, "transcript.delta", {
        "pass_id": PASS, "segment_id": SEGMENT, "row_id": ROW,
        "render_revision": str(projection.revision(chat) + 1), "public_text_delta": text})


def _live_text(snapshot: dict) -> str:
    row = next((row for row in snapshot["rows"] if row["id"] == ROW), None)
    return "".join(block["text"] for block in row["blocks"]) if row else ""


def _applied(text: str, events: list[dict]) -> str:
    """What a client shows after applying replayed events to a snapshot's live row (controller.ts)."""
    for event in events:
        if event["type"] == "transcript.delta" and event["payload"]["row_id"] == ROW:
            text += event["payload"]["public_text_delta"]
    return text


def test_a_token_serialises_its_own_row_never_the_transcript(monkeypatch):
    projection = ConversationProjection("stream-epoch")
    _long_chat(projection, "chat")
    transcript = len(json.dumps(projection.snapshot("chat")["rows"], ensure_ascii=False))
    before = projection.snapshot("chat")
    serialised = []

    def dumps(value, **options):
        encoded = json.dumps(value, **options)
        serialised.append(len(encoded))
        return encoded

    monkeypatch.setattr(conversation, "json", SimpleNamespace(dumps=dumps))
    tokens = [f"token {index} " for index in range(500)]
    published = [_token(projection, "chat", text) for text in tokens]
    monkeypatch.undo()

    # Each token serialises its payload, its event and its own live row (a few KB here), not 200 KB of rows.
    assert max(serialised) < transcript / 20
    assert sum(serialised) / len(tokens) < transcript / 20
    after = projection.snapshot("chat")
    assert _live_text(after) == "".join(tokens)
    assert after["rows"][:100] == before["rows"]
    replay = projection.events_since("chat", before["cursor"])
    assert replay["snapshot_required"] is False
    assert replay["events"] == published
    assert _applied(_live_text(before), replay["events"]) == "".join(tokens)


def test_ten_thousand_tokens_keep_a_small_journal_and_replay_every_token():
    projection = ConversationProjection("stream-epoch")
    # A small journal: one event per token (about 4 MB of event JSON here) would have dropped the start.
    projection.MAX_EVENTS = 16
    projection.MAX_HISTORY_BYTES = 256 * 1024
    _long_chat(projection, "chat")
    start = projection.snapshot("chat")
    tokens = [f"t{index} " for index in range(10_000)]
    for text in tokens:
        _token(projection, "chat", text)
    replay = projection.events_since("chat", start["cursor"])
    assert replay["snapshot_required"] is False
    revisions = [int(event["projection_revision"]) for event in replay["events"]]
    assert revisions == list(range(int(start["cursor"]) + 1, int(start["cursor"]) + 10_001))
    assert [event["payload"]["public_text_delta"] for event in replay["events"]] == tokens
    assert len({event["event_id"] for event in replay["events"]}) == 10_000
    assert projection.events_since("chat", start["cursor"]) == replay  # replay is repeatable, ids included


def test_a_client_resuming_from_any_revision_shows_the_whole_answer_once():
    projection = ConversationProjection("stream-epoch")
    _long_chat(projection, "chat")
    seen = [projection.snapshot("chat")]
    tokens = [f"word{index} " for index in range(600)]
    for index, text in enumerate(tokens):
        _token(projection, "chat", text)
        if index % 100 == 50:  # other events land between runs of the row's text
            projection.publish("chat", "generation.activity", {"state": "thinking"})
        if index % 37 == 0:
            seen.append(projection.snapshot("chat"))
    for snapshot in seen:
        replay = projection.events_since("chat", snapshot["cursor"])
        assert replay["snapshot_required"] is False
        revisions = [int(event["projection_revision"]) for event in replay["events"]]
        assert revisions == list(range(int(snapshot["cursor"]) + 1, projection.revision("chat") + 1))
        assert all(event["source_sequence_start"] == event["source_sequence_end"] == event["projection_revision"]
                   for event in replay["events"])
        assert _applied(_live_text(snapshot), replay["events"]) == "".join(tokens)
    activity = [event for event in projection.events_since("chat", seen[0]["cursor"])["events"]
                if event["type"] == "generation.activity"]
    assert len(activity) == 6
