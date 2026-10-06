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

from functools import cache
import lzma
import math
from pathlib import Path
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


_ACRONYMS = {"api": "API", "url": "URL", "id": "ID", "oauth": "OAuth", "mcp": "MCP", "ssh": "SSH", "http": "HTTP",
             "https": "HTTPS", "json": "JSON", "sql": "SQL", "jwt": "JWT", "aws": "AWS", "gcp": "GCP", "ip": "IP"}


def label(text: str, keep: tuple[str, ...] = ()) -> str:
    """A setting's name as a sentence: ``GITHUB_API_TOKEN`` or ``Personal Access Token`` read
    "GitHub API token" / "Personal access token". Acronyms, names with inner capitals and the
    words in ``keep`` (the app's own name) keep their case."""
    raw = text.strip()
    if re.fullmatch(r"[a-z]+(?:[A-Z][a-z0-9]*)+", raw):  # camelCase: apiKey
        raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
    named = bool(re.search(r"[_-]", raw) or re.fullmatch(r"[A-Z0-9 ]+", raw))  # A variable or header name, not words.
    kept = {word.casefold(): word for name in keep for word in name.split()}
    shown = []
    for word in (w for w in re.split(r"[_\s]+|-(?=[A-Za-z])" if named else r"\s+", raw) if w):
        folded = word.casefold()
        if folded in kept:
            shown.append(kept[folded])
        elif folded in _ACRONYMS:
            shown.append(_ACRONYMS[folded])
        elif not named and (re.search(r".[A-Z]", word) or (word.isupper() and 2 <= len(word) <= 5)):
            shown.append(word)  # GitHub, iOS, SMTP: written that way on purpose.
        else:
            shown.append(folded)
    sentence = " ".join(shown)
    if shown and re.search(r".[A-Z]", shown[0]):
        return sentence[:128]  # iOS stays iOS.
    return (sentence[:1].upper() + sentence[1:])[:128] if sentence else text[:128]


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


@cache
def _suffixes() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """The Public Suffix List as bundled (ICANN and private sections; MPL-2.0, publicsuffix.org): its plain
    rules, the parents of its wildcard rules, and its exceptions."""
    text = lzma.decompress(Path(__file__).with_name("public_suffix_list.dat.xz").read_bytes()).decode("utf-8")
    rules: set[str] = set()
    wildcards: set[str] = set()
    exceptions: set[str] = set()
    for line in text.splitlines():
        rule = line.strip().split(" ", 1)[0]
        if not rule or rule.startswith("//"):
            continue
        try:
            rule = ".".join(label if label in {"*", "!"} or label.isascii() else label.encode("idna").decode("ascii")
                            for label in rule.lower().split("."))
        except UnicodeError:
            continue
        if rule.startswith("!"):
            exceptions.add(rule[1:])
        elif rule.startswith("*."):
            wildcards.add(rule[2:])
        else:
            rules.add(rule)
    return frozenset(rules), frozenset(wildcards), frozenset(exceptions)


def public_suffix(domain: str) -> bool:
    """Whether names directly under ``domain`` belong to anyone (``co.uk``, ``github.io``, ``vercel.app``,
    any top-level domain), so ``{x}.domain`` would not name one service."""
    try:  # As the list keeps them: international names in their ASCII form (公司.cn is xn--55qx5d.cn).
        domain = ".".join(label if label.isascii() else label.encode("idna").decode("ascii")
                          for label in domain.lower().strip(".").split("."))
    except UnicodeError:
        return True  # A name that can't be written in ASCII names no one service.
    rules, wildcards, exceptions = _suffixes()
    if domain in exceptions:
        return False
    # ``*.kawasaki.jp`` makes every name directly under kawasaki.jp a public suffix too.
    return "." not in domain or domain in rules or domain in wildcards or domain.partition(".")[2] in wildcards


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
                or public_suffix(fixed[1:])):
            raise InputError("input_url_unsupported")  # {x}.co.uk, {x}.github.io or {x}.0.0.1 would name anyone's.
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
