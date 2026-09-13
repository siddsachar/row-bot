from __future__ import annotations

import base64
import builtins
from dataclasses import asdict
import json
import socket
import sys
from types import SimpleNamespace

import pytest

from row_bot import agent_tool_catalog as catalog


@pytest.fixture
def owners(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    from row_bot.tools import registry as core
    from row_bot.plugins import registry as plugins, state
    from row_bot.mcp_client import config, runtime

    monkeypatch.setattr(core, "_tools", {})
    monkeypatch.setattr(core, "_enabled", {})
    monkeypatch.setattr(core, "_active_config_path", tmp_path / "tools_config.json")
    monkeypatch.setattr(plugins, "_plugin_tools", {})
    monkeypatch.setattr(plugins, "_tool_to_plugin", {})
    monkeypatch.setattr(plugins, "_loaded_manifests", {})
    monkeypatch.setattr(state, "DATA_DIR", tmp_path)
    monkeypatch.setattr(state, "_state", {})
    monkeypatch.setattr(state, "_loaded", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_config_cache", {"enabled": True, "servers": {}})
    monkeypatch.setattr(runtime, "_catalog", {})
    return SimpleNamespace(core=core, plugins=plugins, state=state, config=config, runtime=runtime)


def _core(owners, identity="lookup", *, enabled=True, label="Lookup"):
    owners.core._tools[identity] = SimpleNamespace(display_name=label, destructive_tool_names=set())
    owners.core._enabled[identity] = enabled


def _plugin(owners, identity="plugin_lookup", *, plugin_id="lookup-plugin", enabled=True):
    owners.plugins._plugin_tools[identity] = SimpleNamespace(
        display_name="Plugin lookup", plugin_api=SimpleNamespace(_registration_revoked=False),
        destructive_tool_names={"delete"},
    )
    owners.plugins._tool_to_plugin[identity] = plugin_id
    owners.state._state[plugin_id] = {"enabled": enabled, "config": {"private": "DO_NOT_RETURN"}}


def _mcp(owners, *, plugin_id=None):
    info = owners.runtime.McpToolInfo("local", "echo", "mcp_local_echo", enabled=True,
                                     input_schema={"private": "DO_NOT_RETURN"},
                                     source={"plugin_id": plugin_id} if plugin_id else {})
    owners.runtime._catalog["local"] = {"echo": info}
    owners.config._config_cache["servers"]["local"] = {
        "enabled": True, "tools": {"enabled": {"echo": True}},
        "env": {"TOKEN": "DO_NOT_RETURN"}, "url": "https://private.invalid/DO_NOT_RETURN",
    }
    return info


def _row(result, identity):
    return next(row for row in result.items if row.id == identity)


def test_unloaded_owners_are_not_imported(monkeypatch):
    names = ("row_bot.tools.registry", "row_bot.plugins.registry", "row_bot.mcp_client.runtime")
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    result = catalog.list_cached_tools()
    assert result.items == () and result.total == 0
    assert result.freshness == "unavailable" and result.generated_at is None
    assert all(item.state == "unavailable" and item.total is None for item in result.sources)
    assert not any(name in sys.modules for name in names)


def test_loaded_empty_is_distinct_from_unavailable(owners):
    result = catalog.list_cached_tools()
    assert result.items == () and result.freshness == "cached"
    assert all(item.state == "cached" and item.total == 0 for item in result.sources)


def test_sources_exact_identities_and_nullable_states(owners):
    _core(owners)
    _core(owners, "disabled", enabled=False)
    _plugin(owners)
    _plugin(owners, "custom_tool_example", plugin_id="custom-tool-example")
    _mcp(owners)
    result = catalog.list_cached_tools()
    assert result.schema_version == 1 and result.generated_at is None
    assert _row(result, "lookup").enabled is True
    assert _row(result, "disabled").enabled is False
    assert _row(result, "plugin_lookup").source == "plugin"
    assert _row(result, "custom_tool_example").source == "custom"
    assert _row(result, "mcp_local_echo").configured is True
    assert _row(result, "mcp_local_echo").parent_id == "mcp"
    assert _row(result, "plugin_lookup").configured is None
    assert all(row.runtime_state == "unknown" for row in result.items)
    assert "DO_NOT_RETURN" not in json.dumps(asdict(result))


@pytest.mark.parametrize("tag", ["custom-tool", " Custom-Tool "])
def test_custom_tags_preserve_existing_source_classification(owners, tag):
    _plugin(owners)
    owners.plugins._loaded_manifests["lookup-plugin"] = SimpleNamespace(tags=[tag])
    assert catalog.list_cached_tools().items[0].source == "custom"


def test_metadata_properties_and_wrappers_never_execute(owners):
    class Hostile:
        @property
        def display_name(self):
            pytest.fail("display property executed")

        @property
        def destructive_tool_names(self):
            pytest.fail("effect property executed")

        @property
        def enabled_by_default(self):
            pytest.fail("default provider probe executed")

        @property
        def config_schema(self):
            pytest.fail("config getter executed")

        def as_langchain_tools(self):
            pytest.fail("plugin wrapper executed")

    _plugin(owners)
    owners.plugins._plugin_tools["plugin_lookup"] = Hostile()
    owners.core._tools["hostile"] = Hostile()
    result = catalog.list_cached_tools()
    assert _row(result, "hostile").label == "hostile"
    assert _row(result, "hostile").destructive is None
    assert _row(result, "plugin_lookup").enabled is None


def test_read_performs_no_io_refresh_mutation_or_secret_access(owners, monkeypatch, tmp_path):
    _core(owners)
    _plugin(owners)
    _mcp(owners)
    before = (owners.core._enabled.copy(), owners.state._state.copy(),
              owners.runtime._catalog["local"]["echo"].__dict__.copy())

    def forbidden(*args, **kwargs):
        pytest.fail("passive catalog performed IO or runtime initialization")

    for owner, names in ((owners.core, ("_ensure_config_scope", "_read_config", "_apply_saved_config")),
                         (owners.state, ("_ensure_loaded", "_read_json", "get_plugin_secret")),
                         (owners.config, ("get_config", "load_config")),
                         (owners.runtime, ("_sync_catalog_from_config", "_get_effective_config", "get_catalog_snapshot")),
                         (owners.plugins, ("get_enabled_plugin_tool_records",))):
        for name in names:
            monkeypatch.setattr(owner, name, forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    first = catalog.list_cached_tools()
    second = catalog.list_cached_tools()
    assert first == second
    assert before == (owners.core._enabled, owners.state._state,
                      owners.runtime._catalog["local"]["echo"].__dict__)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change", ["global", "server", "tool", "removed"])
def test_current_mcp_disabled_toggles_override_stale_discovery(owners, change):
    info = _mcp(owners)
    first = catalog.list_cached_tools()
    if change == "global":
        owners.config._config_cache["enabled"] = False
    elif change == "server":
        owners.config._config_cache["servers"]["local"]["enabled"] = False
    elif change == "tool":
        owners.config._config_cache["servers"]["local"]["tools"]["enabled"]["echo"] = False
    else:
        owners.config._config_cache["servers"].clear()
    result = catalog.list_cached_tools()
    assert info.enabled is True
    assert _row(result, "mcp_local_echo").enabled is False
    assert first.revision != result.revision


def test_mcp_approval_toggle_comes_from_current_cache(owners):
    info = _mcp(owners)
    owners.config._config_cache["servers"]["local"]["tools"]["require_approval"] = ["echo"]
    assert _row(catalog.list_cached_tools(), "mcp_local_echo").requires_approval is True
    assert info.requires_approval is False


@pytest.mark.parametrize("owner", ["mcp", "plugin", "scope"])
def test_unloaded_or_different_scope_enablement_stays_unknown(owners, monkeypatch, tmp_path, owner):
    _core(owners)
    _plugin(owners)
    _mcp(owners)
    if owner == "mcp":
        owners.config._config_cache = None
        identity = "mcp_local_echo"
    elif owner == "plugin":
        owners.state._loaded = False
        identity = "plugin_lookup"
    else:
        monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "absent"))
        identity = "lookup"
    result = catalog.list_cached_tools()
    assert _row(result, identity).enabled is None
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("disabled", [True, False])
def test_plugin_mcp_never_claims_enabled_from_owner_alone(owners, disabled):
    _mcp(owners, plugin_id="lookup-plugin")
    owners.state._state["lookup-plugin"] = {"enabled": not disabled}
    assert _row(catalog.list_cached_tools(), "mcp_local_echo").enabled is (False if disabled else None)


def test_real_plugin_registration_revocation_changes_snapshot(owners, tmp_path):
    from row_bot.plugins.api import PluginAPI

    _plugin(owners)
    api = PluginAPI("lookup-plugin", tmp_path, owners.state)
    owners.plugins._plugin_tools["plugin_lookup"].plugin_api = api
    first = catalog.list_cached_tools()
    assert _row(first, "plugin_lookup").enabled is True
    api._revoke()
    second = catalog.list_cached_tools()
    assert _row(second, "plugin_lookup").enabled is False
    assert first.revision != second.revision
    with pytest.raises(RuntimeError, match="no longer active"):
        api._check_dispatch()


def test_disabled_plugin_and_removed_contribution_are_observed(owners):
    _plugin(owners)
    owners.state._state["lookup-plugin"]["enabled"] = False
    assert catalog.list_cached_tools().items[0].enabled is False
    owners.plugins.unregister_plugin("lookup-plugin")
    assert catalog.list_cached_tools().items == ()


def test_large_filtered_pages_are_complete_stable_and_bounded(owners):
    for index in range(1007):
        _core(owners, f"tool_{index:04}", label=f"{'Match' if index % 2 else 'Other'} {index:04}")
    result = catalog.list_cached_tools(source="core", query="match", limit=100)
    assert result.total == 503 and len(result.items) == 100
    assert result.truncated is False
    identities = [row.id for row in result.items]
    while result.next_cursor:
        result = catalog.list_cached_tools(source="core", query="MATCH", cursor=result.next_cursor, limit=100)
        identities.extend(row.id for row in result.items)
    assert len(identities) == len(set(identities)) == 503
    assert all(int(identity[5:]) % 2 == 1 for identity in identities)


def test_catalog_processing_ceiling_is_explicit(owners):
    for index in range(10002):
        _core(owners, f"tool_{index:05}")
    result = catalog.list_cached_tools(limit=100)
    assert result.truncated and result.total == 10000 and len(result.items) == 100
    assert len(owners.core.get_passive_tool_records()) == 10001


@pytest.mark.parametrize("change", ["query", "source", "revision"])
def test_cursor_binds_public_revision_and_filters(owners, change):
    _core(owners, "one")
    _core(owners, "two")
    first = catalog.list_cached_tools(limit=1)
    kwargs = {"cursor": first.next_cursor, "limit": 1}
    if change == "query":
        kwargs["query"] = "look"
    elif change == "source":
        kwargs["source"] = "core"
    else:
        owners.core._enabled["one"] = False
    with pytest.raises(catalog.ToolCatalogError) as error:
        catalog.list_cached_tools(**kwargs)
    assert error.value.code == "cursor_expired"


@pytest.mark.parametrize("kwargs", [{"source": "unknown"}, {"query": "x" * 257},
                                   {"limit": 0}, {"limit": 101}, {"limit": True},
                                   {"cursor": "!"}, {"cursor": "x" * 2049},
                                   {"cursor": base64.urlsafe_b64encode(b"[]").decode()}])
def test_invalid_queries_are_typed(owners, kwargs):
    with pytest.raises(catalog.ToolCatalogError) as error:
        catalog.list_cached_tools(**kwargs)
    assert error.value.code == "invalid_catalog_query"


def test_unavailable_error_and_arbitrary_metadata_are_redacted(owners, monkeypatch):
    _core(owners, "ok", label="Name\x00\n<script>" + "x" * 1000)
    _core(owners, "C:\\private\\DO_NOT_RETURN")

    def broken():
        raise RuntimeError("DO_NOT_RETURN secret/private/path")

    monkeypatch.setattr(owners.plugins, "get_passive_tool_records", broken)
    result = catalog.list_cached_tools()
    assert len(result.items) == 1
    assert len(result.items[0].label) <= 256 and "\x00" not in result.items[0].label
    assert "DO_NOT_RETURN" not in json.dumps(asdict(result))
    assert next(source for source in result.sources if source.source == "plugin").state == "unavailable"


def test_cursor_expires_when_changed_filter_is_empty(owners):
    _core(owners, "one")
    _core(owners, "two")
    cursor = catalog.list_cached_tools(limit=1).next_cursor
    with pytest.raises(catalog.ToolCatalogError, match="cursor_expired"):
        catalog.list_cached_tools(cursor=cursor, query="absent")


def test_recursive_cursor_payload_is_typed(owners):
    cursor = base64.urlsafe_b64encode(("[" * 1000 + "]" * 1000).encode()).decode()
    with pytest.raises(catalog.ToolCatalogError, match="invalid_catalog_query"):
        catalog.list_cached_tools(cursor=cursor)


def test_source_qualified_collisions_remain_distinguishable(owners):
    _core(owners, "same")
    _plugin(owners, "same")
    result = catalog.list_cached_tools()
    assert {(row.source, row.id) for row in result.items} == {("core", "same"), ("plugin", "same")}


def test_mcp_server_display_identity_preserves_spaces(owners):
    info = _mcp(owners)
    info.server_name = "Local Documents"
    assert _row(catalog.list_cached_tools(), info.prefixed_name).server_name == "Local Documents"


def test_mcp_parent_preserves_existing_source_classification(owners, monkeypatch):
    _core(owners, "mcp")
    monkeypatch.delitem(sys.modules, "row_bot.mcp_client.runtime")
    result = catalog.list_cached_tools()
    assert result.items[0].source == "mcp" and result.items[0].parent_id is None
    assert next(source for source in result.sources if source.source == "mcp").total == 1


@pytest.mark.parametrize("value", ["false", 0, [], None])
def test_malformed_explicit_mcp_toggle_is_unknown(owners, value):
    _mcp(owners)
    owners.config._config_cache["servers"]["local"]["tools"]["enabled"]["echo"] = value
    assert _row(catalog.list_cached_tools(), "mcp_local_echo").enabled is None
