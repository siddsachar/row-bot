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
ADMIN = Actor(ActorKind.ADMIN, "it-lee")
SERVICE = Actor(ActorKind.SERVICE, "recovery-worker")
DEVICE_ACTOR = Actor(ActorKind.DEVICE, DEVICE)


def event_id(number: int) -> str:
    return str(uuid.UUID(int=number, version=4))


_POLICY = {"source": "registry", "schema_version": 1, "digest": DIGEST, "policy_version": "2026-11 baseline",
           "signer": "it-key-1"}
_PROMOTION = {"item_kind": "skill", "space_id": "team-payments"}

# action: (actor, layer, target, outcome, extra fields, details)
_EXAMPLES: dict[Action, tuple[Actor, str, Target | None, str, dict[str, Any], dict[str, Any]]] = {
    Action.TOOL_CALLED: (PERSON, "work", Target(TargetKind.TOOL, "workspace_write_file"), "succeeded",
                         {"decided_by": "person", "device_id": DEVICE, "turn_id": "turn-41", "request_id": "req-9",
                          "redaction": "full"},
                         {"approval_level": "approve", "surface": "chat", "risk": "medium", "app": "files",
                          "rule_id": "rule-3", "summary": "Wrote notes.md in the Q4 plan folder",
                          "arguments": {"path": "notes.md", "lines": 12, "append": False, "tags": ["q4", None]}}),
    Action.MODEL_CALLED: (PERSON, "work", None, "succeeded", {"device_id": DEVICE, "turn_id": "turn-41"},
                          {"provider": "azure_openai", "model": "deployments/gpt-5", "input_tokens": 1200,
                           "output_tokens": 300, "cache_read_tokens": 800, "duration_ms": 2100}),
    Action.POLICY_LOADED: (DEVICE_ACTOR, "org", None, "succeeded", {"device_id": DEVICE}, dict(_POLICY)),
    Action.POLICY_CHANGED: (ADMIN, "org", None, "succeeded", {}, {**_POLICY, "previous_digest": OTHER_DIGEST}),
    Action.POLICY_LOCKED_DOWN: (DEVICE_ACTOR, "org", None, "succeeded", {"device_id": DEVICE},
                                {"source": "file", "reason": "signature_invalid", "digest": DIGEST}),
    Action.SESSION_SIGNED_IN: (PERSON, "org", None, "succeeded", {"device_id": DEVICE}, {"method": "oidc"}),
    Action.SESSION_SIGNED_OUT: (PERSON, "org", None, "succeeded", {"device_id": DEVICE}, {"reason": "expired"}),
    Action.DEVICE_ENROLLED: (PERSON, "org", Target(TargetKind.DEVICE, DEVICE), "succeeded", {"device_id": DEVICE},
                             {"platform": "windows", "app_version": "5.4.0"}),
    Action.DEVICE_REVOKED: (ADMIN, "org", Target(TargetKind.DEVICE, DEVICE), "succeeded", {}, {"reason": "lost"}),
    Action.EXTENSION_INSTALLED: (ADMIN, "org", Target(TargetKind.MCP_SERVER, "jira"), "succeeded", {},
                                 {"version": "1.4.2", "origin": "org_catalog"}),
    Action.EXTENSION_REMOVED: (PERSON, "work", Target(TargetKind.PLUGIN, "pdf-tools"), "succeeded",
                               {"device_id": DEVICE}, {"version": "0.9.0"}),
    Action.PROMOTION_SUBMITTED: (PERSON, "team", Target(TargetKind.PROMOTION, "prom-17"), "succeeded",
                                 {"device_id": DEVICE}, dict(_PROMOTION)),
    Action.PROMOTION_REVIEWED: (Actor(ActorKind.PERSON, "sam"), "team", Target(TargetKind.PROMOTION, "prom-17"),
                                "succeeded", {}, dict(_PROMOTION)),
    Action.PROMOTION_PUBLISHED: (SERVICE, "team", Target(TargetKind.PROMOTION, "prom-17"), "succeeded", {},
                                 dict(_PROMOTION)),
    Action.PROMOTION_REJECTED: (ADMIN, "company", Target(TargetKind.PROMOTION, "prom-18"), "succeeded", {},
                                {"item_kind": "fact", "space_id": "acme"}),
    Action.EXPORT_STARTED: (ADMIN, "company", Target(TargetKind.EXPORT, "exp-2"), "succeeded", {},
                            {"scope": "company"}),
    Action.EXPORT_COMPLETED: (SERVICE, "company", Target(TargetKind.EXPORT, "exp-2"), "succeeded", {},
                              {"scope": "company", "item_count": 5400, "byte_count": 73_000_000}),
    Action.ESCROW_REQUESTED: (ADMIN, "work", Target(TargetKind.PERSON, "priya"), "succeeded", {},
                              {"request_id": "esc-4", "reason": "legal_hold", "wk_version_first": 3,
                               "wk_version_last": 5}),
    Action.ESCROW_REVIEWED: (Actor(ActorKind.ADMIN, "approver-kim"), "work", Target(TargetKind.PERSON, "priya"),
                             "denied", {}, {"request_id": "esc-4"}),
    Action.ESCROW_OPENED: (SERVICE, "work", Target(TargetKind.PERSON, "priya"), "succeeded", {"request_id": "esc-4"},
                           {"request_id": "esc-4", "wk_version": 4, "package_id": "pkg-priya-4",
                            "kms_key": "oek-2026"}),
    Action.ESCROW_NOTICE_DEFERRED: (Actor(ActorKind.ADMIN, "legal-ana"), "work", Target(TargetKind.PERSON, "priya"),
                                    "succeeded", {}, {"request_id": "esc-4", "days": 30}),
    Action.LEGAL_HOLD_SET: (Actor(ActorKind.ADMIN, "legal-ana"), "work", Target(TargetKind.PERSON, "priya"),
                            "succeeded", {}, {"hold_id": "hold-1"}),
    Action.LEGAL_HOLD_RELEASED: (Actor(ActorKind.ADMIN, "legal-ana"), "team", Target(TargetKind.SPACE, "team-payments"),
                                 "succeeded", {}, {"hold_id": "hold-1"}),
    Action.DEPARTURE_STARTED: (Actor(ActorKind.SERVICE, "scim"), "org", Target(TargetKind.PERSON, "priya"),
                               "succeeded", {}, {"trigger": "scim", "window_days": 10}),
    Action.DEPARTURE_COMPLETED: (ADMIN, "org", Target(TargetKind.PERSON, "priya"), "succeeded", {},
                                 {"personal_export_made": True, "handover": "released_by_person"}),
    Action.LINE_ITEMS_MOVED: (PERSON, "work", None, "succeeded", {"device_id": DEVICE},
                              {"direction": "to_personal", "count": 3, "during_departure": True}),
    Action.ORG_SETTINGS_CHANGED: (ADMIN, "org", Target(TargetKind.SETTING, "approvals.max_level"), "succeeded", {},
                                  {}),
    Action.AUDIT_CHAIN_VERIFIED: (SERVICE, "org", Target(TargetKind.AUDIT_CHAIN, "org:acme"), "succeeded", {},
                                  {"links_checked": 1200, "head": DIGEST}),
    Action.AUDIT_CHAIN_BROKEN: (SERVICE, "org", Target(TargetKind.AUDIT_CHAIN, "device:dev-7f3a"), "succeeded", {},
                                {"links_checked": 41, "head": DIGEST, "broken_at": 41, "reason": "prev_hash"}),
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
