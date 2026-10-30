"""Waits (5.3.1): a chat or a workflow run waits for something and carries on when it happens.

What it can wait for:

- one of the conversation's background processes ends, or prints a given text;
- a check every few minutes: a command (a CI status, a deploy) or a web page, until it shows a given text, or,
  for a command with no text, until it exits 0;
- a time ("in 30 minutes", "at 14:00");
- another conversation finishes its turn, or a workflow finishes its run.

No model runs while it waits. When it happens, or the wait runs out, a turn starts in the conversation that set it:
"Checked back: …", with what happened. If the conversation is busy then, it gets the turn when that one ends.

Waits live in tasks.db beside linked conversations, so time waits and checks survive a restart; a process wait
ends then (Row-Bot's processes stop when it quits) and says so. The scheduler ticks them every few seconds.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

_LOG = logging.getLogger(__name__)

Kind = Literal["process", "check", "time", "conversation"]
KINDS: tuple[str, ...] = ("process", "check", "time", "conversation")
MAX_PER_CONVERSATION = 5
MAX_HOURS = 24.0  # A process, check or conversation wait.
MAX_TIME_DAYS = 7.0  # A time wait.
MIN_EVERY_MINUTES = 1.0
CHECK_SECONDS = 60  # A check's command may run this long.
TICK_SECONDS = 15
_OUTPUT_CHARS = 2_000  # Of a check's output, kept for the turn it starts.
_COLUMNS = ("id", "conversation_id", "kind", "label", "spec", "every", "next_at", "until", "status", "result",
            "created_at", "ended_at")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM waits"
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = ""
_TICK_LOCK = threading.Lock()


class WaitError(ValueError):
    """A wait that can't be set, said in words."""


@dataclass(frozen=True)
class Outcome:
    met: bool
    words: str  # What happened, for the turn it starts.


# --- Storage -----------------------------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    from row_bot.tasks import _DB_PATH, _get_conn

    global _SCHEMA_READY
    conn = _get_conn()
    with _SCHEMA_LOCK:
        if _SCHEMA_READY != str(_DB_PATH):
            conn.executescript(
                "CREATE TABLE IF NOT EXISTS waits ("
                " id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, kind TEXT NOT NULL,"
                " label TEXT NOT NULL DEFAULT '', spec TEXT NOT NULL DEFAULT '{}', every REAL NOT NULL DEFAULT 0,"
                " next_at REAL NOT NULL, until REAL NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',"
                " created_at REAL NOT NULL, ended_at REAL NOT NULL DEFAULT 0);"
                "CREATE INDEX IF NOT EXISTS waits_due ON waits(status, next_at);"
                "CREATE INDEX IF NOT EXISTS waits_conversation ON waits(conversation_id, status);")
            _SCHEMA_READY = str(_DB_PATH)
    return conn


def _row(values: tuple) -> dict[str, Any]:
    record = dict(zip(_COLUMNS, values))
    record["spec"] = json.loads(record["spec"] or "{}")
    return record


def _update(wait_id: str, **values: Any) -> None:
    if "spec" in values:
        values["spec"] = json.dumps(values["spec"])
    sets = ", ".join(f"{key} = ?" for key in values)
    with closing(_conn()) as conn, conn:
        conn.execute(f"UPDATE waits SET {sets} WHERE id = ?", (*values.values(), wait_id))
    _publish()


def _publish() -> None:
    try:
        from row_bot.application.live_updates import live_updates
        live_updates.publish("conversations")
    except Exception:
        _LOG.debug("Live update for waits skipped", exc_info=True)


def get(wait_id: str) -> dict[str, Any] | None:
    with closing(_conn()) as conn, conn:
        row = conn.execute(f"{_SELECT} WHERE id = ?", (wait_id,)).fetchone()
    return _row(row) if row else None


def waiting(conversation_id: str | None = None) -> list[dict[str, Any]]:
    """Waits still waiting (or due and about to start their turn), oldest first; one conversation's, or all."""
    where, args = "status IN ('waiting', 'due')", ()
    if conversation_id:
        where, args = where + " AND conversation_id = ?", (conversation_id,)
    with closing(_conn()) as conn, conn:
        rows = conn.execute(f"{_SELECT} WHERE {where} ORDER BY created_at", args).fetchall()
    return [_row(row) for row in rows]


# --- Setting one -------------------------------------------------------------------------------------------

def create(conversation_id: str, kind: str, label: str, *, spec: dict[str, Any], every_minutes: float = 0.0,
           hours: float = 0.0, at: datetime | None = None, now: float | None = None) -> dict[str, Any]:
    """Set a wait: checked first after *every_minutes* (a check), at *at* (a time), else soon; ends after *hours*."""
    now = time.time() if now is None else now
    if kind not in KINDS:
        raise WaitError(f"A wait is for one of: {', '.join(KINDS)}.")
    label = re.sub(r"\s+", " ", str(label or "")).strip()[:120]
    if not label:
        raise WaitError("Say what it waits for, in a few words: the person sees it.")
    if len(waiting(conversation_id)) >= MAX_PER_CONVERSATION:
        raise WaitError(f"This conversation already waits for {MAX_PER_CONVERSATION} things: stop one first.")
    every = 0.0
    if kind == "check":
        if every_minutes < MIN_EVERY_MINUTES:
            raise WaitError(f"A check runs at most once a minute: every_minutes is {MIN_EVERY_MINUTES:g} or more.")
        every = float(every_minutes) * 60
    if kind == "time":
        if at is None:
            raise WaitError("Say when: in_minutes, or at a local time like '14:00' or '2026-10-29T09:30'.")
        moment = at.timestamp()
        if moment <= now:
            raise WaitError(f"{at:%Y-%m-%d %H:%M} has passed.")
        if moment - now > MAX_TIME_DAYS * 86400:
            raise WaitError(f"A wait for a time is at most {MAX_TIME_DAYS:g} days ahead.")
        next_at, until = moment, moment
    else:
        hours = float(hours or 0) or 2.0
        if not 0 < hours <= MAX_HOURS:
            raise WaitError(f"A wait lasts at most {MAX_HOURS:g} hours.")
        next_at = now + (every or TICK_SECONDS)
        until = now + hours * 3600
    record = {"id": uuid.uuid4().hex[:12], "conversation_id": conversation_id, "kind": kind, "label": label,
              "spec": json.dumps(spec), "every": every, "next_at": next_at, "until": until, "status": "waiting",
              "result": "", "created_at": now, "ended_at": 0.0}
    with closing(_conn()) as conn, conn:
        conn.execute(f"INSERT INTO waits ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                     tuple(record[key] for key in _COLUMNS))
    _publish()
    return get(record["id"]) or {}


def stop(wait_id: str, *, conversation_id: str = "", by: str = "person") -> bool:
    """Stop a wait; nothing starts. False when it isn't waiting (or belongs to another conversation)."""
    record = get(wait_id)
    if record is None or record["status"] not in ("waiting", "due"):
        return False
    if conversation_id and record["conversation_id"] != conversation_id:
        return False
    _update(wait_id, status="stopped", result=f"Stopped by {'the person' if by == 'person' else 'Row-Bot'}.",
            ended_at=time.time())
    return True


def heard_from(conversation_id: str, sender_id: str) -> None:
    """A message from *sender_id* arrived in *conversation_id*: a wait there for that conversation has its
    answer in the message, so it ends without starting a turn of its own."""
    for record in waiting(conversation_id):
        if record["kind"] == "conversation" and record["spec"].get("conversation_id") == sender_id:
            _update(record["id"], status="done", result="Its message arrived.", ended_at=time.time())


def stop_all_for(conversation_id: str) -> None:
    """A conversation that's deleted waits for nothing more."""
    for record in waiting(conversation_id):
        _update(record["id"], status="stopped", result="Its conversation was deleted.", ended_at=time.time())


# --- Checking ----------------------------------------------------------------------------------------------

def _contains(text: str, wanted: str) -> bool:
    return wanted.casefold() in (text or "").casefold()


def _check_process(spec: dict[str, Any]) -> Outcome:
    from row_bot import background_processes as processes

    record = processes.get(spec.get("process_id", ""))
    if record is None:
        return Outcome(True, "The process is gone: Row-Bot restarted, which stops its background processes.")
    wanted = str(spec.get("text") or "")
    if wanted:
        text, _cut = record.log.tail(64_000)
        if _contains(text, wanted):
            return Outcome(True, f"It printed “{wanted}”. Its last output:\n{text[-_OUTPUT_CHARS:]}")
    if not record.running:
        from row_bot.tools.shell_tool import _status_words

        text, _cut = record.log.tail(8_000)
        return Outcome(True, f"It {_status_words(record)}. Its last output:\n{text[-_OUTPUT_CHARS:]}")
    return Outcome(False, "")


def _check_command(conversation_id: str, spec: dict[str, Any]) -> Outcome:
    from row_bot.data_paths import default_workspace_root
    from row_bot.tools.shell_tool import shell_argv

    command = str(spec.get("command") or "")
    folder = str(spec.get("folder") or "") or str(default_workspace_root())
    try:
        done = subprocess.run(shell_argv(command, report_cwd=False), cwd=folder, capture_output=True,  # noqa: S603
                              text=True, encoding="utf-8", errors="replace", timeout=CHECK_SECONDS)
        output, code = (done.stdout or "") + (done.stderr or ""), done.returncode
    except subprocess.TimeoutExpired:
        return Outcome(False, "")
    except OSError as error:
        return Outcome(True, f"The check couldn't run: {error}.")
    wanted = str(spec.get("text") or "")
    met = _contains(output, wanted) if wanted else code == 0
    tail = output.strip()[-_OUTPUT_CHARS:]
    return Outcome(met, (f"`{command}` " + (f"shows “{wanted}”" if wanted else "exits 0")
                         + f". Its output:\n{tail}") if met else "")


def _check_page(spec: dict[str, Any]) -> Outcome:
    from row_bot.tools.url_reader_tool import _read_url

    url, wanted = str(spec.get("url") or ""), str(spec.get("text") or "")
    page = _read_url(url)
    if page.startswith(("Error", "Blocked", "BLOCKED", "Couldn't", "Could not")):
        return Outcome(False, "")
    if _contains(page, wanted):
        at = page.casefold().find(wanted.casefold())
        return Outcome(True, f"{url} shows “{wanted}”: …{page[max(0, at - 300):at + 300]}…")
    return Outcome(False, "")


def _check_conversation(record: dict[str, Any]) -> Outcome:
    from row_bot import tasks
    from row_bot.application.client_platform import client_platform_service as service

    target = str(record["spec"].get("conversation_id") or "")
    name = str(record["spec"].get("name") or target)
    if service.registry.active(target) or tasks.active_run_for_conversation(target):
        return Outcome(False, "")
    run = tasks._get_conn().execute(
        "SELECT status, finished_at, COALESCE(notice, '') FROM task_runs WHERE thread_id = ? "
        "AND COALESCE(finished_at, '') != '' ORDER BY finished_at DESC LIMIT 1", (target,)).fetchone()
    if run and datetime.fromisoformat(str(run[1])).timestamp() >= record["created_at"]:
        return Outcome(True, f"“{name}” finished its run: {run[0]}. {run[2]}".strip())
    meta = service._metadata(target) if target else {}
    updated = str(meta.get("updated_at") or "")
    try:
        moved = updated and datetime.fromisoformat(updated).timestamp() >= record["created_at"]
    except ValueError:
        moved = False
    return Outcome(True, f"“{name}” finished its turn.") if moved else Outcome(False, "")


def check(record: dict[str, Any], now: float) -> Outcome:
    """Whether a wait's thing has happened (or its time has come), and what happened."""
    kind, spec = record["kind"], record["spec"]
    if kind == "time":
        return Outcome(now >= record["next_at"], "It's time.")
    if kind == "process":
        return _check_process(spec)
    if kind == "conversation":
        return _check_conversation(record)
    return _check_page(spec) if spec.get("url") else _check_command(record["conversation_id"], spec)


def _prompt(record: dict[str, Any], words: str, *, ran_out: bool) -> tuple[str, str]:
    """(the model's prompt, the line the chat shows) for a wait that ended."""
    head = (f"Checked back: “{record['label']}” didn't happen before the wait ran out."
            if ran_out else f"Checked back: {record['label']}.")
    asked = str(record["spec"].get("then") or "")
    prompt = (f"{head}\n\nThis is Row-Bot, not the person: a wait this conversation set has ended.\n{words}".strip()
              + (f"\n\nWhat you said to do then: {asked}" if asked else "")
              + "\n\nCarry on with the work it was waiting for, or say what's changed.")
    return prompt, head


def _start_turn(record: dict[str, Any], words: str, *, ran_out: bool) -> bool:
    from row_bot.application import conversation_followups as followups
    from row_bot.application.client_platform import client_platform_service as service

    conversation = record["conversation_id"]
    if service.registry.active(conversation) or service._approval_waiting(conversation):
        return False
    prompt, note = _prompt(record, words, ran_out=ran_out)
    receipt = followups.start(service, conversation, followups.Followup(kind="wait", prompt=prompt, note=note))
    return receipt is not None


def tick(now: float | None = None) -> int:
    """Check the waits that are due; start a turn for each that's done. Returns how many turns started."""
    now = time.time() if now is None else now
    if not _TICK_LOCK.acquire(blocking=False):
        return 0
    started = 0
    try:
        with closing(_conn()) as conn, conn:
            rows = conn.execute(f"{_SELECT} WHERE status IN ('waiting', 'due') AND next_at <= ? ORDER BY next_at",
                                (now,)).fetchall()
        for record in map(_row, rows):
            try:
                if record["status"] == "due":
                    outcome, ran_out = Outcome(True, record["result"]), record["result"].startswith("ran out:")
                    words = record["result"].removeprefix("ran out:")
                else:
                    outcome = check(record, now)
                    ran_out = not outcome.met and now >= record["until"]
                    words = outcome.words
                    if not outcome.met and not ran_out:
                        _update(record["id"], next_at=now + (record["every"] or TICK_SECONDS))
                        continue
                if _start_turn(record, words, ran_out=ran_out):
                    _update(record["id"], status="ran_out" if ran_out else "done", result=words, ended_at=now)
                    started += 1
                else:  # Busy: its turn starts when the conversation is free.
                    _update(record["id"], status="due", result=("ran out:" if ran_out else "") + words,
                            next_at=now + TICK_SECONDS)
            except Exception:
                _LOG.warning("A wait could not be checked", exc_info=True)
                _update(record["id"], next_at=now + max(record["every"], 60.0))
    finally:
        _TICK_LOCK.release()
    return started


def schedule(scheduler: Any) -> None:
    """At start-up: tick every few seconds while Row-Bot runs."""
    scheduler.add_job(tick, trigger="interval", seconds=TICK_SECONDS, id="row-bot-waits", replace_existing=True,
                      coalesce=True, max_instances=1)


def view(record: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """A wait as the person sees it: what it waits for, how often it looks, and until when."""
    now = time.time() if now is None else now
    every = record["every"]
    how = (f"checks every {every / 60:g} min" if every else
           f"at {datetime.fromtimestamp(record['next_at']):%H:%M}" if record["kind"] == "time" else "watching")
    return {"wait_id": record["id"], "conversation_id": record["conversation_id"], "kind": record["kind"],
            "label": record["label"], "how": how,
            "until": datetime.fromtimestamp(record["until"]).isoformat(timespec="minutes"),
            "status": record["status"]}
