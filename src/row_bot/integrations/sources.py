"""Catalog sources. Each source is one small adapter; the list is served to clients.

Searching is passive (local data and saved results) unless a request is an
explicit online search or catalog refresh. Eligibility is decided here, on the
server; availability in a catalog never grants runtime authority.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlsplit

from row_bot.integrations import apps, facts, icons, index
from row_bot.integrations.safe import public_url


def tokens(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.casefold())
    return [word for word in words if len(word) > 1] or words


# UTF-8 read as Windows-1252: a lead byte character followed by a continuation character.
_MOJIBAKE = re.compile("[\u00c2-\u00f4][\u00a0-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u0192"
                       "\u02c6\u02dc\u2013\u2014\u2018-\u201e\u2020-\u2022\u2026\u2030\u2039\u203a\u20ac\u2122]")


def plain_text(value: object, limit: int = 600) -> str:
    """One readable line from catalog text: frontmatter, HTML and Markdown removed, mojibake
    repaired, replacement characters dropped, cut at a word."""
    text = str(value or "")
    if _MOJIBAKE.search(text):
        try:
            text = text.encode("cp1252").decode("utf-8")
        except UnicodeError:
            pass
    text = re.sub(r"\A\s*---\s*\n.*?\n---\s*(\n|$)", " ", text, flags=re.S)
    text = re.sub(r"<[^<>]{1,200}>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s*(#{1,6}|>|[-*+]|\d{1,3}[.)])\s+", "", text)
    text = re.sub(r"(\*\*|__|~~|`+)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    text = re.sub(r"(?<!\w)([*_])(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", text)
    text = re.sub(r"\*\*|__|~~|`", "", text)  # Markers left by a truncated upstream summary.
    text = re.sub(r"\s+\ufffd+\s+", " \u2013 ", text).replace("\ufffd", "")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:-") + "\u2026"
    return text


def skill_identities(*, content_hash: str = "", origin: str = "", description: str = "") -> list[str]:
    """Keys that prove two listings are one skill: its content hash, its upstream folder, or
    (when a catalog gives neither) a long declared description, word for word."""
    keys = ["skill:sha:" + content_hash] if content_hash else []
    keys += ["skill:" + ref for ref in apps.repository_refs(origin)] if origin else []
    words = re.sub(r"[^a-z0-9]+", " ", plain_text(description, 4096).casefold().rstrip(".\u2026")).strip()
    if len(words) >= 60:
        keys.append("skill:text:" + hashlib.sha256(words.encode("utf-8")).hexdigest()[:24])
    return keys


_WORDS = re.compile(r"[^\W_]+")


def matches(query: str, *texts: str) -> bool:
    """Every query word begins one of the entry's or its app's words, as the Registry index matches:
    "reddit" finds "Reddit tools" and "reddit-mcp", never "subreddit"."""
    words = set(_WORDS.findall(" ".join(texts).casefold()))
    return all(any(word.startswith(term) for word in words) for term in tokens(query))


@dataclass
class Search:
    owner_id: str
    query: str = ""
    refresh: bool = False
    cancelled: Callable[[], bool] = lambda: False
    validate: Callable[[], None] = lambda: None
    everything: bool = False  # Also placeholder, test and duplicate records ("Show all results").
    # Filters, applied where the results are chosen so counts stay true (None: every label).
    labels: frozenset[str] | None = None
    runs: str = "anywhere"
    no_sign_in: bool = False


@dataclass
class Found:
    rows: list[dict] = field(default_factory=list)
    statuses: list[dict] = field(default_factory=list)
    references: dict = field(default_factory=dict)
    hidden: int = 0

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

    def listed(self) -> bool:
        """Shown among the catalogs; a source that can't exist on this computer is not."""
        return True


def _available(kind: str, ref: str, name: str, *, app: apps.App | None, unsupported: str = "", verified: bool = False,
               **fields) -> dict:
    fields["icon"] = fields.get("icon") or (app.ref()["icon"] if app else apps.letter(name))
    fields["description"] = plain_text(fields.get("description", ""), 2048)
    return facts.entry(kind, ref, name, **{"installed": False, **fields}, lifecycle="available", verified=verified,
        app=app.ref(verified=verified) if app else None,
        blockers=[facts.blocker("unsupported", unsupported)] if unsupported else [])


def mcp_identity(install: dict | None, version: str = "", registry: str = "", *, own_client: bool = False) -> str:
    """Source-neutral identity of one deployment: transport and endpoint, package and version,
    or (without a recipe) the Registry record."""
    install = install or {}
    parts = urlsplit(str(install.get("url") or ""))
    if parts.hostname:
        # Fixed header or query values (a tenant, a workspace, the tools a shared endpoint serves) make another
        # deployment of the same endpoint, and so does signing in through the person's own OAuth app.
        fixed = sorted((name.lower(), value) for name, value in (install.get("headers") or {}).items() if "{" not in value)
        fixed += sorted(["?" + name, value] for name, value in parse_qsl(parts.query) if "{" not in value)
        suffix = "#" + hashlib.sha256(json.dumps(fixed).encode()).hexdigest()[:12] if fixed else ""
        suffix += "#own-client" if own_client else ""
        return f"mcp:endpoint:{install.get('transport', '')}:{parts.hostname.lower()}{parts.path.rstrip('/') or '/'}{suffix}"
    refs = apps.recipe_refs(install)
    if refs:
        return "mcp:" + refs[0] + ("@" + version if version else "")
    return "mcp:registry:" + registry + "@" + version if registry else ""


def launchable(install: dict | None) -> bool:
    """A recipe Row-Bot can run or reach: an endpoint, a command or a Registry bundle."""
    install = install or {}
    return bool(install.get("url") or install.get("command") or (install.get("bundle") or {}).get("url"))


def sign_in(install: dict | None, auth_mode: str = "", requires_auth: bool = False) -> str:
    """Whether a way to connect works without an account or key: "none", "required" or "unknown".
    A key the recipe asks for but can do without (higher rate limits, say) is not required."""
    install = install or {}
    secret = any(isinstance(item, dict) and item.get("secret") and item.get("required") for item in install.get("inputs") or [])
    if auth_mode in {"oauth", "api_key"} or requires_auth or secret:
        return "required"
    return "none" if auth_mode == "none" or (not install.get("url") and launchable(install)) else "unknown"


def shipped_signals(url: str, registry_name: str, updated: int) -> dict:
    """What the signals shipped with this release say about a listing's repository and Registry record.
    Read from the installed file only; Row-Bot never asks GitHub or the Registry for them."""
    from row_bot.mcp_client import registry_signals
    found = registry_signals.repository(facts.repository(url))
    first = registry_signals.record(registry_name)[0] if registry_name else 0
    pushed = found.pushed if found else 0
    public = {"stars": found.stars if found else None, "last_activity_at": pushed or updated or None}
    return {"pushed_at": pushed, "archived": bool(found and found.archived), "fork_parent": found.parent if found else "",
            "fork": bool(found and found.fork), "first_published": first, "licence": found.licence if found else "",
            "signals": {key: value for key, value in public.items() if value is not None} or None}


class _McpCatalog(Source):
    kind = "mcp"

    def entries(self) -> list:
        return []

    def row(self, entry) -> tuple[dict, dict]:
        metadata = entry.metadata or {}
        # A Registry record attaches and earns the badge only through its namespace, which the Registry
        # verifies; any publisher can point a record at a vendor's endpoint. Reviewed recipes use both.
        canonical = metadata.get("canonical_name", "")
        refs = (["curated:" + entry.id.lower()] + apps.recipe_refs(entry.install) if self.id == "recommended"
                else apps.registry_refs(canonical))
        app = apps.match(refs)
        name, publisher = entry.name, entry.publisher
        if canonical:  # A Registry record: its server part when it has no title, its owner as publisher.
            name = canonical.rpartition("/")[2] if name == canonical else name
            publisher = apps.publisher_of(canonical.partition("/")[0])
        supported = launchable(entry.install)
        known = metadata.get("auth_mode") in {"oauth", "api_key", "none"} or not (entry.install or {}).get("url") or bool(
            app and app.auth in {"oauth", "api_key", "none"})
        updated = index.epoch(metadata.get("updated_at", ""))
        shipped = shipped_signals(entry.url, canonical, updated)
        row = _available("mcp", entry.source + ":" + entry.id, name, app=app, source=entry.source,
            verified=apps.verified(app, refs), setup_tier=0 if supported and known else 1 if supported else 2,
            updated_at=updated, icon=icons.entry_icon(app, metadata.get("icon", ""), name),
            # A Registry record is also known by its server part ("reddit-mcp-buddy"), as the index matches it.
            keywords=[canonical.rpartition("/")[2]] if canonical else [], has_icon=bool(metadata.get("icon")),
            sign_in=sign_in(entry.install, metadata.get("auth_mode", ""), entry.requires_auth),
            signals=shipped["signals"], pushed_at=shipped["pushed_at"], archived=shipped["archived"],
            fork_parent=shipped["fork_parent"], fork=shipped["fork"], first_published=shipped["first_published"],
            identities=facts.merge_keys(entry.install, entry.url, name),
            unsupported="" if supported else "No supported launch recipe is available; use advanced configuration.",
            description=entry.description[:2048], source_url=public_url(entry.url), publisher=publisher[:160],
            compatibility="not_inspected" if supported else "unsupported", license=metadata.get("license") or shipped["licence"],
            pin=metadata.get("version_policy", ""), version=str(metadata.get("version", ""))[:128],
            auth_requirement="required" if entry.requires_auth else "unknown", auth_mode=metadata.get("auth_mode", ""),
            canonical_identity=mcp_identity(entry.install, metadata.get("version", ""), metadata.get("canonical_name", ""),
                                            own_client=metadata.get("oauth_client") == "required"),
            evidence_stage="inspected" if self.id == "recommended" else "listed",
            # Set in a reviewed recipe only once a live account worked through Row-Bot (the live harness).
            tested_with_row_bot=self.id == "recommended" and metadata.get("tested_with_row_bot") is True)
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
        return index.lookup(reference.removeprefix(self.id + ":"))

    def search(self, search: Search) -> Found:
        from row_bot.mcp_client.registry_snapshot import MAX_AGE, read_header
        try:
            results, total, current, hidden = index.search(search.query, everything=search.everything, labels=search.labels,
                                                           runs=search.runs, no_sign_in=search.no_sign_in)
        except LookupError:  # Start-up has not finished building the local mirror; show what it is built from.
            import lzma
            try:
                shipped = read_header()
            except (OSError, ValueError, EOFError, lzma.LZMAError):
                shipped = {}
            return Found(statuses=[self.status(status="pending", message="Preparing the Registry on this computer.",
                fetched_at=shipped.get("captured_at"), snapshot_version="v0.1" if shipped else "",
                snapshot_digest=shipped.get("digest", ""))])
        found = Found(hidden=hidden)
        for entry in results:
            found.add(*self.row(entry))
        fetched = max(current["captured_at"], current.get("updated_at", 0))
        found.statuses.append(self.status(status="stale" if time.time() - fetched > MAX_AGE else "cached", fetched_at=fetched,
            snapshot_version="v0.1", snapshot_digest=current.get("digest", ""), truncated=total > len(results)))
        return found

    def update(self, cancelled: Callable[[], bool]) -> dict:
        """Records changed since the mirror's watermark (all of them when it is old), merged into
        a new index generation; then new Registry icons. Failure leaves the mirror as it was."""
        from datetime import datetime, timedelta, timezone
        from row_bot.mcp_client import registry_snapshot
        current = index.ensure()
        try:
            mark = datetime.strptime(current["watermark"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
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
                                  watermark=max(current["watermark"], result["watermark"], key=registry_snapshot.instant),
                                  updated_at=time.time(), cancelled=cancelled)
        try:  # The mirror is already updated; icons are a best-effort extra.
            cached = icons.cache_remote(index.icon_urls(current), cancelled=cancelled)["cached"]
        except (OSError, ValueError):
            cached = 0
        return {"changed": len(result["entries"]) + len(result["deleted"]), "entries": current["count"], "icons": cached}


class WindowsConnectors(_McpCatalog):
    """Connectors Windows and installed apps register on this computer (Windows Insider builds with
    Experimental agentic features on); hidden everywhere else."""
    id, label, network = "windows", "Windows connectors", "explicit"
    message = "Connectors Windows and your apps add on this computer, listed when you update. Windows asks before each is used."

    @property
    def eligibility(self) -> str:  # type: ignore[override]
        from row_bot.integrations import windows_connectors
        return "eligible" if windows_connectors.locate() else "unsupported"

    def listed(self) -> bool:
        return self.eligibility == "eligible"

    def entries(self) -> list:
        from row_bot.integrations import windows_connectors
        return windows_connectors.entries()

    def row(self, entry) -> tuple[dict, dict]:
        row, reference = super().row(entry)
        row["canonical_identity"], row["identities"] = "mcp:windows:" + entry.id, []
        return row, reference

    def search(self, search: Search) -> Found:
        found = super().search(search) if self.listed() else Found()
        found.statuses.append(self.status(status="cached"))
        return found

    def update(self, cancelled: Callable[[], bool]) -> dict:
        from row_bot.integrations import windows_connectors
        return windows_connectors.update(cancelled)


class Composio(_McpCatalog):
    """A hosted broker: many apps through one Composio account. Off until the person turns it on in
    Apps › Advanced › Catalogs, after its disclosure; found by Row-Bot's own list of app names."""
    id, label, access = "composio", "Composio", "public"
    message = "A separate hosted service that connects many apps through one Composio account."

    @property
    def eligibility(self) -> str:  # type: ignore[override]
        from row_bot.integrations import brokers
        return "eligible" if brokers.on() else "explicit_only"

    def view(self) -> dict:
        from row_bot.integrations import brokers
        return {**super().view(), "opt_in": brokers.opt_in()}

    def entries(self) -> list:
        from row_bot.integrations import brokers
        return brokers.entries() if brokers.on() else []

    def row(self, entry) -> tuple[dict, dict]:
        row, reference = super().row(entry)
        row["identities"] = []
        if entry.id.endswith("-key"):  # The same endpoint with a key: another way, not the same card.
            row["canonical_identity"] += "#consumer-key"
        return row, reference

    def search(self, search: Search) -> Found:
        from row_bot.integrations import brokers
        found = Found()
        named = [name for name in brokers.app_names() if matches(search.query, name)] if search.query.strip() else []
        for entry in self.entries():
            if named or matches(search.query, entry.name, entry.description):
                row, reference = self.row(entry)
                if named:
                    row["description"] = f"Connect {named[0]} and other apps through one Composio account. " + row["description"]
                found.add(row, reference)
        found.statuses.append(self.status(status="cached" if brokers.on() else "empty"))
        return found

    def app_ways(self, app: apps.App) -> list[dict]:
        """"{App} via Composio", for an app on Row-Bot's list, while Composio is on."""
        from row_bot.integrations import brokers
        names = {name.casefold() for name in brokers.app_names()}
        if not brokers.on() or not names & {app.name.casefold(), *(s.casefold() for s in app.synonyms)}:
            return []
        row = self.row(self.entries()[0])[0]
        return [{**row, "name": f"{app.name} via Composio"}]


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
                    compatibility="not_inspected", description="Pinned Hermes optional-MCP recipe. Inspect to check compatibility."),
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
            # Only about one in ten is portable: known from its manifest after a catalog update, else checked when added.
            compatibility="unsupported" if entry["compatibility"] == "unsupported" else "not_inspected",
            setup_tier=1 if entry.get("portable") else 2,
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
        """The catalog, then what each new pin is, read from its manifest only (never run)."""
        from row_bot.plugins import hermes_catalog
        catalog = hermes_catalog.read_catalog(refresh=True, cancelled=cancelled)
        if catalog["status"] != "live":
            raise ValueError("source_unavailable")
        return {"entries": len(catalog["entries"]), **hermes_catalog.classify(cancelled=cancelled)}


class Native(Source):
    id, kind, label = "native", "plugin", "Row-Bot marketplace"
    message = "Saved Row-Bot marketplace; update catalogs to read it again."

    def row(self, plugin_id: str, name: str, description: str, version: str) -> tuple[dict, dict]:
        """A marketplace package, added from its GitHub folder and checked against the marketplace's checksum."""
        from row_bot.plugins import marketplace
        index = marketplace.get_cached_index()
        entry = marketplace.get_entry(plugin_id, index) if index else None
        folder = marketplace.github_folder(entry) if entry else ""
        return (_available("plugin", plugin_id, name, app=None, source=self.id, description=description, version=version,
                           source_url=folder, compatibility="not_inspected" if folder else "unsupported",
                           unsupported="" if folder else "Row-Bot can't check this package against the marketplace."),
                {"kind": "plugin", "reference": "marketplace:" + plugin_id})

    def lookup(self, plugin_id: str) -> tuple[dict, dict] | None:
        from row_bot.plugins import marketplace
        index = marketplace.get_cached_index()
        entry = marketplace.get_entry(plugin_id, index) if index else None
        return self.row(entry.id, entry.name, entry.description, entry.version) if entry else None

    def search(self, search: Search) -> Found:
        from row_bot.application import plugin_commands
        found, cursor = Found(), None
        while True:
            page = plugin_commands.read_plugin_catalog(query=search.query, source="marketplace", cursor=cursor, limit=50,
                                                       validate=search.validate)
            for row in page["items"]:
                if matches(search.query, row["name"], row["plugin_id"], row["description"]):  # Words, not substrings.
                    found.add(*self.row(row["plugin_id"], row["name"], row["description"], row["version"]))
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
            description=description)
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
            if not matches(search.query, entry["name"], entry["description"], entry["author"]):
                continue  # The hub's own search also finds words inside words; only whole-word starts count.
            listed = client_skill_hub.listed_entry(search.owner_id, result["revision"], entry["id"])
            meta = listed.metadata if listed is not None else {}
            signals = {key: meta[key] for key in ("downloads", "stars", "official") if meta.get(key) is not None} or None
            origin = f"https://github.com/{meta['repository']}/{meta.get('path', '')}".rstrip("/") if meta.get("repository") else ""
            found.add(_available("skill", entry["id"], plain_text(entry["name"], 160), app=None, installed=entry["installed"],
                source=entry["source"], description=entry["description"], publisher=entry["author"],
                source_url=public_url(entry.get("url")), compatibility="not_inspected", signals=signals, sign_in="none",
                popularity=(signals or {}).get("downloads") or 0, identities=skill_identities(
                    content_hash=str(meta.get("content_hash") or ""), origin=origin, description=entry["description"])),
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


@cache
def featured_skills() -> dict[str, dict]:
    """The featured library: references to pinned folders in official and maintainer repositories,
    each with the licence checked at that commit. Nothing third-party is bundled."""
    raw = json.loads(Path(__file__).with_name("skills.json").read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("invalid_skill_catalog")
    found = {}
    for row in raw["skills"]:
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", row["id"]) or row["id"] in found
                or not re.fullmatch(r"github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", row["repo"])
                or not re.fullmatch(r"[A-Za-z0-9_.@/-]{1,200}", row["path"]) or ".." in row["path"]
                or not re.fullmatch(r"[0-9a-f]{40}", row["commit"]) or not row["license"]
                or row["category"] not in apps.CATEGORIES or not row["license_url"].startswith("https://")):
            raise ValueError("invalid_skill_catalog: " + str(row.get("id")))
        found[row["id"]] = row
    return found


def featured_install_ref(skill: dict) -> str:
    """Where a featured skill is added from: its pinned folder, as the skill hub records it."""
    owner, repo = skill["repo"].split("/")[1:]
    return f"github:{owner}/{repo}/{skill['path']}?ref={skill['commit']}"


def skill_app(install_refs: list[str]) -> dict | None:
    """The app an added skill works with: the one its featured entry names, found by where it was added
    from. Presentation only; a skill never gains an app's access by it."""
    wanted = {ref for ref in install_refs if ref}
    skill = next((skill for skill in featured_skills().values() if featured_install_ref(skill) in wanted), None)
    app = apps.catalog()[0].get(skill.get("app", "")) if skill else None
    return app.ref() if app else None


class FeaturedSkills(Source):
    id, kind, label = "featured_skills", "skill", "Featured skills"
    message = "Skills from official and maintainer repositories, added from their source when you choose."

    def row(self, skill: dict, installed: set[str]) -> tuple[dict, dict]:
        install_ref = featured_install_ref(skill)
        origin = f"https://{skill['repo']}/{skill['path']}"
        app = apps.catalog()[0].get(skill.get("app", ""))
        row = _available("skill", "featured:" + skill["id"], skill["name"], app=app, source=self.id, installed=install_ref in installed,
            description=skill["summary"], publisher=skill["publisher"], license=skill["license"], version=skill["commit"][:12],
            pin=skill["commit"], source_url=f"https://{skill['repo']}/tree/{skill['commit']}/{skill['path']}",
            compatibility="not_inspected", identities=skill_identities(origin=origin), featured_rank=skill["featured_rank"],
            keywords=[*skill["synonyms"], *skill["jobs"]], sign_in="none")
        return row, {"kind": "skill", "install_ref": install_ref, "name": skill["name"], "publisher": skill["publisher"]}

    def search(self, search: Search) -> Found:
        from row_bot.skills_hub.provenance import load_records
        installed = {ref for record in load_records().values()
                     for ref in (record.install_ref, str(record.metadata.get("hub_entry_ref") or ""))}
        found = Found()
        known = apps.catalog()[0]
        for skill in sorted(featured_skills().values(), key=lambda skill: skill.get("featured_rank", 1_000)):
            app = known.get(skill.get("app", ""))
            # A skill made for an app is found by the app's name too ("GitHub" finds "PR writer").
            if matches(search.query, skill["name"], skill["summary"], skill["publisher"], *skill["synonyms"], *skill["jobs"],
                       app.name if app else ""):
                found.add(*self.row(skill, installed))
        found.statuses.append(self.status(status="cached"))
        return found

    def lookup(self, reference: str) -> dict | None:
        return featured_skills().get(reference.removeprefix("featured:"))


class Builtin(Source):
    """Row-Bot's own accounts, channels and key-based tools, read from their owners, so a job like
    "send email" finds Google and each one opens like any app."""
    id, kind, label = "builtin", "mcp", "Built in"
    message = "Ways to connect that are part of Row-Bot: accounts, channels and tools that take your key."

    def search(self, search: Search) -> Found:
        from row_bot.integrations import builtin
        found = Found()
        known = apps.catalog()[0]
        for row in builtin.rows(search.validate, wait=False):  # Never waits for the owners.
            app = known.get((row["app"] or {}).get("id", ""))
            if matches(search.query, row["name"], row["description"], apps.text(app) if app else ""):
                found.add(row, {"kind": "builtin"})
        found.statuses.append(self.status(status="cached"))
        return found


class Unavailable(Source):
    access = "unavailable"

    def __init__(self, source_id: str, kind: str, label: str, eligibility: str, message: str) -> None:
        self.id, self.kind, self.label, self.eligibility, self.message = source_id, kind, label, eligibility, message


# Public contracts checked 2026-10-03; evidence in docs/INTEGRATION_SOURCES.md. Order is ranking precedence.
SOURCES: dict[str, Source] = {source.id: source for source in (
    Curated(), Registry(), HermesMcp(), FeaturedSkills(), Builtin(), WindowsConnectors(), Composio(),
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
    if kind == "mcp" and source_id in {"curated", "official", "windows", "composio"}:
        adapter = SOURCES[{"curated": "recommended"}.get(source_id, source_id)]
        entry = adapter.lookup(reference)
        found = adapter.row(entry) if entry else None
    elif kind == "skill" and reference.startswith("featured:"):
        skill = SOURCES["featured_skills"].lookup(reference)
        found = SOURCES["featured_skills"].row(skill, set()) if skill else None
    elif kind == "plugin" and reference.startswith("hermes:"):
        entry = SOURCES["hermes"].lookup(reference)
        found = SOURCES["hermes"].row(entry) if entry else None
    elif kind == "plugin":
        example = SOURCES["examples"].lookup(reference)
        found = SOURCES["examples"].row(*example) if example else SOURCES["native"].lookup(reference)
    return (facts.finish(found[0]), found[1]) if found else None


# Search ranking. The weights are named so that tuning them is a reviewed change: the golden queries in
# tests/subsystem/integrations/test_search_ranking.py say what each query must show first.
MATCHES = ("exact", "name", "description")  # The query is a name; every word starts a name word; only elsewhere.
EXACT, NAME, DESCRIPTION = range(3)
LABELS = ("official", "reviewed", "built_in", "community")
# Built in ranks with Official: Row-Bot's own code, signing in to the vendor itself.
_LABEL_ORDER = {"official": 0, "built_in": 0, "reviewed": 1, "community": 2}
# Sources Row-Bot reviews: setup recipes (as listed, and once added), the featured skills and its own examples.
_REVIEWED = {"curated", "recommended", "featured_skills", "examples"}
_SETUP_ORDER = {"hosted_sign_in": 0, "hosted": 0, "built_in": 0, "api_key": 1, "local": 2}
_DAY = 86400
STARS = 3                      # Times log10(1 + stars): 10 stars give 3, 100 give 6, 1,000 give 9.
ACTIVE, ALIVE = 2, 1           # Pushed (or, without a repository, updated) within 90 days; within a year.
ARCHIVED = -2                  # Its repository is archived.
COMPLETE_WORDS = 12            # A description of at least this many words; a licence; an icon; a known sign-in: 1 each.
FORK = 3                       # A fork whose parent is listed too.
NEW, NEW_DAYS = 2, 30          # A first version under 30 days old that nobody has starred yet.
BEST_STANDING = 6              # Stars and activity (about 50 stars, kept up) that let a community result lead.


def label(row: dict) -> str:
    """Who stands behind a result, by rule only: the vendor (official), Row-Bot (built in, or reviewed:
    a setup recipe, a featured skill, or a way tested through Row-Bot), or anyone else (community)."""
    if row["kind"] == "builtin" or row["source"] == "bundled":
        return "built_in"
    if row["verified"]:
        return "official"
    if row["tested_with_row_bot"] or row["source"] in _REVIEWED or any(a["source"] in _REVIEWED for a in row["attributions"]):
        return "reviewed"
    return "community"


def strength(row: dict, query: str, app: apps.App | None) -> int:
    """Where a non-empty query matched: the name itself, every word in a name (the entry's, its app's,
    their synonyms and jobs), or only elsewhere (a description, a publisher)."""
    wanted = query.casefold().strip()
    if wanted in {row["name"].casefold(), app.name.casefold() if app else row["name"].casefold()}:
        return EXACT
    return NAME if matches(query, row["name"], *row["keywords"], *apps.names(app)) else DESCRIPTION


def merit(*, stars: int, words: int, licence: bool, icon: bool, sign_in_known: bool, fork: bool) -> float:
    """The part of quality that does not age: popularity, completeness and penalties (the index stores it)."""
    return (STARS * math.log10(1 + stars) + int(words >= COMPLETE_WORDS) + int(licence) + int(icon) + int(sign_in_known)
            - FORK * int(fork))


def activity(pushed: float, archived: bool, now: float) -> int:
    return (ARCHIVED if archived else 0 if not pushed else ACTIVE if now - pushed <= 90 * _DAY
            else ALIVE if now - pushed <= 365 * _DAY else 0)


def quality(merit_: float, *, stars: int, pushed: float, archived: bool, first: float, now: float) -> float:
    """Merit plus activity, less a new listing nobody has starred: the index computes the same in SQL."""
    return merit_ + activity(pushed, archived, now) - (NEW if first and now - first < NEW_DAYS * _DAY and not stars else 0)


def row_quality(row: dict, now: float) -> float:
    stars = (row["signals"] or {}).get("stars") or 0
    found = merit(stars=stars, words=len(row["description"].split()), licence=bool(row["license"]), icon=row["has_icon"],
                  sign_in_known=row["sign_in"] != "unknown", fork=row["fork"])
    return quality(found, stars=stars, pushed=row["pushed_at"] or row["updated_at"], archived=row["archived"],
                   first=row["first_published"], now=now)


def standing(row: dict, now: float) -> float:
    """Popularity and upkeep alone (stars and activity): what lets a community result lead as the best match,
    where a complete listing nobody uses should not."""
    stars = (row["signals"] or {}).get("stars") or 0
    return STARS * math.log10(1 + stars) + activity(row["pushed_at"] or row["updated_at"], row["archived"], now)


def order(*, match: int, label: str, featured_rank: int | None, quality: float, setup: int, popularity: int = 0,
          precedence: int = 0, name: str, ident: str) -> tuple:
    """The one ranking key, inside the Registry index and across sources. Relevance gates: a name match
    (exact or every word) before one found only in a description, and a community record never wins on its
    name alone. Among results Row-Bot or a vendor stands behind, an exact name comes first ("git" is Git before
    GitHub), then official and built in before reviewed, and Row-Bot's featured order. Then quality orders (a
    well-kept community server before a bare one that only shares the name), then an exact name, setup (hosted,
    a key, runs here, no recipe) and stable ties."""
    backed = label != "community"
    return (match == DESCRIPTION, not backed, match if backed else 0, _LABEL_ORDER[label], featured_rank or 1_000_000,
            -quality, match, setup, -popularity, precedence, name.casefold(), ident)


def connects(hosted: bool, auth: str) -> str:
    return "api_key" if auth == "api_key" else "local" if not hosted else "hosted_sign_in" if auth == "oauth" else "hosted"


def catalog_method(identity: str, auth: str) -> str:
    """How a catalog way connects, from its source-neutral identity and declared authentication."""
    hosted = identity.startswith("mcp:endpoint:")
    return connects(hosted, auth) if hosted or identity.startswith("mcp:registry:") else "local"


def setup_order(how: str, setup_tier: int) -> int:
    """Easiest first: a hosted way (signing in), a key, runs on this computer, no supported recipe."""
    return 3 if setup_tier == 2 else _SETUP_ORDER.get(how, 2)


def method(row: dict) -> str:
    """How an app connects, for its card: signs in, takes a key, is hosted, or runs on this computer."""
    if row["kind"] == "plugin":
        return "local"
    if row["kind"] == "builtin":
        return "built_in"
    if row["kind"] != "mcp":
        return ""
    setup = row.get("setup")
    if setup:
        return connects(setup["execution"] == "hosted", setup["auth_mode"])
    return catalog_method(row["canonical_identity"], row.get("auth_mode") or (row["app"] or {}).get("auth")
                          or ("oauth" if row["auth_requirement"] == "required" else ""))


def runs(row: dict) -> str:
    """Where a way runs: on a hosted server, or on this computer (Row-Bot's own ways, skills and packages too)."""
    setup = row.get("setup")
    hosted = setup["execution"] == "hosted" if setup else row["canonical_identity"].startswith("mcp:endpoint:")
    return "hosted" if row["kind"] == "mcp" and hosted else "local"


def wanted(labels: frozenset[str] | None, where: str, no_sign_in: bool) -> Callable[[dict], bool]:
    """The search filters as one test of a labelled row."""
    return lambda row: ((labels is None or row["label"] in labels) and (where == "anywhere" or runs(row) == where)
                        and (not no_sign_in or row["sign_in"] == "none"))


def way_order(row: dict, app: apps.App | None) -> tuple:
    """One app's ways to connect, recommended first: reviewed ways (the vendor's own, or built into
    Row-Bot) before community ones, then the app's own order (Tavily: web search, then hosted)."""
    how = method(row)
    kind = row["owner_ref"].split(":", 1)[0] if how == "built_in" else "local_mcp" if how == "local" else "hosted_mcp"
    kind = {"account": "account", "channel": "channel", "tool": "api_key_tool"}.get(kind, kind)
    variants = app.variants if app else ()
    return (row["compatibility"] == "unsupported", not (row["verified"] or how == "built_in"),
            variants.index(kind) if kind in variants else len(variants), how == "local", row["name"].casefold(), row["id"])


def best_match(rows: list[dict]) -> str | None:
    """The first result leads as the best match only when its name matched and Row-Bot or the vendor stands
    behind it, or many people use it and it is kept up (standing)."""
    first = rows[0] if rows else None
    if first is None or first.get("match") not in {"exact", "name"}:
        return None
    return first["id"] if first["label"] != "community" or first["standing"] >= BEST_STANDING else None


def rank(rows: list[dict], query: str, keep: Callable[[dict], bool] = lambda row: True, now: float | None = None) -> list[dict]:
    """Merge rows that share any source-neutral identity, keeping every attribution; label and filter them
    (``keep``); then one order, and one card per app: an app's other ways to connect are on its page (skills
    are never grouped). Each row gains its ``label``, ``match`` (None without a query) and ``quality``."""
    sources, searched = list(SOURCES), bool(tokens(query))
    known = apps.catalog()[0]
    now = time.time() if now is None else now

    def precedence(row: dict) -> int:
        source = (row["attributions"] or [{"source": row["source"]}])[0]["source"]
        return sources.index(source) if source in sources else len(sources)

    def key(row: dict) -> tuple:
        app = row["app"] or {}
        return order(match=MATCHES.index(row["match"]) if row["match"] else EXACT, label=row["label"],
                     featured_rank=row["featured_rank"] or app.get("featured_rank"), quality=row["quality"],
                     setup=setup_order(method(row), row["setup_tier"]), popularity=row["popularity"],
                     precedence=precedence(row), name=row["name"], ident=row["id"])
    def absorb(primary: dict, row: dict) -> None:
        primary["attributions"].extend(a for a in row["attributions"] if a not in primary["attributions"])
        primary["forks"] += row["forks"]
        if row["verified"] and primary["app"]:
            primary["verified"] = primary["app"]["verified"] = True

    def stars(row: dict) -> int:
        return (row["signals"] or {}).get("stars") or 0
    merged: dict[str, dict] = {}
    shown: list[dict] = []
    # The most reviewed source supplies the merged record; then the vendor's, the official, the most used copy.
    for row in sorted(rows, key=lambda row: (precedence(row), not row["verified"], not (row["signals"] or {}).get("official"),
                                             -row["popularity"], row["id"])):
        keys = [identity for identity in [row["canonical_identity"], *row["identities"]] if identity] or [row["id"]]
        primary = next((merged[identity] for identity in keys if identity in merged), None)
        if primary is None:
            primary = row
            shown.append(row)
        else:
            absorb(primary, row)
        for identity in keys:
            merged.setdefault(identity, primary)
    # Near-identical listings by one owner are one: the most reviewed source leads, then the freshest.
    kept: list[dict] = []
    for row in shown:
        at_ = next((i for i, other in enumerate(kept) if facts.near_identical(other, row)), None)
        if at_ is None:
            kept.append(row)
            continue
        other = kept[at_]
        lead, folded = ((other, row) if (precedence(other), -other["updated_at"]) <= (precedence(row), -row["updated_at"])
                        else (row, other))
        absorb(lead, folded)
        kept[at_] = lead
    # A fork folds into its parent's card, unless more people starred the fork.
    by_repository: dict[str, list[dict]] = {}
    for row in kept:
        by_repository.setdefault(facts.repository(row["source_url"]), []).append(row)
    shown = []
    for row in kept:
        parents = by_repository.get(row["fork_parent"], []) if row["fork_parent"] else []
        if len(parents) == 1 and parents[0] is not row and stars(row) <= stars(parents[0]):
            absorb(parents[0], row)
            parents[0]["forks"] += 1
        else:
            shown.append(row)
    for row in shown:
        row.update(label=label(row), quality=row_quality(row, now), standing=standing(row, now),
                   match=MATCHES[strength(row, query, known.get((row["app"] or {}).get("id", "")))] if searched else None)
    at: dict[str, int] = {}
    grouped = []
    for row in sorted(filter(keep, shown), key=key):
        app = (row["app"] or {}).get("id") if row["kind"] != "skill" else None
        if app is None:
            grouped.append(row)
        elif app not in at:
            at[app] = len(grouped)
            grouped.append(row)
        elif way_order(row, known.get(app)) < way_order(grouped[at[app]], known.get(app)):
            # The card opens the app's recommended way, where the app ranks, under the best label of its ways.
            leading = grouped[at[app]]
            row.update(match=leading["match"], quality=leading["quality"], standing=leading["standing"],
                       label=min(leading["label"], row["label"], key=_LABEL_ORDER.__getitem__))
            grouped[at[app]] = row
        else:
            leading = grouped[at[app]]
            leading["label"] = min(leading["label"], row["label"], key=_LABEL_ORDER.__getitem__)
    return grouped


def import_json(entry) -> str:
    """A disabled, review-required MCP configuration for a catalog entry, as an import."""
    from row_bot.mcp_client.marketplace import entry_to_server_config
    name = re.sub(r"[^A-Za-z0-9_. -]", "-", entry.name).strip()[:64] or "Connection"
    return json.dumps({"mcpServers": {name: entry_to_server_config(entry)}})
