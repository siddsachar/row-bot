"""MCP catalog records: the curated starter list and Registry v0.1 metadata."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from row_bot.mcp_client.conflicts import conflicts_for_entry
from row_bot.mcp_client.logging import log_event

CATALOG_PATH = Path(__file__).with_name("recommended_servers.json")
DEFAULT_TIMEOUT = 3


@dataclass
class MarketplaceEntry:
    id: str
    name: str
    description: str
    source: str
    url: str = ""
    publisher: str = ""
    classification: str = ""
    transport: str = ""
    requires_auth: bool = False
    install: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    category: str = ""
    trust_tier: str = ""
    risk_level: str = ""
    action_scope: str = ""
    capabilities: list[str] = field(default_factory=list)
    overlaps_native: list[str] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    recommended: bool = False
    last_reviewed: str = ""
    notes: list[str] = field(default_factory=list)


def _entry_from_mapping(item: dict[str, Any]) -> MarketplaceEntry | None:
    try:
        allowed = set(MarketplaceEntry.__dataclass_fields__)
        values = {key: value for key, value in item.items() if key in allowed}
        return MarketplaceEntry(**values)
    except Exception as exc:
        log_event("mcp.catalog.entry_invalid", level=30, error=str(exc))
        return None


def _load_curated_catalog() -> list[MarketplaceEntry]:
    try:
        with open(CATALOG_PATH, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception as exc:
        log_event("mcp.catalog.load_failed", level=30, path=str(CATALOG_PATH), error=str(exc))
        return []
    if not isinstance(raw, list):
        log_event("mcp.catalog.invalid", level=30, path=str(CATALOG_PATH), error="catalog root must be a list")
        return []
    entries = [_entry_from_mapping(item) for item in raw if isinstance(item, dict)]
    return [entry for entry in entries if entry is not None]


CURATED_STARTER_CATALOG: list[MarketplaceEntry] = _load_curated_catalog()


def _fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT) -> Any:
    from row_bot.integrations.safe import fetch
    return json.loads(fetch(url, hosts={"registry.modelcontextprotocol.io"}, max_bytes=2 * 1024 * 1024, timeout=timeout,
        refused="registry_source_not_supported", too_large="registry_response_too_large"))


def _registry_setup_digest(item: dict) -> str:
    # Bind all delivery declarations, including unknown extensions. Display-only
    # metadata does not identify a deployment. Never persist raw declaration values:
    # even a public catalog may accidentally contain a secret header/default.
    display = {"$schema", "name", "version", "title", "description", "repository", "websiteUrl", "icons"}
    setup = {key: value for key, value in item.items() if key not in display}
    try:
        encoded = json.dumps(setup, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid_registry_setup") from exc
    if len(encoded) > 65536:
        raise ValueError("registry_setup_too_large")
    return hashlib.sha256(encoded).hexdigest()


def registry_entries(data: dict) -> list[MarketplaceEntry]:
    """Parse bounded v0.1 metadata; unsupported declarations never become recipes."""
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise ValueError("invalid_registry_response")
    entries = []
    known = {"$schema", "name", "version", "title", "description", "repository", "websiteUrl", "icons", "remotes", "packages"}
    for envelope in data["servers"][:1000]:
        if not isinstance(envelope, dict) or not isinstance(envelope.get("server"), dict):
            continue
        item = envelope["server"]
        official = envelope.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
        name, version = item.get("name"), item.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or not name or len(name) > 200 or len(version) > 128:
            continue
        setup_digest = _registry_setup_digest(item)
        status = official.get("status", "unknown")
        install, notes, requires_auth = None, [], False
        remotes, packages = item.get("remotes", []), item.get("packages", [])
        if not isinstance(remotes, list) or not isinstance(packages, list) or len(remotes) > 16 or len(packages) > 16:
            raise ValueError("invalid_registry_declarations")
        if status != "active":
            notes.append("Registry status: " + str(status)[:80] + ". New installation is unavailable.")
        elif set(item) - known:
            notes.append("Additional server setup or authentication declarations are unsupported by catalog import.")
        else:
            for remote in remotes:
                if not isinstance(remote, dict):
                    continue
                if remote.get("headers") or set(remote) - {"type", "url", "headers"}:
                    notes.append("Remote header, authentication or variable declarations require setup that catalog import cannot safely express.")
                    requires_auth = requires_auth or bool(remote.get("headers"))
                    continue
                url = str(remote.get("url", ""))
                parsed = urllib.parse.urlsplit(url)
                if (remote.get("type") not in {"streamable-http", "sse"} or parsed.scheme != "https"
                        or not parsed.hostname or parsed.username or parsed.password or any(c in url for c in "{}")):
                    continue
                install = {"transport": remote["type"].replace("-", "_"), "url": url}
                requires_auth = False
                break
            if install is None:
                for package in packages:
                    if not isinstance(package, dict):
                        continue
                    allowed = {"registryType", "identifier", "version", "transport", "registryBaseUrl", "runtimeHint",
                               "runtimeArguments", "packageArguments", "environmentVariables"}
                    if (set(package) - allowed or package.get("environmentVariables") or package.get("runtimeArguments")
                            or package.get("packageArguments") or package.get("runtimeHint", "npx") != "npx"
                            or package.get("registryBaseUrl", "https://registry.npmjs.org").rstrip("/") != "https://registry.npmjs.org"
                            or package.get("transport") != {"type": "stdio"}):
                        notes.append("Package environment, runtime, argument, integrity or registry declarations are unsupported by catalog import.")
                        requires_auth = requires_auth or bool(package.get("environmentVariables"))
                        continue
                    identifier, package_version = package.get("identifier", ""), package.get("version", "")
                    if (package.get("registryType") != "npm" or not isinstance(identifier, str) or not isinstance(package_version, str)
                            or not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+", identifier)
                            or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", package_version)):
                        continue
                    install = {"transport": "stdio", "command": "npx", "args": [identifier + "@" + package_version]}
                    requires_auth = False
                    notes.append("Prepare reviewed dependencies first. Only self-contained npm archives or complete npm shrinkwraps without install scripts are supported.")
                    break
        repository = item.get("repository", {})
        entries.append(MarketplaceEntry(id=name + "@" + version, name=str(item.get("title") or name)[:128],
            description=str(item.get("description", ""))[:800], source="official", publisher=name.split("/", 1)[0],
            url=str(repository.get("url", "")) if isinstance(repository, dict) else "", classification="official-registry",
            transport=install.get("transport", "") if install else "", requires_auth=requires_auth,
            install=install, notes=list(dict.fromkeys(notes)), metadata={"version": version, "status": status,
                "canonical_name": name, "setup_digest": setup_digest}))
    return entries


def entry_to_server_config(entry: MarketplaceEntry) -> dict[str, Any]:
    """Return a disabled, review-required server config template."""
    if entry.source == "official" and (not entry.install or not (entry.metadata or {}).get("setup_digest")):
        raise ValueError("registry_recipe_unsupported")
    install = dict(entry.install or {})
    conflicts = [conflict.as_dict() for conflict in conflicts_for_entry(entry)]
    return {
        "enabled": False,
        "environment_mode": "minimal",
        "transport": install.get("transport") or entry.transport or "stdio",
        "command": install.get("command", ""),
        "args": install.get("args", []),
        "url": install.get("url", ""),
        "headers": install.get("headers", {}),
        "env": install.get("env", {}),
        "requirements": list(entry.requirements or []),
        "trust_level": entry.trust_tier or "standard",
        "source": {
            "marketplace": entry.source,
            "id": entry.id,
            "name": entry.name,
            "url": entry.url,
            "publisher": entry.publisher,
            "classification": entry.classification,
            "category": entry.category,
            "trust_tier": entry.trust_tier,
            "risk_level": entry.risk_level,
            "action_scope": entry.action_scope,
            "requires_auth": entry.requires_auth,
            **{key: (entry.metadata or {})[key] for key in ("auth_mode", "auth_bindings", "account_requirements", "cost", "evidence") if key in (entry.metadata or {})},
            "recommended": entry.recommended,
            "capabilities": list(entry.capabilities or []),
            "overlaps_native": list(entry.overlaps_native or []),
            "requirements": list(entry.requirements or []),
            "conflicts": conflicts,
            "not_verified_by_row_bot": True,
            **({"registry_name": (entry.metadata or {}).get("canonical_name", ""),
                "registry_version": (entry.metadata or {}).get("version", ""),
                "registry_setup_digest": (entry.metadata or {}).get("setup_digest", "")}
               if entry.source == "official" else {}),
        },
    }
