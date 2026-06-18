"""Deterministic docs-mode helpers for public documentation automation."""

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir


DOCS_MODE_ENV = "ROW_BOT_DOCS_MODE"
DOCS_FIXED_NOW_ENV = "ROW_BOT_DOCS_FIXED_NOW"
DOCS_DISABLE_NETWORK_ENV = "ROW_BOT_DOCS_DISABLE_NETWORK"
DOCS_DISABLE_AUTOSTART_ENV = "ROW_BOT_DOCS_DISABLE_AUTOSTART"
DOCS_REDUCE_MOTION_ENV = "ROW_BOT_DOCS_REDUCE_MOTION"
DOCS_FAKE_PROVIDERS_ENV = "ROW_BOT_DOCS_FAKE_PROVIDERS"
DOCS_DEMO_STATE_FILE = "docs_demo_state.json"


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_docs_mode() -> bool:
    return _truthy(os.environ.get(DOCS_MODE_ENV))


def docs_fixed_now() -> datetime:
    raw = os.environ.get(DOCS_FIXED_NOW_ENV) or "2026-06-18T09:00:00Z"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc)


def docs_disable_network() -> bool:
    return is_docs_mode() and _truthy(os.environ.get(DOCS_DISABLE_NETWORK_ENV, "1"))


def docs_disable_autostart() -> bool:
    return is_docs_mode() and _truthy(os.environ.get(DOCS_DISABLE_AUTOSTART_ENV, "1"))


def docs_fake_providers() -> bool:
    return is_docs_mode() and _truthy(os.environ.get(DOCS_FAKE_PROVIDERS_ENV, "1"))


def docs_reduce_motion_css() -> str:
    if not (is_docs_mode() and _truthy(os.environ.get(DOCS_REDUCE_MOTION_ENV, "1"))):
        return ""
    return """
<style>
*, *::before, *::after {
  animation-duration: 0.001ms !important;
  animation-iteration-count: 1 !important;
  scroll-behavior: auto !important;
  transition-duration: 0.001ms !important;
}
</style>
""".strip()


def docs_demo_state_path(data_dir: Path | None = None) -> Path:
    root = data_dir or get_row_bot_data_dir()
    return root / DOCS_DEMO_STATE_FILE


def load_docs_demo_state(data_dir: Path | None = None) -> dict[str, Any]:
    path = docs_demo_state_path(data_dir)
    if not path.exists():
        return default_docs_demo_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_docs_demo_state()
    return data if isinstance(data, dict) else default_docs_demo_state()


def write_docs_demo_state(data_dir: Path, scenario: str = "full") -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    data = default_docs_demo_state()
    data["scenario"] = scenario
    path = docs_demo_state_path(data_dir)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def default_docs_demo_state() -> dict[str, Any]:
    now = docs_fixed_now().isoformat().replace("+00:00", "Z")
    return {
        "scenario": "full",
        "generated_at": now,
        "workspace": "%ROW_BOT_DATA_DIR%/docs-demo-workspace",
        "account_email": "alex.demo@example.com",
        "providers": [
            {"name": "Ollama Local", "status": "Ready", "model": "llama3.1:8b"},
            {"name": "OpenAI API", "status": "Key saved", "model": "gpt-4.1-mini"},
            {"name": "ChatGPT / Codex", "status": "Signed in", "model": "codex-mini"},
        ],
        "messages": [
            {"role": "user", "text": "Summarize the launch checklist for the demo workspace."},
            {"role": "assistant", "text": "I found the project brief, checked two docs, and drafted a five-step launch checklist."},
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
        "mcp": [
            {"name": "GitHub MCP Server", "status": "Configured"},
            {"name": "Playwright MCP", "status": "Available"},
        ],
        "plugins": [
            {"name": "Demo CRM Lookup", "status": "Enabled"},
            {"name": "Invoice Helper", "status": "Review required"},
        ],
        "channels": [
            {"name": "Telegram", "status": "Configured"},
            {"name": "Slack", "status": "Ready"},
            {"name": "Discord", "status": "Not connected"},
        ],
    }


def docs_surface_ids() -> set[str]:
    return set(_SURFACES)


def render_docs_surface(surface_id: str) -> str:
    if surface_id not in _SURFACES:
        surface_id = "chat-main"
    state = load_docs_demo_state()
    spec = _SURFACES[surface_id]
    return _page(
        surface_id=surface_id,
        title=spec["title"],
        subtitle=spec["subtitle"],
        active=spec.get("active", "Chat"),
        docs_id=spec.get("docs_id", surface_id),
        body=spec["body"](state),
    )


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _page(*, surface_id: str, title: str, subtitle: str, active: str, docs_id: str, body: str) -> str:
    now = docs_fixed_now().strftime("%b %d, %Y %H:%M UTC")
    return f"""
{docs_reduce_motion_css()}
<style>{_CSS}</style>
<main class="docs-shell" data-docs-id="{_e(surface_id)}">
  <aside class="docs-rail">
    <div class="brand">Row-Bot</div>
    {_nav(active)}
    <div class="rail-card">
      <strong>Docs mode</strong>
      <span>Demo data only</span>
      <span>{_e(now)}</span>
    </div>
  </aside>
  <section class="docs-main" data-docs-id="{_e(docs_id)}">
    <header class="docs-topbar">
      <div>
        <p class="eyebrow">Public docs screenshot</p>
        <h1>{_e(title)}</h1>
        <p>{_e(subtitle)}</p>
      </div>
      <div class="status-pill">Safe demo state</div>
    </header>
    {body}
  </section>
</main>
""".strip()


def _nav(active: str) -> str:
    items = ["Chat", "Workflows", "Designer", "Developer", "Knowledge", "Settings", "Skills", "Plugins", "MCP", "Voice"]
    return '<nav class="docs-nav">' + "".join(
        f'<span class="{"active" if item == active else ""}">{_e(item)}</span>' for item in items
    ) + "</nav>"


def _cards(rows: list[dict[str, Any]], *, title_key: str = "name") -> str:
    return '<div class="card-grid">' + "".join(
        f"""
        <article class="surface-card">
          <strong>{_e(row.get(title_key) or row.get("title"))}</strong>
          <span>{_e(row.get("status") or row.get("description") or "")}</span>
          <small>{_e(row.get("next") or row.get("model") or row.get("detail") or "")}</small>
        </article>
        """
        for row in rows
    ) + "</div>"


def _chat_main(state: dict[str, Any]) -> str:
    messages = "".join(
        f'<div class="message {msg["role"]}"><b>{_e(msg["role"].title())}</b><p>{_e(msg["text"])}</p></div>'
        for msg in state["messages"]
    )
    return f"""
    <div class="chat-layout">
      <section class="thread-list">
        <strong>Threads</strong>
        <span>Launch checklist</span>
        <span>Research digest</span>
        <span>Design review</span>
      </section>
      <section class="chat-panel" data-docs-id="chat-shell">
        <div class="model-strip" data-docs-id="model-picker">Ollama Local - llama3.1:8b - Agent Mode</div>
        <div class="messages">{messages}</div>
        <div class="composer" data-docs-id="chat-composer">Ask Row-Bot anything...</div>
      </section>
    </div>
    """


def _model_picker(state: dict[str, Any]) -> str:
    return f"""
    <div class="picker" data-docs-id="model-picker">
      <h2>Model picker</h2>
      {_cards(state["providers"])}
      <div class="callout">Quick Choices keep provider identity explicit before each run.</div>
    </div>
    """


def _tool_trace(_state: dict[str, Any]) -> str:
    steps = [
        ("filesystem.search", "Read-only", "Found launch-checklist.md"),
        ("documents.search", "Read-only", "Matched 2 indexed documents"),
        ("approval.request", "Ask", "Waiting before writing summary.md"),
    ]
    return '<div class="trace" data-docs-id="tool-trace"><h2>Tool trace</h2>' + "".join(
        f'<div><strong>{_e(name)}</strong><span>{_e(mode)}</span><p>{_e(detail)}</p></div>'
        for name, mode, detail in steps
    ) + "</div>"


def _approval(_state: dict[str, Any]) -> str:
    return """
    <div class="modal-card" data-docs-id="approval-dialog">
      <h2>Approval required</h2>
      <p>Row-Bot wants to write <code>summary.md</code> inside the demo workspace.</p>
      <p data-sensitive>Command preview: **** redacted for docs mode ****</p>
      <div class="button-row"><button>Approve once</button><button class="secondary">Deny</button></div>
    </div>
    """


def _setup_wizard(state: dict[str, Any]) -> str:
    return f"""
    <div class="wizard" data-docs-id="setup-wizard">
      <h2>Welcome to Row-Bot</h2>
      <p>Connect one working model first. Everything else can wait.</p>
      <div class="option-row">
        <article>Local Ollama<br /><small>Private local execution</small></article>
        <article>API providers<br /><small data-sensitive>Keys are masked: ****</small></article>
        <article>Custom endpoint<br /><small>OpenAI-compatible server</small></article>
      </div>
      {_cards(state["providers"])}
    </div>
    """


def _setup_center(_state: dict[str, Any]) -> str:
    steps = [
        {"name": "Models", "status": "Done"},
        {"name": "Knowledge", "status": "Open"},
        {"name": "Workflows", "status": "Recommended"},
        {"name": "Designer Studio", "status": "Open"},
        {"name": "Developer Studio", "status": "Open"},
        {"name": "Channels", "status": "Open"},
    ]
    return f'<div data-docs-id="setup-center"><h2>Setup Center</h2>{_cards(steps)}</div>'


def _workflows(state: dict[str, Any]) -> str:
    return f"""
    <div data-docs-id="workflow-card-list">
      <h2>Workflows</h2>
      {_cards(state["workflows"])}
      <div class="timeline"><span>Run history</span><b>Inbox Follow-up waiting for approval</b></div>
    </div>
    """


def _designer_gallery(_state: dict[str, Any]) -> str:
    rows = [
        {"name": "Product one-pager", "status": "Deck", "next": "Updated today"},
        {"name": "Launch page", "status": "Landing page", "next": "Responsive preview"},
        {"name": "Storyboard", "status": "Storyboard", "next": "Needs review"},
    ]
    return f'<div data-docs-id="designer-gallery"><h2>Designer Studio</h2>{_cards(rows)}</div>'


def _designer_new(_state: dict[str, Any]) -> str:
    return """
    <div class="modal-card" data-docs-id="designer-new-project-dialog">
      <h2>New Designer project</h2>
      <p>Choose a deck, landing page, document, app mockup, or storyboard template.</p>
      <div class="option-row"><article>Deck</article><article>Landing page</article><article>Mockup</article></div>
      <div class="button-row"><button>Create project</button><button class="secondary">Cancel</button></div>
    </div>
    """


def _designer_editor(_state: dict[str, Any]) -> str:
    return """
    <div class="editor" data-docs-id="designer-editor">
      <aside><strong>Pages</strong><span>Cover</span><span>Timeline</span><span>CTA</span></aside>
      <section><h2>Launch Page Preview</h2><div class="preview-box">Hero, metrics, and CTA preview</div></section>
      <aside><strong>Assistant</strong><p>Refine the section hierarchy and improve contrast.</p></aside>
    </div>
    """


def _developer_home(_state: dict[str, Any]) -> str:
    rows = [
        {"name": "demo-workspace", "status": "Clean", "next": "%ROW_BOT_DATA_DIR%/docs-demo-workspace"},
        {"name": "custom-tool-lab", "status": "2 pending reviews", "next": "Custom Tools"},
    ]
    return f'<div data-docs-id="developer-home"><h2>Developer Studio</h2>{_cards(rows)}</div>'


def _developer_workspace(_state: dict[str, Any]) -> str:
    return """
    <div class="developer-workspace" data-docs-id="developer-workspace">
      <section><h2>Workspace inspector</h2><p>Branch docs/demo-review has 3 changed files.</p></section>
      <section class="diff"><strong>Changed files</strong><span>README-demo.md</span><span>src/demo_tool.py</span><span>tests/test_demo_tool.py</span></section>
      <section><strong>Approval mode</strong><p>Ask before edits and shell commands.</p></section>
    </div>
    """


def _knowledge(state: dict[str, Any]) -> str:
    return f"""
    <div data-docs-id="knowledge-graph-panel">
      <h2>Knowledge graph</h2>
      <div class="graph-demo"><span>Launch brief</span><span>Support FAQ</span><span>Research digest</span></div>
      {_cards(state["documents"], title_key="title")}
    </div>
    """


def _settings_tab(state: dict[str, Any], tab: str, docs_id: str, rows: list[dict[str, Any]]) -> str:
    return f"""
    <div class="settings" data-docs-id="settings-dialog">
      <aside>
        {''.join(f'<span class="{"active" if label == tab else ""}" data-docs-id="settings-tab-{slug}">{label}</span>' for label, slug in _SETTINGS_TABS)}
      </aside>
      <section data-docs-id="{_e(docs_id)}">
        <h2>{_e(tab)}</h2>
        {_cards(rows)}
      </section>
    </div>
    """


def _skills_hub(_state: dict[str, Any]) -> str:
    rows = [
        {"name": "Meeting Notes", "status": "Verified source", "next": "Preview ready"},
        {"name": "Data Analyst", "status": "Bundled", "next": "Installed"},
        {"name": "Deep Research", "status": "Bundled", "next": "Pinned"},
    ]
    return f'<div data-docs-id="skills-hub-dialog"><h2>Skills Hub</h2>{_cards(rows)}</div>'


def _plugin_marketplace(state: dict[str, Any]) -> str:
    return f'<div data-docs-id="plugin-marketplace"><h2>Plugin marketplace</h2>{_cards(state["plugins"])}</div>'


def _mcp_add(state: dict[str, Any]) -> str:
    return f"""
    <div class="modal-card" data-docs-id="mcp-add-server-dialog">
      <h2>Add MCP server</h2>
      <p>Pick a recommended server or paste a safe local command.</p>
      {_cards(state["mcp"])}
    </div>
    """


def _channel_cards(state: dict[str, Any]) -> str:
    return f'<div data-docs-id="channel-configuration-cards"><h2>Channel configuration</h2>{_cards(state["channels"])}</div>'


def _voice(_state: dict[str, Any]) -> str:
    rows = [
        {"name": "Dictation", "status": "Enabled", "next": "Local microphone"},
        {"name": "Realtime Talk", "status": "Provider required", "next": "OpenAI Realtime"},
        {"name": "Read aloud", "status": "Local TTS ready", "next": "Kokoro voice"},
    ]
    return f'<div data-docs-id="voice-settings"><h2>Voice settings</h2>{_cards(rows)}</div>'


def _buddy(_state: dict[str, Any]) -> str:
    rows = [
        {"name": "Sidebar companion", "status": "Enabled", "next": "Sprout look"},
        {"name": "Desktop overlay", "status": "Paused", "next": "Manual launch"},
        {"name": "Motion", "status": "Still images in docs mode", "next": "Reduced motion"},
    ]
    return f'<div data-docs-id="buddy-settings-tab"><h2>Buddy</h2>{_cards(rows)}</div>'


def _privacy_approval(_state: dict[str, Any]) -> str:
    return """
    <div class="modal-card" data-docs-id="privacy-safety-approval-example">
      <h2>Safety example</h2>
      <p>Filesystem, shell, browser, MCP, and Developer Studio actions can require approval.</p>
      <p data-sensitive>Example token value: ****</p>
      <div class="button-row"><button>Ask each time</button><button class="secondary">Block</button></div>
    </div>
    """


_SETTINGS_TABS = [
    ("Providers", "providers"),
    ("Models", "models"),
    ("Knowledge", "knowledge"),
    ("Skills", "skills"),
    ("Channels", "channels"),
    ("Voice", "voice"),
    ("MCP", "mcp"),
    ("Plugins", "plugins"),
    ("Buddy", "buddy"),
]


_CSS = """
html, body { margin: 0; background: #101418; color: #eef3f7; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
.docs-shell { display: grid; grid-template-columns: 260px 1fr; min-height: 100vh; background: #101418; }
.docs-rail { background: #151b21; border-right: 1px solid #26323c; padding: 22px; display: flex; flex-direction: column; gap: 18px; }
.brand { font-size: 28px; font-weight: 760; letter-spacing: 0; color: #fff; }
.docs-nav { display: grid; gap: 6px; }
.docs-nav span, .settings aside span { padding: 9px 10px; border-radius: 8px; color: #b8c3cc; }
.docs-nav span.active, .settings aside span.active { background: #29415a; color: #fff; }
.rail-card { margin-top: auto; display: grid; gap: 4px; border: 1px solid #31404d; border-radius: 8px; padding: 12px; color: #c8d2dc; }
.docs-main { padding: 28px; overflow: hidden; }
.docs-topbar { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; border-bottom: 1px solid #26323c; padding-bottom: 20px; margin-bottom: 20px; }
.docs-topbar h1 { font-size: 34px; margin: 0 0 6px; letter-spacing: 0; }
.docs-topbar p { margin: 0; color: #b8c3cc; max-width: 760px; }
.eyebrow { color: #7ab3e6 !important; text-transform: uppercase; font-size: 12px; font-weight: 700; margin-bottom: 6px !important; }
.status-pill { border: 1px solid #4f78a4; color: #cfe6ff; border-radius: 999px; padding: 8px 12px; white-space: nowrap; }
.card-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.surface-card { min-height: 118px; border: 1px solid #30404d; border-radius: 8px; padding: 16px; background: #192129; display: grid; gap: 6px; align-content: start; }
.surface-card strong { font-size: 17px; color: #fff; }
.surface-card span { color: #c6d1da; }
.surface-card small { color: #86a7c7; }
.chat-layout { display: grid; grid-template-columns: 230px 1fr; gap: 18px; }
.thread-list, .chat-panel, .picker, .trace, .wizard, .settings, .editor, .developer-workspace, .modal-card { border: 1px solid #30404d; border-radius: 8px; background: #192129; padding: 18px; }
.thread-list { display: grid; gap: 10px; align-content: start; color: #c6d1da; }
.model-strip, .composer, .callout, .timeline { border: 1px solid #39536b; background: #152638; border-radius: 8px; padding: 12px; color: #cfe6ff; }
.messages { display: grid; gap: 12px; margin: 14px 0; }
.message { border-radius: 8px; padding: 12px; background: #202a33; }
.message.user { background: #24384d; }
.message p { margin: 6px 0 0; color: #dce6ee; }
.trace { display: grid; gap: 12px; }
.trace div { border-left: 4px solid #4f78a4; padding: 10px 12px; background: #202a33; }
.trace span { margin-left: 12px; color: #85c7a4; }
.modal-card { max-width: 780px; margin: 24px auto; box-shadow: 0 24px 80px rgba(0,0,0,0.35); }
.button-row { display: flex; gap: 10px; margin-top: 16px; }
button { border: 0; border-radius: 8px; background: #4f78a4; color: #fff; padding: 10px 14px; font-weight: 700; }
button.secondary { background: #2b3641; color: #dce6ee; }
.option-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 14px 0; }
.option-row article { border: 1px solid #39536b; border-radius: 8px; padding: 16px; background: #152638; }
.editor { display: grid; grid-template-columns: 180px 1fr 260px; gap: 16px; min-height: 520px; }
.editor aside, .developer-workspace section, .diff { border: 1px solid #30404d; border-radius: 8px; background: #202a33; padding: 14px; display: grid; gap: 8px; align-content: start; }
.preview-box { height: 360px; border-radius: 8px; background: linear-gradient(135deg, #28415c, #1b2a35 55%, #314047); display: grid; place-items: center; color: #eaf4ff; font-size: 22px; }
.developer-workspace { display: grid; grid-template-columns: 1.2fr 1fr 0.8fr; gap: 14px; }
.graph-demo { height: 320px; border: 1px solid #30404d; border-radius: 8px; background: radial-gradient(circle at 30% 35%, #4f78a4 0 58px, transparent 60px), radial-gradient(circle at 65% 55%, #5a8a74 0 52px, transparent 54px), radial-gradient(circle at 50% 72%, #765f9d 0 42px, transparent 44px), #17212a; display: flex; align-items: center; justify-content: space-around; margin-bottom: 14px; }
.graph-demo span { background: rgba(16,20,24,0.82); border: 1px solid #506273; border-radius: 999px; padding: 8px 12px; }
.settings { display: grid; grid-template-columns: 210px 1fr; gap: 18px; min-height: 570px; }
.settings aside { border-right: 1px solid #30404d; padding-right: 12px; display: grid; gap: 6px; align-content: start; }
code { background: #0f141a; border: 1px solid #2c3945; border-radius: 6px; padding: 2px 5px; }
[data-sensitive] { filter: blur(3px); }
@media (max-width: 900px) {
  .docs-shell { grid-template-columns: 1fr; }
  .docs-rail { display: none; }
  .card-grid, .option-row, .chat-layout, .editor, .developer-workspace, .settings { grid-template-columns: 1fr; }
}
"""


_SURFACES = {
    "first-launch-setup-wizard": {"title": "First Launch Setup", "subtitle": "The first-run wizard with safe demo provider paths.", "active": "Settings", "docs_id": "setup-wizard", "body": _setup_wizard},
    "setup-center": {"title": "Setup Center", "subtitle": "Resumable onboarding checklist after model setup.", "active": "Settings", "docs_id": "setup-center", "body": _setup_center},
    "chat-main": {"title": "Chat Main", "subtitle": "Conversation, model strip, sidebar, messages, and composer.", "active": "Chat", "docs_id": "chat-shell", "body": _chat_main},
    "chat-model-picker": {"title": "Model Picker", "subtitle": "Provider-qualified model choices and readiness.", "active": "Chat", "docs_id": "model-picker", "body": _model_picker},
    "chat-tool-trace": {"title": "Tool Trace", "subtitle": "Grouped tool activity with approval state.", "active": "Chat", "docs_id": "tool-trace", "body": _tool_trace},
    "chat-approval": {"title": "Approval Dialog", "subtitle": "A safe example of a sensitive action prompt.", "active": "Chat", "docs_id": "approval-dialog", "body": _approval},
    "home-workflows": {"title": "Workflows", "subtitle": "Workflow cards, schedules, and run status.", "active": "Workflows", "docs_id": "workflow-card-list", "body": _workflows},
    "designer-gallery": {"title": "Designer Gallery", "subtitle": "Project cards for visual work.", "active": "Designer", "docs_id": "designer-gallery", "body": _designer_gallery},
    "designer-new-project": {"title": "Designer New Project", "subtitle": "Template chooser for new visual work.", "active": "Designer", "docs_id": "designer-new-project-dialog", "body": _designer_new},
    "designer-editor": {"title": "Designer Editor", "subtitle": "Preview, page navigator, and assistant panel.", "active": "Designer", "docs_id": "designer-editor", "body": _designer_editor},
    "developer-home": {"title": "Developer Home", "subtitle": "Workspace list and Custom Tools entry points.", "active": "Developer", "docs_id": "developer-home", "body": _developer_home},
    "developer-workspace": {"title": "Developer Workspace", "subtitle": "Workspace inspector, changed files, and approval mode.", "active": "Developer", "docs_id": "developer-workspace", "body": _developer_workspace},
    "knowledge-graph": {"title": "Knowledge Graph", "subtitle": "Memory graph and document indexing state.", "active": "Knowledge", "docs_id": "knowledge-graph-panel", "body": _knowledge},
    "settings-providers-overview": {"title": "Settings Providers", "subtitle": "Provider connection cards and credential status.", "active": "Settings", "docs_id": "settings-tab-providers", "body": lambda s: _settings_tab(s, "Providers", "settings-tab-providers", s["providers"])},
    "settings-models-catalog": {"title": "Settings Models", "subtitle": "Model catalog and Quick Choices.", "active": "Settings", "docs_id": "settings-tab-models", "body": lambda s: _settings_tab(s, "Models", "settings-tab-models", [{"name": "llama3.1:8b", "status": "Local", "next": "Agent Mode"}, {"name": "gpt-4.1-mini", "status": "Cloud", "next": "Chat and tools"}, {"name": "codex-mini", "status": "Subscription", "next": "Coding"}])},
    "settings-knowledge": {"title": "Settings Knowledge", "subtitle": "Memory, documents, graph, and embeddings.", "active": "Settings", "docs_id": "settings-tab-knowledge", "body": lambda s: _settings_tab(s, "Knowledge", "settings-tab-knowledge", s["documents"])},
    "settings-skills": {"title": "Settings Skills", "subtitle": "Enable, pin, and review Smart Skills.", "active": "Settings", "docs_id": "settings-tab-skills", "body": lambda s: _settings_tab(s, "Skills", "settings-tab-skills", [{"name": "Task Automation", "status": "Enabled", "next": "Pinned"}, {"name": "Design Creator", "status": "Enabled", "next": "Bundled"}, {"name": "Developer Review", "status": "Available", "next": "Manual"}])},
    "settings-channels": {"title": "Settings Channels", "subtitle": "Channel readiness and credential cards.", "active": "Settings", "docs_id": "settings-tab-channels", "body": lambda s: _settings_tab(s, "Channels", "settings-tab-channels", s["channels"])},
    "settings-voice": {"title": "Settings Voice", "subtitle": "Dictation, realtime talk, and read-aloud settings.", "active": "Settings", "docs_id": "settings-tab-voice", "body": lambda s: _settings_tab(s, "Voice", "settings-tab-voice", [{"name": "Dictation", "status": "Enabled", "next": "Local microphone"}, {"name": "Realtime Talk", "status": "Provider required", "next": "OpenAI Realtime"}, {"name": "Read aloud", "status": "Local TTS ready", "next": "Kokoro voice"}])},
    "settings-mcp": {"title": "Settings MCP", "subtitle": "External MCP server status.", "active": "Settings", "docs_id": "settings-tab-mcp", "body": lambda s: _settings_tab(s, "MCP", "settings-tab-mcp", s["mcp"])},
    "settings-plugins": {"title": "Settings Plugins", "subtitle": "Installed plugins and Custom Tools.", "active": "Settings", "docs_id": "settings-tab-plugins", "body": lambda s: _settings_tab(s, "Plugins", "settings-tab-plugins", s["plugins"])},
    "skills-hub-browse": {"title": "Skills Hub", "subtitle": "Browse and preview public skills.", "active": "Skills", "docs_id": "skills-hub-dialog", "body": _skills_hub},
    "plugin-marketplace": {"title": "Plugin Marketplace", "subtitle": "Safe marketplace browsing in docs mode.", "active": "Plugins", "docs_id": "plugin-marketplace", "body": _plugin_marketplace},
    "mcp-add-server": {"title": "MCP Add Server", "subtitle": "Recommended server import and local command review.", "active": "MCP", "docs_id": "mcp-add-server-dialog", "body": _mcp_add},
    "channel-configuration-cards": {"title": "Channel Configuration", "subtitle": "Messaging channel setup and health cards.", "active": "Settings", "docs_id": "channel-configuration-cards", "body": _channel_cards},
    "voice-settings": {"title": "Voice Settings", "subtitle": "Voice feature readiness and diagnostics.", "active": "Voice", "docs_id": "voice-settings", "body": _voice},
    "buddy-settings": {"title": "Buddy Settings", "subtitle": "Companion behavior, look, overlay, and motion state.", "active": "Settings", "docs_id": "buddy-settings-tab", "body": _buddy},
    "privacy-safety-approval-example": {"title": "Privacy And Safety Approval", "subtitle": "Approval example with redacted sensitive fields.", "active": "Settings", "docs_id": "privacy-safety-approval-example", "body": _privacy_approval},
}
