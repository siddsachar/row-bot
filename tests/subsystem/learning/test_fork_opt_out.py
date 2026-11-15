"""A fork (B5) of a chat left out of Learning is left out too; forking never creates a Learning store."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.subsystem


def _chat(threads, thread_id: str) -> str:
    threads.create_thread("Trip", thread_id=thread_id, approval_mode="approve")
    assert threads.append_checkpoint_messages(thread_id, [
        HumanMessage(id=f"{thread_id}-h1", content="Plan a trip"),
        AIMessage(id=f"{thread_id}-a1", content="Where to?")])
    return thread_id


def _fork(threads, source: str) -> str:
    return threads.fork_conversation(source, at_message_id=f"{source}-a1", include_message=True).conversation_id


def test_a_fork_of_an_opted_out_chat_is_opted_out_and_others_are_not(learning_on, reload_for_data_dir):
    from row_bot.learning.store import LearningStore

    threads = reload_for_data_dir(learning_on, "row_bot.threads")[0]
    left_out, everyday = _chat(threads, "left-out"), _chat(threads, "everyday")
    LearningStore().set_chat_opt_out(left_out, True)

    left_out_fork, everyday_fork = _fork(threads, left_out), _fork(threads, everyday)

    store = LearningStore()
    assert store.is_chat_opted_out(left_out_fork) is True
    assert store.is_chat_opted_out(everyday_fork) is False


def test_forking_without_a_learning_store_creates_none(data_dir, reload_for_data_dir):
    from row_bot import learning

    threads = reload_for_data_dir(data_dir, "row_bot.threads")[0]
    source = _chat(threads, "everyday")

    _fork(threads, source)

    assert learning.store_exists() is False
