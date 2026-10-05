"""App identities: the services people recognise, matched from catalog and owner records.

Records attach to an app only through a reviewed reference in ``apps.json``:
a curated recipe id, a Registry namespace or exact server, an endpoint host, a
package name, a package repository or a first-party account or channel. Anything
else stays browsable as a community entry. Whether an attached record comes from
the vendor is decided by rule (``verified``), never by its name.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

CATEGORIES = ("productivity", "developer", "data", "design", "communication", "finance", "local_tools")
VARIANTS = ("hosted_mcp", "local_mcp", "package", "account", "channel", "api_key_tool", "broker")
AUTH = ("", "oauth", "api_key", "none", "account", "mixed")
_DOCKER_FLAGS = {"-i", "-t", "-it", "-d", "--rm", "--interactive", "--tty", "--detach", "--init", "--privileged", "--read-only"}
_REF = re.compile(r"(curated|registry|endpoint|npm|pypi|oci|repo|hermes|bundled|account|channel|tool):[a-z0-9@._/+-]{1,200}")
_DOMAIN = re.compile(r"(?=.{3,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_PATH = Path(__file__).with_name("apps.json")
# Hosting domains whose subdomains belong to anyone; never a vendor domain for the badge.
SHARED_HOSTING = ("netlify.app", "vercel.app", "github.io", "herokuapp.com", "pages.dev", "workers.dev", "web.app",
                  "firebaseapp.com", "appspot.com", "run.app", "azurewebsites.net", "cloudfront.net", "amazonaws.com",
                  "onrender.com", "fly.dev", "railway.app", "ngrok.app", "ngrok.io", "replit.app", "supabase.co")


@dataclass(frozen=True)
class App:
    id: str
    name: str
    publisher: str
    category: str
    summary: str
    refs: tuple[str, ...]
    variants: tuple[str, ...]
    synonyms: tuple[str, ...] = ()
    jobs: tuple[str, ...] = ()
    example_prompts: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    github_orgs: tuple[str, ...] = ()
    auth: str = ""
    docs_url: str = ""
    key_url: str = ""
    icon: str = ""
    featured_rank: int | None = None
    local_app: str = ""
    local_check: tuple = ()  # (("port", 9876),) or (("process", "blender"),): how to tell the app is open.
    checked: str = ""
    sources: tuple[str, ...] = ()

    def ref(self, *, verified: bool = False) -> dict:
        """The small public identity carried by every entry of this app."""
        return {"id": self.id, "name": self.name, "publisher": self.publisher, "category": self.category,
                "icon": self.icon or letter(self.name), "verified": verified,
                "featured_rank": self.featured_rank}

    def view(self) -> dict:
        """Everything the Apps screens show about the app itself."""
        from row_bot.integrations.icons import license_of
        return {**self.ref(), "summary": self.summary, "jobs": list(self.jobs), "example_prompts": list(self.example_prompts),
                "variants": list(self.variants), "auth": self.auth, "docs_url": self.docs_url, "key_url": self.key_url,
                "icon_license": license_of(self.icon)}


def _local_check(check: dict) -> bool:
    """A check that only ever looks at this computer: a loopback port above 1023, or an exact process name."""
    if not check:
        return True
    if set(check) == {"port"}:
        return type(check["port"]) is int and 1024 <= check["port"] <= 65535
    return set(check) == {"process"} and bool(re.fullmatch(r"[a-z0-9._-]{1,64}", str(check["process"])))


def letter(name: str) -> str:
    """A letter-avatar icon id for anything without a mark."""
    found = re.search(r"[A-Za-z0-9]", name or "")
    return "letter:" + (found.group(0).upper() if found else "A")


def _app(row: dict) -> App:
    vendor, facts = row.pop("vendor", {}) or {}, row.pop("facts", {}) or {}
    tuples = {key: tuple(row.get(key, ())) for key in ("refs", "variants", "synonyms", "jobs", "example_prompts")}
    return App(**{**row, **tuples, "domains": tuple(vendor.get("domains", ())), "github_orgs": tuple(vendor.get("github_orgs", ())),
                  "checked": facts.get("checked", ""), "sources": tuple(facts.get("sources", ())),
                  "local_check": tuple(sorted((row.get("local_check") or {}).items()))})


@cache
def catalog() -> tuple[dict[str, App], dict[str, str]]:
    """Every app by id, and the reviewed reference index (reference -> app id)."""
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 2:
        raise ValueError("invalid_app_catalog")
    apps, index, ranks = {}, {}, set()
    for row in raw["apps"]:
        app = _app(dict(row))
        links = [link for link in (app.docs_url, app.key_url, *app.sources) if link]
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", app.id) or app.id in apps or app.category not in CATEGORIES
                or set(app.variants) - set(VARIANTS) or not all(_REF.fullmatch(ref) for ref in app.refs)
                or app.auth not in AUTH or not all(_DOMAIN.fullmatch(d) for d in app.domains)
                or any(_within(domain, SHARED_HOSTING) for domain in app.domains)
                or not all(re.fullmatch(r"[A-Za-z0-9-]{1,39}", org) for org in app.github_orgs)
                or not all(link.startswith("https://") and len(link) <= 512 for link in links)
                or (app.icon and not re.fullmatch(r"si:[a-z0-9]{1,64}", app.icon))
                or (app.featured_rank is not None and (app.featured_rank < 1 or app.featured_rank in ranks))
                or not _local_check(dict(app.local_check))):
            raise ValueError("invalid_app_catalog: " + app.id)
        ranks.add(app.featured_rank)
        apps[app.id] = app
        for ref in app.refs:
            if index.setdefault(ref, app.id) != app.id:
                raise ValueError("ambiguous_app_reference: " + ref)
    return apps, index


@cache
def digest() -> str:
    """Changes whenever attachment or verification rules change, so derived indexes rebuild."""
    return hashlib.sha256(_PATH.read_bytes()).hexdigest()[:16]


def match(refs: list[str]) -> App | None:
    """The first app any reviewed reference names; otherwise a community entry."""
    apps, index = catalog()
    return next((apps[index[ref]] for ref in refs if ref in index), None)


def _within(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def verified(app: App | None, refs: list[str]) -> bool:
    """A record published by the app's vendor, by rule only: its Registry namespace is a
    vendor domain reversed or ``io.github.<vendor org>``, or its endpoint is a vendor host
    (under a vendor domain, or an endpoint the vendor's documentation names exactly).
    The Registry itself checks DNS and GitHub ownership of namespaces, so Registry records
    are judged by their namespace alone (callers pass only those refs); the endpoint rule
    is for reviewed recipes and the user's own configurations."""
    if app is None:
        return False
    orgs = {org.lower() for org in app.github_orgs}
    for ref in refs:
        kind, _, value = ref.partition(":")
        if kind == "registry":
            namespace = value.split("/", 1)[0]
            if namespace.startswith("io.github."):
                if namespace.removeprefix("io.github.") in orgs:
                    return True
            elif _within(".".join(reversed(namespace.split("."))), app.domains):
                return True
        elif kind == "endpoint" and (ref in app.refs or _within(value, app.domains)):
            return True
    return False


def recipe_refs(install: dict | None) -> list[str]:
    """Source-neutral references for an MCP launch recipe or saved configuration."""
    install = install or {}
    host = urlsplit(str(install.get("url") or "")).hostname
    if host:
        return ["endpoint:" + host.lower()]
    command = Path(str(install.get("command") or "")).name.lower().removesuffix(".exe").removesuffix(".cmd")
    kind = {"npx": "npm", "uvx": "pypi", "docker": "oci"}.get(command)
    args = [str(arg) for arg in install.get("args") or []]
    if kind == "oci":
        args, skip = args[1:] if args[:1] == ["run"] else [], False
        for index, arg in enumerate(args):
            if skip or arg.startswith("-"):
                skip = not skip and arg.startswith("-") and "=" not in arg and arg not in _DOCKER_FLAGS
                continue
            args = args[index:index + 1]
            break
        else:
            args = []
    else:
        args = [arg for arg in args if not arg.startswith("-")]
    if not kind or not args:
        return []
    name = args[0].lower()
    name = name.rsplit(":", 1)[0] if kind == "oci" else re.sub(r"(?<=.)@[^/]*$", "", name)
    return [kind + ":" + name]


def registry_refs(name: str) -> list[str]:
    """A Registry name ``namespace/server`` attaches by its exact name, then its namespace."""
    return ["registry:" + name.lower(), "registry:" + name.split("/", 1)[0].lower()] if "/" in name else []


def publisher_of(namespace: str) -> str:
    """Who published a Registry record, read from its namespace: a GitHub account or a domain."""
    parts = [part for part in namespace.split(".") if part]
    if [part.lower() for part in parts[:2]] == ["io", "github"] and len(parts) > 2:
        return ".".join(parts[2:]) + " on GitHub"
    return ".".join(reversed(parts))


def repository_refs(source_identity: str) -> list[str]:
    """A package's pinned source repository (GitHub) or bundled example."""
    if source_identity.startswith("row-bot:"):
        return ["bundled:" + source_identity.removeprefix("row-bot:")]
    parts = urlsplit(source_identity.split("#", 1)[0])
    path = parts.path.strip("/").removesuffix(".git").lower()
    return ["repo:" + parts.hostname.lower() + "/" + path] if parts.hostname and path else []


def text(app: App | None) -> str:
    """Words that find an app by name, publisher, synonym or job."""
    return " ".join([app.name, app.publisher, app.summary, *app.synonyms, *app.jobs]) if app else ""
