from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_developer_recursion_limit_is_defined_separately():
    source = _read("agent.py")
    assert "RECURSION_LIMIT_CHAT = 50" in source
    assert "RECURSION_LIMIT_TASK = 100" in source
    assert "RECURSION_LIMIT_DEVELOPER = 120" in source
    assert "def recursion_limit_for_mode" in source
    assert "if is_developer:" in source
    assert "return RECURSION_LIMIT_DEVELOPER" in source


def test_developer_streaming_uses_mode_specific_limit_for_send_and_resume():
    source = _read("ui/streaming.py")
    assert "recursion_limit_for_mode" in source
    assert "recursion_limit_for_mode(is_developer=is_developer)" in source
    assert source.count('"recursion_limit": recursion_limit') >= 2
    assert source.count("is_developer = bool(getattr(state, \"active_developer_workspace_id\", None))") >= 2
    assert "RECURSION_LIMIT_CHAT" not in source


def test_developer_wind_down_and_recursion_error_are_checkpoint_oriented():
    source = _read("agent.py")
    assert "Developer Studio step budget" in source
    assert "Checkpoint the coding task now" in source
    assert "files inspected or changed" in source
    assert "I reached the Developer Studio step budget for this turn" in source
    assert "Your workspace state, todos, and diffs are preserved" in source
