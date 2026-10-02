"""A shared presentation of existing integration owners; no second runtime."""
from __future__ import annotations

from collections.abc import Callable

from dataclasses import asdict
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit

from row_bot.application.client_platform import ClientPlatformError

_LOCK = threading.RLock()
_RESULTS: dict[tuple[str, str], tuple[float, dict]] = {}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _url(value) -> str:
    try:
        p = urlsplit(str(value))
        return str(value)[:2048] if p.scheme == "https" and p.hostname and not p.username and not p.password else ""
    except ValueError:
        return ""


def _item(kind: str, owner_ref: str, name: str, **fields) -> dict:
    return {"id": kind + ":" + owner_ref, "kind": kind, "owner_ref": owner_ref, "name": str(name)[:256],
        "description": "", "parent_id": None, "source": "local", "publisher": "", "source_url": "", "version": "", "pin": "", "license": "",
        "compatibility": "supported", "reasons": [], "platforms": [], "evidence": "Live service behavior has not been tested.",
        "installed": True, "enabled": False, "status": "off", "revision": "", "actions": [], "auth_status": "none", "account_label": "",
        "children": [], "target": None, **fields}


def _pages(reader, validate, **kwargs):
    values, cursor = [], None
    for _ in range(20):
        result = reader(cursor=cursor, limit=50, validate=validate, **kwargs)
        if not isinstance(result, dict):
            result = asdict(result)
        values.extend(result["items"])
        cursor = result.get("next_cursor")
        if not cursor:
            return values, result.get("revision") or ""
    raise ClientPlatformError("integration_inventory_limit")


def _mcp_status(server: dict, cfg: dict, *, active: bool) -> tuple[str, list[str]]:
    """Derive the four installation states from saved policy and passive facts."""
    reasons = []
    missing = any(not requirement["available"] for requirement in server.get("requirements", []))
    if missing:
        reasons.append("Finish the required runtime setup.")
    tools = cfg.get("tools")
    catalog = tools.get("catalog") if isinstance(tools, dict) else None
    if not isinstance(catalog, dict):
        reasons.append("Test the connection and accept its tools.")
    runtime = server.get("runtime_status")
    if runtime in {"failed", "cleanup_incomplete"}:
        reasons.append("The connection failed or its cleanup needs attention.")
        return "attention", reasons
    if missing or not isinstance(catalog, dict):
        return "setup", reasons
    if not server.get("enabled") or not active:
        return "off", reasons
    if runtime == "connected":
        return "ready", reasons
    return "setup", ["Connect using the accepted tools."]


def _package_children(plugin: dict, identity: str, validate) -> list[dict]:
    from row_bot.application.capability_configuration_controls import read_mcp_configuration
    from row_bot.mcp_client import config, targets
    from row_bot.plugins.state import get_mcp_child_overrides

    children = []
    for child in plugin["children"]:
        row = _item(child["kind"], child["owner_ref"], child["name"], parent_id=identity,
            id=identity + ":" + child["kind"] + ":" + child["owner_ref"], source="package",
            enabled=plugin["enabled"], status="ready" if plugin["enabled"] else "off")
        if child["kind"] == "mcp":
            target = {"kind": "plugin", "plugin_id": plugin["plugin_id"], "server_key": child["server_key"]}
            row.update(target=target, actions=["configure"])
            try:
                with targets.scope(target):
                    page = asdict(read_mcp_configuration(validate=validate))
                    cfg = next(iter(config.read_saved_configuration().document["servers"].values()))
                server = next(item for item in page["items"] if item["server_id"] == child["owner_ref"])
                status, reasons = _mcp_status(server, cfg, active=plugin["enabled"])
                excluded = get_mcp_child_overrides(plugin["plugin_id"], child["server_key"]).get("enabled") is False
                row.update(status="off" if excluded else status, reasons=reasons,
                    enabled=plugin["enabled"] and server["enabled"] is True, revision=page["revision"] or "",
                    account_label=str(cfg.get("auth", {}).get("label", ""))[:128],
                    auth_status="configured" if cfg.get("auth", {}).get("credential_ref") else "none")
            except (OSError, ValueError, KeyError, TypeError, StopIteration):
                row.update(status="attention", reasons=["This included connection is unavailable. Review its setup."])
        children.append(row)
    return children


def _inventory(validate) -> tuple[list[dict], list[dict]]:
    from row_bot.application import skill_commands, plugin_commands, capability_configuration_controls
    from row_bot.application.client_skill_hub import read_installed_public_skills
    rows, errors = [], []
    try:
        skills, revision = _pages(skill_commands.read_skill_library, validate)
        provenance = {record["name"]: record for record in read_installed_public_skills()["items"]}
        from row_bot.skills_hub.provenance import load_records
        blocked = {name: str(record.metadata["source_blocked"])[:512] for name, record in load_records().items()
            if record.metadata.get("source_blocked")}
        for skill in skills:
            if skill["source"] == "plugin":
                continue
            record = provenance.get(skill["id"], {})
            rows.append(_item("skill", skill["id"], skill["display_name"], description=skill["description"][:2048],
                source=record.get("source") or skill["source"], version=skill["version"], revision=skill["revision"],
                enabled=skill["available"], status="attention" if skill["id"] in blocked else "ready" if skill["available"] else "off",
                reasons=[blocked[skill["id"]]] if skill["id"] in blocked else [],
                actions=["configure", "edit"] if skill["editable"] else ["configure"],
                evidence="Local instructions; use remains subject to tool approvals."))
    except (OSError, ValueError, KeyError, TypeError):
        errors.append({"source": "skills", "status": "error", "message": "The skill inventory is unavailable; other integrations remain available.", "fetched_at": None})
    try:
        for plugin in plugin_commands.read_integration_packages(validate=validate):
            if not plugin["installed"] and not plugin.get("retained"):
                continue
            identity = "plugin:" + plugin["plugin_id"]
            children = _package_children(plugin, identity, validate)
            reasons = [str(d.get("message", d.get("reason", "Unsupported component")))[:512] for d in plugin.get("diagnostics", [])[:16]]
            status = "retained" if plugin.get("retained") else "attention" if plugin.get("publication_pending") or plugin.get("health") in {"failed", "error", "load_failed"} else "setup" if not plugin.get("setup_complete") or plugin.get("health") != "passed" else "ready" if plugin["enabled"] else "off"
            if plugin.get("publication_pending"):
                reasons.append("Check the interrupted package operation before making another change.")
            if plugin.get("capabilities", {}).get("enable", {}).get("code") == "package_source_removed":
                status = "attention"
            for child in children:
                if child["status"] in {"attention", "setup"}:
                    reasons.append(child["name"] + ": " + " ".join(child["reasons"]))
                    if plugin["enabled"] and status != "attention":
                        status = child["status"]
            actions = [a for a, c in plugin["capabilities"].items() if c.get("available")]
            if plugin.get("recoverable"):
                actions.append("restore")
            if plugin.get("publication_pending"):
                actions.append("recover")
            rows.append(_item("plugin", plugin["plugin_id"], plugin["name"], description=plugin["description"][:2048],
                source="portable" if plugin["package_format"] != "row-bot-v2" else "native", publisher=plugin.get("publisher", "")[:160],
                license=plugin.get("license", "")[:256], source_url=_url(plugin.get("source_url", "")), version=plugin["version"][:128], pin=plugin.get("pin", ""),
                enabled=plugin["enabled"], installed=plugin["installed"], status=status, revision=plugin.get("manifest_revision") or "",
                compatibility="partial" if plugin.get("diagnostics") else "supported", reasons=reasons, children=children, actions=actions))
    except (OSError, ValueError, KeyError, TypeError):
        errors.append({"source": "plugins", "status": "error", "message": "The package inventory is unavailable; other integrations remain available.", "fetched_at": None})
    try:
        from row_bot.mcp_client.config import read_saved_configuration
        servers, revision = _pages(capability_configuration_controls.read_mcp_configuration, validate)
        saved = read_saved_configuration().document
        for server in servers:
            if not isinstance(server, dict):
                server = asdict(server)
            cfg = saved.get("servers", {}).get(server["name"], {})
            credentials = cfg.get("auth", {})
            status, reasons = _mcp_status(server, cfg, active=saved.get("enabled") is True)
            rows.append(_item("mcp", server["server_id"], server["name"], source=str(cfg.get("source", {}).get("marketplace") or "custom"),
                description="Tools from an external MCP server.", enabled=server["enabled"] is True, revision=revision,
                status=status, reasons=reasons,
                account_label=str(credentials.get("label", ""))[:128], auth_status="configured" if credentials.get("credential_ref") else "none",
                target={"kind": "standalone"}, actions=["configure", "test", "connect", "remove"], source_url=_url(cfg.get("source", {}).get("url", ""))))
    except (OSError, ValueError, KeyError, TypeError):
        errors.append({"source": "mcp", "status": "error", "message": "The MCP inventory is unavailable; other integrations remain available.", "fetched_at": None})
    validate()
    return rows, errors


def read_integrations(*, query: str = "", kind: str = "all", source: str = "all", cursor: str | None = None,
        limit: int = 50, validate: Callable[[], None] = lambda: None) -> dict:
    validate()
    if kind not in {"all", "skill", "mcp", "plugin"} or len(query) > 256 or len(source) > 80 or not 1 <= limit <= 50:
        raise ClientPlatformError("invalid_integration_query")
    rows, errors = _inventory(validate)
    rows = [row for row in rows if (kind == "all" or row["kind"] == kind or any(child["kind"] == kind for child in row["children"])) and (source == "all" or row["source"] == source)
        and query.casefold().strip() in (row["name"] + " " + row["description"]).casefold()]
    rows.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    revision = _digest([rows, errors, query, kind, source])
    offset = 0
    if cursor:
        try:
            retained, raw = cursor.split(":")
            offset = int(raw)
            if retained != revision or not 0 <= offset < len(rows):
                raise ValueError
        except ValueError:
            raise ClientPlatformError("cursor_expired") from None
    return {"schema_version": 1, "revision": revision, "items": rows[offset:offset + limit], "total": len(rows),
        "next_cursor": f"{revision}:{offset + limit}" if offset + limit < len(rows) else None, "sources": errors}


def read_integration(integration_id: str, *, validate: Callable[[], None] = lambda: None) -> dict:
    rows, _ = _inventory(validate)
    for row in rows:
        if row["id"] == integration_id:
            return row
    raise ClientPlatformError("not_found")


def search_integrations(*, owner_id: str, query: str = "", sources: list[str] | None = None, refresh: bool = False, validate: Callable[[], None] = lambda: None) -> dict:
    from row_bot.plugins import hermes_catalog
    from row_bot.mcp_client import marketplace
    from row_bot.application import client_skill_hub, plugin_commands
    validate()
    selected = sources or ["recommended"]
    if len(query) > 256 or len(selected) > 7 or set(selected) - {"recommended", "hermes", "hermes_mcp", "clawhub", "skills_sh", "official", "native"}:
        raise ClientPlatformError("invalid_integration_query")
    items, statuses, references = [], [], {}
    for source in selected:
        validate()
        try:
            if source == "hermes":
                catalog = hermes_catalog.read_catalog(refresh=refresh)
                for entry in catalog["entries"]:
                    if query.casefold() not in (entry["name"] + " " + entry["description"]).casefold():
                        continue
                    item = _item("plugin", entry["id"], entry["name"], installed=False, status="discover", source="hermes",
                        description=entry["description"], publisher=entry["publisher"], source_url=_url(entry["url"]), version=entry["version"],
                        pin=entry["pin"], compatibility=entry["compatibility"], reasons=[entry["reason"]], platforms=entry["platforms"], actions=["preview"])
                    items.append(item)
                    references[item["id"]] = {"kind": "plugin", "reference": entry["id"]}
                statuses.append({"source": source, "status": catalog["status"], "message": catalog["message"], "fetched_at": catalog["fetched_at"] or None})
            elif source == "hermes_mcp":
                from row_bot.plugins import hermes_mcp
                catalog = hermes_mcp.read_catalog(refresh=refresh)
                for name in catalog.get("names", []):
                    if query.casefold() not in name.casefold():
                        continue
                    item = _item("mcp", "hermes_mcp:" + name, name, installed=False, status="discover", source=source,
                        pin=catalog["pin"], compatibility="not_inspected", actions=["preview"],
                        description="Pinned Hermes optional-MCP recipe. Inspect to check compatibility.")
                    items.append(item)
                    references[item["id"]] = {"kind": "hermes_mcp", "name": name, "pin": catalog["pin"]}
                statuses.append({"source": source, "status": catalog["status"], "message": catalog["message"], "fetched_at": catalog.get("fetched_at")})
            elif source in {"recommended", "official"}:
                result = marketplace.search_marketplace_with_status(query, sources=["official"], limit=50, cached_only=not refresh or source == "recommended")
                if source == "recommended":
                    for name, reference, description, evidence in (
                        ("Local text tools", "bundled:local-text-tools", "Two no-auth writing skills, a supporting checklist, and local text statistics over MCP.",
                         "MIT; shipped version 1.0.0 and reviewed tree digest. Managed Node is optional for the MCP child. Windows/macOS/Linux recipe; only Windows checked locally. No network, file reads or telemetry in the server. Skill prompts use the conversation's chosen model."),
                        ("Hello Tool (native example)", "https://github.com/siddsachar/row-bot/tree/9435afbc930799ec30a622a2eb3d234a05214f31/examples/plugins/hello-tool", "Existing native Row-Bot plugin demonstrating a minimal local tool.",
                         "MIT; pinned repository example 0.1.0. Requires Row-Bot's private Python worker environment; no third-party dependencies or telemetry. Windows/macOS/Linux fixture coverage; clean-machine checks pending."),
                    ):
                        if query.casefold() not in (name + description).casefold():
                            continue
                        item = _item("plugin", reference, name, installed=False, status="discover", source="recommended", publisher="Row-Bot", license="MIT",
                            description=description, reasons=[evidence], evidence="Source reviewed; see setup details and validation limits.", actions=["preview"])
                        items.append(item)
                        references[item["id"]] = {"kind": "plugin", "reference": reference}
                for entry in result.entries:
                    if source == "recommended" and not (entry.metadata or {}).get("integration_starter"):
                        continue
                    supported = bool(entry.install and (entry.install.get("url") or entry.install.get("command")))
                    item = _item("mcp", entry.source + ":" + entry.id, entry.name, installed=False, status="discover", source=entry.source,
                        description=entry.description[:2048], source_url=_url(entry.url), publisher=entry.publisher[:160], compatibility="supported" if supported else "unsupported",
                        reasons=entry.notes[:16] if supported else ["No supported launch recipe is available; use advanced configuration."],
                        evidence=(entry.metadata or {}).get("evidence", "Publisher listing only; live service untested."),
                        license=(entry.metadata or {}).get("license", ""), pin=(entry.metadata or {}).get("version_policy", ""), actions=["preview"] if supported else [])
                    items.append(item)
                    references[item["id"]] = {"kind": "mcp", "entry": entry}
                statuses.append({"source": source, "status": "cached" if result.mode in {"cache", "curated"} else "live", "message": "Local recommendations and saved source results." if result.mode != "live" else "Public search results.", "fetched_at": None})
            elif source == "native":
                for row in plugin_commands.read_plugin_catalog(query=query, source="marketplace", validate=validate)["items"]:
                    item = _item("plugin", row["plugin_id"], row["name"], installed=False, status="discover", source="native", description=row["description"],
                        version=row["version"], actions=["install"] if row["capabilities"]["install"]["available"] else [])
                    items.append(item)
                    references[item["id"]] = {"kind": "native", "plugin_id": row["plugin_id"]}
                statuses.append({"source": source, "status": "cached", "message": "Saved native marketplace. Refresh through its reviewed lifecycle action.", "fetched_at": None})
            elif refresh:
                result = client_skill_hub.search_public_skills(owner_id=owner_id, query=query, source=source, refresh=True, limit=48)
                for entry in result["entries"]:
                    item = _item("skill", entry["id"], entry["name"], installed=entry["installed"], status="discover", source=entry["source"], description=entry["description"],
                        publisher=entry["author"], compatibility="not_inspected", actions=["preview"])
                    items.append(item)
                    references[item["id"]] = {"kind": "skill", "revision": result["revision"], "entry_id": entry["id"]}
                statuses.extend({"source": source, "status": str(s["status"]), "message": str(s.get("message", "")), "fetched_at": None} for s in result.get("source_statuses", []))
            else:
                statuses.append({"source": source, "status": "empty", "message": "Choose Search public sources to query this catalog.", "fetched_at": None})
        except (OSError, ValueError, KeyError, TypeError):
            statuses.append({"source": source, "status": "error", "message": "This source is unavailable. Other results remain available.", "fetched_at": None})
    validate()
    items = list({item["id"]: item for item in items}.values())[:96]
    revision = _digest(items)
    with _LOCK:
        if len(_RESULTS) >= 64:
            _RESULTS.pop(next(iter(_RESULTS)))
        _RESULTS[(owner_id, revision)] = (time.monotonic(), references)
    return {"schema_version": 1, "revision": revision, "items": items, "total": len(items), "next_cursor": None, "sources": statuses}


def preview_integration(*, owner_id: str, revision: str = "", item_id: str = "", kind: str = "plugin", reference: str = "", local: bool = False, validate: Callable[[], None] = lambda: None) -> dict:
    from row_bot.plugins.hermes_catalog import inspect_package
    from row_bot.application.client_skill_hub import preview_public_skill
    from row_bot.mcp_client.marketplace import entry_to_server_config
    validate()
    if reference:
        if kind != "plugin":
            raise ClientPlatformError("integration_import_type_required")
        return {"kind": "plugin", "plugin": inspect_package(owner_id=owner_id, reference=reference, local=local)}
    with _LOCK:
        saved = _RESULTS.get((owner_id, revision))
    if saved is None or time.monotonic() - saved[0] > 1200 or item_id not in saved[1]:
        raise ClientPlatformError("integration_preview_expired")
    value = saved[1][item_id]
    if value["kind"] == "plugin":
        result = {"kind": "plugin", "plugin": inspect_package(owner_id=owner_id, reference=value["reference"])}
    elif value["kind"] == "skill":
        result = {"kind": "skill", "skill": preview_public_skill(owner_id=owner_id, revision=value["revision"], entry_id=value["entry_id"])}
    elif value["kind"] == "hermes_mcp":
        from row_bot.plugins.hermes_mcp import inspect_recipe
        result = {"kind": "mcp", "mcp": inspect_recipe(value["name"], value["pin"])}
    elif value["kind"] == "mcp":
        entry = value["entry"]
        import re
        name = re.sub(r"[^A-Za-z0-9_. -]", "-", entry.name).strip()[:64] or "Connection"
        result = {"kind": "mcp", "mcp": {"name": name, "import_json": json.dumps({"mcpServers": {name: entry_to_server_config(entry)}}),
            "requires_auth": entry.requires_auth, "notes": entry.notes[:16], "source_url": _url(entry.url)}}
    else:
        result = {"kind": "native", "plugin_id": value["plugin_id"]}
    validate()
    return result
