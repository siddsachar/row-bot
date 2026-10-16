"""Reading the last 30 days of past chats into Learning (D26).

When Learning is turned on with "Also learn from the last 30 days of chats" ticked, Row-Bot
reads the owner's in-scope conversations from their saved checkpoints and records each turn as a
historical episode of *partial* fidelity. What a model saw then (its system prompt, tool schemas,
compaction) can't be recovered, so:

- a model call's request is the conversation as stored up to that reply (``reconstructed``);
- a reply is the reply as it was kept, never labelled as the model's own output (E3: the runtime
  may have changed it before keeping it), so it is ``repaired_output_blob`` with
  ``executed="repaired"`` and no ``raw_output_blob``;
- a tool call is its arguments (secrets dropped) and its result as stored, with its untrusted
  stamp; approvals and signals are not recoverable.

Which conversations: those *updated* in the 30 days before the import started (O10). Messages carry
no time of their own, but checkpoints do: a conversation created before the window is read from the
first message saved after its newest checkpoint older than the window (found by bisecting its
checkpoints by their timestamps), so a long-lived owner channel chat gives its last 30 days and
nothing older. When the checkpoints from before the window were already tidied away (Row-Bot keeps
the newest few per conversation), only what came after the oldest kept checkpoint counts: never a
turn that might be older than 30 days. A conversation created in the window is read whole. Each is
read up to the length it had when the import was planned, so turns captured live afterwards are
never read twice. Scope is decided per conversation with ``historical=True`` (past chats on; never
a shared conversation, an opted-out chat, or a Slack or Discord DM, whose sender is unknown, O3).
A paused Learning stops the import; resuming carries it on.

One job (:data:`JOB_ID`) holds the plan and the progress the Overview shows ("Reading 214 past
chats · 61 done"). A conversation's records and the job's progress are written in one
transaction, and their ids are derived from the conversation, so after a restart it resumes where
it was and never imports a conversation twice. It runs on a background thread, yields while any
chat is generating (``generation_registry``), and stops when Learning is turned off. It is a plain
resumable function (:func:`run_import`) and a small starter (:func:`start_background`), so the
shared runner and resource lease (L2a/L3a) can take it over.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from row_bot import learning as gate
from row_bot.learning import contracts, redaction, scope
from row_bot.learning.capture import (
    REQUEST_FORMAT,
    TOOL_CALL_FORMAT,
    _SAVED_OUTPUT,
    _canonical,
    _is_untrusted,
    _message_dict,
    _Sink,
)
from row_bot.learning.contracts import Decision, Episode, Job
from row_bot.learning.store import LearningStore, LearningStoreError

logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
JOB_ID = "job_past_chats"
PLAN_FORMAT = "row_bot.learning.past_chat_plan"
CONVERSATION_FORMAT = "row_bot.learning.conversation"
_BUSY_WAIT_SECONDS = 5.0


@dataclass(frozen=True)
class ImportProgress:
    """Where the import stands: ``done`` of ``total`` conversations read."""

    total: int
    done: int
    # planned | running | paused (a chat is generating) | stopped | ready (finished) | failed
    state: str

    @property
    def finished(self) -> bool:
        return self.state == "ready"


class PastChatSource(Protocol):
    def conversations(self, since: float) -> list[tuple[str, float]]:
        """(conversation, created at) for those updated at or after ``since`` (epoch seconds),
        oldest first."""

    def messages(self, thread_id: str) -> list[Any]:
        """A conversation's saved messages, in order."""

    def window_start(self, thread_id: str, since: float, created_at: float) -> tuple[int, float]:
        """(index of the first saved message inside the window, when that part began).

        ``(0, created_at)`` for a conversation created in the window."""


def _timestamp(value: Any) -> float | None:
    try:
        moment = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return moment.timestamp()


class SavedChats:
    """The conversations in ``threads.db`` and their checkpoints."""

    def conversations(self, since: float) -> list[tuple[str, float]]:
        from row_bot import threads

        found: list[tuple[float, str]] = []
        for row in threads._list_threads(include_details=True):  # noqa: SLF001 - one read of thread_meta
            thread_id = str(row[0] or "")
            created_at = _timestamp(row[2])
            if created_at is None:
                continue
            updated_at = _timestamp(row[3]) or created_at
            if max(created_at, updated_at) >= since and contracts.valid_thread_id(thread_id):
                found.append((created_at, thread_id))
        return [(thread_id, created_at) for created_at, thread_id in sorted(found)]

    def messages(self, thread_id: str) -> list[Any]:
        from row_bot.threads import get_latest_checkpoint_messages

        return list(get_latest_checkpoint_messages(thread_id))

    @staticmethod
    def _checkpoint(thread_id: str, checkpoint_id: str) -> tuple[float | None, int]:
        """(saved at, how many messages it holds) for one checkpoint of a conversation."""

        from row_bot.threads import checkpointer

        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": "", "checkpoint_id": checkpoint_id}}
        saved = checkpointer.get_tuple(config)
        checkpoint = getattr(saved, "checkpoint", None) if saved else None
        if not isinstance(checkpoint, dict):
            return None, 0
        values = checkpoint.get("channel_values") or {}
        messages = values.get("messages") if isinstance(values, dict) else None
        return _timestamp(checkpoint.get("ts")), len(messages) if isinstance(messages, list) else 0

    def window_start(self, thread_id: str, since: float, created_at: float) -> tuple[int, float]:
        if created_at >= since:
            return 0, created_at
        from row_bot.threads import checkpointer

        if hasattr(checkpointer, "cursor"):
            with checkpointer.cursor(transaction=False) as cursor:
                ids = [str(row[0]) for row in cursor.execute(
                    "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? AND checkpoint_ns = ''"
                    " ORDER BY checkpoint_id", (thread_id,))]
        else:
            # Ids only: listing is newest first and its ids sort by time.
            config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            listed = (str((item.config.get("configurable") or {}).get("checkpoint_id") or "")
                      for item in checkpointer.list(config))
            ids = sorted(checkpoint_id for checkpoint_id in listed if checkpoint_id)
        return checkpoint_window(ids, since, lambda checkpoint_id: self._checkpoint(thread_id, checkpoint_id))


def checkpoint_window(
    ids: Sequence[str], since: float, read: Callable[[str], tuple[float | None, int]]
) -> tuple[int, float]:
    """Bisect a conversation's checkpoints (oldest first; their ids sort by time) for the window.

    Returns (the index of the first message saved inside the window, when that part began). A
    message first appears in the checkpoint written when it was saved, so the newest checkpoint
    older than the window holds exactly the messages from before it. When every kept checkpoint
    is inside the window (older ones were tidied away), only messages after the oldest kept one
    are known to be recent. With no checkpoint inside the window nothing is recent.
    """

    seen: dict[int, tuple[float | None, int]] = {}

    def at(index: int) -> tuple[float | None, int]:
        if index not in seen:
            seen[index] = read(ids[index])
        return seen[index]

    low, high = 0, len(ids)
    while low < high:
        middle = (low + high) // 2
        stamp = at(middle)[0]
        # An unreadable time counts as old: it never lets an older message in.
        if stamp is None or stamp < since:
            low = middle + 1
        else:
            high = middle
    if low >= len(ids):
        return (at(len(ids) - 1)[1] if ids else 0), since
    began = at(low)[0] or since
    if low > 0:
        return at(low - 1)[1], began
    return at(0)[1], began


def progress(store: LearningStore | None = None) -> ImportProgress | None:
    """The import's progress, or None when none was started. Never creates a store."""

    job = (store or LearningStore()).get("job", JOB_ID)
    if not isinstance(job, Job):
        return None
    return ImportProgress(total=int(job.steps_total or 0), done=job.steps_done, state=job.state)


def _derived_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("\x00".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:40]}"


def _person_started(message: Any) -> bool:
    """A turn the person started (not a goal step's prompt or an event Row-Bot added)."""

    extra = getattr(message, "additional_kwargs", None)
    extra = extra if isinstance(extra, dict) else {}
    ui = extra.get("row_bot_ui")
    return not (extra.get("platform_note") or extra.get("row_bot_internal_event")
                or (isinstance(ui, dict) and ui.get("thread_event")))


def _turns(messages: Sequence[Any]) -> list[tuple[int, int]]:
    """(start, end) of each turn: from a person's message to the next one."""

    from row_bot.turn_context import is_person_message

    starts = [index for index, message in enumerate(messages) if is_person_message(message)]
    return [(start, starts[number + 1] if number + 1 < len(starts) else len(messages))
            for number, start in enumerate(starts)]


def _conversation_records(
    thread_id: str,
    messages: Sequence[Any],
    *,
    facts: scope.SourceFacts,
    started_at: float,
    sink: _Sink,
    first_index: int = 0,
) -> list[contracts.Record]:
    """A past conversation's turns as historical, partial episodes and decisions.

    Turns that begin before ``first_index`` (before the window) are left out; the requests of
    the turns that are read still carry the whole conversation up to each reply.
    """

    conversation = facts.root_thread_id or thread_id
    if not contracts.valid_thread_id(conversation):
        conversation = thread_id
    if facts.conversation == "workflow":
        source, channel = "workflow", None
    elif facts.conversation == "channel":
        source, channel = "channel_chat", facts.channel
    else:
        source, channel = "my_chat", None
    hashes: list[str] = []
    # Whether any tool or web result came before each message (the request then carries it).
    untrusted_before: list[bool] = []
    seen_untrusted = False
    for message in messages:
        sha, _ = sink.json_blob(_message_dict(message))
        hashes.append(sha)
        untrusted_before.append(seen_untrusted)
        seen_untrusted = seen_untrusted or _is_untrusted(message)
    conversation_sha = sink.blob(_canonical({"format": CONVERSATION_FORMAT, "version": 1, "messages": hashes}))
    records: list[contracts.Record] = []
    previous_reply: str | None = None
    for number, (start, end) in enumerate(_turns(messages)):
        if start < first_index:
            continue
        episode_id = _derived_id("eph", thread_id, number)
        # Turns carry no time of their own: they are ordered within the conversation's first day.
        turn_at = started_at + number * 0.001
        calls: dict[str, dict[str, Any]] = {}
        sequence = 0
        last_reply: str | None = None
        for index in range(start + 1, end):
            message = messages[index]
            kind = str(getattr(message, "type", "") or "")
            if kind == "ai":
                for call in getattr(message, "tool_calls", None) or []:
                    if isinstance(call, dict) and call.get("id"):
                        calls[str(call["id"])] = call
                request = sink.blob(_canonical({
                    "format": REQUEST_FORMAT,
                    "version": 1,
                    "purpose": "historical",
                    # The conversation as stored, up to this reply.
                    "messages_of": conversation_sha,
                    "prefix": index,
                    "tools": None,
                    "model_ref": None,
                    "provider": None,
                    "latency_ms": None,
                    "error": None,
                    "reconstructed": True,
                }))
                reply, _ = sink.json_blob(_message_dict(message))
                decision_id = _derived_id("dech", thread_id, number, index)
                records.append(Decision(
                    id=decision_id, episode_id=episode_id, thread_id=thread_id, sequence=sequence,
                    kind="model_call", created_at=turn_at, conversation_id=conversation,
                    fidelity="partial", untrusted=untrusted_before[index],
                    request_blob=request, repaired_output_blob=reply, executed="repaired",
                ))
                last_reply = decision_id
                sequence += 1
            elif kind == "tool":
                call_id = str(getattr(message, "tool_call_id", "") or "")
                call = calls.get(call_id, {})
                name = str(call.get("name") or getattr(message, "name", "") or "tool")
                arguments, _ = redaction.redact_arguments(call.get("args"))
                arguments_sha = sink.blob(_canonical({
                    "format": TOOL_CALL_FORMAT, "version": 1, "name": name, "runtime_name": name,
                    "args": arguments,
                }))
                result_sha, _ = sink.json_blob(_message_dict(message))
                content = getattr(message, "content", "")
                handle = _SAVED_OUTPUT.search(content if isinstance(content, str) else str(content))
                records.append(Decision(
                    id=_derived_id("dech", thread_id, number, index), episode_id=episode_id,
                    thread_id=thread_id, sequence=sequence, kind="tool_call", created_at=turn_at,
                    conversation_id=conversation, fidelity="partial", untrusted=_is_untrusted(message),
                    status="error" if getattr(message, "status", "success") == "error" else "ok",
                    tool_name=name[:256], tool_call_id=call_id[:256] or None, arguments_blob=arguments_sha,
                    result_blob=result_sha, result_handle=handle.group(1) if handle else None,
                ))
                sequence += 1
        records.append(Episode(
            id=episode_id, thread_id=thread_id, source=source, started_at=turn_at,  # type: ignore[arg-type]
            conversation_id=conversation, channel=channel if source == "channel_chat" else None,
            status="completed", ended_at=turn_at, fidelity="partial", historical=True,
            follows_decision_id=previous_reply if _person_started(messages[start]) else None,
        ))
        previous_reply = last_reply or previous_reply
    return records


def _plan(store: LearningStore, source: PastChatSource, now: float) -> Job:
    """The conversations to read and how long each was when the import began."""

    since = now - WINDOW_DAYS * 86400.0
    planned: list[list[Any]] = []
    for thread_id, created_at in source.conversations(since):
        try:
            length = len(source.messages(thread_id))
            first, began = source.window_start(thread_id, since, float(created_at))
            if first >= length:
                continue  # nothing of it is from the last 30 days
            planned.append([thread_id, length, float(began), int(first)])
        except Exception:
            logger.debug("Learning could not size a past chat; it is left out")
    plan = _canonical({"format": PLAN_FORMAT, "version": 2, "window_start": since, "started_at": now,
                       "conversations": planned})
    sha = store.put_blob(plan)
    job = Job(id=JOB_ID, kind="past_chat_import", created_at=now, updated_at=now, state="running",
              backend="local", spec_blob=sha, steps_done=0, steps_total=len(planned))
    store.put(job)
    return job


def _planned(store: LearningStore, job: Job) -> list[tuple[str, int, float, int]]:
    """(conversation, length, when its part in the window began, first message in the window)."""

    data = store.read_blob(job.spec_blob or "")
    if data is None:
        return []
    try:
        plan = json.loads(data.decode("utf-8"))
        return [(str(item[0]), int(item[1]), float(item[2]), int(item[3]) if len(item) > 3 else 0)
                for item in plan.get("conversations") or []]
    except (ValueError, TypeError, IndexError, AttributeError):
        return []


def run_import(
    *,
    store: LearningStore | None = None,
    source: PastChatSource | None = None,
    lookup: scope.ThreadLookup | None = None,
    clock: Callable[[], float] = time.time,
    busy: Callable[[], bool] | None = None,
    wait: Callable[[float], Any] | None = None,
    should_stop: Callable[[], bool] | None = None,
    root: Path | None = None,
) -> ImportProgress | None:
    """Read past chats into Learning, from where the import last stopped. Returns its progress.

    ``busy`` says whether a chat is generating (the import waits), ``wait`` waits a while,
    ``should_stop`` ends it early (Learning turned off, the app quitting). A finished import is
    never run again; None when Learning or "past chats" is off.
    """

    settings = gate.read_settings(root)
    if not settings.enabled or settings.paused or not settings.past_chats:
        return None
    store = store or LearningStore(root, clock=clock)
    source = source or SavedChats()
    if busy is None:
        from row_bot.runtime.executions import generation_registry

        def busy() -> bool:
            return bool(generation_registry.active())

    wait = wait or time.sleep
    stopping = should_stop or (lambda: not gate.is_enabled(root))
    job = store.get("job", JOB_ID)
    if isinstance(job, Job) and job.state == "ready":
        return progress(store)
    if not isinstance(job, Job):
        job = _plan(store, source, float(clock()))
    planned = _planned(store, job)
    total = len(planned)
    try:
        for index in range(job.steps_done, total):
            if busy():
                job = replace(job, state="paused", updated_at=max(float(clock()), job.updated_at))
                store.put(job)
                while busy() and not stopping():
                    wait(_BUSY_WAIT_SECONDS)
            if stopping():
                job = replace(job, state="stopped", updated_at=max(float(clock()), job.updated_at))
                store.put(job)
                return progress(store)
            thread_id, length, began, first = planned[index]
            sink = _Sink()
            try:
                decision, facts = scope.resolve(thread_id, historical=True, lookup=lookup, store=store, root=root)
                if decision.reason in (scope.LEARNING_OFF, scope.LEARNING_PAUSED, scope.PAST_CHATS_OFF):
                    # Turned off or paused just now: stop here, and read this conversation when it is back.
                    job = replace(job, state="stopped", updated_at=max(float(clock()), job.updated_at))
                    store.put(job)
                    return progress(store)
                if decision.allowed:
                    messages = source.messages(thread_id)[:length]
                    sink.records = _conversation_records(thread_id, messages, facts=facts,
                                                         started_at=began, sink=sink, first_index=first)
            except LearningStoreError:
                raise
            except Exception as error:
                logger.warning("Learning skipped a past chat it could not read (%s)", type(error).__name__)
                sink = _Sink()
            job = replace(job, state="running", steps_done=index + 1,
                          updated_at=max(float(clock()), job.updated_at))
            if sink.records:
                store.put_blobs(list(sink.blobs.values()))
            # The conversation's records and the progress, in one transaction.
            store.put_many([*sink.records, job])
        job = replace(job, state="ready", spec_blob=None, updated_at=max(float(clock()), job.updated_at))
        store.put(job)
    except LearningStoreError as error:
        if error.code != "learning_off":
            logger.warning("Learning stopped reading past chats (%s)", error.code)
        return progress(store) if store.exists() else None
    return progress(store)


# ── The background starter ─────────────────────────────────────────────────

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()


def start_background() -> bool:
    """Run the import on a background thread unless it is running. True when started."""

    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()
        root = gate.data_dir()

        def stopping() -> bool:
            settings = gate.read_settings(root)
            return _stop.is_set() or not settings.enabled or settings.paused or not settings.past_chats

        def run() -> None:
            try:
                run_import(should_stop=stopping, wait=_stop.wait, root=root)
            except Exception as error:
                logger.warning("Learning stopped reading past chats (%s)", type(error).__name__)

        _thread = threading.Thread(target=run, name="learning-past-chats", daemon=True)
        _thread.start()
        return True


def stop_background(timeout: float = 5.0) -> None:
    """Stop the background import (app shutdown); it resumes from where it was next time."""

    _stop.set()
    thread = _thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout)


__all__ = [
    "CONVERSATION_FORMAT",
    "ImportProgress",
    "JOB_ID",
    "PLAN_FORMAT",
    "PastChatSource",
    "SavedChats",
    "WINDOW_DAYS",
    "checkpoint_window",
    "progress",
    "run_import",
    "start_background",
    "stop_background",
]
