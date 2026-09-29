from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

import pytest

from tests.helpers.source_test_map import SOURCE_TEST_RULES, normalize_repo_path, select_tests_for_changes

ROOT = Path(__file__).resolve().parents[2]


pytestmark = pytest.mark.subsystem


@pytest.mark.parametrize(('source', 'lane'), [
    ('src/row_bot/application/buddy_commands.py', 'tests/subsystem/buddy'),
    ('src/row_bot/application/capability_catalog_controls.py', 'tests/subsystem/mcp'),
    ('src/row_bot/application/document_job_commands.py', 'tests/subsystem/knowledge_graph'),
    ('src/row_bot/application/knowledge_relations.py', 'tests/subsystem/knowledge_graph'),
    ('src/row_bot/application/workspace_edit_commands.py', 'tests/subsystem/developer'),
])
def test_capability_commands_select_their_canonical_domain_regressions(source, lane):
    assert lane in select_tests_for_changes([source]).test_paths


def test_client_foundation_paths_have_routing_and_dependency_owners() -> None:
    selection = select_tests_for_changes(["frontend/src/main.tsx", "src/row_bot/client_assets.py", "scripts/dependency_requirements.py", "scripts/client_build.py", "scripts/docs/collect_inventory.py", "docs-site/docs/reference/generated/environment-and-config.mdx"])
    assert {"client_foundation", "normalized_dependency_requirements", "public_docs_inventory"} <= set(selection.matched_rules)
    assert "tests/subsystem/client_host" in selection.test_paths
    assert "tests/subsystem/test_dependency_requirement_consistency.py" in selection.test_paths
    assert "tests/docs" in selection.test_paths
    assert not selection.unmatched_files


def test_headless_platform_changes_select_cross_boundary_behavior_and_quality():
    selection = select_tests_for_changes([
        "src/row_bot/runtime/executions.py", "src/row_bot/api/v1/routes.py",
        "src/row_bot/conversation_resources.py", "scripts/check_client_platform_boundaries.py",
    ])
    assert "unified_client_platform" in selection.matched_rules
    assert "client_platform_quality" in selection.matched_rules
    assert "tests/contracts/client_platform" in selection.test_paths
    assert "tests/subsystem/developer/test_conversation_resources.py" in selection.test_paths
    assert not selection.unmatched_files


def test_shared_execution_changes_select_overlay_and_workflow_adapters() -> None:
    selection = select_tests_for_changes(["src/row_bot/tasks.py"])
    assert "tests/test_buddy_overlay.py" in selection.test_paths
    assert "tests/test_channel_workflow_model_routing.py" in selection.test_paths


@pytest.mark.parametrize("path", [
    "frontend/src/features/shell/Conversation.tsx",
    "frontend/src/features/panels/WorkspaceInspector.tsx",
    "frontend/src/api/controller.ts", "frontend/tests/browser/unified-history.spec.ts",
    "contracts/client-platform/v1/schema/DraftView.schema.json",
    "scripts/generate_client_platform_contracts.py",
    "tests/browser/client_workspace/fixture_app.py",
    "tests/browser/client_workspace/run_browser.py",
    "docs/client-platform/unified-workspace.md",
    "src/row_bot/application/workspace_setup.py",
    "src/row_bot/application/conversation_drafts.py",
    "src/row_bot/application/conversation_search.py",
    "src/row_bot/application/folder_selections.py",
    "src/row_bot/application/client_platform.py",
    "src/row_bot/api/v1/routes.py", "src/row_bot/api/v1/schemas.py",
    "src/row_bot/runtime/admissions.py", "src/row_bot/runtime/checkpoint_reader.py",
    "src/row_bot/client_assets.py", "src/row_bot/conversation_resources.py",
    "src/row_bot/threads.py", "src/row_bot/agent_orchestrator.py",
    "src/row_bot/designer/client_service.py", "src/row_bot/designer/html_ops.py",
    "src/row_bot/designer/interaction.py", "src/row_bot/designer/preview.py", "src/row_bot/designer/runtime/loader.py",
    "src/row_bot/designer/state.py", "src/row_bot/designer/storage.py",
    "src/row_bot/developer/client_workspace.py", "src/row_bot/developer/inspector_snapshot.py",
    "src/row_bot/developer/review.py", "src/row_bot/developer/state.py",
    "src/row_bot/developer/storage.py",
    "src/row_bot/voice/actions.py", "src/row_bot/voice/coordinator.py",
])
def test_unified_workspace_changes_select_shared_domain_owners(path: str) -> None:
    selection = select_tests_for_changes([path])
    assert "unified_workspace_composition" in selection.matched_rules
    assert {
        "tests/subsystem/client_platform", "tests/subsystem/client_protocol",
        "tests/subsystem/developer", "tests/subsystem/designer",
        "tests/subsystem/agents/test_client_steering.py",
        "tests/subsystem/mobile/test_voice_client_lifecycle.py",
        "tests/test_developer_workspace_threads.py", "tests/test_agent_write_locks.py",
    } <= set(selection.test_paths)
    assert not selection.unmatched_files


def test_source_test_rules_have_unique_names_and_actionable_tests() -> None:
    names = [rule.name for rule in SOURCE_TEST_RULES]
    assert len(names) == len(set(names))
    for rule in SOURCE_TEST_RULES:
        assert rule.patterns
        assert rule.test_paths
        assert rule.reason.strip()


def _names_something(pattern: str) -> bool:
    if "*" not in pattern:
        # normalize_repo_path strips leading dots, so ".dockerignore" is mapped as "dockerignore".
        return (ROOT / pattern).exists() or (ROOT / f".{pattern}").exists()
    head = pattern.split("*", 1)[0]
    base = ROOT / head.rsplit("/", 1)[0] if "/" in head else ROOT
    return any(fnmatch(normalize_repo_path(path.relative_to(ROOT).as_posix()), pattern)
               for path in base.rglob("*") if path.is_file())


def test_every_mapped_source_and_test_path_exists() -> None:
    stale = [(rule.name, path) for rule in SOURCE_TEST_RULES
             for path in (*rule.patterns, *rule.test_paths) if not _names_something(path)]
    assert stale == []


def test_thread_cleanup_change_selects_cross_subsystem_deletion_contracts() -> None:
    selection = select_tests_for_changes(
        [
            "src/row_bot/thread_cleanup.py",
            "src/row_bot/agent_runner.py",
            "src/row_bot/channels/telegram.py",
        ]
    )

    assert "thread_deletion_cleanup" in selection.matched_rules
    assert "tests/subsystem/threads" in selection.test_paths
    assert "tests/subsystem/designer" in selection.test_paths
    assert "tests/subsystem/developer" in selection.test_paths
    assert "tests/subsystem/workflows" in selection.test_paths
    assert "tests/subsystem/channels" in selection.test_paths
    assert not selection.unmatched_files


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


def test_voice_runtime_change_selects_offline_and_provider_regressions() -> None:
    selection = select_tests_for_changes(["src/row_bot/voice/local_provider.py"])

    assert "voice_runtime" in selection.matched_rules
    assert "tests/test_voice_sensevoice.py" in selection.test_paths
    assert "tests/test_voice_providers.py" in selection.test_paths
    assert "tests/test_voice_coordinator.py" in selection.test_paths
    assert not selection.unmatched_files


def test_agent_profile_workflow_sources_select_profile_and_workflow_regressions() -> None:
    selection = select_tests_for_changes(
        [
            "src/row_bot/tools/agent_tool.py",
            "src/row_bot/tools/task_tool.py",
            "src/row_bot/tools/row_bot_status_tool.py",
            "src/row_bot/agent_runs.py",
            "src/row_bot/agent_commands.py",
            "src/row_bot/agent_context.py",
            "src/row_bot/agent_runner.py",
        ]
    )

    assert "agent_profile_workflows" in selection.matched_rules
    assert "tests/test_agent_commands.py" in selection.test_paths
    assert "tests/test_agent_context.py" in selection.test_paths
    assert "tests/test_agent_profiles.py" in selection.test_paths
    assert "tests/test_agent_runner.py" in selection.test_paths
    assert "tests/test_agent_tool.py" in selection.test_paths
    assert "tests/test_agent_runs.py" in selection.test_paths
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


@pytest.mark.parametrize("path", ["src/row_bot/launcher.py", "src/row_bot/server.py", "src/row_bot/app.py"])
def test_launcher_and_server_changes_select_startup_regressions(path: str) -> None:
    selection = select_tests_for_changes([path])

    assert "startup_runtime" in selection.matched_rules
    assert "tests/test_app_port.py" in selection.test_paths
    assert "tests/test_startup_hardening.py" in selection.test_paths
    assert "tests/subsystem/client_host/test_server_entry.py" in selection.test_paths
    assert "tests/subsystem/mobile" in selection.test_paths
    assert "tests/integration/mobile" in selection.test_paths
    assert not selection.unmatched_files


def test_buddy_overlay_change_selects_state_ui_and_stop_regressions() -> None:
    selection = select_tests_for_changes(
        ["src/row_bot/buddy/overlay.py"]
    )

    assert "buddy_lifecycle" in selection.matched_rules
    assert "tests/test_buddy_core.py" in selection.test_paths
    assert "tests/test_buddy_ui.py" in selection.test_paths
    assert "tests/test_buddy_overlay.py" in selection.test_paths
    assert not selection.unmatched_files


def test_react_buddy_overlay_change_selects_the_host_bridge_and_buddy_suites() -> None:
    selection = select_tests_for_changes(
        ["frontend/src/overlay/BuddyOverlay.tsx", "frontend/buddy-overlay.html", "src/row_bot/buddy/native_host.py"]
    )

    assert "buddy_lifecycle" in selection.matched_rules
    assert "tests/subsystem/buddy" in selection.test_paths
    assert "tests/subsystem/client_host/test_native.py" in selection.test_paths
    assert "tests/test_buddy_ui.py" in selection.test_paths
    assert not selection.unmatched_files


def test_app_port_change_selects_startup_and_mobile_regressions() -> None:
    selection = select_tests_for_changes(["src/row_bot/app_port.py"])

    assert selection.matched_rules == ("startup_runtime",)
    assert "tests/test_app_port.py" in selection.test_paths
    assert "tests/test_startup_hardening.py" in selection.test_paths
    assert "tests/subsystem/mobile" in selection.test_paths
    assert "tests/integration/mobile" in selection.test_paths
    assert len(selection.test_paths) == len(set(selection.test_paths))
    assert not selection.unmatched_files


def test_mobile_change_selects_mobile_owner_access_regressions() -> None:
    selection = select_tests_for_changes(["src/row_bot/mobile/routes.py"])

    assert "mobile_owner_access" in selection.matched_rules
    assert "tests/subsystem/mobile" in selection.test_paths
    assert "tests/integration/mobile" in selection.test_paths
    assert not selection.unmatched_files


def test_computer_tool_guide_selects_runtime_and_prompt_ownership() -> None:
    selection = select_tests_for_changes(
        ["tool_guides/computer_use_guide/SKILL.md"]
    )

    assert "computer_use" in selection.matched_rules
    assert "tests/subsystem/computer_use" in selection.test_paths
    assert "tests/integration/computer_use" in selection.test_paths
    assert "tests/test_skills_activation.py" in selection.test_paths
    assert (
        "tests/integration/computer_use/test_browser_computer_routing_guidance.py"
        in selection.test_paths
    )
    assert (
        "tests/subsystem/providers/test_prompt_cache_payloads.py"
        in selection.test_paths
    )
    assert not selection.unmatched_files


def test_computer_architecture_document_selects_computer_use_ownership() -> None:
    selection = select_tests_for_changes(["docs/ARCHITECTURE.md"])

    assert "computer_use" in selection.matched_rules
    assert "tests/subsystem/computer_use" in selection.test_paths
    assert "tests/integration/computer_use" in selection.test_paths
    assert not selection.unmatched_files


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


def test_context_compaction_crosses_ui_persistence_and_channel_streaming_ownership() -> None:
    selection = select_tests_for_changes([
        "src/row_bot/agent.py",
        "src/row_bot/threads.py",
        "src/row_bot/channels/streaming.py",
    ])

    assert "context_window_compaction" in selection.matched_rules
    assert "tests/subsystem/agents/test_context_preparation.py" in selection.test_paths
    assert "tests/subsystem/agents/test_context_compaction.py" in selection.test_paths
    assert "tests/subsystem/agents/test_context_persistence.py" in selection.test_paths
    assert "tests/test_agent_context.py" in selection.test_paths
    assert "tests/test_agent_readiness.py" in selection.test_paths
    assert "tests/test_provider_runtime.py" in selection.test_paths
    assert "tests/subsystem/channels/test_channel_streaming_engine.py" in selection.test_paths
    assert "tests/subsystem/mobile" in selection.test_paths
    assert not selection.unmatched_files


def test_updater_change_selects_updater_and_installer_contracts() -> None:
    selection = select_tests_for_changes(["src/row_bot/updater.py"])

    assert "installer_and_release" in selection.matched_rules
    assert "tests/subsystem/updater" in selection.test_paths
    assert "tests/subsystem/installer" in selection.test_paths
    assert "tests/contracts/installers" in selection.test_paths


def test_plugin_change_selects_plugin_contracts_and_integration_lanes() -> None:
    selection = select_tests_for_changes(
        [
            "src/row_bot/plugins/manifest.py",
            "src/row_bot/plugins/mcp.py",
            "src/row_bot/app.py",
            "src/row_bot/tools/row_bot_status_tool.py",
            "src/row_bot/status_checks.py",
            "scripts/build_plugin_index.py",
            "docs/PLUGIN_SYSTEM_V2.md",
            "docs/ARCHITECTURE.md",
            "examples/plugins/hello-tool/plugin.json",
        ]
    )

    assert "plugins" in selection.matched_rules
    assert "tests/contracts/plugins/test_plugin_api_contract.py" in selection.test_paths
    assert "tests/subsystem/plugins" in selection.test_paths
    assert "tests/subsystem/mcp/test_mcp_runtime_tools.py" in selection.test_paths
    assert "tests/subsystem/channels/test_channel_registry.py" in selection.test_paths
    assert "tests/test_row_bot_status_agents.py" in selection.test_paths
    assert not selection.unmatched_files


def test_migration_change_selects_migration_wizard_regressions() -> None:
    selection = select_tests_for_changes(["src/row_bot/migration/planner.py"])

    assert "migration" in selection.matched_rules
    assert "tests/test_migration_core.py" in selection.test_paths
    assert "tests/test_migration_detection.py" in selection.test_paths
    assert "tests/test_migration_planner.py" in selection.test_paths
    assert "tests/test_migration_apply.py" in selection.test_paths
    assert not selection.unmatched_files


def test_installer_change_selects_installer_contracts() -> None:
    selection = select_tests_for_changes([".github/workflows/release.yml", "installer/build_linux_app.sh"])

    assert "installer_and_release" in selection.matched_rules
    assert "tests/subsystem/installer" in selection.test_paths
    assert "tests/contracts/installers" in selection.test_paths


def test_unknown_change_is_reported_for_followup() -> None:
    selection = select_tests_for_changes(["docs/random-note.md"])

    assert selection.test_paths == ()
    assert selection.unmatched_files == ("docs/random-note.md",)


def test_isolation_change_selects_notification_producing_runtime_paths() -> None:
    selection = select_tests_for_changes(["tests/conftest.py"])

    assert "test_isolation" in selection.matched_rules
    assert "tests/test_chat_only_runtime.py" in selection.test_paths
    assert "tests/subsystem/workflows/test_delegate_agent_step.py" in selection.test_paths
    assert "tests/test_buddy_core.py" in selection.test_paths
    assert not selection.unmatched_files
