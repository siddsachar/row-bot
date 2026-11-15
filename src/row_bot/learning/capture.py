"""Learning capture: what the seams hand over, queued, then written off the turn's thread.

**How a turn is captured.** Every seam checks ``row_bot.learning.is_enabled()``
first; this module is imported only after that.

- An entry point (``agent.stream_agent``, ``invoke_agent``, their resumes, Chat
  Only) calls :func:`begin_turn`. The :class:`Turn` it returns lives in the run's
  context; nothing is looked up yet.
- The model-call, tool-call and approval seams call :func:`model_call`,
  :func:`tool_call` and :func:`approval`. Each takes cheap references (the request
  list is copied, nothing is serialised) and enqueues them without blocking. When
  the queue is full the item is dropped and the episode is marked ``incomplete``.
- :func:`end_turn` closes the turn: completed, failed, stopped, cancelled, or
  ``waiting`` at an approval. A turn waiting at an approval carries on in the same
  episode when :func:`begin_turn` is called with ``resumed=True`` on its
  conversation; a new message there instead ends it as cancelled.
- A message the person sends while a turn works is read at its next step (5.3.1). The
  request that first carries it starts the turn's next episode, following the step before
  it (``follows_decision_id``), as the turn of its own it used to start did; the episode
  before it is completed there.
- Retry in place (:func:`retry`) and a Slack or Discord message's sender
  (:func:`note_channel_sender`) come from their own seams.

**The writer** is one daemon thread, started by the first item while Learning is
on. It leaves when idle, when Learning is turned off (dropping what is queued) and
on :func:`shutdown`. It never keeps a database handle between batches.

- Per turn, once, before anything of it is written, it decides scope (D27,
  ``scope.resolve``): denied means nothing of that turn is written. A chat that
  is or was incognito (N11) is denied; one that turns incognito while its turn
  runs is asked again with every later batch, and nothing more of that turn is
  written (what was written before stays, as memories saved before do). It finds the
  conversation a delegated Agent's thread belongs to (``conversation_id``), the
  parent episode, and the reply the person's message followed (the next-message
  marker, ``Episode.follows_decision_id``).
- It serialises and redacts (``redaction``) on its own thread. Each message is a
  content-addressed blob, and a request is the ordered list of its messages'
  hashes plus the tool schemas by hash (:data:`REQUEST_FORMAT`), so the prefix a
  turn's calls share is stored once.
- A picture in a message (5.3's page picture after a browser or computer action)
  is moved out before redaction (``pictures``): its original bytes are a blob of
  their own, named in the message and in the request (``pictures``), so redaction
  never reads picture data and no message blob holds base64. Past the size cap
  (``store.PICTURE_CAP_BYTES``) the oldest pictures are dropped.
- A batch is written with ``put_blobs`` then ``put_many`` (one transaction each).
  ``LearningStoreError("learning_off")`` drops the queue; records of a chat opted
  out meanwhile are skipped by the store.

Raw and repaired output stay apart (E3): the model's own response is
``raw_output_blob``; when the runtime changed it before it was kept (inline
reasoning moved out of the answer, F32), the kept version is
``repaired_output_blob`` and ``executed`` is ``"repaired"``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from row_bot import learning as gate
from row_bot.learning import pictures, redaction
from row_bot.learning.contracts import Decision, Episode, Record, Signal, new_id, valid_thread_id
from row_bot.learning.store import REQUEST_FORMAT, LearningStore, LearningStoreError

logger = logging.getLogger(__name__)

TOOL_CALL_FORMAT = "row_bot.learning.tool_call"
APPROVAL_FORMAT = "row_bot.learning.approval"

QUEUE_LIMIT = 2048
BATCH_LIMIT = 128
IDLE_SECONDS = 30.0
_MAX_PAUSED = 256
_MAX_SENDERS = 256
_MAX_LAST_TURNS = 1024
_MAX_MESSAGE_CACHE = 1024
_MAX_MESSAGE_CACHE_BYTES = 32 * 1024 * 1024
_MAX_DECISION_BY_MESSAGE = 4096

# agent._UNTRUSTED_RESULT_KEY: the stamp a tool or web result carries when it was stored (E9).
UNTRUSTED_KEY = "row_bot_untrusted"
_EXTERNAL_FENCE = "<EXTERNAL_CONTENT"
_SAVED_OUTPUT = re.compile(r"The full output is saved as (out_[0-9a-f]{16})")
_REVIEWER_APPROVED = "Approval: approved by the reviewer: "
_REVIEWER_ASKED = ". The reviewer asked you: "

_STATUSES = {"completed", "failed", "stopped", "cancelled", "waiting"}
_FIDELITY_RANK = {"exact": 0, "redacted": 1, "partial": 2}


def _canonical(value: Any) -> bytes:
    """The bytes a JSON value is stored as (the agent's tool fingerprint uses the same)."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _message_dict(message: Any) -> Any:
    try:
        from langchain_core.messages import BaseMessage, message_to_dict

        if isinstance(message, BaseMessage):
            return message_to_dict(message)
    except Exception:
        logger.debug("Learning stored a message as plain data")
    return message


def _is_untrusted(message: Any) -> bool:
    """A tool or web result stamped untrusted when it was stored, or fenced as external content."""

    extra = getattr(message, "additional_kwargs", None)
    if isinstance(extra, Mapping) and extra.get(UNTRUSTED_KEY):
        return True
    if str(getattr(message, "type", "") or "") == "tool":
        content = getattr(message, "content", "")
        return _EXTERNAL_FENCE in content if isinstance(content, str) else _EXTERNAL_FENCE in str(content)
    return False


def _persons_id(message: Any) -> str | None:
    """The id of a message the person sent, as the agent sends it: not Row-Bot's turn context (marked, or sent
    on its own without the mark, ``turn_context.provider_message``: known by its id), a note to the model, the
    page's picture or a compaction summary (they have no id), an event Row-Bot added or a delegated agent's
    task."""

    from row_bot.learning.corrections import is_row_bot_note
    from row_bot.turn_context import MESSAGE_ID_PREFIX, is_person_message

    message_id = str(getattr(message, "id", "") or "")
    if (not message_id or message_id.startswith(MESSAGE_ID_PREFIX) or not is_person_message(message)
            or is_row_bot_note(message)):
        return None
    extra = getattr(message, "additional_kwargs", None)
    extra = extra if isinstance(extra, Mapping) else {}
    ui = extra.get("row_bot_ui")
    if extra.get("row_bot_internal_event") or extra.get("platform_note") or (
            isinstance(ui, Mapping) and ui.get("thread_event")):
        return None
    return message_id


def _valid_id(value: Any) -> str | None:
    text = str(value or "")
    return text if text and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", text) else None


def _text(value: Any, limit: int = 256) -> str | None:
    text = "".join(ch for ch in str(value or "") if ord(ch) >= 32)
    return text[:limit] or None


# ── Turns ───────────────────────────────────────────────────────────────────


class Turn:
    """One episode in the making (the next one from a message read mid-turn). Created on the
    turn's thread; the writer fills in what needs a lookup (scope, conversation, parent,
    next-message marker)."""

    def __init__(
        self,
        *,
        thread_id: str,
        root: Path,
        started_at: float,
        generation_id: str = "",
        surface: str = "",
        agent_run_id: str = "",
        goal: bool = False,
        person_message: bool = False,
        sender_id: str | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.root = root
        self.started_at = float(started_at)
        self.generation_id = generation_id
        self.surface = surface
        self.agent_run_id = agent_run_id
        self.goal = goal
        self.person_message = person_message
        self.sender_id = sender_id
        self.episode_id = new_id("ep")
        self._sequence = itertools.count()
        self.closed = False
        self.incomplete = False
        self.approvals: set[tuple[str, str, str, str]] = set()
        # Filled in by the writer.
        self.resolved = False
        self.allowed = False
        self.conversation_id = thread_id
        self.source = "my_chat"
        self.channel: str | None = None
        self.parent_episode_id: str | None = None
        self.root_episode_id: str | None = None
        self.follows_decision_id: str | None = None
        self.model_ref: str | None = None
        self.status = "running"
        self.ended_at: float | None = None
        self.fidelity = "exact"
        self.written = False
        self.last_model_decision_id: str | None = None
        # The ids of the person's messages its requests carried (None before its first).
        self.heard: set[str] | None = None

    def next_sequence(self) -> int:
        return next(self._sequence)


# ── Queue items ─────────────────────────────────────────────────────────────


@dataclass(eq=False)
class _ModelCall:
    turn: Turn
    decision_id: str
    retry_of: str | None
    sequence: int
    created_at: float
    messages: tuple[Any, ...]
    tools: tuple[Any, ...] | None
    model_ref: str
    provider_id: str
    purpose: str
    raw: Any
    final: Any
    latency_ms: int | None
    ledger_call_id: str | None
    status: str
    error: str | None
    partial: bool = False


@dataclass(eq=False)
class _ToolCall:
    turn: Turn
    sequence: int
    created_at: float
    call: dict[str, Any]
    runtime_name: str
    result: Any
    status: str
    untrusted: bool


@dataclass(eq=False)
class _Approval:
    turn: Turn
    sequence: int
    created_at: float
    question: dict[str, Any]
    level: str | None
    decided_by: str
    outcome: str
    reason: str
    line: str
    tool_call_id: str | None


@dataclass(eq=False)
class _End:
    turn: Turn
    stopped_by_person: bool
    created_at: float


@dataclass(eq=False)
class _Retry:
    thread_id: str
    root: Path
    created_at: float
    replaced_message_ids: tuple[str, ...]


@dataclass(eq=False)
class _Flush:
    done: threading.Event = field(default_factory=threading.Event)


# ── Writing ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Serialised:
    """A message as stored: the object it came from, its blob, whether redaction changed it, and the
    pictures moved out of it (by hash)."""

    message: Any
    sha: str
    changed: bool
    data: bytes
    pictures: Mapping[str, bytes]

    @property
    def size(self) -> int:
        return len(self.data) + sum(len(item) for item in self.pictures.values())


class _Sink:
    """Blobs and records gathered for one batch."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.records: list[Record] = []
        # Messages serialised in this batch: remembered once the batch is written.
        self.staged: dict[int, _Serialised] = {}
        self.turns: dict[int, Turn] = {}
        self.pictures = False

    def blob(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        self.blobs.setdefault(sha, data)
        return sha

    def json_blob(self, value: Any, *, redact: bool = True) -> tuple[str, bool]:
        changed = False
        if redact:
            value, changed = redaction.redact_content(value)
        return self.blob(_canonical(value)), changed


class Capture:
    """The capture queue and its writer thread. One per process (the module's functions);
    tests make their own with an injected clock and store, and ``start_thread=False``
    to write on their own thread with :meth:`drain`."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        store_factory: Callable[[Path], LearningStore] | None = None,
        queue_limit: int = QUEUE_LIMIT,
        batch_limit: int = BATCH_LIMIT,
        idle_seconds: float = IDLE_SECONDS,
        start_thread: bool = True,
        lookup: Any = None,
    ) -> None:
        self._clock = clock
        self._store_factory = store_factory or (lambda root: LearningStore(root, clock=clock))
        # Where scope reads a conversation's facts (scope.ThreadLookup); the saved threads by default.
        self._lookup = lookup
        self._queue_limit = max(1, int(queue_limit))
        self._batch_limit = max(1, int(batch_limit))
        self._idle_seconds = float(idle_seconds)
        self._start_thread = start_thread
        self._cond = threading.Condition()
        self._items: deque[Any] = deque()
        self._data = 0
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._lock = threading.Lock()
        self._paused: OrderedDict[str, Turn] = OrderedDict()
        self._senders: OrderedDict[str, str] = OrderedDict()
        # Writer-side memory (only the writer touches these).
        self._last_turn: OrderedDict[str, Turn] = OrderedDict()
        self._decision_by_message: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._message_cache: OrderedDict[int, _Serialised] = OrderedDict()
        self._message_cache_bytes = 0
        self.dropped = 0

    # ── The turn's side: never blocks, never raises ──────────────────────

    def begin_turn(
        self,
        *,
        thread_id: str,
        generation_id: str = "",
        surface: str = "",
        agent_run_id: str = "",
        goal: bool = False,
        person_message: bool = False,
        resumed: bool = False,
        standalone: bool = False,
    ) -> Turn | None:
        """``standalone``: work beside the conversation's turns (an orchestration's final
        answer); it leaves a turn waiting at an approval as it is."""

        thread = str(thread_id or "")
        if not valid_thread_id(thread):
            return None
        root = gate.data_dir()
        with self._lock:
            paused = None if standalone else self._paused.pop(thread, None)
            # A Slack or Discord DM is one person's: its sender stands for the conversation's
            # later turns too (a goal step, an approval's resume).
            sender = self._senders.get(thread)
        if paused is not None:
            if resumed and paused.root == root and not paused.closed:
                return paused
            # A new message instead of the answer the turn waited for: it ends there.
            self._close(paused, "cancelled")
        return Turn(
            thread_id=thread,
            root=root,
            started_at=self._clock(),
            generation_id=str(generation_id or ""),
            surface=str(surface or ""),
            agent_run_id=str(agent_run_id or ""),
            goal=bool(goal),
            person_message=bool(person_message) and not resumed,
            sender_id=sender,
        )

    def end_turn(self, turn: Turn | None, status: str, *, stopped_by_person: bool = False) -> None:
        if turn is None or turn.closed:
            return
        status = status if status in _STATUSES else "failed"
        if status == "waiting":
            evicted: list[Turn] = []
            with self._lock:
                self._paused[turn.thread_id] = turn
                self._paused.move_to_end(turn.thread_id)
                while len(self._paused) > _MAX_PAUSED:
                    evicted.append(self._paused.popitem(last=False)[1])
            for old in evicted:
                self._close(old, "cancelled")
            return
        self._close(turn, status, stopped_by_person=stopped_by_person)

    def _close(self, turn: Turn, status: str, *, stopped_by_person: bool = False) -> None:
        turn.closed = True
        turn.status = status
        turn.ended_at = max(self._clock(), turn.started_at)
        self._put(_End(turn, stopped_by_person and status == "stopped", turn.ended_at), data=False)

    def model_call(
        self,
        turn: Turn | None,
        *,
        messages: Sequence[Any],
        response: Any = None,
        kept: Any = None,
        tools: Sequence[Any] | None = None,
        model_ref: str = "",
        provider_id: str = "",
        purpose: str = "agent",
        latency_ms: float | None = None,
        ledger_call_id: str | None = None,
        status: str = "ok",
        error: BaseException | None = None,
        retry_of: str | None = None,
        partial: bool = False,
    ) -> str | None:
        """One model call: the request exactly as sent, the model's response, and the
        response as the runtime kept it (``kept``, when it differs: a repair). Returns the
        decision's id (a retry of a refused call names it in ``retry_of``). ``partial``: only
        part of the call is known (the answer's text, say), so it is partial fidelity."""

        if turn is None or turn.closed:
            return None
        item = _ModelCall(
            turn=turn,
            decision_id=new_id("dec"),
            retry_of=_valid_id(retry_of),
            sequence=turn.next_sequence(),
            created_at=self._clock(),
            messages=tuple(messages),
            tools=tuple(tools) if tools is not None else None,
            model_ref=str(model_ref or ""),
            provider_id=str(provider_id or ""),
            purpose=str(purpose or "agent"),
            raw=response,
            final=kept,
            latency_ms=None if latency_ms is None else max(0, int(latency_ms)),
            ledger_call_id=_text(ledger_call_id),
            status=status,
            error=type(error).__name__ if error is not None else None,
            partial=bool(partial),
        )
        if not self._put(item):
            turn.incomplete = True
            return None
        return item.decision_id

    def tool_call(
        self,
        turn: Turn | None,
        *,
        call: Mapping[str, Any],
        runtime_name: str = "",
        result: Any = None,
        status: str = "ok",
        untrusted: bool = False,
    ) -> None:
        """One tool call: what the model asked for, the capped result (with its saved-output
        handle) and whether the result is tool or web content (untrusted)."""

        if turn is None or turn.closed:
            return
        item = _ToolCall(
            turn=turn,
            sequence=turn.next_sequence(),
            created_at=self._clock(),
            call=dict(call or {}),
            runtime_name=str(runtime_name or ""),
            result=result,
            status=status,
            untrusted=bool(untrusted),
        )
        if not self._put(item):
            turn.incomplete = True

    def approval(
        self,
        turn: Turn | None,
        *,
        payload: Mapping[str, Any],
        mode: str,
        response: Any,
        line: str,
        blocked: bool = False,
        tool_call_id: str | None = None,
    ) -> None:
        """One approval decision, as the gate made it (read only, E8): the level, who
        decided and the outcome. A decision replayed when a paused tool runs again is
        recorded once."""

        if turn is None or turn.closed:
            return
        question = {
            "tool": str(payload.get("tool") or payload.get("name") or ""),
            "label": str(payload.get("label") or ""),
            "description": str(payload.get("description") or payload.get("reason") or ""),
        }
        key = (str(tool_call_id or ""), question["tool"], question["label"], question["description"])
        if key in turn.approvals:
            return
        turn.approvals.add(key)
        level = {"approve": "ask", "auto": "auto", "full": "hands_off"}.get(str(mode or ""))
        text = str(line or "")
        reason = ""
        if blocked:
            decided_by, outcome = "policy", "denied"
        elif level == "hands_off":
            decided_by, outcome = "hands_off", "approved"
        elif text.startswith(_REVIEWER_APPROVED):
            decided_by, outcome = "reviewer", "approved"
            reason = text[len(_REVIEWER_APPROVED):]
        else:
            decided_by = "person"
            if response is True:
                outcome = "approved"
            elif str(response or "").strip().casefold() == "take over":
                outcome = "cancelled"
            else:
                outcome = "denied"
            if _REVIEWER_ASKED in text:
                reason = text.split(_REVIEWER_ASKED, 1)[1]
        item = _Approval(
            turn=turn,
            sequence=turn.next_sequence(),
            created_at=self._clock(),
            question=question,
            level=None if blocked else level,
            decided_by=decided_by,
            outcome=outcome,
            reason=reason,
            line=text,
            tool_call_id=_text(tool_call_id),
        )
        if not self._put(item):
            turn.incomplete = True

    def retry(self, thread_id: str, replaced_message_ids: Iterable[str] = ()) -> None:
        """The person pressed Retry: the last turn is run again in place."""

        thread = str(thread_id or "")
        if not valid_thread_id(thread):
            return
        self._put(_Retry(thread, gate.data_dir(), self._clock(),
                         tuple(str(item) for item in replaced_message_ids if item)))

    def note_channel_sender(self, thread_id: str, sender_id: str) -> None:
        """Who sent the next message in a Slack or Discord conversation (O3)."""

        thread, sender = str(thread_id or ""), str(sender_id or "").strip()
        if not valid_thread_id(thread) or not sender:
            return
        with self._lock:
            self._senders[thread] = sender
            self._senders.move_to_end(thread)
            while len(self._senders) > _MAX_SENDERS:
                self._senders.popitem(last=False)

    # ── The queue ────────────────────────────────────────────────────────

    def _put(self, item: Any, *, data: bool = True) -> bool:
        with self._cond:
            if data and self._data >= self._queue_limit:
                self.dropped += 1
                return False
            self._items.append(item)
            if data:
                self._data += 1
            self._cond.notify()
            if self._start_thread and self._thread is None and not self._stopping:
                self._thread = threading.Thread(target=self._run, name="learning-capture", daemon=True)
                self._thread.start()
        return True

    def _take(self) -> list[Any]:
        batch: list[Any] = []
        while self._items and len(batch) < self._batch_limit:
            item = self._items.popleft()
            if not isinstance(item, (_End, _Flush)):
                self._data -= 1
            batch.append(item)
        return batch

    def _run(self) -> None:
        try:
            while True:
                with self._cond:
                    if not self._items and not self._stopping:
                        self._cond.wait(self._idle_seconds)
                    if not self._items:
                        self._thread = None
                        return
                    batch = self._take()
                self._process(batch)
        except BaseException:
            # Whatever ended the writer, the next item starts a new one.
            with self._cond:
                self._thread = None
            raise

    def drain(self) -> None:
        """Write everything queued, on this thread (tests and a writer-less capture)."""

        while True:
            with self._cond:
                if not self._items:
                    return
                batch = self._take()
            self._process(batch)

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until everything queued so far is written. True when it was."""

        marker = _Flush()
        self._put(marker, data=False)
        if not self._start_thread:
            self.drain()
        return marker.done.wait(timeout)

    def pending(self) -> int:
        with self._cond:
            return self._data

    def drop_all(self) -> None:
        """Forget everything queued (Learning was turned off)."""

        with self._cond:
            items = list(self._items)
            self._items.clear()
            self._data = 0
        for item in items:
            if isinstance(item, _Flush):
                item.done.set()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Write what is queued (for at most ``timeout`` seconds) and stop the writer."""

        with self._cond:
            self._stopping = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        elif not self._start_thread:
            self.drain()
        self.drop_all()
        with self._lock:
            self._paused.clear()
            self._senders.clear()
        self._last_turn.clear()
        self._decision_by_message.clear()
        self._message_cache.clear()
        self._message_cache_bytes = 0
        with self._cond:
            self._stopping = False

    # ── The writer ───────────────────────────────────────────────────────

    def _process(self, batch: list[Any]) -> None:
        flushes = [item for item in batch if isinstance(item, _Flush)]
        work = [item for item in batch if not isinstance(item, _Flush)]
        try:
            by_root: OrderedDict[Path, list[Any]] = OrderedDict()
            for item in work:
                root = item.root if isinstance(item, _Retry) else item.turn.root
                by_root.setdefault(root, []).append(item)
            for root, items in by_root.items():
                if not gate.is_enabled(root):
                    # Turned off: nothing more is recorded, and what waits is dropped.
                    self.drop_all()
                    return
                self._write(root, items)
        except LearningStoreError as error:
            if error.code == "learning_off":
                self.drop_all()
            else:
                logger.warning("Learning could not save what it captured (%s)", error.code)
        except Exception as error:  # Capture never stops the app.
            logger.warning("Learning could not save what it captured (%s)", type(error).__name__)
        finally:
            for marker in flushes:
                marker.done.set()

    def _write(self, root: Path, items: list[Any]) -> None:
        store = self._store_factory(root)
        sink = _Sink()
        incognito: dict[str, bool] = {}  # asked once per conversation in a batch
        for item in items:
            try:
                if isinstance(item, _Retry):
                    self._retry_records(store, item, sink)
                    continue
                turn = item.turn
                asked = turn.resolved
                if not self._resolve(turn, store):
                    continue
                if asked and self._turned_incognito(turn, incognito):
                    continue
                if isinstance(item, _End):
                    # A turn that recorded nothing leaves no empty episode (unless it was stopped).
                    if turn.written or item.stopped_by_person or id(turn) in sink.turns:
                        self._end_records(item, sink)
                        sink.turns[id(turn)] = turn
                    continue
                if isinstance(item, _ModelCall):
                    self._model_call_records(item, sink)
                elif isinstance(item, _ToolCall):
                    self._tool_call_records(item, sink)
                elif isinstance(item, _Approval):
                    self._approval_records(item, sink)
                sink.turns[id(turn)] = turn
            except LearningStoreError:
                raise
            except Exception as error:
                logger.warning("Learning skipped a captured %s (%s)", type(item).__name__.strip("_"),
                               type(error).__name__)
        # Each turn's episode, as it stands now (status, incomplete, fidelity).
        sink.records.extend(self._episode(turn) for turn in sink.turns.values())
        if not sink.records:
            return
        store.put_blobs(list(sink.blobs.values()))
        if store.put_many(sink.records) < len(sink.records):
            # A chat switched off meanwhile: its records were skipped, so its blobs go too.
            store.gc_blobs(sink.blobs)
        for turn in sink.turns.values():
            turn.written = True
        if sink.pictures:
            store.keep_pictures_within()
        if any(isinstance(item, _End) for item in items):
            # A finished turn's messages won't be sent again: the cache stays small.
            self._message_cache.clear()
            self._message_cache_bytes = 0
        for key, value in sink.staged.items():
            self._message_cache[key] = value
            self._message_cache_bytes += value.size
        while self._message_cache and (len(self._message_cache) > _MAX_MESSAGE_CACHE
                                       or self._message_cache_bytes > _MAX_MESSAGE_CACHE_BYTES):
            self._message_cache_bytes -= self._message_cache.popitem(last=False)[1].size

    def _resolve(self, turn: Turn, store: LearningStore) -> bool:
        """Scope (D27) and where the turn belongs, once per turn, before anything is written."""

        if turn.resolved:
            return turn.allowed
        turn.resolved = True
        from row_bot.learning import scope

        decision, facts = scope.resolve(
            turn.thread_id, goal=turn.goal, sender_id=turn.sender_id, store=store, root=turn.root,
            lookup=self._lookup,
        )
        if not decision.allowed:
            return False
        turn.allowed = True
        root_thread = facts.root_thread_id or turn.thread_id
        turn.conversation_id = root_thread if valid_thread_id(root_thread) else turn.thread_id
        if facts.conversation == "workflow":
            turn.source = "workflow"
        elif facts.goal:
            turn.source = "goal"
        elif facts.conversation == "channel":
            turn.source, turn.channel = "channel_chat", facts.channel
        else:
            turn.source = "my_chat"
        if turn.conversation_id != turn.thread_id:
            parent = self._last_turn.get(self._parent_thread(turn) or turn.conversation_id)
            if parent is not None:
                turn.parent_episode_id = parent.episode_id
                turn.root_episode_id = parent.root_episode_id or parent.episode_id
        if turn.person_message:
            turn.follows_decision_id = self._last_reply(turn.thread_id, store)
        self._last_turn[turn.thread_id] = turn
        self._last_turn.move_to_end(turn.thread_id)
        while len(self._last_turn) > _MAX_LAST_TURNS:
            self._last_turn.popitem(last=False)
        return True

    def _turned_incognito(self, turn: Turn, asked: dict[str, bool]) -> bool:
        """N11: the turn's chat turned incognito after its scope was decided. Nothing more of the turn is
        written, including what is queued now; what was written before stays."""

        from row_bot.learning import scope

        for thread in dict.fromkeys((turn.thread_id, turn.conversation_id)):
            if thread not in asked:
                asked[thread] = scope.is_incognito([thread], lookup=self._lookup)
            if asked[thread]:
                turn.allowed = False
                return True
        return False

    def _parent_thread(self, turn: Turn) -> str:
        """The conversation a delegated Agent's thread was started from (its Agent run's parent)."""

        try:
            from row_bot.learning import scope

            lookup = self._lookup or scope.SavedThreads()
            return str(lookup.thread(turn.thread_id).parent_thread_id or "")
        except Exception:
            return ""

    def _last_reply(self, thread_id: str, store: LearningStore) -> str | None:
        previous = self._last_turn.get(thread_id)
        if previous is not None:
            return previous.last_model_decision_id
        found = store.find("decision", thread_id=thread_id, subkind="model_call", newest_first=True, limit=1)
        return found[0].id if found else None

    def _note_fidelity(self, turn: Turn, fidelity: str) -> None:
        if _FIDELITY_RANK[fidelity] > _FIDELITY_RANK[turn.fidelity]:
            turn.fidelity = fidelity

    def _message_blob(self, message: Any, sink: _Sink) -> _Serialised:
        """A message's blob. A turn's later calls send the same message objects again:
        each is serialised and redacted once, and its bytes (and its pictures') are handed to
        the store again (which writes nothing it already has), so a purge in between can't
        leave a record pointing at a freed blob. Pictures leave the message before redaction."""

        key = id(message)
        known = next((found for found in (self._message_cache.get(key), sink.staged.get(key))
                      if found is not None and found.message is message), None)
        if known is None:
            value, moved = pictures.split(_message_dict(message))
            value, changed = redaction.redact_content(value)
            data = _canonical(value)
            known = _Serialised(message, hashlib.sha256(data).hexdigest(), changed, data, moved)
            sink.staged[key] = known
        sink.blobs.setdefault(known.sha, known.data)
        for sha, data in known.pictures.items():
            sink.blobs.setdefault(sha, data)
            sink.pictures = True
        return known

    def _model_call_records(self, item: _ModelCall, sink: _Sink) -> None:
        turn = item.turn
        if self._read_mid_turn(turn, item.messages):
            # 5.3.1: the person's message read at this step starts the turn's next episode, after the step
            # before it, as the turn of its own it used to start did (the episode before it is complete).
            sink.records.append(self._episode(turn, ended_at=item.created_at))
            turn.episode_id, turn.started_at = new_id("ep"), item.created_at
            turn.follows_decision_id, turn.fidelity = turn.last_model_decision_id, "exact"
        redacted = False
        hashes = []
        named: set[str] = set()
        for message in item.messages:
            stored = self._message_blob(message, sink)
            hashes.append(stored.sha)
            named.update(stored.pictures)
            redacted = redacted or stored.changed
        tools_sha = None
        if item.tools is not None:
            # The turn's calls share one tool list object: it is serialised once, like a message.
            stored = self._message_blob(item.tools, sink)
            tools_sha = stored.sha
            redacted = redacted or stored.changed
        model_ref = self._model_ref(item.model_ref, item.provider_id)
        manifest: dict[str, Any] = {
            "format": REQUEST_FORMAT,
            "version": 1,
            "purpose": item.purpose,
            "messages": hashes,
            "tools": tools_sha,
            "model_ref": model_ref,
            "provider": item.provider_id or None,
            "latency_ms": item.latency_ms,
            "error": item.error,
            "reconstructed": False,
        }
        if named:
            # The pictures its messages name: the store keeps them with the chat (and under the size cap).
            manifest["pictures"] = sorted(named)
        request_sha = sink.blob(_canonical(manifest))
        raw_sha = kept_sha = None
        if item.raw is not None:
            raw_sha, changed = sink.json_blob(_message_dict(item.raw))
            redacted = redacted or changed
        if item.final is not None and item.final is not item.raw:
            kept_sha, changed = sink.json_blob(_message_dict(item.final))
            redacted = redacted or changed
            if kept_sha == raw_sha:
                kept_sha = None
        fidelity = "partial" if item.partial else ("redacted" if redacted else "exact")
        decision = Decision(
            id=item.decision_id,
            episode_id=turn.episode_id,
            thread_id=turn.thread_id,
            sequence=item.sequence,
            kind="model_call",
            created_at=item.created_at,
            conversation_id=turn.conversation_id,
            fidelity=fidelity,
            untrusted=any(_is_untrusted(message) for message in item.messages),
            status=item.status,
            model_ref=model_ref,
            tool_schema_hash=tools_sha,
            request_blob=request_sha,
            raw_output_blob=raw_sha,
            repaired_output_blob=kept_sha,
            executed="repaired" if kept_sha else ("raw" if raw_sha else "none"),
            retry_of=item.retry_of,
            ledger_call_id=item.ledger_call_id,
        )
        sink.records.append(decision)
        self._note_fidelity(turn, fidelity)
        if model_ref and turn.model_ref is None:
            turn.model_ref = model_ref
        if item.status == "ok":
            turn.last_model_decision_id = decision.id
        for output in (item.final, item.raw):
            message_id = str(getattr(output, "id", "") or "")
            if message_id:
                self._decision_by_message[message_id] = (turn.episode_id, decision.id)
                while len(self._decision_by_message) > _MAX_DECISION_BY_MESSAGE:
                    self._decision_by_message.popitem(last=False)

    @staticmethod
    def _read_mid_turn(turn: Turn, messages: Sequence[Any]) -> bool:
        """Whether this request carries a message the person sent while the turn worked (5.3.1, marked
        ``mid_turn``): one of theirs that no earlier request of the turn carried, after a step of its own.
        Found by where it is, not by its marker, so a copy that lost the marker is found too."""

        heard = {key for key in map(_persons_id, messages) if key}
        if turn.heard is None:
            turn.heard = heard  # the turn's first request: what it started with
            return False
        new = bool(heard - turn.heard)
        turn.heard |= heard
        return new and turn.last_model_decision_id is not None

    @staticmethod
    def _model_ref(model_ref: str, provider_id: str) -> str | None:
        ref = str(model_ref or "")
        if ref and not ref.startswith("model:") and provider_id and not ref.startswith(f"{provider_id}:"):
            ref = f"model:{provider_id}:{ref}"
        elif ref and not ref.startswith("model:") and provider_id:
            ref = f"model:{ref}"
        return _text(ref, 512)

    def _tool_call_records(self, item: _ToolCall, sink: _Sink) -> None:
        turn = item.turn
        name = str(item.call.get("name") or "")
        arguments, args_changed = redaction.redact_arguments(item.call.get("args"))
        args_sha = sink.blob(_canonical({
            "format": TOOL_CALL_FORMAT,
            "version": 1,
            "name": name,
            "runtime_name": item.runtime_name or name,
            "args": arguments,
        }))
        result_sha = handle = None
        result_changed = False
        if item.result is not None:
            result_sha, result_changed = sink.json_blob(_message_dict(item.result))
            content = getattr(item.result, "content", "")
            match = _SAVED_OUTPUT.search(content if isinstance(content, str) else str(content))
            handle = match.group(1) if match else None
        redacted = args_changed or result_changed
        fidelity = "redacted" if redacted else "exact"
        sink.records.append(Decision(
            id=new_id("dec"),
            episode_id=turn.episode_id,
            thread_id=turn.thread_id,
            sequence=item.sequence,
            kind="tool_call",
            created_at=item.created_at,
            conversation_id=turn.conversation_id,
            fidelity=fidelity,
            untrusted=item.untrusted or (item.result is not None and _is_untrusted(item.result)),
            status=item.status,
            tool_name=_text(item.runtime_name or name) or "tool",
            tool_call_id=_text(item.call.get("id")),
            arguments_blob=args_sha,
            result_blob=result_sha,
            result_handle=handle,
        ))
        self._note_fidelity(turn, fidelity)

    def _approval_records(self, item: _Approval, sink: _Sink) -> None:
        turn = item.turn
        question_sha, q_changed = sink.json_blob({"format": APPROVAL_FORMAT, "version": 1, **item.question})
        outcome_sha, o_changed = sink.json_blob({
            "decided_by": item.decided_by,
            "outcome": item.outcome,
            "reviewer_reason": item.reason or None,
            "line": item.line or None,
        })
        fidelity = "redacted" if (q_changed or o_changed) else "exact"
        status = {"approved": "ok", "denied": "denied", "cancelled": "cancelled"}.get(item.outcome, "ok")
        decision = Decision(
            id=new_id("dec"),
            episode_id=turn.episode_id,
            thread_id=turn.thread_id,
            sequence=item.sequence,
            kind="approval",
            created_at=item.created_at,
            conversation_id=turn.conversation_id,
            fidelity=fidelity,
            status=status,
            tool_name=_text(item.question.get("tool")),
            tool_call_id=item.tool_call_id,
            arguments_blob=question_sha,
            result_blob=outcome_sha,
            approval_level=item.level,
            decided_by=item.decided_by,
            approval_outcome=item.outcome,
        )
        sink.records.append(decision)
        self._note_fidelity(turn, fidelity)
        if item.decided_by == "person" and item.outcome == "denied":
            sink.records.append(Signal(
                id=new_id("sig"),
                kind="approval_denied",
                thread_id=turn.thread_id,
                created_at=item.created_at,
                conversation_id=turn.conversation_id,
                episode_id=turn.episode_id,
                decision_id=decision.id,
            ))

    def _end_records(self, item: _End, sink: _Sink) -> None:
        turn = item.turn
        if item.stopped_by_person:
            sink.records.append(Signal(
                id=new_id("sig"),
                kind="stop",
                thread_id=turn.thread_id,
                created_at=item.created_at,
                conversation_id=turn.conversation_id,
                episode_id=turn.episode_id,
                decision_id=turn.last_model_decision_id,
            ))

    def _retry_records(self, store: LearningStore, item: _Retry, sink: _Sink) -> None:
        from row_bot.learning import scope

        decision, facts = scope.resolve(item.thread_id, store=store, root=item.root, lookup=self._lookup)
        if not decision.allowed:
            return
        conversation = facts.root_thread_id or item.thread_id
        episode_id = decision_id = None
        for message_id in reversed(item.replaced_message_ids):
            found = self._decision_by_message.get(message_id)
            if found is not None:
                episode_id, decision_id = found
                break
        if episode_id is None:
            previous = self._last_turn.get(item.thread_id)
            if previous is not None:
                episode_id, decision_id = previous.episode_id, previous.last_model_decision_id
        if episode_id is None:
            found_decisions = store.find("decision", thread_id=item.thread_id, subkind="model_call",
                                         newest_first=True, limit=1)
            if found_decisions:
                episode_id = getattr(found_decisions[0], "episode_id", None)
                decision_id = found_decisions[0].id
        sink.records.append(Signal(
            id=new_id("sig"),
            kind="retry",
            thread_id=item.thread_id,
            created_at=item.created_at,
            conversation_id=conversation if valid_thread_id(conversation) else item.thread_id,
            episode_id=episode_id,
            decision_id=decision_id,
        ))

    def _episode(self, turn: Turn, *, ended_at: float | None = None) -> Episode:
        """The turn's episode as it stands, or (``ended_at``) completed there, before a message read mid-turn."""

        if ended_at is not None:
            status, ended = "completed", ended_at
        else:
            status = "running" if turn.status in {"running", "waiting"} else turn.status
            ended = turn.ended_at if turn.closed else None
        return Episode(
            id=turn.episode_id,
            thread_id=turn.thread_id,
            source=turn.source,  # type: ignore[arg-type]
            started_at=turn.started_at,
            conversation_id=turn.conversation_id,
            channel=_text(turn.channel, 64) if turn.source == "channel_chat" else None,
            parent_episode_id=turn.parent_episode_id,
            root_episode_id=turn.root_episode_id,
            agent_run_id=_valid_id(turn.agent_run_id),
            model_ref=turn.model_ref,
            status=status,  # type: ignore[arg-type]
            ended_at=ended,
            fidelity=turn.fidelity,  # type: ignore[arg-type]
            incomplete=turn.incomplete,
            follows_decision_id=turn.follows_decision_id,
        )


# ── Reading a captured request back (replay at its captured prefix) ─────────


def _json_blob(store: LearningStore, sha: Any) -> Any:
    data = store.read_blob(sha) if isinstance(sha, str) else None
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def read_request(store: LearningStore, decision: Decision) -> dict[str, Any] | None:
    """A captured model call's request: ``messages`` (LangChain message dicts, in order, for
    ``messages_from_dict``, each picture put back as it was sent), ``tools`` (the schemas, or None),
    ``model_ref``, ``purpose``, ``latency_ms``, ``error``, ``reconstructed`` (a past chat's
    conversation as stored, not the exact request) and ``pictures_dropped`` (pictures dropped under
    the size cap: they stay references, so the request is no longer exact). None when a blob it needs
    is gone (purged) or damaged."""

    manifest = _json_blob(store, decision.request_blob)
    if not isinstance(manifest, dict) or manifest.get("format") != REQUEST_FORMAT:
        return None
    if manifest.get("messages_of"):
        conversation = _json_blob(store, manifest["messages_of"])
        if not isinstance(conversation, dict):
            return None
        hashes = list(conversation.get("messages") or [])[: int(manifest.get("prefix") or 0)]
    else:
        hashes = list(manifest.get("messages") or [])
    messages = []
    dropped = 0
    for sha in hashes:
        value = _json_blob(store, sha)
        if value is None:
            return None
        if manifest.get("pictures"):
            try:
                value, lost = pictures.join(value, store.read_blob, store.picture_dropped)
            except pictures.Missing:
                return None
            dropped += lost
        messages.append(value)
    tools = _json_blob(store, manifest["tools"]) if manifest.get("tools") else None
    return {
        "messages": messages,
        "tools": tools,
        "model_ref": manifest.get("model_ref"),
        "purpose": manifest.get("purpose"),
        "latency_ms": manifest.get("latency_ms"),
        "error": manifest.get("error"),
        "reconstructed": bool(manifest.get("reconstructed")),
        "pictures_dropped": dropped,
    }


# ── The process's capture ───────────────────────────────────────────────────

_default = Capture()


def _capture() -> Capture:
    return _default


def begin_turn(**kwargs: Any) -> Turn | None:
    """Open a turn's episode (see :meth:`Capture.begin_turn`)."""

    return _capture().begin_turn(**kwargs)


def end_turn(turn: Turn | None, status: str, *, stopped_by_person: bool = False) -> None:
    _capture().end_turn(turn, status, stopped_by_person=stopped_by_person)


def model_call(turn: Turn | None, **kwargs: Any) -> str | None:
    return _capture().model_call(turn, **kwargs)


def tool_call(turn: Turn | None, **kwargs: Any) -> None:
    _capture().tool_call(turn, **kwargs)


def approval(turn: Turn | None, **kwargs: Any) -> None:
    _capture().approval(turn, **kwargs)


def retry(thread_id: str, replaced_message_ids: Iterable[str] = ()) -> None:
    _capture().retry(thread_id, replaced_message_ids)


def note_channel_sender(thread_id: str, sender_id: str) -> None:
    _capture().note_channel_sender(thread_id, sender_id)


def flush(timeout: float = 10.0) -> bool:
    """Wait until everything captured so far is written (tests; shutdown)."""

    return _capture().flush(timeout)


def learning_turned_off() -> None:
    """Learning was turned off or paused: drop what waits to be written."""

    _capture().drop_all()


def decision_for_message(message_id: str) -> tuple[str, str] | None:
    """(episode id, model-call decision id) of a reply this process captured, if it remembers it."""

    found = _capture()._decision_by_message.get(str(message_id or ""))  # noqa: SLF001 - its own map
    return (str(found[0]), str(found[1])) if found else None


def shutdown(timeout: float = 5.0) -> None:
    """App shutdown: write what is queued (bounded) and stop the writer."""

    _capture().shutdown(timeout)


__all__ = [
    "APPROVAL_FORMAT",
    "BATCH_LIMIT",
    "Capture",
    "QUEUE_LIMIT",
    "REQUEST_FORMAT",
    "TOOL_CALL_FORMAT",
    "Turn",
    "UNTRUSTED_KEY",
    "approval",
    "begin_turn",
    "decision_for_message",
    "end_turn",
    "flush",
    "learning_turned_off",
    "model_call",
    "note_channel_sender",
    "read_request",
    "retry",
    "shutdown",
    "tool_call",
]
