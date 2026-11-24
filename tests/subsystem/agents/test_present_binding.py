"""present shows a final in a Row-Bot chat; a chat app's turn (Telegram, WhatsApp, Slack, Discord) never binds it:
there, send_file delivers. Row-Bot's real tool assembly for a plain chat, as the tool budget test builds it."""
from __future__ import annotations

import contextvars

import pytest

from tests.subsystem.agents.test_default_tool_budget import MODEL, plain_chat  # noqa: F401

pytestmark = pytest.mark.subsystem


def _bound(chat, surface: str) -> set[str]:
    def turn():
        chat.agent._set_active_runtime_context(
            thread_id=chat.thread_id, runtime_surface=surface, requested_runtime_mode="agent",
            selected_runtime_mode="agent", approval_mode=chat.approval, generation_id=f"{chat.thread_id}-{surface}",
            model_override=MODEL, enabled_tool_names=tuple(chat.enabled), tool_allowlist=None,
        )
        graph = chat.agent.get_agent_graph(list(chat.enabled))
        return set(graph.tools.tools_by_name) | set(chat.agent._current_on_demand_tools_var.get())
    return contextvars.copy_context().run(turn)


def test_a_chat_app_turn_never_binds_present_and_a_row_bot_chat_does(plain_chat):  # noqa: F811
    assert "present" in _bound(plain_chat, "normal_chat")
    channel = _bound(plain_chat, "channel")
    assert "present" not in channel and "send_file" in channel
