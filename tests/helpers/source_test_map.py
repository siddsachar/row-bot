from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path


def normalize_repo_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").lstrip("./")


@dataclass(frozen=True)
class SourceTestRule:
    name: str
    patterns: tuple[str, ...]
    test_paths: tuple[str, ...]
    reason: str

    def matches(self, path: str | Path) -> bool:
        normalized = normalize_repo_path(path)
        return any(fnmatch(normalized, pattern) for pattern in self.patterns)


SOURCE_TEST_RULES: tuple[SourceTestRule, ...] = (
    SourceTestRule(
        "providers",
        ("src/row_bot/providers/**", "src/row_bot/tools/image_gen_tool.py", "src/row_bot/tools/video_gen_tool.py"),
        (
            "tests/contracts/test_provider_contract.py",
            "tests/subsystem/providers",
            "tests/test_provider_catalog.py",
            "tests/test_provider_runtime.py",
            "tests/test_provider_selection.py",
            "tests/test_provider_resolution.py",
            "tests/test_xai_media.py",
        ),
        "Provider and media runtime changes need fake-provider contracts plus focused provider regressions.",
    ),
    SourceTestRule(
        "tools_memory",
        ("src/row_bot/tools/memory_tool.py",),
        (
            "tests/subsystem/tools",
            "tests/subsystem/knowledge_graph",
            "tests/test_memory_recall_uplift.py",
            "tests/test_memory_evolution.py",
        ),
        "Memory tool changes need graph-backed tool contracts and deterministic memory regressions.",
    ),
    SourceTestRule(
        "channels",
        ("src/row_bot/channels/**",),
        (
            "tests/contracts/test_channel_contract.py",
            "tests/subsystem/channels",
            "tests/test_channel_goal_runtime.py",
            "tests/test_channel_streaming.py",
        ),
        "Channel adapter changes need fake-channel contracts and channel runtime regressions.",
    ),
    SourceTestRule(
        "workflows",
        ("src/row_bot/tasks.py", "src/row_bot/workflows/**", "src/row_bot/agents/**"),
        (
            "tests/subsystem/workflows",
            "tests/test_tasks_schema_recovery.py",
            "tests/test_workflow_delivery_defaults.py",
            "tests/test_goal_mode.py",
        ),
        "Task, workflow, and agent orchestration changes need resume/approval and schema coverage.",
    ),
    SourceTestRule(
        "mcp",
        ("src/row_bot/mcp_client/**",),
        (
            "tests/contracts/test_mcp_contract.py",
            "tests/subsystem/mcp",
            "tests/test_mcp_client.py",
        ),
        "MCP changes need fake transport, safety, and client coverage.",
    ),
    SourceTestRule(
        "memory_and_knowledge",
        (
            "src/row_bot/knowledge_graph.py",
            "src/row_bot/memory.py",
            "src/row_bot/memory_*.py",
            "src/row_bot/dream_cycle.py",
            "src/row_bot/wiki_vault.py",
            "src/row_bot/documents.py",
        ),
        (
            "tests/subsystem/knowledge_graph",
            "tests/subsystem/dream_cycle",
            "tests/test_memory_recall_uplift.py",
            "tests/test_memory_evolution.py",
            "tests/test_knowledge_audit.py",
        ),
        "Memory, knowledge graph, document, and Dream Cycle changes need deterministic recall and graph coverage.",
    ),
    SourceTestRule(
        "developer_studio",
        ("src/row_bot/developer/**",),
        (
            "tests/subsystem/developer",
            "tests/test_agent_write_locks.py",
            "tests/test_approval_policy.py",
        ),
        "Developer Studio changes need sandbox, import-gate, and approval-policy coverage.",
    ),
    SourceTestRule(
        "designer",
        ("src/row_bot/designer/**",),
        (
            "tests/subsystem/designer",
            "tests/test_developer_studio_phase2.py",
        ),
        "Designer changes need export and deterministic snapshot smoke coverage.",
    ),
    SourceTestRule(
        "installer_and_release",
        (
            ".github/workflows/**",
            "installer/**",
            "src/row_bot/updater.py",
            "scripts/coverage_summary.py",
            "scripts/smoke_app.py",
            "scripts/verify_runtime_dependencies.py",
            "scripts/export_locked_requirements.py",
            "scripts/app_payload_manifest.py",
            "scripts/cut_release.py",
            "pyproject.toml",
            "uv.lock",
            "requirements.txt",
        ),
        (
            "tests/subsystem/installer",
            "tests/subsystem/updater",
            "tests/contracts/installers",
        ),
        "Installer, release, dependency, and workflow changes need matrix and package contract coverage.",
    ),
    SourceTestRule(
        "tests",
        ("tests/**", "pytest.ini"),
        (
            "tests/subsystem/test_coverage_inventory.py",
            "tests/subsystem/test_legacy_inventory.py",
            "tests/subsystem/test_source_test_map.py",
        ),
        "Test architecture changes need inventory and source-to-test map validation.",
    ),
)


@dataclass(frozen=True)
class ChangeSelection:
    changed_files: tuple[str, ...]
    test_paths: tuple[str, ...]
    matched_rules: tuple[str, ...]
    unmatched_files: tuple[str, ...]
    reasons: tuple[str, ...]


def select_tests_for_changes(changed_files: list[str] | tuple[str, ...]) -> ChangeSelection:
    normalized_files = tuple(normalize_repo_path(path) for path in changed_files if str(path).strip())
    selected: list[str] = []
    matched_rules: list[str] = []
    reasons: list[str] = []
    unmatched: list[str] = []

    for changed in normalized_files:
        matches = [rule for rule in SOURCE_TEST_RULES if rule.matches(changed)]
        if not matches:
            unmatched.append(changed)
            continue
        for rule in matches:
            if rule.name not in matched_rules:
                matched_rules.append(rule.name)
                reasons.append(rule.reason)
            for test_path in rule.test_paths:
                if test_path not in selected:
                    selected.append(test_path)

    return ChangeSelection(
        changed_files=normalized_files,
        test_paths=tuple(selected),
        matched_rules=tuple(matched_rules),
        unmatched_files=tuple(unmatched),
        reasons=tuple(reasons),
    )
