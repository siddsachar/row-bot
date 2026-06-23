from __future__ import annotations

from dataclasses import dataclass, field


LEGACY_COVERAGE_FILES = {
    "tests/test_suite.py",
    "tests/integration_tests.py",
    "tests/test_memory_e2e.py",
}

PRIORITY_SUBSYSTEMS = {
    "providers",
    "channels",
    "workflows",
    "mcp",
    "knowledge_graph",
    "memory_extraction",
    "tools_memory",
    "dream_cycle",
    "developer_studio",
    "designer",
    "cli_installer",
    "updater",
}

ALLOWED_STATUSES = {"covered", "retired-shim", "opt-in-live", "planned"}


@dataclass(frozen=True)
class CoverageEntry:
    subsystem: str
    legacy_files: tuple[str, ...]
    replacement_files: tuple[str, ...]
    status: str = "covered"
    notes: str = ""
    invariants: tuple[str, ...] = field(default_factory=tuple)


COVERAGE_INVENTORY: tuple[CoverageEntry, ...] = (
    CoverageEntry(
        "providers",
        ("tests/test_suite.py",),
        (
            "tests/contracts/test_provider_contract.py",
            "tests/subsystem/providers/test_provider_resolution.py",
            "tests/subsystem/providers/test_provider_catalog_edges.py",
            "tests/subsystem/providers/test_provider_selection_edges.py",
            "tests/subsystem/providers/test_provider_runtime_edges.py",
            "tests/test_provider_catalog.py",
            "tests/test_provider_runtime.py",
            "tests/test_provider_selection.py",
        ),
        invariants=("fake provider never makes network calls", "live provider matrix remains opt-in"),
    ),
    CoverageEntry(
        "channels",
        ("tests/integration_tests.py",),
        (
            "tests/contracts/test_channel_contract.py",
            "tests/subsystem/channels/test_channel_registry.py",
            "tests/test_channel_goal_runtime.py",
        ),
        invariants=("fake channel records outbound sends in memory", "approval messages can be resolved cross-channel"),
    ),
    CoverageEntry(
        "workflows",
        ("tests/test_suite.py", "tests/integration_tests.py"),
        (
            "tests/subsystem/workflows/test_workflow_graph.py",
            "tests/subsystem/workflows/test_workflow_approvals.py",
            "tests/test_workflow_delivery_defaults.py",
        ),
        invariants=("approval resume tokens are single-use", "workflow state uses isolated temp data dirs"),
    ),
    CoverageEntry(
        "mcp",
        ("tests/integration_tests.py",),
        (
            "tests/contracts/test_mcp_contract.py",
            "tests/subsystem/mcp/test_mcp_transports.py",
            "tests/subsystem/mcp/test_mcp_safety.py",
            "tests/subsystem/mcp/test_mcp_runtime_helpers.py",
            "tests/subsystem/mcp/test_mcp_runtime_tools.py",
            "tests/test_mcp_client.py",
        ),
        invariants=("real MCP/network checks are opt-in", "destructive tools default to approval-gated"),
    ),
    CoverageEntry(
        "knowledge_graph",
        ("tests/test_memory_e2e.py",),
        (
            "tests/subsystem/knowledge_graph/test_knowledge_graph_migration.py",
            "tests/subsystem/knowledge_graph/test_knowledge_graph_recall.py",
            "tests/subsystem/knowledge_graph/test_knowledge_graph_relations.py",
            "tests/subsystem/knowledge_graph/test_knowledge_graph_edges.py",
            "tests/test_memory_recall_uplift.py",
        ),
        invariants=("legacy memory migration is covered", "recall ranking is deterministic under fake semantic search"),
    ),
    CoverageEntry(
        "memory_extraction",
        ("tests/test_memory_e2e.py",),
        (
            "tests/subsystem/memory/test_memory_extraction_state.py",
            "tests/subsystem/memory/test_memory_extraction_dedup.py",
            "tests/subsystem/regression/test_memory_graph_regressions.py",
        ),
        invariants=("extraction state uses isolated temp data dirs", "LLM extraction is faked or monkeypatched"),
    ),
    CoverageEntry(
        "tools_memory",
        ("tests/test_memory_e2e.py",),
        (
            "tests/subsystem/tools/test_memory_tool_contracts.py",
            "tests/subsystem/knowledge_graph",
            "tests/test_memory_recall_uplift.py",
        ),
        invariants=("memory tool tests use fake graph/model behavior", "tool deletes stay deterministic"),
    ),
    CoverageEntry(
        "dream_cycle",
        ("tests/test_memory_e2e.py",),
        (
            "tests/subsystem/dream_cycle/test_dream_cycle_scheduling.py",
            "tests/subsystem/dream_cycle/test_dream_cycle_consolidation.py",
            "tests/subsystem/dream_cycle/test_dream_cycle_gates_and_journal.py",
            "tests/subsystem/dream_cycle/test_dream_cycle_phase_helpers.py",
        ),
        invariants=("nightly work is gated by idle/busy checks", "LLM consolidation is faked"),
    ),
    CoverageEntry(
        "developer_studio",
        ("tests/test_suite.py",),
        (
            "tests/subsystem/developer/test_developer_sandbox.py",
            "tests/subsystem/developer/test_developer_import_gate.py",
            "tests/subsystem/developer/test_developer_runtime_commands.py",
            "tests/subsystem/developer/test_developer_runtime_processes.py",
            "tests/test_developer_studio_phase2.py",
        ),
        invariants=("sandbox imports require approval", "command action classification is conservative"),
    ),
    CoverageEntry(
        "designer",
        ("tests/test_suite.py",),
        (
            "tests/subsystem/designer/test_designer_export_snapshots.py",
            "tests/subsystem/designer/test_designer_thumbnail.py",
            "tests/subsystem/designer/test_designer_export_helpers.py",
        ),
        invariants=("HTML export snapshot is deterministic", "snapshot smoke stays browser-free"),
    ),
    CoverageEntry(
        "cli_installer",
        ("tests/test_suite.py", "tests/integration_tests.py"),
        (
            "tests/subsystem/installer/test_cli_smoke_contracts.py",
            "tests/subsystem/installer/test_installer_metadata.py",
            "scripts/smoke_app.py",
        ),
        invariants=("launcher smoke uses temp data dirs", "installer checks do not hit network"),
    ),
    CoverageEntry(
        "updater",
        ("tests/test_suite.py", "tests/integration_tests.py"),
        (
            "tests/subsystem/updater/test_updater_contracts.py",
            "tests/subsystem/updater/test_updater_state_and_release.py",
            "tests/subsystem/updater/test_updater_download_install.py",
            "tests/subsystem/installer",
            "tests/contracts/installers",
        ),
        invariants=("release metadata fetches are monkeypatched", "installer handoff/signing checks remain fake by default"),
    ),
    CoverageEntry(
        "legacy_retired_shims",
        ("tests/test_suite.py", "tests/integration_tests.py", "tests/test_memory_e2e.py"),
        (
            "tests/test_suite.py",
            "tests/integration_tests.py",
            "tests/test_memory_e2e.py",
            "tests/helpers/legacy_inventory.py",
            "tests/helpers/legacy_inventory_snapshot.py",
        ),
        status="retired-shim",
        notes="The old monoliths are compatibility shims; substantive coverage has moved to focused pytest suites.",
    ),
    CoverageEntry(
        "live_provider",
        tuple(),
        ("tests/e2e/test_live_provider_matrix.py",),
        status="opt-in-live",
        notes="Live cloud/local provider calls stay behind the live_provider marker.",
    ),
)


def inventory_by_subsystem() -> dict[str, list[CoverageEntry]]:
    result: dict[str, list[CoverageEntry]] = {}
    for entry in COVERAGE_INVENTORY:
        result.setdefault(entry.subsystem, []).append(entry)
    return result
