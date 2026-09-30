"""Safe capture support for real public documentation screenshots.

This module is intentionally small and boring: it can freeze time, reduce
motion, seed safe demo state, and disable side-effect-heavy startup paths. It
must never render replacement screenshot UI.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir


DOCS_CAPTURE_ENV = "ROW_BOT_DOCS_CAPTURE"
DOCS_FIXED_NOW_ENV = "ROW_BOT_DOCS_FIXED_NOW"
DOCS_DISABLE_AUTOSTART_ENV = "ROW_BOT_DOCS_DISABLE_AUTOSTART"
DOCS_REDUCE_MOTION_ENV = "ROW_BOT_DOCS_REDUCE_MOTION"
DOCS_FAKE_PROVIDERS_ENV = "ROW_BOT_DOCS_FAKE_PROVIDERS"
DOCS_REAL_DATA_ENV = "ROW_BOT_DOCS_REAL_DATA"
MARKETING_CAPTURE_ENV = "ROW_BOT_MARKETING_CAPTURE"
DOCS_DEMO_STATE_FILE = "docs_real_ui_demo_state.json"
DEMO_THREAD_ID = "docs-demo-chat"

SCENARIOS = {
    "first-run",
    "configured",
    "chat",
    "workflows",
    "designer",
    "developer",
    "knowledge",
    "settings",
    "channels",
    "voice",
    "mcp",
    "plugins",
    "mobile",
    "full",
}

ALLOWED_EMAIL_DOMAINS = {"example.com", "example.test"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{16,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"BEGIN (?:RSA |OPENSSH |EC |)PRIVATE KEY"),
    re.compile(r"C:\\Users\\", re.IGNORECASE),
    re.compile(r"/Users/[^/\s]+"),
]


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_docs_capture() -> bool:
    return _truthy(os.environ.get(DOCS_CAPTURE_ENV))


def is_docs_real_data_capture() -> bool:
    """Return whether this already-opted-in docs process uses the real profile."""

    return is_docs_capture() and _truthy(os.environ.get(DOCS_REAL_DATA_ENV))


def is_authorized_marketing_capture() -> bool:
    """Return whether the explicitly authorized real-profile marketing mode is active."""

    return is_docs_real_data_capture() and _truthy(os.environ.get(MARKETING_CAPTURE_ENV))


def is_docs_read_only_real_data_capture() -> bool:
    """Keep ordinary real-data docs capture read-only, excluding marketing runs."""

    return is_docs_real_data_capture() and not is_authorized_marketing_capture()


def docs_capture_fixed_now() -> datetime:
    raw = os.environ.get(DOCS_FIXED_NOW_ENV) or "2026-06-18T09:00:00Z"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc)


def docs_capture_disable_autostart() -> bool:
    return is_docs_capture() and _truthy(os.environ.get(DOCS_DISABLE_AUTOSTART_ENV, "1"))


def docs_capture_fake_provider_status() -> bool:
    return is_docs_capture() and _truthy(os.environ.get(DOCS_FAKE_PROVIDERS_ENV, "1"))


def docs_capture_provider_cards() -> list[dict[str, Any]]:
    """Return display-only provider states for isolated documentation capture."""
    if not docs_capture_fake_provider_status():
        return []
    return [
        {
            "provider_id": "ollama",
            "display_name": "Ollama Local",
            "icon": "OL",
            "group": "Local",
            "configured": True,
            "runtime_enabled": True,
            "source": "local_daemon",
            "model_count": 3,
            "chat_count": 3,
            "media_count": 0,
            "risk_label": "local_private",
        },
        {
            "provider_id": "codex",
            "display_name": "ChatGPT / Codex",
            "icon": "C",
            "group": "Subscription Accounts",
            "configured": False,
            "runtime_enabled": False,
            "source": "",
            "model_count": 3,
            "chat_count": 3,
            "media_count": 0,
            "risk_label": "subscription",
        },
        {
            "provider_id": "claude_subscription",
            "display_name": "Claude Subscription",
            "icon": "CS",
            "group": "Subscription Accounts",
            "configured": False,
            "runtime_enabled": False,
            "source": "",
            "model_count": 4,
            "chat_count": 4,
            "media_count": 0,
            "risk_label": "subscription",
        },
        {
            "provider_id": "xai_oauth",
            "display_name": "xAI Grok",
            "icon": "X",
            "group": "Subscription Accounts",
            "configured": False,
            "runtime_enabled": False,
            "source": "",
            "oauth_client_id_configured": True,
            "oauth_client_id_source": "default",
            "model_count": 4,
            "chat_count": 3,
            "media_count": 1,
            "risk_label": "subscription",
        },
        {
            "provider_id": "openai",
            "display_name": "OpenAI API",
            "icon": "AI",
            "group": "API Providers",
            "configured": False,
            "runtime_enabled": False,
            "source": "",
            "model_count": 8,
            "chat_count": 6,
            "media_count": 2,
            "risk_label": "api_key",
        },
        {
            "provider_id": "anthropic",
            "display_name": "Anthropic API",
            "icon": "AC",
            "group": "API Providers",
            "configured": False,
            "runtime_enabled": False,
            "source": "",
            "model_count": 5,
            "chat_count": 5,
            "media_count": 0,
            "risk_label": "api_key",
        },
        {
            "provider_id": "google",
            "display_name": "Google AI API",
            "icon": "G",
            "group": "API Providers",
            "configured": False,
            "runtime_enabled": False,
            "source": "",
            "model_count": 7,
            "chat_count": 5,
            "media_count": 2,
            "risk_label": "api_key",
        },
    ]


def docs_capture_demo_state_path(data_dir: Path | None = None) -> Path:
    root = data_dir or get_row_bot_data_dir()
    return root / DOCS_DEMO_STATE_FILE


def write_docs_capture_demo_state(data_dir: Path, scenario: str = "full") -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    data = default_docs_capture_demo_state()
    data["scenario"] = scenario
    path = docs_capture_demo_state_path(data_dir)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def default_docs_capture_demo_state() -> dict[str, Any]:
    now = docs_capture_fixed_now().isoformat().replace("+00:00", "Z")
    return {
        "scenario": "full",
        "generated_at": now,
        "workspace": "%ROW_BOT_DATA_DIR%/docs-demo-workspace",
        "account_email": "demo.operator@example.com",
        "thread_id": DEMO_THREAD_ID,
        "thread_name": "Demo launch checklist",
        "model": "llama3.1:8b",
        "messages": [
            {
                "role": "user",
                "content": "Summarize the launch checklist for the demo workspace.",
            },
            {
                "role": "assistant",
                "content": (
                    "I found the project brief, checked two indexed documents, "
                    "and drafted a five-step checklist. The remaining action is "
                    "waiting for approval before writing a summary file."
                ),
                "tool_results": [
                    {"name": "filesystem.search", "content": "Found launch-checklist.md in the demo workspace."},
                    {"name": "documents.search", "content": "Matched Launch brief.pdf and Support FAQ.md."},
                ],
            },
        ],
        "threads": [
            {"id": DEMO_THREAD_ID, "name": "Demo launch checklist", "kind": "chat"},
            {"id": "docs-demo-research", "name": "Research digest", "kind": "chat"},
            {"id": "docs-demo-workflow", "name": "Morning brief run", "kind": "workflow"},
        ],
        "profiles": [
            {
                "id": "docs-profile-research",
                "slug": "demo_research_guide",
                "display_name": "Research Guide",
                "description": "Reviews local sources and returns a concise, cited summary.",
                "capability": "read_only",
            },
            {
                "id": "docs-profile-project",
                "slug": "demo_project_coordinator",
                "display_name": "Project Coordinator",
                "description": "Breaks a safe internal project into reviewable steps.",
                "capability": "orchestrator",
            },
        ],
        "goal": {
            "id": "docs-goal-launch",
            "thread_id": DEMO_THREAD_ID,
            "objective": "Prepare and verify the fictional launch checklist",
            "status": "active",
            "turns_used": 3,
            "max_turns": 8,
            "progress": ["Indexed the launch brief", "Drafted the five-step checklist"],
            "blockers": ["Approval required before writing the summary file"],
        },
        "agents": [
            {
                "id": "docs-agent-parent",
                "thread_id": DEMO_THREAD_ID,
                "display_name": "Launch coordinator",
                "kind": "subagent",
                "status": "running",
                "summary": "Coordinating two safe review tasks.",
            },
            {
                "id": "docs-agent-child-complete",
                "parent_run_id": "docs-agent-parent",
                "thread_id": "docs-agent-source-review",
                "display_name": "Source reviewer",
                "kind": "subagent",
                "status": "completed",
                "summary": "Checked the two local demo documents.",
            },
            {
                "id": "docs-agent-child-running",
                "parent_run_id": "docs-agent-parent",
                "thread_id": "docs-agent-checklist",
                "display_name": "Checklist editor",
                "kind": "subagent",
                "status": "running",
                "summary": "Preparing a reviewable checklist draft.",
            },
        ],
        "workflows": [
            {"id": "docs-workflow-brief", "name": "Morning Brief", "status": "Paused", "next": "Weekdays 08:30"},
            {"id": "docs-workflow-approval", "name": "Launch Summary", "status": "Needs approval", "next": "Manual"},
            {"id": "docs-workflow-research", "name": "Research Digest", "status": "Ready", "next": "Fridays 16:00"},
        ],
        "workflow_runs": [
            {"id": "docs-run-complete", "workflow": "Morning Brief", "status": "completed", "steps": "3/3"},
            {"id": "docs-run-failed", "workflow": "Research Digest", "status": "failed", "steps": "1/2", "message": "Demo source unavailable; safe to retry."},
        ],
        "documents": [
            {"title": "Launch brief.pdf", "status": "Indexed"},
            {"title": "Support FAQ.md", "status": "Indexed"},
        ],
        "knowledge": {
            "entities": [
                {"id": "docs-entity-launch", "subject": "Demo Launch", "type": "project", "description": "A fictional product launch used only for documentation."},
                {"id": "docs-entity-checklist", "subject": "Launch Checklist", "type": "fact", "description": "Five review steps derived from the demo brief."},
                {"id": "docs-entity-review", "subject": "Safety Review", "type": "concept", "description": "Approval is required before consequential writes."},
                {"id": "docs-entity-support", "subject": "Support FAQ", "type": "fact", "description": "Answers for a fictional support team."},
            ],
            "relations": [
                ["docs-entity-launch", "docs-entity-checklist", "uses"],
                ["docs-entity-checklist", "docs-entity-review", "builds_on"],
                ["docs-entity-support", "docs-entity-launch", "part_of"],
            ],
            "wiki_pages": ["Demo Launch", "Launch Checklist", "Safety Review"],
        },
        "developer": {
            "workspace_id": "",
            "name": "demo-release-notes",
            "branch": "docs/demo-checklist",
            "todo": "Add a recovery note to the fictional launch checklist.",
            "test_output": "3 passed in 0.04s",
        },
        "designer": {
            "project_id": "docs-designer-project",
            "name": "Community Workshop Deck",
            "template": "Clean presentation",
            "export": "PDF and PowerPoint ready",
        },
        "providers": [
            {"name": "Ollama Local", "status": "Ready", "model": "llama3.1:8b"},
            {"name": "Custom local endpoint", "status": "Ready", "model": "demo-chat"},
            {"name": "OpenAI API", "status": "Not connected", "model": "gpt-4.1-mini"},
            {"name": "ChatGPT / Codex", "status": "Available to connect", "model": "subscription"},
            {"name": "xAI Grok", "status": "Available to connect", "model": "OAuth or API"},
        ],
        "channels": [
            {"name": "Telegram", "status": "Configured, stopped"},
            {"name": "WhatsApp", "status": "Configured, stopped"},
            {"name": "Slack", "status": "Configured, stopped"},
            {"name": "Discord", "status": "Configured, stopped"},
            {"name": "SMS", "status": "Not configured"},
        ],
        "mcp": [
            {"name": "GitHub MCP Server", "status": "Configured, disabled"},
            {"name": "Playwright MCP", "status": "Configured, disabled"},
        ],
        "plugins": [
            {"name": "Demo CRM Lookup", "status": "Installed, disabled"},
            {"name": "Invoice Helper", "status": "Install review required"},
        ],
        "integrations": [
            {"name": "GitHub", "status": "Not connected"},
            {"name": "Google Gmail and Calendar", "status": "Not connected"},
            {"name": "X", "status": "Not connected"},
        ],
        "mobile": {
            "device_name": "Demo Android Phone",
            "status": "Paired",
            "access_mode": "Trusted LAN",
            "events": ["Paired", "Access granted", "Session refreshed"],
        },
    }


def scan_demo_data_safety(data_dir: Path) -> list[str]:
    errors: list[str] = []
    data_dir = data_dir.resolve()
    payload_parts: list[str] = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
        except Exception:
            errors.append(f"Could not resolve demo path: {path}")
            continue
        if data_dir not in resolved.parents and resolved != data_dir:
            errors.append(f"Demo file escaped data dir: {resolved}")
        if path.stat().st_size > 2_000_000:
            continue
        try:
            payload_parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
    payload = "\n".join(payload_parts)
    for pattern in SECRET_PATTERNS:
        if pattern.search(payload):
            errors.append(f"Demo data contains blocked pattern: {pattern.pattern}")
    for email in re.findall(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", payload):
        if email.lower() not in ALLOWED_EMAIL_DOMAINS:
            errors.append(f"Demo data contains non-example email domain: {email}")
    return errors
