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

Plugins are installed disabled by default. A user should review permissions,
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
the runtime inventory.

## Local Developer Workflow

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
