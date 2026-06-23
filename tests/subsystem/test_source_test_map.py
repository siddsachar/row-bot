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


def test_memory_tool_change_selects_tool_and_graph_coverage() -> None:
    selection = select_tests_for_changes(["src/row_bot/tools/memory_tool.py"])

    assert "tools_memory" in selection.matched_rules
    assert "tests/subsystem/tools" in selection.test_paths
    assert "tests/subsystem/knowledge_graph" in selection.test_paths
    assert "tests/test_memory_recall_uplift.py" in selection.test_paths


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
