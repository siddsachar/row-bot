"""One example event per audit action, with every detail key the action defines, for the contract tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from row_bot.audit import Action, Actor, ActorKind, AuditEvent, RedactionLevel, Target, TargetKind

START = datetime(2026, 11, 2, 9, 30, 0, 123456, tzinfo=timezone.utc)
ORG = "acme"
DEVICE = "dev-7f3a"
DIGEST = "ab" * 32
OTHER_DIGEST = "cd" * 32
PERSON = Actor(ActorKind.PERSON, "priya")
OWNER = Actor(ActorKind.PERSON, "sam")
ADMIN = Actor(ActorKind.ADMIN, "it-lee")
SERVICE = Actor(ActorKind.SERVICE, "gateway")
OPERATOR = Actor(ActorKind.OPERATOR, "cli")


def event_id(number: int) -> str:
    return str(uuid.UUID(int=number, version=4))


_SHARE = {"item_kind": "skill", "space_id": "team-payments"}
_ON_DEVICE = {"device_id": DEVICE}

# action: (actor, layer, target, outcome, extra fields, details)
_EXAMPLES: dict[Action, tuple[Actor, str, Target | None, str, dict[str, Any], dict[str, Any]]] = {
    Action.SESSION_SIGNED_IN: (PERSON, "org", None, "succeeded", _ON_DEVICE, {}),
    Action.SESSION_SIGNED_OUT: (PERSON, "org", None, "succeeded", _ON_DEVICE, {"reason": "expired"}),
    Action.SESSION_REVOKED: (ADMIN, "org", Target(TargetKind.PERSON, "priya"), "succeeded", {}, {}),
    Action.DEVICE_JOINED: (PERSON, "org", Target(TargetKind.DEVICE, DEVICE), "succeeded", _ON_DEVICE,
                           {"platform": "windows", "app_version": "5.4.0"}),
    Action.DEVICE_REMOVED: (ADMIN, "org", Target(TargetKind.DEVICE, DEVICE), "succeeded", {}, {"reason": "revoked"}),
    Action.DEVICE_LOCKED_DOWN: (PERSON, "org", Target(TargetKind.DEVICE, DEVICE), "succeeded", _ON_DEVICE,
                                {"offline_days": 8}),
    Action.DEVICE_CREDENTIAL_REUSED: (SERVICE, "org", Target(TargetKind.DEVICE, DEVICE), "blocked", _ON_DEVICE,
                                      {"credential": "refresh_token"}),
    Action.MODEL_CALLED: (PERSON, "org", None, "succeeded", {**_ON_DEVICE, "turn_id": "turn-41"},
                          {"provider": "azure_openai", "model": "deployments/gpt-5", "input_tokens": 1200,
                           "output_tokens": 300, "cache_read_tokens": 800, "duration_ms": 2100,
                           "cost_micros": 4500}),
    Action.TOOL_CALLED: (PERSON, "org", Target(TargetKind.TOOL, "workspace_write_file"), "succeeded",
                         {"decided_by": "person", **_ON_DEVICE, "turn_id": "turn-41", "request_id": "req-9",
                          "redaction": "summaries"},
                         {"approval_level": "approve", "surface": "chat", "risk": "medium", "app": "files",
                          "rule_id": "rule-3", "summary": "Wrote notes.md in the Q4 plan folder"}),
    Action.POLICY_CHANGED: (ADMIN, "org", None, "succeeded", {},
                            {"digest": DIGEST, "previous_digest": OTHER_DIGEST, "schema_version": 1,
                             "group": "Engineering/Platform team"}),
    Action.APP_APPROVED: (ADMIN, "org", Target(TargetKind.INTEGRATION, "github"), "succeeded", {},
                          {"version": "2.1.0"}),
    Action.APP_PUSHED: (ADMIN, "org", Target(TargetKind.MCP_SERVER, "jira"), "succeeded", {}, {"version": "1.4.2"}),
    Action.APP_REMOVED: (ADMIN, "org", Target(TargetKind.PLUGIN, "pdf-tools"), "succeeded", {}, {"version": "0.9.0"}),
    Action.DOCUMENT_UPLOADED: (ADMIN, "company", Target(TargetKind.DOCUMENT, "doc-88"), "succeeded", {},
                               {"space_id": "acme", "byte_count": 48_000}),
    Action.DOCUMENT_REMOVED: (OWNER, "team", Target(TargetKind.DOCUMENT, "doc-12"), "succeeded", {},
                              {"space_id": "team-payments"}),
    Action.SHARE_SUBMITTED: (PERSON, "team", Target(TargetKind.SHARE, "share-17"), "succeeded", _ON_DEVICE,
                             dict(_SHARE)),
    Action.SHARE_APPROVED: (OWNER, "team", Target(TargetKind.SHARE, "share-17"), "succeeded", {}, dict(_SHARE)),
    Action.SHARE_REJECTED: (ADMIN, "company", Target(TargetKind.SHARE, "share-18"), "succeeded", {},
                            {"item_kind": "fact", "space_id": "acme"}),
    Action.LIBRARY_PUBLISHED: (OWNER, "team", Target(TargetKind.LIBRARY_ITEM, "skill-refunds"), "succeeded", {},
                               {"item_kind": "skill", "version": "3"}),
    Action.DATA_EXPORTED: (ADMIN, "org", Target(TargetKind.EXPORT, "exp-2"), "succeeded", {},
                           {"scope": "audit", "item_count": 5400, "byte_count": 73_000_000}),
    Action.DATA_DELETED: (ADMIN, "org", Target(TargetKind.PERSON, "priya"), "succeeded", {}, {}),
    Action.ROLE_CHANGED: (ADMIN, "team", Target(TargetKind.PERSON, "sam"), "succeeded", {},
                          {"role": "space_owner", "change": "granted", "space_id": "team-payments"}),
    Action.PERSON_SUSPENDED: (OPERATOR, "org", Target(TargetKind.PERSON, "priya"), "succeeded", {}, {}),
    Action.PERSON_RESUMED: (ADMIN, "org", Target(TargetKind.PERSON, "priya"), "succeeded", {}, {}),
    Action.ORG_SETTINGS_CHANGED: (ADMIN, "org", Target(TargetKind.SETTING, "models.budget.monthly"), "succeeded", {},
                                  {"change": "updated"}),
    Action.SPACE_CHANGED: (ADMIN, "team", Target(TargetKind.SPACE, "team-payments"), "succeeded", {},
                           {"change": "updated", "fields": "reader_groups,owner_principals", "groups_added": 1,
                            "groups_removed": 0, "people_added": 2, "people_removed": 1}),
    Action.FACT_CHANGED: (OWNER, "company", Target(TargetKind.FACT, "fact-31"), "succeeded", {},
                          {"change": "retired", "space_id": "acme"}),
}


def example(action: Action, number: int = 1, **changes: Any) -> AuditEvent:
    """The example event of ``action`` (at its highest redaction level), with ``changes`` applied."""
    actor, layer, target, outcome, extra, details = _EXAMPLES[action]
    fields: dict[str, Any] = {
        "id": event_id(number), "occurred_at": START, "recorded_at": START + timedelta(seconds=2), "org_id": ORG,
        "actor": actor, "action": action, "outcome": outcome, "layer": layer, "target": target,
        "redaction": RedactionLevel.METADATA, "details": details, **extra,
    }
    fields.update(changes)
    return AuditEvent(**fields)
