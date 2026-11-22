"""The closed audit vocabulary (v1): categories, actions, who may act, where, on what, and each action's
``details`` keys with the redaction level each key needs.

Nothing here can name the ``personal`` layer: ``AuditLayer`` has no such member, no action's details hold Personal
content or metadata, and Personal-layer actions have no action at all. The only Personal-facing facts are the ones
LINE-CRYPTO L4 / §13.3 lets the org learn: moves across the Line as counts and dates (``line.items_moved``) and
"Personal export made: yes/no" (``departure.completed``).

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
    all; ``summaries`` adds a short masked summary of a Work tool action; ``full`` adds its masked arguments.
    No level ever holds Personal content or a secret."""

    METADATA = "metadata"
    SUMMARIES = "summaries"
    FULL = "full"


LEVEL_RANK = MappingProxyType({RedactionLevel.METADATA: 0, RedactionLevel.SUMMARIES: 1, RedactionLevel.FULL: 2})


class AuditLayer(StrEnum):
    """Which layer an event concerns. There is deliberately no ``personal``: a Personal event can't be built."""

    WORK = "work"
    TEAM = "team"
    COMPANY = "company"
    ORG = "org"


class ActorKind(StrEnum):
    """Who acted: a person, a service (the Org server's workers, SCIM), an org admin, or a device on its own."""

    PERSON = "person"
    SERVICE = "service"
    ADMIN = "admin"
    DEVICE = "device"


class Outcome(StrEnum):
    """How it ended: ``denied`` means a person or reviewer said no, ``blocked`` a rule or policy refused it,
    ``changed`` the person changed the call before it ran."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CHANGED = "changed"
    BLOCKED = "blocked"


class DecidedBy(StrEnum):
    """Who let a Work tool action run, or stopped it (the activity log's deciders)."""

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
    PLUGIN = "plugin"
    MCP_SERVER = "mcp_server"
    SKILL = "skill"
    PROMOTION = "promotion"
    EXPORT = "export"
    SPACE = "space"
    ORG = "org"
    SETTING = "setting"
    AUDIT_CHAIN = "audit_chain"


class Category(StrEnum):
    """An action's family: the part of its name before the dot."""

    TOOL = "tool"
    MODEL = "model"
    POLICY = "policy"
    SESSION = "session"
    DEVICE = "device"
    EXTENSION = "extension"
    PROMOTION = "promotion"
    EXPORT = "export"
    ESCROW = "escrow"
    LEGAL_HOLD = "legal_hold"
    DEPARTURE = "departure"
    LINE = "line"
    ORG_SETTINGS = "org_settings"
    AUDIT_CHAIN = "audit_chain"


class Action(StrEnum):
    """Every v1 audit action. A new one is a schema change. ``escrow.opened`` is written before the recovery
    worker's KMS call (LINE-CRYPTO §7.5), so it records an unwrap about to happen."""

    TOOL_CALLED = "tool.called"
    MODEL_CALLED = "model.called"
    POLICY_LOADED = "policy.loaded"
    POLICY_CHANGED = "policy.changed"
    POLICY_LOCKED_DOWN = "policy.locked_down"
    SESSION_SIGNED_IN = "session.signed_in"
    SESSION_SIGNED_OUT = "session.signed_out"
    DEVICE_ENROLLED = "device.enrolled"
    DEVICE_REVOKED = "device.revoked"
    EXTENSION_INSTALLED = "extension.installed"
    EXTENSION_REMOVED = "extension.removed"
    PROMOTION_SUBMITTED = "promotion.submitted"
    PROMOTION_REVIEWED = "promotion.reviewed"
    PROMOTION_PUBLISHED = "promotion.published"
    PROMOTION_REJECTED = "promotion.rejected"
    EXPORT_STARTED = "export.started"
    EXPORT_COMPLETED = "export.completed"
    ESCROW_REQUESTED = "escrow.requested"
    ESCROW_REVIEWED = "escrow.reviewed"
    ESCROW_OPENED = "escrow.opened"
    ESCROW_NOTICE_DEFERRED = "escrow.notice_deferred"
    LEGAL_HOLD_SET = "legal_hold.set"
    LEGAL_HOLD_RELEASED = "legal_hold.released"
    DEPARTURE_STARTED = "departure.started"
    DEPARTURE_COMPLETED = "departure.completed"
    LINE_ITEMS_MOVED = "line.items_moved"
    ORG_SETTINGS_CHANGED = "org_settings.changed"
    AUDIT_CHAIN_VERIFIED = "audit_chain.verified"
    AUDIT_CHAIN_BROKEN = "audit_chain.broken"


# The forms an identifier may take. Ids match CON-1's (no "/", so no path can pass as one); model ids may hold "/".
ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:@|+-]{0,127}"
MODEL_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,127}"
ORG_ID_PATTERN = r"[a-z0-9][a-z0-9._-]{0,63}"  # The policy's organisation id (POL-1 ``ORG_ID``).
VERSION_PATTERN = r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}"
HASH_PATTERN = r"[0-9a-f]{64}"  # SHA-256, lowercase hex.
LABEL_PATTERN = r"[^\x00-\x1f\x7f]{1,64}"  # An admin's short label (the policy's ``policy_version``).
EVENT_ID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"  # A random UUID.
TIME_PATTERN = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z"  # UTC, microseconds, Z: one spelling per instant.

SUMMARY_MAX = 300  # As the activity log's summaries.
ARGUMENTS_MAX_BYTES = 8_192
ARGUMENTS_MAX_DEPTH = 8

DetailKind = Literal["id", "model", "version", "hash", "label", "enum", "int", "bool", "text", "json"]
PATTERNS = MappingProxyType({"id": ID_PATTERN, "model": MODEL_PATTERN, "version": VERSION_PATTERN,
                             "hash": HASH_PATTERN, "label": LABEL_PATTERN})


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


# Values the details' enums take. Levels, surfaces and risks are the activity log's; policy sources and lockdown
# reasons are POL-1's.
APPROVAL_LEVELS = ("block", "approve", "auto", "full")
SURFACES = ("chat", "workflow", "agent", "goal", "channel", "proactive", "developer")
RISKS = ("low", "medium", "high", "critical", "unknown")
POLICY_SOURCES = ("registry", "managed_preferences", "file", "env_file", "org_server")
LOCKDOWN_REASONS = ("unreadable", "untrusted_location", "invalid", "unsupported_schema_version", "keys_invalid",
                    "signature_missing", "signature_invalid", "signature_key_missing",
                    "unsigned_source_with_pinned_keys")
ITEM_KINDS = ("fact", "relation", "skill", "source")  # CON-1 ``ItemKind``.

_P, _S, _A, _D = ActorKind.PERSON, ActorKind.SERVICE, ActorKind.ADMIN, ActorKind.DEVICE
_WORK, _TEAM, _COMPANY, _ORG = AuditLayer.WORK, AuditLayer.TEAM, AuditLayer.COMPANY, AuditLayer.ORG
_DONE = frozenset({Outcome.SUCCEEDED, Outcome.FAILED})
_GATED = frozenset({Outcome.SUCCEEDED, Outcome.FAILED, Outcome.DENIED, Outcome.BLOCKED})
_EXTENSIONS = frozenset({TargetKind.PLUGIN, TargetKind.MCP_SERVER, TargetKind.SKILL})


def _spec(actors: set[ActorKind], layers: set[AuditLayer], targets: set[TargetKind], outcomes: frozenset[Outcome],
          decided_by: bool = False, **details: Detail) -> ActionSpec:
    return ActionSpec(frozenset(actors), frozenset(layers), frozenset(targets), outcomes,
                      MappingProxyType(details), decided_by)


def _enum(*choices: str, required: bool = True) -> Detail:
    return Detail("enum", required=required, choices=choices)


_REQ = {"required": True}
_POLICY_DETAILS = {
    "source": _enum(*POLICY_SOURCES),
    "schema_version": Detail("int", **_REQ, minimum=1, maximum=1_000),
    "digest": Detail("hash", **_REQ),
    "policy_version": Detail("label"),
    "signer": Detail("id"),
}
_PROMOTION = {"item_kind": _enum(*ITEM_KINDS), "space_id": Detail("id", **_REQ)}
_EXPORT_SCOPES = ("work", "team_space", "company", "audit")
_HOLD = {"hold_id": Detail("id", **_REQ)}
_CHAIN = {"links_checked": Detail("int", **_REQ), "head": Detail("hash", **_REQ)}

SPECS: MappingProxyType[Action, ActionSpec] = MappingProxyType({
    Action.TOOL_CALLED: _spec(
        {_P}, {_WORK}, {TargetKind.TOOL},
        frozenset({Outcome.SUCCEEDED, Outcome.FAILED, Outcome.DENIED, Outcome.CHANGED, Outcome.BLOCKED}),
        decided_by=True,
        approval_level=_enum(*APPROVAL_LEVELS), surface=_enum(*SURFACES), risk=_enum(*RISKS, required=False),
        app=Detail("id"), rule_id=Detail("id"),
        summary=Detail("text", level=RedactionLevel.SUMMARIES),
        arguments=Detail("json", level=RedactionLevel.FULL)),
    Action.MODEL_CALLED: _spec(
        {_P}, {_WORK}, set(), _DONE,
        provider=Detail("id", **_REQ), model=Detail("model", **_REQ), input_tokens=Detail("int", **_REQ),
        output_tokens=Detail("int", **_REQ), cache_read_tokens=Detail("int"), duration_ms=Detail("int")),
    Action.POLICY_LOADED: _spec({_D}, {_ORG}, set(), _DONE, **_POLICY_DETAILS),
    Action.POLICY_CHANGED: _spec({_D, _A}, {_ORG}, set(), _DONE, previous_digest=Detail("hash"), **_POLICY_DETAILS),
    Action.POLICY_LOCKED_DOWN: _spec(
        {_D}, {_ORG}, set(), frozenset({Outcome.SUCCEEDED}),
        source=_enum(*POLICY_SOURCES), reason=_enum(*LOCKDOWN_REASONS), digest=Detail("hash")),
    Action.SESSION_SIGNED_IN: _spec({_P, _A}, {_ORG}, set(), _GATED, method=_enum("oidc", "device_code", "refresh")),
    Action.SESSION_SIGNED_OUT: _spec(
        {_P, _A, _S}, {_ORG}, set(), _DONE, reason=_enum("signed_out", "expired", "revoked", "departure")),
    Action.DEVICE_ENROLLED: _spec(
        {_P, _A}, {_ORG}, {TargetKind.DEVICE}, _GATED,
        platform=_enum("windows", "macos", "linux", "server"), app_version=Detail("version", **_REQ)),
    Action.DEVICE_REVOKED: _spec(
        {_P, _A, _S}, {_ORG}, {TargetKind.DEVICE}, _DONE,
        reason=_enum("lost", "replaced", "departure", "admin", "compromised")),
    Action.EXTENSION_INSTALLED: _spec(
        {_P, _A}, {_WORK, _ORG}, set(_EXTENSIONS), _GATED,
        version=Detail("version"), origin=_enum("org_catalog", "marketplace", "local", required=False)),
    Action.EXTENSION_REMOVED: _spec({_P, _A}, {_WORK, _ORG}, set(_EXTENSIONS), _DONE, version=Detail("version")),
    Action.PROMOTION_SUBMITTED: _spec({_P}, {_TEAM, _COMPANY}, {TargetKind.PROMOTION}, _GATED, **_PROMOTION),
    Action.PROMOTION_REVIEWED: _spec({_P, _A}, {_TEAM, _COMPANY}, {TargetKind.PROMOTION}, _DONE, **_PROMOTION),
    Action.PROMOTION_PUBLISHED: _spec({_P, _A, _S}, {_TEAM, _COMPANY}, {TargetKind.PROMOTION}, _DONE, **_PROMOTION),
    Action.PROMOTION_REJECTED: _spec({_P, _A}, {_TEAM, _COMPANY}, {TargetKind.PROMOTION}, _DONE, **_PROMOTION),
    Action.EXPORT_STARTED: _spec(
        {_P, _A, _S}, {_WORK, _TEAM, _COMPANY, _ORG}, {TargetKind.EXPORT}, _GATED, scope=_enum(*_EXPORT_SCOPES)),
    Action.EXPORT_COMPLETED: _spec(
        {_P, _A, _S}, {_WORK, _TEAM, _COMPANY, _ORG}, {TargetKind.EXPORT}, _DONE,
        scope=_enum(*_EXPORT_SCOPES), item_count=Detail("int"), byte_count=Detail("int")),
    Action.ESCROW_REQUESTED: _spec(
        {_A}, {_WORK}, {TargetKind.PERSON}, _GATED,
        request_id=Detail("id", **_REQ), reason=_enum("legal_hold", "departure_handover", "reseal", "person_restore"),
        wk_version_first=Detail("int", **_REQ), wk_version_last=Detail("int", **_REQ)),
    Action.ESCROW_REVIEWED: _spec(
        {_A}, {_WORK}, {TargetKind.PERSON}, frozenset({Outcome.SUCCEEDED, Outcome.DENIED}),
        request_id=Detail("id", **_REQ)),
    Action.ESCROW_OPENED: _spec(
        {_S}, {_WORK}, {TargetKind.PERSON}, _DONE,
        request_id=Detail("id", **_REQ), wk_version=Detail("int", **_REQ), package_id=Detail("id", **_REQ),
        kms_key=Detail("id", **_REQ)),
    Action.ESCROW_NOTICE_DEFERRED: _spec(
        {_A}, {_WORK}, {TargetKind.PERSON}, _DONE,
        request_id=Detail("id", **_REQ), days=Detail("int", **_REQ, minimum=1, maximum=90)),
    Action.LEGAL_HOLD_SET: _spec(
        {_A}, {_WORK, _TEAM, _COMPANY}, {TargetKind.PERSON, TargetKind.SPACE, TargetKind.ORG}, _DONE, **_HOLD),
    Action.LEGAL_HOLD_RELEASED: _spec(
        {_A}, {_WORK, _TEAM, _COMPANY}, {TargetKind.PERSON, TargetKind.SPACE, TargetKind.ORG}, _DONE, **_HOLD),
    Action.DEPARTURE_STARTED: _spec(
        {_A, _S}, {_ORG}, {TargetKind.PERSON}, _DONE,
        trigger=_enum("scim", "admin"), window_days=Detail("int", minimum=1, maximum=365)),
    Action.DEPARTURE_COMPLETED: _spec(
        {_A, _S}, {_ORG}, {TargetKind.PERSON}, _DONE,
        personal_export_made=Detail("bool", **_REQ), handover=_enum("released_by_person", "escrow_drafted", "none")),
    Action.LINE_ITEMS_MOVED: _spec(
        {_P}, {_WORK}, set(), frozenset({Outcome.SUCCEEDED}),
        direction=_enum("to_personal", "to_work"), count=Detail("int", **_REQ, minimum=1),
        during_departure=Detail("bool", **_REQ)),
    Action.ORG_SETTINGS_CHANGED: _spec({_A}, {_ORG}, {TargetKind.SETTING}, _GATED),
    Action.AUDIT_CHAIN_VERIFIED: _spec({_S, _A, _D}, {_ORG}, {TargetKind.AUDIT_CHAIN}, frozenset({Outcome.SUCCEEDED}),
                                       **_CHAIN),
    Action.AUDIT_CHAIN_BROKEN: _spec(
        {_S, _A, _D}, {_ORG}, {TargetKind.AUDIT_CHAIN}, frozenset({Outcome.SUCCEEDED}),
        **_CHAIN, broken_at=Detail("int", **_REQ), reason=_enum("invalid", "prev_hash", "seq", "hash", "head")),
})
