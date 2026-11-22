"""Organisation policy, schema v1: the typed settings, their documented defaults, the lockdown values and
the strict parser.

Every setting is declared once, as a field of a section below, with the value it takes when a policy is
present but leaves it out (the field default) and the value it takes in lockdown. The JSON Schema in
``contracts/policy/v1/``, the admin templates and the defaults table are generated from these fields, so
they cannot drift from what the loader accepts.

The parser is strict: an unknown key, a wrong type, a duplicate key or a value out of range refuses the whole
policy (``InvalidPolicy``), and the loader turns that into lockdown, never into "no policy".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any, Literal

from row_bot.approval_policy import APPROVAL_MODES

SCHEMA_VERSION = 1
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

Kind = Literal["bool", "int", "text", "url", "enum", "list"]

# Identifier shapes, matching the app's own ids (providers, tools, channels, plugins, MCP servers, skills).
PROVIDER_ID = r"[a-z0-9][a-z0-9_-]{0,99}"
MODEL_REF = rf"model:{PROVIDER_ID}:[^\s]{{1,200}}"
TOOL_NAME = r"\*|[a-z0-9][a-z0-9_]{0,63}"
CHANNEL_ID = r"[a-z0-9][a-z0-9_-]{0,63}"
EXTENSION_ID = r"[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,199}"
ORG_ID = r"[a-z0-9][a-z0-9._-]{0,63}"
VERSION = r"\d{1,4}\.\d{1,4}\.\d{1,4}(?:[-+][0-9A-Za-z.-]{1,64})?"
TEXT = r"[^\x00-\x1f\x7f]*"
# https anywhere; plain http only to this computer. No user name or password in the URL, no fragment.
URL = r"(?:https://[A-Za-z0-9.-]+(?::\d{1,5})?|http://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?)(?:/[^\s#@]*)?"
KEY_ID = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
ED25519_KEY = r"[A-Za-z0-9+/]{43}="
MAX_LIST_ITEMS = 512

# Providers a person signs in to with a consumer subscription (``providers.catalog.PROVIDER_BILLING``
# "subscription"): off under any policy unless ``models.subscription_sign_ins`` turns them on.
SUBSCRIPTION_PROVIDERS = frozenset({"claude_subscription", "codex", "xai_oauth", "opencode_go"})
EXTENSION_MODES = ("none", "allow-list", "any")
FEATURES = ("learning", "proactive", "remote_access", "data_export", "install_count")

# Lockdown keeps a recovery path: chat with a model on this computer, the app's own help and settings, and
# Row-Bot's signed self-update. Its tools are an allow-list of registry tool names (``BaseTool.name``, not the
# model-facing sub-tool names): core tools whose every sub-tool stays inside Row-Bot (its chats, memory, pages,
# documents, the workspace and its own updater) and acts only within the conversation. Every other tool,
# including any added later, a plugin's, an MCP server's or a custom one, is refused.
LOCKDOWN_TOOLS = frozenset({
    "calculator", "chart", "conversation_search", "documents", "filesystem", "goal", "media", "memory", "pages",
    "presentation", "row_bot_status", "row_bot_updater", "tracker",
})
# The core tools lockdown refuses, named so the defaults table lists them: they reach outside Row-Bot (a shell,
# a browser, the screen and camera, the network, other accounts, code and processes, channels, public links,
# cloud generation, a vault folder outside the workspace) or act beyond the conversation (background agents
# that choose their own model, scheduled workflows that deliver to channels, messages into other chats, and
# waits that run commands or fetch pages).
OUTSIDE_TOOLS = frozenset({
    "agents", "arxiv", "browser", "calendar", "computer_use", "conversation_setup", "custom_tool_builder",
    "designer", "developer", "duckduckgo", "file_delivery", "gmail", "image_gen", "linked_conversations", "mcp",
    "outlook_calendar", "outlook_mail", "shell", "sound", "system_info", "task", "url_reader", "video_editing",
    "video_gen", "vision", "waits", "weather", "web_search", "wiki", "wikipedia", "wolfram_alpha", "x", "youtube",
})
# The providers lockdown keeps, and only at a loopback address: Ollama and Row-Bot's own llama.cpp runtime
# (``local_runtime.PROVIDER_ID``). Other local servers (LM Studio, vLLM) are custom endpoints with any id, so a
# loopback address alone (a local proxy to a cloud included) is not enough.
LOCAL_PROVIDERS = frozenset({"ollama", "custom_openai_llamacpp"})


class InvalidPolicy(ValueError):
    """The policy can't be used. ``code`` is a stable word for the client; ``detail`` tells IT what to fix."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Spec:
    """How one setting is written, checked, defaulted and locked down."""

    kind: Kind
    lockdown: Any
    title: str
    help: str
    unset: str = ""  # what a missing setting means, when its default is None
    choices: tuple[str, ...] = ()
    pattern: str = ""
    max_length: int = 200
    minimum: int = 0
    maximum: int = 0
    requires: str = ""  # a sibling setting that must be present when this one is
    requires_value: str | None = None  # ...and the value it must have


def _setting(default: Any, kind: Kind, lockdown: Any, title: str, help: str, **spec: Any) -> Any:
    return field(default=default, metadata={"policy": Spec(kind, lockdown, title, help, **spec)})


@dataclass(frozen=True)
class Organisation:
    """Who the policy belongs to. Shown in Settings; nothing here changes behaviour on its own."""

    id: str | None = _setting(None, "text", None, "Organisation id",
                              "A short, stable id for the organisation.", unset="none", pattern=ORG_ID, max_length=64)
    display_name: str | None = _setting(None, "text", None, "Organisation name",
                                        "The organisation's name as people see it.", unset="none", pattern=TEXT)
    server_url: str | None = _setting(None, "url", None, "Org server URL",
                                      "The organisation's Row-Bot Org server.", unset="none", max_length=2048)
    policy_version: str | None = _setting(None, "text", None, "Policy version",
                                          "IT's own version label for this policy, shown in Settings.",
                                          unset="none", pattern=TEXT, max_length=64)
    contact: str | None = _setting(None, "text", None, "IT contact",
                                   "Who to contact about this policy, shown when Row-Bot is locked down.",
                                   unset="none", pattern=TEXT)


@dataclass(frozen=True)
class Approvals:
    """How far people may let Row-Bot act without asking."""

    max_level: str = _setting("auto", "enum", "block", "Highest approval level",
                              "The highest approval level anyone may choose: block, approve (Ask), auto (reviewed) "
                              "or full (Hands-off). Hands-off is allowed only when this is full.",
                              choices=APPROVAL_MODES)


@dataclass(frozen=True)
class Models:
    """Which models people may use, and how they reach them."""

    allowed_providers: frozenset[str] | None = _setting(
        None, "list", LOCAL_PROVIDERS, "Allowed providers",
        "Provider ids people may use (for example anthropic, openai, ollama, custom_openai_<endpoint>).",
        unset="any provider", pattern=PROVIDER_ID)
    allowed_models: frozenset[str] | None = _setting(
        None, "list", None, "Allowed models",
        "Provider-qualified model refs people may use (model:<provider>:<model id>).",
        unset="any model of an allowed provider", pattern=MODEL_REF)
    gateway_url: str | None = _setting(None, "url", None, "Organisation gateway URL",
                                       "An organisation model gateway Row-Bot offers as a provider.",
                                       unset="none", max_length=2048)
    gateway_shape: str = _setting("openai_chat", "enum", "openai_chat", "Organisation gateway API",
                                  "The API the gateway speaks. Needs gateway_url.",
                                  choices=("openai_chat", "openai_responses", "anthropic_messages"),
                                  requires="gateway_url")
    personal_api_keys: bool = _setting(True, "bool", False, "Allow personal API keys",
                                       "Whether people may add their own API keys and custom endpoints.")
    subscription_sign_ins: bool = _setting(False, "bool", False, "Allow consumer subscription sign-ins",
                                           "Whether people may sign in with a consumer subscription (Claude plan, "
                                           "ChatGPT/Codex, xAI Grok, OpenCode Go).")
    cloud_models: bool = _setting(True, "bool", False, "Allow cloud models",
                                  "Whether people may use models that run anywhere but this computer: cloud "
                                  "providers, the organisation gateway and endpoints on other machines.")
    local_models: bool = _setting(True, "bool", True, "Allow local models",
                                  "Whether people may run models on this computer (Ollama, llama.cpp, LM Studio). "
                                  "An endpoint counts as on this computer only when its address is loopback.")


@dataclass(frozen=True)
class Tools:
    """Which of Row-Bot's tools are switched off."""

    allowed: frozenset[str] | None = _setting(None, "list", LOCKDOWN_TOOLS, "Allowed tools",
                                              "The only tool names people may use; any other tool, including "
                                              "plugin, MCP and custom tools, is switched off.",
                                              unset="every tool", pattern=TOOL_NAME)
    disabled: frozenset[str] = _setting(frozenset(), "list", OUTSIDE_TOOLS, "Disabled tools",
                                        "Tool names switched off and locked (for example shell, computer_use, "
                                        "browser, developer); * switches off every tool.", pattern=TOOL_NAME)
    files_outside_workspace: bool = _setting(True, "bool", False, "Allow files outside the workspace",
                                             "Whether tools may read or change files outside the workspace (each "
                                             "time still asked by the approval level).")


@dataclass(frozen=True)
class Channels:
    """Which messaging channels people may connect."""

    allowed: frozenset[str] = _setting(frozenset({"discord", "slack", "sms", "telegram"}), "list", frozenset(),
                                       "Allowed channels",
                                       "Channel ids people may connect. The WhatsApp bridge (whatsapp) is "
                                       "unofficial, so it is allowed only when named here.", pattern=CHANNEL_ID)


@dataclass(frozen=True)
class Extensions:
    """Which plugins, MCP servers and skills people may add."""

    plugins: str = _setting("any", "enum", "none", "Plugins",
                            "none, allow-list (only allowed_plugins) or any.", choices=EXTENSION_MODES)
    allowed_plugins: frozenset[str] = _setting(frozenset(), "list", frozenset(), "Allowed plugins",
                                               "Plugin ids, when plugins is allow-list.", pattern=EXTENSION_ID,
                                               requires="plugins", requires_value="allow-list")
    mcp_servers: str = _setting("any", "enum", "none", "MCP servers",
                                "none, allow-list (only allowed_mcp_servers) or any.", choices=EXTENSION_MODES)
    allowed_mcp_servers: frozenset[str] = _setting(frozenset(), "list", frozenset(), "Allowed MCP servers",
                                                   "MCP server names, when mcp_servers is allow-list.",
                                                   pattern=EXTENSION_ID, requires="mcp_servers",
                                                   requires_value="allow-list")
    skills: str = _setting("any", "enum", "none", "Skills",
                           "none, allow-list (only allowed_skills) or any.", choices=EXTENSION_MODES)
    allowed_skills: frozenset[str] = _setting(frozenset(), "list", frozenset(), "Allowed skills",
                                              "Skill ids, when skills is allow-list.", pattern=EXTENSION_ID,
                                              requires="skills", requires_value="allow-list")


@dataclass(frozen=True)
class Features:
    """Features people may turn on. Off means off and locked; on leaves the person's own choice."""

    learning: bool = _setting(False, "bool", False, "Allow Learning", "Whether people may turn on Learning.")
    proactive: bool = _setting(True, "bool", False, "Allow Proactive Row-Bot",
                               "Whether people may turn on Proactive Row-Bot.")
    remote_access: bool = _setting(False, "bool", False, "Allow remote access",
                                   "Whether people may reach this Row-Bot from other devices.")
    data_export: bool = _setting(True, "bool", False, "Allow data export",
                                 "Whether people may export their conversations and data.")
    install_count: bool = _setting(False, "bool", False, "Allow the install count",
                                   "Whether the anonymous install count may be sent.")


@dataclass(frozen=True)
class Updates:
    """How Row-Bot updates itself."""

    channel: str | None = _setting(None, "enum", None, "Update channel", "stable or beta.",
                                   unset="the person chooses", choices=("stable", "beta"))
    deferral_days: int = _setting(0, "int", 0, "Defer updates (days)",
                                  "Days to wait after a release before offering it.", minimum=0, maximum=365)
    pinned_version: str | None = _setting(None, "text", None, "Pinned version",
                                          "Stay on this version (for example 5.4.0).", unset="none",
                                          pattern=VERSION, max_length=80)
    # Lockdown keeps Row-Bot's signed self-update: it is how the fix for a bad push or a loader bug arrives.
    external: bool = _setting(False, "bool", False, "Updates come from IT",
                              "Row-Bot never updates itself; IT deploys new versions.")


@dataclass(frozen=True)
class Line:
    """The Line between Personal and Work."""

    regulated: bool = _setting(False, "bool", True, "Regulated mode",
                               "Regulated mode for Work. It never changes Personal.")


@dataclass(frozen=True)
class Audit:
    """Where audit events go (placeholders; AUD-1 defines them)."""

    sink: str = _setting("local", "enum", "local", "Audit sink", "local or org_server.",
                         choices=("local", "org_server"))
    redaction: str = _setting("strict", "enum", "strict", "Audit redaction", "strict or standard.",
                              choices=("strict", "standard"))


@dataclass(frozen=True)
class Settings:
    """Every section of a policy."""

    organisation: Organisation = field(default_factory=Organisation)
    approvals: Approvals = field(default_factory=Approvals)
    models: Models = field(default_factory=Models)
    tools: Tools = field(default_factory=Tools)
    channels: Channels = field(default_factory=Channels)
    extensions: Extensions = field(default_factory=Extensions)
    features: Features = field(default_factory=Features)
    updates: Updates = field(default_factory=Updates)
    line: Line = field(default_factory=Line)
    audit: Audit = field(default_factory=Audit)


SECTIONS: dict[str, type] = {f.name: f.default_factory for f in fields(Settings)}  # type: ignore[misc]


def specs(section: str) -> dict[str, Spec]:
    """The settings of *section*, in declaration order."""
    return {f.name: f.metadata["policy"] for f in fields(SECTIONS[section])}


def default(section: str, name: str) -> Any:
    """The value of a setting a policy leaves out."""
    return getattr(SECTIONS[section](), name)


DEFAULTS = Settings()
LOCKDOWN = Settings(**{name: cls(**{key: spec.lockdown for key, spec in specs(name).items()})
                       for name, cls in SECTIONS.items()})


def _value(spec: Spec, raw: object, path: str) -> Any:
    def refuse(what: str) -> InvalidPolicy:
        return InvalidPolicy("invalid", f"{path}: {what}")

    if spec.kind == "bool":
        if type(raw) is not bool:
            raise refuse("expected true or false")
        return raw
    if spec.kind == "int":
        if type(raw) is not int or not spec.minimum <= raw <= spec.maximum:
            raise refuse(f"expected a whole number from {spec.minimum} to {spec.maximum}")
        return raw
    if spec.kind == "enum":
        if type(raw) is not str or raw not in spec.choices:
            raise refuse("expected one of " + ", ".join(spec.choices))
        return raw
    if spec.kind == "list":
        if type(raw) is not list or len(raw) > MAX_LIST_ITEMS:
            raise refuse(f"expected a list of at most {MAX_LIST_ITEMS} entries")
        for index, item in enumerate(raw):
            if type(item) is not str or not re.fullmatch(spec.pattern, item):
                raise InvalidPolicy("invalid", f"{path}[{index}]: not a valid entry")
        return frozenset(raw)
    pattern = URL if spec.kind == "url" else (spec.pattern or TEXT)
    if type(raw) is not str or not 1 <= len(raw) <= spec.max_length or not re.fullmatch(pattern, raw):
        raise refuse("expected an https URL" if spec.kind == "url" else "not a valid value")
    return raw


def _require_object(raw: object, path: str) -> dict[str, Any]:
    if type(raw) is not dict:
        raise InvalidPolicy("invalid", f"{path}: expected an object")
    return raw


def parse(document: object) -> tuple[Settings, bool]:
    """The settings a policy document sets, and whether it requires a signature. Refuses anything not in v1."""
    document = _require_object(document, "policy")
    version = document.get("schema_version")
    if "schema_version" not in document:
        raise InvalidPolicy("invalid", "schema_version: required")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise InvalidPolicy("unsupported_schema_version",
                            f"schema_version: this Row-Bot reads version {SCHEMA_VERSION}, not {version!r}")
    require_signature = document.get("require_signature", False)
    if type(require_signature) is not bool:
        raise InvalidPolicy("invalid", "require_signature: expected true or false")
    sections: dict[str, Any] = {}
    for name, raw in document.items():
        if name in ("schema_version", "require_signature"):
            continue
        if name not in SECTIONS:
            raise InvalidPolicy("invalid", f"{name}: not a policy section")
        values = _require_object(raw, name)
        section_specs = specs(name)
        parsed: dict[str, Any] = {}
        for key, item in values.items():
            if key not in section_specs:
                raise InvalidPolicy("invalid", f"{name}.{key}: not a policy setting")
            parsed[key] = _value(section_specs[key], item, f"{name}.{key}")
        for key, spec in section_specs.items():
            if key in parsed and spec.requires:
                needed = parsed.get(spec.requires)
                if needed is None or (spec.requires_value is not None and needed != spec.requires_value):
                    wanted = f" set to {spec.requires_value}" if spec.requires_value else ""
                    raise InvalidPolicy("invalid", f"{name}.{key}: needs {name}.{spec.requires}{wanted}")
        sections[name] = SECTIONS[name](**parsed)
    return Settings(**sections), require_signature


def parse_keys(document: object) -> tuple[tuple[str, str], ...]:
    """The pinned signing keys of a keys file: ``(id, base64 Ed25519 public key)`` pairs."""
    document = _require_object(document, "keys file")
    if set(document) != {"schema_version", "keys"}:
        raise InvalidPolicy("keys_invalid", "keys file: expected exactly schema_version and keys")
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
        raise InvalidPolicy("keys_invalid", f"keys file: schema_version must be {SCHEMA_VERSION}")
    keys = document["keys"]
    if type(keys) is not list or not 1 <= len(keys) <= 16:
        raise InvalidPolicy("keys_invalid", "keys file: expected 1 to 16 keys")
    pinned = []
    for index, key in enumerate(keys):
        if (type(key) is not dict or set(key) != {"id", "ed25519"} or type(key["id"]) is not str
                or type(key["ed25519"]) is not str or not re.fullmatch(KEY_ID, key["id"])
                or not re.fullmatch(ED25519_KEY, key["ed25519"])):
            raise InvalidPolicy("keys_invalid", f"keys file: keys[{index}] needs an id and a base64 ed25519 key")
        pinned.append((key["id"], key["ed25519"]))
    return tuple(pinned)


# --- JSON Schema ---------------------------------------------------------------------------------------------

def _show(value: Any, spec: Spec) -> str:
    if value is None:
        return spec.unset
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, frozenset):
        return ", ".join(sorted(value)) if value else "[]"
    return str(value)


def defaults_table() -> str:
    """Every setting with its value when a policy leaves it out and its value in lockdown, as a Markdown table."""
    rows = ["| Setting | Not set in a policy | Lockdown |", "| --- | --- | --- |"]
    for section in SECTIONS:
        for name, spec in specs(section).items():
            rows.append(f"| `{section}.{name}` | {_show(default(section, name), spec)} | "
                        f"{_show(spec.lockdown, spec) or 'none'} |")
    return "\n".join(rows)


def _json(value: Any) -> Any:
    return sorted(value) if isinstance(value, frozenset) else value


def property_schema(section: str, name: str) -> dict[str, Any]:
    """The JSON Schema of one setting."""
    spec = specs(section)[name]
    value = default(section, name)
    shown_default = _show(value, spec)
    schema: dict[str, Any] = {
        "title": spec.title,
        "description": f"{spec.help} Not set in a policy: {shown_default}. Lockdown: "
                       f"{_show(spec.lockdown, spec) or 'none'}.",
    }
    if value is not None:
        schema["default"] = _json(value)
    if spec.kind == "bool":
        schema["type"] = "boolean"
    elif spec.kind == "int":
        schema.update(type="integer", minimum=spec.minimum, maximum=spec.maximum)
    elif spec.kind == "enum":
        schema.update(type="string", enum=list(spec.choices))
    elif spec.kind == "list":
        schema.update(type="array", maxItems=MAX_LIST_ITEMS,
                      items={"type": "string", "pattern": f"^(?:{spec.pattern})$"})
    else:
        pattern = URL if spec.kind == "url" else (spec.pattern or TEXT)
        schema.update(type="string", minLength=1, maxLength=spec.max_length, pattern=f"^(?:{pattern})$")
    return schema


def json_schema() -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of an ``org-policy.json``."""
    properties: dict[str, Any] = {
        "schema_version": {"title": "Schema version", "description": "Always 1 for this schema.",
                           "const": SCHEMA_VERSION},
        "require_signature": {"title": "Require a signature",
                              "description": "Refuse this file unless org-policy.json.sig holds a good Ed25519 "
                                             "signature by a key in org-policy-keys.json. Not set: false.",
                              "type": "boolean", "default": False},
    }
    for section, cls in SECTIONS.items():
        section_schema: dict[str, Any] = {
            "title": section.replace("_", " ").capitalize(),
            "description": (cls.__doc__ or "").strip(),
            "type": "object",
            "additionalProperties": False,
            "properties": {name: property_schema(section, name) for name in specs(section)},
        }
        dependent = {}
        for name, spec in specs(section).items():
            if spec.requires:
                rule: dict[str, Any] = {"required": [spec.requires]}
                if spec.requires_value is not None:
                    rule["properties"] = {spec.requires: {"const": spec.requires_value}}
                dependent[name] = rule
        if dependent:
            section_schema["dependentSchemas"] = dependent
        properties[section] = section_schema
    return {
        "$schema": SCHEMA_DIALECT,
        "title": "Row-Bot organisation policy",
        "description": "An organisation's policy for Row-Bot, schema version 1. Unknown keys are refused: a policy "
                       "Row-Bot can't fully read locks Row-Bot down. Every setting a policy leaves out takes the "
                       "value below; lockdown is what an unreadable, invalid or badly signed policy means.\n\n"
                       + defaults_table(),
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version"],
        "properties": properties,
    }


def keys_json_schema() -> dict[str, Any]:
    """The JSON Schema of ``org-policy-keys.json``, the pinned signing keys beside a policy file."""
    return {
        "$schema": SCHEMA_DIALECT,
        "title": "Row-Bot organisation policy signing keys",
        "description": "Ed25519 public keys that may sign org-policy.json. While this file is present, an unsigned "
                       "policy file is refused.",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "keys"],
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "keys": {
                "type": "array", "minItems": 1, "maxItems": 16,
                "items": {
                    "type": "object", "additionalProperties": False, "required": ["id", "ed25519"],
                    "properties": {
                        "id": {"type": "string", "pattern": f"^(?:{KEY_ID})$",
                               "description": "Names the signer in Settings."},
                        "ed25519": {"type": "string", "pattern": f"^(?:{ED25519_KEY})$",
                                    "description": "The raw 32-byte public key, base64."},
                    },
                },
            },
        },
    }
