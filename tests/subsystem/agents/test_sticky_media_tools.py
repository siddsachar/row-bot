"""Media tools follow the work (M7): once a chat makes media, its media tools stay bound for the rest of it.

Deferred behind tool_search, one video chat reached edit_video, video, speak and send_file through 552
tool_invoke calls, and the delivery step was the one forgotten. These tests build Row-Bot's real tool
assembly over its default tools (the plain chat of the B1 budget test) with a saved history. Nothing
reaches a provider.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.subsystem.agents.test_default_tool_budget import plain_chat  # noqa: F401 -- shared harness

pytestmark = pytest.mark.subsystem

STICKY_SET = {"edit_video", "video", "speak", "make_music", "transcribe", "send_file", "media_find", "media_tag",
              "media_bring", "browser_record"}
SOUND_LEFT_OUT = {"exclude_servers": [], "exclude_tools": ["sound"], "focus": [], "skills": []}


@pytest.fixture
def chat(plain_chat):  # noqa: F811 -- the B1 budget test's plain chat
    from row_bot import threads

    threads.create_thread("Media", thread_id=plain_chat.thread_id, seed_default_skills=False)
    return plain_chat


def _history(thread_id: str, *calls: tuple[str, dict]) -> None:
    from row_bot import threads

    messages: list = [HumanMessage(content="Make me something.")]
    for index, (name, args) in enumerate(calls):
        messages.append(AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{index}"}]))
        messages.append(ToolMessage(content="done", tool_call_id=f"call-{index}", name=name))
    messages.append(AIMessage(content="Here it is."))
    assert threads.append_checkpoint_messages(thread_id, messages)


def _call_by_name(chat, name: str, args: dict) -> None:
    """One tool call through the platform wrapper, as the graph's tool node makes it (the tool stubbed)."""
    def call(graph, on_demand):
        request = SimpleNamespace(
            tool=None, tool_call={"name": name, "args": args, "id": "call-live"},
            override=lambda **change: SimpleNamespace(tool=change["tool"], tool_call=request.tool_call),
        )
        chat.agent._wrap_platform_tool_call(request, lambda req: "ran")
    chat.run(call)


def test_the_rule_by_tool_names_and_over_saved_messages():
    from row_bot.agent import STICKY_MEDIA_GROUPS, sticky_media_for, sticky_media_tools

    assert sticky_media_for(["workspace_read_file", "run_command"], small=False) == sticky_media_for([], small=False)
    assert sticky_media_for(["edit_video"], small=False).groups == STICKY_MEDIA_GROUPS
    # A small window keeps only what the chat has used, and present.
    small = sticky_media_for(["speak", "generate_image", "run_command"], small=True)
    assert (small.groups, small.tools) == (frozenset(), frozenset({"speak", "generate_image", "present"}))

    plain = [HumanMessage(content="hi"), AIMessage(content="", tool_calls=[
        {"name": "workspace_read_file", "args": {"file_path": "a.txt"}, "id": "1"}])]
    by_invoke = [AIMessage(content="", tool_calls=[
        {"name": "tool_invoke", "args": {"name": "speak", "arguments": {"text": "Hi"}}, "id": "2"}])]
    as_dicts = [{"type": "ai", "tool_calls": [{"name": "generate_image", "args": {"prompt": "a fox"}}]}]
    assert sticky_media_tools(plain, small=False) == sticky_media_for([], small=False)
    assert sticky_media_tools(by_invoke, small=False).groups == STICKY_MEDIA_GROUPS
    assert sticky_media_tools(by_invoke + as_dicts, small=True) == small


def test_a_chat_that_never_made_media_keeps_todays_tool_set(chat):
    fresh_bound, fresh_on_demand = chat.graph()
    _history(chat.thread_id, ("workspace_read_file", {"file_path": "notes.txt"}))

    bound, on_demand = chat.graph()

    assert set(bound) == set(fresh_bound) and set(on_demand) == set(fresh_on_demand)
    assert STICKY_SET <= set(on_demand) and not STICKY_SET & set(bound)


def test_after_a_media_call_in_the_history_the_next_turn_binds_the_media_groups(chat):
    _history(chat.thread_id, ("tool_invoke", {"name": "edit_video", "arguments": {"inputs": ["a.mp4"]}}))

    bound, on_demand = chat.graph()

    assert STICKY_SET <= set(bound)
    assert not STICKY_SET & set(on_demand)
    assert {"task_delete", "wikipedia", "row_bot_fix"} <= set(on_demand)  # the rest stays on demand
    assert chat.graph()[0].keys() == bound.keys()  # stable from turn to turn


def test_a_media_call_is_recorded_so_later_tool_lists_never_read_the_history(chat, monkeypatch):
    from row_bot import threads

    assert "speak" in chat.graph()[1]  # a new chat: nothing to bind yet, and that is recorded
    _call_by_name(chat, "speak", {"text": "Hello"})  # the turn's first media call
    reads: list[str] = []
    monkeypatch.setattr(threads, "get_latest_checkpoint_messages", lambda thread_id: reads.append(thread_id) or [])

    bound, on_demand = chat.graph()  # the next turn

    assert STICKY_SET <= set(bound) and not STICKY_SET & set(on_demand)
    assert reads == []


@pytest.mark.parametrize("window", [32_768, 65_536])  # the small profile and the middle tier
def test_a_small_window_binds_only_the_media_tools_the_chat_used(chat, monkeypatch, window):
    monkeypatch.setattr(chat.agent, "get_context_size", lambda *args, **kwargs: window)
    _history(chat.thread_id, ("edit_video", {"inputs": ["a.mp4"]}),
             ("tool_invoke", {"name": "speak", "arguments": {"text": "Hello"}}))

    bound, on_demand = chat.graph()

    assert {"edit_video", "speak"} <= set(bound)
    assert {"video", "make_music", "transcribe", "send_file", "media_find", "browser_record"} <= set(on_demand)


@pytest.mark.parametrize("window", [32_768, 200_000])
def test_a_chat_apps_turn_never_gets_present_back_from_the_sticky_set(chat, monkeypatch, window):
    """present shows a final in a Row-Bot chat; a chat app's turn delivers with send_file."""
    monkeypatch.setattr(chat.agent, "get_context_size", lambda *args, **kwargs: window)
    real_context = chat.agent._set_active_runtime_context
    monkeypatch.setattr(chat.agent, "_set_active_runtime_context",
                        lambda **kwargs: real_context(**{**kwargs, "runtime_surface": "channel"}))
    _history(chat.thread_id, ("speak", {"text": "Hello"}), ("present", {"files": ["Hello.mp3"]}))

    bound, on_demand = chat.graph()

    assert "speak" in bound and "present" not in bound and "present" not in on_demand


def test_a_turns_scope_still_refuses_a_bound_media_tool_it_leaves_out(chat):
    _history(chat.thread_id, ("speak", {"text": "Hello"}))

    def scoped_turn(graph, on_demand):
        bound = graph.tools.tools_by_name
        assert "speak" in bound and "speak" not in on_demand
        chat.agent._current_app_scope_var.set(SOUND_LEFT_OUT)
        # By name, and through tool_invoke (a call made before it was bound, or out of habit).
        return (bound["speak"].invoke({"text": "Hello again"}),
                bound["tool_invoke"].invoke({"name": "speak", "arguments": {"text": "And again"}}))

    direct, invoked = chat.run(scoped_turn)

    assert str(direct).startswith("BLOCKED: speak is not part of this turn.")
    assert str(invoked).startswith("BLOCKED: speak is not part of this turn."), json.loads(invoked)
    assert chat.asked == []  # refused before any approval card
