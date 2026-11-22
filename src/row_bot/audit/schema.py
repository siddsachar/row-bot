"""JSON Schemas (draft 2020-12) for ``AuditEvent`` and ``ChainLink``, generated from the vocabulary and published
under ``contracts/audit/v1/``.

They refuse what ``from_dict`` refuses, except what JSON Schema can't say: secret-shaped text (identifiers that
look like a secret, unmasked summaries) and whether a time is a real calendar time. ``from_dict`` is the
authority; the schemas are for other languages and for reviewers.
"""

from __future__ import annotations

from typing import Any

from .jcs import MAX_SAFE_INTEGER
from .vocabulary import (
    EVENT_ID_PATTERN, ID_PATTERN, LEVEL_RANK, ORG_ID_PATTERN, PATTERNS, SPECS, SUMMARY_MAX, TIME_PATTERN, Action,
    ActorKind, AuditLayer, DecidedBy, Detail, Outcome, RedactionLevel, TargetKind, category_of,
)

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def _pattern(pattern: str) -> str:
    return f"^(?:{pattern})$"


def _string(pattern: str) -> dict[str, Any]:
    return {"type": "string", "pattern": _pattern(pattern)}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _enum(values: Any) -> dict[str, Any]:
    return {"type": "string", "enum": sorted(member.value for member in values)}


def _detail(spec: Detail) -> dict[str, Any]:
    if spec.kind in PATTERNS:
        return _string(PATTERNS[spec.kind])
    if spec.kind == "enum":
        return {"type": "string", "enum": list(spec.choices)}
    if spec.kind == "int":
        return {"type": "integer", "minimum": spec.minimum, "maximum": spec.maximum}
    # text: a short summary.
    return {"type": "string", "minLength": 1, "maxLength": SUMMARY_MAX, "pattern": "^[^\\u0000]*$"}


def _action_rule(action: Action) -> dict[str, Any]:
    spec = SPECS[action]
    details: dict[str, Any] = {
        "type": "object", "additionalProperties": False,
        "properties": {key: _detail(detail) for key, detail in sorted(spec.details.items())},
        "required": sorted(key for key, detail in spec.details.items() if detail.required),
    }
    then: dict[str, Any] = {
        "properties": {
            "category": {"const": category_of(action).value},
            "actor": {"properties": {"kind": {"enum": sorted(kind.value for kind in spec.actors)}}},
            "layer": {"enum": sorted(layer.value for layer in spec.layers)},
            "outcome": {"enum": sorted(outcome.value for outcome in spec.outcomes)},
            "target": ({"type": "object", "properties": {"kind": {"enum": sorted(k.value for k in spec.targets)}}}
                       if spec.targets else {"type": "null"}),
            "decided_by": _enum(DecidedBy) if spec.decided_by else {"type": "null"},
            "details": details,
        },
    }
    gated = []
    for key, detail in sorted(spec.details.items()):
        lower = sorted((level.value for level in RedactionLevel if LEVEL_RANK[level] < LEVEL_RANK[detail.level]),
                       key=lambda value: LEVEL_RANK[RedactionLevel(value)])
        if lower:
            gated.append({"if": {"properties": {"redaction": {"enum": lower}}},
                          "then": {"properties": {"details": {"not": {"required": [key]}}}}})
    if gated:
        then["allOf"] = gated
    return {"if": {"properties": {"action": {"const": action.value}}}, "then": then}


def _event_schema() -> dict[str, Any]:
    time = {"type": "string", "format": "date-time", "pattern": _pattern(TIME_PATTERN)}
    identifier = _string(ID_PATTERN)
    properties = {
        "id": _string(EVENT_ID_PATTERN),
        "occurred_at": time,
        "recorded_at": time,
        "org_id": _string(ORG_ID_PATTERN),
        "actor": {"type": "object", "additionalProperties": False, "required": ["id", "kind"],
                  "properties": {"kind": _enum(ActorKind), "id": identifier}},
        "action": _enum(Action),
        "category": {"type": "string", "enum": sorted({category_of(action).value for action in Action})},
        "outcome": _enum(Outcome),
        "layer": _enum(AuditLayer),
        "target": _nullable({"type": "object", "additionalProperties": False, "required": ["kind", "ref"],
                             "properties": {"kind": _enum(TargetKind), "ref": identifier}}),
        "device_id": _nullable(identifier),
        "decided_by": _nullable(_enum(DecidedBy)),
        "turn_id": _nullable(identifier),
        "request_id": _nullable(identifier),
        "redaction": _enum(RedactionLevel),
        "details": {"type": "object"},
    }
    return {
        "type": "object",
        "description": ("One Row-Bot for Teams audit event: team, company or org layer, never personal; details "
                        "hold only the keys the action defines at or below the event's redaction level."),
        "additionalProperties": False,
        "required": sorted(properties),
        "properties": properties,
        "allOf": [_action_rule(action) for action in Action],
    }


def json_schemas() -> dict[str, dict[str, Any]]:
    """The published schemas by name: ``AuditEvent`` and ``ChainLink``."""
    event = _event_schema()
    digest = _string(PATTERNS["hash"])
    link = {
        "$schema": SCHEMA_DIALECT, "title": "ChainLink",
        "description": ("One link of an audit hash chain: hash = SHA-256(lp('row-bot/audit/v1') || lp(prev_hash) "
                        "|| lp(JCS(event))), lp = 8-byte big-endian length prefix."),
        "type": "object", "additionalProperties": False, "required": ["event", "hash", "prev_hash", "seq"],
        "properties": {"seq": {"type": "integer", "minimum": 0, "maximum": MAX_SAFE_INTEGER},
                       "prev_hash": digest, "hash": digest, "event": {"$ref": "#/$defs/AuditEvent"}},
        "$defs": {"AuditEvent": event},
    }
    return {
        "AuditEvent": {"$schema": SCHEMA_DIALECT, "title": "AuditEvent", **event},
        "ChainLink": link,
    }
