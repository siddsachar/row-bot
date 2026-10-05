"""Declared inputs: what a connection needs from the person (a key, a tenant, a folder), one path for
every source (the Registry, a plugin's mcp.json, a Hermes recipe, an MCP bundle).

A declaration says where a value goes (a header, an environment variable, an argument or a URL
variable). The template stays in the saved configuration itself (``"Authorization": "Bearer {token}"``,
``"--region", "{region}"``, ``https://{tenant}.example.com/mcp``), with the declarations in
``cfg["inputs"]``. Values are filled in only when Row-Bot connects: plain ones from
``cfg["input_values"]``, secrets from the keychain. Neither ever appears in a template, a log or
an API response.
"""
from __future__ import annotations

import math
import re
from urllib.parse import quote, urlsplit

KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
TARGETS = ("header", "env", "argument", "url_variable")
LIMIT = 32
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]{0,63})\}")
_HEADER = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
# Never set from a declaration: they change what runs or where it looks for code and data.
NEVER_ENV = {"PATH", "PATHEXT", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD",
             "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "SYSTEMROOT", "COMSPEC", "WINDIR", "HOME",
             "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PLUGIN_ROOT", "PLUGIN_DATA"}
# Second-level labels shared by everyone under a country domain (co.uk, com.au): not one service's domain.
_SHARED = {"co", "com", "net", "org", "gov", "ac", "edu", "ne", "or", "go"}
_SECRETISH = re.compile(r"(api|access|auth|bearer|client)?_?(key|token|secret|password|passwd|pat|credential|signature)s?$|^pat$",
                        re.IGNORECASE)


class InputError(ValueError):
    pass


def key_of(name: str) -> str:
    """An input key from a header or variable name: ``X-API-Key`` gives ``x_api_key``."""
    key = re.sub(r"[^A-Za-z0-9_]+", "_", str(name)).strip("_").lower()[:64] or "value"
    return key if KEY.fullmatch(key) else ("v_" + key)[:64]


def secretish(name: str) -> bool:
    """A name that reads like a credential (``api_key``, ``GITHUB_TOKEN``, ``Authorization``)."""
    return name.lower() in {"authorization", "proxy-authorization", "cookie"} or bool(_SECRETISH.search(key_of(name)))


def declaration(key: str, *, target: str, name: str, label: str = "", description: str = "", secret: bool = False,
                required: bool = False, default: str = "", choices: list | tuple = (), help_url: str = "",
                format: str = "string", flag: str = "") -> dict:
    """One declared input. ``flag`` is a named argument's own word (``--region``), dropped with an empty value."""
    return {"key": key, "label": (label or name or key)[:128], "description": description[:512], "secret": bool(secret),
            "required": bool(required), "target": target, "name": name[:128], "default": "" if secret else str(default)[:1024],
            "choices": [str(choice)[:256] for choice in choices][:64], "help_url": help_url[:2048],
            "format": format if format in {"string", "number", "boolean", "filepath"} else "string", "flag": flag[:128]}


def check(declared: object) -> list[dict]:
    """A declaration list from a catalog or a saved configuration, or ``invalid_inputs``."""
    if declared is None:
        return []
    if type(declared) is not list or len(declared) > LIMIT:
        raise InputError("invalid_inputs")
    keys = set()
    for item in declared:
        if (type(item) is not dict or not KEY.fullmatch(str(item.get("key", ""))) or item["key"] in keys
                or item.get("target") not in TARGETS or type(item.get("secret")) is not bool
                or type(item.get("required")) is not bool or type(item.get("choices", [])) is not list
                or any(type(value) is not str or len(value) > 2048 for value in
                       [item.get("name", ""), item.get("label", ""), item.get("default", ""), item.get("help_url", ""),
                        item.get("flag", ""), *item.get("choices", [])])
                or (item["secret"] and item.get("default"))):
            raise InputError("invalid_inputs")
        if item["target"] == "env" and (not _ENV.fullmatch(item["name"]) or item["name"].upper() in NEVER_ENV):
            raise InputError("invalid_inputs")
        if item["target"] == "header" and not _HEADER.fullmatch(item["name"]):
            raise InputError("invalid_inputs")
        keys.add(item["key"])
    return [dict(item) for item in declared]


def check_url(template: str, declared: list[dict]) -> None:
    """A URL template may fill path segments, query values or subdomain labels, never the whole host:
    the destination a person agreed to (``*.example.com``) cannot move."""
    parts = urlsplit(template.replace("{", "x").replace("}", "x"))
    host = template.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    if parts.scheme != "https" or not parts.hostname or "@" in host:
        raise InputError("input_url_unsupported")
    if "{" in host:
        fixed = host.rsplit("}", 1)[1]
        labels = fixed.lower().split(".")[1:]
        if (not fixed.startswith(".") or fixed.count(".") < 2 or "{" in fixed or ":" in fixed
                or any(not label or label.isdigit() for label in labels)  # {x}.com. is any .com host.
                or (len(labels) == 2 and len(labels[1]) == 2 and labels[0] in _SHARED)):
            raise InputError("input_url_unsupported")  # {x}.co.uk or {x}.0.0.1 would name anyone's address.
    known = {item["key"] for item in declared}
    if any(name not in known for name in _PLACEHOLDER.findall(template)):
        raise InputError("invalid_inputs")


def values(declared: list[dict], given: dict) -> tuple[dict, dict]:
    """The person's values, checked against the declarations: ``(plain, secret)``. Defaults fill
    what was left empty; a required input without a value, a value outside its choices, or one a
    URL or header cannot carry is refused."""
    plain, secret = {}, {}
    for item in declared:
        value = str(given.get(item["key"], "") or "").strip() or item.get("default", "")
        if not value:
            if item["required"]:
                raise InputError("input_required:" + item["key"])
            continue
        if len(value) > 16384 or "\r" in value or "\n" in value or "\0" in value:
            raise InputError("input_invalid:" + item["key"])
        if item.get("choices") and value not in item["choices"]:
            raise InputError("input_invalid:" + item["key"])
        if item.get("format") == "number" and not re.fullmatch(r"-?\d+(\.\d+)?", value):
            raise InputError("input_invalid:" + item["key"])
        if item.get("format") == "boolean" and value not in {"true", "false"}:
            raise InputError("input_invalid:" + item["key"])
        if item["target"] == "url_variable" and not re.fullmatch(r"[A-Za-z0-9._~-]{1,256}", value):
            raise InputError("input_invalid:" + item["key"])
        (secret if item["secret"] else plain)[item["key"]] = value
    return plain, secret


def missing(cfg: dict, secrets_saved: bool) -> tuple[bool, bool]:
    """Whether a saved connection still needs a secret, and whether it still needs a plain value."""
    declared = cfg.get("inputs") or []
    saved = cfg.get("input_values") or {}
    secret = any(item["secret"] and item["required"] for item in declared) and not secrets_saved
    plain = any(not item["secret"] and item["required"] and not (saved.get(item["key"]) or item.get("default"))
                for item in declared)
    return secret, plain


def _fill(text: str, found: dict, *, url_part: str = "") -> str:
    def value(match: re.Match) -> str:
        filled = found.get(match.group(1), "")
        return quote(filled, safe="-._~") if url_part == "path" else filled
    return _PLACEHOLDER.sub(value, text)


def resolve(cfg: dict, secrets: dict, *, partial: bool = False) -> dict:
    """A copy of a saved connection with every declared input filled in, for one connection only.
    Optional inputs left empty drop their header, variable or argument; a required one refuses
    (``partial``: for the address alone, before any key is saved)."""
    declared = check(cfg.get("inputs"))
    if not declared:
        return cfg
    found = {item["key"]: item.get("default", "") for item in declared}
    found.update({key: str(value) for key, value in (cfg.get("input_values") or {}).items() if type(value) is str})
    found.update({key: value for key, value in secrets.items() if key in found})
    for item in declared:
        if item["required"] and not found.get(item["key"]) and not (partial and item["secret"]):
            raise InputError("mcp_inputs_required")
    resolved = dict(cfg)
    used = {item["key"] for item in declared}

    def empty(text: str) -> bool:  # Only placeholders, all of them empty.
        keys = _PLACEHOLDER.findall(text)
        return bool(keys) and all(key in used and not found.get(key) for key in keys)
    if "{" in str(cfg.get("url") or ""):
        check_url(cfg["url"], declared)
        host, slash, rest = cfg["url"].split("://", 1)[1].partition("/")
        filled_host = _fill(host, found)
        if "{" in host and not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", filled_host):
            raise InputError("input_invalid")  # A subdomain value can never add a port, a user or a path.
        resolved["url"] = "https://" + filled_host + slash + _fill(rest, found, url_part="path")
    for field in ("headers", "env"):
        resolved[field] = {name: _fill(str(text), found) for name, text in (cfg.get(field) or {}).items() if not empty(str(text))}
    for name, text in (cfg.get("headers") or {}).items():
        if name.lower() == "authorization" and name in resolved["headers"] and _PLACEHOLDER.search(str(text)):
            resolved["headers"][name] = _scheme(resolved["headers"][name])
    args, flags = [], {item["flag"] for item in declared if item.get("flag")}
    for arg in [str(arg) for arg in cfg.get("args") or []]:
        if empty(arg):
            if args and args[-1] in flags:
                args.pop()  # A named argument goes with its value.
            continue
        args.append(_fill(arg, found))
    resolved["args"] = args
    return resolved


def _scheme(value: str) -> str:
    """An Authorization value the person typed: a bare token is sent as ``Bearer`` (a header value
    needs a scheme), and a pasted ``Bearer x`` into a ``Bearer {key}`` template is not doubled."""
    value = re.sub(r"^(bearer)\s+bearer\s+", r"\1 ", value.strip(), flags=re.IGNORECASE)
    return value if not value or re.search(r"\s", value) else "Bearer " + value


def secret_segment(segment: str) -> bool:
    """A link path segment that looks like a key (long, random, letters and digits): it is never
    saved as part of an address; the person enters it as a secret instead."""
    if len(segment) < 20 or not re.fullmatch(r"[A-Za-z0-9_-]+", segment):
        return False
    if re.match(r"(sk|pk|ghp|gho|github_pat|xox[abp]|pat|key)[_-]", segment, re.IGNORECASE):
        return True
    if not (re.search(r"[A-Za-z]", segment) and re.search(r"\d", segment)):
        return False
    counts = {char: segment.count(char) for char in set(segment)}
    entropy = -sum(n / len(segment) * math.log2(n / len(segment)) for n in counts.values())
    return entropy >= 3.5
