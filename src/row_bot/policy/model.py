"""Company policy, schema v1: the settings, their defaults, the strict parser, the additive merge and lockdown.

Every setting is declared once below, as a field of a section, with its default (the value it takes when the
company policy leaves it out), how a group override widens it, and its lockdown value. The JSON Schemas in
``contracts/policy/v1/`` are generated from these fields (``row_bot.policy.schema``).

Two kinds of document, both parsed strictly (an unknown key, a wrong type, a duplicate key or a value out of
range refuses the whole document, ``PolicyError``):

- **the company policy** (``parse_policy``): what everyone gets. It holds the restrictions.
- **a group override** (``parse_override``): what one IdP group gets on top. It can only widen: each setting
  it sets is merged with the company's by that setting's rule (``Spec.widen``), and the more permissive value
  wins. A list the company takes away (disabled tools) is named in an override by what it gives back
  (``enabled_groups``, ``enabled_tools``), so a later change to the company policy is never undone by an
  override that restated it. A person in several groups gets the union of what each group adds.

``resolve(company, overrides)`` is the effective policy of one person. ``lockdown_policy()`` is what a
Row-Bot falls back to when its cached policy is too old (``offline.max_days``): chat with local models only.

Standard library only, and nothing else from ``row_bot``, so the Company Server can use it without the app.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from typing import Any, Literal

Kind = Literal["bool", "int", "enum", "list"]
# How a group override widens a setting: "any" (on if anyone says on), "max" (the most permissive choice or
# the larger number), "union" (every entry anyone allows), "remove" (the company's list less what any
# override gives back).
Widen = Literal["any", "max", "union", "remove"]

# The app's approval levels (row_bot.approval_policy.APPROVAL_MODES), least permissive first. "approve" is
# shown as Ask and "full" as Hands-off.
APPROVAL_LEVELS = ("block", "approve", "auto", "full")
APP_MODES = ("none", "allow-list", "any")

ALL_MODELS = "*"
MODEL_ID = r"\*|[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}"
# A registry tool (BaseTool.name, such as shell) and a model-facing tool name (such as run_command).
TOOL_GROUP = r"[a-z0-9][a-z0-9_]{0,63}"
TOOL_NAME = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}"
CHANNEL_ID = r"[a-z0-9][a-z0-9_-]{0,63}"
EXTENSION_ID = r"[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,199}"
MAX_LIST_ITEMS = 512
MAX_VERSION = 2**63 - 1
MAX_DOCUMENT_BYTES = 256 * 1024

# The registry tools that reach outside Row-Bot (a shell, a browser, the screen and camera, the network, other
# accounts, code and processes, channels, public links, cloud generation, a vault folder outside the
# workspace) or act beyond the conversation. Lockdown switches them off. (POL-1's list.)
OUTSIDE_TOOLS = frozenset({
    "agents", "arxiv", "browser", "calendar", "computer_use", "conversation_setup", "custom_tool_builder",
    "designer", "developer", "duckduckgo", "file_delivery", "gmail", "image_gen", "linked_conversations", "mcp",
    "outlook_calendar", "outlook_mail", "shell", "sound", "system_info", "task", "url_reader", "video_editing",
    "video_gen", "vision", "waits", "weather", "web_search", "wiki", "wikipedia", "wolfram_alpha", "x", "youtube",
})
# The WhatsApp bridge (whatsapp) is unofficial, so it is allowed only when named.
DEFAULT_CHANNELS = frozenset({"discord", "slack", "sms", "telegram"})


class PolicyError(ValueError):
    """A document can't be used. ``detail`` names the setting and what is wrong, for the admin."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class Spec:
    """How one setting is written, checked, defaulted, widened and locked down."""

    kind: Kind
    widen: Widen
    lockdown: Any
    title: str
    help: str
    choices: tuple[str, ...] = ()  # least permissive first
    pattern: str = ""
    maximum: int = 0
    # The key a group override uses instead, for a list it gives back ("remove").
    override_key: str = ""


def _setting(default: Any, kind: Kind, widen: Widen, lockdown: Any, title: str, help: str, **spec: Any) -> Any:
    return field(default=default, metadata={"policy": Spec(kind, widen, lockdown, title, help, **spec)})


@dataclass(frozen=True)
class Models:
    """Which models people may use, and how they reach them."""

    allowed: frozenset[str] = _setting(
        frozenset({ALL_MODELS}), "list", "union", frozenset(), "Allowed catalog models",
        "Company catalog model ids people may use; * is every model in the catalog.", pattern=MODEL_ID)
    personal_api_keys: bool = _setting(False, "bool", "any", False, "Allow personal API keys",
                                       "Whether people may add their own API keys and custom endpoints.")
    consumer_sign_ins: bool = _setting(False, "bool", "any", False, "Allow consumer sign-ins",
                                       "Whether people may sign in with a consumer subscription (Claude plan, "
                                       "ChatGPT/Codex, xAI Grok, OpenCode Go).")
    local_models: bool = _setting(True, "bool", "any", True, "Allow local models",
                                  "Whether people may run models on their own computer (Ollama, llama.cpp).")


@dataclass(frozen=True)
class Tools:
    """Which of Row-Bot's tools are switched off."""

    disabled_groups: frozenset[str] = _setting(
        frozenset(), "list", "remove", OUTSIDE_TOOLS, "Disabled tool groups",
        "Registry tools switched off, with every tool they offer (for example shell, browser, computer_use, "
        "developer).", pattern=TOOL_GROUP, override_key="enabled_groups")
    disabled_tools: frozenset[str] = _setting(
        frozenset(), "list", "remove", frozenset(), "Disabled tools",
        "Single model-facing tools switched off (for example run_command).", pattern=TOOL_NAME,
        override_key="enabled_tools")


@dataclass(frozen=True)
class Approvals:
    """How far people may let Row-Bot act without asking."""

    max_level: str = _setting("auto", "enum", "max", "block", "Highest approval level",
                              "The highest approval level anyone may choose: block, approve (Ask), auto or full "
                              "(Hands-off).", choices=APPROVAL_LEVELS)


@dataclass(frozen=True)
class Apps:
    """Which plugins, MCP servers, Skills Hub skills and channels people may use."""

    plugins: str = _setting("any", "enum", "max", "none", "Plugins",
                            "none, allow-list (only allowed_plugins) or any.", choices=APP_MODES)
    allowed_plugins: frozenset[str] = _setting(frozenset(), "list", "union", frozenset(), "Allowed plugins",
                                               "Plugin ids, used when plugins is allow-list.", pattern=EXTENSION_ID)
    mcp_servers: str = _setting("any", "enum", "max", "none", "MCP servers",
                                "none, allow-list (only allowed_mcp_servers) or any.", choices=APP_MODES)
    allowed_mcp_servers: frozenset[str] = _setting(frozenset(), "list", "union", frozenset(), "Allowed MCP servers",
                                                   "MCP server names, used when mcp_servers is allow-list.",
                                                   pattern=EXTENSION_ID)
    skills_hub: str = _setting("any", "enum", "max", "none", "Skills Hub",
                               "none, allow-list (only allowed_skills) or any.", choices=APP_MODES)
    allowed_skills: frozenset[str] = _setting(frozenset(), "list", "union", frozenset(), "Allowed skills",
                                              "Skills Hub skill ids, used when skills_hub is allow-list.",
                                              pattern=EXTENSION_ID)
    channels: str = _setting("allow-list", "enum", "max", "none", "Channels",
                             "none, allow-list (only allowed_channels) or any.", choices=APP_MODES)
    allowed_channels: frozenset[str] = _setting(DEFAULT_CHANNELS, "list", "union", frozenset(), "Allowed channels",
                                                "Channel ids, used when channels is allow-list. The WhatsApp "
                                                "bridge (whatsapp) is unofficial, so it is allowed only when "
                                                "named.", pattern=CHANNEL_ID)


@dataclass(frozen=True)
class Data:
    """What people may take out of Row-Bot or offer to the company."""

    chat_export: bool = _setting(True, "bool", "any", False, "Allow chat export",
                                 "Whether people may export their chats and data.")
    share_to_company: bool = _setting(True, "bool", "any", False, "Allow Share to company",
                                      "Whether people may offer a document, fact or answer to the company "
                                      "(a space owner still approves it).")


@dataclass(frozen=True)
class Offline:
    """How long a Row-Bot may keep working on its cached policy without reaching the server."""

    max_days: int = _setting(7, "int", "max", 0, "Offline days",
                             "Days a Row-Bot may run on its cached policy; after that it locks down to chat with "
                             "local models only. 0 locks down as soon as the server can't be reached.",
                             maximum=365)


@dataclass(frozen=True)
class Policy:
    """A person's effective policy: every setting, and the version it was made from."""

    version: int = 0
    models: Models = field(default_factory=Models)
    tools: Tools = field(default_factory=Tools)
    approvals: Approvals = field(default_factory=Approvals)
    apps: Apps = field(default_factory=Apps)
    data: Data = field(default_factory=Data)
    offline: Offline = field(default_factory=Offline)


SECTIONS: dict[str, type] = {f.name: f.default_factory for f in fields(Policy) if f.name != "version"}  # type: ignore[misc]


def specs(section: str) -> dict[str, Spec]:
    """The settings of *section*, in declaration order."""
    return {f.name: f.metadata["policy"] for f in fields(SECTIONS[section])}


def document_key(spec: Spec, name: str, *, override: bool) -> str:
    """The key a setting is written under in a company policy, or in a group override."""
    return spec.override_key if override and spec.override_key else name


DEFAULT_POLICY = Policy()
_LOCKDOWN = Policy(**{section: cls(**{name: spec.lockdown for name, spec in specs(section).items()})
                      for section, cls in SECTIONS.items()})


def lockdown_policy() -> Policy:
    """What a Row-Bot applies when its cached policy is older than ``offline.max_days``: chat with local models,
    the in-conversation tools, and nothing that reaches outside Row-Bot."""
    return _LOCKDOWN


@dataclass(frozen=True)
class Document:
    """A company policy or a group override as written: only the settings it sets, checked and normalised.

    ``settings`` maps a section to its settings by their ``Policy`` names (an override's ``enabled_groups`` is
    held as ``disabled_groups``, the setting it gives back). ``version`` is the server's version of it, if any.
    """

    settings: Mapping[str, Mapping[str, Any]]
    override: bool = False
    version: int | None = None


# -- parsing ------------------------------------------------------------------------------------------

def _refuse_constant(name: str) -> Any:
    raise PolicyError(f"{name} is not a JSON number")


def _refuse_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"{key}: appears twice")
        result[key] = value
    return result


def loads(text: str | bytes) -> object:
    """Strict JSON: at most ``MAX_DOCUMENT_BYTES``, UTF-8, no duplicate keys, no NaN or Infinity."""
    raw = text.encode("utf-8") if isinstance(text, str) else text
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise PolicyError(f"policy: larger than {MAX_DOCUMENT_BYTES} bytes")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_refuse_duplicates,
                          parse_constant=_refuse_constant)
    except UnicodeDecodeError:
        raise PolicyError("policy: not UTF-8") from None
    except json.JSONDecodeError as error:
        raise PolicyError(f"policy: not JSON ({error.msg} at line {error.lineno} column {error.colno})") from None


def _value(spec: Spec, raw: object, path: str) -> Any:
    if spec.kind == "bool":
        if type(raw) is not bool:
            raise PolicyError(f"{path}: expected true or false")
        return raw
    if spec.kind == "int":
        if type(raw) is not int or not 0 <= raw <= spec.maximum:
            raise PolicyError(f"{path}: expected a whole number from 0 to {spec.maximum}")
        return raw
    if spec.kind == "enum":
        if type(raw) is not str or raw not in spec.choices:
            raise PolicyError(f"{path}: expected one of " + ", ".join(spec.choices))
        return raw
    if type(raw) is not list or len(raw) > MAX_LIST_ITEMS:
        raise PolicyError(f"{path}: expected a list of at most {MAX_LIST_ITEMS} entries")
    for index, item in enumerate(raw):
        if type(item) is not str or not re.fullmatch(spec.pattern, item):
            raise PolicyError(f"{path}[{index}]: not a valid entry")
    entries = frozenset(raw)
    return frozenset({ALL_MODELS}) if ALL_MODELS in entries else entries


def _object(raw: object, path: str) -> dict[str, Any]:
    if type(raw) is not dict:
        raise PolicyError(f"{path}: expected an object")
    return raw


def _parse(raw: object, *, override: bool) -> Document:
    document = _object(raw, "policy")
    version = None
    settings: dict[str, dict[str, Any]] = {}
    for section, values in document.items():
        if section == "version":
            if type(values) is not int or not 0 <= values <= MAX_VERSION:
                raise PolicyError("version: expected a whole number from 0")
            version = values
            continue
        if section not in SECTIONS:
            raise PolicyError(f"{section}: not a policy section")
        keys = {document_key(spec, name, override=override): name for name, spec in specs(section).items()}
        parsed: dict[str, Any] = {}
        for key, item in _object(values, section).items():
            if key not in keys:
                kind = "group override" if override else "company policy"
                raise PolicyError(f"{section}.{key}: not a {kind} setting")
            parsed[keys[key]] = _value(specs(section)[keys[key]], item, f"{section}.{key}")
        settings[section] = parsed
    return Document(settings, override=override, version=version)


def parse_policy(raw: object) -> Document:
    """A company policy (a parsed JSON object, see ``loads``). Refuses anything not in schema v1."""
    return _parse(raw, override=False)


def parse_override(raw: object) -> Document:
    """A group override: the company policy's shape, with ``tools.enabled_groups`` and ``tools.enabled_tools``
    in place of the disabled lists."""
    return _parse(raw, override=True)


# -- merging ------------------------------------------------------------------------------------------

def _widen(spec: Spec, current: Any, offered: Any) -> Any:
    if spec.widen == "any":
        return current or offered
    if spec.widen == "max":
        return max(current, offered, key=spec.choices.index) if spec.kind == "enum" else max(current, offered)
    if spec.widen == "union":
        joined = current | offered
        return frozenset({ALL_MODELS}) if ALL_MODELS in joined else joined
    return current - offered


def resolve(company: Document | None, overrides: Iterable[Document] = ()) -> Policy:
    """The effective policy: the defaults, then the company policy's settings, then each override widening
    them. The order of the overrides doesn't matter. ``version`` is the newest of the documents' versions."""
    chosen = list(overrides)
    if company is not None and company.override or any(not override.override for override in chosen):
        raise ValueError("resolve takes one company policy and group overrides")
    sections: dict[str, dict[str, Any]] = {}
    for section in SECTIONS:
        values = {name: getattr(getattr(DEFAULT_POLICY, section), name) for name in specs(section)}
        if company is not None:
            values.update(company.settings.get(section, {}))
        for override in chosen:
            for name, offered in override.settings.get(section, {}).items():
                values[name] = _widen(specs(section)[name], values[name], offered)
        sections[section] = values
    versions = [document.version or 0 for document in ([company] if company else []) + chosen]
    return Policy(version=max(versions, default=0),
                  **{section: SECTIONS[section](**values) for section, values in sections.items()})


# -- writing ------------------------------------------------------------------------------------------

def _json(value: Any) -> Any:
    return sorted(value) if isinstance(value, frozenset) else value


def policy_to_dict(policy: Policy) -> dict[str, Any]:
    """The effective policy as JSON (every setting; lists sorted). ``resolve(parse_policy(...))`` reads it back."""
    result: dict[str, Any] = {"version": policy.version}
    for section in SECTIONS:
        values = getattr(policy, section)
        result[section] = {name: _json(getattr(values, name)) for name in specs(section)}
    return result


def document_to_dict(document: Document) -> dict[str, Any]:
    """A document as written (only what it sets, in declaration order, lists sorted), with its version if any."""
    result: dict[str, Any] = {} if document.version is None else {"version": document.version}
    for section in SECTIONS:
        values = document.settings.get(section)
        if values:
            result[section] = {document_key(spec, name, override=document.override): _json(values[name])
                               for name, spec in specs(section).items() if name in values}
    return result


def canonical_json(value: object) -> bytes:
    """``value`` as compact JSON with sorted keys: the same bytes for the same policy."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


__all__ = [
    "ALL_MODELS", "APPROVAL_LEVELS", "APP_MODES", "DEFAULT_CHANNELS", "DEFAULT_POLICY", "MAX_DOCUMENT_BYTES",
    "OUTSIDE_TOOLS", "SECTIONS", "Apps", "Approvals", "Data", "Document", "Models", "Offline", "Policy",
    "PolicyError", "Spec", "Tools", "canonical_json", "document_key", "document_to_dict", "loads",
    "lockdown_policy", "parse_override", "parse_policy", "policy_to_dict", "resolve", "specs",
]
