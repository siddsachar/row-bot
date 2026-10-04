"""Catalog sources. Each source is one small adapter; the list is served to clients.

Searching is passive (local data and saved results) unless a request is an
explicit online search or catalog refresh. Eligibility is decided here, on the
server; availability in a catalog never grants runtime authority.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import re
import time
from urllib.parse import urlsplit

from row_bot.integrations import apps, facts, icons
from row_bot.integrations.safe import public_url


def tokens(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.casefold())
    return [word for word in words if len(word) > 1] or words


def matches(query: str, *texts: str) -> bool:
    """Every query word appears among the entry's and its app's words."""
    haystack = " ".join(texts).casefold()
    return all(word in haystack for word in tokens(query))


@dataclass
class Search:
    owner_id: str
    query: str = ""
    refresh: bool = False
    cancelled: Callable[[], bool] = lambda: False
    validate: Callable[[], None] = lambda: None


@dataclass
class Found:
    rows: list[dict] = field(default_factory=list)
    statuses: list[dict] = field(default_factory=list)
    references: dict = field(default_factory=dict)

    def add(self, row: dict, reference: dict) -> None:
        self.rows.append(facts.finish(row))
        self.references[row["id"]] = reference


class Source:
    """``search`` returns entries; ``lookup`` resolves one locally for detail and plans.

    A source with ``network = "explicit"`` also has ``update``: the only time it contacts
    its catalog outside an explicit online search. It returns a small summary or raises,
    and a failed update keeps what the source had before."""
    id = kind = label = message = ""
    access = "local"
    eligibility = "eligible"
    network = "none"

    def view(self) -> dict:
        return {"id": self.id, "kinds": [self.kind], "label": self.label, "access": self.access,
                "eligibility": self.eligibility, "network": self.network,
                "enabled": self.eligibility == "eligible", "message": self.message}

    def status(self, **fields) -> dict:
        return {"source": self.id, "kind": self.kind, "access": self.access, "eligibility": self.eligibility,
                "enabled": self.eligibility == "eligible", "status": "unavailable" if self.access == "unavailable" else "empty",
                "message": self.message, "fetched_at": None, "snapshot_version": "", "snapshot_digest": "", "truncated": False, **fields}

    def search(self, search: Search) -> Found:
        return Found(statuses=[self.status()])

    def lookup(self, reference: str) -> dict | None:
        return None

    def update(self, cancelled: Callable[[], bool]) -> dict:
        raise ValueError("not_updatable")


def _available(kind: str, ref: str, name: str, *, app: apps.App | None, unsupported: str = "", verified: bool = False,
               **fields) -> dict:
    fields["icon"] = fields.get("icon") or (app.ref()["icon"] if app else apps.letter(name))
    return facts.entry(kind, ref, name, **{"installed": False, **fields}, lifecycle="available", verified=verified,
        app=app.ref(verified=verified) if app else None,
        blockers=[facts.blocker("unsupported", unsupported)] if unsupported else [])


def mcp_identity(install: dict | None, version: str = "", registry: str = "") -> str:
    """Source-neutral identity of one deployment: transport and endpoint, package and version,
    or (without a recipe) the Registry record."""
    install = install or {}
    parts = urlsplit(str(install.get("url") or ""))
    if parts.hostname:
        return f"mcp:endpoint:{install.get('transport', '')}:{parts.hostname.lower()}{parts.path.rstrip('/') or '/'}"
    refs = apps.recipe_refs(install)
    if refs:
        return "mcp:" + refs[0] + ("@" + version if version else "")
    return "mcp:registry:" + registry + "@" + version if registry else ""


class _McpCatalog(Source):
    kind = "mcp"

    def entries(self) -> list:
        return []

    def row(self, entry) -> tuple[dict, dict]:
        metadata = entry.metadata or {}
        refs = (["curated:" + entry.id.lower()] if self.id == "recommended" else apps.registry_refs(metadata.get("canonical_name", "")))
        refs += apps.recipe_refs(entry.install)
        app = apps.match(refs)
        supported = bool(entry.install and (entry.install.get("url") or entry.install.get("command")))
        known = metadata.get("auth_mode") in {"oauth", "api_key", "none"} or not (entry.install or {}).get("url") or bool(
            app and app.auth in {"oauth", "api_key", "none"})
        row = _available("mcp", entry.source + ":" + entry.id, entry.name, app=app, source=entry.source,
            verified=apps.verified(app, refs), setup_tier=0 if supported and known else 1 if supported else 2,
            updated_at=_day(metadata.get("updated_at", "")), icon=icons.entry_icon(app, metadata.get("icon", ""), entry.name),
            unsupported="" if supported else "No supported launch recipe is available; use advanced configuration.",
            description=entry.description[:2048], source_url=public_url(entry.url), publisher=entry.publisher[:160],
            compatibility="not_inspected" if supported else "unsupported", license=metadata.get("license", ""),
            evidence=metadata.get("evidence", "Publisher listing only; live service untested."),
            pin=metadata.get("version_policy", ""), actions=["preview"] if supported else [], version=str(metadata.get("version", ""))[:128],
            auth_requirement="required" if entry.requires_auth else "unknown", canonical_identity=mcp_identity(entry.install, metadata.get("version", ""), metadata.get("canonical_name", "")),
            evidence_stage="inspected" if self.id == "recommended" else "listed")
        row["blockers"] += [facts.blocker("note", note) for note in entry.notes[:8]]
        return row, {"kind": "mcp", "entry": entry}

    def search(self, search: Search) -> Found:
        found = Found()
        for entry in self.entries():
            app = apps.match(["curated:" + entry.id.lower()] + apps.recipe_refs(entry.install))
            if matches(search.query, entry.id, entry.name, entry.description, entry.publisher, apps.text(app)):
                found.add(*self.row(entry))
        return found

    def lookup(self, reference: str):
        return next((entry for entry in self.entries() if entry.source + ":" + entry.id == reference), None)


class Curated(_McpCatalog):
    id, label = "recommended", "Vendor recommendations"
    message = "Reviewed setup recipes; live accounts untested."

    def entries(self) -> list:
        from row_bot.mcp_client.marketplace import CURATED_STARTER_CATALOG
        return CURATED_STARTER_CATALOG

    def search(self, search: Search) -> Found:
        found = super().search(search)
        found.statuses.append(self.status(status="cached"))
        return found


class Registry(_McpCatalog):
    id, label, access, network = "official", "Official MCP Registry", "snapshot", "explicit"
    message = "The whole Registry, searched on this computer; updated only when you ask."

    def lookup(self, reference: str):
        from row_bot.integrations import index
        return index.lookup(reference.removeprefix(self.id + ":"))

    def search(self, search: Search) -> Found:
        from row_bot.integrations import index
        from row_bot.mcp_client.registry_snapshot import MAX_AGE
        try:
            results, total, current = index.search(search.query)
        except LookupError:  # Start-up has not finished building the local mirror.
            return Found(statuses=[self.status(status="pending", message="Preparing the Registry on this computer.")])
        found = Found()
        for entry, _derived in results:
            found.add(*self.row(entry))
        fetched = max(current["captured_at"], current.get("updated_at", 0))
        found.statuses.append(self.status(status="stale" if time.time() - fetched > MAX_AGE else "cached", fetched_at=fetched,
            snapshot_version="v0.1", snapshot_digest=current.get("digest", ""), truncated=total > len(results)))
        return found

    def update(self, cancelled: Callable[[], bool]) -> dict:
        """Records changed since the mirror's watermark (all of them when it is old), merged into
        a new index generation; then new Registry icons. Failure leaves the mirror as it was."""
        from datetime import datetime, timedelta, timezone
        from row_bot.integrations import index
        from row_bot.mcp_client import registry_snapshot
        current = index.ensure()
        try:
            mark = datetime.fromisoformat(current["watermark"].replace("Z", "+00:00"))
            fresh = datetime.now(timezone.utc) - mark < timedelta(days=180)
        except ValueError:
            fresh = False
        since = (mark - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ") if fresh else ""
        result = registry_snapshot.sync(since=since, etag=current.get("etag", "") if since else "", cancelled=cancelled)
        if not result["not_modified"]:
            rows = {e.metadata["canonical_name"]: e for e in index.rows(current)} if since else {}
            rows.update((e.metadata["canonical_name"], e) for e in result["entries"])
            for name in result["deleted"]:
                rows.pop(name, None)
            current = index.build(rows.values(), captured_at=current["captured_at"], etag=result["etag"],
                                  watermark=max(current["watermark"], result["watermark"]), updated_at=time.time(),
                                  cancelled=cancelled)
        cached = icons.cache_remote(index.icon_urls(current), cancelled=cancelled)
        return {"changed": len(result["entries"]) + len(result["deleted"]), "entries": current["count"], "icons": cached["cached"]}


class HermesMcp(Source):
    id, kind, label, access, network = "hermes_mcp", "mcp", "Hermes MCP recipes", "public", "explicit"
    message = "Pinned recipe metadata; inspect before setup."

    def search(self, search: Search) -> Found:
        from row_bot.plugins import hermes_mcp
        catalog = hermes_mcp.read_catalog(refresh=search.refresh, cancelled=search.cancelled)
        found = Found()
        for name in catalog.get("names", []):
            if matches(search.query, name):
                found.add(_available("mcp", "hermes_mcp:" + name, name, app=None, source=self.id, pin=catalog["pin"],
                    compatibility="not_inspected", actions=["preview"],
                    description="Pinned Hermes optional-MCP recipe. Inspect to check compatibility."),
                    {"kind": "hermes_mcp", "name": name, "pin": catalog["pin"]})
        found.statuses.append(self.status(status=catalog["status"], message=catalog["message"], fetched_at=catalog.get("fetched_at")))
        return found

    def update(self, cancelled: Callable[[], bool]) -> dict:
        from row_bot.plugins import hermes_mcp
        catalog = hermes_mcp.read_catalog(refresh=True, cancelled=cancelled)
        if catalog["status"] != "live":
            raise ValueError("source_unavailable")
        return {"entries": len(catalog.get("names", []))}


class Hermes(Source):
    id, kind, label, access, network = "hermes", "plugin", "Hermes", "public", "explicit"
    message = "Pinned catalog packages; native foreign SDKs unsupported."

    def row(self, entry: dict) -> tuple[dict, dict]:
        app = apps.match(["hermes:" + entry["id"].removeprefix("hermes:")] + apps.repository_refs(entry["url"]))
        row = _available("plugin", entry["id"], entry["name"], app=app, source="hermes", description=entry["description"],
            publisher=entry["publisher"], source_url=public_url(entry["url"]), version=entry["version"], pin=entry["pin"],
            compatibility=entry["compatibility"], platforms=entry["platforms"], actions=["preview"],
            unsupported=entry["reason"] if entry["compatibility"] == "unsupported" else "",
            canonical_identity="plugin:" + entry["source_identity"] + "@" + entry["pin"])
        if entry["compatibility"] != "unsupported":
            row["blockers"].append(facts.blocker("note", entry["reason"]))
        return row, {"kind": "plugin", "reference": entry["id"], "pin": entry["pin"], "identity": entry["source_identity"]}

    def search(self, search: Search) -> Found:
        from row_bot.plugins import hermes_catalog
        catalog = hermes_catalog.read_catalog(refresh=search.refresh, cancelled=search.cancelled)
        found = Found()
        for entry in catalog["entries"]:
            app = apps.match(["hermes:" + entry["id"].removeprefix("hermes:")])
            if matches(search.query, entry["name"], entry["description"], apps.text(app)):
                found.add(*self.row(entry))
        found.statuses.append(self.status(status=catalog["status"], message=catalog["message"], fetched_at=catalog["fetched_at"] or None))
        return found

    def lookup(self, reference: str) -> dict | None:
        from row_bot.plugins import hermes_catalog
        return next((e for e in hermes_catalog.read_catalog()["entries"] if e["id"] == reference), None)

    def update(self, cancelled: Callable[[], bool]) -> dict:
        from row_bot.plugins import hermes_catalog
        catalog = hermes_catalog.read_catalog(refresh=True, cancelled=cancelled)
        if catalog["status"] != "live":
            raise ValueError("source_unavailable")
        return {"entries": len(catalog["entries"])}


class Native(Source):
    id, kind, label = "native", "plugin", "Row-Bot marketplace"
    message = "Saved Row-Bot marketplace; refresh through its reviewed lifecycle action."

    def search(self, search: Search) -> Found:
        from row_bot.application import plugin_commands
        found, cursor = Found(), None
        while True:
            page = plugin_commands.read_plugin_catalog(query=search.query, source="marketplace", cursor=cursor, limit=50,
                                                       validate=search.validate)
            for row in page["items"]:
                found.add(_available("plugin", row["plugin_id"], row["name"], app=None, source=self.id, description=row["description"],
                    version=row["version"], compatibility="not_inspected",
                    actions=["install"] if row["capabilities"]["install"]["available"] else []),
                    {"kind": "native", "plugin_id": row["plugin_id"]})
            cursor = page.get("next_cursor")
            if not cursor:
                break
        found.statuses.append(self.status(status="cached"))
        return found


_EXAMPLES = (
    ("Local text tools", "bundled:local-text-tools", "Two no-auth writing skills, a supporting checklist, and local text statistics over MCP.",
     "MIT; shipped version 1.0.0 and reviewed tree digest. Managed Node is optional for the MCP child. Windows/macOS/Linux recipe; only Windows checked locally. No network, file reads or telemetry in the server. Skill prompts use the conversation's chosen model."),
    ("Hello Tool (native example)", "https://github.com/siddsachar/row-bot/tree/9435afbc930799ec30a622a2eb3d234a05214f31/examples/plugins/hello-tool",
     "Existing native Row-Bot plugin demonstrating a minimal local tool.",
     "MIT; pinned repository example 0.1.0. Requires Row-Bot's private Python worker environment; no third-party dependencies or telemetry. Windows/macOS/Linux fixture coverage; clean-machine checks pending."),
)


class Examples(Source):
    id, kind, label, eligibility = "examples", "plugin", "Examples", "explicit_only"
    message = "Developer fixtures, available only by explicit source selection or import."

    def row(self, name: str, reference: str, description: str, evidence: str) -> tuple[dict, dict]:
        app = apps.match(["bundled:" + reference.removeprefix("bundled:")] if reference.startswith("bundled:") else [])
        row = _available("plugin", reference, name, app=app, source=self.id, publisher="Row-Bot", license="MIT",
            description=description, evidence="Source reviewed; see setup details and validation limits.", actions=["preview"])
        row["blockers"].append(facts.blocker("note", evidence))
        return row, {"kind": "plugin", "reference": reference}

    def search(self, search: Search) -> Found:
        found = Found()
        for example in _EXAMPLES:
            if matches(search.query, example[0], example[2]):
                found.add(*self.row(*example))
        found.statuses.append(self.status(status="cached"))
        return found

    def lookup(self, reference: str):
        return next((example for example in _EXAMPLES if example[1] == reference), None)


class Skills(Source):
    kind, access, network = "skill", "public", "explicit"

    def __init__(self, source_id: str, label: str, message: str) -> None:
        self.id, self.label, self.message = source_id, label, message

    def search(self, search: Search) -> Found:
        from row_bot.application import client_skill_hub
        result = client_skill_hub.search_public_skills(owner_id=search.owner_id, query=search.query, source=self.id,
            refresh=search.refresh, cached_only=not search.refresh, limit=96, cancelled=search.cancelled)
        found = Found()
        for entry in result["entries"]:
            found.add(_available("skill", entry["id"], entry["name"], app=None, installed=entry["installed"], source=entry["source"],
                description=entry["description"], publisher=entry["author"], source_url=public_url(entry.get("url")),
                compatibility="not_inspected", actions=["preview"]),
                {"kind": "skill", "revision": result["revision"], "entry_id": entry["id"]})
        found.statuses += [self.status(status=str(s["status"]), message=str(s.get("message", "")), fetched_at=s.get("fetched_at"),
                                       truncated=result.get("has_more", False)) for s in result.get("source_statuses", [])]
        return found

    def update(self, cancelled: Callable[[], bool]) -> dict:
        from row_bot.skills_hub.source_registry import default_registry
        result = default_registry().refresh(self.id, cancelled=cancelled)
        if result.status not in {"live", "partial"} or not result.entries:
            raise ValueError("source_unavailable")
        return {"entries": len(result.entries)}


class Unavailable(Source):
    access = "unavailable"

    def __init__(self, source_id: str, kind: str, label: str, eligibility: str, message: str) -> None:
        self.id, self.kind, self.label, self.eligibility, self.message = source_id, kind, label, eligibility, message


# Public contracts checked 2026-10-03; evidence in docs/INTEGRATION_SOURCES.md. Order is ranking precedence.
SOURCES: dict[str, Source] = {source.id: source for source in (
    Curated(), Registry(), HermesMcp(),
    Skills("clawhub", "ClawHub", "Public v1 skill search and complete version downloads."),
    Skills("github", "GitHub", "Maintainer skill repositories through the existing GitHub owner."),
    Hermes(), Native(), Examples(),
    Unavailable("skills_sh", "skill", "skills.sh", "auth_required", "Documented v1 needs Vercel OIDC; desktop access is not implemented."),
    Unavailable("browse_sh", "skill", "browse.sh", "contract_unresolved", "A supported public discovery contract has not been established."),
    Unavailable("lobehub", "skill", "LobeHub", "contract_unresolved", "Agent prompt conversion is an explicit import, not an Agent Skills catalog."),
    Unavailable("glama", "mcp", "Glama", "auth_required", "Directory key, data license, visible Glama credit and listing backlinks require a separate integration."),
    Unavailable("pulsemcp", "mcp", "PulseMCP", "auth_required", "B2B tenant and API key integration is not implemented."),
    Unavailable("smithery", "mcp", "Smithery", "contract_unresolved", "Documented bearer authentication and anonymous access differ; desktop access unresolved."),
    Unavailable("clawhub_plugins", "plugin", "ClawHub plugins", "unsupported", "Bundle labels do not establish Agent Plugins 1.0 compatibility; native SDKs unsupported."),
)}


def catalog_entry(item_id: str) -> tuple[dict, dict] | None:
    """A catalog entry by its stable id from local data only; never contacts a source."""
    kind, _, reference = item_id.partition(":")
    source_id = reference.partition(":")[0]
    found = None
    if kind == "mcp" and source_id in {"curated", "official"}:
        adapter = SOURCES["recommended" if source_id == "curated" else "official"]
        entry = adapter.lookup(reference)
        found = adapter.row(entry) if entry else None
    elif kind == "plugin" and reference.startswith("hermes:"):
        entry = SOURCES["hermes"].lookup(reference)
        found = SOURCES["hermes"].row(entry) if entry else None
    elif kind == "plugin":
        example = SOURCES["examples"].lookup(reference)
        found = SOURCES["examples"].row(*example) if example else None
    return (facts.finish(found[0]), found[1]) if found else None


def _day(value: str) -> int:
    """Seconds since the epoch for a Registry ``YYYY-MM-DD`` date, or 0."""
    from row_bot.integrations.index import epoch
    return epoch(value)


def order(*, exact: bool, preferred: bool, strong: bool, featured_rank: int | None, setup: int, updated: float,
          popularity: int, precedence: int, name: str, ident: str, now: float | None = None) -> tuple:
    """The one ranking key, inside the Registry index and across sources: an exact name, then
    featured or vendor-verified, a strong text hit, featured order, known authentication with an
    installable plan, freshness (90 days, a year), source popularity, and stable ties."""
    age = (time.time() if now is None else now) - updated if updated else None
    fresh = 0 if age is None else 2 if age <= 90 * 86400 else 1 if age <= 365 * 86400 else 0
    return (not exact, not preferred, not strong, featured_rank or 1_000_000, setup, -fresh, -popularity, precedence,
            name.casefold(), ident)


def rank(rows: list[dict], query: str) -> list[dict]:
    """Merge rows that share any source-neutral identity, keeping every attribution; then one order."""
    sources, words, wanted = list(SOURCES), tokens(query), query.casefold().strip()
    known = apps.catalog()[0]

    def precedence(row: dict) -> int:
        source = (row["attributions"] or [{"source": row["source"]}])[0]["source"]
        return sources.index(source) if source in sources else len(sources)

    def key(row: dict) -> tuple:
        app = row["app"] or {}
        strong = " ".join([row["name"], row["publisher"], apps.text(known.get(app.get("id", "")))]).casefold()
        return order(exact=bool(wanted) and wanted in {row["name"].casefold(), app.get("name", "").casefold()},
                     preferred=app.get("featured_rank") is not None or row["verified"],
                     strong=all(word in strong for word in words), featured_rank=app.get("featured_rank"),
                     setup=row["setup_tier"], updated=row["updated_at"], popularity=row["popularity"],
                     precedence=precedence(row), name=row["name"], ident=row["id"])
    merged: dict[str, dict] = {}
    shown: list[dict] = []
    # The most reviewed source supplies the merged record; then the official, then the most used copy.
    for row in sorted(rows, key=lambda row: (precedence(row), not (row["signals"] or {}).get("official"),
                                             -row["popularity"], row["id"])):
        keys = [identity for identity in [row["canonical_identity"], *row["identities"]] if identity] or [row["id"]]
        primary = next((merged[identity] for identity in keys if identity in merged), None)
        if primary is None:
            primary = row
            shown.append(row)
        else:
            primary["attributions"].extend(a for a in row["attributions"] if a not in primary["attributions"])
            if row["verified"] and primary["app"]:
                primary["verified"] = primary["app"]["verified"] = True
        for identity in keys:
            merged.setdefault(identity, primary)
    return sorted(shown, key=key)


def describe(entry) -> dict:
    """A disabled, review-required MCP configuration for a catalog entry."""
    from row_bot.mcp_client.marketplace import entry_to_server_config
    name = re.sub(r"[^A-Za-z0-9_. -]", "-", entry.name).strip()[:64] or "Connection"
    return {"name": name, "import_json": json.dumps({"mcpServers": {name: entry_to_server_config(entry)}}),
            "requires_auth": entry.requires_auth, "auth_requirement": "required" if entry.requires_auth else "unknown",
            "notes": entry.notes[:16], "source_url": public_url(entry.url)}
