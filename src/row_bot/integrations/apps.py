"""App identities: the services people recognise, matched from catalog and owner records.

Records attach to an app only through a reviewed reference in ``apps.json``:
a curated recipe id, a Registry namespace, an endpoint host, a package name,
a package repository or a first-party account or channel. Anything else stays
browsable as a community entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

CATEGORIES = ("productivity", "developer", "data", "design", "communication", "finance", "local_tools")
VARIANTS = ("hosted_mcp", "local_mcp", "package", "account", "channel", "api_key_tool", "broker")
_DOCKER_FLAGS = {"-i", "-t", "-it", "-d", "--rm", "--interactive", "--tty", "--detach", "--init", "--privileged", "--read-only"}
_REF = re.compile(r"(curated|registry|endpoint|npm|pypi|oci|repo|hermes|bundled|account|channel):[a-z0-9@._/+-]{1,200}")


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
    example_prompts: tuple[str, ...] = ()
    docs_url: str = ""
    key_url: str = ""
    icon: str = ""
    featured_rank: int | None = None
    placeholder: bool = False
    local_app: str = ""

    def ref(self) -> dict:
        """The small public identity carried by every entry of this app."""
        return {"id": self.id, "name": self.name, "publisher": self.publisher, "category": self.category,
                "icon": self.icon, "verified": True, "placeholder": self.placeholder}


@cache
def catalog() -> tuple[dict[str, App], dict[str, str]]:
    """Every app by id, and the reviewed reference index (reference -> app id)."""
    raw = json.loads(Path(__file__).with_name("apps.json").read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("invalid_app_catalog")
    apps, index = {}, {}
    for row in raw["apps"]:
        app = App(**{**row, **{key: tuple(row.get(key, ())) for key in ("refs", "variants", "synonyms", "example_prompts")}})
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", app.id) or app.id in apps or app.category not in CATEGORIES
                or set(app.variants) - set(VARIANTS) or not all(_REF.fullmatch(ref) for ref in app.refs)):
            raise ValueError("invalid_app_catalog: " + app.id)
        apps[app.id] = app
        for ref in app.refs:
            if index.setdefault(ref, app.id) != app.id:
                raise ValueError("ambiguous_app_reference: " + ref)
    return apps, index


def match(refs: list[str]) -> App | None:
    """The first app any reviewed reference names; otherwise a community entry."""
    apps, index = catalog()
    return next((apps[index[ref]] for ref in refs if ref in index), None)


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
    """A Registry name ``namespace/server`` attaches by its verified namespace."""
    return ["registry:" + name.split("/", 1)[0].lower()] if "/" in name else []


def repository_refs(source_identity: str) -> list[str]:
    """A package's pinned source repository (GitHub) or bundled example."""
    if source_identity.startswith("row-bot:"):
        return ["bundled:" + source_identity.removeprefix("row-bot:")]
    parts = urlsplit(source_identity.split("#", 1)[0])
    path = parts.path.strip("/").removesuffix(".git").lower()
    return ["repo:" + parts.hostname.lower() + "/" + path] if parts.hostname and path else []


def text(app: App | None) -> str:
    """Words that find an app by name, publisher, synonym or job."""
    return " ".join([app.name, app.publisher, app.summary, *app.synonyms]) if app else ""
