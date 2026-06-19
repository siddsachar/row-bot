"""Generate curated real-UI public docs pages from coverage metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from textwrap import dedent
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs-site" / "docs"
METADATA = ROOT / "docs-content" / "metadata"


def _load_yaml(name: str) -> dict[str, Any]:
    data = yaml.safe_load((METADATA / name).read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _write(rel: str, text: str) -> None:
    path = DOCS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).strip() + "\n", encoding="utf-8")


def _front(title: str, description: str) -> str:
    return (
        "---\n"
        f"title: {json.dumps(title)}\n"
        f"description: {json.dumps(description)}\n"
        "---\n"
    )


def _screenshot(sid: str, alt: str, caption: str) -> str:
    return f'\n<Screenshot id="{sid}" alt="{alt}" caption="{caption}" />\n'


def _common_surface_sections(*, local: str, external: str, saved: str, credentials: str) -> str:
    return f"""
## What Is Saved

{saved}

## Local And External Behavior

{local}

{external}

## Credentials And Safety

{credentials}

Actions that can change files, call external systems, start servers, or use MCP tools follow the active approval policy. When a surface is disconnected or empty, Row-Bot keeps the controls visible so you can see what still needs setup.
"""


def write_core_pages() -> None:
    _write(
        "index.mdx",
        _front("Row-Bot Documentation", "Public documentation for installing, configuring, and using the real Row-Bot app.")
        + """
import PagefindSearch from '@site/src/components/PagefindSearch';
import Screenshot from '@site/src/components/Screenshot';

# Row-Bot Documentation

Row-Bot is a local-first desktop AI assistant for chat, tools, workflows, Designer Studio, Developer Studio, knowledge, skills, plugins, MCP, channels, voice, and safety-reviewed automation.

This docs site is built from source inventory, route metadata, generated references, and screenshots captured from the actual NiceGUI app at `/`. Capture mode can seed fake demo data and disable side effects, but it does not render replacement screenshot UI.

<Screenshot id="app-shell-overview" alt="Row-Bot app shell with sidebar and main Home surface." caption="The real app shell captured from the NiceGUI route." />

## Start Here

<div className="rowBotPanelGrid">
  <div className="rowBotPanel"><h3>Install</h3><p>Choose a platform and understand where Row-Bot stores local data.</p><a href="/docs/getting-started/installation">Installation</a></div>
  <div className="rowBotPanel"><h3>First Launch</h3><p>Connect the first model path, then continue through optional setup areas.</p><a href="/docs/getting-started/first-launch">First launch</a></div>
  <div className="rowBotPanel"><h3>App Shell</h3><p>Learn navigation, threads, status surfaces, command center, terminal, and settings.</p><a href="/docs/app-shell/navigation">Navigation</a></div>
  <div className="rowBotPanel"><h3>Settings</h3><p>Every Settings tab is documented from the real tab list.</p><a href="/docs/settings/">Settings reference</a></div>
</div>

## Search

<PagefindSearch />

## Code-Backed Reference

Generated reference pages cover tools, providers, Home tabs, Settings tabs, channels, skills, MCP, plugins, storage, environment variables, screenshots, and approvals. Start with [Generated Reference](/docs/reference/generated/).
""",
    )
    _write(
        "getting-started/index.mdx",
        _front("Getting Started", "Start Row-Bot, connect a model, and understand the real first-run flow.")
        + """
# Getting Started

Use the installation page for platform setup, then use first launch to choose a local, API, subscription, or custom endpoint model path. Row-Bot stores app state under the active data directory, which can be overridden with `ROW_BOT_DATA_DIR` for tests or portable runs.

- [Installation](/docs/getting-started/installation)
- [First Launch](/docs/getting-started/first-launch)
- [App Shell Navigation](/docs/app-shell/navigation)
""",
    )
    _write(
        "getting-started/installation.mdx",
        _front("Installation", "Install Row-Bot and identify the local data directory used by the app.")
        + """
# Installation

Install Row-Bot from the release package for your platform, or run it from source with the Python dependencies installed. The app starts a local NiceGUI server and opens a browser or native window depending on launch settings.

## Data Directory

Row-Bot keeps local app data in the active Row-Bot data directory. Set `ROW_BOT_DATA_DIR` to isolate test, review, or portable runs. The docs capture flow always uses a temporary data directory and generated demo files.

## After Install

Open Row-Bot, complete the first model setup path, then use the Setup Center for documents, workflows, Designer, Developer, channels, accounts, MCP, plugins, and voice.
""",
    )
    _write(
        "getting-started/first-launch.mdx",
        _front("First Launch", "Use the real first-run wizard and Setup Center.")
        + """
import Screenshot from '@site/src/components/Screenshot';

# First Launch

The first-run wizard appears until `setup_complete` is saved in the app config. It asks you to connect one working model path first; every other feature can be configured later.

"""
        + _screenshot("first-launch-setup-wizard", "Row-Bot first launch wizard.", "The real first-launch wizard with local, API provider, and custom endpoint paths.")
        + """
## Model Paths

- Local Ollama keeps model inference on the machine when Ollama and the chosen model are available.
- API providers require credentials and send prompts to the selected provider.
- Custom endpoints can be local or remote OpenAI-compatible servers; the endpoint location determines the privacy boundary.

"""
        + _screenshot("setup-center", "Row-Bot Setup Center.", "The real Setup Center for optional post-model setup.")
        + _common_surface_sections(
            saved="Setup progress is saved in app config, including completed and skipped Setup Center steps.",
            local="Local setup choices can stay entirely on the machine when using local models and local files.",
            external="API providers, account integrations, channels, MCP servers, and plugins can contact external services only after you configure and start them.",
            credentials="Credential fields are password-style controls. Docs screenshots use fake demo state and mask sensitive inputs.",
        ),
    )
    _write(
        "app-shell/navigation.mdx",
        _front("Navigation", "Understand the real Row-Bot app shell, sidebar, thread list, command/status surfaces, and terminal panel.")
        + """
import Screenshot from '@site/src/components/Screenshot';

# Navigation

The app shell combines the left sidebar, thread list and filters, Home button, new chat flow, central main surface, command/status surfaces, Buddy, and the inline terminal panel.

"""
        + _screenshot("app-shell-overview", "Row-Bot app shell overview.", "The real app shell with Home selected.")
        + """
## Visible Areas

- Sidebar: Home, new chat, thread filters, thread list, settings entry points, and setup/status access.
- Main content: Home, chat, Designer editor, Developer workspace, or other active surface.
- Command/status surfaces: running workflows, approvals, status messages, and operational controls.
- Terminal panel: shown inline when terminal tooling is available; commands still follow approval and capability policy.

"""
        + _common_surface_sections(
            saved="Thread metadata, drafts, local UI preferences, and workflow state are saved under the app data directory.",
            local="Navigation itself is local UI. Opening a thread reads local checkpoint and metadata databases.",
            external="External effects only occur when a selected tool, channel, provider, MCP server, or plugin is configured and invoked.",
            credentials="The sidebar can surface account or provider attention states, but it does not expose saved secret values.",
        ),
    )


def write_chat_pages() -> None:
    _write(
        "chat/index.mdx",
        _front("Chat", "Use Row-Bot chat, composer controls, model choices, attachments, skills, and exports.")
        + """
import Screenshot from '@site/src/components/Screenshot';

# Chat

Chat is the main conversation surface. It shows the active thread name, transcript, model/privacy banner, parent agent and goal strips when relevant, the composer, attachment controls, model and approval controls, voice buttons, send/stop buttons, and export when messages exist.

"""
        + _screenshot("chat-main", "Row-Bot chat surface with seeded messages.", "The real chat surface rendered from seeded safe demo state.")
        + """
## Controls In The Screenshot

- Thread header: current thread title, rename, profile picker, and export when messages are present.
- Transcript: user and assistant messages, tool results, charts, images, and reasoning sections when present.
- Composer: textarea, attachments, model and approval controls, Smart Skill suggestions, slash commands, voice, send, and stop.
- Model banner: indicates local/private or cloud/provider execution before a run.

"""
        + _common_surface_sections(
            saved="Thread metadata lives in `threads.db`; transcript checkpoints and media sidecars are stored under the active data directory.",
            local="Local model runs and local tools can stay on the machine. File attachments are copied into Row-Bot-managed storage before use.",
            external="Cloud models, web search, browser actions, account tools, MCP tools, channels, and plugins can call outside services only when configured and invoked.",
            credentials="Secrets are not inserted into chat. Approval prompts are used for sensitive file, shell, browser, MCP, and external actions.",
        ),
    )
    _write(
        "chat/model-picker.mdx",
        _front("Model Picker", "Understand model choices, provider identity, Quick Choices, and execution boundaries.")
        + """
# Model Picker

The composer model controls choose which provider-qualified model handles the next run. The visible labels separate local models, hosted API providers, subscription-backed providers, and custom endpoints.

The focused model-picker screenshot is deferred until the composer policy cluster has stable real selectors. The current chat screenshot still shows the live model/privacy banner and composer area.

## Saved State

Quick Choices, defaults, context settings, and model catalog cache entries are saved locally. Provider credentials stay in the configured secure or session storage path rather than in docs or screenshots.

## Safety

Changing a model can change where prompts and tool results are sent. Check the banner before sending sensitive content.
""",
    )
    _write(
        "chat/tools-approvals-and-terminal.mdx",
        _front("Tools, Approvals, And Terminal", "Review tool traces, approval policy, export behavior, and terminal safety.")
        + """
# Tools, Approvals, And Terminal

Tool activity appears with the conversation so you can inspect what Row-Bot read, wrote, searched, or requested. Sensitive actions follow the active approval policy.

## Tool Trace

Tool trace rendering is part of the real chat transcript. The focused trace screenshot is deferred until a dedicated selector is added to `row_bot.ui.tool_trace`, but the chat screenshot includes seeded tool-result state.

## Approvals

Approval prompts are used for operations such as filesystem writes, shell commands, browser automation, MCP tool calls, Developer workspace edits, and channel actions. A pending approval fixture is required before a deterministic screenshot can be captured.

## Terminal Panel

The terminal panel is rendered inside the real app shell. Commands are subject to tool availability and approval mode. Docs capture disables unsafe startup side effects and does not start real terminal workloads.
""",
    )


def write_home_pages() -> None:
    data = _load_yaml("home_tabs.yml").get("tabs", {})
    details = {
        "Workflows": ("Background Agents, schedules, delivery defaults, run status, bulk actions, and workflow dialogs.", "home-workflows"),
        "Designer": ("Designer Studio entry point for projects, generated pages, decks, mockups, imports, exports, brand config, and review.", "home-designer"),
        "Developer": ("Developer Studio entry point for workspaces, repos, diffs, terminal/server controls, approvals, git actions, and Custom Tools.", "home-developer"),
        "Knowledge": ("Knowledge graph, memory review, document relationships, and audit flows.", "home-knowledge"),
        "Monitor": ("Knowledge extraction, dream cycle, channels, recent logs, and system activity.", "home-monitor"),
    }
    for name in data:
        desc, shot = details[name]
        slug = name.lower()
        _write(
            f"home/{slug}.mdx",
            _front(f"Home: {name}", f"Documented real Home {name} tab.")
            + f"""
import Screenshot from '@site/src/components/Screenshot';

# Home: {name}

{desc}

"""
            + _screenshot(shot, f"Row-Bot Home {name} tab.", f"The real Home {name} tab captured from the app.")
            + f"""
## How To Open

Choose Home in the sidebar, then select the **{name}** tab.

## Visible Sections

The tab is built by `src/row_bot/ui/home.py`. Its controls vary with local app state, but the screenshot shows the real tab shell, labels, and empty or seeded-state cards that are safe for public docs.

"""
            + _common_surface_sections(
                saved="Home tab state reads from local workflow, memory, thread, Designer, Developer, and log storage depending on the tab.",
                local="Opening the tab is local UI work. Docs capture disables background autostart while preserving the real tab builders.",
                external="External work starts only from configured workflows, providers, channels, MCP servers, plugins, or Developer actions.",
                credentials="Home tabs can show disconnected states, but seeded docs data never includes real credentials or account names.",
            ),
        )
    _write(
        "home/index.mdx",
        _front("Home", "Overview of every real Home tab in Row-Bot.")
        + """
# Home

The Home surface has five real tabs:

- [Workflows](/docs/home/workflows)
- [Designer](/docs/home/designer)
- [Developer](/docs/home/developer)
- [Knowledge](/docs/home/knowledge)
- [Monitor](/docs/home/monitor)
""",
    )


def write_settings_pages() -> None:
    tabs = _load_yaml("settings.yml").get("tabs", {})
    _write(
        "settings/index.mdx",
        _front("Settings", "Overview of every real Row-Bot Settings tab.")
        + """
# Settings

Settings is a maximized real NiceGUI dialog. It contains every configuration tab listed in `src/row_bot/ui/settings.py`: Providers, Models, Documents, Search, Skills, System, Accounts, Utilities, Tracker, Knowledge, Buddy, Voice, Channels, MCP, Plugins, and Preferences.

Use the pages below for tab-by-tab behavior, saved state, credentials, side effects, and troubleshooting.

"""
        + "\n".join(f"- [{name}]({meta['docs_route']})" for name, meta in tabs.items()),
    )
    tab_notes = {
        "Providers": "Connection cards, credential status, subscription/API/custom provider paths, validation buttons, and catalog refresh behavior.",
        "Models": "Default model choices, Quick Choices, context window settings, local/cloud catalog concepts, and model capability signals.",
        "Documents": "Document upload, processed file records, embedding engine selection, vector rebuild/reset controls, and document removal.",
        "Search": "Retrieval compression, web research providers, browser/search tool availability, and search-related diagnostics.",
        "Skills": "Skill enablement, pinned defaults, Skills Hub access, installed skill state, and per-thread skill behavior.",
        "System": "Workspace and data paths, local access controls, shell/browser tooling, tunnels, logs, launch behavior, and update controls.",
        "Accounts": "GitHub, Google, X, and other account connection states. Public docs show disconnected or attention states only.",
        "Utilities": "Built-in utility tools and small productivity integrations available to the assistant.",
        "Tracker": "Recurring activities, habits, symptoms, health/event logging, charts, and tracker preferences.",
        "Knowledge": "Memory extraction, graph health, document knowledge, wiki vault export, embedding behavior, and audit/review flows.",
        "Buddy": "In-app companion behavior, look, desktop overlay, motion, generated-safe state, and reduced-motion behavior.",
        "Voice": "Dictation, local talk, realtime talk, read-aloud, voice models, diagnostics, and microphone/runtime safety.",
        "Channels": "Telegram, WhatsApp, Discord, Slack, SMS, delivery defaults, channel credentials, and health/status cards.",
        "MCP": "Recommended servers, imports, add/test controls, disabled-by-default server configs, and overlap/safety warnings.",
        "Plugins": "Installed plugins, plugin manifest validation, marketplace entry points, Custom Tools promotion, and sandbox state.",
        "Preferences": "Assistant identity, personality, launch behavior, background intelligence, updates, migration, and self-improvement toggle.",
    }
    for name, meta in tabs.items():
        sid = meta["screenshot_id"]
        slug = name.lower()
        _write(
            f"settings/{slug}.mdx",
            _front(f"Settings: {name}", f"Documented real Settings {name} tab.")
            + f"""
import Screenshot from '@site/src/components/Screenshot';

# Settings: {name}

{tab_notes[name]}

"""
            + _screenshot(sid, f"Row-Bot Settings {name} tab.", f"The real Settings {name} tab captured from the app.")
            + f"""
## How To Open

Open Settings from the sidebar or setup/status surfaces, then choose **{name}**.

## Visible Sections And Controls

The tab is documented from `{meta.get('source', 'src/row_bot/ui/settings.py')}` and the real Settings tab inventory. Controls include the tab header, setting groups, status rows, action buttons, and empty/error states shown by the current local configuration.

"""
            + _common_surface_sections(
                saved=f"{name} settings are saved under the active Row-Bot data directory using the app's normal config, database, or provider-specific storage APIs.",
                local="Opening and editing the tab is local UI work. Local-only options affect files, app config, or local services.",
                external="Provider checks, account auth, channel starts, MCP servers, plugin installs, and network probes are external or side-effectful only when the user explicitly configures or starts them.",
                credentials="Password and token controls are masked. Docs demo data uses fake disconnected/example states and never stores real credentials.",
            ),
        )


def write_integration_pages() -> None:
    pages = {
        "integrations/channels.mdx": ("Channels", "Configure messaging channels, delivery defaults, and disconnected-safe channel states.", "settings-channels"),
        "integrations/mcp.mdx": ("MCP", "Add, import, test, enable, and troubleshoot MCP servers safely.", "settings-mcp"),
        "integrations/plugins.mdx": ("Plugins", "Manage installed plugins, marketplace review, manifests, and Custom Tools.", "settings-plugins"),
        "skills/index.mdx": ("Skills", "Enable, pin, import, browse, and review Smart Skills.", "settings-skills"),
        "voice-and-buddy/index.mdx": ("Voice And Buddy", "Configure voice input/output and Buddy companion behavior.", "settings-voice"),
        "designer/index.mdx": ("Designer Studio", "Use Designer Studio from Home Designer and project editor surfaces.", "home-designer"),
        "developer/index.mdx": ("Developer Studio", "Use Developer Studio from Home Developer and workspace surfaces.", "home-developer"),
    }
    for rel, (title, desc, shot) in pages.items():
        _write(
            rel,
            _front(title, desc)
            + f"""
import Screenshot from '@site/src/components/Screenshot';

# {title}

{desc}

"""
            + _screenshot(shot, f"Row-Bot {title} surface.", f"Related real Row-Bot surface for {title}.")
            + _common_surface_sections(
                saved="Configuration is saved under the active Row-Bot data directory.",
                local="The documented UI opens locally. Side effects are disabled during docs capture.",
                external="External services are contacted only when credentials/configuration are added and the user starts or invokes the integration.",
                credentials="Public demo screenshots use example domains, disconnected states, or masked controls.",
            ),
        )
    _write(
        "privacy-safety/index.mdx",
        _front("Privacy And Safety", "Understand local/external boundaries, approvals, credentials, and safe capture behavior.")
        + """
# Privacy And Safety

Row-Bot separates local UI state from external provider, account, channel, MCP, plugin, shell, browser, and filesystem actions. Sensitive operations can require approval, and credential controls are masked.

## Docs Capture Safety

Public docs screenshots are captured from the real NiceGUI app route with `ROW_BOT_DOCS_CAPTURE=1`, a temporary `ROW_BOT_DATA_DIR`, fixed time, reduced motion, fake provider readiness, disabled autostart, and no real credentials.

## Approval Examples

Approval screenshots are deferred until a deterministic pending-interrupt fixture exists. The docs still describe the behavior: file writes, shell commands, browser automation, MCP tools, Developer edits, and external actions can ask before continuing or be blocked by policy.
""",
    )
    _write(
        "troubleshooting/index.mdx",
        _front("Troubleshooting", "Resolve common setup, model, data, documents, channels, MCP, plugin, and capture issues safely.")
        + """
# Troubleshooting

Start with the visible status text in the app shell, Setup Center, Settings tabs, and Monitor tab. Then check the local logs under the active data directory.

## Common Checks

- Model unavailable: open Settings > Providers and Settings > Models.
- Documents not searchable: open Settings > Documents and check indexing state.
- Workflows not running: open Home > Workflows and verify delivery defaults and approvals.
- Channels disconnected: open Settings > Channels and keep credentials masked.
- MCP or plugins unsafe: keep servers/plugins disabled until reviewed.
- Voice unavailable: open Settings > Voice and confirm provider and microphone/runtime settings.

Never paste real credentials into screenshots, docs fixtures, or review artifacts.
""",
    )


def write_compat_pages() -> None:
    replacements = {
        "ui-tour/index.mdx": ("UI Tour", "The UI tour now maps to the real app shell, Home, Chat, and Settings pages.", "/docs/app-shell/navigation"),
        "configuration/models-and-providers.mdx": ("Models And Providers", "Model and provider docs now live under Settings Providers and Settings Models.", "/docs/settings/providers"),
        "guides/workflows.mdx": ("Workflows Guide", "Workflow docs now live under Home Workflows.", "/docs/home/workflows"),
        "guides/designer-studio.mdx": ("Designer Studio Guide", "Designer docs now live under Home Designer and Designer Studio.", "/docs/home/designer"),
        "guides/developer-studio.mdx": ("Developer Studio Guide", "Developer docs now live under Home Developer and Developer Studio.", "/docs/home/developer"),
        "guides/skills-plugins-mcp.mdx": ("Skills, Plugins, And MCP", "These docs now live under Skills, Settings Plugins, and Settings MCP.", "/docs/skills/"),
        "guides/channels-and-voice.mdx": ("Channels And Voice", "These docs now live under Settings Channels, Settings Voice, and Voice And Buddy.", "/docs/settings/channels"),
    }
    for rel, (title, desc, target) in replacements.items():
        _write(
            rel,
            _front(title, desc)
            + f"""
# {title}

This compatibility page remains so older docs links keep working.

Continue to [{desc}]({target}).
""",
        )
    _write(
        "reference/index.mdx",
        _front("Reference", "Generated source-backed reference pages for Row-Bot public docs.")
        + """
# Reference

Generated pages are refreshed from source inventory and metadata.

- [Tools](/docs/reference/generated/tools)
- [Providers](/docs/reference/generated/providers)
- [Settings](/docs/reference/generated/settings)
- [Home Tabs](/docs/reference/generated/home-tabs)
- [Channels](/docs/reference/generated/channels)
- [Skills](/docs/reference/generated/skills)
- [MCP](/docs/reference/generated/mcp)
- [Plugins](/docs/reference/generated/plugins)
- [Data Storage](/docs/reference/generated/data-storage)
- [Safety And Approvals](/docs/reference/generated/safety-approvals)
- [Environment And Config](/docs/reference/generated/environment-and-config)
- [Screenshots](/docs/reference/generated/screenshots)
""",
    )


def write_sidebars() -> None:
    sidebars = ROOT / "docs-site" / "sidebars.ts"
    sidebars.write_text(
        dedent(
            """
            import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

            const sidebars: SidebarsConfig = {
              docs: [
                'index',
                {
                  type: 'category',
                  label: 'Getting Started',
                  collapsed: false,
                  items: ['getting-started/index', 'getting-started/installation', 'getting-started/first-launch'],
                },
                {
                  type: 'category',
                  label: 'App Shell',
                  collapsed: false,
                  items: ['app-shell/navigation'],
                },
                {
                  type: 'category',
                  label: 'Chat',
                  collapsed: false,
                  items: ['chat/index', 'chat/model-picker', 'chat/tools-approvals-and-terminal'],
                },
                {
                  type: 'category',
                  label: 'Home',
                  collapsed: false,
                  items: ['home/workflows', 'home/designer', 'home/developer', 'home/knowledge', 'home/monitor'],
                },
                'designer/index',
                'developer/index',
                {
                  type: 'category',
                  label: 'Settings',
                  collapsed: false,
                  items: [
                    'settings/index',
                    'settings/providers',
                    'settings/models',
                    'settings/documents',
                    'settings/search',
                    'settings/skills',
                    'settings/system',
                    'settings/accounts',
                    'settings/utilities',
                    'settings/tracker',
                    'settings/knowledge',
                    'settings/buddy',
                    'settings/voice',
                    'settings/channels',
                    'settings/mcp',
                    'settings/plugins',
                    'settings/preferences',
                  ],
                },
                {
                  type: 'category',
                  label: 'Integrations',
                  collapsed: false,
                  items: ['integrations/channels', 'integrations/mcp', 'integrations/plugins', 'skills/index', 'voice-and-buddy/index'],
                },
                'privacy-safety/index',
                'troubleshooting/index',
                {
                  type: 'category',
                  label: 'Reference',
                  collapsed: false,
                  items: [
                    'reference/index',
                    {
                      type: 'category',
                      label: 'Generated',
                      collapsed: false,
                      items: [
                        'reference/generated/index',
                        'reference/generated/tools',
                        'reference/generated/providers',
                        'reference/generated/settings',
                        'reference/generated/home-tabs',
                        'reference/generated/channels',
                        'reference/generated/skills',
                        'reference/generated/mcp',
                        'reference/generated/plugins',
                        'reference/generated/data-storage',
                        'reference/generated/safety-approvals',
                        'reference/generated/environment-and-config',
                        'reference/generated/screenshots',
                      ],
                    },
                  ],
                },
              ],
            };

            export default sidebars;
            """
        ).lstrip(),
        encoding="utf-8",
    )


def write_readmes() -> None:
    (ROOT / "docs-site" / "README.md").write_text(
        dedent(
            """
            # Row-Bot Docs Site

            This Docusaurus site is a review-only public documentation build. It is not wired into the current public website and is not published by CI.

            ## Local Preview

            ```powershell
            cd docs-site
            npm ci
            npm run start
            ```

            ## Validation

            ```powershell
            python scripts\\docs\\collect_inventory.py --out docs-build\\inventory
            python scripts\\docs\\capture_real_ui_screenshots.py --validate-only
            python scripts\\docs\\validate_public_docs.py
            cd docs-site
            npm run build:ci
            ```

            Full screenshot recapture is local-only for now:

            ```powershell
            python scripts\\docs\\seed_real_app_demo_data.py --scenario full --data-dir docs-build\\demo-data
            python scripts\\docs\\capture_real_ui_screenshots.py --scenario full
            ```
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (ROOT / "docs-content" / "review-status.md").write_text(
        dedent(
            """
            # Real UI Docs Review Status

            - Public website under `docs/` remains unchanged.
            - Screenshots are captured from the real NiceGUI `/` route.
            - Deferred screenshots are listed in `docs-build/reports/docs-real-ui-review.md`.
            - CI remains validation-only and does not publish `docs-site/build`.
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate curated real UI docs pages")
    parser.parse_args()
    write_core_pages()
    write_chat_pages()
    write_home_pages()
    write_settings_pages()
    write_integration_pages()
    write_compat_pages()
    write_sidebars()
    write_readmes()
    print(f"Wrote real UI docs pages under {DOCS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
