"""Stop keeps the reply so far (B149); a sent message never returns as a draft (B151).

Real admission, checkpoint, draft and projection owners; the model is a
scripted stream.
"""
from __future__ import annotations

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import command, platform, submit  # noqa: F401
from tests.helpers.client_platform_fakes import ScriptedAgentStream, StreamBarrier

pytestmark = pytest.mark.subsystem

CONVERSATION = "conversation-a"
MARKER = "\n\n⏹️ *[Stopped]*"


def _stop(platform):  # noqa: F811
    platform.execute(owner_id="owner", idempotency_key=command("conversation.stop", "stop")["command_id"],
                     target=CONVERSATION, command=command("conversation.stop", "stop", {}))


def _rows(platform):  # noqa: F811
    return platform.snapshot(CONVERSATION)["rows"]


def _text(row) -> str:
    return "".join(str(block.get("text") or "") for block in row["blocks"])


def test_stop_keeps_the_reply_streamed_so_far(platform):  # noqa: F811
    barrier = StreamBarrier(release_on_cancel=True)
    fake = ScriptedAgentStream((("token", "Tides rise twice a day because "), ("token", "the moon pulls"), barrier))
    receipt = submit(platform, fake, "partial")
    assert barrier.entered.wait(10)
    _stop(platform)
    handle = platform.registry.get(receipt["execution_id"])
    assert handle.producer_done.wait(10)
    rows = _rows(platform)
    assert rows[-1]["role"] == "assistant"
    assert _text(rows[-1]) == "Tides rise twice a day because the moon pulls" + MARKER


def test_stop_before_any_text_leaves_the_message_unanswered(platform):  # noqa: F811
    barrier = StreamBarrier(release_on_cancel=True)
    fake = ScriptedAgentStream((barrier,))
    receipt = submit(platform, fake, "nothing-yet")
    assert barrier.entered.wait(10)
    _stop(platform)
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    assert _rows(platform)[-1]["role"] == "user"


def _save(platform, text, refs, revision):  # noqa: F811
    from row_bot import threads
    from row_bot.application.conversation_drafts import save_draft
    threads._THREAD_UI_DIR.mkdir(parents=True, exist_ok=True)
    return save_draft(platform, CONVERSATION, {"text": text, "attachment_refs": refs,
                                               "expected_revision": revision})


def test_a_sent_message_never_comes_back_as_a_draft(platform):  # noqa: F811
    from row_bot.application.conversation_drafts import read_draft
    empty = read_draft(platform, CONVERSATION)
    saved = _save(platform, "Identical synthetic input", [], empty["revision"])
    fake = ScriptedAgentStream((("done", "ok"),))
    receipt = submit(platform, fake, "sent-draft")
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    # The client's own clear was lost (the page lost its server): a reload
    # reads the server's draft, which the admission already cleared.
    assert read_draft(platform, CONVERSATION)["text"] == ""
    # A save of the sent text still in flight stays ignored ...
    late = _save(platform, "Identical synthetic input", [], saved["revision"])
    assert late["text"] == ""
    # ... while an edit made from it continues without a conflict.
    edited = _save(platform, "Identical synthetic input, and one more thing", [], saved["revision"])
    assert edited["text"] == "Identical synthetic input, and one more thing"


def test_a_different_draft_is_kept_when_something_else_is_sent(platform):  # noqa: F811
    from row_bot.application.conversation_drafts import read_draft
    empty = read_draft(platform, CONVERSATION)
    _save(platform, "A note I am still writing", [], empty["revision"])
    fake = ScriptedAgentStream((("done", "ok"),))
    receipt = submit(platform, fake, "welcome-prompt")
    assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
    assert read_draft(platform, CONVERSATION)["text"] == "A note I am still writing"
