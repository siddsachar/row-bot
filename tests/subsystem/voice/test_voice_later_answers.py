"""An answer that lands in a later orchestration wake reaches the chat's open voice session (V3).

The turn ended while helpers worked; their parent's later pass writes the answer. A voice session open in
that chat hears it (and says it at its next pause); with none open, nothing is said.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.subsystem


def _orchestrator(tmp_path, reload_for_data_dir):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    *_, orchestrator = reload_for_data_dir(data_dir, "row_bot.tasks", "row_bot.agent_profiles",
                                           "row_bot.agent_settings", "row_bot.agent_runs",
                                           "row_bot.agent_orchestrator")
    orchestrator.set_test_executors(delivery=lambda *_args: True)
    orchestration = orchestrator.create_or_get_orchestration(
        parent_thread_id="voice-thread", parent_generation_id="generation-1",
        root_objective="Compare two flights.", model_ref="provider:model", approval_mode="block",
        runtime_surface="normal_chat", orchestration_version=2)
    return orchestrator, orchestration


@pytest.mark.parametrize("foreground", [False, True])
def test_a_background_pass_answer_reaches_the_open_voice_session(tmp_path, reload_for_data_dir, monkeypatch,
                                                                 foreground):
    from row_bot.application import client_platform

    orchestrator, orchestration = _orchestrator(tmp_path, reload_for_data_dir)
    service = client_platform.ClientPlatformService()
    monkeypatch.setattr(client_platform, "client_platform_service", service)
    heard: list[tuple] = []
    service.voice_listeners["voice-thread"] = lambda event, handle: heard.append(event)
    try:
        orchestrator.complete_parent_pass(orchestration["id"], "Both helpers are done: Oslo is cheaper.",
                                          continuation_state={"config": {"configurable": {}}},
                                          foreground=foreground)
    finally:
        orchestrator.set_test_executors()
    # A foreground pass is the running turn itself: the session already hears its tokens.
    assert heard == ([] if foreground else [("later_answer", "Both helpers are done: Oslo is cheaper.")])


def test_with_no_voice_session_open_a_later_answer_is_not_spoken(tmp_path, reload_for_data_dir, monkeypatch):
    from row_bot.application import client_platform

    orchestrator, orchestration = _orchestrator(tmp_path, reload_for_data_dir)
    service = client_platform.ClientPlatformService()
    monkeypatch.setattr(client_platform, "client_platform_service", service)
    heard: list[tuple] = []
    service.voice_listeners["another-thread"] = lambda event, handle: heard.append(event)
    try:
        result = orchestrator.complete_parent_pass(orchestration["id"], "Done.",
                                                   continuation_state={"config": {"configurable": {}}},
                                                   foreground=False)
    finally:
        orchestrator.set_test_executors()
    assert result.output_kind == "final" and heard == []
