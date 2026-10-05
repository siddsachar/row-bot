"""Apps & Skills over the existing owners: the typed /integrations API. No second runtime."""
from __future__ import annotations

from collections.abc import Callable
import concurrent.futures
from itertools import islice
from pathlib import PurePosixPath
from typing import Any
import copy
import hashlib
import json
import threading
import time
from urllib.parse import urlsplit, urlunsplit

from row_bot.application.client_platform import ClientPlatformError
from row_bot.integrations import facts, plans, presets
from row_bot.integrations import sources as catalog
from row_bot.integrations.safe import TtlCache

_SEARCHES = TtlCache(1200, 64)
_SEARCH_SLOTS = threading.BoundedSemaphore(4)
SOURCE_DEADLINE = 8.0
# "app" is everything that is not a skill: connections and the packages that bring them.
_KINDS = {"all": {"skill", "mcp", "plugin"}, "app": {"mcp", "plugin"}, "skill": {"skill"}, "mcp": {"mcp"}, "plugin": {"plugin"}}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _method(row: dict) -> str:
    """How an app connects, for its card: signs in, takes a key, is hosted, or runs on this computer."""
    if row["kind"] == "plugin":
        return "local"
    if row["kind"] != "mcp" or row["canonical_identity"].startswith("account:"):
        return ""
    setup = row.get("setup")
    if setup:
        hosted, auth = setup["execution"] == "hosted", setup["auth_mode"]
    else:
        hosted = row["canonical_identity"].startswith("mcp:endpoint:")
        auth = (row["app"] or {}).get("auth") or ("oauth" if row["auth_requirement"] == "required" else "")
        if not hosted and not row["canonical_identity"].startswith("mcp:registry:"):
            return "local"
    return "api_key" if auth == "api_key" else "local" if not hosted else "hosted_sign_in" if auth == "oauth" else "hosted"


def entry(row: dict) -> dict:
    """The typed /integrations shape of one entry."""
    return {"id": row["id"], "kind": row["kind"], "parent_id": row["parent_id"], "name": row["name"],
            "description": row["description"], "app": copy.deepcopy(row["app"]), "icon": row["icon"], "verified": row["verified"],
            "signals": copy.deepcopy(row["signals"]), "source": row["source"], "method": _method(row),
            "publisher": row["publisher"], "version": row["version"], "installed": row["installed"], "enabled": row["enabled"],
            "required": row.get("required", True), "account_label": row["account_label"], "compatibility": row["compatibility"],
            "evidence": row["evidence_stage"], "tested_with_row_bot": row["tested_with_row_bot"], "lifecycle": row["lifecycle"],
            "readiness": row["readiness"], "blockers": copy.deepcopy(row["blockers"][:64]), "next_action": dict(row["next_action"]),
            "attributions": copy.deepcopy(row["attributions"]), "children": [entry(child) for child in row["children"]]}


def _installed(*, query: str, kind: str, cursor: str | None, limit: int, validate: Callable[[], None]) -> dict:
    validate()
    if kind not in _KINDS or len(query) > 256 or not 1 <= limit <= 50:
        raise ClientPlatformError("invalid_integration_query")
    rows, errors = facts.inventory(validate)
    rows = [entry(row) for row in sorted(rows, key=lambda item: (item["name"].casefold(), item["id"]))
            if (row["kind"] in _KINDS[kind] or any(c["kind"] in _KINDS[kind] for c in row["children"]))
            and catalog.matches(query, row["name"], row["description"], (row["app"] or {}).get("name", ""))]
    revision = _digest([rows, errors, query, kind])
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


def _error_status(exc: Exception) -> str:
    code = getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if code in {401, 403}:
        return "auth_required"
    if code == 429:
        return "rate_limited"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "malformed" if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)) else "error"


def _search_source(source: str, *, owner_id: str, query: str, refresh: bool, everything: bool,
                   cancelled: Callable[[], bool], validate: Callable[[], None]) -> tuple[list, list, dict, int]:
    """One source adapter's results; a failing source never hides the others."""
    adapter = catalog.SOURCES[source]
    try:
        found = adapter.search(catalog.Search(owner_id, query, refresh, cancelled, validate, everything))
    except Exception as exc:
        return [], [adapter.status(status=_error_status(exc), message="This source is unavailable. Other results remain available.")], {}, 0
    return found.rows, found.statuses, found.references, found.hidden


def _remember(owner_id: str, key: str, items: list[dict], statuses: list[dict], references: dict, hidden: int = 0) -> dict:
    """Keep one owner's result list, so its entries can be opened and set up from it."""
    revision = _digest([key, [entry(row) for row in items], list(references)])
    references = {item["id"]: references[item["id"]] for item in items if item["id"] in references}
    page = {"schema_version": 1, "revision": revision, "items": items, "total": len(items), "sources": statuses,
            "hidden": hidden}
    _SEARCHES.put((owner_id, revision), (key, page, references))
    return page


def _search(*, owner_id: str, query: str, sources: list[str] | None, kind: str, refresh: bool,
            include_incompatible: bool, cursor: str | None, limit: int, cancelled: Callable[[], bool],
            validate: Callable[[], None]) -> tuple[dict, int]:
    """One bounded fan-out; immutable owner-bound pagination and late-result suppression.
    ``include_incompatible`` ("Show all results") also keeps unsupported, placeholder and duplicate records."""
    registry = catalog.SOURCES
    validate()
    if kind not in _KINDS or len(query) > 256 or not 1 <= limit <= 96:
        raise ClientPlatformError("invalid_integration_query")
    selected = sorted(set(sources if sources is not None else [s for s, a in registry.items()
        if a.eligibility == "eligible" and a.kind in _KINDS[kind]]))
    if len(selected) > len(registry) or set(selected) - registry.keys():
        raise ClientPlatformError("invalid_integration_query")
    selected = [s for s in selected if registry[s].kind in _KINDS[kind]]
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
    items, statuses, references, pending, hidden = [], [], {}, {}, 0

    def start(source: str) -> concurrent.futures.Future:
        deadline = time.monotonic() + SOURCE_DEADLINE
        future = concurrent.futures.Future()

        def run() -> None:
            if not _SEARCH_SLOTS.acquire(timeout=SOURCE_DEADLINE):
                future.set_result(([], [registry[source].status(status="busy", message="Catalog search capacity reached; retry shortly.")], {}, 0))
                return
            try:
                if not check_cancelled() and time.monotonic() < deadline:
                    future.set_result(_search_source(source, owner_id=owner_id, query=query, refresh=refresh,
                        everything=include_incompatible, cancelled=lambda: check_cancelled() or time.monotonic() >= deadline, validate=validate))
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
                rows, states, refs, left_out = future.result()
                hidden += left_out
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
        statuses.sort(key=lambda status: status["source"])
        return _remember(owner_id, key, catalog.rank(shown, query), statuses, references, hidden), 0
    finally:
        stopped.set()


def _page(page: dict, offset: int, limit: int) -> dict:
    return {k: copy.deepcopy(v) for k, v in page.items() if k != "items"} | {
        "items": [entry(row) for row in page["items"][offset:offset + limit]],
        "next_cursor": f"{page['revision']}:{offset + limit}" if offset + limit < page["total"] else None}


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
    try:
        return catalogs.set_schedule(**change) if change is not None else catalogs.schedule()
    except ValueError as exc:
        raise ClientPlatformError("invalid_integration_query") from exc


def list_apps(query: str = "") -> dict:
    """App identities, featured first, or those a query names by name, synonym or job; local data only."""
    from row_bot.integrations import apps
    if len(query) > 256:
        raise ClientPlatformError("invalid_integration_query")
    wanted = query.casefold().strip()
    known = sorted((app for app in apps.catalog()[0].values() if catalog.matches(query, apps.text(app))),
                   key=lambda app: (wanted != app.name.casefold(), app.featured_rank or 1_000_000, app.name.casefold()))
    return {"schema_version": 1, "items": [app.view() for app in known]}


def read_icons(icon_ids: list[str]) -> dict:
    """Icons from bundled marks, letter avatars or the update-time cache, in one answer; never fetched here."""
    from row_bot.integrations import icons
    return {"schema_version": 1, "items": icons.batch(icon_ids)}


def list_presets() -> dict:
    return {"schema_version": 1, "items": presets.views()}


def read_items(*, owner_id: str, query: str = "", kind: str = "all", scope: str = "installed", cursor: str | None = None,
               limit: int = 50, everything: bool = False, validate: Callable[[], None] = lambda: None) -> dict:
    """Installed items, or the local catalogs (``everything``: placeholder and duplicate records too). Neither
    contacts a source."""
    if scope == "installed":
        return _installed(query=query, kind=kind, cursor=cursor, limit=limit, validate=validate)
    if scope != "catalog":
        raise ClientPlatformError("invalid_integration_query")
    page, offset = _search(owner_id=owner_id, query=query, sources=None, kind=kind, refresh=False, include_incompatible=everything,
        cursor=cursor, limit=limit, cancelled=lambda: False, validate=validate)
    return _page(page, offset, limit)


def search_items(*, owner_id: str, cancelled: Callable[[], bool] = lambda: False, validate: Callable[[], None] = lambda: None,
                 **fields: Any) -> dict:
    """An explicit catalog search; only this, and catalog refresh, contacts sources."""
    page, offset = _search(owner_id=owner_id, cancelled=cancelled, validate=validate, **fields)
    return _page(page, offset, fields["limit"])


def _added(owner_id: str, kind: str, name: str, reference: dict, *, description: str, source_url: str = "",
           unsupported: str = "", install: dict | None = None) -> dict:
    """An entry for something the person brought themselves; it opens like a catalog entry."""
    from row_bot.integrations import apps
    ref = "link:" + _digest([kind, reference])[:32]
    row = facts.finish(facts.entry(kind, ref, name, installed=False, lifecycle="available", source="link",
        description=description, source_url=source_url, compatibility="unsupported" if unsupported else "not_inspected",
        icon=apps.letter(name), canonical_identity=catalog.mcp_identity(install) if install else "",
        blockers=[facts.blocker("unsupported", unsupported)] if unsupported else []))
    return _page(_remember(owner_id, _digest(["added", row["id"]]), [row], [], {row["id"]: reference}), 0, 1)


def resolve_reference(*, owner_id: str, reference: str, kind: str = "", validate: Callable[[], None] = lambda: None) -> dict:
    """What a pasted link is (a hosted app, a skill or a package), read locally; nothing is fetched until consent."""
    from row_bot.skills_hub.input_detection import detect_source_input
    validate()
    link = reference.strip()
    parts = urlsplit(link)
    if len(link) > 2048 or kind not in {"", "mcp", "skill", "plugin"} or parts.scheme != "https" or not parts.hostname \
            or parts.username or parts.password:
        raise ClientPlatformError("integration_link_unsupported")
    detected = detect_source_input(link).kind
    skill_path = parts.path.endswith("SKILL.md") or "/skills/" in parts.path
    guess = kind or ("skill" if detected in {"marketplace_url", "direct_skill_url", "well_known_index_url"} or (
        detected == "github_url" and skill_path) else "plugin" if detected == "github_url" else "mcp")
    name = PurePosixPath(parts.path.rstrip("/")).name or parts.hostname
    if guess == "mcp" and (parts.query or parts.fragment):
        raise ClientPlatformError("integration_link_unsupported")  # Keys belong in the keychain, never in a saved address.
    link = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    if guess == "mcp":
        from row_bot.integrations import inputs
        from row_bot.mcp_client.marketplace import MarketplaceEntry
        # A key inside the address (".../s/<key>/mcp") is never saved as part of it: it becomes a
        # secret the person enters, kept in the keychain and put back only when connecting.
        segments, declared = parts.path.split("/"), []
        for index, segment in enumerate(segments):
            if inputs.secret_segment(segment):
                key = "link_key" + (f"_{len(declared) + 1}" if declared else "")
                declared.append(inputs.declaration(key, target="url_variable", name=key, label="Key from your link",
                    description="The part of the link that works like a password. Paste it again here.", secret=True,
                    required=True))
                segments[index] = "{" + key + "}"
        link = urlunsplit((parts.scheme, parts.netloc, "/".join(segments), "", ""))
        install = {"transport": "streamable_http", "url": link, **({"inputs": declared} if declared else {})}
        entry_ = MarketplaceEntry(id="link-" + _digest(link)[:12], name=parts.hostname, description="A connection you added by link.",
            source="link", url=link if not declared else "", transport="streamable_http", install=install, publisher=parts.hostname,
            metadata={"auth_mode": "api_key"} if declared else None)
        return _added(owner_id, "mcp", parts.hostname, {"kind": "mcp", "entry": entry_}, install=install,
                      description="A connection you added by link. Row-Bot checks it after you agree.")
    if guess == "plugin":
        return _added(owner_id, "plugin", name, {"kind": "plugin", "reference": link}, source_url=link,
                      description="A package you added by link. Row-Bot checks it after you agree.")
    return _added(owner_id, "skill", name, {"kind": "skill", "link": link}, source_url=link,
                  description="A skill you added by link. Row-Bot checks it after you agree.")


def upload_file(*, owner_id: str, data: bytes, filename: str, validate: Callable[[], None] = lambda: None) -> dict:
    """A file the person picked: kept privately, recognised, and opened like a catalog entry."""
    from row_bot.integrations import uploads
    validate()
    try:
        staged = uploads.stage(data, filename)
    except (ValueError, OSError) as exc:
        raise ClientPlatformError(str(exc) if str(exc) in {"unsupported_upload", "upload_too_large", "unsafe_upload"}
                                  else "invalid_upload") from None
    kind = "mcp" if staged["kind"] == "mcpb" else staged["kind"]
    reference = {"kind": staged["kind"], "upload": staged["upload"]} if kind != "plugin" else {
        "kind": "plugin", "upload": str(uploads.path(staged["upload"])), "reference": ""}
    return _added(owner_id, kind, staged["name"], reference, description="Added from a file on this device.",
                  unsupported="Bundles (.mcpb) arrive in a later update." if staged["kind"] == "mcpb" else "")


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


def _ways(row: dict) -> list[dict]:
    """Every way to connect this item's app, from local catalogs only: vendor-published first, then
    the ones Row-Bot can set up completely, hosted before local."""
    from row_bot.integrations import apps, index
    from row_bot.mcp_client.marketplace import CURATED_STARTER_CATALOG
    app = apps.catalog()[0].get((row["app"] or {}).get("id", ""))
    if app is None or row["kind"] == "skill":
        return []
    sources = catalog.SOURCES
    rows = [sources["recommended"].row(e)[0] for e in CURATED_STARTER_CATALOG
            if apps.match(["curated:" + e.id.lower(), *apps.recipe_refs(e.install)]) is app]
    rows += [sources["official"].row(e)[0] for e in index.by_app(app.id)]
    if app.placeholder:
        rows.append(sources["accounts"].row(app)[0])
    try:
        from row_bot.plugins import hermes_catalog
        rows += [sources["hermes"].row(e)[0] for e in hermes_catalog.read_catalog()["entries"]
                 if apps.match(["hermes:" + e["id"].removeprefix("hermes:"), *apps.repository_refs(e["url"])]) is app]
    except (OSError, ValueError, KeyError):
        pass
    found, seen = [], set()
    for way in sorted((entry(facts.finish(r)) for r in rows), key=lambda w: (
            not w["verified"], w["compatibility"] == "unsupported", w["method"] == "local", w["name"].casefold(), w["id"])):
        identity = next((r["canonical_identity"] for r in rows if r["id"] == way["id"]), "") or way["id"]
        if identity in seen:
            continue
        seen.add(identity)
        found.append({"id": way["id"], "name": way["name"], "method": way["method"], "verified": way["verified"],
                      "publisher": way["publisher"], "supported": way["compatibility"] != "unsupported"})
    for index_, way in enumerate(found[:24]):
        way["recommended"] = index_ == 0 and way["supported"]
    return found[:24]


def _about(row: dict, validate: Callable[[], None], plan: dict | None) -> dict:
    """What the detail page shows beyond the card: settings, access, source facts and, for skills, what's inside."""
    setup, local = row.get("setup") or {}, row["kind"] == "skill"
    about = {"license": row["license"], "source_url": row["source_url"], "pin": row["pin"], "identifier": row["id"],
             "destination": setup.get("destination", "") if setup.get("execution") == "hosted" else "",
             "runs_locally": setup.get("execution", "local") == "local" and row["kind"] != "skill",
             "saved_key": bool(setup.get("credential_configured")) and setup.get("auth_mode") == "api_key",
             "signs_in": setup.get("auth_mode") == "oauth", "signed_in": bool(setup.get("credential_configured")),
             "requirements": [{"label": r["label"][:96], "available": bool(r["available"])} for r in setup.get("requirements", [])][:16],
             "access": plans.current_access(row), "package": "", "files": [], "profiles": [], "ways": _ways(row),
             "actions": [intent for intent in ("turn_off", "update", "remove") if plans.changeable(row, intent)]}
    if plan is not None and row["lifecycle"] == "available":  # Not set up yet: say what the plan would do.
        about.update(destination=next(iter(plan["consent"]["destinations"]), ""),
                     runs_locally=plan["consent"]["runs_locally"] and row["kind"] != "skill")
    if row["parent_id"]:
        parent = facts.read(row["parent_id"], validate)
        about["package"] = parent["name"] if parent else ""
    if local and row["lifecycle"] != "available":
        from row_bot import skills
        from row_bot.agent_profiles import list_agent_profiles
        from row_bot.skills_hub.scanner import EXECUTABLE_EXTENSIONS
        skill = (skills.read_client_skills()["items"].get(row["owner_ref"]) or {}).get("skill")
        root = getattr(skill, "path", None)
        if root is not None and root.is_dir():
            # Bounded: a skill that vendors thousands of files still opens quickly.
            paths = sorted(islice((p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".row-bot")), 1000))[:100]
            about["files"] = [{"path": p.relative_to(root).as_posix()[:256], "size_bytes": p.stat().st_size,
                               "executable": p.suffix.lower() in EXECUTABLE_EXTENSIONS} for p in paths]
        about["profiles"] = [str(p.get("display_name") or p.get("slug"))[:80] for p in list_agent_profiles()
                             if row["owner_ref"] in ((p.get("skill_policy_json") or {}).get("skills_override") or [])][:32]
    return about


def read_item(*, owner_id: str, item_id: str, revision: str = "", intent: str = "", cleanup: bool = False,
              validate: Callable[[], None] = lambda: None, context: plans.Context | None = None) -> tuple[dict, dict | None]:
    """One entry with its unfinished plan, or the plan for its next action (or a requested
    intent). The second value is the plan still to consent to; reading never sends a command."""
    validate()
    row, reference = _resolve(owner_id, item_id, revision, validate)
    current = plans.open_plan(context or plans.Context(owner_id, owner_id, validate), item_id)
    if current is not None:
        return {"entry": entry(row), "plan": current, "about": _about(row, validate, current)}, None
    plan = plans.compute(row, reference, intent=intent, cleanup=cleanup)
    validate()
    return {"entry": entry(row), "plan": plans.view(plan) if plan else None, "about": _about(row, validate, plan)}, plan


def settle_item(ctx: plans.Context, *, item_id: str) -> dict:
    """The Retry on an unfinished change: check it again explicitly, then read the item."""
    ctx.validate()
    row = facts.read(item_id, ctx.validate)
    if row is None:
        raise ClientPlatformError("not_found")
    if row["kind"] == "plugin" and not ctx.local_owner:
        raise ClientPlatformError("owner_local_only")  # Package recovery stays with Row-Bot on this computer.
    facts.settle(row, ctx.owner_id, ctx.mcp_owner_id, ctx.validate)
    return read_item(owner_id=ctx.owner_id, item_id=item_id, validate=ctx.validate, context=ctx)[0]


def start_plan(ctx: plans.Context, *, plan_id: str, item_id: str, revision: str = "", intent: str = "", digest: str,
               preset: str = "", overrides: dict | None = None, cleanup: bool = False, background: bool = False) -> dict:
    ctx.validate()
    row, reference = _resolve(ctx.owner_id, item_id, revision, ctx.validate)
    return plans.start(ctx, row, reference, digest=digest, intent=intent, preset=preset, plan_id=plan_id,
                       overrides=overrides, cleanup=cleanup, background=background)


def existing_plan(ctx: plans.Context, plan_id: str) -> dict | None:
    """A retried start returns the plan it already admitted, without asking for consent again."""
    try:
        return plans.read_plan(ctx, plan_id)
    except plans.PlanError:
        return None
