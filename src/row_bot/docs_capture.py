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
from typing import Any, Callable

from row_bot.data_paths import get_row_bot_data_dir


DOCS_CAPTURE_ENV = "ROW_BOT_DOCS_CAPTURE"
DOCS_FIXED_NOW_ENV = "ROW_BOT_DOCS_FIXED_NOW"
DOCS_DISABLE_NETWORK_ENV = "ROW_BOT_DOCS_DISABLE_NETWORK"
DOCS_DISABLE_AUTOSTART_ENV = "ROW_BOT_DOCS_DISABLE_AUTOSTART"
DOCS_REDUCE_MOTION_ENV = "ROW_BOT_DOCS_REDUCE_MOTION"
DOCS_FAKE_PROVIDERS_ENV = "ROW_BOT_DOCS_FAKE_PROVIDERS"
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


def docs_capture_fixed_now() -> datetime:
    raw = os.environ.get(DOCS_FIXED_NOW_ENV) or "2026-06-18T09:00:00Z"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc)


def docs_capture_disable_network() -> bool:
    return is_docs_capture() and _truthy(os.environ.get(DOCS_DISABLE_NETWORK_ENV, "1"))


def docs_capture_disable_autostart() -> bool:
    return is_docs_capture() and _truthy(os.environ.get(DOCS_DISABLE_AUTOSTART_ENV, "1"))


def docs_capture_fake_provider_status() -> bool:
    return is_docs_capture() and _truthy(os.environ.get(DOCS_FAKE_PROVIDERS_ENV, "1"))


def docs_capture_reduce_motion_css() -> str:
    if not (is_docs_capture() and _truthy(os.environ.get(DOCS_REDUCE_MOTION_ENV, "1"))):
        return ""
    return """
<style>
html[data-row-bot-docs-capture="1"] *, html[data-row-bot-docs-capture="1"] *::before, html[data-row-bot-docs-capture="1"] *::after {
  animation-duration: 0.001ms !important;
  animation-iteration-count: 1 !important;
  scroll-behavior: auto !important;
  transition-duration: 0.001ms !important;
}
</style>
""".strip()


def docs_capture_bootstrap_html() -> str:
    if not is_docs_capture():
        return ""
    return """
<script>
(() => {
  if (window.__rowBotDocsCaptureInstalled) return;
  window.__rowBotDocsCaptureInstalled = true;
  document.documentElement.setAttribute('data-row-bot-docs-capture', '1');
  const mark = () => {
    if (document.body) document.body.setAttribute('data-docs-id', 'app-shell');
    document.querySelectorAll('input[type="password"], [autocomplete*="token"], [autocomplete*="password"]').forEach((el) => {
      el.setAttribute('data-sensitive', 'true');
    });
  };
  mark();
  new MutationObserver(mark).observe(document.documentElement, {childList: true, subtree: true});
})();
</script>
""".strip()


def docs_capture_demo_state_path(data_dir: Path | None = None) -> Path:
    root = data_dir or get_row_bot_data_dir()
    return root / DOCS_DEMO_STATE_FILE


def load_docs_capture_demo_state(data_dir: Path | None = None) -> dict[str, Any]:
    path = docs_capture_demo_state_path(data_dir)
    if not path.exists():
        return default_docs_capture_demo_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_docs_capture_demo_state()
    return data if isinstance(data, dict) else default_docs_capture_demo_state()


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
        "workflows": [
            {"name": "Morning Brief", "status": "Paused", "next": "Weekdays 08:30"},
            {"name": "Inbox Follow-up", "status": "Needs approval", "next": "Manual"},
            {"name": "Research Digest", "status": "Ready", "next": "Fridays 16:00"},
        ],
        "documents": [
            {"title": "Launch brief.pdf", "status": "Indexed"},
            {"title": "Support FAQ.md", "status": "Indexed"},
        ],
        "providers": [
            {"name": "Ollama Local", "status": "Ready", "model": "llama3.1:8b"},
            {"name": "OpenAI API", "status": "Not connected", "model": "gpt-4.1-mini"},
            {"name": "Custom endpoint", "status": "Preview", "model": "demo-model"},
        ],
        "channels": [
            {"name": "Telegram", "status": "Disconnected demo"},
            {"name": "Slack", "status": "Disconnected demo"},
            {"name": "Discord", "status": "Disconnected demo"},
        ],
        "mcp": [
            {"name": "GitHub MCP Server", "status": "Disabled preview"},
            {"name": "Playwright MCP", "status": "Disabled preview"},
        ],
        "plugins": [
            {"name": "Demo CRM Lookup", "status": "Not installed"},
            {"name": "Invoice Helper", "status": "Review required"},
        ],
    }


def docs_capture_query_params(client: Any) -> dict[str, str]:
    try:
        params = getattr(getattr(client, "request", None), "query_params", {})
        return {str(key): str(value) for key, value in dict(params).items()}
    except Exception:
        return {}


def configure_docs_capture_state(
    state: Any,
    query: dict[str, str],
    *,
    load_messages: Callable[[str], list[dict[str, Any]]] | None = None,
) -> dict[str, str]:
    """Apply capture-only navigation state to the real app state object."""
    if not is_docs_capture():
        return {}
    intent = {
        "surface": query.get("docs_surface", ""),
        "home_tab": query.get("home_tab", ""),
        "settings_tab": query.get("settings_tab", ""),
        "dialog": query.get("dialog", ""),
    }
    demo = load_docs_capture_demo_state()
    state.active_designer_project = None
    state.active_developer_workspace_id = None
    if intent["home_tab"]:
        state.thread_id = None
        state.thread_name = None
        state.messages = []
        state.preferred_home_tab = intent["home_tab"]
        return intent
    if intent["settings_tab"] or intent["dialog"] in {"setup-center", "skills-hub", "plugin-marketplace", "mcp-add-server"}:
        state.thread_id = None
        state.thread_name = None
        state.messages = []
        state.preferred_home_tab = "Workflows"
        return intent
    if intent["surface"].startswith("chat") or query.get("thread_id"):
        thread_id = query.get("thread_id") or str(demo.get("thread_id") or DEMO_THREAD_ID)
        state.thread_id = thread_id
        state.thread_name = str(demo.get("thread_name") or "Demo thread")
        state.thread_model_override = str(demo.get("model") or "")
        loaded = load_messages(thread_id) if load_messages else []
        state.messages = loaded or list(demo.get("messages") or [])
        return intent
    state.thread_id = None
    state.thread_name = None
    state.messages = []
    state.preferred_home_tab = intent["home_tab"] or "Workflows"
    return intent


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
