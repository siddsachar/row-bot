# Row-Bot Plugin System v2

Plugin System v2 is the supported extension model for Row-Bot plugins. A plugin
may add only these surfaces:

- Native tools registered with `PluginAPI` and `PluginTool`
- Plugin-packaged MCP-backed tools declared as `mcp_servers`
- Bundled skills under `skills/<skill_id>/SKILL.md`
- Channels registered with `PluginAPI.register_channel(...)`

Plugins must not add arbitrary app panels, custom NiceGUI, JavaScript, provider
runtimes, memory providers, workflow triggers, general hooks, or custom settings
tabs. Row-Bot owns one native Plugin Center that renders plugin metadata,
permissions, settings, secrets, auth, health checks, tools, channels, skills,
logs, updates, and enablement.

## Manifest v2

Each plugin directory contains `plugin.json` with `schema_version: 2`.

Required fields:

- `schema_version`
- `id`
- `name`
- `version`
- `min_row_bot_version`
- `description`
- `provides`

Supported `provides` keys:

- `native_tools`
- `mcp_servers`
- `channels`
- `skills`

Supported permissions:

- `network`
- `files`
- `account`
- `external_send`
- `messaging`
- `memory_documents`
- `shell_processes`

Settings and secrets are declarative. Secret values live in the OS keyring
through Row-Bot plugin state; plugin files and marketplace indexes must not
contain real API keys, tokens, private local paths, or real user data.

## Runtime Rules

Plugins are installed and kept off by default. A user should review permissions,
configure required settings and secrets, run Plugin Center health checks, then
enable the plugin.

Plugin code should import Row-Bot only through:

```python
from plugins.api import PluginTool
from plugins.api import Channel
```

The loader blocks imports from Row-Bot internals and UI frameworks. Channel
plugins can subclass the public `Channel` export, but plugin-owned channels do
not render arbitrary settings UI and do not render `build_custom_ui`; their
setup is rendered by Plugin Center metadata.

Plugin-packaged MCP servers follow plugin enablement. Disabling a plugin removes
native tools, plugin MCP tools, plugin skills, and plugin-owned channels from
the runtime inventory. Agent Profiles that use `Selected tools only` also scope
plugin-bundled skills: if the profile does not select the plugin's runtime tool,
the plugin's auto-injected skill instructions are not included for that profile.

## Public Channel Runtime API

Plugin channels may act as transport adapters while Row-Bot core owns agent
execution, shared slash commands, approval gates, Goal Mode, media processing,
pairing, and webhook lifecycle. Plugin code must keep using `plugins.api`; it
must not import `row_bot.agent`, `row_bot.channels.*`, `row_bot.tasks`,
`row_bot.tunnel`, NiceGUI, or other Row-Bot internals.

The public channel bridge is exposed from `plugins.api`:

- `ChannelInboundMessage`, `ChannelAttachment`, and
  `ChannelOutboundCallbacks` describe inbound platform messages, attachments,
  and platform-specific send/edit callbacks.
- `PluginAPI.handle_channel_message(...)` routes inbound text and attachments
  through Row-Bot's channel runtime.
- `PluginAPI.handle_channel_approval(...)` resumes interrupted agent turns after
  an approve/deny action.
- `PluginAPI.process_channel_attachment(...)` reuses Row-Bot's shared media
  pipeline for downloaded or local attachments. URL-only attachments are not
  fetched by core.
- `PluginAPI.register_webhook_route(...)`, `get_webhook_path(...)`, and
  `get_webhook_url(...)` register namespaced plugin webhooks under
  `/plugin-webhooks/{plugin_id}/{name}` without exposing Starlette or NiceGUI
  types to plugin code.
- Pairing and allowlist helpers on `PluginAPI` wrap the same channel auth store
  used by native channels.

Webhook handlers are disabled when a plugin is disabled, unloaded, uninstalled,
or fails during load. Channel adapters registered by a plugin must match
`provides.channels` in `plugin.json`; Row-Bot accepts the current
hyphen-to-underscore channel-name compatibility but rejects unrelated channel
names.

## Local Developer Workflow

The contributor-facing marketplace documentation lives in the
`siddsachar/row-bot-plugins` repository:

- `README.md`: quick start and repository layout
- `CONTRIBUTING.md`: contribution workflow and review expectations
- `AGENTS.md`: instructions for AI coding agents
- `docs/PLUGIN_AUTHOR_GUIDE.md`: end-to-end plugin authoring guide
- `docs/MANIFEST_V2_REFERENCE.md`: manifest field reference
- `docs/VALIDATION_AND_CATALOG.md`: validation, index, and local marketplace flow
- `docs/PLUGIN_REVIEW_CHECKLIST.md`: reviewer checklist

Keep Row-Bot and the plugin repository adjacent while developing:

```text
Code/
  row-bot/
  row-bot-plugins/
```

Validate a plugin directory:

```powershell
$PluginRepo = "$env:USERPROFILE\Code\row-bot-plugins"
uv run python scripts/validate_plugin.py "$PluginRepo\plugins\office365"
```

Link a plugin for local development:

```powershell
uv run python launcher.py plugins link "$PluginRepo\plugins\office365"
uv run python launcher.py plugins reload office365
uv run python launcher.py plugins doctor office365
```

Build a local marketplace index:

```powershell
uv run python scripts/build_plugin_index.py "$PluginRepo" --source "$PluginRepo"
```

Validate the full marketplace repository from the Row-Bot checkout:

```powershell
$env:ROW_BOT_SOURCE = (Get-Location)
uv run python "$PluginRepo\scripts\validate_repo.py" "$PluginRepo"
```

For a local fixture repo, the expected shape is:

```text
row-bot-plugins/
  index.json
  plugins/
    hello-tool/
    settings-secrets-tool/
    mcp-backed-tool/
    fake-channel/
  templates/
    native-tool/
    mcp-tool/
    channel/
  scripts/
    validate_plugin.py
    build_index.py
```

Set `ROW_BOT_PLUGIN_INDEX_URL` to a plain local `index.json` path, a `file://`
URL, or a remote HTTPS index while testing the marketplace flow. Local index
entries can use relative `path` values; Row-Bot resolves them against the index
`source`, verifies the `sha256:` checksum when present, installs disabled, and
reuses the same source/checksum data for updates. Entries can also provide an
`archive_url` that points at a local or remote zip archive containing either the
plugin directory itself or a `plugins/<plugin_id>/` tree.

## Health Checks

Plugin Center deterministically evaluates local checks without live providers,
live channels, or real network calls:

- Required settings present
- Required secrets present
- Channel declaration present for `channel_configured`
- MCP launch config present for `mcp_server_starts` and `mcp_tools_discovered`

Health checks that require real service probes, OAuth refreshes, or dry-run
sends should be declared, but they remain manual/informational local checks and
must not run in deterministic test lanes. They do not block local enablement
after required settings and secrets pass.

## Test Guidance

Default tests must not depend on live providers, live MCP servers, live channels,
real network availability, real messages, or a specific local Ollama model.

Useful focused commands:

```powershell
uv run python -m pytest tests\subsystem\plugins tests\subsystem\channels tests\contracts\plugins\test_plugin_api_contract.py tests\contracts\test_channel_contract.py -q
uv run python -m pytest tests\subsystem\plugins tests\subsystem\mcp\test_mcp_runtime_tools.py tests\subsystem\channels tests\contracts\plugins\test_plugin_api_contract.py tests\contracts\test_channel_contract.py -q
```

For shared or release-sensitive changes, use:

```powershell
uv run python scripts/run_test_matrix.py pr
```
