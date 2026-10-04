"""Apps & Skills over the existing owners: the typed /integrations API, and the
legacy /settings/integrations adapters that Phase 3 removes. No second runtime."""
from __future__ import annotations

from collections.abc import Callable
import concurrent.futures
from typing import Any
import copy
import hashlib
import json
import threading
import time

from row_bot.application.client_platform import ClientPlatformError
from row_bot.integrations import facts, plans, presets
from row_bot.integrations import sources as catalog
from row_bot.integrations.safe import TtlCache

_SEARCHES = TtlCache(1200, 64)
_SEARCH_SLOTS = threading.BoundedSemaphore(4)
SOURCE_DEADLINE = 8.0
_LEGACY = ("id", "kind", "owner_ref", "parent_id", "name", "description", "source", "publisher", "source_url", "version",
           "pin", "license", "compatibility", "reasons", "platforms", "evidence", "installed", "enabled", "status", "revision",
           "actions", "auth_status", "account_label", "children", "target", "attributions", "evidence_stage",
           "tested_with_row_bot", "auth_requirement", "canonical_identity", "setup", "required")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def legacy(row: dict) -> dict:
    """The /settings/integrations shape of one entry."""
    value = {key: copy.deepcopy(row[key]) for key in _LEGACY if key in row}
    value["children"] = [legacy(child) for child in row["children"]]
    return value


def entry(row: dict) -> dict:
    """The typed /integrations shape of one entry."""
    return {"id": row["id"], "kind": row["kind"], "parent_id": row["parent_id"], "name": row["name"],
            "description": row["description"], "app": copy.deepcopy(row["app"]), "icon": row["icon"], "verified": row["verified"],
            "signals": copy.deepcopy(row["signals"]), "source": row["source"],
            "publisher": row["publisher"], "version": row["version"], "installed": row["installed"], "enabled": row["enabled"],
            "required": row.get("required", True), "account_label": row["account_label"], "compatibility": row["compatibility"],
            "evidence": row["evidence_stage"], "tested_with_row_bot": row["tested_with_row_bot"], "lifecycle": row["lifecycle"],
            "readiness": row["readiness"], "blockers": copy.deepcopy(row["blockers"][:64]), "next_action": dict(row["next_action"]),
            "attributions": copy.deepcopy(row["attributions"]), "children": [entry(child) for child in row["children"]]}


def _installed(*, query: str, kind: str, source: str, cursor: str | None, limit: int, validate: Callable[[], None],
               render: Callable[[dict], dict]) -> dict:
    validate()
    if kind not in {"all", "skill", "mcp", "plugin"} or len(query) > 256 or len(source) > 80 or not 1 <= limit <= 50:
        raise ClientPlatformError("invalid_integration_query")
    rows, errors = facts.inventory(validate)
    rows = [render(row) for row in sorted(rows, key=lambda item: (item["name"].casefold(), item["id"]))
            if (kind == "all" or row["kind"] == kind or any(c["kind"] == kind for c in row["children"]))
            and (source == "all" or row["source"] == source)
            and catalog.matches(query, row["name"], row["description"], (row["app"] or {}).get("name", ""))]
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


def read_integrations(*, query: str = "", kind: str = "all", source: str = "all", cursor: str | None = None,
        limit: int = 50, validate: Callable[[], None] = lambda: None) -> dict:
    return _installed(query=query, kind=kind, source=source, cursor=cursor, limit=limit, validate=validate, render=legacy)


def read_integration(integration_id: str, *, validate: Callable[[], None] = lambda: None) -> dict:
    row = facts.read(integration_id, validate)
    if row is None:
        raise ClientPlatformError("not_found")
    return legacy(row)


def _error_status(exc: Exception) -> str:
    code = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if code in {401, 403}:
        return "auth_required"
    if code == 429:
        return "rate_limited"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "malformed" if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)) else "error"


def _search_source(source: str, *, owner_id: str, query: str, refresh: bool,
                   cancelled: Callable[[], bool], validate: Callable[[], None]) -> tuple[list, list, dict]:
    """One source adapter's results; a failing source never hides the others."""
    adapter = catalog.SOURCES[source]
    try:
        found = adapter.search(catalog.Search(owner_id, query, refresh, cancelled, validate))
    except Exception as exc:
        return [], [adapter.status(status=_error_status(exc), message="This source is unavailable. Other results remain available.")], {}
    return found.rows, found.statuses, found.references


def _search(*, owner_id: str, query: str, sources: list[str] | None, kind: str, refresh: bool,
            include_incompatible: bool, cursor: str | None, limit: int, cancelled: Callable[[], bool],
            validate: Callable[[], None]) -> tuple[dict, int]:
    """One bounded fan-out; immutable owner-bound pagination and late-result suppression."""
    registry = catalog.SOURCES
    validate()
    if kind not in {"all", "mcp", "skill", "plugin"} or len(query) > 256 or not 1 <= limit <= 96:
        raise ClientPlatformError("invalid_integration_query")
    selected = sorted(set(sources if sources is not None else [s for s, a in registry.items()
        if a.eligibility == "eligible" and kind in {"all", a.kind}]))
    if len(selected) > len(registry) or set(selected) - registry.keys():
        raise ClientPlatformError("invalid_integration_query")
    selected = [s for s in selected if kind in {"all", registry[s].kind}]
    key = _digest([query, selected, kind, include_incompatible])
    if cursor:
        try:
            revision, raw_offset = cursor.split(":")
            offset = int(raw_offset)
            saved = _SEARCHES.get((owner_id, revision))
            if saved is None or saved[0] != key or not 0 <= offset < len(saved[1]["items"]):
                raise ValueError
        except ValueError:
            raise ClientPlatformError("cursor_expired") from None
        validate()
        return saved[1], offset
    stopped = threading.Event()

    def check_cancelled() -> bool:
        return stopped.is_set() or cancelled()
    items, statuses, references, pending = [], [], {}, {}

    def start(source: str) -> concurrent.futures.Future:
        deadline = time.monotonic() + SOURCE_DEADLINE
        future = concurrent.futures.Future()

        def run() -> None:
            if not _SEARCH_SLOTS.acquire(timeout=SOURCE_DEADLINE):
                future.set_result(([], [registry[source].status(status="busy", message="Catalog search capacity reached; retry shortly.")], {}))
                return
            try:
                if not check_cancelled() and time.monotonic() < deadline:
                    future.set_result(_search_source(source, owner_id=owner_id, query=query, refresh=refresh,
                        cancelled=lambda: check_cancelled() or time.monotonic() >= deadline, validate=validate))
            except BaseException as exc:
                future.set_exception(exc)
            finally:
                _SEARCH_SLOTS.release()
        threading.Thread(target=run, daemon=True, name="integration-catalog-" + source).start()
        return future
    for source in selected:
        if registry[source].eligibility not in {"eligible", "explicit_only"}:
            statuses.append(registry[source].status())
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
                    statuses.append(registry[source].status(status="timeout", message="Catalog deadline reached; other results remain available."))
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
        # Unsupported entries appear only when searched for, with their reason.
        shown = [r for r in items if include_incompatible or query.strip() or r["compatibility"] != "unsupported"]
        items = catalog.rank(shown, query)
        statuses.sort(key=lambda status: status["source"])
        revision = _digest([key, [legacy(row) for row in items], list(references)])
        references = {item["id"]: references[item["id"]] for item in items if item["id"] in references}
        page = {"schema_version": 1, "revision": revision, "items": items, "total": len(items), "sources": statuses}
        _SEARCHES.put((owner_id, revision), (key, page, references))
        return page, 0
    finally:
        stopped.set()


def _page(page: dict, offset: int, limit: int, render: Callable[[dict], dict]) -> dict:
    return {k: copy.deepcopy(v) for k, v in page.items() if k != "items"} | {
        "items": [render(row) for row in page["items"][offset:offset + limit]],
        "next_cursor": f"{page['revision']}:{offset + limit}" if offset + limit < page["total"] else None}


def search_integrations(*, owner_id: str, query: str = "", sources: list[str] | None = None, kind: str = "all",
        refresh: bool = False, include_incompatible: bool = False, cursor: str | None = None,
        limit: int = 50, cancelled: Callable[[], bool] = lambda: False, validate: Callable[[], None] = lambda: None) -> dict:
    page, offset = _search(owner_id=owner_id, query=query, sources=sources, kind=kind, refresh=refresh,
        include_incompatible=include_incompatible, cursor=cursor, limit=limit,
        cancelled=cancelled, validate=validate)
    return _page(page, offset, limit, legacy)


def preview_integration(*, owner_id: str, revision: str = "", item_id: str = "", kind: str = "plugin", reference: str = "",
                        local: bool = False, validate: Callable[[], None] = lambda: None) -> dict:
    from row_bot.plugins.hermes_catalog import inspect_package
    from row_bot.application.client_skill_hub import preview_public_skill
    validate()
    if reference:
        if kind != "plugin":
            raise ClientPlatformError("integration_import_type_required")
        return {"kind": "plugin", "plugin": inspect_package(owner_id=owner_id, reference=reference, local=local)}
    saved = _SEARCHES.get((owner_id, revision))
    if saved is None or item_id not in saved[2]:
        raise ClientPlatformError("integration_preview_expired")
    value = saved[2][item_id]
    if value["kind"] == "plugin":
        if value["reference"].startswith("hermes:"):
            from row_bot.plugins import hermes_catalog
            current = hermes_catalog.read_catalog(refresh=True)
            found = next((row for row in current["entries"] if row["id"] == value["reference"]), None)
            if current["status"] != "live" or found is None or found["compatibility"] == "unsupported":
                raise ClientPlatformError("integration_source_unavailable")
            if found["pin"] != value["pin"] or found["source_identity"] != value["identity"]:
                raise ClientPlatformError("integration_catalog_changed")
        result = {"kind": "plugin", "plugin": inspect_package(owner_id=owner_id, reference=value["reference"])}
    elif value["kind"] == "skill":
        result = {"kind": "skill", "skill": preview_public_skill(owner_id=owner_id, revision=value["revision"], entry_id=value["entry_id"])}
    elif value["kind"] == "hermes_mcp":
        from row_bot.plugins.hermes_mcp import inspect_recipe
        result = {"kind": "mcp", "mcp": inspect_recipe(value["name"], value["pin"])}
    elif value["kind"] == "mcp":
        listed = value["entry"]
        if listed.source == "official":
            from row_bot.mcp_client.registry_snapshot import revalidate_entry
            listed = revalidate_entry(listed)
        result = {"kind": "mcp", "mcp": catalog.describe(listed)}
    else:
        result = {"kind": "native", "plugin_id": value["plugin_id"]}
    validate()
    return result


# --- Typed /integrations API -------------------------------------------------

def list_sources() -> dict:
    from row_bot.integrations import catalogs
    states = catalogs.states()
    return {"schema_version": 1, "items": [{**source.view(), "catalog": states.get(source.id)}
                                           for source in catalog.SOURCES.values()]}


def update_source(source_id: str) -> dict:
    """Start the explicit update of one catalog; the client polls the source list."""
    from row_bot.integrations import catalogs
    try:
        catalogs.update(source_id)
    except ValueError as exc:
        raise ClientPlatformError("not_found") from exc
    return next(item for item in list_sources()["items"] if item["id"] == source_id)


def catalog_schedule(change: dict | None = None) -> dict:
    """The optional update schedule (off by default); ``change`` saves the user's choice."""
    from row_bot.integrations import catalogs
    return catalogs.set_schedule(**change) if change is not None else catalogs.schedule()


def list_apps(query: str = "") -> dict:
    """App identities, featured first, or those a query names by name, synonym or job; local data only."""
    from row_bot.integrations import apps
    if len(query) > 256:
        raise ClientPlatformError("invalid_integration_query")
    wanted = query.casefold().strip()
    known = sorted((app for app in apps.catalog()[0].values() if catalog.matches(query, apps.text(app))),
                   key=lambda app: (wanted != app.name.casefold(), app.featured_rank or 1_000_000, app.name.casefold()))
    return {"schema_version": 1, "items": [app.view() for app in known]}


def read_icon(icon_id: str) -> tuple[bytes, str]:
    """An icon from bundled marks, letter avatars or the update-time cache; never fetched here."""
    from row_bot.integrations import icons
    try:
        return icons.render(icon_id)
    except ValueError as exc:
        raise ClientPlatformError("not_found") from exc


def list_presets() -> dict:
    return {"schema_version": 1, "items": presets.views()}


def read_items(*, owner_id: str, query: str = "", kind: str = "all", scope: str = "installed", cursor: str | None = None,
               limit: int = 50, validate: Callable[[], None] = lambda: None) -> dict:
    """Installed items, or the local catalogs. Neither contacts a source."""
    if scope == "installed":
        return _installed(query=query, kind=kind, source="all", cursor=cursor, limit=limit, validate=validate, render=entry)
    if scope != "catalog":
        raise ClientPlatformError("invalid_integration_query")
    page, offset = _search(owner_id=owner_id, query=query, sources=None, kind=kind, refresh=False, include_incompatible=False,
        cursor=cursor, limit=limit, cancelled=lambda: False, validate=validate)
    return _page(page, offset, limit, entry)


def search_items(*, owner_id: str, cancelled: Callable[[], bool] = lambda: False, validate: Callable[[], None] = lambda: None,
                 **fields: Any) -> dict:
    """An explicit catalog search; only this, and catalog refresh, contacts sources."""
    page, offset = _search(owner_id=owner_id, cancelled=cancelled, validate=validate, **fields)
    return _page(page, offset, fields["limit"], entry)


def _resolve(owner_id: str, item_id: str, revision: str, validate: Callable[[], None]) -> tuple[dict, dict]:
    """An installed item, an entry from this owner's search, or a local catalog entry, with what owners need."""
    row = facts.read(item_id, validate)
    if row is not None:
        reference: dict = {}
        if row["kind"] == "mcp":
            from row_bot.application.capability_configuration_controls import _server_id
            from row_bot.mcp_client import config, targets
            saved = config.read_saved_configuration(targets.normalize(row["target"]))
            reference["cfg"] = next((cfg for name, cfg in saved.document.get("servers", {}).items()
                                     if _server_id(name) == row["owner_ref"]), {})
        return row, reference
    saved = _SEARCHES.get((owner_id, revision)) if revision else None
    found = next((r for r in saved[1]["items"] if r["id"] == item_id), None) if saved else None
    if found is not None and item_id in saved[2]:
        return copy.deepcopy(found), saved[2][item_id]
    local = catalog.catalog_entry(item_id)
    if local is None:
        raise ClientPlatformError("not_found")
    return local


def read_item(*, owner_id: str, item_id: str, revision: str = "", intent: str = "",
              validate: Callable[[], None] = lambda: None, context: plans.Context | None = None) -> tuple[dict, dict | None]:
    """One entry with its unfinished plan, or the plan for its next action (or a requested
    intent). The second value is the plan still to consent to; reading never sends a command."""
    validate()
    row, reference = _resolve(owner_id, item_id, revision, validate)
    current = plans.open_plan(context or plans.Context(owner_id, owner_id, validate), item_id)
    if current is not None:
        return {"entry": entry(row), "plan": current}, None
    plan = plans.compute(row, reference, intent=intent)
    validate()
    return {"entry": entry(row), "plan": plans.view(plan) if plan else None}, plan


def start_plan(ctx: plans.Context, *, plan_id: str, item_id: str, revision: str = "", intent: str = "", digest: str,
               preset: str = "") -> dict:
    ctx.validate()
    row, reference = _resolve(ctx.owner_id, item_id, revision, ctx.validate)
    return plans.start(ctx, row, reference, digest=digest, intent=intent, preset=preset, plan_id=plan_id)


def existing_plan(ctx: plans.Context, plan_id: str) -> dict | None:
    """A retried start returns the plan it already admitted, without asking for consent again."""
    try:
        return plans.read_plan(ctx, plan_id)
    except plans.PlanError:
        return None
