"""Unified Agent Profile tool catalog.

The normal chat runtime has several tool sources: built-in Row-Bot tools,
dynamic MCP tools, plugin tools, and promoted Custom Tools that register as
synthetic plugins.  Agent Profiles need one stable picker/catalog across those
sources without changing normal chat binding.
"""

from __future__ import annotations

from collections import Counter
import base64
from dataclasses import asdict, dataclass
import hashlib
import inspect
import json
import logging
import re
import sys
from typing import Any, Iterable, Literal, Mapping, Sequence

logger = logging.getLogger(__name__)

GROUP_ORDER = {
    "Core": 0,
    "MCP": 1,
    "Plugins": 2,
    "Custom Tools": 3,
    "Unavailable": 9,
}

ToolSource = Literal["core", "mcp", "plugin", "custom"]
_PASSIVE_SOURCES = ("core", "mcp", "plugin", "custom")
_PASSIVE_LIMIT = 10000


@dataclass(frozen=True)
class ToolCatalogRow:
    id: str
    label: str
    source: ToolSource
    parent_id: str | None
    plugin_id: str | None
    server_name: str | None
    enabled: bool | None
    configured: bool | None
    destructive: bool | None
    requires_approval: bool | None
    runtime_state: Literal["unknown"] = "unknown"


@dataclass(frozen=True)
class ToolCatalogSource:
    source: ToolSource
    state: Literal["cached", "unavailable"]
    total: int | None


@dataclass(frozen=True)
class ToolCatalogPage:
    schema_version: int
    revision: str
    generated_at: float | None
    freshness: Literal["cached", "unavailable"]
    sources: tuple[ToolCatalogSource, ...]
    items: tuple[ToolCatalogRow, ...]
    total: int
    next_cursor: str | None
    truncated: bool


class ToolCatalogError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _passive_tool_fields(name: str, tool: object) -> dict[str, Any]:
    """Inspect plain registration metadata without executing tool descriptors."""
    label = inspect.getattr_static(tool, "display_name", None)
    destructive = inspect.getattr_static(tool, "destructive_tool_names", None)
    return {"id": name, "label": label if type(label) is str else name,
            "destructive": bool(destructive) if type(destructive) in (set, frozenset) else None}


def _passive_identity(value: object) -> str | None:
    # Tool identifiers are machine names, not paths, descriptions or arbitrary HTML.
    return value if type(value) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) else None


def _passive_bool(value: object) -> bool | None:
    return value if type(value) is bool else None


def _passive_rows() -> tuple[list[ToolCatalogRow], tuple[ToolCatalogSource, ...], bool]:
    rows: list[ToolCatalogRow] = []
    available: set[str] = set()
    seen: set[tuple[str, str]] = set()
    truncated = False
    for source, module_name in (("core", "row_bot.tools.registry"),
                                ("mcp", "row_bot.mcp_client.runtime"),
                                ("plugin", "row_bot.plugins.registry")):
        owner = sys.modules.get(module_name)
        if owner is None:
            continue
        try:
            records = owner.get_passive_tool_records()
        except Exception:
            # Exception strings can include secrets or private paths.
            continue
        available.add(source)
        if source == "plugin":
            available.add("custom")
        truncated |= len(records) > _PASSIVE_LIMIT
        for item in records[:_PASSIVE_LIMIT]:
            identity = _passive_identity(item.get("id"))
            if identity is None:
                continue
            plugin_id = _passive_identity(item.get("plugin_id"))
            kind = "custom" if source == "plugin" and (item.get("custom") is True or (plugin_id or "").startswith("custom-tool-")) else source
            if source == "core" and identity == "mcp":
                kind = "mcp"
                available.add(kind)
            if (kind, identity) in seen:
                continue
            seen.add((kind, identity))
            label = item.get("label")
            label = "".join(c for c in label[:256] if c.isprintable()) if type(label) is str else identity
            server_name = item.get("server_name")
            server_name = server_name if (type(server_name) is str and 0 < len(server_name) <= 256
                                          and all(c.isprintable() for c in server_name)) else None
            rows.append(ToolCatalogRow(
                identity, label or identity, kind,
                "mcp" if source == "mcp" else identity if source == "plugin" else None,
                plugin_id, server_name,
                _passive_bool(item.get("enabled")), _passive_bool(item.get("configured")),
                _passive_bool(item.get("destructive")), _passive_bool(item.get("requires_approval")),
            ))
    rows.sort(key=lambda row: (_PASSIVE_SOURCES.index(row.source), row.label.casefold(), row.id))
    sources = tuple(ToolCatalogSource(source, "cached" if source in available else "unavailable",
                                     sum(row.source == source for row in rows) if source in available else None)
                    for source in _PASSIVE_SOURCES)
    return rows, sources, truncated


def list_cached_tools(*, source: ToolSource | None = None, query: str = "",
                      cursor: str | None = None, limit: int = 50) -> ToolCatalogPage:
    """Bounded passive registration/discovery view; never authorizes execution.

    Missing owners are not imported. Labels backed by executable properties use
    registered IDs; plugin child aliases are not enumerated. ``enabled`` reflects
    only currently provable owner toggles, ``configured`` only a known configuration
    record (not credentials/readiness); unknown values stay null. No cache timestamp
    exists, so generated_at is null and freshness never claims fresh verification.
    Source totals describe retained parent/discovery rows, not runtime aliases.
    """
    if (source is not None and source not in _PASSIVE_SOURCES
            or type(query) is not str or len(query) > 256
            or type(limit) is not int or not 1 <= limit <= 100
            or cursor is not None and (type(cursor) is not str or len(cursor) > 2048)):
        raise ToolCatalogError("invalid_catalog_query")
    query = query.strip().casefold()
    rows, sources, truncated = _passive_rows()
    revision = hashlib.sha256(json.dumps(
        {"rows": [asdict(row) for row in rows], "sources": [asdict(item) for item in sources],
         "truncated": truncated}, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
    selected = [row for row in rows if (source is None or row.source == source)
                and (not query or query in " ".join((row.id, row.label, row.plugin_id or "", row.server_name or "")).casefold())]
    offset = 0
    if cursor is not None:
        try:
            data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        except (ValueError, UnicodeError, RecursionError):
            raise ToolCatalogError("invalid_catalog_query") from None
        if (type(data) is not dict or set(data) != {"revision", "source", "query", "offset"}
                or type(data["offset"]) is not int or data["offset"] <= 0):
            raise ToolCatalogError("invalid_catalog_query")
        if data["revision"] != revision or data["source"] != source or data["query"] != query:
            raise ToolCatalogError("cursor_expired")
        if data["offset"] >= len(selected):
            raise ToolCatalogError("invalid_catalog_query")
        offset = data["offset"]
    page = tuple(selected[offset:offset + limit])
    next_cursor = None
    if offset + limit < len(selected):
        next_cursor = base64.urlsafe_b64encode(json.dumps(
            {"revision": revision, "source": source, "query": query, "offset": offset + limit},
            separators=(",", ":"),
        ).encode()).decode()
    return ToolCatalogPage(1, revision, None, "cached" if rows or any(s.state == "cached" for s in sources)
                           else "unavailable", sources, page, len(selected), next_cursor, truncated)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _record(
    *,
    id: str,
    runtime_name: str,
    label: str,
    description: str = "",
    source: str,
    group: str,
    enabled: bool = True,
    destructive: bool = False,
    selectable: bool = True,
    parent_id: str = "",
    plugin_id: str = "",
    server_name: str = "",
) -> dict[str, Any]:
    record_id = _text(id)
    return {
        "id": record_id,
        "runtime_name": _text(runtime_name) or record_id,
        "label": _text(label) or record_id,
        "description": _text(description),
        "source": _text(source),
        "group": _text(group),
        "enabled": bool(enabled),
        "destructive": bool(destructive),
        "selectable": bool(selectable),
        "parent_id": _text(parent_id),
        "plugin_id": _text(plugin_id),
        "server_name": _text(server_name),
    }


def _unavailable(
    *,
    id: str,
    label: str,
    description: str,
    group: str,
    source: str,
) -> dict[str, Any]:
    return _record(
        id=id,
        runtime_name=id,
        label=label,
        description=description,
        source=source,
        group=group,
        enabled=False,
        selectable=False,
    )


def _core_records() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    from row_bot.tools import registry as tool_registry

    records: list[dict[str, Any]] = []
    mcp_parent: dict[str, Any] | None = None
    for tool in sorted(tool_registry.get_enabled_tools(), key=lambda item: item.name):
        name = _text(getattr(tool, "name", ""))
        if not name:
            continue
        label = _text(getattr(tool, "display_name", "")) or name
        description = _text(getattr(tool, "description", ""))
        destructive = bool(getattr(tool, "destructive_tool_names", set()) or set())
        record = _record(
            id=name,
            runtime_name=name,
            label=label,
            description=description,
            source="core",
            group="Core",
            destructive=destructive,
        )
        if name == "mcp":
            mcp_parent = record
        else:
            records.append(record)
    return records, mcp_parent


def _mcp_records(
    parent_record: Mapping[str, Any],
    *,
    include_unavailable: bool,
) -> list[dict[str, Any]]:
    records = [
        _record(
            id="mcp",
            runtime_name="mcp",
            label="All enabled MCP tools",
            description=(
                _text(parent_record.get("description"))
                or "Use all enabled tools exposed by configured MCP servers."
            ),
            source="mcp",
            group="MCP",
            destructive=bool(parent_record.get("destructive")),
        )
    ]
    try:
        from row_bot.mcp_client.runtime import get_catalog_snapshot

        snapshot = get_catalog_snapshot()
    except Exception as exc:
        logger.debug("Could not load MCP catalog for Agent Profile tools", exc_info=True)
        if include_unavailable:
            records.append(
                _unavailable(
                    id="__mcp_catalog_unavailable__",
                    label="MCP catalog unavailable",
                    description=f"Individual MCP tools could not be listed: {exc}",
                    group="MCP",
                    source="mcp",
                )
            )
        return records

    enabled_count = 0
    for server_name in sorted(snapshot):
        tools = snapshot.get(server_name) or []
        for info in sorted(tools, key=lambda item: _text(item.get("prefixed_name") or item.get("name"))):
            if not bool(info.get("enabled")):
                continue
            runtime_name = _text(info.get("prefixed_name")) or f"mcp_{server_name}_{info.get('name')}"
            enabled_count += 1
            label = f"{server_name}: {_text(info.get('name')) or runtime_name}"
            records.append(
                _record(
                    id=runtime_name,
                    runtime_name=runtime_name,
                    label=label,
                    description=_text(info.get("description")),
                    source="mcp",
                    group="MCP",
                    destructive=bool(info.get("requires_approval") or info.get("destructive")),
                    parent_id="mcp",
                    server_name=server_name,
                )
            )
    if enabled_count == 0 and include_unavailable:
        records.append(
            _unavailable(
                id="__mcp_catalog_empty__",
                label="No individual MCP tools discovered",
                description=(
                    "The MCP parent tool is enabled, but no enabled server tools are currently "
                    "available in the runtime catalog."
                ),
                group="MCP",
                source="mcp",
            )
        )
    return records


def _is_custom_plugin(plugin_id: str, tags: Sequence[str]) -> bool:
    normalized_tags = {str(tag).strip().lower() for tag in tags}
    return "custom-tool" in normalized_tags or str(plugin_id or "").startswith("custom-tool-")


def _plugin_records(*, include_unavailable: bool) -> list[dict[str, Any]]:
    try:
        from row_bot.plugins import registry as plugin_registry

        plugin_records = plugin_registry.get_enabled_plugin_tool_records()
    except Exception as exc:
        logger.debug("Could not load plugin tool catalog for Agent Profiles", exc_info=True)
        if not include_unavailable:
            return []
        return [
            _unavailable(
                id="__plugin_catalog_unavailable__",
                label="Plugin catalog unavailable",
                description=f"Plugin tools could not be listed: {exc}",
                group="Plugins",
                source="plugin",
            )
        ]

    records: list[dict[str, Any]] = []
    for item in plugin_records:
        runtime_name = _text(item.get("runtime_name"))
        if not runtime_name:
            continue
        plugin_id = _text(item.get("plugin_id"))
        tags = [str(tag) for tag in item.get("tags") or []]
        source = "custom" if _is_custom_plugin(plugin_id, tags) else "plugin"
        group = "Custom Tools" if source == "custom" else "Plugins"
        plugin_label = _text(item.get("plugin_name")) or plugin_id
        label = _text(item.get("label")) or runtime_name
        if plugin_label and plugin_label not in label:
            label = f"{plugin_label}: {label}"
        records.append(
            _record(
                id=runtime_name,
                runtime_name=runtime_name,
                label=label,
                description=_text(item.get("description")),
                source=source,
                group=group,
                destructive=bool(item.get("destructive")),
                parent_id=_text(item.get("parent_name")),
                plugin_id=plugin_id,
            )
        )
    return records


def _sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        records,
        key=lambda item: (
            GROUP_ORDER.get(str(item.get("group") or ""), 8),
            str(item.get("label") or item.get("id") or "").lower(),
            str(item.get("id") or ""),
        ),
    )


def list_agent_tool_catalog(*, include_unavailable: bool = True) -> list[dict[str, Any]]:
    """Return normalized tool records for Agent Profile UI/runtime policy."""

    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_many(items: Iterable[Mapping[str, Any]]) -> None:
        for item in items:
            record = dict(item)
            record_id = _text(record.get("id"))
            if not record_id or record_id in seen:
                continue
            seen.add(record_id)
            records.append(record)

    try:
        core_records, mcp_parent = _core_records()
        add_many(core_records)
        if mcp_parent is not None:
            add_many(_mcp_records(mcp_parent, include_unavailable=include_unavailable))
    except Exception as exc:
        logger.debug("Could not load core tools for Agent Profile catalog", exc_info=True)
        if include_unavailable:
            add_many([
                _unavailable(
                    id="__core_catalog_unavailable__",
                    label="Core tool catalog unavailable",
                    description=f"Core tools could not be listed: {exc}",
                    group="Core",
                    source="core",
                )
            ])

    add_many(_plugin_records(include_unavailable=include_unavailable))
    return _sort_records(records)


def selectable_tool_ids(catalog: Sequence[Mapping[str, Any]] | None = None) -> list[str]:
    records = list(catalog) if catalog is not None else list_agent_tool_catalog(include_unavailable=False)
    return [
        _text(record.get("id"))
        for record in records
        if _text(record.get("id")) and bool(record.get("selectable", True))
    ]


def _guess_source(tool_id: str) -> str:
    if tool_id == "mcp" or tool_id.startswith("mcp_"):
        return "mcp"
    if tool_id.startswith("custom_tool_") or tool_id.startswith("custom-tool-"):
        return "custom"
    try:
        from row_bot.tools import registry as tool_registry

        if tool_registry.get_tool(tool_id) is not None:
            return "core"
    except Exception:
        pass
    return "plugin"


def count_tool_ids_by_source(
    tool_ids: Iterable[str],
    *,
    catalog: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, int]:
    """Count selected tool ids by source, using catalog metadata when present."""

    records = list(catalog) if catalog is not None else list_agent_tool_catalog(include_unavailable=False)
    lookup = {
        _text(record.get("id")): _text(record.get("source")) or "core"
        for record in records
        if _text(record.get("id"))
    }
    counts: Counter[str] = Counter()
    for item in tool_ids:
        tool_id = _text(item)
        if not tool_id:
            continue
        counts[lookup.get(tool_id) or _guess_source(tool_id)] += 1
    return dict(sorted(counts.items()))


def format_tool_source_counts(counts: Mapping[str, int]) -> str:
    if not counts:
        return ""
    labels = {
        "core": "core",
        "mcp": "MCP",
        "plugin": "plugin",
        "custom": "custom",
    }
    return ", ".join(
        f"{labels.get(source, source)}={count}"
        for source, count in sorted(counts.items())
        if count
    )
