"""``to_ocsf``: an audit event as an OCSF 1.3.0 event, for the exporters (OTLP, syslog, SIEM webhook).

A lean mapping, not OCSF wholesale. Every event gets the base attributes (category, class, activity, type,
severity, status, time, metadata with product, version and tenant) and the object its class requires (``api``,
``user``, ``entity``, ``app``, ``database`` or ``finding_info``). What OCSF has no place for is kept under
``unmapped.row_bot``; ``metadata.event_code`` is the Row-Bot action. Profile attributes (``cloud``, ``osint``) are
not produced. Map the event after redaction: ``to_ocsf`` writes only what the event holds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .event import AuditEvent, InvalidAuditEvent, Target, to_dict
from .vocabulary import Action, ActorKind, Outcome

OCSF_VERSION = "1.3.0"

_CATEGORIES = {2: "Findings", 3: "Identity & Access Management", 6: "Application Activity"}
_CLASSES: dict[int, tuple[str, dict[int, str]]] = {
    2004: ("Detection Finding", {1: "Create"}),
    3001: ("Account Change", {6: "Delete", 99: "Other"}),
    3002: ("Authentication", {1: "Logon", 2: "Logoff"}),
    3004: ("Entity Management", {1: "Create", 2: "Read", 3: "Update", 5: "Move", 6: "Enroll", 7: "Unenroll",
                                 8: "Enable", 9: "Disable", 10: "Activate", 99: "Other"}),
    6002: ("Application Lifecycle", {1: "Install", 2: "Remove"}),
    6003: ("API Activity", {99: "Other"}),
    6005: ("Datastore Activity", {1: "Read", 10: "Decrypt"}),
}
_SEVERITIES = {1: "Informational", 2: "Low", 3: "Medium", 4: "High"}

# action: (class_uid, activity_id, severity_id)
MAPPING: dict[Action, tuple[int, int, int]] = {
    Action.TOOL_CALLED: (6003, 99, 1),
    Action.MODEL_CALLED: (6003, 99, 1),
    Action.POLICY_LOADED: (3004, 10, 1),
    Action.POLICY_CHANGED: (3004, 3, 2),
    Action.POLICY_LOCKED_DOWN: (3004, 99, 3),
    Action.SESSION_SIGNED_IN: (3002, 1, 1),
    Action.SESSION_SIGNED_OUT: (3002, 2, 1),
    Action.DEVICE_ENROLLED: (3004, 6, 1),
    Action.DEVICE_REVOKED: (3004, 7, 2),
    Action.EXTENSION_INSTALLED: (6002, 1, 1),
    Action.EXTENSION_REMOVED: (6002, 2, 1),
    Action.PROMOTION_SUBMITTED: (3004, 1, 1),
    Action.PROMOTION_REVIEWED: (3004, 3, 1),
    Action.PROMOTION_PUBLISHED: (3004, 10, 1),
    Action.PROMOTION_REJECTED: (3004, 3, 1),
    Action.EXPORT_STARTED: (6005, 1, 2),
    Action.EXPORT_COMPLETED: (6005, 1, 2),
    Action.ESCROW_REQUESTED: (3004, 1, 3),
    Action.ESCROW_REVIEWED: (3004, 3, 3),
    Action.ESCROW_OPENED: (6005, 10, 4),
    Action.ESCROW_NOTICE_DEFERRED: (3004, 3, 3),
    Action.LEGAL_HOLD_SET: (3004, 8, 3),
    Action.LEGAL_HOLD_RELEASED: (3004, 9, 3),
    Action.DEPARTURE_STARTED: (3001, 99, 2),
    Action.DEPARTURE_COMPLETED: (3001, 6, 2),
    Action.LINE_ITEMS_MOVED: (3004, 5, 1),
    Action.ORG_SETTINGS_CHANGED: (3004, 3, 2),
    Action.AUDIT_CHAIN_VERIFIED: (3004, 2, 1),
    Action.AUDIT_CHAIN_BROKEN: (2004, 1, 4),
}
_STATUS = {Outcome.SUCCEEDED: (1, "Success"), Outcome.FAILED: (2, "Failure"), Outcome.DENIED: (2, "Failure"),
           Outcome.BLOCKED: (2, "Failure"), Outcome.CHANGED: (99, "Other")}
_DISPOSITION = {Outcome.SUCCEEDED: (1, "Allowed"), Outcome.CHANGED: (1, "Allowed"), Outcome.DENIED: (2, "Denied"),
                Outcome.BLOCKED: (2, "Denied"), Outcome.FAILED: (0, "Unknown")}
_USER_TYPES = {ActorKind.PERSON: (1, "User"), ActorKind.ADMIN: (2, "Admin"), ActorKind.SERVICE: (3, "System")}
_ORG_SERVER = "Row-Bot Org"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _ms(value: datetime) -> int:
    return (value - _EPOCH) // timedelta(milliseconds=1)


def _target(event: AuditEvent) -> Target:
    if event.target is None:  # The vocabulary gives every action mapped here a target.
        raise InvalidAuditEvent(f"target: required for {event.action.value}")
    return event.target


def _actor(event: AuditEvent) -> dict[str, Any]:
    if event.actor.kind is ActorKind.DEVICE:
        return {"app_name": "Row-Bot", "app_uid": event.actor.id}
    type_id, name = _USER_TYPES[event.actor.kind]
    return {"user": {"uid": event.actor.id, "type_id": type_id, "type": name}}


def _src(event: AuditEvent) -> dict[str, Any]:
    return {"uid": event.device_id} if event.device_id else {"name": _ORG_SERVER}


def _entity(event: AuditEvent) -> dict[str, Any]:
    details = event.details
    if event.category.value == "policy":
        entity = {"type": "policy", "uid": details.get("digest") or details["source"]}
        if "policy_version" in details:
            entity["version"] = details["policy_version"]
        return entity
    if event.action is Action.LINE_ITEMS_MOVED:
        return {"type": "work_items", "name": f"{details['count']} items {details['direction'].replace('_', ' ')}"}
    return {"type": _target(event).kind.value, "uid": _target(event).ref}


def _class_objects(event: AuditEvent, class_uid: int) -> dict[str, Any]:
    details = event.details
    if class_uid == 6003:
        if event.action is Action.TOOL_CALLED:
            api = {"operation": _target(event).ref, "service": {"name": details.get("app", "Row-Bot")}}
        else:
            api = {"operation": "model.called", "service": {"name": details["provider"]}}
        if event.request_id:
            api["request"] = {"uid": event.request_id}
        return {"api": api, "src_endpoint": _src(event)}
    if class_uid == 3002:
        user = _actor(event).get("user", {"uid": event.actor.id})
        return {"user": user, "service": {"name": _ORG_SERVER}}
    if class_uid == 3004:
        return {"entity": _entity(event)}
    if class_uid == 6002:
        app = {"name": _target(event).ref, "uid": _target(event).ref}
        if "version" in details:
            app["version"] = details["version"]
        return {"app": app}
    if class_uid == 6005:
        if event.action is Action.ESCROW_OPENED:
            database = {"name": "work_vault", "uid": details["package_id"], "type_id": 99, "type": "Other"}
        else:
            database = {"name": details["scope"], "uid": _target(event).ref, "type_id": 99, "type": "Other"}
        return {"src_endpoint": _src(event), "database": database}
    if class_uid == 3001:
        return {"user": {"uid": _target(event).ref, "type_id": 1, "type": "User"}}
    # 2004: an audit chain found broken.
    return {"finding_info": {"uid": event.id, "title": "Audit chain broken",
                             "desc": f"First broken link {details['broken_at']}: {details['reason']}"}}


def to_ocsf(event: AuditEvent) -> dict[str, Any]:
    """``event`` as an OCSF 1.3.0 event dict."""
    data = to_dict(event)  # Checks the event first.
    class_uid, activity_id, severity_id = MAPPING[event.action]
    class_name, activities = _CLASSES[class_uid]
    category_uid = class_uid // 1000
    status_id, status = _STATUS[event.outcome]
    action_id, action = _DISPOSITION[event.outcome]
    metadata: dict[str, Any] = {
        "version": OCSF_VERSION,
        "product": {"name": "Row-Bot", "vendor_name": "Row-Bot"},
        "uid": event.id,
        "event_code": event.action.value,
        "logged_time": _ms(event.recorded_at),
        "tenant_uid": event.org_id,
    }
    if event.request_id or event.turn_id:
        metadata["correlation_uid"] = event.request_id or event.turn_id
    unmapped = {key: data[key] for key in ("layer", "redaction", "decided_by", "device_id", "turn_id", "request_id",
                                           "target") if data[key] is not None}
    unmapped["details"] = data["details"]
    return {
        "category_uid": category_uid,
        "category_name": _CATEGORIES[category_uid],
        "class_uid": class_uid,
        "class_name": class_name,
        "activity_id": activity_id,
        "activity_name": activities[activity_id],
        "type_uid": class_uid * 100 + activity_id,
        "type_name": f"{class_name}: {activities[activity_id]}",
        "severity_id": severity_id,
        "severity": _SEVERITIES[severity_id],
        "status_id": status_id,
        "status": status,
        "action_id": action_id,
        "action": action,
        "time": _ms(event.occurred_at),
        "metadata": metadata,
        "actor": _actor(event),
        **_class_objects(event, class_uid),
        "unmapped": {"row_bot": unmapped},
    }
