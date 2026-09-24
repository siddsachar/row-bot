"""Conversation approval context is useful without exposing raw arguments."""

from __future__ import annotations

import json

from row_bot.application.approval_projection import project_approval_context


def test_approval_context_is_bounded_allowlisted_and_trace_correlated():
    private = "C:/private/secret.txt"
    projected = project_approval_context({
        "__interrupt_id": "interrupt-1",
        "tool": "workspace_read_file",
        "description": "Read a reviewed local resource.",
        "risk_class": "low",
        "scope": "One local read.",
        "args": {"limit": 3, "path": private, "token": "secret"},
    })
    assert projected == {
        "action_label": "workspace_read_file",
        "reason": "Read a reviewed local resource.",
        "risk_class": "low",
        "scope": "One local read.",
        "safe_argument_summary": '{"limit":3}',
        "requesting_trace_id": "interrupt-1",
    }
    assert private not in json.dumps(projected)
    assert "secret" not in json.dumps(projected)


def test_approval_context_has_truthful_unknown_risk_and_multi_action_scope():
    projected = project_approval_context([
        {"tool": "fixture", "args": {"unsafe": "hidden"}},
        {"tool": "second"},
    ])
    assert projected["risk_class"] == "unknown"
    assert projected["scope"] == "2 requested actions will be resolved together."
    assert projected["safe_argument_summary"] == ""
