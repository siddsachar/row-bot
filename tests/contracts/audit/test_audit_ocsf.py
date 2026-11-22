from __future__ import annotations

import json

import pytest

from row_bot.audit import OCSF_VERSION, Action, redact, to_ocsf
from tests.contracts.audit.examples import DEVICE, DIGEST, example

pytestmark = pytest.mark.contract

TIME, LOGGED = 1_793_611_800_123, 1_793_611_802_123  # 2026-11-02T09:30:00.123Z and two seconds later.
ID = "00000000-0000-4000-8000-000000000001"


def _metadata(action: str, **extra) -> dict:
    return {"version": OCSF_VERSION, "product": {"name": "Row-Bot", "vendor_name": "Row-Bot"}, "uid": ID,
            "event_code": action, "logged_time": LOGGED, "tenant_uid": "acme", **extra}


def test_a_work_tool_action_at_metadata_is_api_activity():
    assert to_ocsf(redact(example(Action.TOOL_CALLED), "metadata")) == {
        "category_uid": 6, "category_name": "Application Activity",
        "class_uid": 6003, "class_name": "API Activity",
        "activity_id": 99, "activity_name": "Other", "type_uid": 600399, "type_name": "API Activity: Other",
        "severity_id": 1, "severity": "Informational",
        "status_id": 1, "status": "Success", "action_id": 1, "action": "Allowed",
        "time": TIME,
        "metadata": _metadata("tool.called", correlation_uid="req-9"),
        "actor": {"user": {"uid": "priya", "type_id": 1, "type": "User"}},
        "api": {"operation": "workspace_write_file", "service": {"name": "files"}, "request": {"uid": "req-9"}},
        "src_endpoint": {"uid": DEVICE},
        "unmapped": {"row_bot": {
            "layer": "work", "redaction": "metadata", "decided_by": "person", "device_id": DEVICE,
            "turn_id": "turn-41", "request_id": "req-9", "target": {"kind": "tool", "ref": "workspace_write_file"},
            "details": {"app": "files", "approval_level": "approve", "risk": "medium", "rule_id": "rule-3",
                        "surface": "chat"}}},
    }


def test_a_sign_in_is_an_authentication_logon():
    assert to_ocsf(example(Action.SESSION_SIGNED_IN)) == {
        "category_uid": 3, "category_name": "Identity & Access Management",
        "class_uid": 3002, "class_name": "Authentication",
        "activity_id": 1, "activity_name": "Logon", "type_uid": 300201, "type_name": "Authentication: Logon",
        "severity_id": 1, "severity": "Informational",
        "status_id": 1, "status": "Success", "action_id": 1, "action": "Allowed",
        "time": TIME,
        "metadata": _metadata("session.signed_in"),
        "actor": {"user": {"uid": "priya", "type_id": 1, "type": "User"}},
        "user": {"uid": "priya", "type_id": 1, "type": "User"},
        "service": {"name": "Row-Bot Org"},
        "unmapped": {"row_bot": {"layer": "org", "redaction": "metadata", "device_id": DEVICE,
                                 "details": {"method": "oidc"}}},
    }


def test_an_escrow_open_is_a_datastore_decrypt():
    assert to_ocsf(example(Action.ESCROW_OPENED)) == {
        "category_uid": 6, "category_name": "Application Activity",
        "class_uid": 6005, "class_name": "Datastore Activity",
        "activity_id": 10, "activity_name": "Decrypt", "type_uid": 600510,
        "type_name": "Datastore Activity: Decrypt",
        "severity_id": 4, "severity": "High",
        "status_id": 1, "status": "Success", "action_id": 1, "action": "Allowed",
        "time": TIME,
        "metadata": _metadata("escrow.opened", correlation_uid="esc-4"),
        "actor": {"user": {"uid": "recovery-worker", "type_id": 3, "type": "System"}},
        "src_endpoint": {"name": "Row-Bot Org"},
        "database": {"name": "work_vault", "uid": "pkg-priya-4", "type_id": 99, "type": "Other"},
        "unmapped": {"row_bot": {
            "layer": "work", "redaction": "metadata", "request_id": "esc-4",
            "target": {"kind": "person", "ref": "priya"},
            "details": {"kms_key": "oek-2026", "package_id": "pkg-priya-4", "request_id": "esc-4",
                        "wk_version": 4}}},
    }


def test_a_broken_audit_chain_is_a_detection_finding():
    assert to_ocsf(example(Action.AUDIT_CHAIN_BROKEN)) == {
        "category_uid": 2, "category_name": "Findings",
        "class_uid": 2004, "class_name": "Detection Finding",
        "activity_id": 1, "activity_name": "Create", "type_uid": 200401, "type_name": "Detection Finding: Create",
        "severity_id": 4, "severity": "High",
        "status_id": 1, "status": "Success", "action_id": 1, "action": "Allowed",
        "time": TIME,
        "metadata": _metadata("audit_chain.broken"),
        "actor": {"user": {"uid": "recovery-worker", "type_id": 3, "type": "System"}},
        "finding_info": {"uid": ID, "title": "Audit chain broken", "desc": "First broken link 41: prev_hash"},
        "unmapped": {"row_bot": {
            "layer": "org", "redaction": "metadata", "target": {"kind": "audit_chain", "ref": "device:dev-7f3a"},
            "details": {"broken_at": 41, "head": DIGEST, "links_checked": 41, "reason": "prev_hash"}}},
    }


def test_a_blocked_install_and_a_device_policy_load_map_their_outcome_and_actor():
    blocked = to_ocsf(example(Action.EXTENSION_INSTALLED, outcome="blocked"))
    loaded = to_ocsf(example(Action.POLICY_LOADED))

    assert (blocked["class_uid"], blocked["activity_id"], blocked["status_id"], blocked["action_id"]) == (
        6002, 1, 2, 2)
    assert blocked["app"] == {"name": "jira", "uid": "jira", "version": "1.4.2"}
    assert loaded["actor"] == {"app_name": "Row-Bot", "app_uid": DEVICE}
    assert loaded["entity"] == {"type": "policy", "uid": DIGEST, "version": "2026-11 baseline"}
    assert (loaded["class_uid"], loaded["activity_name"]) == (3004, "Activate")


@pytest.mark.parametrize("action", list(Action), ids=lambda action: action.value)
def test_every_action_maps_to_a_consistent_ocsf_event(action):
    mapped = to_ocsf(example(action))

    json.dumps(mapped)
    assert mapped["type_uid"] == mapped["class_uid"] * 100 + mapped["activity_id"]
    assert mapped["category_uid"] == mapped["class_uid"] // 1000
    assert mapped["metadata"]["event_code"] == action.value
