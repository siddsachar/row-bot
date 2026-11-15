"""The day's start and end: the morning brief and the end-of-day wrap, each an editable workflow.

- **Morning brief**: at the first break of the day (unlock, back from being away, the start of the day
  after quiet hours, or opening Row-Bot), Row-Bot gathers today's meetings, what waits in For you
  (follow-ups, deadlines, dates) and what Dream Cycle prepared overnight, and runs the "Morning brief"
  workflow on it (``{{for_you}}`` in its message). The run's reply becomes the brief moment, ending
  "That's everything for today.", and carries the moments it covered. Nothing for today: no brief.
- **End-of-day wrap**: at the end of the day (Settings › Proactive's day end), the "End-of-day wrap"
  workflow runs on what got done today, what moved to tomorrow and the drafts waiting (``{{day_wrap}}``).
  Off at Quiet and on at Balanced and Eager unless the person chose (``settings.wrap_on``).

Both are ordinary workflows in the person's Workflows (``tasks.MORNING_BRIEF`` and
``tasks.END_OF_DAY_WRAP``): a run is a chat turn in the workflow's own conversation, and its reply goes to
the workflow's channels too, so editing the workflow's message or its delivery (a phone, a chat app)
changes the brief. Their runs take the runtime surface "proactive" however they start, so they only read
(``workflow_turns.surface``). Proactive Row-Bot makes the workflow the first time it needs it (the brief is
also a starter workflow) and remembers which one it is; a workflow the person deleted isn't made again until
they turn that switch off and on.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from row_bot.proactive import store, timing
from row_bot.proactive.model import Candidate, Evidence

logger = logging.getLogger(__name__)

CLOSING = "That's everything for today."
BODY_CHARS = 4000
EXPIRES_SECONDS = {"brief": 6 * 3600, "wrap": 4 * 3600}
# A run that ends within this long of the break it started at goes out in that break.
SAME_BREAK_SECONDS = 30 * 60
OVERNIGHT_FROM_SECONDS = 6 * 3600  # "Overnight": since 18:00 the evening before.
TITLES = {"brief": "Your morning brief", "wrap": "Your day, wrapped"}
_WORDS = {"brief": "Morning brief", "wrap": "End-of-day wrap"}
_VARIABLES = {"for_you": "brief", "day_wrap": "wrap"}
_DONE = ("completed", "completed_delivery_failed")
_LISTED = 12
# What was gathered for the run in progress, until it ends (in memory: after a restart there is no such run,
# and a run the person starts gathers afresh).
_GATHERED: dict[str, str] = {}


# --- Which workflow is which -------------------------------------------------------------------------------

def which(task_id: str) -> str | None:
    """"brief" or "wrap" when this workflow is Proactive Row-Bot's morning brief or end-of-day wrap."""
    if not task_id or not store.exists():
        return None
    ids = store.get_state("workflows") or {}
    return next((name for name, saved in ids.items() if saved == task_id), None)


def forget_deleted(name: str) -> None:
    """The person asked for the brief (or wrap) again: a workflow of it they deleted may be made anew."""
    from row_bot import tasks

    ids = store.get_state("workflows") or {}
    if ids.get(name) and tasks.get_task(ids[name]) is None:
        ids.pop(name)
        store.set_state("workflows", ids)


def _template(name: str) -> dict[str, Any]:
    from row_bot import tasks

    return tasks.MORNING_BRIEF if name == "brief" else tasks.END_OF_DAY_WRAP


def workflow(name: str, *, now: float) -> dict[str, Any] | None:
    """The workflow of the brief (or wrap): the one remembered, else the starter of that name, else a new one.
    None when the person deleted it."""
    from row_bot import tasks

    ids = store.get_state("workflows") or {}
    if ids.get(name):
        return tasks.get_task(ids[name])
    template = _template(name)
    existing = next((task for task in tasks.list_tasks() if task["name"] == template["name"]), None)
    task_id = existing["id"] if existing else tasks.create_from_template(template)
    store.set_state("workflows", {**ids, name: task_id})
    if existing is None:
        store.log("pass", f"Made the “{template['name']}” workflow. Edit it in Workflows to change what it says, "
                  "or to send it to your phone or a chat app too.", now=now, detail={"task_id": task_id})
    return tasks.get_task(task_id)


# --- What it gathers ---------------------------------------------------------------------------------------

def _clock(value: float) -> str:
    return datetime.fromtimestamp(value).strftime("%H:%M")


def _meetings(start: float, end: float) -> list[str]:
    from row_bot.proactive import settings as proactive_settings
    from row_bot.proactive import sources as proactive_sources

    if not proactive_settings.load()["sources"]["calendar"]:
        return []
    events = []
    for source in proactive_sources.calendar_sources():
        try:
            if source.available():
                events.extend(event for event in source.events(start, end) if not event.cancelled)
        except Exception:
            logger.info("A calendar couldn't be read for the day's brief", exc_info=True)
    lines = []
    for event in sorted(events, key=lambda item: item.start)[:_LISTED]:
        people = len(event.attendees)
        with_whom = f" (with {people} other{'s' if people != 1 else ''})" if people else ""
        lines.append(f"- {_clock(event.start)} {event.title or 'A meeting'}{with_whom}")
    return lines


def _section(title: str, lines: list[str]) -> list[str]:
    return [f"{title}:", *lines, ""] if lines else []


def _draft_line(draft: dict[str, Any]) -> str:
    return f"- {draft['title']} ({'a brief' if draft['kind'] == 'brief' else 'a drafted reply'}, not sent)"


def for_you(at: datetime) -> tuple[str, list[str]]:
    """What the morning brief is about: today's meetings, what waits, what was prepared overnight. (The text,
    the waiting moments it carries.) Empty text when there is nothing at all."""
    today = timing.day_start(at)
    waiting = [moment for moment in store.list_moments(("pending",), since=today - 86400)
               if moment["kind"] != "brief" and moment["urgency"] != "urgent"]
    waiting.reverse()  # Oldest first, as they came.
    prepared = store.drafts(_LISTED, since=today - OVERNIGHT_FROM_SECONDS, waiting=True)
    lines = (_section("Meetings today", _meetings(at.timestamp(), today + 86400))
             + _section("Waiting for you", [f"- {moment['title']}" for moment in waiting[:_LISTED]])
             + _section("Prepared overnight", [_draft_line(draft) for draft in prepared]))
    return "\n".join(lines).strip(), [moment["id"] for moment in waiting]


def _workflow_runs_done(since: float, besides: set[str]) -> list[str]:
    from row_bot import tasks

    conn = tasks._get_conn()
    try:
        rows = conn.execute("SELECT task_id, COALESCE(task_name, '') FROM task_runs WHERE started_at >= ? AND "
                            "status IN ('completed', 'completed_delivery_failed') ORDER BY started_at",
                            (datetime.fromtimestamp(since).isoformat(),)).fetchall()
    finally:
        conn.close()
    names = list(dict.fromkeys(str(row[1]) for row in rows if str(row[0]) not in besides and row[1]))
    return [f"- The workflow “{name}” ran" for name in names[:_LISTED]]


def day_wrap(at: datetime) -> str:
    """What the end-of-day wrap is about: what got done today, what moved to tomorrow, the drafts waiting."""
    today = timing.day_start(at)
    acted = [moment for moment in store.list_moments(("acted",), since=today - 7 * 86400)
             if moment["ended_at"] >= today]
    still_open = [moment for moment in store.list_moments(("pending", "shown"))
                  if moment["kind"] != "brief"]
    open_ids = {moment["id"] for moment in still_open}
    waiting = [draft for draft in store.drafts(100) if draft["moment_id"] in open_ids]
    own = set((store.get_state("workflows") or {}).values())
    lines = (_section("Done today", [f"- {moment['title']}" for moment in acted[:_LISTED]]
                      + _workflow_runs_done(today, own))
             + _section("Moved to tomorrow", [f"- {moment['title']}" for moment in still_open[:_LISTED]])
             + _section("Tomorrow's meetings", _meetings(today + 86400, today + 2 * 86400))
             + _section("Drafts waiting", [_draft_line(draft) for draft in waiting[:_LISTED]]))
    return "\n".join(lines).strip()


def gathered(variable: str) -> str:
    """``{{for_you}}`` / ``{{day_wrap}}`` in a workflow's message: what Proactive Row-Bot gathered at the
    break its run started at, or (a run the person started) what there is now."""
    name = _VARIABLES[variable]
    if name in _GATHERED:
        return _GATHERED[name]
    at = datetime.now()
    text = for_you(at)[0] if name == "brief" else day_wrap(at)
    return text or "Nothing in the calendar, nothing waiting and nothing prepared."


# --- Running it --------------------------------------------------------------------------------------------

def start_run(task: dict[str, Any], trigger: str) -> None:
    """A run of the workflow, as a chat turn in its own conversation (the reply comes back in ``finished``)."""
    from row_bot import tasks
    from row_bot.tools import registry

    thread_id = tasks._prepare_task_thread(task)
    tasks.run_task_background(task["id"], thread_id, [tool.name for tool in registry.get_enabled_tools()],
                              trigger=trigger)


def _run(name: str, text: str, carried: list[str], boundary: str, at: datetime) -> None:
    now = at.timestamp()
    runs = store.get_state("day_runs") or {}
    runs[name] = {"day": at.date().isoformat(), "boundary": boundary, "started": now}
    if not text:
        store.set_state("day_runs", runs)
        store.log("pass", f"{_WORDS[name]}: nothing for today, so none.", now=now)
        return
    task = workflow(name, now=now)
    if task is None:
        store.set_state("day_runs", runs)
        store.log("pass", f"{_WORDS[name]}: its workflow was deleted, so none. Turn it off and on in Settings › "
                  "Proactive to have it again.", now=now)
        return
    runs[name].update(task_id=task["id"], carried=carried)
    store.set_state("day_runs", runs)
    _GATHERED[name] = text
    store.log("pass", f"{_WORDS[name]}: running the “{task['name']}” workflow (reads only).", now=now,
              detail={"task_id": task["id"]})
    try:
        start_run(task, _WORDS[name])
    except Exception:
        logger.warning("The %s workflow couldn't start", name, exc_info=True)
        finished(task, "", "failed", "", "It couldn't start.", now=now)


def morning(boundary: str | None, at: datetime, settings: dict[str, Any]) -> None:
    """The first break of the day runs the morning brief (once a day)."""
    if (boundary not in timing.DAY_OPENERS or not settings["kinds"].get("brief")
            or store.kind_stats("brief")["quieted_at"]):
        return  # Not a day's first break, or the brief is off or went quiet: no run at all.
    if ((store.get_state("day_runs") or {}).get("brief") or {}).get("day") == at.date().isoformat():
        return
    text, carried = for_you(at)
    _run("brief", text, carried, boundary, at)


def evening(found: list[str], at: datetime, settings: dict[str, Any]) -> None:
    """The end of the day runs the end-of-day wrap, when it is on (once a day)."""
    from row_bot.proactive.settings import wrap_on

    if "day_end" not in found or not wrap_on(settings):
        return
    if ((store.get_state("day_runs") or {}).get("wrap") or {}).get("day") == at.date().isoformat():
        return
    _run("wrap", day_wrap(at), [], "day_end", at)


def _closed(reply: str) -> str:
    """The reply, ending with the closing line (once)."""
    lines = reply.strip().splitlines()
    while lines and lines[-1].strip(" *_>\"'“”").rstrip(".").casefold() in ("", CLOSING.rstrip(".").casefold()):
        lines.pop()
    text = "\n".join(lines).strip()[: BODY_CHARS - len(CLOSING) - 2].rstrip()
    return f"{text}\n\n{CLOSING}" if text else CLOSING


def finished(task: dict[str, Any], run_id: str, status: str, reply: str, notice: str, *,
             now: float | None = None) -> dict[str, Any] | None:
    """A run of the brief's (or wrap's) workflow ended: the one Proactive Row-Bot started today becomes the
    moment; a run the person started stays in the workflow's conversation."""
    now = time.time() if now is None else now
    name = which(str(task.get("id") or ""))
    runs = store.get_state("day_runs") or {}
    run = runs.get(name or "")
    today = datetime.fromtimestamp(now).date().isoformat()
    if not name or not run or run.get("task_id") != task["id"] or run.get("done") or run.get("day") != today:
        return None
    _GATHERED.pop(name, None)
    runs[name] = {**run, "done": True}
    store.set_state("day_runs", runs)
    if status not in _DONE or not reply.strip():
        store.log("pass", f"{_WORDS[name]}: the workflow didn't finish ({notice or status}), so none today.",
                  now=now, detail={"task_id": task["id"], "status": status})
        return None
    body = _closed(reply)
    candidate = Candidate(
        kind="brief", key=f"{name}:{today}", title=TITLES[name], summary=f"From the “{task['name']}” workflow.",
        evidence=(Evidence("workflow_run", run_id, str(task["name"]), "workflows", seen=str(task["id"])),),
        watcher=name, expires_at=now + EXPIRES_SECONDS[name],
        actions=({"id": "open_for_you", "label": "Open For you"},))
    moment = store.add_moment(candidate, title=candidate.title, body=body, now=now)
    if moment is None:
        return None
    carried = [item for item in run.get("carried") or [] if (store.get_moment(item) or {}).get("state") == "pending"]
    for item in carried:  # The brief carries them: each stays in For you, none comes again on its own.
        store.move(item, "shown", from_states=("pending",), now=now, surface="brief", shown_at=now)
    if now - float(run.get("started") or 0) <= SAME_BREAK_SECONDS:
        store.set_state("boundary", {"kind": run.get("boundary") or "app_open", "at": now})
    store.log("moment", f"{_WORDS[name]} ready" + (f", with {len(carried)} waiting" if carried else "") + ".",
              now=now, moment_id=moment["id"])
    return moment
