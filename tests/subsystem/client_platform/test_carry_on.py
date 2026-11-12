"""A reply that stops on its next step carries on once by itself (R1).

A model can write "Next I'll …" with no tool call, which ends its turn with the work half done, and the
person had to ask why it stopped. Real admission, checkpoint and follow-up owners; the model is scripted,
so nothing calls a provider.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta

import pytest
from langchain_core.messages import AIMessage

from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, fixture_id

pytestmark = pytest.mark.subsystem

CONVERSATION = "conversation-a"
NEXT = "The landscape cut is saved. Next I'll render the vertical version and check its framing."


def replied(text: str, label: str):
    native_id = fixture_id(label + ":assistant")
    return (CheckpointCommit((AIMessage(content=text, id=native_id),), native_id), ("done", text))


class Recording(ScriptedAgentStream):
    """Keeps the prompt each turn received."""

    def __init__(self, *scripts) -> None:
        super().__init__(*scripts)
        self.prompts: list[str] = []

    def stream(self, text, enabled_tools, config, *, stop_event=None):
        self.prompts.append(text)
        yield from super().stream(text, enabled_tools, config, stop_event=stop_event)


@pytest.fixture
def chat(platform):  # noqa: F811
    from row_bot import threads
    from row_bot.application import conversation_followups

    with sqlite3.connect(threads.DB_PATH) as connection:
        connection.execute("UPDATE thread_meta SET model_override='fixture/model' WHERE thread_id=?",
                           (CONVERSATION,))
    for key in list(conversation_followups._PENDING):
        conversation_followups._PENDING.pop(key, None)
    return platform


def send(platform, fake, text: str, label: str) -> None:  # noqa: F811
    platform.stream_factory = fake.stream
    platform.resume_factory = fake.resume
    platform.execute(owner_id="fixture", idempotency_key=label, target=CONVERSATION,
                     command=command("conversation.submit", label, {
                         "text": text, "submission_id": label,
                         "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"}}))


def settle(platform, fake, calls: int) -> None:  # noqa: F811
    """Wait until ``calls`` turns ran and nothing is running or about to start."""
    from row_bot.application import conversation_followups
    from row_bot.application.client_platform import _COMMAND_LOCK
    for _ in range(200):
        with _COMMAND_LOCK:
            if (len(fake.calls) >= calls and not platform.registry.active(CONVERSATION)
                    and conversation_followups.pending(CONVERSATION) is None):
                threading.Event().wait(0.2)  # A further turn would have started by now.
                if not platform.registry.active(CONVERSATION):
                    return
        threading.Event().wait(0.05)
    raise AssertionError(f"expected {calls} turns, saw {len(fake.calls)}")


def notes(platform) -> list[str]:  # noqa: F811
    return [row["blocks"][0]["text"] for row in platform.snapshot(CONVERSATION)["rows"]
            if row.get("note") == "continuation"]


def test_a_reply_that_stops_on_its_next_step_carries_on_once(chat):
    from row_bot.application.conversation_followups import CARRY_ON_NOTE, CARRY_ON_PROMPT
    fake = Recording(replied(NEXT, "first"), replied("Next I'll check the captions are off.", "second"),
                     replied("unused", "third"))
    send(chat, fake, "Make the launch film in both shapes", "film")
    settle(chat, fake, 2)
    assert len(fake.calls) == 2, "one carry-on turn, never a chain of them"
    assert fake.prompts[1] == CARRY_ON_PROMPT
    assert "This is Row-Bot, not the person" in fake.prompts[1]
    assert notes(chat) == [CARRY_ON_NOTE], "the transcript says why it went on, not the prompt"


@pytest.mark.parametrize("reply", [
    "Next I'll render the vertical version. Do you want it with music?",
    "The film is ready. Let me know if you'd like a shorter cut.",
    "Here is the finished film, with the vertical cut beside it.",
])
def test_a_reply_that_hands_over_or_is_done_does_not_carry_on(chat, reply):
    fake = Recording(replied(reply, "only"), replied("unused", "extra"))
    send(chat, fake, "Make the launch film", "film")
    settle(chat, fake, 1)
    assert len(fake.calls) == 1
    assert notes(chat) == []


def test_a_wait_already_brings_it_back(chat):
    from row_bot import waits
    waits.create(CONVERSATION, "time", "The render finishes", spec={"then": "Check the render"},
                 at=datetime.now() + timedelta(minutes=30))
    fake = Recording(replied(NEXT, "only"), replied("unused", "extra"))
    send(chat, fake, "Make the launch film", "film")
    settle(chat, fake, 1)
    assert len(fake.calls) == 1, "the wait carries it on when the render is done"


def test_the_persons_message_replaces_a_pending_carry_on(chat):
    from row_bot.application import conversation_followups
    conversation_followups._PENDING[CONVERSATION] = conversation_followups.Followup(
        kind="carry_on", prompt=conversation_followups.CARRY_ON_PROMPT, note=conversation_followups.CARRY_ON_NOTE)
    fake = Recording(replied("Done: the film is in the chat.", "answer"), replied("unused", "extra"))
    send(chat, fake, "Stop there and show me what you have", "steer")
    settle(chat, fake, 1)
    assert fake.prompts == ["Stop there and show me what you have"]
    assert len(fake.calls) == 1, "the stale carry-on doesn't run after the person's turn"
