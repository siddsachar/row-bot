from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path


LEGACY_FILES = (
    "tests/test_suite.py",
    "tests/integration_tests.py",
    "tests/test_memory_e2e.py",
)

LEGACY_STATUSES = {"mapped", "covered", "retained-smoke", "removed", "opt-in-live"}
SNAPSHOT_MIN_SECTION_COUNT = 100


@dataclass(frozen=True)
class LegacySection:
    legacy_file: str
    heading: str
    start_line: int
    end_line: int
    subsystem: str
    replacement_paths: tuple[str, ...]
    status: str
    verification_command: str
    notes: str = ""


@dataclass(frozen=True)
class LegacyTarget:
    subsystem: str
    replacement_paths: tuple[str, ...]
    verification_command: str
    status: str = "mapped"


TARGETS: dict[str, LegacyTarget] = {
    "app_startup": LegacyTarget(
        "app_startup",
        (
            "tests/test_dependency_metadata.py",
            "tests/test_optional_dependency_imports.py",
            "tests/test_startup_hardening.py",
            "tests/test_app_port.py",
            "tests/subsystem/installer",
        ),
        "uv run python scripts/run_test_matrix.py fast",
        status="covered",
    ),
    "tools": LegacyTarget(
        "tools",
        (
            "tests/test_agent_tool.py",
            "tests/test_agent_tool_catalog.py",
            "tests/test_tool_config_isolation.py",
            "tests/test_wikipedia_tool.py",
            "tests/test_row_bot_tool_ids.py",
        ),
        "uv run python -m pytest tests/test_agent_tool.py tests/test_agent_tool_catalog.py tests/test_tool_config_isolation.py tests/test_wikipedia_tool.py tests/test_row_bot_tool_ids.py -q",
        status="covered",
    ),
    "channels": LegacyTarget(
        "channels",
        ("tests/contracts/test_channel_contract.py", "tests/subsystem/channels", "tests/test_tunnel_manager.py"),
        "uv run python -m pytest tests/contracts/test_channel_contract.py tests/subsystem/channels -q",
        status="covered",
    ),
    "workflows": LegacyTarget(
        "workflows",
        ("tests/subsystem/workflows", "tests/test_workflow_delivery_defaults.py"),
        "uv run python -m pytest tests/subsystem/workflows tests/test_workflow_delivery_defaults.py -q",
        status="covered",
    ),
    "providers": LegacyTarget(
        "providers",
        (
            "tests/contracts/test_provider_contract.py",
            "tests/subsystem/providers",
            "tests/test_provider_runtime.py",
            "tests/test_streaming_batcher.py",
        ),
        "uv run python -m pytest tests/contracts/test_provider_contract.py tests/subsystem/providers tests/test_provider_runtime.py -q",
        status="covered",
    ),
    "mcp": LegacyTarget(
        "mcp",
        ("tests/contracts/test_mcp_contract.py", "tests/subsystem/mcp", "tests/test_mcp_client.py"),
        "uv run python -m pytest tests/contracts/test_mcp_contract.py tests/subsystem/mcp tests/test_mcp_client.py -q",
        status="covered",
    ),
    "knowledge_graph": LegacyTarget(
        "knowledge_graph",
        ("tests/subsystem/knowledge_graph", "tests/test_memory_recall_uplift.py", "tests/test_knowledge_audit.py"),
        "uv run python -m pytest tests/subsystem/knowledge_graph tests/test_memory_recall_uplift.py tests/test_knowledge_audit.py -q",
        status="covered",
    ),
    "dream_cycle": LegacyTarget(
        "dream_cycle",
        ("tests/subsystem/dream_cycle",),
        "uv run python -m pytest tests/subsystem/dream_cycle -q",
        status="covered",
    ),
    "documents": LegacyTarget(
        "documents",
        ("tests/subsystem/documents", "tests/integration/document_knowledge"),
        "uv run python -m pytest tests/subsystem/documents tests/integration/document_knowledge -q",
    ),
    "wiki_vault": LegacyTarget(
        "wiki_vault",
        ("tests/integration/wiki_vault", "tests/subsystem/knowledge_graph"),
        "uv run python -m pytest tests/integration/wiki_vault tests/subsystem/knowledge_graph -q",
        status="covered",
    ),
    "skills": LegacyTarget(
        "skills",
        (
            "tests/test_skills_activation.py",
            "tests/test_skills_hub.py",
            "tests/test_skills_hub_search.py",
            "tests/test_skills_hub_sources.py",
            "tests/test_skill_pinning.py",
        ),
        "uv run python -m pytest tests/test_skills_activation.py tests/test_skills_hub.py tests/test_skills_hub_search.py tests/test_skills_hub_sources.py tests/test_skill_pinning.py -q",
        status="covered",
    ),
    "plugins": LegacyTarget(
        "plugins",
        ("tests/contracts/plugins", "tests/subsystem/plugins"),
        "uv run python -m pytest tests/contracts/plugins tests/subsystem/plugins -q",
        status="covered",
    ),
    "security": LegacyTarget(
        "security",
        ("tests/test_approval_policy.py", "tests/test_agent_approvals.py", "tests/test_agent_write_locks.py"),
        "uv run python -m pytest tests/test_approval_policy.py tests/test_agent_approvals.py tests/test_agent_write_locks.py -q",
        status="covered",
    ),
    "logging": LegacyTarget(
        "logging",
        ("tests/subsystem/logging/test_persistent_logging.py",),
        "uv run python -m pytest tests/subsystem/logging -q",
        status="covered",
    ),
    "designer": LegacyTarget(
        "designer",
        ("tests/subsystem/designer",),
        "uv run python -m pytest tests/subsystem/designer -q",
        status="covered",
    ),
    "developer_studio": LegacyTarget(
        "developer_studio",
        ("tests/subsystem/developer", "tests/test_developer_studio_phase2.py"),
        "uv run python -m pytest tests/subsystem/developer tests/test_developer_studio_phase2.py -q",
        status="covered",
    ),
    "updater": LegacyTarget(
        "updater",
        ("tests/subsystem/updater/test_updater_contracts.py",),
        "uv run python -m pytest tests/subsystem/updater -q",
        status="covered",
    ),
    "voice": LegacyTarget(
        "voice",
        (
            "tests/test_voice_actions.py",
            "tests/test_voice_agent_bridge.py",
            "tests/test_voice_coordinator.py",
            "tests/test_voice_runtime.py",
            "tests/test_voice_speech_policy.py",
        ),
        "uv run python -m pytest tests/test_voice_actions.py tests/test_voice_agent_bridge.py tests/test_voice_coordinator.py tests/test_voice_runtime.py tests/test_voice_speech_policy.py -q",
        status="covered",
    ),
    "agents": LegacyTarget(
        "agents",
        (
            "tests/test_agent_context.py",
            "tests/test_agent_runner.py",
            "tests/test_agent_runs.py",
            "tests/test_agent_profiles.py",
            "tests/test_goal_mode.py",
            "tests/test_row_bot_status_agents.py",
            "tests/test_home_status_workflow_buddy.py",
        ),
        "uv run python -m pytest tests/test_agent_context.py tests/test_agent_runner.py tests/test_agent_runs.py tests/test_agent_profiles.py tests/test_goal_mode.py -q",
        status="covered",
    ),
    "legacy_regression": LegacyTarget(
        "legacy_regression",
        ("tests/subsystem/regression", "tests/integration/wiki_vault"),
        "uv run python -m pytest tests/subsystem/regression tests/integration/wiki_vault -q",
        status="covered",
    ),
}


CLASSIFICATION_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("developer studio", "sandbox", "import gate"), "developer_studio"),
    (("designer", "export filename"), "designer"),
    (("auto-update", "update state", "updater"), "updater"),
    (("prompt-injection", "approval", "defence", "security"), "security"),
    (("logging", "persistent logging"), "logging"),
    (("plugin", "marketplace"), "plugins"),
    (("skill", "bundled skills"), "skills"),
    (("dream",), "dream_cycle"),
    (("wiki vault", "wiki tool", "vault sync"), "wiki_vault"),
    (("document", "knowledge extraction", "faiss", "semantic search", "memory", "knowledge graph", "graph", "triple-based extraction", "auto-recall"), "knowledge_graph"),
    (("channel", "telegram", "whatsapp", "sms", "discord", "slack", "tunnel", "webhook"), "channels"),
    (("workflow", "task", "thread lifecycle", "thread db", "context management", "background permissions"), "workflows"),
    (("agent", "goal mode"), "agents"),
    (("mcp",), "mcp"),
    (("provider", "cloud model", "anthropic", "google", "xai", "minimax", "oauth", "model picker"), "providers"),
    (("image generation", "video generation", "image handling"), "providers"),
    (("tts", "voice"), "voice"),
    (("streaming finish-reason", "finish-reason"), "providers"),
    (("status monitor", "activity tab", "prompt content"), "agents"),
    (("shell", "browser", "tool", "arxiv", "twitter", "terminal", "gemini compatibility", "mime", "messaging pipeline"), "tools"),
    (("syntax", "import", "dependency", "launcher", "startup", "splash", "nicegui", "streamlit", "version", "smoke regression", "cross-platform", "key function", "live launch", "codebase consistency", "prerequisites"), "app_startup"),
    (("audit fix", "condition operator", "bug-fix", "data integrity", "edge cases", "repair", "maintenance"), "legacy_regression"),
)


def _normalize_heading(value: str) -> str:
    text = value.replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _literal_print_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("print(") or not stripped.endswith(")"):
        return None
    expr = stripped[len("print(") : -1]
    try:
        value = ast.literal_eval(expr)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, str):
        return None
    heading = _normalize_heading(value)
    if not heading or not any(ch.isalnum() for ch in heading):
        return None
    lowered = heading.lower()
    if lowered.startswith(("pass:", "fail:", "warn:", "total:", "summary", "failed tests", "warnings")):
        return None
    if "all tests passed" in lowered or "test(s) failed" in lowered:
        return None
    return heading


def _comment_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("# SECTION "):
        return None
    return _normalize_heading(stripped[2:])


def _extract_headings(path: Path) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    use_comments = path.name in {"integration_tests.py", "test_memory_e2e.py"}
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        heading = _comment_heading(line) if use_comments else _literal_print_heading(line)
        if not heading:
            continue
        if headings and headings[-1][1] == heading:
            continue
        headings.append((line_no, heading))
    return headings


def classify_legacy_heading(heading: str) -> LegacyTarget:
    lowered = heading.lower()
    for keywords, target_name in CLASSIFICATION_RULES:
        if any(keyword in lowered for keyword in keywords):
            return TARGETS[target_name]
    return TARGETS["legacy_regression"]


def build_legacy_inventory(repo_root: Path | None = None) -> tuple[LegacySection, ...]:
    root = Path.cwd() if repo_root is None else repo_root
    sections: list[LegacySection] = []
    for legacy_file in LEGACY_FILES:
        path = root / legacy_file
        if not path.exists():
            continue
        headings = _extract_headings(path)
        file_line_count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        for index, (start_line, heading) in enumerate(headings):
            end_line = headings[index + 1][0] - 1 if index + 1 < len(headings) else file_line_count
            target = classify_legacy_heading(heading)
            sections.append(
                LegacySection(
                    legacy_file=legacy_file,
                    heading=heading,
                    start_line=start_line,
                    end_line=end_line,
                    subsystem=target.subsystem,
                    replacement_paths=target.replacement_paths,
                    status=target.status,
                    verification_command=target.verification_command,
                    notes="Section-level migration target generated from the current legacy suite heading.",
                )
            )
    if len(sections) < SNAPSHOT_MIN_SECTION_COUNT:
        from tests.helpers.legacy_inventory_snapshot import LEGACY_INVENTORY_SNAPSHOT

        return tuple(
            LegacySection(
                legacy_file=entry["legacy_file"],
                heading=entry["heading"],
                start_line=entry["start_line"],
                end_line=entry["end_line"],
                subsystem=entry["subsystem"],
                replacement_paths=tuple(entry["replacement_paths"]),
                status=entry["status"],
                verification_command=entry["verification_command"],
                notes=entry.get("notes", "Migrated from retired legacy monolith snapshot."),
            )
            for entry in LEGACY_INVENTORY_SNAPSHOT
        )
    return tuple(sections)
