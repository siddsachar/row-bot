"""A reply that stops on its next step carries on by itself (R1), again after a carry-on that used tools.

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


def worked(text: str, label: str):
    """A turn that ran a tool, then ended its reply with *text*."""
    native_id = fixture_id(label + ":assistant")
    call_id = f"call-{label}"
    return (("tool_call", {"id": call_id, "name": "render_video", "args": {}}),
            ("tool_done", {"tool_call_id": call_id, "message_id": f"tool-{label}", "name": "render_video"}),
            ("token", text), CheckpointCommit((AIMessage(content=text, id=native_id),), native_id), ("done", text))


class Recording(ScriptedAgentStream):
    """Keeps the prompt each turn received."""

    def __init__(self, *scripts) -> None:
        super().__init__(*scripts)
        self.prompts: list[str] = []

    def stream(self, text, enabled_tools, config, *, stop_event=None):
        self.prompts.append(text)
        yield from super().stream(text, enabled_tools, config, stop_event=stop_event)


class StopCheck:
    """The chat's model answering the stop check (S1); it notes whether the command lock was free."""

    def __init__(self) -> None:
        self.answer = "YES"
        self.asked: list[str] = []
        self.lock_free: list[bool] = []

    def invoke(self, messages):
        from row_bot.application.client_platform import _COMMAND_LOCK
        free: list[bool] = []

        def other_thread() -> None:
            got = _COMMAND_LOCK.acquire(timeout=2)
            if got:
                _COMMAND_LOCK.release()
            free.append(got)
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
        self.lock_free.append(free == [True])
        self.asked.append(messages[-1].content)
        return AIMessage(content=self.answer)


@pytest.fixture
def stop_check(monkeypatch):
    check = StopCheck()
    monkeypatch.setattr("row_bot.models.get_llm_for", lambda *_args, **_kwargs: check)
    return check


@pytest.fixture
def chat(platform, stop_check):  # noqa: F811
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


def test_a_carry_on_that_used_tools_and_stops_again_carries_on_again_up_to_the_cap(chat):
    from row_bot.application.conversation_followups import CARRY_ON_NOTE, CARRY_ON_PROMPT, MAX_CARRY_ONS
    steps = [worked(f"Next I'll render part {n} of the film.", f"step-{n}") for n in range(MAX_CARRY_ONS + 1)]
    fake = Recording(replied(NEXT, "first"), *steps, replied("unused", "last"))
    send(chat, fake, "Make the launch film in both shapes", "film")
    settle(chat, fake, 1 + MAX_CARRY_ONS)
    assert len(fake.calls) == 1 + MAX_CARRY_ONS, "past the cap the reply waits for the person"
    assert fake.prompts[1:] == [CARRY_ON_PROMPT] * MAX_CARRY_ONS
    assert notes(chat) == [CARRY_ON_NOTE] * MAX_CARRY_ONS


def test_a_repeat_carry_on_needs_a_clear_yes_from_the_stop_check(chat, stop_check):
    stop_check.answer = "Okay, it looks unfinished."
    fake = Recording(replied(NEXT, "first"), worked("Next I'll check the captions are off.", "second"),
                     replied("unused", "third"))
    send(chat, fake, "Make the launch film in both shapes", "film")
    settle(chat, fake, 2)
    assert len(fake.calls) == 2, "the first carry-on reads an unclear answer as before; a repeat needs YES"


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


def test_a_progress_line_before_a_tool_is_not_how_the_turn_ended(chat, stop_check):
    """F7: "I'll paint …" came before the picture tool, then the finished caption; the turn didn't carry on."""
    caption = "**Red Lighthouse at Sunset**: a watercolour of a rocky coast, with lavender skies."
    native_id = fixture_id("picture:assistant")
    fake = Recording((
        ("token", "I'll paint the red lighthouse against a soft sunset wash."),
        ("tool_call", {"id": "call-1", "name": "generate_image", "args": {}}),
        ("tool_done", {"tool_call_id": "call-1", "message_id": "tool-1", "name": "generate_image"}),
        ("token", caption),
        CheckpointCommit((AIMessage(content=caption, id=native_id),), native_id),
        ("done", "I'll paint the red lighthouse against a soft sunset wash." + caption),
    ), replied("unused", "extra"))
    send(chat, fake, "Make a picture of a red lighthouse", "picture")
    settle(chat, fake, 1)
    assert len(fake.calls) == 1
    assert notes(chat) == []
    assert stop_check.asked == [], "the caption alone never looked unfinished"


def test_a_wait_already_brings_it_back(chat):
    from row_bot import waits
    waits.create(CONVERSATION, "time", "The render finishes", spec={"then": "Check the render"},
                 at=datetime.now() + timedelta(minutes=30))
    fake = Recording(replied(NEXT, "only"), replied("unused", "extra"))
    send(chat, fake, "Make the launch film", "film")
    settle(chat, fake, 1)
    assert len(fake.calls) == 1, "the wait carries it on when the render is done"


MISSING = "The launch notes are written up. I saved them to `launch-notes.md` in your workspace."


def workspace():
    from row_bot.application.conversation_creation import configured_workspace_root
    return configured_workspace_root()


def test_a_reply_that_names_a_file_it_never_saved_checks_once(chat):
    """A2 WP4: one turn to make the file or correct the reply, and never a chain of them."""
    from row_bot.application.conversation_followups import FILE_CHECK_NOTE, file_check_prompt
    fake = Recording(replied(MISSING, "first"), replied("Saved them to `launch-notes.md` now.", "second"),
                     replied("unused", "third"))
    send(chat, fake, "Write up the launch notes", "notes")
    settle(chat, fake, 2)
    assert len(fake.calls) == 2, "one check turn, never a chain of them"
    assert fake.prompts[1] == file_check_prompt(["launch-notes.md"]) == (
        "This is Row-Bot, not the person: your last reply says you saved `launch-notes.md`, but there's no "
        "file there. Make it now, or correct your reply in a line.")
    assert notes(chat) == [FILE_CHECK_NOTE]


@pytest.mark.parametrize("reply", [
    MISSING,  # The file is there.
    "The launch notes are online at https://example.test/launch-notes.md.",
    "Want me to save the notes as launch-notes.md?",
])
def test_a_reply_naming_a_file_that_is_there_a_link_or_an_offer_does_not_check(chat, reply):
    (workspace() / "launch-notes.md").write_text("# Launch\n", encoding="utf-8")
    fake = Recording(replied(reply, "only"), replied("unused", "extra"))
    send(chat, fake, "Write up the launch notes", "notes")
    settle(chat, fake, 1)
    assert len(fake.calls) == 1
    assert notes(chat) == []


@pytest.mark.parametrize("status", ["waiting_approval", "interrupted", "stopped"])
def test_no_file_check_after_an_unfinished_turn(chat, status):
    from row_bot.application import conversation_followups
    conversation_followups.after_platform_turn(CONVERSATION, generation_id="turn-1", status=status,
                                               assistant_text=MISSING, model_ref="fixture/model")
    assert conversation_followups.pending(CONVERSATION) is None


def test_no_file_check_while_a_goal_is_live(chat, monkeypatch):
    """The goal's own follow-ups drive its next turn."""
    from row_bot import goals
    from row_bot.application import conversation_followups
    goals.start_goal(CONVERSATION, "Write up the launch")
    monkeypatch.setattr(goals, "_invoke_goal_verifier",
                        lambda _goal, _context: {"progress": "progress", "reason": "More to do."})
    conversation_followups.after_platform_turn(CONVERSATION, generation_id="turn-1", status="completed",
                                               assistant_text=MISSING, model_ref="fixture/model")
    followup = conversation_followups.pending(CONVERSATION)
    assert followup is not None and followup.kind == "goal"
    assert followup.note != conversation_followups.FILE_CHECK_NOTE


def test_the_persons_message_replaces_a_pending_carry_on(chat):
    from row_bot.application import conversation_followups
    conversation_followups._PENDING[CONVERSATION] = conversation_followups.Followup(
        kind="carry_on", prompt=conversation_followups.CARRY_ON_PROMPT, note=conversation_followups.CARRY_ON_NOTE)
    fake = Recording(replied("Done: the film is in the chat.", "answer"), replied("unused", "extra"))
    send(chat, fake, "Stop there and show me what you have", "steer")
    settle(chat, fake, 1)
    assert fake.prompts == ["Stop there and show me what you have"]
    assert len(fake.calls) == 1, "the stale carry-on doesn't run after the person's turn"


@pytest.mark.parametrize("reply", [
    "The first take is saved. I also caught that the brief went in as separate messages. I'll reshoot with the "
    "same brief sent as one message in each run. The replacement film isn't finished yet.",
    "That approval only blocked one run, not the retakes. I should have kept going. I've saved the fix.",
    "I've updated the studio plan beside the chat. The real-data video hasn't been rendered yet.",
    # Stops from the τ²-bench traces (19 Nov): the reply ends announcing work, and no tool call follows.
    "Your reservation has travel insurance. I'm checking the flight statuses before determining the next steps.",
    "I found four more reservations; I've checked two and will review the remaining two before totaling them.",
    "One booking is basic economy. I still need to verify the flight statuses before deciding what can change.",
    # Stops from the DeepSWE traces (26 Nov): work announced with no "I'll".
    "The parser is in. I'm now finishing the schema exports before type-checking and writing tests.",
    "Struct methods work. Next I'm wiring interface method dispatch and running the full test suite.",
])
def test_a_reply_that_stops_mid_work_in_other_words_carries_on_when_the_model_agrees(chat, stop_check, reply):
    """S1 (18 Nov): the owner's real stops, which the narrower rule never caught; the chat's own model is asked
    first, with the command lock free so other chats don't wait on it."""
    from row_bot.application.conversation_followups import CARRY_ON_PROMPT
    fake = Recording(replied(reply, "first"), replied("Reshot all three: the film is in the chat.", "second"),
                     replied("unused", "third"))
    send(chat, fake, "Film the three models on the same task", "film")
    settle(chat, fake, 2)
    assert fake.prompts[1] == CARRY_ON_PROMPT
    assert len(stop_check.asked) == 1 and reply[-60:] in stop_check.asked[0]
    assert stop_check.lock_free == [True]


def test_no_carry_on_when_the_chats_model_reads_the_reply_as_done(chat, stop_check):
    stop_check.answer = "NO"
    fake = Recording(replied("Saved both cuts. I'll keep the raw takes in the chat's files.", "only"),
                     replied("unused", "extra"))
    send(chat, fake, "Make the launch film", "film")
    for _ in range(100):
        if stop_check.asked:
            break
        threading.Event().wait(0.05)
    settle(chat, fake, 1)
    assert stop_check.asked, "the word rule found it and the model was asked"
    assert len(fake.calls) == 1
    assert notes(chat) == []


@pytest.mark.parametrize("reply, carries_on", [
    ("The landscape cut is saved. Next I'll render the vertical version.", True),
    ("Recording Row-Bot's browser now. I'll stop it once the sign-up flow is done.", False),
    # The broader rule's newer phrasings (19 Nov) never carry on by themselves: only the model's check can.
    ("Your reservation has travel insurance. I'm checking the flight statuses before determining the next steps.",
     False),
])
def test_when_the_check_cannot_be_asked_only_the_narrower_rules_words_carry_on(chat, monkeypatch, reply, carries_on):
    """With no model to ask (a provider error, an endpoint that is down), the broader rule alone fired on
    replies that were waiting, not stopping; the narrower R1 rule decides then, as before."""
    asked: list[str] = []

    def unavailable(*_args, **_kwargs):
        asked.append("asked")
        raise RuntimeError("the provider is down")

    monkeypatch.setattr("row_bot.models.get_llm_for", unavailable)
    fake = Recording(replied(reply, "first"), replied("Done.", "second"), replied("unused", "third"))
    send(chat, fake, "Film the sign-up flow", "film")
    for _ in range(100):
        if asked:
            break
        threading.Event().wait(0.05)
    settle(chat, fake, 2 if carries_on else 1)
    assert asked, "the check was tried"
    assert len(fake.calls) == (2 if carries_on else 1)

