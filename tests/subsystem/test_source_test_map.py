from __future__ import annotations

import pytest

from tests.helpers.source_test_map import SOURCE_TEST_RULES, select_tests_for_changes


pytestmark = pytest.mark.subsystem


def test_source_test_rules_have_unique_names_and_actionable_tests() -> None:
    names = [rule.name for rule in SOURCE_TEST_RULES]
    assert len(names) == len(set(names))
    for rule in SOURCE_TEST_RULES:
        assert rule.patterns
        assert rule.test_paths
        assert rule.reason.strip()


def test_provider_change_selects_provider_contract_and_focused_regressions() -> None:
    selection = select_tests_for_changes(["src/row_bot/providers/runtime.py"])

    assert "providers" in selection.matched_rules
    assert "tests/contracts/test_provider_contract.py" in selection.test_paths
    assert "tests/subsystem/providers" in selection.test_paths
    assert "tests/test_provider_runtime.py" in selection.test_paths
    assert not selection.unmatched_files


def test_selected_provider_sources_select_focused_legacy_regressions() -> None:
    selection = select_tests_for_changes(
        [
            "src/row_bot/providers/runtime.py",
            "src/row_bot/providers/selection.py",
            "src/row_bot/providers/catalog.py",
        ]
    )

    assert "tests/contracts/test_provider_contract.py" in selection.test_paths
    assert "tests/subsystem/providers" in selection.test_paths
    assert "tests/test_provider_runtime.py" in selection.test_paths
    assert "tests/test_provider_selection.py" in selection.test_paths
    assert "tests/test_provider_catalog.py" in selection.test_paths


def test_agent_profile_workflow_sources_select_profile_and_workflow_regressions() -> None:
    selection = select_tests_for_changes(
        [
            "src/row_bot/tools/agent_tool.py",
            "src/row_bot/tools/task_tool.py",
            "src/row_bot/tools/row_bot_status_tool.py",
            "src/row_bot/ui/task_dialog.py",
            "src/row_bot/agent_runs.py",
            "src/row_bot/agent_commands.py",
            "src/row_bot/agent_context.py",
            "src/row_bot/agent_runner.py",
            "src/row_bot/ui/streaming.py",
        ]
    )

    assert "agent_profile_workflows" in selection.matched_rules
    assert "tests/test_agent_commands.py" in selection.test_paths
    assert "tests/test_agent_context.py" in selection.test_paths
    assert "tests/test_agent_profiles.py" in selection.test_paths
    assert "tests/test_agent_runner.py" in selection.test_paths
    assert "tests/test_agent_tool.py" in selection.test_paths
    assert "tests/test_agent_runs.py" in selection.test_paths
    assert "tests/test_active_run_queue.py" in selection.test_paths
    assert "tests/test_chat_tool_trace_ui.py" in selection.test_paths
    assert "tests/test_row_bot_status_agents.py" in selection.test_paths
    assert "tests/subsystem/workflows" in selection.test_paths
    assert not selection.unmatched_files


def test_memory_tool_change_selects_tool_and_graph_coverage() -> None:
    selection = select_tests_for_changes(["src/row_bot/tools/memory_tool.py"])

    assert "tools_memory" in selection.matched_rules
    assert "tests/subsystem/tools" in selection.test_paths
    assert "tests/subsystem/knowledge_graph" in selection.test_paths
    assert "tests/test_memory_recall_uplift.py" in selection.test_paths


def test_prompt_context_change_selects_prompt_and_provider_regressions() -> None:
    selection = select_tests_for_changes([
        "src/row_bot/agent.py",
        "src/row_bot/prompts.py",
        "src/row_bot/self_knowledge.py",
        "src/row_bot/prompt_context.py",
        "src/row_bot/prompt_cache.py",
    ])

    assert "prompt_context" in selection.matched_rules
    assert "tests/subsystem/agents" in selection.test_paths
    assert "tests/subsystem/providers/test_prompt_cache_payloads.py" in selection.test_paths
    assert "tests/subsystem/providers/test_prompt_cache_metrics.py" in selection.test_paths
    assert "tests/test_provider_runtime.py" in selection.test_paths
    assert "tests/test_chat_only_runtime.py" in selection.test_paths
    assert "tests/test_memory_recall_uplift.py" in selection.test_paths
    assert not selection.unmatched_files


def test_updater_change_selects_updater_and_installer_contracts() -> None:
    selection = select_tests_for_changes(["src/row_bot/updater.py"])

    assert "installer_and_release" in selection.matched_rules
    assert "tests/subsystem/updater" in selection.test_paths
    assert "tests/subsystem/installer" in selection.test_paths
    assert "tests/contracts/installers" in selection.test_paths


def test_installer_change_selects_installer_contracts() -> None:
    selection = select_tests_for_changes([".github/workflows/release.yml", "installer/build_linux_app.sh"])

    assert "installer_and_release" in selection.matched_rules
    assert "tests/subsystem/installer" in selection.test_paths
    assert "tests/contracts/installers" in selection.test_paths


def test_unknown_change_is_reported_for_followup() -> None:
    selection = select_tests_for_changes(["docs/random-note.md"])

    assert selection.test_paths == ()
    assert selection.unmatched_files == ("docs/random-note.md",)
