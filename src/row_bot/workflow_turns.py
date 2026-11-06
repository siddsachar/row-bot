"""A workflow run is a chat turn in the workflow's own conversation (F31).

The scheduler, Run now, a webhook or another workflow finishing starts a run: the
workflow's first message goes into its conversation as a turn the server starts,
the way a goal continues (``conversation_followups``). It runs exactly as a chat
turn does: every tool, tool search, the conversation's approval level, the same
approval cards and Stop. Each further message is a turn of its own after the one
before completes. When the last turn ends the run gets its outcome from what its
tools returned (never from the model's words) and its reply goes where the
workflow sends results.

A turn that fails (a provider limit, a cut-off reply, the network) is tried again
by itself after a wait, twice at most, unless the run has already done something
outside Row-Bot: then it ends and says so, and nothing is repeated.

A workflow keeps short notes between runs (5.3.1 G4): its first message carries
them, and the run keeps them current with ``task_update(notes=...)``, so a run
doesn't check again what an earlier one already did.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from typing import Any, Mapping

logger = logging.getLogger(__name__)

KIND = "workflow"
# A failed turn is tried again after these waits, in seconds.
RETRY_WAITS = (30.0, 120.0)
_LINK = re.compile(r"https?://[^\s<>\"')\]]+")
# Actions outside Row-Bot that ask on no card under every level: drafts and engagement.
_ALSO_OUTBOUND = frozenset({"x_engage", "outlook_draft_mail", "create_gmail_draft"})
_LOCK = threading.Lock()
_ATTEMPTS: dict[str, int] = {}  # run id -> passing failures tried again so far
NOTES_LIMIT = 2_000  # Characters a workflow's notes may hold.


def _service() -> Any:
    from row_bot.application.client_platform import client_platform_service
    return client_platform_service


def person_rules(conversation_id: str) -> dict | None:
    """The workflow's "Always ask me" apps and tools, when this is a workflow's conversation (N9)."""
    from row_bot import tasks
    conn = tasks._get_conn()
    try:
        row = conn.execute("SELECT always_ask FROM tasks WHERE persistent_thread_id = ? LIMIT 1",
                           (conversation_id,)).fetchone()
    finally:
        conn.close()
    try:
        items = json.loads(row["always_ask"] or "[]") if row else []
    except (TypeError, ValueError):
        items = []
    items = [str(item) for item in items if isinstance(item, str)] if isinstance(items, list) else []
    return {"step": False, "items": items} if items else None


def _notes_conn() -> Any:
    from row_bot import tasks
    return tasks._get_conn()


def notes(task_id: str) -> str:
    """The notes a workflow kept from its earlier runs ("" when none)."""
    conn = _notes_conn()
    try:
        row = conn.execute("SELECT text FROM workflow_notes WHERE task_id = ?", (task_id,)).fetchone()
    finally:
        conn.close()
    return str(row["text"]) if row else ""


def save_notes(task_id: str, text: str) -> str:
    """Replace a workflow's notes ("" clears them); the notes kept, cut to NOTES_LIMIT."""
    kept = str(text or "").strip()[:NOTES_LIMIT]
    conn = _notes_conn()
    try:
        with conn:
            if kept:
                conn.execute("INSERT INTO workflow_notes (task_id, text, updated_at) VALUES (?, ?, ?) "
                             "ON CONFLICT(task_id) DO UPDATE SET text = excluded.text, "
                             "updated_at = excluded.updated_at", (task_id, kept, datetime.now().isoformat()))
            else:
                conn.execute("DELETE FROM workflow_notes WHERE task_id = ?", (task_id,))
    finally:
        conn.close()
    return kept


def with_notes(task: Mapping[str, Any], prompt: str, *, held: list[dict[str, str]] | None = None) -> str:
    """A run's first message: the workflow's notes from earlier runs, how to keep them, then its message.

    *held*: the messages other conversations left for this run, already taken; None takes them here.
    """
    task_id = str(task["id"])
    # A one-off runs once: no notes for a next run, and nothing to say about them (U21).
    once = bool(task.get("at")) and not task.get("schedule")
    kept = "" if once else notes(task_id)
    lead = ("" if once else f"Notes this workflow kept from its earlier runs:\n{kept}" if kept
            else "This workflow has no notes from earlier runs yet.")
    if held is None:
        held = _take_held(task)
    if held:
        # Sent with when="next_run" by other conversations: information for this run, never the person's request.
        lead += ("\n\n" if lead else "") + (
            "Messages other conversations left for this run (their information, not the person's "
            "request):\n" + "\n".join(f"- From “{item['title']}”: {item['text']}" for item in held))
    if once:
        return f"{lead}\n\n---\n\n{prompt}" if lead else prompt
    return (f"{lead}\n\nBefore this run ends, keep them current for the next run with "
            f"task_update(task_id=\"{task_id}\", notes=...): what you checked and when, ids you'll need again, "
            f"and what's left. Replace them whole; under {NOTES_LIMIT:,} characters.\n\n---\n\n{prompt}")


def _take_held(task: Mapping[str, Any]) -> list[dict[str, str]]:
    conversation = str(task.get("persistent_thread_id") or "")
    if not conversation:
        return []
    from row_bot.linked_conversations import take_held

    return take_held(conversation)


def messages(task: Mapping[str, Any]) -> list[str]:
    """A workflow's messages, in the order its turns send them."""
    return [str(text) for text in task.get("prompts") or [] if str(text or "").strip()]


def _note(task: Mapping[str, Any], index: int, total: int, trigger: str) -> str:
    """What the conversation shows for a run's turn instead of its message: "Scheduled run · 26 Oct, 12:00"."""
    now = datetime.now()
    when = f"{now.day} {now:%b}, {now:%H:%M}"
    if index:
        return f"Message {index + 1} of {total}"
    return f"{trigger} · {when}"


def start(task: Mapping[str, Any], thread_id: str, run_id: str, *, trigger: str = "Run") -> bool:
    """Send the run's first message; False when it couldn't start (the run is then finished as failed)."""
    from row_bot import tasks
    sent = messages(task)
    if not sent:
        tasks._finish_run(run_id, "failed", notice="This workflow has no message to send.")
        return False
    with _LOCK:
        _ATTEMPTS.pop(run_id, None)
    return _send(task, thread_id, run_id, 0, trigger=trigger)


def _send(task: Mapping[str, Any], thread_id: str, run_id: str, index: int, *, trigger: str = "Run") -> bool:
    from row_bot import tasks
    from row_bot.application import conversation_followups as followups
    sent = messages(task)
    prompt = tasks.expand_template_vars(sent[index], task_id=str(task["id"]))
    held = _take_held(task) if index == 0 else []
    if index == 0:
        prompt = with_notes(task, prompt, held=held)
    # The run's line shows what other conversations left for it, under who sent each.
    followup = followups.Followup(kind=KIND, prompt=prompt, note=_note(task, index, len(sent), trigger),
                                  held=tuple(held))
    try:
        receipt = followups.start(_service(), thread_id, followup, model_ref=str(task.get("model_override") or ""))
    except Exception:  # The turn's worker could not start (the app is closing, or no thread was free).
        logger.warning("Workflow run %s could not start its turn", run_id, exc_info=True)
        receipt = None
    if receipt is None:
        tasks._finish_run(run_id, "failed", notice="The run couldn't start. Check this workflow's model, then run it again.")
        tasks.forget_active_run(thread_id)
        return False
    if index == 0:
        tasks.note_run_first_message(run_id, str(receipt.get("submission_id") or ""))
    return True


def after_turn(conversation_id: str, *, status: str, reply: str, error_text: str = "") -> None:
    """A turn in a conversation ended: move its workflow run on, if one is going there.

    Runs in the turn's own producer before the turn is finished, so the next message is
    scheduled in time to follow it; the run's end (delivery, notices) runs in the background.
    """
    from row_bot import tasks
    run = tasks.active_run_for_conversation(conversation_id)
    if run is None:
        return
    task = tasks.get_task(str(run["task_id"]))
    run_id = str(run["id"])
    if task is None:
        _finish_later(run, None, "stopped", reply)
        return
    if status == "waiting_approval":
        tasks.mark_run_waiting(run_id, task)
        return
    if status == "completed":
        done = int(run.get("steps_done") or 0) + 1
        tasks._update_run_progress(run_id, done)
        if done < len(messages(task)):
            from row_bot.application import conversation_followups as followups
            sent = messages(task)
            followups.schedule(conversation_id, followups.Followup(
                kind=KIND, prompt=tasks.expand_template_vars(sent[done], task_id=str(task["id"])),
                note=_note(task, done, len(sent), "")))
            return
        _finish_later(run, task, "completed", reply)
        return
    if status == "stopped":
        _finish_later(run, task, "stopped", reply)
        return
    if _retry(run, task, conversation_id, error_text):
        return
    _finish_later(run, task, "failed", reply, reason=_failure_words(error_text))


def _failure_words(error_text: str) -> str:
    text = " ".join(str(error_text or "").split()).lstrip("⚠️ ").strip()
    return f"The run didn't finish: {text}" if text else "The run didn't finish."


def _retry(run: Mapping[str, Any], task: Mapping[str, Any], conversation_id: str, error_text: str) -> bool:
    """Try a failed turn again, unless the run already acted outside Row-Bot (then trying again could
    post or send twice). A provider limit that says when it resets waits for the reset."""
    from row_bot import goals, tasks
    run_id = str(run["id"])
    if actions(conversation_id, str(run.get("first_message_id") or "")):
        return False
    with _LOCK:
        tried = _ATTEMPTS.get(run_id, 0)
        if tried >= len(RETRY_WAITS):
            return False
        _ATTEMPTS[run_id] = tried + 1
    wait = max(RETRY_WAITS[tried], goals.provider_limit_wait(error_text) or 0.0)
    tasks.note_run_retry(run_id, f"Trying again in {_about(wait)}: {_failure_words(error_text)}")
    index = int(run.get("steps_done") or 0)

    def again() -> None:
        current = tasks.read_task_run(run_id) or {}
        if current.get("status") not in tasks.ACTIVE_RUN_STATUSES:
            return  # Stopped or replaced while it waited.
        if _service().registry.active(conversation_id):
            _later(1.0, again)  # The failed turn is still ending, or the person is using the chat.
            return
        latest = tasks.get_task(str(task["id"]))
        if latest is not None:
            _send(latest, conversation_id, run_id, min(index, len(messages(latest)) - 1))

    _later(wait, again)
    return True


def _later(seconds: float, run: Any) -> None:
    timer = threading.Timer(seconds, run)
    timer.daemon = True
    timer.start()


def _about(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{int(seconds)} seconds" if seconds < 90 else f"{minutes} minutes"


def _finish_later(run: Mapping[str, Any], task: Mapping[str, Any] | None, status: str, reply: str,
                  *, reason: str = "") -> None:
    from row_bot import tasks
    from row_bot.application.client_platform import run_in_background
    tasks.forget_active_run(str(run["thread_id"]))
    with _LOCK:
        _ATTEMPTS.pop(str(run["id"]), None)
    run_in_background(lambda: _finish(run, task, status, reply, reason),
                      name=f"workflow-run-end-{run['id']}")


def _finish(run: Mapping[str, Any], task: Mapping[str, Any] | None, status: str, reply: str, reason: str) -> None:
    from row_bot import tasks
    run_id, thread_id = str(run["id"]), str(run["thread_id"])
    done = actions(thread_id, str(run.get("first_message_id") or ""))
    denied = _denied_since(thread_id, str(run.get("started_at") or ""))
    unsure = next((item for item in done if item["unsure"]), None)
    if status == "completed" and unsure:
        status, notice = "unsure", unsure["result"]
    elif status == "completed" and denied:
        before = "; ".join(item["result"] for item in done)
        status, notice = "denied", (f"You said no to “{denied}”, so it stopped there."
                                    + (f" Before that: {before}" if before else ""))
        if task is not None:
            _note_denial(task, denied, done)
    elif status == "completed":
        notice = "; ".join(item["result"] for item in done) or _first_line(reply) or "Finished."
    elif status == "stopped":
        notice = "You stopped it."
    else:
        notice = reason or "The run didn't finish."
    delivery_status, delivery_detail = "", ""
    if task is not None and _delivers(task, status):
        text = outcome(status, notice, done)
        if reply and status in {"completed", "unsure"}:
            text += "\n\n" + reply
        try:
            delivery_status, delivery_detail = tasks._deliver_to_channels(dict(task), text)
        except Exception as exc:
            logger.warning("Delivering workflow run %s failed", run_id, exc_info=True)
            delivery_status, delivery_detail = "delivery_failed", f"Delivery failed: {exc}"
    if status == "completed" and delivery_status == "delivery_failed":
        status = "completed_delivery_failed"
    tasks._finish_run(run_id, status, status_message=delivery_detail, notice=notice[:512],
                      actions=[{key: item[key] for key in ("tool", "title", "result", "link")} for item in done])
    if status == "failed":
        _service().conversation_changed(thread_id, reread=True)  # Its chat says why under the run's line.
    if task is None:
        return
    tasks.after_run_finished(dict(task), run_id, thread_id, status, notice, delivery_status, delivery_detail)


def outcome(status: str, notice: str, done: list[Mapping[str, Any]]) -> str:
    """What a run did, in one line, ahead of what it delivers (G8): what went out, or that nothing did."""
    sent = "; ".join(str(item.get("result") or "") for item in done if item.get("result"))
    if status == "unsure":
        return f"Not sure it went through · {notice}"
    if status == "denied":
        return "You said no · nothing went out"
    if status in {"failed", "expired"}:
        return f"Didn't finish · {notice}" if notice else "Didn't finish"
    return f"Sent · {sent}" if sent else "Finished · nothing was posted or sent"


def _delivers(task: Mapping[str, Any], status: str) -> bool:
    """Whether this run's ending goes to the workflow's channels now (a digest sends it later)."""
    rule = task.get("send_results") or "every_run"
    if rule == "every_run":
        return status in {"completed", "unsure", "denied", "failed"}
    return rule == "needs_me" and status in {"unsure", "failed"}


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        cleaned = line.strip().strip("#*_ ").strip()
        if cleaned:
            return cleaned[:240]
    return ""


def _outbound_names() -> set[str]:
    """Tools that act outside Row-Bot: each tool's own list of actions that ask, plus drafts and likes."""
    from row_bot.tools import registry as tool_registry
    names = set(_ALSO_OUTBOUND)
    for tool in tool_registry.get_all_tools():
        try:
            names.update(tool.destructive_tool_names)
        except Exception:
            continue
    return names


def actions(conversation_id: str, first_message_id: str) -> list[dict[str, Any]]:
    """What this run's turns did outside Row-Bot, read from the tool results in its conversation."""
    if not first_message_id:
        return []
    from row_bot import threads
    from row_bot.tools.x_posting import _NOT_SURE
    saved = threads.checkpointer.get_tuple({"configurable": {"thread_id": conversation_id, "checkpoint_ns": ""}})
    history = list(saved.checkpoint.get("channel_values", {}).get("messages", []) or []) if saved else []
    ids = [str(getattr(message, "id", "") or "") for message in history]
    if first_message_id not in ids:
        return []
    outbound = _outbound_names()
    found: list[dict[str, Any]] = []
    for message in history[ids.index(first_message_id):]:
        name = str(getattr(message, "name", "") or "")
        if getattr(message, "type", "") != "tool" or name not in outbound:
            continue
        content = message.content if isinstance(message.content, str) else json.dumps(message.content)
        link = _LINK.search(content)
        found.append({"tool": name, "title": _title(name), "result": _first_line(content) or "Done.",
                      "link": link.group(0).rstrip(".,") if link else "", "unsure": _NOT_SURE in content})
    return found


def _title(name: str) -> str:
    from row_bot.integrations import builtin
    return builtin.tool_title(name) or name.replace("_", " ").capitalize()


def _denied_since(conversation_id: str, started_at: str) -> str | None:
    """What the person said no to in this run, in a few words; None when they said no to nothing."""
    from row_bot import tasks
    conn = tasks._get_conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(message, ''), COALESCE(approval_payload_json, '{}') FROM approval_requests "
            "WHERE source_thread_id=? AND requested_at>=? AND status='denied' ORDER BY requested_at DESC LIMIT 1",
            (conversation_id, started_at)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        payload = json.loads(row[1] or "{}")
    except ValueError:
        payload = {}
    what = str(payload.get("question") or payload.get("label") or payload.get("description") or row[0] or "")
    return _first_line(what).rstrip("?").strip()[:160] or "an action"


def _note_denial(task: Mapping[str, Any], what: str, done: list[Mapping[str, Any]]) -> None:
    """A denied run keeps a dated line in its notes, so the next run knows and doesn't try it another way."""
    went = "; ".join(str(item.get("result") or "") for item in done if item.get("result")) or "nothing went out"
    line = (f"{datetime.now():%d %b %H:%M}: the person said no to “{what}”, so that run stopped there ({went}). "
            "Don't do it another way; if it still matters, say so in the next run's report.")
    kept = notes(str(task["id"]))
    save_notes(str(task["id"]), (kept + "\n" + line).strip()[-NOTES_LIMIT:] if kept else line)
