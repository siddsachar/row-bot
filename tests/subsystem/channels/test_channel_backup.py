"""A channel message the main model can't answer is answered by the backup model (#140).

A plugin channel's turn goes through ``agent.stream_agent`` as every channel's does; the agent run
underneath is a fake that fails on the main model. Nothing is sent anywhere real.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.subsystem

CLOUD = "model:openai:gpt-5-mini"


@pytest.fixture
def store(tmp_path, reload_for_data_dir):
    tasks, threads = reload_for_data_dir(tmp_path / "data", "row_bot.tasks", "row_bot.threads")
    return SimpleNamespace(tasks=tasks, threads=threads)


def test_a_channel_message_is_answered_by_the_backup_with_nothing_extra(store, monkeypatch, tmp_path) -> None:
    import row_bot.agent as agent
    from row_bot.channels import runtime as channel_runtime
    from row_bot.plugins.api import ChannelInboundMessage, ChannelOutboundCallbacks
    from row_bot.plugins.channel_runtime import handle_plugin_channel_message
    from row_bot.providers import backup, saved_model_settings
    from row_bot.tools import registry as tool_registry

    monkeypatch.setattr(saved_model_settings, "SETTINGS_PATH", tmp_path / "model_settings.json")
    backup.save_setting(enabled=True, ref=CLOUD)
    models: list[str] = []

    async def no_goal(**_kwargs: Any):
        return channel_runtime.ChannelGoalRunResult(turns=0, status="idle", reason="")

    def run_on(_text: str, _enabled: list[str], config: dict, *, stop_event=None):
        model = config["configurable"].get("model_override") or agent.get_current_model()
        models.append(model)
        if model != CLOUD:
            backup.note_failure(ConnectionError("Failed to connect to Ollama."))
            yield ("error", "⚠️ API error: Failed to connect to Ollama.")
            return
        yield ("token", "Here you go.")
        yield ("done", "Here you go.")

    monkeypatch.setattr(tool_registry, "get_enabled_tools", lambda: [])
    monkeypatch.setattr(channel_runtime, "continue_channel_goal_after_turn_async", no_goal)
    from row_bot import models as model_settings

    for owner in (agent, model_settings):  # the person's main model: a local one
        monkeypatch.setattr(owner, "get_current_model", lambda: "model:ollama:qwen3:8b")
    monkeypatch.setattr(agent, "stream_agent_without_backup", run_on)
    sent: list[str] = []

    result = asyncio.run(handle_plugin_channel_message(
        plugin_id="teams-plugin",
        message=ChannelInboundMessage(channel_name="teams", external_conversation_id="conversation-1",
                                      sender_id="user-1", text="What's on today?"),
        callbacks=ChannelOutboundCallbacks(send_text=sent.append),
        channel=None,
        enabled_tool_names=[],
        stream=False,
    ))

    assert models[-1] == CLOUD and len(models) == 2
    assert result.answer == "Here you go."
    assert sent == ["Here you go."], "the reply carries no note; the app keeps it"
