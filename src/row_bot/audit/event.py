"""The audit record: ``AuditEvent``, its strict JSON mapping, and ``redact``.

An event checks itself when it is built and again whenever it is written out (``to_dict``, which hashing, the
schemas and OCSF all go through), so no code path can serialise one that breaks the vocabulary: an unknown
action, a ``personal`` layer, a detail key the action doesn't define, or a key its redaction level doesn't allow
are refused, not filtered. Free text is masked for secrets when the event is built; an identifier that looks like
a secret is refused.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any

from .jcs import MAX_SAFE_INTEGER, canonical_json
from .masking import looks_like_secret, mask_json, mask_text
from .vocabulary import (
    ARGUMENTS_MAX_BYTES, ARGUMENTS_MAX_DEPTH, EVENT_ID_PATTERN, ID_PATTERN, LEVEL_RANK, ORG_ID_PATTERN, PATTERNS,
    SPECS, SUMMARY_MAX, TIME_PATTERN, Action, ActorKind, AuditLayer, Category, DecidedBy, Detail, Outcome,
    RedactionLevel, TargetKind, category_of,
)


class InvalidAuditEvent(ValueError):
    """An audit record, or its JSON, that breaks the contract."""


_EVENT_ID = re.compile(EVENT_ID_PATTERN)
_ID = re.compile(ID_PATTERN)
_ORG_ID = re.compile(ORG_ID_PATTERN)
_TIME = re.compile(TIME_PATTERN)
_DETAIL_PATTERNS = {kind: re.compile(pattern) for kind, pattern in PATTERNS.items()}


def new_event_id() -> str:
    """A fresh random event id (UUID version 4). Ids are never sequential: a counter would leak volumes."""
    return str(uuid.uuid4())


def format_time(value: datetime) -> str:
    """A UTC time as ISO-8601 with microseconds and ``Z``: the one spelling the contract accepts."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_time(value: object, path: str) -> datetime:
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise InvalidAuditEvent(f"{path}: must be a UTC time written as YYYY-MM-DDTHH:MM:SS.ffffffZ")
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except ValueError:
        raise InvalidAuditEvent(f"{path}: is not a real time") from None


def _time(value: object, path: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise InvalidAuditEvent(f"{path}: must be a timezone-aware UTC time")
    return value.astimezone(timezone.utc)


def _enum(cls: Any, value: object, path: str) -> Any:
    try:
        return cls(value)
    except (TypeError, ValueError):
        raise InvalidAuditEvent(f"{path}: must be one of {', '.join(member.value for member in cls)}") from None


def _unicode(value: str, path: str) -> str:
    if "\x00" in value:
        raise InvalidAuditEvent(f"{path}: may not contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidAuditEvent(f"{path}: must be valid Unicode (no lone surrogates)") from None
    return value


def _identifier(value: object, pattern: re.Pattern[str], path: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise InvalidAuditEvent(f"{path}: has the wrong form")
    _unicode(value, path)
    if looks_like_secret(value):
        raise InvalidAuditEvent(f"{path}: looks like a secret")
    return value


def _optional_id(value: object, path: str) -> str | None:
    return None if value is None else _identifier(value, _ID, path)


@dataclass(frozen=True)
class Actor:
    """Who acted: a principal id and its kind."""

    kind: ActorKind
    id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(ActorKind, self.kind, "actor.kind"))
        _identifier(self.id, _ID, "actor.id")


@dataclass(frozen=True)
class Target:
    """What was acted on: its kind and an opaque reference."""

    kind: TargetKind
    ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(TargetKind, self.kind, "target.kind"))
        _identifier(self.ref, _ID, "target.ref")


def _json_value(value: object, depth: int, path: str) -> object:
    if depth > ARGUMENTS_MAX_DEPTH:
        raise InvalidAuditEvent(f"{path}: nested too deeply")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise InvalidAuditEvent(f"{path}: integer out of range")
        return value
    if isinstance(value, float):
        raise InvalidAuditEvent(f"{path}: numbers must be integers (write other numbers as text)")
    if isinstance(value, str):
        return _unicode(value, path)
    if isinstance(value, (list, tuple)):
        return tuple(_json_value(item, depth + 1, f"{path}[{index}]") for index, item in enumerate(value))
    if isinstance(value, Mapping):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidAuditEvent(f"{path}: keys must be text")
            out[_unicode(key, path)] = _json_value(item, depth + 1, f"{path}.{key}")
        return MappingProxyType(out)
    raise InvalidAuditEvent(f"{path}: must be JSON")


def _thaw(value: object) -> object:
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    return value


def _detail(spec: Detail, value: object, path: str) -> object:
    kind = spec.kind
    if kind in _DETAIL_PATTERNS:
        return _identifier(value, _DETAIL_PATTERNS[kind], path)
    if kind == "enum":
        if value not in spec.choices:
            raise InvalidAuditEvent(f"{path}: must be one of {', '.join(spec.choices)}")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise InvalidAuditEvent(f"{path}: must be true or false")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidAuditEvent(f"{path}: must be an integer")
        if not spec.minimum <= value <= spec.maximum:
            raise InvalidAuditEvent(f"{path}: must be from {spec.minimum} to {spec.maximum}")
        return value
    if kind == "text":
        if not isinstance(value, str) or not 1 <= len(value) <= SUMMARY_MAX:
            raise InvalidAuditEvent(f"{path}: must be 1 to {SUMMARY_MAX} characters")
        return mask_text(_unicode(value, path))
    # json: a JSON object, masked, then bounded in its canonical size.
    if not isinstance(value, Mapping):
        raise InvalidAuditEvent(f"{path}: must be a JSON object")
    frozen = _json_value(mask_json(_thaw(_json_value(value, 1, path))), 1, path)
    if len(canonical_json(_thaw(frozen))) > ARGUMENTS_MAX_BYTES:
        raise InvalidAuditEvent(f"{path}: at most {ARGUMENTS_MAX_BYTES} bytes as canonical JSON")
    return frozen


def _details(action: Action, level: RedactionLevel, details: object) -> MappingProxyType[str, object]:
    if not isinstance(details, Mapping):
        raise InvalidAuditEvent("details: must be an object")
    allowed = SPECS[action].details
    if unknown := sorted(str(key) for key in details if key not in allowed):
        raise InvalidAuditEvent(f"details.{unknown[0]}: not a detail of {action.value}")
    if missing := sorted(key for key, spec in allowed.items() if spec.required and key not in details):
        raise InvalidAuditEvent(f"details.{missing[0]}: required for {action.value}")
    out = {}
    for key in sorted(details):
        spec = allowed[key]
        if LEVEL_RANK[spec.level] > LEVEL_RANK[level]:
            raise InvalidAuditEvent(f"details.{key}: needs redaction level {spec.level.value} or above")
        out[key] = _detail(spec, details[key], f"details.{key}")
    return MappingProxyType(out)


@dataclass(frozen=True)
class AuditEvent:
    """One org audit event. ``category`` follows from ``action``; ``decided_by`` is set exactly for approvals
    (``tool.called``); ``device_id`` is required when a device is the actor; ``details`` holds only the keys the
    action defines at or below ``redaction``. ``occurred_at`` is when it happened (the acting party's clock),
    ``recorded_at`` when the recording party wrote it."""

    id: str
    occurred_at: datetime
    recorded_at: datetime
    org_id: str
    actor: Actor
    action: Action
    outcome: Outcome
    layer: AuditLayer
    target: Target | None = None
    device_id: str | None = None
    decided_by: DecidedBy | None = None
    turn_id: str | None = None
    request_id: str | None = None
    redaction: RedactionLevel = RedactionLevel.METADATA
    details: Mapping[str, Any] = field(default_factory=dict)
    category: Category = field(init=False)

    def __post_init__(self) -> None:
        _check(self, build=True)


def _check(event: AuditEvent, *, build: bool) -> None:
    def put(name: str, value: object) -> None:
        if build:
            object.__setattr__(event, name, value)
        elif type(getattr(event, name)) is not type(value) or getattr(event, name) != value:
            raise InvalidAuditEvent(f"{name}: changed after it was built")

    if not isinstance(event.id, str) or not _EVENT_ID.fullmatch(event.id):
        raise InvalidAuditEvent("id: must be a random UUID (version 4), lowercase")
    put("occurred_at", _time(event.occurred_at, "occurred_at"))
    put("recorded_at", _time(event.recorded_at, "recorded_at"))
    if not isinstance(event.org_id, str) or not _ORG_ID.fullmatch(event.org_id):
        raise InvalidAuditEvent("org_id: has the wrong form")
    if not isinstance(event.actor, Actor):
        raise InvalidAuditEvent("actor: must be an Actor")
    Actor(event.actor.kind, event.actor.id)  # Re-checked: frozen records can still be forced.
    if type(event.actor.kind) is not ActorKind:
        raise InvalidAuditEvent("actor.kind: changed after it was built")
    action = _enum(Action, event.action, "action")
    put("action", action)
    put("outcome", _enum(Outcome, event.outcome, "outcome"))
    put("layer", _enum(AuditLayer, event.layer, "layer"))
    put("redaction", _enum(RedactionLevel, event.redaction, "redaction"))
    if build:
        object.__setattr__(event, "category", category_of(action))
    elif event.category != category_of(action):
        raise InvalidAuditEvent("category: doesn't match the action")
    spec = SPECS[action]
    if event.actor.kind not in spec.actors:
        raise InvalidAuditEvent(f"actor.kind: {event.actor.kind.value} can't perform {action.value}")
    if event.layer not in spec.layers:
        raise InvalidAuditEvent(f"layer: {action.value} happens only in {', '.join(sorted(spec.layers))}")
    if event.outcome not in spec.outcomes:
        raise InvalidAuditEvent(f"outcome: {action.value} can't end {event.outcome.value}")
    if event.target is None:
        if spec.targets:
            raise InvalidAuditEvent(f"target: required for {action.value}")
    else:
        if not isinstance(event.target, Target):
            raise InvalidAuditEvent("target: must be a Target")
        Target(event.target.kind, event.target.ref)
        if type(event.target.kind) is not TargetKind or event.target.kind not in spec.targets:
            raise InvalidAuditEvent(f"target.kind: {action.value} can't act on {event.target.kind}")
    if spec.decided_by:
        put("decided_by", _enum(DecidedBy, event.decided_by, "decided_by"))
    elif event.decided_by is not None:
        raise InvalidAuditEvent(f"decided_by: only approvals record who decided, not {action.value}")
    put("device_id", _optional_id(event.device_id, "device_id"))
    if event.actor.kind is ActorKind.DEVICE and event.device_id != event.actor.id:
        raise InvalidAuditEvent("device_id: a device actor's events carry its device id")
    put("turn_id", _optional_id(event.turn_id, "turn_id"))
    put("request_id", _optional_id(event.request_id, "request_id"))
    details = _details(action, event.redaction, _thaw(event.details) if not build else event.details)
    if build:
        object.__setattr__(event, "details", details)
    elif _thaw(details) != _thaw(event.details):
        raise InvalidAuditEvent("details: changed after it was built")


_TOP = ("id", "occurred_at", "recorded_at", "org_id", "actor", "action", "category", "outcome", "layer", "target",
        "device_id", "decided_by", "turn_id", "request_id", "redaction", "details")


def to_dict(event: AuditEvent) -> dict[str, Any]:
    """The event as JSON-safe data (what is hashed, stored and exported). Checks the event again first."""
    if not isinstance(event, AuditEvent):
        raise InvalidAuditEvent("must be an AuditEvent")
    _check(event, build=False)
    return {
        "id": event.id,
        "occurred_at": format_time(event.occurred_at),
        "recorded_at": format_time(event.recorded_at),
        "org_id": event.org_id,
        "actor": {"kind": event.actor.kind.value, "id": event.actor.id},
        "action": event.action.value,
        "category": event.category.value,
        "outcome": event.outcome.value,
        "layer": event.layer.value,
        "target": None if event.target is None else {"kind": event.target.kind.value, "ref": event.target.ref},
        "device_id": event.device_id,
        "decided_by": None if event.decided_by is None else event.decided_by.value,
        "turn_id": event.turn_id,
        "request_id": event.request_id,
        "redaction": event.redaction.value,
        "details": _thaw(event.details),
    }


def _object(data: object, keys: tuple[str, ...], path: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise InvalidAuditEvent(f"{path}: must be an object")
    if unknown := sorted(str(key) for key in data if key not in keys):
        raise InvalidAuditEvent(f"{path}: unknown field {unknown[0]!r}")
    if missing := [key for key in keys if key not in data]:
        raise InvalidAuditEvent(f"{path}: missing field {missing[0]!r}")
    return data


def from_dict(data: object) -> AuditEvent:
    """An event from JSON made by ``to_dict``. Unknown, missing, mistyped or out-of-bounds fields are refused."""
    data = _object(data, _TOP, "event")
    actor = _object(data["actor"], ("kind", "id"), "actor")
    target = None if data["target"] is None else _object(data["target"], ("kind", "ref"), "target")
    event = AuditEvent(
        id=data["id"],
        occurred_at=_parse_time(data["occurred_at"], "occurred_at"),
        recorded_at=_parse_time(data["recorded_at"], "recorded_at"),
        org_id=data["org_id"],
        actor=Actor(actor["kind"], actor["id"]),
        action=data["action"],
        outcome=data["outcome"],
        layer=data["layer"],
        target=None if target is None else Target(target["kind"], target["ref"]),
        device_id=data["device_id"],
        decided_by=data["decided_by"],
        turn_id=data["turn_id"],
        request_id=data["request_id"],
        redaction=data["redaction"],
        details=data["details"],
    )
    if data["category"] != event.category.value:
        raise InvalidAuditEvent("category: doesn't match the action")
    if to_dict(event)["details"] != data["details"]:
        raise InvalidAuditEvent("details: holds unmasked secret-shaped text")
    return event


def redact(event: AuditEvent, level: RedactionLevel | str) -> AuditEvent:
    """``event`` with only the details ``level`` allows. Redaction only removes: an event already at or below
    ``level`` comes back unchanged."""
    level = _enum(RedactionLevel, level, "level")
    to_dict(event)  # Checks the event first: a forced one is refused, not passed on.
    if LEVEL_RANK[level] >= LEVEL_RANK[event.redaction]:
        return event
    allowed = SPECS[event.action].details
    kept = {key: value for key, value in event.details.items() if LEVEL_RANK[allowed[key].level] <= LEVEL_RANK[level]}
    return dataclasses.replace(event, redaction=level, details=kept)


def canonical_bytes(event: AuditEvent) -> bytes:
    """The event's RFC 8785 bytes: what the hash chain covers. A checked event is always within the JCS subset."""
    return canonical_json(to_dict(event))
