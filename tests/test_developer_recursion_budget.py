from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_agent_iteration_budget_is_uniform_across_surfaces():
    agent_source = _read("src/row_bot/agent.py")
    settings_source = _read("src/row_bot/agent_settings.py")
    assert "max_iterations: int = 90" in settings_source
    assert "del is_background, is_developer" in agent_source
    assert "framework_recursion_limit(load_agent_runtime_settings().max_iterations)" in agent_source
    assert "RECURSION_LIMIT_CHAT = _DEFAULT_FRAMEWORK_LIMIT" in agent_source
    assert "RECURSION_LIMIT_TASK = _DEFAULT_FRAMEWORK_LIMIT" in agent_source
    assert "RECURSION_LIMIT_DEVELOPER = _DEFAULT_FRAMEWORK_LIMIT" in agent_source


def test_send_and_resume_use_checkpointed_budget_not_ui_recursion_constants():
    agent_source = _read("src/row_bot/agent.py")
    streaming_source = _read("src/row_bot/ui/streaming.py")
    assert "def _new_agent_graph_input" in agent_source
    assert "def _resume_agent_graph_config" in agent_source
    assert "validate_execution_budget(raw_budget)" in agent_source
    assert "recursion_limit_for_mode" not in streaming_source
    assert "RECURSION_LIMIT_CHAT" not in streaming_source


def test_developer_wind_down_and_recursion_error_are_checkpoint_oriented():
    source = _read("src/row_bot/agent.py")
    assert "Developer Studio step budget" in source
    assert "I reached the Developer Studio step budget for this turn" in source
    assert "Your workspace state, todos, and diffs are preserved" in source
    assert "current checkpoint" in source
    assert "percentage wind-down is no longer used" in source
