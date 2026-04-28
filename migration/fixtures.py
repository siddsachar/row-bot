"""Realistic source fixtures for migration tests and manual dry runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def create_realistic_hermes_home(root: str | Path) -> Path:
    """Create a realistic Hermes home for a 1-3 month user."""
    home = Path(root)
    _write(
        home / "config.yaml",
        "\n".join(
            [
                "model:",
                "  provider: openai",
                "  model: gpt-5.4",
                "providers:",
                "  openai:",
                "    base_url: https://api.openai.example/v1",
                "    api_key_env: OPENAI_API_KEY",
                "    models: [gpt-5.4, gpt-5-mini]",
                "custom_providers:",
                "  - name: local-llm",
                "    base_url: http://127.0.0.1:11434/v1",
                "    models: [qwen3:8b, llama4:latest]",
                "memory:",
                "  provider: honcho",
                "  honcho:",
                "    project: personal-os",
                "skills:",
                "  config:",
                "    ship-it:",
                "      mode: fast",
                "    meeting-notes:",
                "      summarize_actions: true",
                "mcp_servers:",
                "  time:",
                "    command: npx",
                "    args: ['-y', 'mcp-server-time']",
                "  linear:",
                "    command: npx",
                "    args: ['-y', '@linear/mcp-server']",
                "    env:",
                "      LINEAR_API_KEY: ${LINEAR_API_KEY}",
                "browser:",
                "  headless: false",
                "terminal:",
                "  backend: local",
                "",
            ]
        ),
    )
    _write(
        home / ".env",
        "\n".join(
            [
                "OPENAI_API_KEY=sk-hermes-three-month-user",
                "ANTHROPIC_API_KEY=sk-ant-hermes-example",
                "LINEAR_API_KEY=lin_api_1234567890abcdef",
                "TELEGRAM_BOT_TOKEN=123456:hermes-demo-token",
                "",
            ]
        ),
    )
    _write(home / "SOUL.md", "# Hermes Soul\n\nCalm, direct, remembers commitments, prefers concise plans.\n")
    _write(
        home / "AGENTS.md",
        "# Hermes Agent Instructions\n\nUse the project workspace, keep changelogs current, and ask before destructive shell operations.\n",
    )
    _write(
        home / "memories" / "MEMORY.md",
        "\n".join(
            [
                "# Long-term Memory",
                "",
                "- 2026-02-12: User started weekly research briefs for AI infra vendors.",
                "- 2026-03-04: User prefers local-first tools unless cloud is explicitly faster.",
                "- 2026-03-27: Project Atlas uses Linear labels: research, blocked, shipped.",
                "- 2026-04-16: Keep meeting summaries under 12 bullets with owners and dates.",
                "",
            ]
        ),
    )
    _write(
        home / "memories" / "USER.md",
        "# User Profile\n\nBuilder, prefers robust defaults, uses Windows desktop and a local Ollama fallback.\n",
    )
    _write_skill(home / "skills" / "ship-it", "ship-it", "Turn a rough implementation into a release-ready change.")
    _write_skill(home / "skills" / "meeting-notes", "meeting-notes", "Extract decisions, owners, and follow-up tasks.")
    _write(home / "plugins" / "memory-honcho" / "config.json", _json({"workspaceId": "personal-os"}))
    _write(home / "sessions" / "2026-03-18-research.jsonl", '{"role":"user","content":"summarize MCP options"}\n')
    _write(home / "sessions" / "2026-04-21-release.jsonl", '{"role":"assistant","content":"release checklist drafted"}\n')
    _write(home / "logs" / "hermes.log", "2026-04-21T10:00:00Z INFO gateway ready\n")
    _write(home / "cron" / "jobs.json", _json({"jobs": [{"name": "daily briefing", "schedule": "daily:08:30"}]}))
    _write(home / "mcp-tokens" / "linear.json", _json({"access_token": "linear-secret-token"}))
    _write(home / "auth.json", _json({"providers": {"openai": {"api_key": "sk-legacy-auth"}}}))
    _write(home / "state.db", "sqlite placeholder for Hermes state\n")
    return home


def create_realistic_openclaw_home(root: str | Path) -> Path:
    """Create a realistic OpenClaw home for a 1-3 month user."""
    home = Path(root)
    workspace = home / "workspace"
    openclaw_config = {
        "agents": {
            "defaults": {
                "workspace": str(workspace),
                "model": {"primary": "anthropic/claude-sonnet-4.6", "fallbacks": ["openai/gpt-5.4"]},
                "contextTokens": 120000,
                "timeoutSeconds": 900,
                "thinkingDefault": "adaptive",
                "compaction": {"mode": "semantic", "maxTokens": 60000},
                "sandbox": {"backend": "docker", "docker": {"image": "ghcr.io/openclaw/sandbox:latest"}},
            },
            "list": [
                {"id": "main", "default": True, "name": "Personal Ops"},
                {"id": "research", "name": "Research Analyst", "workspace": str(workspace / "research")},
            ],
        },
        "models": {
            "providers": {
                "anthropic": {"apiKey": {"source": "env", "id": "ANTHROPIC_API_KEY"}},
                "openrouter": {"baseUrl": "https://openrouter.ai/api/v1", "apiKey": {"source": "env", "id": "OPENROUTER_API_KEY"}},
            }
        },
        "mcp": {
            "servers": {
                "time": {"command": "npx", "args": ["-y", "mcp-server-time"]},
                "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", str(workspace)]},
                "linear": {"url": "https://mcp.linear.app/sse", "headers": {"Authorization": "Bearer ${LINEAR_API_KEY}"}},
            }
        },
        "channels": {
            "telegram": {"botToken": {"source": "env", "id": "TELEGRAM_BOT_TOKEN"}, "allowFrom": ["10001", "10002"]},
            "slack": {"botToken": "xoxb-demo-token", "appToken": "xapp-demo-token", "allowFrom": ["U111", "U222"]},
            "whatsapp": {"allowFrom": ["+15550101"], "sessionName": "default"},
            "signal": {"account": "+15550102", "httpUrl": "http://127.0.0.1:8081", "allowFrom": ["+15550103"]},
        },
        "skills": {
            "entries": {
                "daily-briefing": {"enabled": True, "config": {"timezone": "America/Los_Angeles"}},
                "deep-research": {"enabled": True, "config": {"depth": "balanced"}},
            }
        },
        "memory": {"backend": "wiki", "vectorSearch": True, "citations": True},
        "approvals": {"exec": {"mode": "smart", "rules": [{"pattern": "git *", "decision": "allow"}]}},
        "cron": {"enabled": True},
        "hooks": {"webhooks": [{"name": "linear-updates", "url": "https://hooks.example/linear"}]},
        "browser": {"cdpUrl": "http://127.0.0.1:9222", "headless": False},
        "tools": {"execTimeoutMs": 120000, "webSearch": {"provider": "tavily"}},
        "ui": {"theme": "dark", "assistantName": "OpenClaw"},
        "logging": {"level": "info", "retainDays": 14},
    }
    _write(home / "openclaw.json", _json(openclaw_config))
    _write(
        home / ".env",
        "\n".join(
            [
                "ANTHROPIC_API_KEY=sk-ant-openclaw-example",
                "OPENROUTER_API_KEY=sk-or-openclaw-example",
                "TELEGRAM_BOT_TOKEN=654321:openclaw-demo-token",
                "LINEAR_API_KEY=lin_api_openclaw_demo",
                "ELEVENLABS_API_KEY=el_openclaw_demo",
                "",
            ]
        ),
    )
    _write(
        workspace / "AGENTS.md",
        "# OpenClaw Workspace\n\nDefault agent handles personal operations, research briefs, and task follow-up.\n",
    )
    _write(
        workspace / "MEMORY.md",
        "\n".join(
            [
                "# Memory",
                "",
                "- 2026-02-03: Started using OpenClaw as a Telegram and Slack gateway.",
                "- 2026-02-27: Daily briefing should include calendar, priority tasks, and unread newsletters.",
                "- 2026-03-19: Use Linear project OPS for migration and reliability work.",
                "- 2026-04-11: Prefer explicit dry-runs before apply commands.",
                "",
            ]
        ),
    )
    _write(workspace / "USER.md", "# User\n\nPower user, uses Telegram mobile and a Windows workstation.\n")
    _write_skill(workspace / "skills" / "daily-briefing", "daily-briefing", "Prepare a morning operating brief.")
    _write_skill(workspace / "skills" / "deep-research", "deep-research", "Research a topic with sources and tradeoffs.")
    _write_skill(home / "skills" / "triage-inbox", "triage-inbox", "Cluster inbound messages into decisions and tasks.")
    _write(workspace / "memory" / "2026-02.md", "# February Memory\n\n- OpenClaw gateway stabilized after Telegram setup.\n")
    _write(workspace / "memory" / "2026-03.md", "# March Memory\n\n- Added Linear and filesystem MCP servers.\n")
    _write(workspace / "memory" / "2026-04.md", "# April Memory\n\n- Preparing migration to Thoth.\n")
    _write(
        home / "exec-approvals.json",
        _json({"agents": {"*": {"allowlist": [{"pattern": "git status"}, {"pattern": "python -m unittest *"}]}}}),
    )
    _write(home / "credentials" / "telegram-default-allowFrom.json", _json({"allowFrom": ["10001", "10002"]}))
    _write(home / "cron-store.json", _json({"jobs": [{"name": "Daily briefing", "schedule": "daily:08:00"}]}))
    _write(home / "hooks.json", _json({"webhooks": [{"name": "linear-updates", "enabled": True}]}))
    _write(home / "sessions" / "main" / "2026-04-20.jsonl", '{"channel":"telegram","text":"what changed today?"}\n')
    _write(home / "logs" / "gateway.log", "2026-04-20T09:00:00Z INFO gateway online\n")
    _write(home / "plugins" / "installed.json", _json({"plugins": ["openclaw-honcho", "openclaw-telegram"]}))
    _write(home / "workspace" / ".agents" / "skills" / "project-context" / "SKILL.md", "---\nname: project-context\ndescription: Project context\n---\n\nUse workspace notes.\n")
    _write(home / "state.db", "sqlite placeholder for OpenClaw gateway state\n")
    return home


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_skill(path: Path, name: str, description: str) -> None:
    _write(
        path / "SKILL.md",
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                "version: 1.0.0",
                "---",
                "",
                f"# {name}",
                "",
                description,
                "",
            ]
        ),
    )


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
