from __future__ import annotations


def _record(tool_id: str, *, source: str = "core", group: str = "Core") -> dict:
    return {
        "id": tool_id,
        "runtime_name": tool_id,
        "label": tool_id,
        "description": "",
        "source": source,
        "group": group,
        "enabled": True,
        "destructive": False,
        "selectable": True,
        "parent_id": "",
        "plugin_id": "",
        "server_name": "",
    }


def test_agent_tool_catalog_includes_core_tools(monkeypatch):
    from row_bot import agent_tool_catalog as catalog

    monkeypatch.setattr(
        catalog,
        "_core_records",
        lambda: ([_record("filesystem"), _record("row_bot_status")], None),
    )
    monkeypatch.setattr(catalog, "_plugin_records", lambda **_kwargs: [])

    records = catalog.list_agent_tool_catalog()
    ids = {item["id"] for item in records}

    assert {"filesystem", "row_bot_status"} <= ids
    assert all(item["source"] == "core" for item in records)


def test_agent_tool_catalog_includes_individual_mcp_tools(monkeypatch):
    from row_bot import agent_tool_catalog as catalog
    from row_bot.mcp_client import runtime as mcp_runtime

    monkeypatch.setattr(
        catalog,
        "_core_records",
        lambda: ([], [_record("mcp", source="core", group="Core")][0]),
    )
    monkeypatch.setattr(catalog, "_plugin_records", lambda **_kwargs: [])
    monkeypatch.setattr(
        mcp_runtime,
        "get_catalog_snapshot",
        lambda: {
            "local": [
                {
                    "server_name": "local",
                    "name": "echo",
                    "prefixed_name": "mcp_local_echo",
                    "description": "Echo text",
                    "enabled": True,
                    "requires_approval": False,
                },
                {
                    "server_name": "local",
                    "name": "write",
                    "prefixed_name": "mcp_local_write",
                    "description": "Write text",
                    "enabled": False,
                    "requires_approval": True,
                },
            ]
        },
    )

    records = catalog.list_agent_tool_catalog()
    by_id = {item["id"]: item for item in records}

    assert by_id["mcp"]["source"] == "mcp"
    assert by_id["mcp_local_echo"]["source"] == "mcp"
    assert by_id["mcp_local_echo"]["group"] == "MCP"
    assert "mcp_local_write" not in by_id


def test_agent_tool_catalog_mcp_fallback_when_runtime_unavailable(monkeypatch):
    from row_bot import agent_tool_catalog as catalog
    from row_bot.mcp_client import runtime as mcp_runtime

    monkeypatch.setattr(
        catalog,
        "_core_records",
        lambda: ([], [_record("mcp", source="core", group="Core")][0]),
    )
    monkeypatch.setattr(catalog, "_plugin_records", lambda **_kwargs: [])
    monkeypatch.setattr(
        mcp_runtime,
        "get_catalog_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("not ready")),
    )

    records = catalog.list_agent_tool_catalog()
    by_id = {item["id"]: item for item in records}

    assert by_id["mcp"]["selectable"] is True
    assert by_id["__mcp_catalog_unavailable__"]["selectable"] is False
    assert "not ready" in by_id["__mcp_catalog_unavailable__"]["description"]


def test_agent_tool_catalog_includes_plugin_and_custom_tools(monkeypatch):
    from row_bot import agent_tool_catalog as catalog
    from row_bot.plugins import registry as plugin_registry

    monkeypatch.setattr(catalog, "_core_records", lambda: ([], None))
    monkeypatch.setattr(
        plugin_registry,
        "get_enabled_plugin_tool_records",
        lambda: [
            {
                "runtime_name": "plugin_lookup",
                "parent_name": "plugin_lookup",
                "plugin_id": "lookup-plugin",
                "plugin_name": "Lookup Plugin",
                "tags": [],
                "label": "Lookup",
                "description": "Look things up",
                "destructive": False,
            },
            {
                "runtime_name": "custom_tool_helper_smoke",
                "parent_name": "custom-tool-helper",
                "plugin_id": "custom-tool-helper",
                "plugin_name": "Helper Custom Tool",
                "tags": ["custom-tool"],
                "label": "Smoke",
                "description": "Run a smoke command",
                "destructive": True,
            },
        ],
    )

    records = catalog.list_agent_tool_catalog()
    by_id = {item["id"]: item for item in records}

    assert by_id["plugin_lookup"]["source"] == "plugin"
    assert by_id["plugin_lookup"]["group"] == "Plugins"
    assert by_id["custom_tool_helper_smoke"]["source"] == "custom"
    assert by_id["custom_tool_helper_smoke"]["group"] == "Custom Tools"
    assert by_id["custom_tool_helper_smoke"]["destructive"] is True


def test_tool_source_counts_use_catalog_and_fallbacks(monkeypatch):
    from row_bot import agent_tool_catalog as catalog

    records = [
        _record("filesystem", source="core", group="Core"),
        _record("plugin_lookup", source="plugin", group="Plugins"),
        _record("custom_tool_helper_smoke", source="custom", group="Custom Tools"),
    ]

    counts = catalog.count_tool_ids_by_source(
        ["filesystem", "mcp_local_echo", "plugin_lookup", "custom_tool_helper_smoke"],
        catalog=records,
    )

    assert counts == {"core": 1, "custom": 1, "mcp": 1, "plugin": 1}


def test_profile_tool_items_use_catalog_and_preserve_missing_selection(monkeypatch):
    from row_bot import agent_tool_catalog as catalog
    from row_bot.ui import profile_library

    monkeypatch.setattr(
        catalog,
        "list_agent_tool_catalog",
        lambda include_unavailable=True: [
            {
                "id": "plugin_lookup",
                "runtime_name": "plugin_lookup",
                "label": "Lookup",
                "description": "Look things up",
                "source": "plugin",
                "group": "Plugins",
                "enabled": True,
                "destructive": False,
                "selectable": True,
            },
            {
                "id": "__plugin_catalog_unavailable__",
                "runtime_name": "__plugin_catalog_unavailable__",
                "label": "Plugin catalog unavailable",
                "description": "not ready",
                "source": "plugin",
                "group": "Plugins",
                "enabled": False,
                "destructive": False,
                "selectable": False,
            },
        ],
    )

    items = profile_library._tool_items(["missing_external_tool"])
    by_name = {item["name"]: item for item in items}

    assert by_name["plugin_lookup"]["group"] == "Plugins"
    assert by_name["__plugin_catalog_unavailable__"]["selectable"] is False
    assert by_name["missing_external_tool"]["group"] == "Unavailable"
    assert by_name["missing_external_tool"]["selectable"] is True
