from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def evolution_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "row-bot-data"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))

    import row_bot.skills as skills
    import row_bot.insights as insights
    import row_bot.threads as threads
    import row_bot.evolution as evolution
    import row_bot.tools.row_bot_status_tool as status_tool

    skills = importlib.reload(skills)
    insights = importlib.reload(insights)
    threads = importlib.reload(threads)
    evolution = importlib.reload(evolution)
    status_tool = importlib.reload(status_tool)
    skills.load_skills()

    return SimpleNamespace(
        data_dir=data_dir,
        skills=skills,
        insights=insights,
        threads=threads,
        evolution=evolution,
        status_tool=status_tool,
    )


def test_proposal_persistence_status_transitions_and_category_mapping(evolution_env):
    ev = evolution_env.evolution

    expected = {
        "error_pattern": ["investigate", "send_feedback"],
        "skill_proposal": ["investigate"],
        "tool_config": ["investigate", "send_feedback"],
        "knowledge_quality": ["investigate"],
        "usage_pattern": ["investigate"],
        "system_health": ["send_feedback", "investigate"],
    }
    for category, proposal_types in expected.items():
        assert ev.proposal_types_for_insight({"category": category}) == proposal_types

    skill_insight = {
        "category": "skill_proposal",
        "title": "Repeated export review",
        "body": "The user repeatedly asks for export review before sharing.",
        "suggestion": "Create a reusable export-review workflow.",
        "skill_draft": {
            "name": "export_review",
            "display_name": "Export Review",
            "description": "Review generated exports before sharing.",
            "instructions": "When reviewing an export, check audience, destination, private data, formatting, and user intent before sharing.",
        },
    }
    assert ev.proposal_types_for_insight(skill_insight) == ["create_skill", "patch_skill"]

    proposal = ev.create_proposal(
        insight_ids=["ins_test"],
        proposal_type="investigate",
        title="Investigate a flaky workflow",
        rationale="Needs diagnosis before mutation.",
        risk="low",
        confidence=0.8,
        payload={"prompt": "Look into this"},
        preview={"draft_prompt": "Look into this"},
        verification_plan="Open a draft and review.",
    )

    assert proposal["status"] == "ready"
    assert ev.get_proposal(proposal["id"])["title"] == "Investigate a flaky workflow"
    assert ev.update_proposal_status(proposal["id"], "approved")
    assert ev.get_proposal(proposal["id"])["status"] == "approved"
    ev.reject_proposal(proposal["id"], "Too vague")
    assert ev.get_proposal(proposal["id"])["status"] == "rejected"
    assert ev.list_rejected_proposals(limit=1)[0]["reason"] == "Too vague"


def test_investigate_creates_thread_with_durable_draft(evolution_env):
    insights = evolution_env.insights
    threads = evolution_env.threads
    ev = evolution_env.evolution

    insight = insights.add_insight(
        category="system_health",
        severity="warning",
        title="Status panel failed to refresh",
        body="The panel did not update after a background run.",
        suggestion="Investigate the refresh path.",
    )

    created = ev.create_investigation_thread_from_insight(insight)
    draft = threads.load_thread_draft(created["thread_id"])

    assert draft is not None
    assert "Status panel failed to refresh" in draft["text"]
    assert draft["source"] == "insight_investigate"


def test_ensure_proposals_does_not_regenerate_after_terminal_actions(evolution_env):
    insights = evolution_env.insights
    ev = evolution_env.evolution

    insight = insights.add_insight(
        category="system_health",
        severity="warning",
        title="Provider discovery failed",
        body="The provider catalog failed to list models.",
        suggestion="Investigate and prepare feedback.",
    )

    proposals = ev.ensure_proposals_for_insight(insight)
    original_ids = {proposal["id"] for proposal in proposals}
    assert {proposal["proposal_type"] for proposal in proposals} == {"send_feedback", "investigate"}

    for proposal in list(proposals):
        result = ev.apply_proposal(
            proposal["id"],
            require_approval=False,
            approved_by_user=True,
        )
        assert result["ok"] is True

    after = ev.ensure_proposals_for_insight(insight)
    assert {proposal["id"] for proposal in after} == original_ids
    assert {proposal["status"] for proposal in after} == {"applied"}


def test_display_proposals_collapse_old_ready_duplicates_after_apply(evolution_env):
    ev = evolution_env.evolution

    insight = {
        "id": "ins_duplicate_display",
        "category": "system_health",
        "severity": "warning",
        "title": "xAI provider cannot list models",
        "body": "Provider model discovery fails with HTTP 403.",
        "suggestion": "Verify API key permissions.",
        "confidence": 0.7,
    }
    proposals = ev.map_insight_to_proposals(insight)
    investigate = next(item for item in proposals if item["proposal_type"] == "investigate")
    feedback = next(item for item in proposals if item["proposal_type"] == "send_feedback")
    ev.apply_proposal(investigate["id"], require_approval=False, approved_by_user=True)
    ev.apply_proposal(feedback["id"], require_approval=False, approved_by_user=True)
    ev.map_insight_to_proposals(insight, only_types={"investigate", "send_feedback"})

    display = ev.list_display_proposals_for_insight(insight)

    assert len(display) == 2
    assert {proposal["proposal_type"] for proposal in display} == {"send_feedback", "investigate"}
    assert {proposal["status"] for proposal in display} == {"applied"}


def test_legacy_system_skill_proposal_is_hidden_and_does_not_block_safe_generation(evolution_env):
    ev = evolution_env.evolution

    stale_tasks = {
        "id": "ins_legacy_stale_tasks",
        "category": "skill_proposal",
        "severity": "warning",
        "title": "Enabled tasks appear stale or duplicated",
        "body": "Several enabled tasks have not run recently, have never run, or appear duplicated test tasks.",
        "suggestion": "Run a task hygiene pass and verify schedules for enabled tasks.",
        "skill_draft": {
            "name": "enabled_tasks_appear_stale_or_duplicated",
            "display_name": "Enabled tasks appear stale or duplicated",
            "description": "Review stale enabled tasks.",
            "instructions": "Run a task hygiene pass: disable or archive old test automations, rename duplicates, and verify schedules for enabled tasks that have not run in over 30 days.",
        },
        "confidence": 0.72,
    }
    stale_skill = ev.create_proposal(
        insight_ids=[stale_tasks["id"]],
        proposal_type="create_skill",
        title="Create skill: Enabled tasks appear stale or duplicated",
        rationale="Legacy bad proposal.",
        risk="low",
        confidence=0.72,
        payload={
            "name": "enabled_tasks_appear_stale_or_duplicated",
            "display_name": "Enabled tasks appear stale or duplicated",
            "icon": "sparkles",
            "description": "Several enabled tasks have not run recently.",
            "instructions": "Run a task hygiene pass: disable or archive old test automations, rename duplicates, and verify schedules for enabled tasks that have not run in over 30 days.",
            "tags": ["self-improvement"],
            "enabled": True,
            "version": "1.0",
        },
        preview={},
        verification_plan="Legacy.",
    )

    assert ev.list_display_proposals_for_insight(stale_tasks) == []
    result = ev.apply_proposal(stale_skill["id"], require_approval=False, approved_by_user=True)
    assert result["ok"] is False
    assert "obsolete" in result["message"]

    regenerated = ev.ensure_proposals_for_insight(stale_tasks)

    assert {proposal["proposal_type"] for proposal in regenerated} == {"send_feedback", "investigate"}
    assert "create_skill" not in {proposal["proposal_type"] for proposal in regenerated}


def test_system_maintenance_insight_does_not_become_skill_proposal(evolution_env):
    insights = evolution_env.insights
    ev = evolution_env.evolution

    stale_tasks = {
        "category": "skill_proposal",
        "severity": "warning",
        "title": "Enabled tasks appear stale or duplicated",
        "body": "Several enabled tasks have not run recently, have never run, or appear duplicated test tasks.",
        "suggestion": "Run a task hygiene pass and verify schedules for enabled tasks.",
        "skill_draft": {
            "name": "enabled_tasks_appear_stale_or_duplicated",
            "display_name": "Enabled tasks appear stale or duplicated",
            "description": "Review stale enabled tasks.",
            "instructions": "Run a task hygiene pass: disable or archive old test automations, rename duplicates, and verify schedules for enabled tasks that have not run in over 30 days.",
        },
    }

    normalized = ev.normalize_insight_for_evolution(stale_tasks)
    assert normalized["category"] == "system_health"
    assert normalized["skill_draft"] is None
    assert ev.proposal_types_for_insight(stale_tasks) == ["send_feedback", "investigate"]

    added = insights.add_insight(**stale_tasks)
    assert added["category"] == "system_health"
    assert added["skill_draft"] is None
    linked = ev.list_proposals_for_insight(added["id"], include_terminal=True)
    assert {proposal["proposal_type"] for proposal in linked} == {"send_feedback", "investigate"}


def test_create_skill_proposal_requires_apply_before_mutation(evolution_env):
    ev = evolution_env.evolution
    skills = evolution_env.skills

    proposal = ev.build_create_skill_proposal(
        {
            "name": "controlled_alpha_workflow",
            "display_name": "Controlled Alpha Workflow",
            "icon": "sparkles",
            "description": "A test workflow created through proposals.",
            "instructions": "When asked for alpha workflow, gather inputs and summarize next steps.",
            "tags": ["test"],
        },
        rationale="Test proposal path.",
    )

    assert skills.get_skill("controlled_alpha_workflow") is None
    result = ev.apply_proposal(
        proposal["id"],
        require_approval=False,
        approved_by_user=True,
    )

    assert result["ok"] is True
    assert skills.get_skill("controlled_alpha_workflow") is not None
    run = ev.list_action_runs(proposal_id=proposal["id"])[0]
    assert run["approved_by_user"] is True
    assert run["result"] == "success"


def test_patch_skill_proposal_uses_bounded_diff_backup_and_rollback_ref(evolution_env):
    ev = evolution_env.evolution
    skills = evolution_env.skills

    skills.create_skill(
        name="patchable_controlled_skill",
        display_name="Patchable Controlled Skill",
        icon="wrench",
        description="Patch target.",
        instructions="Step 1: Do the original thing.",
        enabled=True,
    )

    proposal = ev.build_patch_skill_proposal(
        target_skill="patchable_controlled_skill",
        updated_instructions="Step 1: Do the original thing.\nStep 2: Record the validation result.",
        reason="Add validation guidance.",
    )

    assert proposal["preview"]["bounded"] is True
    assert "Step 2" in proposal["preview"]["diff"]

    result = ev.apply_proposal(
        proposal["id"],
        require_approval=False,
        approved_by_user=True,
    )

    assert result["ok"] is True
    run = ev.list_action_runs(proposal_id=proposal["id"])[0]
    assert run["rollback_ref"]
    assert "Step 2" in skills.get_skill("patchable_controlled_skill").instructions


def test_send_feedback_redacts_and_saves_local_markdown(evolution_env):
    ev = evolution_env.evolution
    from row_bot.brand import APP_SUPPORT_URL

    draft = ev.build_feedback_draft(
        title="Token leaked in D:\\Users\\Alice\\project",
        summary="Failure used token=sk-abcdefghijklmnopqrstuvwxyz and alice@example.com",
    )

    assert "sk-" not in draft["body"]
    assert "alice@example.com" not in draft["body"]
    assert "D:\\Users\\Alice" not in draft["title"]

    built = ev.build_send_feedback_proposal(title="Plain feedback", summary="Prepare a report.")
    assert built["risk"] == "low"

    proposal = ev.create_proposal(
        insight_ids=[],
        proposal_type="send_feedback",
        title=f"Send feedback: {draft['title']}",
        rationale="Test feedback fallback.",
        risk="medium",
        confidence=0.7,
        payload={"feedback_draft": draft},
        preview={"feedback_draft": draft},
        verification_plan="Review redaction, then save local markdown.",
    )
    result = ev.apply_proposal(
        proposal["id"],
        require_approval=False,
        approved_by_user=True,
    )

    assert result["ok"] is True
    refs = result["action_run"]["result_refs"]
    report_path = refs[0]
    assert refs[1] == APP_SUPPORT_URL
    assert report_path.endswith(".md")
    assert "feedback_reports" in report_path
    assert "sk-" not in open(report_path, encoding="utf-8").read()


def test_send_feedback_ignores_stale_github_payload_and_returns_contact_url(evolution_env):
    ev = evolution_env.evolution
    from row_bot.brand import APP_SUPPORT_URL

    draft = ev.build_feedback_draft(
        title="Contact page smoke",
        summary="Prepare a redacted feedback report from a controlled proposal.",
    )
    proposal = ev.create_proposal(
        insight_ids=[],
        proposal_type="send_feedback",
        title=f"Send feedback: {draft['title']}",
        rationale="Test send feedback path.",
        risk="medium",
        confidence=0.7,
        payload={"feedback_draft": draft, "github": True},
        preview={"feedback_draft": draft},
        verification_plan="Review redaction, then save locally or open the contact page.",
    )

    result = ev.apply_proposal(
        proposal["id"],
        require_approval=False,
        approved_by_user=True,
    )

    assert result["ok"] is True
    refs = result["action_run"]["result_refs"]
    assert refs[0].endswith(".md")
    assert refs[1] == APP_SUPPORT_URL
    assert "github.com" not in "\n".join(refs)


def test_legacy_report_issue_records_load_as_send_feedback(evolution_env):
    ev = evolution_env.evolution
    from row_bot.brand import APP_SUPPORT_URL

    store_path = evolution_env.data_dir / "controlled_evolution.json"
    draft = {"title": "Legacy report", "body": "A redacted legacy body."}
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(
        json.dumps(
            {
                "proposals": [
                    {
                        "id": "proposal_legacy_feedback",
                        "insight_ids": [],
                        "proposal_type": "report_issue",
                        "title": "Report issue: Legacy report",
                        "rationale": "Legacy record.",
                        "risk": "medium",
                        "confidence": 0.7,
                        "payload": {"issue_draft": draft, "github": True},
                        "preview": {"issue_draft": draft, "github": True},
                        "verification_plan": "Legacy plan.",
                        "status": "ready",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "action_runs": [],
                "rejected_proposals": [],
                "outcomes": [],
                "curator_reports": [],
                "meta": {"schema_version": 1},
            }
        ),
        encoding="utf-8",
    )

    proposal = ev.get_proposal("proposal_legacy_feedback")
    assert proposal["proposal_type"] == "send_feedback"
    assert proposal["title"] == "Send feedback: Legacy report"
    assert proposal["payload"] == {"feedback_draft": draft}
    assert proposal["preview"]["contact_url"] == APP_SUPPORT_URL
    result = ev.apply_proposal(
        proposal["id"],
        require_approval=False,
        approved_by_user=True,
    )
    assert result["ok"] is True
    assert result["action_run"]["result_refs"][1] == APP_SUPPORT_URL


def test_rejection_memory_influences_future_similar_proposals(evolution_env):
    ev = evolution_env.evolution

    first = ev.create_proposal(
        insight_ids=["ins_repeat"],
        proposal_type="create_skill",
        title="Create skill: Repeated Export Cleanup",
        rationale="Seems reusable.",
        risk="low",
        confidence=0.8,
        payload={
            "name": "repeated_export_cleanup",
            "display_name": "Repeated Export Cleanup",
            "icon": "sparkles",
            "description": "Export cleanup.",
            "instructions": "Clean exports consistently.",
            "tags": [],
            "enabled": True,
            "version": "1.0",
        },
        preview={},
        verification_plan="Validate metadata.",
    )
    ev.reject_proposal(first["id"], "Too specific for a reusable skill")

    second = ev.create_proposal(
        insight_ids=["ins_repeat"],
        proposal_type="create_skill",
        title="Create skill: Repeated Export Cleanup",
        rationale="Seems reusable again.",
        risk="low",
        confidence=0.8,
        payload={
            "name": "repeated_export_cleanup_v2",
            "display_name": "Repeated Export Cleanup",
            "icon": "sparkles",
            "description": "Export cleanup.",
            "instructions": "Clean exports consistently with a broader scope.",
            "tags": [],
            "enabled": True,
            "version": "1.0",
        },
        preview={},
        verification_plan="Validate metadata.",
        dedupe=False,
    )

    assert second["confidence"] <= 0.45
    assert second["preview"]["previous_rejection"]["reason"] == "Too specific for a reusable skill"
    assert "Previous similar proposal was rejected" in second["rationale"]


def test_curator_dry_run_generates_report_and_does_not_mutate_skills(evolution_env):
    ev = evolution_env.evolution
    skills = evolution_env.skills

    before_names = {skill.name for skill in skills.get_all_skills()}
    skills.create_skill(
        name="curator_overlap_one",
        display_name="Curator Overlap One",
        icon="one",
        description="Review pull request checklists.",
        instructions="Review pull request checklists, tests, risks, and release notes.",
        enabled=True,
    )
    skills.create_skill(
        name="curator_overlap_two",
        display_name="Curator Overlap Two",
        icon="two",
        description="Review pull request checklists.",
        instructions="Review pull request checklists, tests, risks, and release notes before merge.",
        enabled=True,
    )
    after_create_names = {skill.name for skill in skills.get_all_skills()}

    report = ev.review_skill_library_dry_run(create_proposals=True)

    assert report["mutated_skills"] == []
    assert report["summary"]["manual_skill_count"] >= 2
    assert {skill.name for skill in skills.get_all_skills()} == after_create_names
    assert before_names < after_create_names
    assert ev.list_curator_reports(limit=1)[0]["id"] == report["id"]


def test_row_bot_status_exposes_evolution_and_proposal_tools(evolution_env):
    status = evolution_env.status_tool
    skills = evolution_env.skills

    output = status._create_skill(
        name="status_tool_proposed_skill",
        display_name="Status Tool Proposed Skill",
        icon="sparkles",
        description="Created as a proposal.",
        instructions="Use proposals before skill mutation.",
        tags="test",
    )
    assert "Skill creation proposal created:" in output
    assert skills.get_skill("status_tool_proposed_skill") is None
    assert "**Controlled Self-Evolution**" in status._query_evolution()

    tool_names = {tool.name for tool in status.RowBotStatusTool().as_langchain_tools()}
    assert {
        "row_bot_apply_proposal",
        "row_bot_reject_proposal",
        "row_bot_send_feedback",
        "row_bot_review_skill_library",
        "row_bot_verify_proposal",
    } <= tool_names


def test_command_center_send_feedback_dialog_has_copy_save_submit_controls():
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "row_bot"
        / "ui"
        / "command_center.py"
    ).read_text(encoding="utf-8")

    assert 'proposal_type == "send_feedback"' in source
    assert '"Copy report"' in source
    assert '"Save report"' in source
    assert '"Submit"' in source
    assert "APP_SUPPORT_URL" in source
    assert "window.open" in source
    assert "list_display_proposals_for_insight" in source
    assert "ensure_proposals_for_insight(ins)" not in source
    assert '"Investigate", on_click' not in source
    assert "proposal_dialog_state" in source
    assert "navigate_thread" in source
    assert "_compact_proposal_title" in source
    assert "_proposal_status_label" in source
    assert "row-bot-insight-proposal-row" in source
    assert "grid-template-columns: minmax(0, 1fr) 26px" in source
    assert "workflow-console-content" in source
    assert "workflow-console-section" in source
    assert ".row-bot-command-center-drawer *,\n.row-bot-command-center-drawer *::before" in source
    assert "min-width: 0;\n}\n.workflow-console-rail" not in source
    assert "width: 100%; min-width: 100%; max-width: 100%; overflow-x: hidden;" in source
    assert "risk: {proposal.get('risk'" in source


def test_dream_insights_prompt_discourages_system_issues_as_skills():
    prompt = (
        Path(__file__).resolve().parents[1] / "src" / "row_bot" / "prompts.py"
    ).read_text(encoding="utf-8")

    assert "Use skill_proposal only for repeated user-facing workflows" in prompt
    assert "provider/model discovery failures" in prompt
    assert "task" in prompt and "hygiene" in prompt
