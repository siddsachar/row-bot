"""Seed neutral demonstration data for real Row-Bot docs screenshots.

Everything here is fictional and lives only in the isolated capture profile:
conversations with a tool trace, a waiting approval, a goal with agents,
workflows with runs, memories, a design and a code folder bound to their
conversations, a Monitor problem with its fix, and insights. Times are relative
to now, so the client groups and dates them as it would for a real person.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
DEMO_THREAD = "docs-demo-chat"
CODE_THREAD = "docs-demo-code"
DESIGN_THREAD = "docs-demo-design"
# Fixed ids, so the capture can mark both approvals as already announced and
# the floating "needs your approval" notice does not cover every screen.
WORKFLOW_APPROVAL_ID = "d0c5a0000001"
CHAT_APPROVAL_ID = "d0c5a0000002"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed real app demo data for docs capture")
    parser.add_argument("--data-dir", required=True, help="Temporary ROW_BOT_DATA_DIR to seed")
    parser.add_argument("--scenario", default="full", help="Demo scenario to seed")
    parser.add_argument(
        "--ollama-host",
        default="",
        help="The capture's display-only local runtime; the model catalog is seeded only from it",
    )
    return parser.parse_args()


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _ago(**delta: float) -> datetime:
    return datetime.now() - timedelta(**delta)


def _seed_app_config(data_dir: Path, *, first_run: bool) -> None:
    if first_run:
        config = {
            "onboarding_seen": False,
            "setup_complete": False,
            "window_mode": "browser",
        }
    else:
        config = {
            "onboarding_seen": True,
            "setup_complete": True,
            "onboarding_version": 4,
            "onboarding_profile": ["chat", "research", "workflows", "designer", "developer"],
            "onboarding_completed_steps": ["models", "knowledge", "workflows"],
            "onboarding_skipped_steps": [],
            "onboarding_dismissed_home_card": False,
            "window_mode": "browser",
        }
    _write_json(data_dir / "app_config.json", config)
    if not first_run:
        # The chat default (the first-run question is answered).
        _write_json(data_dir / "model_settings.json", {"model": "model:ollama:llama3.1:8b"})


def _seed_model_catalog() -> None:
    """The picker's local models and Quick Choices, read from the demo runtime."""
    from row_bot.providers.model_catalog_cache import refresh_model_catalog_cache
    from row_bot.providers.selection import add_quick_choice_for_model

    refresh_model_catalog_cache(reason="docs_demo", force=True, provider_id="ollama")
    for model_id in ("llama3.1:8b", "qwen3:8b"):
        add_quick_choice_for_model(model_id, provider_id="ollama")


# Conversations ---------------------------------------------------------------

def _call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _conversations() -> list[dict]:
    """Each conversation: id, title, minutes since last activity, messages."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    return [
        {
            "id": DEMO_THREAD,
            "title": "Demo launch checklist",
            "minutes": 6,
            "messages": [
                HumanMessage(content="Prepare the launch checklist for the community workshop and check it against the brief."),
                AIMessage(content="", tool_calls=[
                    _call("file_search", {"pattern": "launch", "folder": "docs-demo-workspace"}, "call_search"),
                    _call("read_file", {"path": "launch-checklist.md"}, "call_read"),
                    _call("search_memory", {"query": "community workshop launch"}, "call_memory"),
                ]),
                ToolMessage(content="Found launch-checklist.md and support-faq.md in the demo workspace.",
                            name="file_search", tool_call_id="call_search"),
                ToolMessage(content=(
                    "# Demo Launch Checklist\n\n- Confirm local model path.\n"
                    "- Review approvals before file writes.\n- Keep channels disabled until credentials are added.\n"
                ), name="read_file", tool_call_id="call_read"),
                ToolMessage(content="3 memories: Demo Launch (project), Safety Review (concept), Support FAQ (fact).",
                            name="search_memory", tool_call_id="call_memory"),
                AIMessage(content="", tool_calls=[
                    _call("delegate_work", {"task": "Review both sources and tidy the checklist wording", "agents": 2},
                          "call_delegate"),
                ]),
                ToolMessage(content="Launch coordinator started Source reviewer and Checklist editor.",
                            name="delegate_work", tool_call_id="call_delegate"),
                AIMessage(content=(
                    "Here is the checklist, checked against the brief:\n\n"
                    "1. **Confirm the local model**: llama3.1:8b answers on this computer.\n"
                    "2. **Review the sources**: the brief and the Support FAQ agree on the workshop date.\n"
                    "3. **Approve before writing**: every file change waits for your approval.\n"
                    "4. **Keep channels off** until their credentials are added.\n"
                    "5. **Plan the recovery**: keep the previous checklist as a draft.\n\n"
                    "The Source reviewer has finished; the Checklist editor is still tidying the wording, "
                    "and I'll update the goal when it's done."
                )),
            ],
        },
        {
            "id": CODE_THREAD,
            "title": "Release notes cleanup",
            "minutes": 24,
            "messages": [
                HumanMessage(content="Add the recovery note to the release notes README, run the tests and commit it."),
                AIMessage(content="", tool_calls=[
                    _call("developer_git_status", {}, "call_status"),
                    _call("developer_read_file", {"path": "README.md"}, "call_readme"),
                ]),
                ToolMessage(content="On branch docs/demo-checklist\nChanges not staged for commit:\n  modified: README.md",
                            name="developer_git_status", tool_call_id="call_status"),
                ToolMessage(content="# Demo release notes\n\nA fictional local repository for documentation capture.\n",
                            name="developer_read_file", tool_call_id="call_readme"),
                AIMessage(content="", tool_calls=[
                    _call("developer_write_file", {"path": "README.md"}, "call_write"),
                    _call("developer_run_detected_test", {}, "call_test"),
                ]),
                ToolMessage(content="Updated README.md (+3 lines).", name="developer_write_file",
                            tool_call_id="call_write"),
                ToolMessage(content="1 passed in 0.04s", name="developer_run_detected_test", tool_call_id="call_test"),
                AIMessage(content=(
                    "The README now has a **Recovery** section and the test passes. "
                    "Committing changes the repository, so it waits for your approval."
                ), tool_calls=[
                    _call("developer_commit_changes", {"message": "Add the recovery note to the release notes"},
                          "call_commit"),
                ]),
            ],
        },
        {
            "id": DESIGN_THREAD,
            "title": "Community workshop deck",
            "minutes": 70,
            "messages": [
                HumanMessage(content="Make a two-slide deck for the community workshop: a welcome slide and the three review steps."),
                AIMessage(content="", tool_calls=[
                    _call("create_design", {"name": "Community Workshop Deck", "mode": "deck", "pages": 2}, "call_design"),
                ]),
                ToolMessage(content="Created Community Workshop Deck with 2 slides.", name="create_design",
                            tool_call_id="call_design"),
                AIMessage(content=(
                    "The deck is open in the Design panel: a welcome slide and the three review steps, "
                    "in the workshop's blue and green. Tell me what to change, or edit a slide directly."
                )),
            ],
        },
        {
            "id": "docs-demo-research",
            "title": "Research digest",
            "minutes": 60 * 20,
            "messages": [
                HumanMessage(content="What did we learn about local embeddings last week?"),
                AIMessage(content=(
                    "Two things stood out in your notes:\n\n"
                    "- **Small local models are enough** for searching a few hundred documents.\n"
                    "- **Re-indexing is quick**: the demo library rebuilt in under a minute.\n\n"
                    "Both are saved in Knowledge under *Embeddings*."
                )),
            ],
        },
        {
            "id": "docs-demo-garden",
            "title": "Balcony garden watering plan",
            "minutes": 60 * 26,
            "messages": [
                HumanMessage(content="Plan a watering schedule for tomatoes and basil on a sunny balcony."),
                AIMessage(content=(
                    "- **Tomatoes**: water deeply every morning in hot weeks, every other day otherwise.\n"
                    "- **Basil**: keep the soil just moist; water when the top centimetre is dry.\n"
                    "- Water at the base, not the leaves, and add mulch to keep the pots cool."
                )),
            ],
        },
        {
            "id": "docs-demo-books",
            "title": "Book club questions",
            "minutes": 60 * 24 * 4,
            "messages": [
                HumanMessage(content="Suggest five discussion questions for a mystery novel."),
                AIMessage(content=(
                    "1. When did you first suspect the culprit, and why?\n"
                    "2. Which clue felt fair, and which felt hidden?\n"
                    "3. How did the setting shape the mystery?\n"
                    "4. Did the detective change by the end?\n"
                    "5. Would the story work told from another character's view?"
                )),
            ],
        },
        {
            "id": "docs-demo-cycling",
            "title": "Weekend cycling route",
            "minutes": 60 * 24 * 9,
            "messages": [
                HumanMessage(content="Suggest an easy 30 km loop for a Saturday morning ride."),
                AIMessage(content=(
                    "Try the **Riverside Loop**: flat paths along the water, a café stop at the halfway "
                    "bridge, and a gentle climb back through the park. Allow about two hours."
                )),
            ],
        },
        {
            "id": "docs-demo-workflow",
            "title": "Morning brief run",
            "minutes": 60 * 3,
            "thread_type": "workflow",
            "messages": [
                HumanMessage(content="Summarise the two local demo documents and prepare a concise morning brief."),
                AIMessage(content=(
                    "**Morning brief**\n\n- The launch checklist is ready for review.\n"
                    "- The Support FAQ has no open questions.\n- One workflow is waiting for your approval."
                )),
            ],
        },
    ]


def _seed_conversations(state: dict) -> None:
    from row_bot import threads

    for item in _conversations():
        threads.create_thread(
            item["title"],
            thread_id=item["id"],
            thread_type=item.get("thread_type", ""),
            approval_mode="approve",
            name_source="manual",
        )
        threads.append_checkpoint_messages(item["id"], item["messages"])
        moment = _ago(minutes=item["minutes"]).isoformat()
        with sqlite3.connect(threads.DB_PATH) as conn:
            conn.execute(
                "UPDATE thread_meta SET created_at = ?, updated_at = ? WHERE thread_id = ?",
                (moment, moment, item["id"]),
            )
    state["threads"] = [
        {"id": item["id"], "name": item["title"], "kind": item.get("thread_type") or "chat"}
        for item in _conversations()
    ]


def _seed_demo_files(data_dir: Path, state: dict) -> None:
    workspace = data_dir / "docs-demo-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "launch-checklist.md").write_text(
        "# Demo Launch Checklist\n\n"
        "- Confirm local model path.\n"
        "- Review approvals before file writes.\n"
        "- Keep channels disabled until credentials are added.\n",
        encoding="utf-8",
    )
    (workspace / "support-faq.md").write_text(
        "# Support FAQ\n\nAll demo records use example.com addresses and fake provider states.\n",
        encoding="utf-8",
    )
    docs_dir = data_dir / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "Launch brief.txt").write_text(
        "Demo launch brief for Row-Bot public documentation screenshots.\n",
        encoding="utf-8",
    )
    _write_json(data_dir / "provider_catalog_cache.json", {"providers": state.get("providers", [])})
    _write_json(data_dir / "docs_demo_review_state.json", state)


def _seed_profiles_goals_and_agents(state: dict) -> None:
    from row_bot.agent_profiles import save_agent_profile
    from row_bot.agent_runs import create_agent_run, create_agent_run_edge, finish_agent_run, record_agent_run_progress
    from row_bot.goals import start_goal

    for profile in state.get("profiles", []):
        save_agent_profile(
            id=profile["id"],
            slug=profile["slug"],
            display_name=profile["display_name"],
            description=profile["description"],
            capability=profile["capability"],
            enabled=True,
            tool_policy_json={"allow": ["documents", "memory"], "deny": []},
            skill_policy_json={"allow": ["research"], "deny": []},
            context_policy_json={"mode": "summary"},
            workspace_policy_json={"mode": "read_only", "lock": False},
            model_policy_json={"model": "ollama/llama3.1:8b"},
            approval_policy_json={"mode": "approve"},
            output_contract_json={"summary": True, "tests": False},
            limits_json={"max_turns": 8, "timeout_seconds": 600},
        )

    goal = start_goal(
        state["goal"]["thread_id"],
        state["goal"]["objective"],
        max_turns=state["goal"]["max_turns"],
    )
    with sqlite3.connect(Path(os.environ["ROW_BOT_DATA_DIR"]) / "tasks.db") as conn:
        conn.execute(
            "UPDATE thread_goals SET turns_used = ?, last_progress = ?, blockers_json = ?, "
            "evidence_json = ?, last_reason = ? WHERE id = ?",
            (
                state["goal"]["turns_used"],
                state["goal"]["progress"][-1],
                json.dumps(state["goal"]["blockers"]),
                json.dumps(state["goal"]["progress"]),
                "Checklist draft is ready for review.",
                goal["id"],
            ),
        )

    parent = state["agents"][0]
    # The coordinating agent works in its own thread under the conversation.
    create_agent_run(
        run_id=parent["id"],
        kind=parent["kind"],
        status=parent["status"],
        parent_thread_id=DEMO_THREAD,
        thread_id=parent["thread_id"],
        display_name=parent["display_name"],
        prompt="Coordinate the fictional launch review.",
        profile_id="docs-profile-project",
        summary=parent["summary"],
        max_turns=6,
        turns_used=2,
    )
    for child in state["agents"][1:]:
        create_agent_run(
            run_id=child["id"],
            kind=child["kind"],
            status="running" if child["status"] == "completed" else child["status"],
            parent_run_id=parent["id"],
            root_run_id=parent["id"],
            parent_thread_id=DEMO_THREAD,
            thread_id=child["thread_id"],
            display_name=child["display_name"],
            prompt=child["summary"],
            profile_id="docs-profile-research",
            max_turns=4,
        )
        create_agent_run_edge(parent["id"], child["id"])
        record_agent_run_progress(child["id"], steps_done=2 if child["status"] == "completed" else 1, steps_total=2)
        if child["status"] == "completed":
            finish_agent_run(child["id"], "completed", summary=child["summary"])


def _fix_approval_id(old_id: str, new_id: str) -> None:
    with sqlite3.connect(Path(os.environ["ROW_BOT_DATA_DIR"]) / "tasks.db") as conn:
        conn.execute("UPDATE approval_requests SET id = ? WHERE id = ?", (new_id, old_id))


def _seed_chat_approval() -> None:
    """The code conversation's commit waits for Approve or Deny in place."""
    from row_bot.tasks import create_approval_request

    interrupt = {
        "tool": "developer_commit_changes",
        "label": "Commit changes",
        "description": "Commit the README change in demo-release-notes",
        "args": {"message": "Add the recovery note to the release notes"},
        "tool_call_id": "call_commit",
        "risk_class": "write",
        "scope": "One commit on the docs/demo-checklist branch of the demo folder.",
    }
    _token, approval_id = create_approval_request(
        "docs-chat-approval-pass",
        "",
        "conversation",
        interrupt["description"],
        resume_kind="conversation",
        source_thread_id=CODE_THREAD,
        parent_thread_id=CODE_THREAD,
        approval_payload_json={
            "interrupt": interrupt,
            "interrupt_ids": [],
            "pass_id": "docs-chat-approval-pass",
            "model_selection": {"provider_id": "ollama", "model_ref": "model:ollama:llama3.1:8b"},
        },
    )
    _fix_approval_id(approval_id, CHAT_APPROVAL_ID)


def _seed_workflows(state: dict) -> None:
    from row_bot import tasks

    brief_id = tasks.create_task(
        "Morning Brief",
        prompts=["Summarise the two local demo documents.", "Prepare a concise morning brief."],
        description="A safe scheduled summary from local demo sources.",
        schedule="daily:08:00",
        safety_mode="approve",
        channels=[],
        persistent_thread_id="docs-demo-workflow",
    )
    approval_id = tasks.create_task(
        "Launch Summary",
        description="Draft then approve a fictional launch summary.",
        steps=[
            {"id": "draft", "type": "prompt", "prompt": "Draft the launch summary.", "next": "review"},
            {"id": "review", "type": "approval", "message": "Approve writing the demo summary?", "next": "publish"},
            {"id": "publish", "type": "prompt", "prompt": "Write the approved summary to the demo workspace."},
        ],
        advanced_mode=True,
        safety_mode="approve",
        channels=[],
    )
    research_id = tasks.create_task(
        "Research Digest",
        prompts=["Search the local demo knowledge graph.", "Summarise matching evidence."],
        description="A weekly research round-up with a safe retry example.",
        schedule="weekly:friday:16:00",
        safety_mode="block",
        channels=None,
    )
    tidy_id = tasks.create_task(
        "Weekly Tidy-up",
        prompts=["List conversations finished this week.", "Summarise what was decided."],
        description="Summarises the week's finished conversations every Sunday evening.",
        schedule="weekly:sunday:18:00",
        safety_mode="block",
        channels=[],
    )
    tasks.create_task(
        "Garden Reminder",
        prompts=["Remind me to water the balcony plants."],
        description="An evening reminder to water the balcony garden.",
        schedule="daily:19:30",
        notify_only=True,
        notify_label="Water the balcony plants",
        channels=[],
    )
    state["workflows"][0]["id"] = brief_id
    state["workflows"][1]["id"] = approval_id
    state["workflows"][2]["id"] = research_id

    for days in (3, 2, 1):
        run = tasks._record_run_start(brief_id, "docs-demo-workflow", 2, "Morning Brief", "bolt")
        tasks._update_run_progress(run, 2)
        tasks._finish_run(run, "completed", "Demo brief created successfully.")
        _backdate_run(run, days)
    complete_run = tasks._record_run_start(brief_id, "docs-demo-workflow", 2, "Morning Brief", "bolt")
    tasks._update_run_progress(complete_run, 2)
    tasks._finish_run(complete_run, "completed", "Demo brief created successfully.")
    _backdate_run(complete_run, 0, hours=3)
    tidy_run = tasks._record_run_start(tidy_id, "docs-workflow-tidy", 2, "Weekly Tidy-up", "bolt")
    tasks._update_run_progress(tidy_run, 2)
    tasks._finish_run(tidy_run, "completed", "Four conversations summarised.")
    _backdate_run(tidy_run, 4)
    failed_run = tasks._record_run_start(research_id, "docs-workflow-failed", 2, "Research Digest", "science")
    tasks._update_run_progress(failed_run, 1)
    tasks._finish_run(failed_run, "failed", "Demo source unavailable; safe to retry.")
    _backdate_run(failed_run, 0, hours=2)
    pending_run = tasks._record_run_start(approval_id, "docs-workflow-approval", 3, "Launch Summary", "approval")
    tasks._update_run_progress(pending_run, 1)
    _token, request_id = tasks.create_approval_request(
        pending_run,
        approval_id,
        "review",
        "Approve writing the fictional launch summary to the demo workspace?",
        timeout_minutes=0,
        source_label="Launch Summary",
        source_thread_id="docs-workflow-approval",
        approval_payload_json={"action": "write demo summary", "path": "%ROW_BOT_DATA_DIR%/docs-demo-workspace/summary.md"},
    )
    _fix_approval_id(request_id, WORKFLOW_APPROVAL_ID)
    with sqlite3.connect(Path(os.environ["ROW_BOT_DATA_DIR"]) / "tasks.db") as conn:
        for task_id, hours in ((brief_id, 3), (tidy_id, 24 * 4), (research_id, 2)):
            conn.execute("UPDATE tasks SET last_run = ? WHERE id = ?", (_ago(hours=hours).isoformat(), task_id))


def _backdate_run(run_id: object, days: int, *, hours: float = 0) -> None:
    started = _ago(days=days, hours=hours)
    finished = started + timedelta(minutes=2)
    with sqlite3.connect(Path(os.environ["ROW_BOT_DATA_DIR"]) / "tasks.db") as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(task_runs)")}
        updates = {"started_at": started.isoformat(), "finished_at": finished.isoformat()}
        sets = [f"{name} = ?" for name in updates if name in columns]
        values = [value for name, value in updates.items() if name in columns]
        if sets:
            conn.execute(f"UPDATE task_runs SET {', '.join(sets)} WHERE id = ?", (*values, run_id))


# Knowledge -------------------------------------------------------------------

_ENTITIES = [
    # (key, type, subject, description)
    ("launch", "project", "Demo Launch", "A fictional product launch used only for documentation."),
    ("workshop", "project", "Community Workshop", "A free evening workshop on safer local AI workflows."),
    ("release", "project", "Release Notes", "The demo repository's notes for each version."),
    ("garden", "project", "Balcony Garden", "Tomatoes and basil on a sunny balcony."),
    ("checklist", "fact", "Launch Checklist", "Five review steps derived from the demo brief."),
    ("faq", "fact", "Support FAQ", "Answers for a fictional support team."),
    ("brief", "fact", "Launch Brief", "The one-page brief the checklist is checked against."),
    ("review", "concept", "Safety Review", "Approval is required before consequential writes."),
    ("approvals", "concept", "Approval Gates", "Actions that change things wait for a person's approval."),
    ("recovery", "concept", "Recovery Plan", "Keep the previous version so a change can be undone."),
    ("localfirst", "concept", "Local-first AI", "Models and data stay on this computer by default."),
    ("embeddings", "concept", "Embeddings", "Small local models are enough to search a few hundred documents."),
    ("graph", "concept", "Knowledge Graph", "Memories linked by how they relate."),
    ("dream", "concept", "Dream Cycle", "Overnight review that merges duplicates and links memories."),
    ("hall", "place", "Community Hall", "The workshop venue, with a projector and forty seats."),
    ("library", "place", "Riverside Library", "Hosts the book club on the first Thursday of the month."),
    ("makers", "organisation", "Neighbourhood Makers Club", "Volunteers who run the community workshop."),
    ("dryrun", "event", "Workshop Dry Run", "A practice session on the Friday before the workshop."),
    ("workshopday", "event", "Workshop Evening", "The community workshop itself, 18:30 to 20:30."),
    ("deck", "media", "Workshop Slide Deck", "Two slides: a welcome and the three review steps."),
    ("chart", "media", "Readiness Chart", "Shows which checklist items are done."),
    ("short", "preference", "Prefers short summaries", "Keep answers brief, with the detail on request."),
    ("metric", "preference", "Uses metric units", "Distances in kilometres and temperatures in Celsius."),
    ("morning", "preference", "Morning brief at 8:00", "A short brief every weekday morning."),
    ("markdown", "skill", "Markdown tables", "Formats comparisons as small tables."),
    ("outline", "skill", "Slide outlines", "Turns a brief into a slide-by-slide outline."),
    ("tomatoes", "fact", "Tomatoes", "Water deeply every morning in hot weeks."),
    ("basil", "fact", "Basil", "Keep the soil just moist."),
    ("watering", "fact", "Watering Schedule", "Morning watering at the base of each plant."),
    ("bookclub", "project", "Book Club", "Monthly reading group; this month a mystery novel."),
    ("questions", "fact", "Discussion Questions", "Five questions about clues, setting and character."),
    ("mystery", "media", "Mystery Novel", "This month's book club choice."),
    ("cycling", "project", "Weekend Rides", "Easy Saturday morning rides."),
    ("loop", "place", "Riverside Loop", "A flat 30 km route with a café stop at the bridge."),
    ("model", "self_knowledge", "Local model", "Row-Bot answers with llama3.1:8b on this computer."),
    ("wiki", "concept", "Wiki Vault", "Memories written out as linked Markdown pages."),
]

_RELATIONS = [
    ("launch", "checklist", "uses"),
    ("checklist", "review", "builds_on"),
    ("faq", "launch", "part_of"),
    ("brief", "launch", "part_of"),
    ("checklist", "brief", "cites"),
    ("review", "approvals", "uses"),
    ("recovery", "review", "part_of"),
    ("checklist", "recovery", "uses"),
    ("workshop", "launch", "builds_on"),
    ("workshop", "hall", "located_in"),
    ("makers", "workshop", "leads"),
    ("dryrun", "workshop", "part_of"),
    ("workshopday", "workshop", "scheduled_for"),
    ("deck", "workshop", "part_of"),
    ("chart", "deck", "part_of"),
    ("outline", "deck", "uses"),
    ("workshop", "localfirst", "teaches"),
    ("localfirst", "embeddings", "uses"),
    ("graph", "embeddings", "uses"),
    ("dream", "graph", "builds_on"),
    ("wiki", "graph", "extends"),
    ("release", "recovery", "uses"),
    ("release", "markdown", "uses"),
    ("model", "localfirst", "part_of"),
    ("model", "short", "prefers"),
    ("model", "morning", "prefers"),
    ("model", "metric", "prefers"),
    ("model", "embeddings", "uses"),
    ("localfirst", "approvals", "builds_on"),
    ("localfirst", "graph", "uses"),
    ("workshop", "review", "teaches"),
    ("workshop", "approvals", "teaches"),
    ("dryrun", "hall", "located_in"),
    ("workshopday", "hall", "located_in"),
    ("makers", "dryrun", "participates_in"),
    ("makers", "library", "visits"),
    ("bookclub", "makers", "part_of"),
    ("release", "checklist", "cites"),
    ("faq", "brief", "cites"),
    ("chart", "checklist", "cites"),
    ("dream", "wiki", "uses"),
    ("questions", "markdown", "uses"),
    ("tomatoes", "garden", "part_of"),
    ("basil", "garden", "part_of"),
    ("watering", "garden", "part_of"),
    ("watering", "tomatoes", "treats"),
    ("watering", "metric", "uses"),
    ("bookclub", "library", "located_in"),
    ("questions", "bookclub", "part_of"),
    ("mystery", "bookclub", "part_of"),
    ("questions", "mystery", "cites"),
    ("loop", "cycling", "part_of"),
    ("loop", "metric", "uses"),
    ("makers", "hall", "based_in"),
]


def _seed_knowledge_and_wiki(data_dir: Path, state: dict) -> None:
    from row_bot import knowledge_graph as kg
    from row_bot import wiki_vault

    previous = kg._skip_reindex
    kg._skip_reindex = True
    by_key: dict[str, str] = {}
    try:
        for key, entity_type, subject, description in _ENTITIES:
            entity = kg.save_entity(
                entity_type,
                subject,
                description,
                tags="demo,documentation",
                properties={"provenance": "docs demo fixture", "reviewed": True},
                source="docs-demo",
            )
            by_key[key] = entity["id"]
        for source_key, target_key, relation in _RELATIONS:
            kg.add_relation(
                by_key[source_key],
                by_key[target_key],
                relation,
                confidence=0.95,
                properties={"provenance": "docs demo fixture"},
                source="docs-demo",
            )
    finally:
        kg._skip_reindex = previous
    wiki_vault.set_vault_path(str(data_dir / "wiki-vault"))
    wiki_vault.set_enabled(True)
    wiki_vault.rebuild_vault()
    _write_json(
        data_dir / "dream_config.json",
        {"enabled": True, "window_start": 1, "window_end": 5, "last_run": _ago(hours=8).date().isoformat()},
    )
    _write_json(
        data_dir / "dream_journal.json",
        [
            {"timestamp": _ago(hours=7).isoformat(), "phase": "review", "message": "Reviewed 36 demo memories."},
            {"timestamp": _ago(hours=7).isoformat(), "phase": "complete", "message": "No duplicates required merging."},
        ],
    )


def _seed_insights() -> None:
    from row_bot import insights

    for category, severity, title, body, suggestion in (
        (
            "error_pattern", "warning", "Research Digest failed on a missing source",
            "The last Research Digest run stopped because a demo source was unavailable.",
            "Retry the workflow, or remove the missing source from its first step.",
        ),
        (
            "tool_config", "info", "Turn on web search for research questions",
            "Three recent questions asked for current information that local tools cannot reach.",
            "Enable a search tool in Settings › Tools if you want answers from the web.",
        ),
        (
            "knowledge_quality", "info", "Two garden memories could be linked",
            "Watering Schedule and Basil describe the same routine but are not connected.",
            "Link them so recall finds both together.",
        ),
        (
            "usage_pattern", "info", "Morning briefs are read within minutes",
            "You open the Morning Brief soon after 8:00 on most weekdays.",
            "Keep the schedule; it matches how you use it.",
        ),
    ):
        insights.add_insight(
            category=category,
            severity=severity,
            title=title,
            body=body,
            suggestion=suggestion,
            confidence=0.8,
            source="docs-demo",
            found_with_model="model:ollama:llama3.1:8b",
        )
    found = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    store = insights._load_store()
    for insight in store["insights"]:
        insight["created"] = found
    insights._save_store(store)
    insights.set_last_analysis(found)


def _seed_monitor_health(data_dir: Path) -> None:
    """Monitor's kept check results: healthy areas and one stopped channel."""
    checked = time.time() - 6 * 60

    def entry(name: str, status: str, detail: str, tab: str, network: bool = False) -> dict:
        return {"name": name, "status": status, "detail": detail, "checked_at": checked,
                "settings_tab": tab, "network": network}

    checks = {
        "ollama": entry("Ollama", "ok", "Server reachable", "Models"),
        "model": entry("Model", "ok", "llama3.1:8b", "Models"),
        "cloud-api": entry("Cloud API", "inactive", "No API keys", "Providers", True),
        "tts": entry("TTS", "inactive", "Disabled", "Voice"),
        "channel:whatsapp": entry("WhatsApp", "warn", "Stopped", "Channels"),
        "channel:slack": entry("Slack", "inactive", "Not configured", "Channels"),
        "tunnel": entry("Tunnel", "inactive", "Public link off", "Access"),
        "mcp": entry("MCP", "inactive", "Disabled · 2 servers", "MCP"),
        "plugins": entry("Plugins", "ok", "1 installed", "Plugins"),
        "skills": entry("Skills", "ok", "12 enabled", "Skills"),
        "tools": entry("Tools", "ok", "24 / 31 enabled", "Tools"),
        "workflows": entry("Workflows", "ok", "5 workflows · next at 19:30", "Workflows"),
        "dream-cycle": entry("Dream Cycle", "ok", "Last: last night", "Preferences"),
        "tracker": entry("Tracker", "ok", "Enabled", "Tracker"),
        "knowledge": entry("Knowledge", "ok", "36 memories", "Knowledge"),
        "faiss-index": entry("FAISS Index", "ok", "36 vectors", "Knowledge"),
        "wiki-vault": entry("Wiki Vault", "ok", "36 pages", "Knowledge"),
        "documents": entry("Documents", "ok", "1 document indexed", "Documents"),
        "disk": entry("Disk", "ok", "212.4 GB free", "System"),
        "threads-db": entry("Threads DB", "ok", "8 threads", "System"),
        "logging": entry("Logging", "ok", "INFO", "System"),
        "network": entry("Network", "ok", "Connected", "System", True),
        "buddy": entry("Buddy", "ok", "Window · Default", "Buddy"),
    }
    _write_json(data_dir / "system_health.json", {"hourly_network_checks": False, "checks": checks})


def _seed_tools_and_tracker(data_dir: Path) -> None:
    """The file tools' folder is the demo workspace, and two habits are tracked."""
    from row_bot.tools import registry
    from row_bot.tools import tracker_tool

    registry.set_tool_config("filesystem", "workspace_root", str(data_dir / "docs-demo-workspace"))
    if registry.get_tool("tracker") is not None:
        registry.set_enabled("tracker", True)
    conn = tracker_tool._get_db()
    try:
        for name, kind, unit, values in (
            ("Water the balcony plants", "boolean", None, ["true"] * 9),
            ("Morning walk", "numeric", "min", ["25", "30", "20", "35", "30", "40", "25", "30", "45"]),
            ("Glasses of water", "numeric", "glasses", ["6", "7", "5", "8", "6", "7", "8", "6", "7"]),
        ):
            tracker = tracker_tool._create_tracker(conn, name, kind, unit)
            for index, value in enumerate(values):
                moment = _ago(days=len(values) - index, hours=2)
                tracker_tool._log_entry(conn, tracker["id"], value, timestamp=moment.strftime("%Y-%m-%dT%H:%M:%S"))
    finally:
        conn.close()


# Developer and Designer ------------------------------------------------------

def _seed_developer_workspace(data_dir: Path, state: dict) -> None:
    from row_bot.conversation_resources import bind, list_bindings
    from row_bot.developer.storage import add_or_update_local_workspace

    repo = data_dir / "docs-demo-workspace" / "demo-release-notes"
    repo.mkdir(parents=True, exist_ok=True)
    readme = repo / "README.md"
    readme.write_text("# Demo release notes\n\nA fictional local repository for documentation capture.\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## 1.2.0\n\n- Launch checklist reviewed.\n- Support FAQ updated.\n", encoding="utf-8"
    )
    test_file = repo / "test_demo.py"
    test_file.write_text("def test_demo_checklist():\n    assert ['review', 'approve', 'write'][-1] == 'write'\n", encoding="utf-8")
    git = ["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "config", "user.name", "Row-Bot Docs Demo"], cwd=repo, check=True)
    subprocess.run([*git, "config", "user.email", "docs-demo@example.com"], cwd=repo, check=True)
    subprocess.run([*git, "add", "README.md", "CHANGELOG.md", "test_demo.py"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "Seed fictional demo project"], cwd=repo, check=True)
    subprocess.run([*git, "switch", "-q", "-c", "docs/demo-checklist"], cwd=repo, check=True)
    readme.write_text(
        "# Demo release notes\n\nA fictional local repository for documentation capture.\n\n"
        "## Recovery\n\nKeep the previous checklist as a draft so any change can be undone.\n",
        encoding="utf-8",
    )
    workspace = add_or_update_local_workspace(str(repo))
    state["developer"]["workspace_id"] = workspace.id
    _write_json(
        data_dir / "developer" / "docs_demo_inspector.json",
        {"todos": [{"label": state["developer"]["todo"], "status": "in_progress"}], "test_output": state["developer"]["test_output"]},
    )
    snapshot = list_bindings(CODE_THREAD)
    bind(CODE_THREAD, "workspace", workspace.id, expected_revision=snapshot.revision, role="primary")


# Two 1920x1080 slides for the fictional workshop deck.
_WELCOME_SLIDE = (
    "<section style=\"width:1920px;height:1080px;box-sizing:border-box;padding:160px 180px;"
    "display:flex;flex-direction:column;justify-content:center;gap:36px;"
    "background:linear-gradient(135deg,#0f172a 0%,#1e3a8a 100%);color:#f8fafc;font-family:Inter,sans-serif\">"
    "<p style=\"margin:0;font-size:34px;letter-spacing:6px;color:#86efac;font-weight:600\">COMMUNITY WORKSHOP</p>"
    "<h1 style=\"margin:0;font-size:120px;line-height:1.05;font-weight:800;max-width:1400px\">"
    "Build a safer local AI workflow</h1>"
    "<div style=\"width:220px;height:12px;border-radius:6px;background:#22c55e\"></div>"
    "<p style=\"margin:0;font-size:44px;color:#cbd5e1\">Neighbourhood Makers Club &middot; Community Hall &middot; 18:30</p>"
    "</section>"
)
_STEPS_SLIDE = (
    "<section style=\"width:1920px;height:1080px;box-sizing:border-box;padding:140px 160px;"
    "background:#f8fafc;color:#0f172a;font-family:Inter,sans-serif\">"
    "<h1 style=\"margin:0 0 90px;font-size:96px;font-weight:800\">Three review steps</h1>"
    "<div style=\"display:grid;grid-template-columns:repeat(3,1fr);gap:56px\">"
    + "".join(
        "<div style=\"background:#ffffff;border-radius:32px;padding:64px 56px;"
        "box-shadow:0 12px 40px rgba(15,23,42,.10);border-top:14px solid " + color + "\">"
        "<p style=\"margin:0 0 28px;font-size:88px;font-weight:800;color:" + color + "\">" + number + "</p>"
        "<p style=\"margin:0;font-size:52px;font-weight:700;line-height:1.2\">" + title + "</p>"
        "<p style=\"margin:28px 0 0;font-size:34px;line-height:1.4;color:#475569\">" + detail + "</p></div>"
        for number, title, detail, color in (
            ("1", "Check local sources", "Read the brief and notes on this computer.", "#2563eb"),
            ("2", "Approve actions", "Anything that changes files waits for you.", "#22c55e"),
            ("3", "Keep a recovery path", "Save the previous version first.", "#f59e0b"),
        )
    )
    + "</div></section>"
)


def _seed_designer_project(state: dict) -> None:
    from row_bot.designer.state import BrandConfig, DesignerAsset, DesignerPage, DesignerProject, ProjectBrief
    from row_bot.designer.storage import save_project

    project = DesignerProject(
        id=state["designer"]["project_id"],
        name=state["designer"]["name"],
        mode="deck",
        template_id="clean-presentation",
        pages=[
            DesignerPage(
                title="Welcome",
                notes="Open with the workshop purpose and a friendly local-first message.",
                html=_WELCOME_SLIDE,
            ),
            DesignerPage(
                title="Review steps",
                notes="Explain review, approval, and recovery.",
                html=_STEPS_SLIDE,
            ),
        ],
        brand=BrandConfig(primary_color="#2563EB", accent_color="#22C55E"),
        brief=ProjectBrief(output_type="Presentation", audience="Community organisers", tone="Clear and welcoming", length="2 slides"),
        assets=[DesignerAsset(id="asset-demo-chart", kind="chart", label="Workshop readiness", mime_type="application/json", filename="readiness-chart.json")],
        thread_id=DESIGN_THREAD,
    )
    save_project(project)


def _bind_designer_project(state: dict) -> None:
    """The design conversation opens with its deck (the docs profile only: the
    browser fixture reuses the project without that conversation)."""
    from row_bot.conversation_resources import bind, list_bindings

    snapshot = list_bindings(DESIGN_THREAD)
    bind(DESIGN_THREAD, "artifact", state["designer"]["project_id"], expected_revision=snapshot.revision, role="primary")


def _seed_integrations_and_mobile(data_dir: Path, state: dict) -> None:
    from row_bot.mcp_client.config import save_config
    from row_bot.mobile.store import MobileAuthStore
    from row_bot.providers.config import load_provider_config, save_provider_config
    from row_bot.providers.custom import normalize_custom_endpoint

    provider_cfg = load_provider_config()
    provider_cfg["custom_endpoints"] = [
        normalize_custom_endpoint(
            {
                "id": "docs-local-endpoint",
                "name": "Demo Local Endpoint",
                "base_url": "http://127.0.0.1:11435/v1",
                "profile": "openai_compatible",
                "transport": "openai_chat",
                "auth_required": False,
                "execution_location": "local",
                "risk_label": "local_private",
                "enabled": True,
                "capability_probe": False,
                "models": [{"id": "demo-chat", "model_id": "demo-chat", "display_name": "Demo Chat"}],
            }
        )
    ]
    save_provider_config(provider_cfg)

    save_config(
        {
            "enabled": False,
            "marketplace": {"enabled": True, "sources": ["official"]},
            "servers": {
                "Demo GitHub MCP": {
                    "enabled": False,
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "trust_level": "standard",
                    "source": {"catalog": "docs-demo"},
                },
                "Demo Browser MCP": {
                    "enabled": False,
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "@playwright/mcp"],
                    "trust_level": "standard",
                    "source": {"catalog": "docs-demo"},
                },
            },
        }
    )

    plugin_dir = data_dir / "installed_plugins" / "docs-demo-crm"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        plugin_dir / "plugin.json",
        {
            "schema_version": 2,
            "id": "docs-demo-crm",
            "name": "Demo CRM Lookup",
            "version": "1.0.0",
            "min_row_bot_version": "4.9.1",
            "author": {"name": "Row-Bot Docs Demo"},
            "description": "An inert fictional plugin used only for documentation capture.",
            "provides": {"native_tools": [], "mcp_servers": [], "channels": [], "skills": []},
            "permissions": [],
            "settings": {},
            "secrets": {},
            "auth": {},
            "health_checks": [],
        },
    )

    store = MobileAuthStore(data_dir / "mobile.db")
    paired = datetime.now(timezone.utc) - timedelta(days=2)
    store.create_device(
        device_id="docs-demo-android",
        display_name=state["mobile"]["device_name"],
        token_hash="demo-token-hash-not-a-secret",
        token_salt="demo-token-salt",
        user_agent="Android Demo Browser",
        paired_from="192.0.2.10",
        access_mode="trusted_lan",
        now=paired,
    )
    for index, event in enumerate(state["mobile"]["events"]):
        store.log_event(
            event.lower().replace(" ", "_"),
            event_id=f"docs-mobile-event-{index}",
            device_id="docs-demo-android",
            ip="192.0.2.10",
            user_agent="Android Demo Browser",
            detail={"display": event, "source": "docs demo"},
            now=paired + timedelta(minutes=1 + index),
        )


def main() -> int:
    args = _parse_args()
    data_dir = Path(args.data_dir).resolve()
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.environ["ROW_BOT_DATA_DIR"] = str(data_dir)
    if args.ollama_host:
        os.environ["OLLAMA_HOST"] = args.ollama_host
    os.environ.setdefault("ROW_BOT_DOCS_CAPTURE", "1")
    os.environ.setdefault("ROW_BOT_DOCS_FIXED_NOW", "2026-06-18T09:00:00Z")

    from row_bot.docs_capture import (
        SCENARIOS,
        default_docs_capture_demo_state,
        scan_demo_data_safety,
        write_docs_capture_demo_state,
    )

    scenario = str(args.scenario or "full")
    if scenario not in SCENARIOS:
        raise SystemExit(f"Unknown scenario {scenario!r}. Expected one of: {', '.join(sorted(SCENARIOS))}")
    first_run = scenario == "first-run"
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = write_docs_capture_demo_state(data_dir, scenario=scenario)
    state = default_docs_capture_demo_state()
    state["scenario"] = scenario
    _seed_app_config(data_dir, first_run=first_run)
    if not first_run:
        _seed_conversations(state)
        _seed_demo_files(data_dir, state)
        _seed_profiles_goals_and_agents(state)
        _seed_chat_approval()
        _seed_workflows(state)
        _seed_knowledge_and_wiki(data_dir, state)
        _seed_developer_workspace(data_dir, state)
        _seed_designer_project(state)
        _bind_designer_project(state)
        _seed_integrations_and_mobile(data_dir, state)
        if args.ollama_host:
            _seed_model_catalog()
        _seed_insights()
        _seed_monitor_health(data_dir)
        _seed_tools_and_tracker(data_dir)
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    errors = scan_demo_data_safety(data_dir)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Seeded real docs demo data in {data_dir}")
    print(f"Wrote demo state to {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
