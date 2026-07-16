from pathlib import Path


def test_models_tab_owns_one_collapsed_agent_runtime_section_in_all_states() -> None:
    source = Path("src/row_bot/ui/settings.py").read_text(encoding="utf-8")

    assert "def _render_agent_runtime_settings()" in source
    assert source.count("_render_agent_runtime_settings()") == 4
    assert "Agent runtime & delegation" in source
    assert "Maximum work rounds" in source
    assert "Maximum nested agent levels" in source
    assert "Active children per parent" in source
    assert "Active children across the app" in source
    assert "Child active-time limit" in source
    assert "Restore recommended defaults" in source
    assert 'with expansion.add_slot("header")' in source
    assert "row-bot-agent-runtime-expansion" in source
    assert "Optional limits for long-running work and delegated child agents" in source
    assert "cursor-help" in source
    assert "This is not a per-tool-call timeout." in source
    assert "A value of 1 allows children but not grandchildren." in source
    assert "children wait in the queue instead of being rejected." in source
    assert "Optional active execution limit for each child." in source
    assert "not counted; 0 leaves the child without a time limit." in source


def test_agent_runtime_settings_ui_has_no_profile_or_workflow_override() -> None:
    source = Path("src/row_bot/ui/settings.py").read_text(encoding="utf-8")
    section = source.split("def _render_agent_runtime_settings()", 1)[1].split(
        "def _render_models_tab_content", 1
    )[0]

    assert "profile" not in section.lower()
    assert "workflow" not in section.lower()
    assert "model_override" not in section
