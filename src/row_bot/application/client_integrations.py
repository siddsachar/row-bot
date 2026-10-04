"""A shared presentation of existing integration owners; no second runtime."""
from __future__ import annotations

from collections.abc import Callable

from dataclasses import asdict
import copy
import concurrent.futures
import re
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit

from row_bot.application.client_platform import ClientPlatformError

_LOCK = threading.RLock()
_RESULTS: dict[tuple[str, str], tuple[float, dict]] = {}
_SEARCHES: dict[tuple[str, str], tuple[float, dict, dict]] = {}
_SEARCH_SLOTS = threading.BoundedSemaphore(4)
SOURCE_DEADLINE = 8.0
from row_bot.application.integration_sources import SOURCES, source_status
from row_bot.application.integration_setup import describe_mcp_setup


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
        "children": [], "target": None, "attributions": [], "evidence_stage": "listed",
        "auth_requirement": "unknown", "canonical_identity": "", **fields}


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
    setup = describe_mcp_setup(server, cfg)
    if setup["auth_mode"] == "unsupported":
        return "setup", ["This authentication method is not supported. Review the publisher setup requirements."]
    if setup["auth_mode"] in {"oauth", "api_key"} and not setup["credential_configured"]:
        return "disconnected", ["Connect the account for this connection before testing its tools."]
    missing = any(not requirement["available"] for requirement in server.get("requirements", []))
    missing = missing or setup["package_required"]
    if missing:
        reasons.append("Finish the required runtime setup.")
    tools = cfg.get("tools")
    catalog = tools.get("catalog") if isinstance(tools, dict) else None
    if not isinstance(catalog, dict):
        reasons.append("Test the connection and accept its tools.")
    runtime = server.get("runtime_status")
    if runtime in {"failed", "error", "sdk_missing", "dependency_missing", "cleanup_incomplete"}:
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
            enabled=plugin["enabled"], status="ready" if plugin["enabled"] else "off", required=child.get("optional") is not True)
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
                row.update(status="off" if excluded else status, reasons=reasons, setup=describe_mcp_setup(server, cfg),
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
            status = "retained" if plugin.get("retained") else "attention" if plugin.get("publication_pending") or plugin.get("diagnostics") or plugin.get("health") in {"failed", "error", "load_failed"} else "setup" if not plugin.get("setup_complete") or plugin.get("health") != "passed" else "ready" if plugin["enabled"] else "off"
            if plugin.get("publication_pending"):
                reasons.append("Check the interrupted package operation before making another change.")
            if plugin.get("capabilities", {}).get("enable", {}).get("code") == "package_source_removed":
                status = "attention"
            for child in children:
                inherited_off = not plugin["enabled"] and child["status"] == "off" and (
                    child["kind"] == "skill" or not child["reasons"])
                if child["status"] != "ready" and not inherited_off:
                    reasons.append(child["name"] + (" (optional): " if not child["required"] else ": ") + (" ".join(child["reasons"]) or "Turn on this included capability."))
                    if child["required"] and status not in {"attention", "retained"}:
                        status = "attention" if child["status"] == "attention" else "setup"
            actions = [a for a, c in plugin["capabilities"].items() if c.get("available")]
            if plugin.get("recoverable"):
                actions.append("restore")
            if plugin.get("publication_pending"):
                actions.append("recover")
            rows.append(_item("plugin", plugin["plugin_id"], plugin["name"], description=plugin["description"][:2048],
                source="portable" if plugin["package_format"] != "row-bot-v2" else "native", publisher=plugin.get("publisher", "")[:160],
                license=plugin.get("license", "")[:256], source_url=_url(plugin.get("source_url", "")), version=plugin["version"][:128], pin=plugin.get("pin", ""),
                enabled=plugin["enabled"], installed=plugin["installed"], status=status, revision=plugin.get("manifest_revision") or "",
                compatibility="partial" if plugin.get("diagnostics") else "supported", reasons=reasons[:16], children=children, actions=actions))
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
                status="recovery" if capability_configuration_controls.config.configuration_recovery_required() else status, reasons=reasons, setup=describe_mcp_setup(server, cfg),
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


def _mcp_identity(entry) -> str:
    # Never merge by label, repository alone, or a versionless package command.
    install = entry.install or {}
    if entry.source == "official":
        binding = (entry.metadata or {}).get("setup_digest")
        return "mcp:registry:" + _digest([install, binding]) if binding else ""
    if install.get("url"):
        return "mcp:endpoint:" + _digest([install, entry.transport])
    if install.get("command") and (entry.metadata or {}).get("version"):
        return "mcp:package:" + _digest([install, (entry.metadata or {}).get("version")])
    return ""


def _error_status(exc: Exception) -> str:
    code = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if code in {401, 403}:
        return "auth_required"
    if code == 429:
        return "rate_limited"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "malformed" if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)) else "error"


def _search_source(source: str, *, owner_id: str, query: str, refresh: bool, refresh_catalogs: bool,
                   cancelled: Callable[[], bool], validate: Callable[[], None]) -> tuple[list, list, dict]:
    from row_bot.plugins import hermes_catalog
    from row_bot.mcp_client import marketplace
    from row_bot.application import client_skill_hub, plugin_commands
    items, statuses, references = [], [], {}
    try:
        if source == "hermes":
            catalog = hermes_catalog.read_catalog(refresh=refresh, cancelled=cancelled)
            for entry in catalog["entries"]:
                if query.casefold() not in (entry["name"] + " " + entry["description"]).casefold():
                    continue
                item = _item("plugin", entry["id"], entry["name"], installed=False, status="discover", source="hermes",
                    description=entry["description"], publisher=entry["publisher"], source_url=_url(entry["url"]), version=entry["version"],
                    pin=entry["pin"], compatibility=entry["compatibility"], reasons=[entry["reason"]], platforms=entry["platforms"], actions=["preview"],
                    canonical_identity="plugin:" + entry["source_identity"] + "@" + entry["pin"])
                items.append(item)
                references[item["id"]] = {"kind": "plugin", "reference": entry["id"], "pin": entry["pin"], "identity": entry["source_identity"]}
            statuses.append({"source": source, "status": catalog["status"], "message": catalog["message"], "fetched_at": catalog["fetched_at"] or None})
        elif source == "hermes_mcp":
            from row_bot.plugins import hermes_mcp
            catalog = hermes_mcp.read_catalog(refresh=refresh, cancelled=cancelled)
            for name in catalog.get("names", []):
                if query.casefold() not in name.casefold():
                    continue
                item = _item("mcp", "hermes_mcp:" + name, name, installed=False, status="discover", source=source,
                    pin=catalog["pin"], compatibility="not_inspected", actions=["preview"],
                    description="Pinned Hermes optional-MCP recipe. Inspect to check compatibility.")
                items.append(item)
                references[item["id"]] = {"kind": "hermes_mcp", "name": name, "pin": catalog["pin"]}
            statuses.append({"source": source, "status": catalog["status"], "message": catalog["message"], "fetched_at": catalog.get("fetched_at")})
        elif source in {"recommended", "official", "examples"}:
            from row_bot.mcp_client import registry_snapshot
            snapshot = registry_snapshot.read_snapshot()
            if source == "official" and refresh_catalogs:
                try:
                    snapshot = registry_snapshot.refresh_snapshot(cancelled=cancelled)
                except Exception:
                    snapshot = {**snapshot, "status": "stale" if snapshot["entries"] else "error"}
            entries = snapshot["entries"] if source == "official" else marketplace.CURATED_STARTER_CATALOG if source == "recommended" else []
            result = marketplace.MarketplaceSearchResult(marketplace._filter_relevant(entries, query), "cache")
            if source == "examples":
                for name, reference, description, evidence in (
                    ("Local text tools", "bundled:local-text-tools", "Two no-auth writing skills, a supporting checklist, and local text statistics over MCP.",
                     "MIT; shipped version 1.0.0 and reviewed tree digest. Managed Node is optional for the MCP child. Windows/macOS/Linux recipe; only Windows checked locally. No network, file reads or telemetry in the server. Skill prompts use the conversation's chosen model."),
                    ("Hello Tool (native example)", "https://github.com/siddsachar/row-bot/tree/9435afbc930799ec30a622a2eb3d234a05214f31/examples/plugins/hello-tool", "Existing native Row-Bot plugin demonstrating a minimal local tool.",
                     "MIT; pinned repository example 0.1.0. Requires Row-Bot's private Python worker environment; no third-party dependencies or telemetry. Windows/macOS/Linux fixture coverage; clean-machine checks pending."),
                ):
                    if query.casefold() not in (name + description).casefold():
                        continue
                    item = _item("plugin", reference, name, installed=False, status="discover", source="examples", publisher="Row-Bot", license="MIT",
                        description=description, reasons=[evidence], evidence="Source reviewed; see setup details and validation limits.", actions=["preview"])
                    items.append(item)
                    references[item["id"]] = {"kind": "plugin", "reference": reference}
            for entry in result.entries:
                if source == "recommended" and not (entry.metadata or {}).get("integration_starter"):
                    continue
                supported = bool(entry.install and (entry.install.get("url") or entry.install.get("command")))
                item = _item("mcp", entry.source + ":" + entry.id, entry.name, installed=False, status="discover", source=entry.source,
                    description=entry.description[:2048], source_url=_url(entry.url), publisher=entry.publisher[:160], compatibility="not_inspected" if supported else "unsupported",
                    reasons=entry.notes[:16] or ([] if supported else ["No supported launch recipe is available; use advanced configuration."]),
                    evidence=(entry.metadata or {}).get("evidence", "Publisher listing only; live service untested."),
                    license=(entry.metadata or {}).get("license", ""), pin=(entry.metadata or {}).get("version_policy", ""), actions=["preview"] if supported else [], version=str((entry.metadata or {}).get("version", ""))[:128],
                    auth_requirement="required" if entry.requires_auth else "unknown",
                    canonical_identity=_mcp_identity(entry), evidence_stage="metadata_inspected" if source == "recommended" else "listed")
                items.append(item)
                references[item["id"]] = {"kind": "mcp", "entry": entry}
            statuses.append(source_status(source, status=snapshot["status"] if source == "official" else "cached",
                fetched_at=snapshot.get("captured_at") if source == "official" else None,
                snapshot_version=snapshot.get("api_version", "") if source == "official" else "",
                snapshot_digest=snapshot.get("digest", "") if source == "official" else "",
                truncated=source == "official" and not snapshot.get("complete", False)))
        elif source == "native":
            native_rows, _ = _pages(plugin_commands.read_plugin_catalog, validate, query=query, source="marketplace")
            for row in native_rows:
                item = _item("plugin", row["plugin_id"], row["name"], installed=False, status="discover", source="native", description=row["description"],
                    version=row["version"], compatibility="not_inspected", actions=["install"] if row["capabilities"]["install"]["available"] else [])
                items.append(item)
                references[item["id"]] = {"kind": "native", "plugin_id": row["plugin_id"]}
            statuses.append({"source": source, "status": "cached", "message": "Saved native marketplace. Refresh through its reviewed lifecycle action.", "fetched_at": None})
        else:
            result = client_skill_hub.search_public_skills(owner_id=owner_id, query=query, source=source,
                refresh=refresh, cached_only=not refresh, limit=96, cancelled=cancelled)
            for entry in result["entries"]:
                item = _item("skill", entry["id"], entry["name"], installed=entry["installed"], status="discover", source=entry["source"], description=entry["description"],
                    publisher=entry["author"], source_url=_url(entry.get("url", "")), compatibility="not_inspected", actions=["preview"])
                items.append(item)
                references[item["id"]] = {"kind": "skill", "revision": result["revision"], "entry_id": entry["id"]}
            statuses.extend({"source": source, "status": str(s["status"]), "message": str(s.get("message", "")), "fetched_at": s.get("fetched_at"), "truncated": result.get("has_more", False)} for s in result.get("source_statuses", []))
    except Exception as exc:
        statuses.append({"source": source, "status": _error_status(exc), "message": "This source is unavailable. Other results remain available.", "fetched_at": None})
    checked = []
    for item in items:
        try:
            for field, bound in {"id": 512, "owner_ref": 256, "name": 256, "description": 2048,
                    "source": 80, "publisher": 160, "source_url": 2048, "version": 128, "pin": 128,
                    "license": 256, "evidence": 512, "canonical_identity": 1024}.items():
                if not isinstance(item[field], str) or len(item[field]) > bound:
                    raise ValueError("invalid_catalog_item")
            if item["compatibility"] not in {"supported", "partial", "unsupported", "not_inspected"}:
                raise ValueError("invalid_catalog_item")
            checked.append(item)
        except ValueError:
            statuses = [{"source": source, "status": "malformed", "message": "Some catalog metadata could not be read.", "fetched_at": None}]
    items = checked
    references = {item["id"]: references[item["id"]] for item in items if item["id"] in references}
    return items, [source_status(source, **{k: v for k, v in status.items() if k != "source"}) for status in statuses], references


def _rank_merge(items: list[dict], query: str) -> list[dict]:
    tokens = re.findall(r"\w+", query.casefold())
    def rank(item: dict) -> tuple:
        name = item["name"].casefold()
        text = name + " " + item["description"].casefold() + " " + item["publisher"].casefold()
        return (-(100 if name == query.casefold() and query else 0)
                - sum(10 if token in name else 1 if token in text else 0 for token in tokens),
                -int(item["evidence_stage"] == "metadata_inspected"),
                item["name"].casefold(), item["id"])
    grouped = {}
    for item in sorted(items, key=rank):
        key = item["canonical_identity"] or item["id"]
        if key in grouped:
            grouped[key]["attributions"].extend(a for a in item["attributions"] if a not in grouped[key]["attributions"])
        else:
            grouped[key] = item
    ordered, positions = [], {}
    for item in sorted(grouped.values(), key=rank):
        score = rank(item)[:2]
        source = item["attributions"][0]["source"] if item["attributions"] else item["source"]
        group = (score, source)
        position = positions.get(group, 0)
        positions[group] = position + 1
        ordered.append((score, position, source, rank(item), item))
    return [row[-1] for row in sorted(ordered, key=lambda row: row[:-1])]


def search_integrations(*, owner_id: str, query: str = "", sources: list[str] | None = None,
        kind: str = "all", refresh: bool = False, refresh_catalogs: bool = False,
        include_incompatible: bool = False, cursor: str | None = None, limit: int = 50,
        cancelled: Callable[[], bool] = lambda: False, validate: Callable[[], None] = lambda: None) -> dict:
    """One bounded fan-out; immutable owner-bound pagination and late-result suppression."""
    validate()
    if kind not in {"all", "mcp", "skill", "plugin"} or len(query) > 256 or not 1 <= limit <= 96:
        raise ClientPlatformError("invalid_integration_query")
    selected = sorted(set(sources if sources is not None else [s for s, spec in SOURCES.items()
        if spec[2] == "eligible" and (kind == "all" or kind == spec[0])]))
    if len(selected) > len(SOURCES) or set(selected) - SOURCES.keys():
        raise ClientPlatformError("invalid_integration_query")
    selected = [s for s in selected if kind == "all" or SOURCES[s][0] == kind]
    key = _digest([query, selected, kind, include_incompatible])
    if cursor:
        try:
            revision, raw_offset = cursor.split(":")
            offset = int(raw_offset)
            with _LOCK:
                stamp, page, refs = _SEARCHES[(owner_id, revision)]
            if time.monotonic() - stamp > 1200 or page["key"] != key or not 0 <= offset < len(page["items"]):
                raise ValueError
        except (ValueError, KeyError):
            raise ClientPlatformError("cursor_expired") from None
        validate()
        return _search_page(page, offset, limit)
    stopped = threading.Event()
    def check_cancelled() -> bool:
        return stopped.is_set() or cancelled()
    items, statuses, references, pending = [], [], {}, {}
    def start(source: str) -> concurrent.futures.Future:
        deadline = time.monotonic() + SOURCE_DEADLINE
        future = concurrent.futures.Future()
        def run() -> None:
            if not _SEARCH_SLOTS.acquire(timeout=SOURCE_DEADLINE):
                future.set_result(([], [source_status(source, status="busy", message="Catalog search capacity reached; retry shortly.")], {}))
                return
            try:
                if not check_cancelled() and time.monotonic() < deadline:
                    future.set_result(_search_source(source, owner_id=owner_id, query=query, refresh=refresh,
                        refresh_catalogs=refresh_catalogs, cancelled=lambda: check_cancelled() or time.monotonic() >= deadline, validate=validate))
            except BaseException as exc:
                future.set_exception(exc)
            finally:
                _SEARCH_SLOTS.release()
        threading.Thread(target=run, daemon=True, name="integration-catalog-" + source).start()
        return future
    for source in selected:
        if SOURCES[source][2] not in {"eligible", "explicit_only"}:
            statuses.append(source_status(source))
        else:
            pending[start(source)] = (source, time.monotonic() + SOURCE_DEADLINE)
    try:
        while pending:
            if cancelled():
                raise ClientPlatformError("integration_search_cancelled")
            done, _ = concurrent.futures.wait(pending, timeout=0.05, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in list(pending):
                source, deadline = pending[future]
                if future not in done and time.monotonic() < deadline:
                    continue
                del pending[future]
                if future not in done:
                    statuses.append(source_status(source, status="timeout", message="Catalog deadline reached; other results remain available."))
                    continue
                rows, states, refs = future.result()
                for row in rows:
                    row["attributions"] = [{"source": source, "item_id": row["id"], "url": row["source_url"],
                        "publisher": row["publisher"], "version": row["version"], "pin": row["pin"]}]
                items.extend(rows)
                statuses.extend(states)
                references.update(refs)
        validate()
        if cancelled():
            raise ClientPlatformError("integration_search_cancelled")
        items = _rank_merge([r for r in items if include_incompatible or r["compatibility"] != "unsupported"], query)
        statuses.sort(key=lambda status: status["source"])
        revision = _digest([key, items, references])
        page = {"schema_version": 1, "revision": revision, "items": items, "total": len(items), "sources": statuses, "key": key}
        with _LOCK:
            if len(_RESULTS) >= 64:
                _RESULTS.pop(next(iter(_RESULTS)))
            if len(_SEARCHES) >= 64:
                _SEARCHES.pop(next(iter(_SEARCHES)))
            _RESULTS[(owner_id, revision)] = (time.monotonic(), references)
            _SEARCHES[(owner_id, revision)] = (time.monotonic(), copy.deepcopy(page), references)
        return _search_page(page, 0, limit)
    finally:
        stopped.set()


def _search_page(page: dict, offset: int, limit: int) -> dict:
    return {k: copy.deepcopy(v) for k, v in page.items() if k != "key" and k != "items"} | {
        "items": copy.deepcopy(page["items"][offset:offset + limit]),
        "next_cursor": f"{page['revision']}:{offset + limit}" if offset + limit < page["total"] else None}


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
        if value["reference"].startswith("hermes:"):
            from row_bot.plugins import hermes_catalog
            current = hermes_catalog.read_catalog(refresh=True)
            entry = next((row for row in current["entries"] if row["id"] == value["reference"]), None)
            if current["status"] != "live" or entry is None or entry["compatibility"] == "unsupported":
                raise ClientPlatformError("integration_source_unavailable")
            if entry["pin"] != value["pin"] or entry["source_identity"] != value["identity"]:
                raise ClientPlatformError("integration_catalog_changed")
        result = {"kind": "plugin", "plugin": inspect_package(owner_id=owner_id, reference=value["reference"])}
    elif value["kind"] == "skill":
        result = {"kind": "skill", "skill": preview_public_skill(owner_id=owner_id, revision=value["revision"], entry_id=value["entry_id"])}
    elif value["kind"] == "hermes_mcp":
        from row_bot.plugins.hermes_mcp import inspect_recipe
        result = {"kind": "mcp", "mcp": inspect_recipe(value["name"], value["pin"])}
    elif value["kind"] == "mcp":
        entry = value["entry"]
        if entry.source == "official":
            from row_bot.mcp_client.registry_snapshot import revalidate_entry
            entry = revalidate_entry(entry)
        import re
        name = re.sub(r"[^A-Za-z0-9_. -]", "-", entry.name).strip()[:64] or "Connection"
        result = {"kind": "mcp", "mcp": {"name": name, "import_json": json.dumps({"mcpServers": {name: entry_to_server_config(entry)}}),
            "requires_auth": entry.requires_auth, "auth_requirement": "required" if entry.requires_auth else "unknown", "notes": entry.notes[:16], "source_url": _url(entry.url)}}
    else:
        result = {"kind": "native", "plugin_id": value["plugin_id"]}
    validate()
    return result
