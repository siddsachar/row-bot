"""Local-owner Insights and controlled-evolution client facade."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions


_ACTIONS = frozenset({"pin", "unpin", "dismiss", "generate", "review_skills", "apply", "reject"})


def _text(value: object, maximum: int) -> str:
    return str(value or "")[:maximum]


def _proposal_view(value: dict) -> dict:
    preview = value.get("preview") if isinstance(value.get("preview"), dict) else {}
    proposal_id = _text(value.get("id"), 128)
    open_thread_id = ""
    if value.get("proposal_type") == "investigate" and value.get("status") in {"applied", "verified"}:
        from row_bot import evolution

        runs = evolution.list_action_runs(proposal_id=proposal_id, limit=1)
        refs = runs[0].get("result_refs", []) if runs else []
        if refs and isinstance(refs[0], str):
            open_thread_id = _text(refs[0], 256)
    feedback_body = ""
    support_url = ""
    if value.get("proposal_type") == "send_feedback":
        from row_bot.brand import APP_SUPPORT_URL

        draft = preview.get("feedback_draft") if isinstance(preview.get("feedback_draft"), dict) else {}
        feedback_body = _text(draft.get("body"), 4000)
        support_url = APP_SUPPORT_URL
    return {
        "id": proposal_id,
        "title": _text(value.get("title"), 256),
        "proposal_type": _text(value.get("proposal_type"), 64),
        "status": _text(value.get("status"), 64),
        "risk": _text(value.get("risk"), 64),
        "rationale": _text(value.get("rationale"), 2048),
        "verification_plan": _text(value.get("verification_plan"), 2048),
        "preview": _text(json.dumps(preview, ensure_ascii=False, default=str), 4000),
        "open_thread_id": open_thread_id,
        "feedback_body": feedback_body,
        "support_url": support_url,
    }


def read_insights(*, validate: Callable[[], None]) -> dict:
    from row_bot import evolution, insights

    validate()
    items = []
    for value in insights.get_active_insights()[:100]:
        if not isinstance(value, dict):
            continue
        proposals = evolution.list_display_proposals_for_insight(
            value, include_terminal=True
        )[:8]
        items.append(
            {
                "id": _text(value.get("id"), 128),
                "title": _text(value.get("title"), 256),
                "body": _text(value.get("body"), 4000),
                "suggestion": _text(value.get("suggestion"), 2048),
                "category": _text(value.get("category"), 64),
                "severity": _text(value.get("severity"), 32),
                "status": _text(value.get("status"), 32),
                "proposals": [_proposal_view(item) for item in proposals],
            }
        )
    reports = evolution.list_curator_reports(limit=1)
    curator_report = None
    if reports:
        report = reports[0]
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        curator_report = {
            "created_at": _text(report.get("created_at"), 64),
            "manual_skill_count": int(summary.get("manual_skill_count") or 0),
            "finding_count": int(summary.get("finding_count") or 0),
            "proposal_count": int(summary.get("proposal_count") or 0),
            "findings": [
                _text(json.dumps(finding, ensure_ascii=False, default=str), 1000)
                for finding in report.get("findings", [])[:32]
                if isinstance(finding, dict)
            ],
        }
    data = {"schema_version": 1, "items": items, "curator_report": curator_report}
    data["revision"] = sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    validate()
    return data


def execute_insight(
    command: dict, *, owner_id: str, validate: Callable[[], None]
) -> dict:
    from row_bot import evolution, insights

    action = command["action"]
    if action not in _ACTIONS:
        raise ClientPlatformError("invalid_insight_command")
    insight_id = _text(command.get("insight_id"), 128)
    proposal_id = _text(command.get("proposal_id"), 128)
    if action in {"pin", "unpin", "dismiss", "generate"} and not insight_id:
        raise ClientPlatformError("invalid_insight_command")
    if action in {"apply", "reject"} and not proposal_id:
        raise ClientPlatformError("invalid_insight_command")
    target = f"insight:{insight_id or proposal_id or 'library'}"
    wire = {
        "command_id": command["command_id"],
        "type": f"insight.{action}",
        "action": action,
        "revision": command["revision"],
        "insight_id": insight_id,
        "proposal_id": proposal_id,
        "reason": _text(command.get("reason"), 512),
    }
    existing = admissions.read_command_metadata(owner_id, command["command_id"])
    if existing is not None:
        if existing["target"] != target or existing["type"] != wire["type"]:
            raise ClientPlatformError("idempotency_mismatch")
        return admissions.claim_command(owner_id, command["command_id"], wire, target)
    snapshot = read_insights(validate=validate)
    if snapshot["revision"] != command["revision"]:
        raise ClientPlatformError("insight_revision_conflict")
    current = next((item for item in snapshot["items"] if item["id"] == insight_id), None)
    if action in {"pin", "unpin", "dismiss", "generate"} and current is None:
        raise ClientPlatformError("insight_unavailable")
    if action in {"apply", "reject"}:
        proposal = evolution.get_proposal(proposal_id)
        if proposal is None or not any(
            item["id"] == proposal_id
            for insight in snapshot["items"]
            for item in insight["proposals"]
        ):
            raise ClientPlatformError("insight_proposal_unavailable")
        if proposal.get("status") in {"applied", "verified", "rejected", "failed"}:
            raise ClientPlatformError("insight_proposal_finished")
    admissions.claim_command(
        owner_id, command["command_id"], wire, target, exclusive_target=True
    )
    validate()
    succeeded = True
    if action in {"pin", "unpin", "dismiss"}:
        status = {"pin": "pinned", "unpin": "new", "dismiss": "dismissed"}[action]
        if not insights.update_insight_status(insight_id, status):
            raise ClientPlatformError("insight_unavailable")
        summary = f"Insight {status}."
    elif action == "generate":
        source = insights.get_insight_by_id(insight_id)
        if source is None:
            raise ClientPlatformError("insight_unavailable")
        proposals = evolution.ensure_proposals_for_insight(source)
        summary = f"Prepared {len(proposals)} proposal(s)."
    elif action == "review_skills":
        report = evolution.review_skill_library_dry_run(create_proposals=True)
        summary = f"Skill review prepared {report.get('summary', {}).get('proposal_count', 0)} proposal(s)."
    elif action == "reject":
        evolution.reject_proposal(proposal_id, wire["reason"])
        summary = "Proposal rejected."
    else:
        result = evolution.apply_proposal(
            proposal_id, require_approval=False, approved_by_user=True
        )
        succeeded = bool(result.get("ok"))
        summary = "Proposal applied." if succeeded else "Proposal failed; inspect its status."
    validate()
    outcome = {
        "command_id": command["command_id"],
        "status": "completed" if succeeded else "failed",
        "summary": summary,
        "snapshot": read_insights(validate=validate),
    }
    return admissions.complete_command(owner_id, command["command_id"], outcome)


def read_insight_receipt(
    command_id: str, *, owner_id: str, validate: Callable[[], None]
) -> dict:
    validate()
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is None or not metadata["type"].startswith("insight."):
        raise ClientPlatformError("insight_receipt_unavailable")
    result = admissions.read_command_receipt(owner_id, command_id)
    if result is None:
        raise ClientPlatformError("insight_receipt_unavailable")
    if result["status"] == "admitting":
        return {
            "command_id": command_id,
            "status": "uncertain",
            "summary": "The outcome is unconfirmed. Inspect the current Insights before another action.",
            "snapshot": read_insights(validate=validate),
        }
    validate()
    return result
