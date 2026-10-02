"""Capture the landing page's story stills and clips from the real React app.

    uv run --with imageio-ffmpeg python scripts/docs/capture_landing_media.py
    uv run --with imageio-ffmpeg python scripts/docs/capture_landing_media.py --scenes ship
    uv run python scripts/docs/capture_landing_media.py --serve

Row-Bot runs against an isolated temporary profile seeded with a fictional
neighbourhood solar co-op: conversations with a tool trace, a knowledge graph,
local workflows with their runs, a landing page design bound to its
conversation and an outward email waiting for approval. The local model runtime
is the docs capture's display-only stand-in on loopback, so nothing runs a
model. The Create scene's hosted model is display-only too: the harness tells
the client that ChatGPT / Codex is connected; no account is connected or
called. In the Ship scene the harness answers the Approve request itself, so
nothing is sent.

Each scene is driven with Playwright at 1440x810 in the dark appearance, with a
drawn pointer showing where the scripted clicks land. Stills are rendered at
twice the density and downscaled; clips are recorded from the browser's
screencast, resampled to 30 fps and encoded with ffmpeg (``imageio-ffmpeg``,
added with ``uv run --with``; it is not a project dependency). Output lands in
``docs/media/landing-story/``, with the manifest and recording receipt rewritten
to match (``--records`` rewrites only those); scratch stays under
``.tmp/landing-capture/``. ``--serve`` seeds and starts the app for a manual look
and stops it when ``.tmp/landing-capture/serve.stop`` appears.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import deque
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.docs.capture_real_ui_screenshots import (  # noqa: E402
    _free_port,
    _launch_app,
    _start_demo_ollama,
    _wait_ping,
)

SCRATCH = ROOT / ".tmp" / "landing-capture"
MEDIA = ROOT / "docs" / "media" / "landing-story"
WIDTH, HEIGHT = 1440, 810
FPS = 30
DISSOLVE_FRAMES = 4
SCENES = ("research", "create", "automate", "ship")

RESEARCH_THREAD = "landing-research"
CREATE_THREAD = "landing-create"
SHIP_THREAD = "landing-ship"
DIGEST_THREAD = "landing-digest-runs"
DESIGN_ID = "landing-launch-page"
SHIP_APPROVAL_ID = "1a4d1a000001"
LOCAL_MODEL = "model:ollama:qwen3:8b"
HOSTED_MODEL = "model:codex:gpt-5.6-sol"
CO_OP = "Riverbend Community Solar"
# The capture's clock: conversations, runs and the approval are dated around a
# late-morning "now", and the browser's clock runs from the same moment.
CLOCK_HOUR, CLOCK_MINUTE = 10, 40


def _anchor() -> datetime:
    return datetime.now().replace(hour=CLOCK_HOUR, minute=CLOCK_MINUTE, second=0, microsecond=0)


# Seeding (runs in its own process, bound to the capture profile) -------------

def _call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _conversations() -> list[dict]:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    def step(name: str, args: dict, call_id: str, result: str) -> list:
        return [AIMessage(content="", tool_calls=[_call(name, args, call_id)]),
                ToolMessage(content=result, name=name, tool_call_id=call_id)]

    return [
        {
            "id": RESEARCH_THREAD,
            "title": "Community solar research",
            "minutes": 28,
            "model": LOCAL_MODEL,
            "messages": [
                HumanMessage(content=(
                    "Research how community solar co-ops share savings with their members. "
                    "Use public sources, keep the evidence visible, and save what we learn to Knowledge."
                )),
                *step("search_memory", {"query": "Riverbend community solar"}, "r1",
                      "4 memories about Riverbend Community Solar"),
                *step("duckduckgo_search", {"query": "community solar co-op member savings"}, "r2",
                      "8 results from public sources"),
                *step("wikipedia", {"query": "community solar"}, "r3",
                      "3 articles: community solar, net metering, cooperatives"),
                *step("read_url", {"url": "https://en.wikipedia.org/wiki/Net_metering"}, "r4",
                      "Net metering · encyclopedia article"),
                *step("read_url", {"url": "https://en.wikipedia.org/wiki/Cooperative"}, "r5",
                      "Cooperative · encyclopedia article"),
                *step("save_memory", {"subject": "Bill credits", "category": "concept"}, "r6",
                      "Saved Bill credits"),
                *step("save_memory", {"subject": "Subscription model", "category": "concept"}, "r7",
                      "Saved Subscription model"),
                *step("link_memories", {"subject": CO_OP}, "r8",
                      f"Linked 12 memories to {CO_OP}"),
                AIMessage(content=(
                    "Here is what the public sources agree on:\n\n"
                    "- **Shared ownership**: members subscribe to, or buy, a share of one larger array "
                    "instead of fitting panels at home.\n"
                    "- **Bill credits**: each share's output shows up as a credit on the member's bill.\n"
                    "- **Subscribe or buy**: subscriptions avoid an upfront cost; bought shares pay back over time.\n"
                    "- **Renters can join**: no roof of their own is needed.\n"
                    "- **Local rules decide the credit**: confirm the calculation with the grid operator.\n\n"
                    "I saved 12 memories and linked them to *Riverbend Community Solar* in Knowledge."
                )),
            ],
        },
        {
            "id": CREATE_THREAD,
            "title": "Launch page direction",
            "minutes": 9,
            "model": HOSTED_MODEL,
            "messages": [
                HumanMessage(content=(
                    "Turn the research into a launch page for Riverbend Community Solar: a warm hero, "
                    "how shares work, and a clear invitation to join."
                )),
                *step("search_memory", {"query": "Riverbend community solar research"}, "c1",
                      "12 memories from Community solar research"),
                *step("create_design", {"name": "Riverbend launch page", "mode": "landing"}, "c2",
                      "Created Riverbend launch page"),
                AIMessage(content=(
                    "The launch page is open in the Design panel: a sunrise hero with the invitation to join, "
                    "three steps for how shares work, and what members get back. Edit any section directly, "
                    "or tell me what to change."
                )),
            ],
        },
        {
            "id": SHIP_THREAD,
            "title": "Share the launch page",
            "minutes": 2,
            "model": LOCAL_MODEL,
            "messages": [
                HumanMessage(content="Who should review the launch page before it goes public?"),
                *step("search_memory", {"query": "Riverbend board review"}, "s9",
                      "3 memories: Board chair, Board meets on the first Tuesday, Board pack"),
                AIMessage(content=(
                    "The board reviews anything members will read. Their shared address, "
                    "riverbend-board@example.com, reaches all five board members."
                )),
                HumanMessage(content="Check the launch page against the research before we share it."),
                *step("search_memory", {"query": "Riverbend launch page facts"}, "s0",
                      "9 memories behind the launch page"),
                AIMessage(content=(
                    "It matches the research: shares, bill credits and renter access are all explained, "
                    "and every figure on the page comes from Knowledge. The open evening date is on it too."
                )),
                HumanMessage(content="Export it as a PDF and email it to the co-op board for review."),
                *step("export_to_pdf", {"name": "riverbend-launch-page.pdf"}, "s1",
                      "Saved riverbend-launch-page.pdf (3 pages)"),
                AIMessage(
                    content="The PDF is ready. Emailing it leaves this computer, so it waits for your approval.",
                    tool_calls=[_call("send_gmail_message", {
                        "to": ["riverbend-board@example.com"],
                        "subject": "Launch page for review",
                        "attachments": ["riverbend-launch-page.pdf"],
                    }, "s2")],
                ),
            ],
        },
        {
            "id": DIGEST_THREAD,
            "title": "Weekly policy digest",
            "minutes": 60 * 26,
            "model": LOCAL_MODEL,
            "thread_type": "workflow",
            "messages": [
                HumanMessage(content="Check public sources for community solar rule changes and summarise them."),
                AIMessage(content=(
                    "**Policy digest**\n\n- No change to how bill credits are calculated this week.\n"
                    "- The small-grants round is open until the end of the month.\n"
                    "- Two new questions for the grid connection FAQ."
                )),
            ],
        },
        {
            "id": "landing-agenda",
            "title": "Board meeting agenda",
            "minutes": 95,
            "model": LOCAL_MODEL,
            "messages": [
                HumanMessage(content="Draft the agenda for Tuesday's board meeting."),
                AIMessage(content=(
                    "1. Survey results: 14 suitable roofs\n2. Launch page review\n"
                    "3. Grant application timeline\n4. Open evening at Market Hall"
                )),
            ],
        },
        {
            "id": "landing-survey",
            "title": "Rooftop survey checklist",
            "minutes": 60 * 23,
            "model": LOCAL_MODEL,
            "messages": [
                HumanMessage(content="Make a checklist for the rooftop survey day."),
                AIMessage(content=(
                    "- Roof direction and shading\n- Access and safety\n"
                    "- Structural notes for the surveyor\n- Photos of each roof"
                )),
            ],
        },
        {
            "id": "landing-volunteers",
            "title": "Volunteer onboarding notes",
            "minutes": 60 * 24 * 3,
            "model": LOCAL_MODEL,
            "messages": [
                HumanMessage(content="Summarise what new volunteers need to know."),
                AIMessage(content=(
                    "Volunteers help at the open evening, on survey days and with member questions. "
                    "Training night covers all three."
                )),
            ],
        },
        {
            "id": "landing-grant",
            "title": "Grant application outline",
            "minutes": 60 * 24 * 8,
            "model": LOCAL_MODEL,
            "messages": [
                HumanMessage(content="Outline the small-grants application."),
                AIMessage(content=(
                    "1. The co-op and who it serves\n2. The first array\n3. Costs the grant covers\n"
                    "4. Community benefit"
                )),
            ],
        },
    ]


def _seed_conversations(anchor: datetime) -> None:
    from row_bot import threads

    for item in _conversations():
        threads.create_thread(
            item["title"],
            thread_id=item["id"],
            thread_type=item.get("thread_type", ""),
            approval_mode="approve",
            model_override=item["model"],
            name_source="manual",
        )
        threads.append_checkpoint_messages(item["id"], item["messages"])
        moment = (anchor - timedelta(minutes=item["minutes"])).isoformat()
        with sqlite3.connect(threads.DB_PATH) as conn:
            conn.execute(
                "UPDATE thread_meta SET created_at = ?, updated_at = ? WHERE thread_id = ?",
                (moment, moment, item["id"]),
            )


def _tasks_db() -> sqlite3.Connection:
    return sqlite3.connect(Path(os.environ["ROW_BOT_DATA_DIR"]) / "tasks.db")


def _seed_workflows(anchor: datetime) -> None:
    from row_bot import tasks

    digest = tasks.create_task(
        "Weekly policy digest",
        prompts=[
            "Check public sources for changes to community solar rules this week.",
            "Summarise anything that affects Riverbend in five plain-language bullets.",
            "Save new facts to Knowledge and link them to Riverbend Community Solar.",
        ],
        description="Tracks community solar rule changes every Monday and keeps each run for review.",
        icon="📰",
        schedule="weekly:monday:08:00",
        model_override=LOCAL_MODEL,
        safety_mode="block",
        channels=[],
        persistent_thread_id=DIGEST_THREAD,
    )
    questions = tasks.create_task(
        "Member questions round-up",
        prompts=["Collect this week's member questions.", "Draft short answers for the FAQ."],
        description="Gathers member questions into draft FAQ answers.",
        icon="💬",
        schedule="weekly:friday:16:00",
        model_override=LOCAL_MODEL,
        safety_mode="block",
        channels=[],
    )
    survey = tasks.create_task(
        "Survey photo sorter",
        prompts=["Sort the new roof survey photos by site.", "List any roof that needs a second visit."],
        description="Files survey photos by roof and flags second visits.",
        icon="🏠",
        schedule="daily:18:00",
        model_override=LOCAL_MODEL,
        safety_mode="block",
        channels=[],
    )
    tasks.create_task(
        "Open evening reminder",
        prompts=["Remind me to confirm the Market Hall booking."],
        description="A reminder before the open evening.",
        icon="🔔",
        schedule="weekly:wednesday:09:00",
        notify_only=True,
        notify_label="Confirm the Market Hall booking",
        channels=[],
    )

    def run(task_id: str, thread: str, name: str, steps: int, days: float, message: str) -> None:
        run_id = tasks._record_run_start(task_id, thread, steps, name, "bolt")
        tasks._update_run_progress(run_id, steps)
        tasks._finish_run(run_id, "completed", message)
        started = anchor - timedelta(days=days)
        with _tasks_db() as conn:
            conn.execute(
                "UPDATE task_runs SET started_at = ?, finished_at = ? WHERE id = ?",
                (started.isoformat(), (started + timedelta(minutes=3)).isoformat(), run_id),
            )

    for week, note in enumerate((
        "Two rule updates summarised; 3 facts saved.",
        "No changes this week.",
        "Grant round opened; 2 facts saved.",
        "One consultation found; 1 fact saved.",
        "No changes this week.",
        "Connection FAQ updated; 2 facts saved.",
        "Credit rules unchanged; 1 fact saved.",
        "Three updates summarised; 4 facts saved.",
    )):
        run(digest, DIGEST_THREAD, "Weekly policy digest", 3, 1.1 + 7 * week, note)
    for week in range(5):
        run(questions, f"landing-questions-{week}", "Member questions round-up", 2, 4.7 + 7 * week,
            "Five questions answered in draft.")
    for day in range(9):
        run(survey, f"landing-survey-{day}", "Survey photo sorter", 2, 0.7 + day, "Photos filed by roof.")
    with _tasks_db() as conn:
        for task_id, days in ((digest, 1.1), (questions, 4.7), (survey, 0.7)):
            conn.execute("UPDATE tasks SET last_run = ? WHERE id = ?",
                         ((anchor - timedelta(days=days)).isoformat(), task_id))


# Knowledge: (key, type, subject, description)
_ENTITIES = [
    ("coop", "organisation", CO_OP, "A neighbourhood energy co-op planning its first shared solar array."),
    ("tenants", "organisation", "Mill Street Tenants Association", "Helps renters join without a roof of their own."),
    ("library", "organisation", "Riverbend Library", "Offers its south-facing roof and a meeting room."),
    ("school", "organisation", "Northside Primary School", "Its gym roof is shortlisted for a second array."),
    ("credit", "organisation", "Riverbend Credit Union", "Discussing low-interest loans for member shares."),
    ("grid", "organisation", "Regional grid operator", "Approves the connection and sets the credit rules."),
    ("city", "organisation", "City sustainability office", "Runs the small-grants round."),
    ("riverbend", "place", "Riverbend", "The neighbourhood the co-op serves."),
    ("libroof", "place", "Library roof", "South-facing, with room for the first array."),
    ("gymroof", "place", "Northside gym roof", "Large and flat; needs a structural check."),
    ("millst", "place", "Mill Street", "Terraced housing, mostly rented."),
    ("market", "place", "Market Hall", "Venue for the open evening."),
    ("depot", "place", "Old tram depot", "The largest roof in the neighbourhood."),
    ("chair", "person", "Board chair", "Chairs the monthly board meeting."),
    ("treasurer", "person", "Treasurer", "Keeps the share register and the savings model."),
    ("volco", "person", "Volunteer coordinator", "Runs training night and the survey rota."),
    ("surveyor", "person", "Site surveyor", "Checks each roof's direction, shade and structure."),
    ("liaison", "person", "Member liaison", "Answers member questions."),
    ("openeve", "event", "Open evening", "The launch event for founding members."),
    ("vote", "event", "Board vote on the first array", "Decides between the library and the depot."),
    ("surveyday", "event", "Roof survey day", "Volunteers and the surveyor visit each shortlisted roof."),
    ("deadline", "event", "Grant deadline", "The small-grants round closes at the end of the month."),
    ("training", "event", "Volunteer training night", "Covers the open evening, surveys and member questions."),
    ("launchday", "event", "Launch day", "The launch page goes live and the member drive starts."),
    ("launchpage", "project", "Launch page", "The public page inviting neighbours to join."),
    ("drive", "project", "Member drive", "Aims for 120 founding member households."),
    ("survey", "project", "Rooftop survey", "Shortlists roofs for the first two arrays."),
    ("grant", "project", "Grant application", "Asks the city to cover survey and legal costs."),
    ("battery", "project", "Battery pilot", "A small storage trial at the library."),
    ("digest", "project", "Weekly policy digest", "A local workflow that tracks rule changes."),
    ("boardpack", "project", "Board pack", "Papers for the board vote."),
    ("cs", "concept", "Community solar", "Many households share the output of one array."),
    ("shared", "concept", "Shared ownership", "Members own the array together."),
    ("credits", "concept", "Bill credits", "A share's output appears as a credit on the member's bill."),
    ("subscription", "concept", "Subscription model", "Members pay monthly instead of buying a share."),
    ("shares", "concept", "Member shares", "A one-off purchase that pays back over time."),
    ("netmeter", "concept", "Net metering", "Exported power offsets power bought from the grid."),
    ("vnm", "concept", "Virtual net metering", "Credits from one array spread across many bills."),
    ("fit", "concept", "Feed-in tariff", "A set price paid for exported power."),
    ("ppa", "concept", "Power purchase agreement", "A long-term contract to buy the array's output."),
    ("interconnect", "concept", "Grid connection", "Permission and equipment to connect the array."),
    ("capacity", "concept", "Capacity factor", "How much of its rated output an array delivers."),
    ("payback", "concept", "Payback period", "How long a share takes to repay its cost."),
    ("energycoop", "concept", "Energy cooperative", "A member-owned energy organisation."),
    ("control", "concept", "Democratic member control", "Members decide together."),
    ("onevote", "concept", "One member, one vote", "Every member has an equal say."),
    ("renters", "concept", "Renter access", "People without a roof can still take part."),
    ("equity", "concept", "Energy equity", "Fair access to clean, affordable power."),
    ("carveout", "concept", "Low-income places", "Shares set aside for households on low incomes."),
    ("storage", "concept", "Battery storage", "Keeps daytime power for the evening."),
    ("peak", "concept", "Peak shaving", "Using storage to cut demand at the busiest times."),
    ("inverter", "concept", "Inverter", "Turns the panels' direct current into mains power."),
    ("suitability", "concept", "Roof suitability", "Direction, shade, strength and access."),
    ("gridcap", "concept", "Grid capacity", "How much new generation the local network can take."),
    ("benefit", "concept", "Community benefit fund", "Surplus income set aside for local projects."),
    ("tariff", "concept", "Green tariff", "A supplier's renewable electricity offer."),
    ("localfirst", "concept", "Local-first research", "Research runs on this computer and keeps its sources."),
    ("evidence", "concept", "Visible evidence", "Every claim keeps a link to its source."),
    ("f_south", "fact", "Library roof faces south", "Good for a first array."),
    ("f_roofs", "fact", "Survey found 14 suitable roofs", "Out of 31 roofs checked."),
    ("f_target", "fact", "Founding target: 120 households", "Set by the board in spring."),
    ("f_instal", "fact", "Shares can be paid in instalments", "Agreed with the credit union."),
    ("f_monthly", "fact", "Credits appear on the monthly bill", "Confirmed in the grid connection FAQ."),
    ("f_noupfront", "fact", "Subscriptions need no upfront cost", "From the public sources."),
    ("f_renters", "fact", "Renters can join without a roof", "From the public sources."),
    ("f_months", "fact", "Connection approval takes months", "Start the application early."),
    ("f_tuesday", "fact", "Board meets on the first Tuesday", "Monthly, at the library."),
    ("f_thursday", "fact", "Open evening is on a Thursday", "At Market Hall, from 18:30."),
    ("f_vote", "fact", "Each member gets one vote", "Whatever the size of their share."),
    ("f_surplus", "fact", "Surplus goes to the community fund", "Agreed in the draft rules."),
    ("f_gym", "fact", "Gym roof needs a structural check", "Booked for survey day."),
    ("f_depot", "fact", "Depot roof is the largest site", "About three times the library roof."),
    ("f_grant", "fact", "Grant covers survey costs", "And part of the legal fees."),
    ("f_loans", "fact", "Credit union offers share loans", "Repaid over two years."),
    ("f_rules", "fact", "Credit rules unchanged this month", "From the latest policy digest."),
    ("f_faq", "fact", "Two new connection FAQ questions", "From the latest policy digest."),
    ("m_cs", "media", "Community solar article", "Public source read during research."),
    ("m_nm", "media", "Net metering article", "Public source read during research."),
    ("m_coop", "media", "Cooperative article", "Public source read during research."),
    ("m_guide", "media", "Co-op starter guide", "A public guide to setting up an energy co-op."),
    ("m_faq", "media", "Grid connection FAQ", "The grid operator's public questions and answers."),
    ("m_member", "media", "Member survey results", "What 86 neighbours said about joining."),
    ("m_photos", "media", "Roof survey photos", "Photos from survey day, filed by roof."),
    ("m_design", "media", "Launch page design", "The editable design in the Design panel."),
    ("m_deck", "media", "Board deck: first array", "Slides for the board vote."),
    ("m_flyer", "media", "Open evening flyer", "Printed for the library and Market Hall."),
    ("m_d38", "media", "Policy digest: week 38", "Weekly digest from the local workflow."),
    ("m_d39", "media", "Policy digest: week 39", "Weekly digest from the local workflow."),
    ("m_d40", "media", "Policy digest: week 40", "Weekly digest from the local workflow."),
    ("p_plain", "preference", "Prefers plain-language summaries", "Short sentences, no jargon."),
    ("p_delivery", "preference", "Keep delivery off for drafts", "Results stay in the app until reviewed."),
    ("p_metric", "preference", "Uses metric units", "Square metres and kilowatt-hours."),
    ("p_cite", "preference", "Cite sources inline", "Every claim names where it came from."),
    ("p_monday", "preference", "Digest on Monday mornings", "Ready before the week starts."),
    ("p_tone", "preference", "Warm, neighbourly tone", "For anything members read."),
    ("s_roof", "skill", "Roof suitability checks", "Direction, shade and access."),
    ("s_writing", "skill", "Plain-language writing", "Turns rules into everyday words."),
    ("s_model", "skill", "Savings modelling", "Share price, credits and payback."),
    ("s_grant", "skill", "Grant writing", "Structure, budget and benefit."),
    ("s_events", "skill", "Event planning", "Venues, rotas and flyers."),
    ("k_local", "self_knowledge", "Research runs on qwen3:8b", "On this computer, with sources kept."),
    ("k_hosted", "self_knowledge", "Hosted models only when chosen", "A frontier model joins only when picked."),
    ("k_drafts", "self_knowledge", "Drafts stay on this computer", "Nothing is sent without approval."),
]

_RELATIONS = [
    ("coop", "riverbend", "based_in"), ("coop", "cs", "uses"), ("coop", "energycoop", "builds_on"),
    ("tenants", "coop", "member_of"), ("library", "coop", "member_of"), ("school", "coop", "participates_in"),
    ("credit", "coop", "participates_in"), ("coop", "grid", "uses"), ("city", "grant", "participates_in"),
    ("libroof", "riverbend", "located_in"), ("gymroof", "riverbend", "located_in"),
    ("millst", "riverbend", "located_in"), ("market", "riverbend", "located_in"),
    ("depot", "riverbend", "located_in"), ("library", "libroof", "owns"), ("school", "gymroof", "owns"),
    ("tenants", "millst", "based_in"), ("chair", "coop", "leads"), ("treasurer", "coop", "member_of"),
    ("volco", "coop", "member_of"), ("surveyor", "survey", "works_on"), ("liaison", "drive", "works_on"),
    ("treasurer", "s_model", "has_skill"), ("surveyor", "s_roof", "has_skill"), ("volco", "s_events", "has_skill"),
    ("liaison", "s_writing", "has_skill"), ("chair", "vote", "leads"), ("treasurer", "grant", "works_on"),
    ("volco", "training", "leads"), ("openeve", "market", "located_in"), ("openeve", "drive", "part_of"),
    ("vote", "boardpack", "uses"), ("surveyday", "survey", "part_of"), ("deadline", "grant", "deadline_for"),
    ("training", "volco", "created_by"), ("launchday", "launchpage", "scheduled_for"),
    ("launchday", "drive", "part_of"), ("launchpage", "coop", "part_of"), ("launchpage", "m_design", "uses"),
    ("launchpage", "drive", "part_of"), ("launchpage", "p_tone", "uses"), ("launchpage", "credits", "cites"),
    ("launchpage", "shares", "cites"), ("drive", "coop", "part_of"), ("drive", "f_target", "uses"),
    ("survey", "coop", "part_of"), ("survey", "suitability", "uses"), ("survey", "m_photos", "uses"),
    ("grant", "coop", "part_of"), ("grant", "f_grant", "cites"), ("grant", "s_grant", "uses"),
    ("battery", "libroof", "located_in"), ("battery", "storage", "uses"), ("battery", "peak", "uses"),
    ("digest", "coop", "part_of"), ("digest", "k_local", "uses"), ("digest", "p_delivery", "uses"),
    ("digest", "p_monday", "uses"), ("boardpack", "m_deck", "uses"), ("boardpack", "vote", "part_of"),
    ("shared", "cs", "part_of"), ("credits", "cs", "part_of"), ("subscription", "cs", "part_of"),
    ("shares", "cs", "part_of"), ("vnm", "netmeter", "extends"), ("credits", "vnm", "uses"),
    ("netmeter", "credits", "builds_on"), ("fit", "netmeter", "contradicts"), ("ppa", "cs", "part_of"),
    ("interconnect", "grid", "uses"), ("interconnect", "gridcap", "uses"), ("capacity", "payback", "builds_on"),
    ("payback", "shares", "part_of"), ("energycoop", "control", "uses"), ("control", "onevote", "builds_on"),
    ("renters", "cs", "part_of"), ("equity", "renters", "builds_on"), ("carveout", "equity", "part_of"),
    ("storage", "peak", "uses"), ("inverter", "cs", "part_of"), ("suitability", "libroof", "cites"),
    ("benefit", "energycoop", "part_of"), ("tariff", "cs", "contradicts"), ("localfirst", "evidence", "uses"),
    ("localfirst", "k_local", "uses"), ("evidence", "p_cite", "builds_on"), ("shared", "energycoop", "builds_on"),
    ("subscription", "shares", "contradicts"), ("credits", "f_monthly", "cites"),
    ("f_south", "libroof", "part_of"), ("f_roofs", "survey", "part_of"), ("f_target", "drive", "part_of"),
    ("f_instal", "shares", "part_of"), ("f_instal", "credit", "cites"), ("f_monthly", "m_faq", "cites"),
    ("f_noupfront", "subscription", "part_of"), ("f_noupfront", "m_cs", "cites"),
    ("f_renters", "renters", "part_of"), ("f_renters", "m_cs", "cites"), ("f_months", "interconnect", "part_of"),
    ("f_months", "m_faq", "cites"), ("f_tuesday", "chair", "part_of"), ("f_thursday", "openeve", "part_of"),
    ("f_vote", "onevote", "part_of"), ("f_vote", "m_coop", "cites"), ("f_surplus", "benefit", "part_of"),
    ("f_gym", "gymroof", "part_of"), ("f_gym", "surveyday", "scheduled_for"), ("f_depot", "depot", "part_of"),
    ("f_grant", "city", "cites"), ("f_loans", "credit", "part_of"), ("f_loans", "shares", "part_of"),
    ("f_rules", "m_d40", "extracted_from"), ("f_rules", "credits", "part_of"),
    ("f_faq", "m_d40", "extracted_from"), ("f_faq", "m_faq", "extends"), ("m_cs", "cs", "cites"),
    ("m_nm", "netmeter", "cites"), ("m_coop", "energycoop", "cites"), ("m_guide", "energycoop", "cites"),
    ("m_guide", "control", "cites"), ("m_faq", "grid", "created_by"), ("m_member", "drive", "part_of"),
    ("m_member", "renters", "cites"), ("m_photos", "surveyday", "part_of"), ("m_design", "launchpage", "part_of"),
    ("m_deck", "vote", "part_of"), ("m_flyer", "openeve", "part_of"), ("m_d38", "digest", "created_by"),
    ("m_d39", "digest", "created_by"), ("m_d40", "digest", "created_by"), ("m_d39", "m_d38", "extends"),
    ("m_d40", "m_d39", "extends"), ("m_d38", "f_months", "cites"), ("p_plain", "s_writing", "uses"),
    ("p_cite", "evidence", "uses"), ("p_metric", "s_model", "uses"), ("p_tone", "m_flyer", "uses"),
    ("p_plain", "launchpage", "uses"), ("s_model", "payback", "uses"), ("s_model", "credits", "uses"),
    ("s_roof", "suitability", "uses"), ("s_grant", "grant", "uses"), ("s_events", "openeve", "uses"),
    ("s_writing", "m_flyer", "uses"), ("k_local", "localfirst", "part_of"), ("k_hosted", "launchpage", "uses"),
    ("k_drafts", "p_delivery", "uses"), ("depot", "vote", "participates_in"), ("libroof", "vote", "participates_in"),
    ("gymroof", "survey", "part_of"), ("depot", "survey", "part_of"), ("libroof", "survey", "part_of"),
    ("millst", "renters", "uses"), ("tenants", "renters", "interested_in"), ("tenants", "carveout", "interested_in"),
    ("school", "battery", "interested_in"), ("library", "battery", "participates_in"),
    ("grid", "interconnect", "owns"), ("grid", "netmeter", "uses"), ("city", "equity", "interested_in"),
    ("coop", "benefit", "owns"), ("coop", "control", "uses"), ("coop", "shares", "uses"),
    ("coop", "subscription", "uses"), ("coop", "localfirst", "uses"), ("treasurer", "payback", "studies"),
    ("chair", "m_deck", "authored"), ("liaison", "m_member", "authored"), ("volco", "m_flyer", "authored"),
]


def _entity_id(key: str) -> str:
    # Fixed ids give the graph the same layout on every capture; this seed
    # spreads the co-op's labelled neighbours without overlapping labels.
    return hashlib.sha256(f"v28-{key}".encode()).hexdigest()[:12]


def _seed_knowledge(anchor: datetime) -> None:
    from row_bot import knowledge_graph as kg

    previous = kg._skip_reindex
    kg._skip_reindex = True
    try:
        for key, entity_type, subject, description in _ENTITIES:
            kg.save_entity(entity_type, subject, description, tags="riverbend,demo",
                           properties={"provenance": "landing demo fixture"}, source="landing-demo",
                           entity_id=_entity_id(key))
        for source, target, relation in _RELATIONS:
            # Fixed relation ids too: the graph reads its edges in id order, and
            # that order steers the layout.
            kg.add_relation(_entity_id(source), _entity_id(target), relation, confidence=0.95,
                            properties={"provenance": "landing demo fixture"}, source="landing-demo",
                            relation_id=_entity_id(f"{source}>{target}"))
    finally:
        kg._skip_reindex = previous
    moment = (anchor - timedelta(minutes=26)).isoformat()
    with sqlite3.connect(Path(os.environ["ROW_BOT_DATA_DIR"]) / "memory.db") as conn:
        conn.execute("UPDATE entities SET created_at = ?, updated_at = ?", (moment, moment))
        conn.execute("UPDATE relations SET created_at = ?, updated_at = ?", (moment, moment))


def _launch_page_html() -> str:
    """The fictional co-op's launch page, as the Design panel stores it."""
    ink, cream, sun, muted = "#1F2A1C", "#FFF8EC", "#F59E0B", "#5B6656"
    eyebrow = "margin:0 0 14px;font-size:20px;font-weight:700;letter-spacing:4px;color:#B45309"
    steps = "".join(
        "<div style=\"background:#FFFDF8;border-radius:28px;padding:44px 40px;"
        "box-shadow:0 18px 40px rgba(120,72,10,.10)\">"
        f"<p style=\"margin:0 0 18px;font-size:22px;font-weight:700;color:{color}\">{number}</p>"
        f"<h3 style=\"margin:0 0 14px;font-size:32px;line-height:1.15;color:{ink}\">{title}</h3>"
        f"<p style=\"margin:0;font-size:21px;line-height:1.55;color:#4B5547\">{detail}</p></div>"
        for number, title, detail, color in (
            ("01", "Join for a share", "Subscribe monthly or buy a share outright. Renters welcome.", "#D97706"),
            ("02", "We build together", "One array on the library roof, owned by its members.", "#16A34A"),
            ("03", "Credits on your bill", "Your share's sunshine appears on every monthly bill.", "#0E7490"),
        )
    )
    stats = "".join(
        f"<div><p style=\"margin:0;font-size:56px;font-weight:800;color:{ink}\">{value}</p>"
        f"<p style=\"margin:6px 0 0;font-size:20px;color:{muted}\">{label}</p></div>"
        for value, label in (("120", "founding households"), ("14", "suitable roofs"), ("1", "vote per member"))
    )
    benefits = "".join(
        f"<div style=\"padding:36px 34px;border-radius:26px;background:{bg}\">"
        f"<h3 style=\"margin:0 0 12px;font-size:30px;color:{ink}\">{title}</h3>"
        f"<p style=\"margin:0;font-size:20px;line-height:1.55;color:#4B5547\">{detail}</p></div>"
        for title, detail, bg in (
            ("Lower bills", "Credits from your share arrive on every bill, all year.", "#FFEFC7"),
            ("A local fund", "Surplus goes to projects the members choose together.", "#E3F1DE"),
            ("A real say", "Every member votes on what the co-op builds next.", "#DCEEF3"),
            ("No roof needed", "Renters and flat owners join on the same terms.", "#F7E3D6"),
        )
    )
    questions = "".join(
        f"<div style=\"padding:30px 0;border-top:2px solid #EADFC9\">"
        f"<h3 style=\"margin:0 0 10px;font-size:28px;color:{ink}\">{q}</h3>"
        f"<p style=\"margin:0;font-size:20px;line-height:1.55;color:#4B5547\">{a}</p></div>"
        for q, a in (
            ("Do I need my own roof?", "No. Your share is part of the array on the library roof."),
            ("Can I pay over time?", "Yes. Shares can be paid in instalments with the credit union."),
            ("What happens to the surplus?", "It goes to the community fund, and members decide how it is spent."),
        )
    )
    return (
        f"<main style=\"width:1440px;min-height:3200px;font-family:Inter,'Segoe UI',sans-serif;"
        f"background:{cream};color:{ink}\">"
        "<section style=\"position:relative;overflow:hidden;padding:56px 96px 120px;"
        "background:radial-gradient(circle at 78% 30%,#FDE68A 0%,#FBBF24 18%,rgba(251,191,36,0) 42%),"
        "linear-gradient(160deg,#FFF3D6 0%,#FFE1A8 55%,#F9C97B 100%)\">"
        "<nav style=\"display:flex;align-items:center;justify-content:space-between;margin-bottom:120px\">"
        "<p style=\"margin:0;font-size:24px;font-weight:800;letter-spacing:.5px\">&#9728; Riverbend Solar</p>"
        "<p style=\"margin:0;font-size:19px;color:#5B4A2A\">How it works &nbsp;&middot;&nbsp; Savings "
        "&nbsp;&middot;&nbsp; Open evening</p></nav>"
        f"<p style=\"{eyebrow};margin-bottom:22px\">COMMUNITY-OWNED ENERGY</p>"
        "<h1 style=\"margin:0;max-width:880px;font-size:104px;line-height:.98;font-weight:800;letter-spacing:-2px\">"
        "Power your street, together.</h1>"
        "<p style=\"margin:34px 0 46px;max-width:700px;font-size:27px;line-height:1.5;color:#4A3B1F\">"
        "Riverbend Community Solar puts one shared array on the library roof. Every member owns a piece, "
        "and every share earns credits on the bill.</p>"
        f"<div style=\"display:flex;gap:18px\"><span style=\"padding:22px 38px;border-radius:999px;background:{ink};"
        f"color:{cream};font-size:22px;font-weight:700\">Become a founding member</span>"
        "<span style=\"padding:22px 34px;border-radius:999px;border:2px solid #7C5A1E;font-size:22px;"
        "font-weight:600;color:#5B4A2A\">Come to the open evening</span></div>"
        "<div style=\"position:absolute;right:120px;top:190px;width:300px;height:300px;border-radius:50%;"
        f"background:radial-gradient(circle,#FFFBEB 0%,{sun} 70%);box-shadow:0 0 120px 40px rgba(245,158,11,.45)\">"
        "</div></section>"
        "<section style=\"padding:110px 96px 90px\">"
        f"<p style=\"{eyebrow}\">HOW SHARES WORK</p>"
        "<h2 style=\"margin:0 0 56px;font-size:64px;line-height:1.05;font-weight:800\">"
        "Three steps to clean, local power</h2>"
        f"<div style=\"display:grid;grid-template-columns:repeat(3,1fr);gap:32px\">{steps}</div></section>"
        f"<section style=\"margin:0 96px 110px;padding:70px 80px;border-radius:36px;background:{ink};color:{cream};"
        "display:grid;grid-template-columns:1.2fr 1fr;gap:60px;align-items:center\">"
        "<div><h2 style=\"margin:0 0 18px;font-size:54px;line-height:1.08;font-weight:800\">Owned by neighbours, "
        "run by members</h2><p style=\"margin:0;font-size:22px;line-height:1.55;color:#D9E3D2\">One member, one "
        "vote. Surplus goes to the community fund for local projects.</p></div>"
        f"<div style=\"display:grid;grid-template-columns:repeat(3,1fr);gap:24px;background:{cream};color:{ink};"
        f"padding:40px 36px;border-radius:28px\">{stats}</div></section>"
        "<section style=\"padding:0 96px 110px\">"
        f"<p style=\"{eyebrow}\">WHAT MEMBERS GET BACK</p>"
        "<h2 style=\"margin:0 0 48px;font-size:60px;line-height:1.05;font-weight:800\">More than cheaper power</h2>"
        f"<div style=\"display:grid;grid-template-columns:repeat(2,1fr);gap:28px\">{benefits}</div></section>"
        "<section style=\"padding:0 96px 100px;display:grid;grid-template-columns:1fr 1.4fr;gap:80px\">"
        f"<div><p style=\"{eyebrow}\">QUESTIONS</p><h2 style=\"margin:0;font-size:56px;line-height:1.05;"
        "font-weight:800\">Asked at every street meeting</h2></div>"
        f"<div>{questions}</div></section>"
        f"<section style=\"margin:0 96px;padding:64px 72px;border-radius:36px;background:{sun};"
        "display:flex;align-items:center;justify-content:space-between\">"
        "<div><h2 style=\"margin:0 0 12px;font-size:50px;font-weight:800\">Open evening, Thursday 18:30</h2>"
        "<p style=\"margin:0;font-size:22px;color:#4A3B1F\">Market Hall, Riverbend. Bring your questions and your "
        f"neighbours.</p></div><span style=\"padding:22px 38px;border-radius:999px;background:{ink};color:{cream};"
        "font-size:22px;font-weight:800\">Save my place</span></section>"
        f"<footer style=\"padding:80px 96px 70px;display:flex;justify-content:space-between;color:{muted};"
        "font-size:19px\"><p style=\"margin:0\">&#9728; Riverbend Community Solar &middot; a member-owned co-op</p>"
        "<p style=\"margin:0\">Library roof array &middot; Market Hall open evenings</p></footer>"
        "</main>"
    )


def _seed_ship_goal(anchor: datetime) -> None:
    """The Ship conversation works toward a goal, shown in its details beside the approval."""
    from row_bot.goals import start_goal

    goal = start_goal(SHIP_THREAD, "Share the reviewed launch page with the co-op board", max_turns=6)
    progress = ["Checked the page against the research", "Exported riverbend-launch-page.pdf"]
    started = (anchor - timedelta(minutes=4)).isoformat()
    with _tasks_db() as conn:
        conn.execute(
            "UPDATE thread_goals SET turns_used = ?, last_progress = ?, evidence_json = ?, blockers_json = ?, "
            "last_reason = ?, created_at = ?, updated_at = ?, window_started_at = ? WHERE id = ?",
            (2, progress[-1], json.dumps(progress), json.dumps(["Waiting for approval to send the email"]),
             "The email waits for your approval.", started, started, started, goal["id"]),
        )


def _seed_design() -> None:
    from row_bot.conversation_resources import bind, list_bindings
    from row_bot.designer.state import BrandConfig, DesignerPage, DesignerProject, ProjectBrief
    from row_bot.designer.storage import save_project

    save_project(DesignerProject(
        id=DESIGN_ID,
        name="Riverbend launch page",
        mode="landing",
        aspect_ratio="landing",
        pages=[DesignerPage(title="Home", route_id="home", kind="screen", html=_launch_page_html(),
                            notes="Hero, how shares work, ownership, member benefits and the open evening.")],
        brand=BrandConfig(primary_color="#1F2A1C", accent_color="#F59E0B"),
        brief=ProjectBrief(output_type="Landing page", audience="Riverbend neighbours",
                           tone="Warm and neighbourly", length="One page"),
        thread_id=CREATE_THREAD,
    ))
    snapshot = list_bindings(CREATE_THREAD)
    bind(CREATE_THREAD, "artifact", DESIGN_ID, expected_revision=snapshot.revision, role="primary")


def _bind_profile(data_dir: Path, ollama_host: str = "") -> None:
    os.environ["ROW_BOT_DATA_DIR"] = str(data_dir)
    if ollama_host:
        os.environ["OLLAMA_HOST"] = ollama_host
    os.environ.setdefault("ROW_BOT_DOCS_CAPTURE", "1")
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def seed_profile(data_dir: Path, ollama_host: str, anchor: datetime) -> None:
    """Seed the fictional co-op into the capture profile (in this process)."""
    _bind_profile(data_dir, ollama_host)
    from scripts.docs import seed_real_app_demo_data as demo
    from row_bot.docs_capture import scan_demo_data_safety

    demo._seed_app_config(data_dir, first_run=False)
    demo._write_json(data_dir / "model_settings.json", {"model": LOCAL_MODEL})
    _seed_conversations(anchor)
    _seed_workflows(anchor)
    _seed_knowledge(anchor)
    _seed_design()
    _seed_ship_goal(anchor)
    demo._seed_model_catalog()
    errors = scan_demo_data_safety(data_dir)
    if errors:
        raise SystemExit("\n".join(errors))


def add_ship_approval(data_dir: Path, anchor: datetime) -> None:
    """The email waits for Approve or Deny in its conversation."""
    _bind_profile(data_dir)
    from row_bot.tasks import create_approval_request

    interrupt = {
        "tool": "send_gmail_message",
        "label": "Send email",
        "description": "Email riverbend-launch-page.pdf to riverbend-board@example.com",
        "args": {
            "to": ["riverbend-board@example.com"],
            "subject": "Launch page for review",
            "attachments": ["riverbend-launch-page.pdf"],
        },
        "tool_call_id": "s2",
        "risk_class": "medium",
        "scope": "One email to riverbend-board@example.com, with the PDF attached.",
    }
    _token, approval_id = create_approval_request(
        "landing-ship-pass", "", "conversation", interrupt["description"],
        resume_kind="conversation", source_thread_id=SHIP_THREAD, parent_thread_id=SHIP_THREAD,
        approval_payload_json={
            "interrupt": interrupt,
            "interrupt_ids": [],
            "pass_id": "landing-ship-pass",
            "model_selection": {"provider_id": "ollama", "model_ref": LOCAL_MODEL},
        },
    )
    with _tasks_db() as conn:
        conn.execute("UPDATE approval_requests SET id = ?, requested_at = ? WHERE id = ?",
                     (SHIP_APPROVAL_ID, (anchor - timedelta(minutes=1)).isoformat(), approval_id))


# The app and the browser --------------------------------------------------------

class App:
    """The real app on the isolated profile; stopped when the stack closes."""

    def __init__(self, stack: ExitStack, data_dir: Path, ollama_host: str) -> None:
        self.port = _free_port()
        self.proc, secret = _launch_app(self.port, data_dir, stack, ollama_host=ollama_host)
        stack.callback(self.stop)
        _wait_ping(self.port, self.proc, 120, launcher_secret=secret)
        self.base = f"http://127.0.0.1:{self.port}/app-v2"

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=15)


def _helper(*args: str) -> None:
    subprocess.run([sys.executable, str(Path(__file__).resolve()), *args], cwd=str(ROOT), check=True)


_HOSTED_CHOICE = {
    "provider_id": "codex",
    "model_ref": HOSTED_MODEL,
    "label": "GPT-5.6 Sol - ChatGPT / Codex",
    "available": True,
    "unavailable_reason": None,
    "billing": "subscription",
}


def _hosted_model_is_display_only(page) -> None:
    """Show GPT-5.6 Sol as a connected choice; nothing is connected or called."""

    def ready(workspace: dict) -> None:
        selection = (workspace.get("controls") or {}).get("model_selection") or {}
        if selection.get("provider_id") == "codex":
            workspace["model_status"] = {"state": "ready", "reason": "", "fix": None,
                                         "local": False, "sees_images": True}
            for action in workspace.get("actions") or []:
                action.update(ready=True, code=None)

    def handshake(route) -> None:
        response = route.fetch()
        body = response.json()
        body["models"] = [*body.get("models", []), _HOSTED_CHOICE]
        route.fulfill(response=response, json=body)

    def opened(route) -> None:
        response = route.fetch()
        body = response.json()
        ready(body.get("workspace") or {})
        route.fulfill(response=response, json=body)

    def workspace(route) -> None:
        response = route.fetch()
        body = response.json()
        ready(body)
        route.fulfill(response=response, json=body)

    page.route("**/api/v1/handshake", handshake)
    page.route("**/api/v1/conversations/*/open", opened)
    page.route("**/api/v1/conversations/*/workspace", workspace)


def _approval_is_answered_here(page) -> None:
    """Answer Approve in the harness: the app shows its submitted state and nothing is sent."""

    def answer(route) -> None:
        command = json.loads(route.request.post_data or "{}")
        if command.get("type") != "approval.resolve":
            route.abort()
            return
        route.fulfill(status=200, json={"command_id": command.get("command_id", ""), "status": "accepted",
                                        "approval_id": SHIP_APPROVAL_ID})

    page.route("**/api/v1/approvals/*/commands", answer)


_POINTER = """
(() => {
  if (window.top !== window) return;
  const install = () => {
    if (document.getElementById('landing-pointer')) return;
    const pointer = document.createElement('div');
    pointer.id = 'landing-pointer';
    pointer.innerHTML = '<svg width="22" height="28" viewBox="0 0 22 28"><path d="M2 2 L2 22 L7.2 17.4 L10.6 25.4 '
      + 'L14.2 23.9 L10.8 16.1 L17.6 16.1 Z" fill="#fff" stroke="#11151a" stroke-width="1.6" '
      + 'stroke-linejoin="round"/></svg><span></span>';
    pointer.style.cssText = 'position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;opacity:0;'
      + 'transition:opacity .25s ease;filter:drop-shadow(0 2px 3px rgba(0,0,0,.45));will-change:transform';
    const ring = pointer.querySelector('span');
    ring.style.cssText = 'position:absolute;left:-12px;top:-12px;width:28px;height:28px;border-radius:50%;'
      + 'border:2px solid rgba(255,255,255,.85);opacity:0;transform:scale(.4)';
    document.documentElement.appendChild(pointer);
    window.__pointer = (x, y, show) => {
      pointer.style.transform = `translate(${x - 2}px, ${y - 2}px)`;
      if (show != null) pointer.style.opacity = show ? '1' : '0';
    };
    window.__press = () => ring.animate(
      [{opacity: .9, transform: 'scale(.4)'}, {opacity: 0, transform: 'scale(1.25)'}],
      {duration: 420, easing: 'ease-out'});
  };
  if (document.documentElement) install();
  document.addEventListener('DOMContentLoaded', install);
})();
"""


def _ease(t: float) -> float:
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


class Director:
    """Human-paced pointer moves, clicks and scrolls, with the drawn pointer."""

    def __init__(self, page, start: tuple[float, float] = (WIDTH * 0.72, HEIGHT * 0.62)) -> None:
        self.page = page
        self.x, self.y = start

    def place(self, x: float, y: float, show: bool | None = None) -> None:
        self.x, self.y = x, y
        self.page.mouse.move(x, y)
        self.page.evaluate("([x, y, show]) => window.__pointer && window.__pointer(x, y, show)",
                           [x, y, show])

    def show(self) -> None:
        self.place(self.x, self.y, True)

    def hide(self) -> None:
        self.place(self.x, self.y, False)

    def move(self, x: float, y: float, ms: int = 800) -> None:
        start = time.monotonic()
        x0, y0 = self.x, self.y
        while True:
            t = min(1.0, (time.monotonic() - start) * 1000 / ms)
            k = _ease(t)
            self.place(x0 + (x - x0) * k, y0 + (y - y0) * k)
            if t >= 1:
                return
            self.page.wait_for_timeout(12)

    def to(self, locator, ms: int = 800, dx: float = 0, dy: float = 0) -> None:
        box = locator.bounding_box()
        if not box:
            raise RuntimeError(f"not on screen: {locator}")
        self.move(box["x"] + box["width"] / 2 + dx, box["y"] + box["height"] / 2 + dy, ms)

    def click(self, locator=None, ms: int = 800, settle: int = 120) -> None:
        if locator is not None:
            self.to(locator, ms)
        self.page.wait_for_timeout(settle)
        self.page.evaluate("() => window.__press && window.__press()")
        self.page.mouse.down()
        self.page.wait_for_timeout(70)
        self.page.mouse.up()

    def scroll(self, dy: float, ms: int = 600) -> None:
        steps = max(1, ms // 16)
        for _ in range(steps):
            self.page.mouse.wheel(0, dy / steps)
            self.page.wait_for_timeout(16)

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)


class Recorder:
    """The page's screencast, saved frame by frame with its timestamps."""

    def __init__(self, context, page, folder: Path) -> None:
        self.folder = folder
        self.frames: list[tuple[float, Path]] = []
        self.marks: dict[str, float] = {}
        self.cdp = context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._frame)
        self.recording = False

    def _frame(self, params: dict) -> None:
        if self.recording:
            path = self.folder / f"{len(self.frames):05d}.png"
            path.write_bytes(base64.b64decode(params["data"]))
            self.frames.append((float(params["metadata"]["timestamp"]), path))
        try:
            self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:  # noqa: BLE001 - the page may already be closing
            pass

    def start(self, page) -> None:
        self.recording = True
        self.cdp.send("Page.startScreencast", {"format": "png", "everyNthFrame": 1})
        deadline = time.monotonic() + 5
        while not self.frames and time.monotonic() < deadline:
            page.wait_for_timeout(20)
        # Repaint once so the first frame is current.
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")

    def mark(self, name: str) -> None:
        self.marks[name] = time.time()

    def stop(self, page) -> None:
        page.wait_for_timeout(200)
        self.cdp.send("Page.stopScreencast")
        self.recording = False

    def sample(self, start: float, end: float) -> list[Path]:
        """Frames at 30 fps between two marks, each the newest shown at its moment."""
        frames = sorted(self.frames)
        picked: list[Path] = []
        index = 0
        count = round((end - start) * FPS)
        for n in range(count):
            moment = start + n / FPS
            while index + 1 < len(frames) and frames[index + 1][0] <= moment:
                index += 1
            picked.append(frames[index][1])
        return picked


def _new_context(browser, *, scale: int, clock: Callable[[], datetime], pointer: bool):
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=scale,
        color_scheme="dark",
        reduced_motion="no-preference",
        locale="en-GB",
        timezone_id="Europe/London",
    )
    # The approval counts as announced (no floating notice over the scene), and
    # the sidebar's agents row is folded so the conversation list ends tidily.
    context.add_init_script(
        "try { localStorage.setItem('row-bot.approvals-announced.v1', '[\"%s\"]');"
        " localStorage.setItem('row-bot.sidebar-agents.v1', 'collapsed') } catch (e) {}"
        % SHIP_APPROVAL_ID
    )
    if pointer:
        context.add_init_script(_POINTER)
    page = context.new_page()
    page.clock.set_system_time(clock())
    _hosted_model_is_display_only(page)
    _approval_is_answered_here(page)
    return context, page


def _open(page, base: str, route: str, text: str) -> None:
    page.goto(base + route, wait_until="load", timeout=60_000)
    page.get_by_text(text, exact=False).first.wait_for(timeout=30_000)
    page.wait_for_timeout(800)


def _close_details(page) -> None:
    """Close the conversation's details rail (its goal card) if it is open."""
    toggle = page.locator('button[aria-label="Conversation details"]').first
    if toggle.get_attribute("aria-pressed") == "true":
        toggle.click()
        page.wait_for_timeout(400)
    _blur(page)


def _blur(page) -> None:
    page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")


def _park(page) -> None:
    page.mouse.move(WIDTH - 2, HEIGHT - 2)


# Scenes ---------------------------------------------------------------------------

def _graph_hub(page, scale: int) -> tuple[float, float]:
    """Where the co-op's memory sits: the largest organisation-coloured node."""
    from PIL import Image

    image = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    width, height = image.size
    pixels = image.load()
    target = (213, 81, 129)
    left = int(310 * scale)
    mask = {
        (x, y)
        for y in range(int(60 * scale), height, 1)
        for x in range(left, int(1250 * scale))
        if sum(abs(a - b) for a, b in zip(pixels[x, y], target)) < 24
    }
    best: list[tuple[int, int]] = []
    while mask:
        seed = mask.pop()
        blob, queue = [seed], deque([seed])
        while queue:
            x, y = queue.popleft()
            for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbour in mask:
                    mask.remove(neighbour)
                    blob.append(neighbour)
                    queue.append(neighbour)
        if len(blob) > len(best):
            best = blob
    if not best:
        raise RuntimeError("the co-op's memory is not on the graph")
    return (sum(x for x, _ in best) / len(best) / scale, sum(y for _, y in best) / len(best) / scale)


def _setup_research(page, base: str) -> None:
    _open(page, base, f"/conversations/{RESEARCH_THREAD}", "Used 8 tools")
    _close_details(page)


def _settled_hub(page, base: str, scale: int) -> tuple[float, float]:
    page.goto(base + "/?tab=knowledge", wait_until="load", timeout=60_000)
    page.locator('[aria-label^="Interactive knowledge graph"]').wait_for(timeout=30_000)
    page.wait_for_timeout(7000)
    return _graph_hub(page, scale)


def _setup_create(page, base: str) -> None:
    _open(page, base, f"/conversations/{CREATE_THREAD}", "Launch page direction")
    preview = page.locator('iframe[title="Page preview: Home"]')
    page.wait_for_timeout(1000)
    if not preview.is_visible():
        # The conversation's details list its design; opening it docks the panel.
        details = page.locator('button[aria-label="Conversation details"]').first
        if details.get_attribute("aria-pressed") != "true":
            details.click()
        page.locator('button[title="Open Riverbend launch page"]').first.click()
    preview.wait_for(timeout=30_000)
    page.locator('button[aria-label="Toggle navigation"]').first.click()
    page.wait_for_timeout(1500)
    # Widen the panel so the page reads at a glance beside the conversation.
    handle = page.locator('[role="separator"][aria-label="Resize side panel"]').first
    box = handle.bounding_box()
    page.mouse.move(box["x"] + 1, box["y"] + 400)
    page.mouse.down()
    page.mouse.move(box["x"] - 150, box["y"] + 400, steps=8)
    page.mouse.move(620, box["y"] + 400, steps=8)
    page.mouse.up()
    page.wait_for_timeout(1500)
    _blur(page)


def _setup_automate(page, base: str) -> None:
    _open(page, base, "/?tab=workflows", "Weekly policy digest")
    page.wait_for_timeout(600)


def _workflow_menu(page, d: Director | None, item: str) -> None:
    row = page.locator('[data-home-tab="workflows"]').get_by_text("Weekly policy digest").first
    more = page.locator('button[aria-label="More actions for Weekly policy digest"]')
    if d is None:
        row.hover()
        more.click()
        page.get_by_role("menuitem", name=item).click()
        return
    d.to(row, ms=600, dx=420)
    d.click(more, ms=450)
    d.wait(300)
    d.click(page.get_by_role("menuitem", name=item), ms=450)


def _run_history(page, base: str) -> None:
    """The workflow's run drawer, as the Automate still shows it."""
    _setup_automate(page, base)
    _workflow_menu(page, None, "Run history")
    page.get_by_text("History").first.wait_for(timeout=15_000)
    page.wait_for_timeout(900)
    _blur(page)


def _setup_ship(page, base: str) -> None:
    # The details beside it show the goal; they also keep the approval clear of
    # Buddy, who stands over the right of the landing page's Ship scene.
    _open(page, base, f"/conversations/{SHIP_THREAD}", "Send an email?")
    details = page.locator('button[aria-label="Conversation details"]').first
    if details.get_attribute("aria-pressed") != "true":
        details.click()
    page.get_by_text("Share the reviewed launch page").first.wait_for(timeout=15_000)
    page.wait_for_timeout(600)
    _blur(page)


SETUP = {"research": _setup_research, "create": _setup_create, "automate": _setup_automate, "ship": _setup_ship}


def _still(browser, base: str, scene: str, clock: Callable[[], datetime], out: Path) -> None:
    from PIL import Image

    context, page = _new_context(browser, scale=2, clock=clock, pointer=False)
    try:
        if scene == "research":
            x, y = _settled_hub(page, base, 2)
            page.mouse.move(x - 40, y + 30)
            page.mouse.move(x, y, steps=6)
            page.wait_for_timeout(1200)
        elif scene == "automate":
            _run_history(page, base)
            _park(page)
        else:
            SETUP[scene](page, base)
            _park(page)
            page.wait_for_timeout(1500)
        raw = page.screenshot(animations="disabled")
    finally:
        context.close()
    image = Image.open(io.BytesIO(raw)).convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    image.save(out, "WEBP", quality=86, method=6)


def _record_research(page, base: str, d: Director, rec: Recorder) -> list[tuple[str, str]]:
    # Find the settled co-op memory first; reloading forgets the layout, so
    # the recorded graph settles again from the start.
    x, y = _settled_hub(page, base, 1)
    _setup_research(page, base)
    _park(page)
    page.wait_for_timeout(1000)
    trace = page.get_by_text("Used 8 tools").first
    d.place(980, 560)
    rec.start(page)
    rec.mark("a0")
    d.wait(400)
    d.show()
    d.click(trace, ms=900)
    d.wait(400)
    d.move(d.x + 140, d.y + 60, 800)
    d.wait(700)
    rec.mark("a1")
    page.locator('a[aria-label="Home"]').first.click()
    page.get_by_role("tab", name="Knowledge").click()
    rec.mark("b0")
    d.place(1190, 700)
    d.wait(3500)
    d.move(x + 60, y + 70, 700)
    d.move(x, y, 450)
    d.wait(1250)
    rec.mark("b1")
    rec.stop(page)
    return [("a0", "a1"), ("b0", "b1")]


def _record_create(page, base: str, d: Director, rec: Recorder) -> list[tuple[str, str]]:
    pill = page.locator('button[aria-label="Model"]').first
    d.place(470, 560)
    rec.start(page)
    rec.mark("s")
    d.wait(700)
    d.show()
    d.click(pill, ms=900)
    d.wait(500)
    d.to(page.locator(".model-picker").get_by_text("GPT-5.6 Sol").first, ms=600)
    d.wait(1000)
    page.keyboard.press("Escape")
    d.wait(400)
    d.click(page.get_by_role("radio", name="Edit"), ms=900)
    d.wait(900)
    # The preview is the page scaled into the panel: map the headline into it.
    box = page.locator('iframe[title="Page preview: Home"]').bounding_box()
    left, top, width, height, page_width = page.frame_locator('iframe[title="Page preview: Home"]').locator(
        "h1").first.evaluate("e => { const r = e.getBoundingClientRect(); "
                             "return [r.x, r.y, r.width, r.height, document.documentElement.clientWidth]; }")
    scale = box["width"] / page_width
    d.move(box["x"] + (left + width * 0.42) * scale, box["y"] + (top + height * 0.28) * scale, 800)
    d.wait(300)
    d.click()
    d.wait(1500)
    rec.mark("e")
    rec.stop(page)
    return [("s", "e")]


def _record_automate(page, base: str, d: Director, rec: Recorder) -> list[tuple[str, str]]:
    # Everything that matters stays above and left of Buddy, who stands over
    # the lower right of the landing page's Automate scene. Three takes: the
    # delivery defaults, the workflow's local model saved, and its run history.
    d.place(760, 470)
    rec.start(page)
    rec.mark("a0")
    d.wait(400)
    d.show()
    d.click(page.locator('button[aria-label="Delivery defaults"]'), ms=700)
    page.get_by_text("No external channels are set up.").first.wait_for(timeout=15_000)
    d.move(1085, 232, 500)
    d.wait(1250)
    page.keyboard.press("Escape")
    d.wait(200)
    rec.mark("a1")
    _workflow_menu(page, d, "Workflow settings")
    page.get_by_text("Readiness is checked when it runs").first.wait_for(timeout=15_000)
    rec.mark("b0")
    d.wait(150)
    d.to(page.get_by_text("qwen3:8b - Ollama Local").first, ms=650)
    d.wait(1000)
    d.click(page.get_by_role("button", name="Save settings"), ms=600)
    page.get_by_text("4 workflows").first.wait_for(timeout=15_000)
    d.wait(250)
    rec.mark("b1")
    _workflow_menu(page, d, "Run history")
    page.get_by_text("History").first.wait_for(timeout=15_000)
    _blur(page)  # The drawer focuses its first button, whose tooltip would cover the history.
    rec.mark("c0")
    d.wait(200)
    d.move(1075, 400, 650)
    d.wait(1650)
    rec.mark("c1")
    rec.stop(page)
    return [("a0", "a1"), ("b0", "b1"), ("c0", "c1")]


def _record_ship(page, base: str, d: Director, rec: Recorder) -> list[tuple[str, str]]:
    approve = page.locator('aside.approval-card button[aria-label="Approve"]').first
    # The landing page turns Buddy to "success" 2.25 s into this clip
    # (SHIP_APPROVAL_AT in docs/landing-story.js), so Approve lands there.
    d.place(830, 690)
    rec.start(page)
    rec.mark("s")
    d.wait(1100)
    d.show()
    d.to(approve, ms=950)
    d.wait(120)
    rec.mark("click")
    d.click(settle=0)
    d.wait(560)
    rec.mark("e")
    rec.stop(page)
    return [("s", "e")]


RECORD = {"research": _record_research, "create": _record_create, "automate": _record_automate,
          "ship": _record_ship}


def _clip_frames(rec: Recorder, cuts: list[tuple[str, str]], folder: Path) -> tuple[Path, int]:
    """The edited clip as 30 fps PNG frames: segments joined by short dissolves."""
    from PIL import Image

    segments = [rec.sample(rec.marks[a], rec.marks[b]) for a, b in cuts]
    frames: list[Any] = list(segments[0])
    for segment in segments[1:]:
        tail, head = frames[-DISSOLVE_FRAMES:], segment[:DISSOLVE_FRAMES]
        blends = [
            Image.blend(Image.open(a).convert("RGB"), Image.open(b).convert("RGB"), (i + 1) / (DISSOLVE_FRAMES + 1))
            for i, (a, b) in enumerate(zip(tail, head))
        ]
        frames = frames[:-DISSOLVE_FRAMES] + blends + list(segment[DISSOLVE_FRAMES:])
    for index, frame in enumerate(frames):
        target = folder / f"{index:05d}.png"
        if isinstance(frame, Path):
            image = Image.open(frame).convert("RGB")
        else:
            image = frame
        if image.size != (WIDTH, HEIGHT):
            image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        image.save(target)
    return folder, len(frames)


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _encode(frames: Path, name: str, out_dir: Path) -> None:
    ffmpeg = _ffmpeg()
    source = ["-framerate", str(FPS), "-i", str(frames / "%05d.png")]
    webm = out_dir / f"{name}.webm"
    mp4 = out_dir / f"{name}.mp4"
    log = SCRATCH / f"{name}-vp9"
    common = [ffmpeg, "-v", "error", "-y", *source, "-an", "-pix_fmt", "yuv420p"]
    vp9 = ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "36", "-row-mt", "1", "-tile-columns", "2",
           "-deadline", "good", "-cpu-used", "1", "-g", "150", "-passlogfile", str(log)]
    subprocess.run([*common, *vp9, "-pass", "1", "-f", "webm", os.devnull], check=True)
    subprocess.run([*common, *vp9, "-pass", "2", str(webm)], check=True)
    subprocess.run([*common, "-c:v", "libx264", "-preset", "veryslow", "-crf", "25", "-profile:v", "high",
                    "-tune", "stillimage", "-g", "150", "-movflags", "+faststart", str(mp4)], check=True)
    for leftover in SCRATCH.glob(f"{name}-vp9*.log"):
        leftover.unlink()


def capture(scenes: list[str]) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    stills_dir = MEDIA / "screenshots"
    clips_dir = MEDIA / "clips"
    anchor = _anchor()
    started = time.time()

    def clock() -> datetime:
        return anchor + timedelta(seconds=time.time() - started)

    timings: dict[str, Any] = {}
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        profile = stack.enter_context(tempfile.TemporaryDirectory(prefix="profile-", dir=SCRATCH,
                                                                  ignore_cleanup_errors=True))
        data_dir = Path(profile).resolve()
        ollama = _start_demo_ollama(stack)
        _helper("--seed", str(data_dir), "--ollama-host", ollama, "--anchor", anchor.isoformat())
        pw = stack.enter_context(sync_playwright())
        browser = pw.chromium.launch(channel="msedge", headless=True)
        stack.callback(browser.close)
        groups = [[s for s in scenes if s != "ship"], [s for s in scenes if s == "ship"]]
        for group in groups:
            if not group:
                continue
            with ExitStack() as run:
                if group == ["ship"]:
                    _helper("--add-approval", str(data_dir), "--anchor", anchor.isoformat())
                app = App(run, data_dir, ollama)
                for scene in group:
                    print(f"{scene}: still", flush=True)
                    _still(browser, app.base, scene, clock, stills_dir / f"{scene}.webp")
                    print(f"{scene}: clip", flush=True)
                    # The screencast and the edited frames are scratch; only the encodes are kept.
                    raw = Path(run.enter_context(tempfile.TemporaryDirectory(prefix=f"{scene}-raw-", dir=SCRATCH)))
                    edited = Path(run.enter_context(tempfile.TemporaryDirectory(prefix=f"{scene}-", dir=SCRATCH)))
                    context, page = _new_context(browser, scale=1, clock=clock, pointer=True)
                    try:
                        SETUP[scene](page, app.base)
                        _park(page)
                        page.wait_for_timeout(1200)
                        rec = Recorder(context, page, raw)
                        cuts = RECORD[scene](page, app.base, Director(page), rec)
                    finally:
                        context.close()
                    frames, count = _clip_frames(rec, cuts, edited)
                    _encode(frames, scene, clips_dir)
                    first = rec.marks[cuts[0][0]]
                    timings[scene] = {
                        "segments_seconds": [[round(rec.marks[a] - first, 3), round(rec.marks[b] - first, 3)]
                                             for a, b in cuts],
                        "frames": count,
                        "raw_frames": len(rec.frames),
                    }
                    if "click" in rec.marks:
                        timings[scene]["click_seconds"] = round(rec.marks["click"] - first, 3)
                    saved = SCRATCH / "timings.json"
                    merged = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else {}
                    saved.write_text(json.dumps({**merged, scene: timings[scene]}, indent=2), encoding="utf-8")
    return timings


STORY_ID = "react-workbench-v1"
PRODUCTION = {
    "app": "Row-Bot 5.0.0, the real app process and its React client at /app-v2/",
    "method": (
        "Scripted Playwright capture (scripts/docs/capture_landing_media.py) in Microsoft Edge at 1440x810 CSS px, "
        "dark appearance, on an isolated temporary profile seeded with fictional demo data (a neighbourhood solar "
        "co-op); no real user data, accounts, names or paths"
    ),
    "models": (
        "No model ran. The local runtime is the docs capture's display-only stand-in on loopback (it lists "
        "qwen3:8b and runs nothing); GPT-5.6 Sol via ChatGPT / Codex is display-only: the harness tells the "
        "client it is connected, and nothing is connected or called"
    ),
    "outward_actions": "None: the harness answers the Ship clip's Approve request itself, so no email is sent",
    "pointer": "Drawn by the harness to show where the scripted clicks land",
    "stills": "Rendered at 2x and downscaled with Lanczos to 1440x810 WebP",
    "clips": "Browser screencast frames resampled to 30 fps; VP9 WebM and H.264 MP4 (faststart), no audio",
}
CLIP_NOTES = {
    "research": "Two takes at normal speed joined by a four-frame dissolve: the local-model conversation's "
                "source trace, then the Knowledge graph settling and its co-op memory highlighted.",
    "create": "One continuous take at normal speed: the hosted model shown in the picker, then the landing page "
              "opened for editing in the Design panel.",
    "automate": "Three takes at normal speed joined by four-frame dissolves: the workflow delivery defaults (no "
                "external channels), the workflow's local model saved, then its retained run history.",
    "ship": "One continuous take at normal speed: the waiting email approval, then Approve.",
}
# The landing page's story assets: scene id -> (scene, has clip, has MP4 fallback, alt text).
STORY_ASSETS = {
    "hero-app": ("research", False, False,
                 "Row-Bot's Knowledge graph after local-model research, with the co-op's memory and its "
                 "connections highlighted."),
    "research-local": ("research", True, True,
                       "A local-model conversation with its source trace, followed by the Knowledge graph "
                       "settling into connected memories."),
    "synthesis-sol": ("create", True, True,
                      "A conversation handed to a frontier model, GPT-5.6 Sol, with an editable landing page "
                      "in its Design panel."),
    "knowledge-control": ("research", True, False,
                          "A Row-Bot knowledge graph of 103 connected memories, with one memory's connections "
                          "labelled."),
    "designer-output": ("create", True, False,
                        "An editable landing page open in the conversation's Design panel, its heading then "
                        "selected for editing."),
    "workflow-repeat": ("automate", True, True,
                        "Workflow delivery kept in the app with no outside channels, a workflow's local model "
                        "saved, then its retained run history."),
    "approval-boundary": ("ship", True, True,
                          "Row-Bot waiting for approval before emailing a PDF to riverbend-board@example.com, "
                          "then the approval being given."),
    "model-choice": ("create", False, False,
                     "The composer's model pill showing GPT-5.6 Sol as a cloud model, beside the conversation's "
                     "Design panel."),
}


def _file_record(path: Path) -> tuple[str, int]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size


def write_records() -> None:
    """Hash the published media into the manifest and the recording receipt."""
    import imageio_ffmpeg

    timings = json.loads((SCRATCH / "timings.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    clips: dict[str, Any] = {}
    for scene in SCENES:
        still = MEDIA / "screenshots" / f"{scene}.webp"
        webm, mp4 = MEDIA / "clips" / f"{scene}.webm", MEDIA / "clips" / f"{scene}.mp4"
        frames, _seconds = imageio_ffmpeg.count_frames_and_secs(str(webm))
        record: dict[str, Any] = {
            "source": "Screencast of the real Row-Bot 5.0.0 React client",
            "source_frames": timings[scene]["raw_frames"],
            "segments_seconds": timings[scene]["segments_seconds"],
            "duration_seconds": round(frames / FPS, 3),
            "dimensions": [WIDTH, HEIGHT],
            "fps": FPS,
        }
        for key, path in (("webm", webm), ("mp4", mp4), ("poster", still)):
            sha, size = _file_record(path)
            digest.update(sha.encode())
            record.update({key: f"media/landing-story/{path.parent.name}/{path.name}",
                           f"{key}_sha256": sha, f"{key}_bytes": size})
        record["editorial_note"] = CLIP_NOTES[scene]
        if "click_seconds" in timings[scene]:
            record["approval_moment_seconds"] = timings[scene]["click_seconds"]
        clips[scene] = record
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + digest.hexdigest()[:6]
    receipt = {
        "schema": 1,
        "story_id": STORY_ID,
        "run_id": run_id,
        "editing": ("normal-speed editorial cuts with four-frame dissolves; scripted takes of the real app, "
                    "no acceleration"),
        "production": PRODUCTION,
        "clips": clips,
    }
    (MEDIA / "recording-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")

    manifest_path = MEDIA / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets = []
    for asset in manifest["assets"]:
        if asset["scene_id"] not in STORY_ASSETS:
            assets.append(asset)
            continue
        scene, has_clip, has_fallback, alt = STORY_ASSETS[asset["scene_id"]]
        entry = {"scene_id": asset["scene_id"], "still": f"screenshots/{scene}.webp",
                 "sha256": clips[scene]["poster_sha256"]}
        if has_clip:
            entry.update(clip=f"clips/{scene}.webm", clip_sha256=clips[scene]["webm_sha256"])
        if has_fallback:
            entry.update(clip_fallback=f"clips/{scene}.mp4", clip_fallback_sha256=clips[scene]["mp4_sha256"])
        entry["alt"] = alt
        assets.append(entry)
    manifest.update(story_id=STORY_ID, run_id=run_id, review_status="pending_review", reviewed_at=None,
                    production=PRODUCTION, assets=assets)
    first = ("schema", "story_id", "run_id", "review_status", "reviewed_at", "recording_receipt", "production")
    ordered = {key: manifest[key] for key in first}
    ordered.update({key: value for key, value in manifest.items() if key not in ordered})
    manifest_path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def serve() -> None:
    anchor = _anchor()
    with ExitStack() as stack:
        data_dir = Path(stack.enter_context(tempfile.TemporaryDirectory(
            prefix="profile-", dir=SCRATCH, ignore_cleanup_errors=True))).resolve()
        ollama = _start_demo_ollama(stack)
        _helper("--seed", str(data_dir), "--ollama-host", ollama, "--anchor", anchor.isoformat())
        _helper("--add-approval", str(data_dir), "--anchor", anchor.isoformat())
        app = App(stack, data_dir, ollama)
        stop = SCRATCH / "serve.stop"
        stop.unlink(missing_ok=True)
        (SCRATCH / "serve.json").write_text(json.dumps({"base": app.base, "anchor": anchor.isoformat()}),
                                            encoding="utf-8")
        print(f"Row-Bot is running at {app.base}/; create {stop.relative_to(ROOT)} to stop it.", flush=True)
        while app.proc.poll() is None and not stop.exists():
            time.sleep(0.5)
        stop.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenes", nargs="*", default=list(SCENES), choices=SCENES)
    parser.add_argument("--serve", action="store_true", help="seed and start the app for a manual look")
    parser.add_argument("--records", action="store_true",
                        help="only rewrite the manifest and recording receipt from the published media")
    parser.add_argument("--seed", metavar="DATA_DIR", help=argparse.SUPPRESS)
    parser.add_argument("--add-approval", metavar="DATA_DIR", help=argparse.SUPPRESS)
    parser.add_argument("--ollama-host", default="", help=argparse.SUPPRESS)
    parser.add_argument("--anchor", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    anchor = datetime.fromisoformat(args.anchor) if args.anchor else _anchor()
    if args.seed:
        seed_profile(Path(args.seed), args.ollama_host, anchor)
    elif args.add_approval:
        add_ship_approval(Path(args.add_approval), anchor)
    elif args.serve:
        serve()
    elif args.records:
        write_records()
    else:
        print(json.dumps(capture(args.scenes), indent=2))
        write_records()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
