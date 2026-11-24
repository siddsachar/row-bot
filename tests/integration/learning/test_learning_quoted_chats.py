"""A chat the person excluded from Learning stays out of what Learning keeps, even when another chat's search
quotes it (real Agent turns: a fake model, the real graph, tool wrapper and capture). A step that quotes it is
never stored; excluding it afterwards purges every step that quoted it, frees the stored words, and keeps the
quoting chat's other steps. Training datasets name the quoted chat, and no test task copies its words."""

from __future__ import annotations

import pytest
from langchain_core.tools import tool

from row_bot.learning import capture, evaluation, scope
from row_bot.learning.store import LearningStore
from row_bot.learning.training import dataset, recipes
from tests.fixtures.learning import all_blob_text
from tests.helpers.learning_turns import AgentTurns, ScriptedModel, asks, call, says

pytestmark = [pytest.mark.integration, pytest.mark.platform]  # the writer thread across real turns

QUOTED = "chat-b"
SECRET = "the spare key is under the blue pot"


@tool
def search_past(query: str) -> str:
    """Search the person's past conversations."""
    from row_bot.tool_outputs import note_quoted_conversations

    note_quoted_conversations([QUOTED])  # as search_conversations notes the chats its matches come from
    return f'[Match 1] Thread: "Garden" | Person\nRemember, {SECRET}.'


def _turn(monkeypatch) -> AgentTurns:
    model = ScriptedModel(responses=[
        asks(call("search_past", "c1", query="spare key")),
        says(f"You said {SECRET}."),
    ], seen=[])
    turns = AgentTurns(monkeypatch, model, [search_past])
    turns.send("Where did I say the spare key is?")
    return turns


def _decisions(store: LearningStore) -> list:
    return sorted(store.find("decision"), key=lambda item: (item.created_at, item.sequence))


def test_a_step_quoting_an_excluded_chat_is_never_stored(learning_dir, monkeypatch):
    scope.set_chat_opt_out(QUOTED, True)

    _turn(monkeypatch)

    assert capture.flush(10)
    store = LearningStore(learning_dir)
    decisions = _decisions(store)
    # The step before the search holds nothing of the excluded chat and is kept; nothing after it is.
    assert [item.kind for item in decisions] == ["model_call"]
    assert decisions[0].quoted_thread_ids == ()
    assert SECRET not in all_blob_text(learning_dir)


def test_excluding_a_quoted_chat_later_purges_every_step_that_quoted_it(learning_dir, monkeypatch):
    _turn(monkeypatch)
    assert capture.flush(10)
    store = LearningStore(learning_dir)
    quoting = [item for item in _decisions(store) if item.quoted_thread_ids]
    assert {item.kind for item in quoting} == {"tool_call", "model_call"}
    assert {item.quoted_thread_ids for item in quoting} == {(QUOTED,)}
    assert SECRET in all_blob_text(learning_dir)
    # Until then a training dataset names the quoted chat, and no test task copies its words.
    compiled = dataset.compile_dataset(store, recipes.load("16gb"), refresh_vault=False, shape="captured")
    assert QUOTED in compiled.conversations
    assert evaluation.collect_replay(store).skipped["quotes_another_chat"] == 1

    scope.set_chat_opt_out(QUOTED, True)

    left = _decisions(LearningStore(learning_dir))
    assert [item.kind for item in left] == ["model_call"] and left[0].quoted_thread_ids == ()
    assert SECRET not in all_blob_text(learning_dir)
