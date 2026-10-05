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


_RASTER = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}


def _registry_display(item: dict, official: dict) -> dict:
    """Display-only facts: freshness and at most one declared raster icon (SVG is never kept)."""
    shown = {}
    updated = official.get("updatedAt") or official.get("publishedAt")
    if isinstance(updated, str) and re.match(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", updated):
        shown["updated_at"] = updated[:10]
    icons = item.get("icons")
    for icon in icons if isinstance(icons, list) else []:
        source = icon.get("src") if isinstance(icon, dict) else None
        if (isinstance(source, str) and source.startswith("https://") and len(source) <= 2048
                and str(icon.get("mimeType") or "image/png").lower() in _RASTER and not source.lower().endswith(".svg")):
            shown["icon"] = source
            break
    return shown


_UNSUPPORTED_PACKAGE = "Package environment, runtime, argument, integrity or registry declarations are unsupported by catalog import."
_UNSUPPORTED_REMOTE = "Remote header, authentication or variable declarations require setup that catalog import cannot safely express."
_NPM = r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+"
_SEMVER = r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?"
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}")
_VARIABLE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.-]{0,63})\}")
# Docker flags a declaration may keep: they only narrow what the container can do.
_DOCKER_KEEP = {("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges"), ("--read-only", None), ("--init", None)}


class _Declared(ValueError):
    """A declaration Row-Bot cannot express safely; the record stays listed with this reason."""


def _filled(spec: dict, *, target: str, name: str, inputs: dict, flag: str = "", carrier: str = "") -> str:
    """The template for one Registry input, adding the inputs it asks for: a fixed value stays fixed,
    ``{variables}`` in a value become inputs, and a value the person supplies becomes one input."""
    from row_bot.integrations import inputs as declared
    if not isinstance(spec, dict):
        raise _Declared(_UNSUPPORTED_PACKAGE)
    description = str(spec.get("description") or "")[:512]

    def add(key: str, item: object, label: str, implied_secret: bool) -> None:
        item = item if isinstance(item, dict) else {}
        secret = bool(item.get("isSecret")) or implied_secret
        found = declared.declaration(key, target=target, name=label, label=label, secret=secret,
            required=bool(item.get("isRequired", spec.get("isRequired"))), description=str(item.get("description") or description),
            default="" if secret else str(item.get("default") or ""), choices=[str(c) for c in item.get("choices") or []],
            format=str(item.get("format") or "string"), flag=flag)
        if key in inputs:  # The same variable twice is one value; the stricter declaration wins.
            found["secret"] = found["secret"] or inputs[key]["secret"]
            found["required"] = found["required"] or inputs[key]["required"]
            found["default"] = "" if found["secret"] else found["default"]
        inputs[key] = found
    fixed = str(spec.get("value") or "")
    if "value" in spec and not (spec.get("isSecret") and not _VARIABLE.search(fixed.replace("${", "{"))):
        # A secret written into a public listing is never copied: the person supplies their own instead.
        value = fixed.replace("${", "{")
        variables = spec.get("variables") if isinstance(spec.get("variables"), dict) else {}
        for var in dict.fromkeys(_VARIABLE.findall(value)):
            key = declared.key_of(var)
            value = value.replace("{" + var + "}", "{" + key + "}")
            implied = var not in variables and (declared.secretish(var) or declared.secretish(carrier or name)
                                                or bool(spec.get("isSecret")))
            add(key, variables.get(var, {}), var, implied)
        return value
    key = declared.key_of(name)
    add(key, spec, name, declared.secretish(name))
    if (target == "header" and name.lower() == "authorization" and not inputs[key]["choices"]
            and "bearer" in description.lower()):
        return "Bearer {" + key + "}"  # Most declarations describe the scheme but leave it to the person.
    return "{" + key + "}"


def _arguments(arguments: object, inputs: dict) -> list[str]:
    from row_bot.integrations import inputs as declared
    argv: list[str] = []
    listed = arguments if isinstance(arguments, list) else []
    # A flag with nothing but a name is a switch (``--stdio``), unless it names a secret or the package
    # lists so many that they are its settings, not switches it needs: then each is an optional input.
    bare = [a for a in listed if isinstance(a, dict) and a.get("type") == "named" and not any(
        key in a for key in ("value", "valueHint", "default", "isSecret", "isRequired", "choices", "format"))]
    for argument in listed:
        if not isinstance(argument, dict) or argument.get("type") not in {"positional", "named"}:
            raise _Declared(_UNSUPPORTED_PACKAGE)
        if argument["type"] == "positional":
            hint = str(argument.get("valueHint") or "value")
            if "value" not in argument and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", hint):
                raise _Declared(_UNSUPPORTED_PACKAGE)
            argv.append(_filled(argument, target="argument", name=hint, inputs=inputs))
            continue
        flag = str(argument.get("name") or "")
        if not re.fullmatch(r"--?[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", flag):
            raise _Declared(_UNSUPPORTED_PACKAGE)
        if argument in bare and len(bare) <= 8 and not declared.secretish(flag.lstrip("-")):
            argv.append(flag)  # A plain switch.
            continue
        argv += [flag, _filled(argument, target="argument", name=str(argument.get("valueHint") or flag.lstrip("-")),
                               inputs=inputs, flag=flag)]
    return argv


def _checked(install: dict) -> dict:
    from row_bot.integrations import inputs as declared
    try:
        found = declared.check(install.get("inputs"))
        if "{" in install.get("url", ""):
            declared.check_url(install["url"], found)
        templates = [*install.get("headers", {}).values(), *install.get("env", {}).values(), *install.get("args", [])]
        known = {item["key"] for item in found}
        if any(key not in known for text in templates for key in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]{0,63})\}", text)):
            raise declared.InputError("invalid_inputs")
    except declared.InputError as exc:
        raise _Declared("Its address is chosen when you set it up; add it from a link instead." if "url" in str(exc)
                        else _UNSUPPORTED_PACKAGE if install.get("command") else _UNSUPPORTED_REMOTE) from None
    return install


def _remote(remote: dict) -> dict:
    from row_bot.integrations import inputs as declared
    url, kind = str(remote.get("url", "")), remote.get("type")
    parsed = urllib.parse.urlsplit(url.replace("{", "x").replace("}", "x"))
    if (kind not in {"streamable-http", "sse"} or parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or set(remote) - {"type", "url", "headers", "variables"}):
        raise _Declared(_UNSUPPORTED_REMOTE)
    inputs: dict = {}
    variables = remote.get("variables") if isinstance(remote.get("variables"), dict) else {}
    for var in dict.fromkeys(_VARIABLE.findall(url)):
        key = declared.key_of(var)
        url = url.replace("{" + var + "}", "{" + key + "}")
        spec = variables.get(var) if isinstance(variables.get(var), dict) else {}
        _filled({"value": "{" + var + "}", "variables": {var: spec}, "isRequired": True},
                target="url_variable", name=var, inputs=inputs)
    headers: dict = {}
    for header in remote.get("headers") or []:
        name = str((header or {}).get("name") or "") if isinstance(header, dict) else ""
        if name.lower() == "payment-signature":
            raise _Declared("It charges for each request (x402 payments); Row-Bot can't connect to it.")
        if not _HEADER_NAME.fullmatch(name) or name.lower() in {key.lower() for key in headers}:
            raise _Declared(_UNSUPPORTED_REMOTE)
        headers[name] = _filled(header, target="header", name=name, inputs=inputs, carrier=name)
    return _checked({"transport": kind.replace("-", "_"), "url": url, **({"headers": headers} if headers else {}),
                     **({"inputs": list(inputs.values())} if inputs else {})})


def _package(package: dict) -> dict:
    from row_bot.integrations import inputs as declared
    kind, identifier, version = package.get("registryType"), str(package.get("identifier") or ""), str(package.get("version") or "")
    allowed = {"registryType", "identifier", "version", "transport", "registryBaseUrl", "runtimeHint", "runtimeArguments",
               "packageArguments", "environmentVariables", "fileSha256"}
    if set(package) - allowed or kind not in {"npm", "pypi", "oci"}:
        raise _Declared("Download this bundle from its publisher, then add it from a file." if kind == "mcpb"
                        else _UNSUPPORTED_PACKAGE)
    if package.get("transport") != {"type": "stdio"}:
        raise _Declared("It runs as a web server on this computer; Row-Bot can't start those yet.")
    default = {"npm": "https://registry.npmjs.org", "pypi": "https://pypi.org"}.get(kind, "")
    if str(package.get("registryBaseUrl") or default).rstrip("/") not in {default, "https://pypi.org/simple"}:
        raise _Declared(_UNSUPPORTED_PACKAGE)
    inputs: dict = {}
    env: dict = {}
    for variable in package.get("environmentVariables") or []:
        name = str((variable or {}).get("name") or "") if isinstance(variable, dict) else ""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) or name.upper() in declared.NEVER_ENV or name in env:
            raise _Declared(_UNSUPPORTED_PACKAGE)
        env[name] = _filled(variable, target="env", name=name, inputs=inputs, carrier=name)
    arguments = _arguments(package.get("packageArguments"), inputs)
    flags = [flag for flag in package.get("runtimeArguments") or [] if isinstance(flag, dict)]
    if len(flags) != len(package.get("runtimeArguments") or []):
        raise _Declared(_UNSUPPORTED_PACKAGE)
    if kind == "npm":
        if (not re.fullmatch(_NPM, identifier) or not re.fullmatch(_SEMVER, version)
                or any(str(flag.get("name") or flag.get("value") or "") not in {"-y", "--yes"} for flag in flags)):
            raise _Declared(_UNSUPPORTED_PACKAGE)
        install = {"transport": "stdio", "command": "npx", "args": [identifier + "@" + version, *arguments]}
    elif kind == "pypi":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", identifier) or not re.fullmatch(r"[A-Za-z0-9.!+_-]{1,64}", version):
            raise _Declared(_UNSUPPORTED_PACKAGE)
        spec, entry = identifier + "==" + version, identifier
        for flag in flags:
            word, value = str(flag.get("name") or flag.get("value") or ""), str(flag.get("value") or "")
            if word == "--from" and re.fullmatch(re.escape(identifier) + r"(\[[A-Za-z0-9,_-]+\])?(==[A-Za-z0-9.!+_-]+)?", value):
                spec = value if "==" in value else value + "==" + version  # Extras of this record's own package, pinned.
            elif (flag.get("type") == "positional" and "value" in flag and entry == identifier and spec != identifier + "==" + version
                  and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)):
                entry = value  # The console script to run from that package (``uvx --from pkg[mcp]==1.0 pkg-mcp``).
            elif word != "--python":
                raise _Declared(_UNSUPPORTED_PACKAGE)
        install = {"transport": "stdio", "command": "uvx", "args": ["--from", spec, entry, *arguments]}
    else:
        tagged = ":" in identifier.rsplit("/", 1)[-1] or "@sha256:" in identifier
        image = identifier if tagged else identifier + ":" + (version or "latest")
        if image.endswith(":latest"):
            raise _Declared("Its container image has no fixed version, so what runs could change without review.")
        if not re.fullmatch(r"[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+(:[A-Za-z0-9._-]{1,128}|@sha256:[0-9a-f]{64})", image):
            raise _Declared(_UNSUPPORTED_PACKAGE)
        kept: list[str] = []
        for flag in flags:
            word, value = str(flag.get("name") or ""), flag.get("value")
            if (flag.get("type") == "positional" and str(value) == "run") or word in {"-i", "--interactive", "--rm"}:
                continue
            if word == "-e" and isinstance(value, str) and "=" in value:
                name, _, template = value.partition("=")
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) or name.upper() in declared.NEVER_ENV:
                    raise _Declared(_UNSUPPORTED_PACKAGE)
                env[name] = _filled({**flag, "value": template}, target="env", name=name, inputs=inputs, carrier=name)
                continue
            if (word, value if value is None else str(value)) not in _DOCKER_KEEP:
                raise _Declared("It asks Docker for access to this computer (folders, ports or the network) that "
                                "Row-Bot doesn't grant.")
            kept += [word] + ([str(value)] if value is not None else [])
        passed = [part for name in env for part in ("-e", name)]
        install = {"transport": "stdio", "command": "docker", "args": ["run", "-i", "--rm", *kept, *passed, image, *arguments]}
    if len(inputs) > declared.LIMIT:
        # Dozens of tuning options (one server lists 84): keep what the person must give or keeps secret;
        # the rest stay at the server's own defaults.
        dropped = {key for key, item in inputs.items() if not item["required"] and not item["secret"]}
        flags = {inputs[key]["flag"] for key in dropped if inputs[key]["flag"]}
        inputs = {key: item for key, item in inputs.items() if key not in dropped}

        def unused(text: str) -> bool:
            keys = set(_VARIABLE.findall(text))
            return bool(keys) and keys <= dropped
        gone = {name for name, text in env.items() if unused(text)}
        env = {name: text for name, text in env.items() if name not in gone}
        kept: list[str] = []
        for arg in install["args"]:
            if unused(arg) or (install["command"] == "docker" and arg in gone and kept[-1:] == ["-e"]):
                if kept and kept[-1] in flags | {"-e"}:
                    kept.pop()  # A named argument goes with its value; ``-e NAME`` with its variable.
                continue
            kept.append(arg)
        install["args"] = kept
    if env:
        install["env"] = env
    if inputs:
        install["inputs"] = list(inputs.values())
    return _checked(install)


def registry_entries(data: dict) -> list[MarketplaceEntry]:
    """Parse bounded v0.1 metadata; unsupported declarations never become recipes.

    Declared headers, URL variables, environment variables and arguments become the recipe's
    templates and declared inputs (``row_bot.integrations.inputs``); the person fills them in when
    connecting. A server whose declarations cannot be expressed safely is still listed, with the
    reason and no recipe or setup binding, so it can never be imported.
    """
    if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
        raise ValueError("invalid_registry_response")
    entries = []
    known = {"$schema", "name", "version", "title", "description", "repository", "websiteUrl", "icons", "remotes", "packages", "_meta"}
    for envelope in data["servers"][:1000]:
        if not isinstance(envelope, dict) or not isinstance(envelope.get("server"), dict):
            continue
        item = envelope["server"]
        official = envelope.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
        official = official if isinstance(official, dict) else {}
        name, version = item.get("name"), item.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or not name or len(name) > 200 or len(version) > 128:
            continue
        status = official.get("status", "unknown")
        install, notes = None, []
        remotes, packages, reviewable = item.get("remotes", []), item.get("packages", []), True
        try:
            if not isinstance(remotes, list) or not isinstance(packages, list) or len(remotes) > 16 or len(packages) > 16:
                raise ValueError("invalid_registry_declarations")
            setup_digest = _registry_setup_digest(item)
        except ValueError:
            reviewable, setup_digest = False, ""
            notes.append("This server declares more setup than Row-Bot can review.")
        if not reviewable:
            pass
        elif status != "active":
            notes.append("Registry status: " + str(status)[:80] + ". New installation is unavailable.")
        elif set(item) - known:
            notes.append("Additional server setup or authentication declarations are unsupported by catalog import.")
        else:
            # The first route Row-Bot can express: a hosted remote, else a package it can run.
            routes = [(remote, _remote) for remote in remotes] + [(package, _package) for package in packages]
            for declaration, build in routes:
                try:
                    install = build(declaration if isinstance(declaration, dict) else {})
                    break
                except _Declared as reason:
                    notes.append(str(reason))
            if install is not None:
                notes = []  # Reasons for routes not taken are not the record's.
        secret = any(field["secret"] for field in (install or {}).get("inputs", []))
        repository = item.get("repository", {})
        url = str(repository.get("url", "")) if isinstance(repository, dict) else ""
        entries.append(MarketplaceEntry(id=name + "@" + version, name=str(item.get("title") or name)[:128],
            description=str(item.get("description", ""))[:800], source="official", publisher=name.split("/", 1)[0],
            url=url or str(item.get("websiteUrl") or "")[:2048], classification="official-registry",
            transport=install.get("transport", "") if install else "", requires_auth=secret,
            install=install, notes=list(dict.fromkeys(notes)), metadata={"version": version, "status": status,
                "canonical_name": name, **({"setup_digest": setup_digest} if install else {}),
                **({"auth_mode": "api_key"} if secret else {}), **_registry_display(item, official)}))
    return entries


def entry_to_server_config(entry: MarketplaceEntry) -> dict[str, Any]:
    """Return a disabled, review-required server config template."""
    if entry.source == "official" and (not entry.install or not (entry.metadata or {}).get("setup_digest")):
        raise ValueError("registry_recipe_unsupported")
    from row_bot.integrations import inputs as declared
    install = dict(entry.install or {})
    conflicts = [conflict.as_dict() for conflict in conflicts_for_entry(entry)]
    fields = {field: dict(install.get(field) or {}) for field in ("headers", "env")}
    found = declared.check(install.get("inputs"))
    if not found and not (entry.metadata or {}).get("auth_bindings"):
        # A recipe that leaves a header or variable blank asks the person for it when connecting.
        for field, target in (("headers", "header"), ("env", "env")):
            for name, value in fields[field].items():
                if value == "":
                    found.append(declared.declaration(declared.key_of(name), target=target, name=name,
                                                      secret=declared.secretish(name), required=True))
                    fields[field][name] = "{" + found[-1]["key"] + "}"
    metadata = dict(entry.metadata or {})
    if any(item["secret"] for item in found) and not metadata.get("auth_mode"):
        metadata["auth_mode"] = "api_key"
    return {
        "enabled": False,
        "environment_mode": "minimal",
        "transport": install.get("transport") or entry.transport or "stdio",
        "command": install.get("command", ""),
        "args": install.get("args", []),
        "url": install.get("url", ""),
        "headers": fields["headers"],
        "env": fields["env"],
        **({"inputs": found} if found else {}),
        **({"bundle": dict(install["bundle"])} if install.get("bundle") else {}),
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
            **{key: metadata[key] for key in ("auth_mode", "auth_bindings", "account_requirements", "cost", "evidence",
                                              "oauth_client", "oauth_client_url", "oauth_scope") if key in metadata},
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
