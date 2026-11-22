"""The closed audit vocabulary (v1): categories, actions, who may act, where, on what, and each action's
``details`` keys with the redaction level each key needs.

Only company-governed actions are audited: sign-ins and devices, model calls through the gateway, tool actions
under company policy, admin changes, documents, shares, the library and exports. A person's own chats are not
audited, and nothing here can name a personal layer: ``AuditLayer`` is ``team | company | org``.

Outcomes and deciders reuse the person's activity log (``row_bot.activity_log``): its ``ran``,
``approved_by_you``, ``approved_by_reviewer``, ``hands_off``, ``allowed_by_rule`` and ``allowed_in_chat`` are all
``succeeded`` here, with who let it in ``decided_by``; ``denied``, ``changed``, ``blocked`` and ``failed`` keep
their names. ``decided_by`` and the approval levels are the activity log's ``DECIDERS`` and ``LEVELS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from .jcs import MAX_SAFE_INTEGER


class RedactionLevel(StrEnum):
    """How much an event may say (policy ``audit.redaction``). ``metadata``, the default, holds no free text at
    all; ``summaries`` adds a short masked summary of a tool action. No level ever holds chat content or a
    secret."""

    METADATA = "metadata"
    SUMMARIES = "summaries"


LEVEL_RANK = MappingProxyType({RedactionLevel.METADATA: 0, RedactionLevel.SUMMARIES: 1})


class AuditLayer(StrEnum):
    """Which layer an event concerns: a team space, the company layer, or the organisation's administration
    (sign-in, devices, models, tools, policy). There is deliberately no personal layer."""

    TEAM = "team"
    COMPANY = "company"
    ORG = "org"


class ActorKind(StrEnum):
    """Who acted: a person, an admin in the console, or a service (the Company Server's own workers)."""

    PERSON = "person"
    ADMIN = "admin"
    SERVICE = "service"


class Outcome(StrEnum):
    """How it ended: ``denied`` means a person or reviewer said no, ``blocked`` a rule or policy refused it,
    ``changed`` the person changed the call before it ran."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CHANGED = "changed"
    BLOCKED = "blocked"


class DecidedBy(StrEnum):
    """Who let a tool action run, or stopped it (the activity log's deciders)."""

    NONE = "none"
    PERSON = "person"
    REVIEWER = "reviewer"
    HANDS_OFF = "hands_off"
    RULE = "rule"
    CHAT_GRANT = "chat_grant"
    BLOCK = "block"
    POLICY = "policy"


class TargetKind(StrEnum):
    """What an event acted on; the target's ``ref`` is an opaque id of that kind."""

    TOOL = "tool"
    PERSON = "person"
    DEVICE = "device"
    INTEGRATION = "integration"
    MCP_SERVER = "mcp_server"
    PLUGIN = "plugin"
    CHANNEL = "channel"
    DOCUMENT = "document"
    SHARE = "share"
    LIBRARY_ITEM = "library_item"
    EXPORT = "export"
    SETTING = "setting"


class Category(StrEnum):
    """An action's family: the part of its name before the dot."""

    SESSION = "session"
    DEVICE = "device"
    MODEL = "model"
    TOOL = "tool"
    POLICY = "policy"
    APP = "app"
    DOCUMENT = "document"
    SHARE = "share"
    LIBRARY = "library"
    DATA = "data"
    ROLE = "role"
    ORG_SETTINGS = "org_settings"


class Action(StrEnum):
    """Every v1 audit action. A new one is a schema change."""

    SESSION_SIGNED_IN = "session.signed_in"
    SESSION_SIGNED_OUT = "session.signed_out"
    SESSION_REVOKED = "session.revoked"
    DEVICE_JOINED = "device.joined"
    DEVICE_REMOVED = "device.removed"
    MODEL_CALLED = "model.called"
    TOOL_CALLED = "tool.called"
    POLICY_CHANGED = "policy.changed"
    APP_APPROVED = "app.approved"
    APP_PUSHED = "app.pushed"
    APP_REMOVED = "app.removed"
    DOCUMENT_UPLOADED = "document.uploaded"
    DOCUMENT_REMOVED = "document.removed"
    SHARE_SUBMITTED = "share.submitted"
    SHARE_APPROVED = "share.approved"
    SHARE_REJECTED = "share.rejected"
    LIBRARY_PUBLISHED = "library.published"
    DATA_EXPORTED = "data.exported"
    ROLE_CHANGED = "role.changed"
    ORG_SETTINGS_CHANGED = "org_settings.changed"


# The forms an identifier may take. Ids match CON-1's (no "/", so no path can pass as one); model ids may hold "/".
ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:@|+-]{0,127}"
MODEL_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,127}"
ORG_ID_PATTERN = r"[a-z0-9][a-z0-9._-]{0,63}"  # The policy's organisation id (POL-1 ``ORG_ID``).
VERSION_PATTERN = r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}"
HASH_PATTERN = r"[0-9a-f]{64}"  # SHA-256, lowercase hex.
EVENT_ID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"  # A random UUID.
TIME_PATTERN = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z"  # UTC, microseconds, Z: one spelling per instant.

SUMMARY_MAX = 300  # As the activity log's summaries.

DetailKind = Literal["id", "model", "version", "hash", "enum", "int", "text"]
PATTERNS = MappingProxyType({"id": ID_PATTERN, "model": MODEL_PATTERN, "version": VERSION_PATTERN,
                             "hash": HASH_PATTERN})


@dataclass(frozen=True)
class Detail:
    """One ``details`` key: its kind, whether it is required, and the redaction level it needs."""

    kind: DetailKind
    required: bool = False
    level: RedactionLevel = RedactionLevel.METADATA
    choices: tuple[str, ...] = ()
    minimum: int = 0
    maximum: int = MAX_SAFE_INTEGER


@dataclass(frozen=True)
class ActionSpec:
    """What an action allows: actors, layers, targets (none means it has no target), outcomes, details, and
    whether it records who decided (approvals)."""

    actors: frozenset[ActorKind]
    layers: frozenset[AuditLayer]
    targets: frozenset[TargetKind]
    outcomes: frozenset[Outcome]
    details: MappingProxyType[str, Detail] = field(default_factory=lambda: MappingProxyType({}))
    decided_by: bool = False


def category_of(action: Action) -> Category:
    """The category an action belongs to."""
    return Category(action.value.split(".", 1)[0])


# Values the details' enums take. Levels, surfaces and risks are the activity log's; item kinds are CON-1's.
APPROVAL_LEVELS = ("block", "approve", "auto", "full")
SURFACES = ("chat", "workflow", "agent", "goal", "channel", "proactive", "developer")
RISKS = ("low", "medium", "high", "critical", "unknown")
ITEM_KINDS = ("fact", "relation", "skill", "source")  # CON-1 ``ItemKind``.
LIBRARY_KINDS = ("skill", "agent", "workflow")
# ``person``: one person's chats and memory (their own export where policy allows, or an admin's at offboarding).
EXPORT_SCOPES = ("person", "space", "company", "audit")
ROLES = ("admin", "member", "space_owner")

_P, _A, _S = ActorKind.PERSON, ActorKind.ADMIN, ActorKind.SERVICE
_TEAM, _COMPANY, _ORG = AuditLayer.TEAM, AuditLayer.COMPANY, AuditLayer.ORG
_SHARED = {_TEAM, _COMPANY}
_DONE = frozenset({Outcome.SUCCEEDED, Outcome.FAILED})
_GATED = frozenset({Outcome.SUCCEEDED, Outcome.FAILED, Outcome.DENIED, Outcome.BLOCKED})
_APPS = {TargetKind.INTEGRATION, TargetKind.MCP_SERVER, TargetKind.PLUGIN, TargetKind.CHANNEL}


def _spec(actors: set[ActorKind], layers: set[AuditLayer], targets: set[TargetKind], outcomes: frozenset[Outcome],
          decided_by: bool = False, **details: Detail) -> ActionSpec:
    return ActionSpec(frozenset(actors), frozenset(layers), frozenset(targets), outcomes,
                      MappingProxyType(details), decided_by)


def _enum(*choices: str, required: bool = True) -> Detail:
    return Detail("enum", required=required, choices=choices)


_REQ = {"required": True}
_SPACE = {"space_id": Detail("id", **_REQ)}
_SHARE = {"item_kind": _enum(*ITEM_KINDS), **_SPACE}
_APP = {"version": Detail("version")}

SPECS: MappingProxyType[Action, ActionSpec] = MappingProxyType({
    Action.SESSION_SIGNED_IN: _spec({_P, _A}, {_ORG}, set(), _GATED),
    Action.SESSION_SIGNED_OUT: _spec({_P, _A}, {_ORG}, set(), _DONE, reason=_enum("signed_out", "expired")),
    Action.SESSION_REVOKED: _spec({_A, _S}, {_ORG}, {TargetKind.PERSON}, _DONE),
    Action.DEVICE_JOINED: _spec(
        {_P}, {_ORG}, {TargetKind.DEVICE}, _GATED,
        platform=_enum("windows", "macos", "linux"), app_version=Detail("version", **_REQ)),
    Action.DEVICE_REMOVED: _spec({_P, _A, _S}, {_ORG}, {TargetKind.DEVICE}, _DONE, reason=_enum("left", "revoked")),
    Action.MODEL_CALLED: _spec(
        {_P, _S}, {_ORG}, set(), _GATED,
        provider=Detail("id", **_REQ), model=Detail("model", **_REQ), input_tokens=Detail("int", **_REQ),
        output_tokens=Detail("int", **_REQ), cache_read_tokens=Detail("int"), duration_ms=Detail("int")),
    Action.TOOL_CALLED: _spec(
        {_P}, {_ORG}, {TargetKind.TOOL},
        frozenset({Outcome.SUCCEEDED, Outcome.FAILED, Outcome.DENIED, Outcome.CHANGED, Outcome.BLOCKED}),
        decided_by=True,
        approval_level=_enum(*APPROVAL_LEVELS), surface=_enum(*SURFACES), risk=_enum(*RISKS, required=False),
        app=Detail("id"), rule_id=Detail("id"),
        summary=Detail("text", level=RedactionLevel.SUMMARIES)),
    Action.POLICY_CHANGED: _spec(
        {_A}, {_ORG}, set(), _GATED,
        digest=Detail("hash", **_REQ), previous_digest=Detail("hash"),
        schema_version=Detail("int", **_REQ, minimum=1, maximum=1_000), group=Detail("id")),
    Action.APP_APPROVED: _spec({_A}, {_ORG}, _APPS, _GATED, **_APP),
    Action.APP_PUSHED: _spec({_A}, {_ORG}, _APPS, _GATED, **_APP),
    Action.APP_REMOVED: _spec({_A}, {_ORG}, _APPS, _DONE, **_APP),
    Action.DOCUMENT_UPLOADED: _spec(
        {_P, _A}, _SHARED, {TargetKind.DOCUMENT}, _GATED, byte_count=Detail("int"), **_SPACE),
    Action.DOCUMENT_REMOVED: _spec({_P, _A}, _SHARED, {TargetKind.DOCUMENT}, _DONE, **_SPACE),
    Action.SHARE_SUBMITTED: _spec({_P}, _SHARED, {TargetKind.SHARE}, _GATED, **_SHARE),
    Action.SHARE_APPROVED: _spec({_P, _A}, _SHARED, {TargetKind.SHARE}, _DONE, **_SHARE),
    Action.SHARE_REJECTED: _spec({_P, _A}, _SHARED, {TargetKind.SHARE}, _DONE, **_SHARE),
    Action.LIBRARY_PUBLISHED: _spec(
        {_P, _A, _S}, _SHARED, {TargetKind.LIBRARY_ITEM}, _GATED,
        item_kind=_enum(*LIBRARY_KINDS), version=Detail("version", **_REQ)),
    Action.DATA_EXPORTED: _spec(
        {_P, _A, _S}, {_TEAM, _COMPANY, _ORG}, {TargetKind.EXPORT}, _GATED,
        scope=_enum(*EXPORT_SCOPES), item_count=Detail("int"), byte_count=Detail("int")),
    Action.ROLE_CHANGED: _spec(
        {_A}, {_TEAM, _COMPANY, _ORG}, {TargetKind.PERSON}, _GATED,
        role=_enum(*ROLES), change=_enum("granted", "removed"), space_id=Detail("id")),
    Action.ORG_SETTINGS_CHANGED: _spec({_A}, {_ORG}, {TargetKind.SETTING}, _GATED),
})
