"""Local ledger of model calls: token counts per call, never prompt or reply text.

One row per finished model call says when it ran, which conversation it served
(if any), why it ran (its purpose), which provider and model answered, and the
counts the provider reported: input (including cache reads and writes), cache
read, cache write, output, reasoning, latency, time to first token and, for
OpenRouter only, the cost it returned. Nothing here leaves the machine.

The ledger lives in ``usage.db`` in the data folder. Rows older than 90 days
are pruned during idle maintenance, the table is capped in size, and a
conversation's rows go when the conversation is deleted. Writes never raise:
a failed write is logged and the model call carries on.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from row_bot.data_paths import get_usage_db_path
from row_bot.prompt_cache import ModelUsage, normalize_model_usage

logger = logging.getLogger(__name__)

PURPOSES: tuple[str, ...] = (
    "main",
    "chat_only",
    "compaction",
    "title",
    "extraction",
    "dream",
    "document",
    "verifier",
    "condition",
    "contradiction",
    "approval_review",
    "child",
    "synthesis",
    "finalization",
    "other",
)
RETENTION_DAYS = 90
MAX_ROWS = 250_000

# Run-config metadata keys a caller sets (through ``with_usage_purpose``) to
# say why a model call runs and which conversation it serves.
PURPOSE_KEY = "row_bot_usage_purpose"
THREAD_KEY = "row_bot_usage_thread_id"
GENERATION_KEY = "row_bot_usage_generation_id"

_MAX_TEXT = 256
_MAX_OPEN_RUNS = 512

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS model_usage ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "created_at REAL NOT NULL, "
    "thread_id TEXT NOT NULL DEFAULT '', "
    "generation_id TEXT NOT NULL DEFAULT '', "
    "call_id TEXT NOT NULL DEFAULT '', "
    "iteration INTEGER, "
    "purpose TEXT NOT NULL DEFAULT 'other', "
    "provider TEXT NOT NULL DEFAULT '', "
    "model TEXT NOT NULL DEFAULT '', "
    "input_tokens INTEGER, "
    "cache_read_tokens INTEGER, "
    "cache_write_tokens INTEGER, "
    "cache_write_1h_tokens INTEGER, "
    "output_tokens INTEGER, "
    "reasoning_tokens INTEGER, "
    "latency_ms INTEGER, "
    "ttft_ms INTEGER, "
    "cost REAL)"
)
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_model_usage_thread ON model_usage(thread_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_model_usage_created ON model_usage(created_at)",
)

_schema_lock = threading.Lock()
_schema_ready: set[str] = set()


@dataclass(frozen=True)
class UsageRecord:
    """One model call. Counts and identifiers only, never content."""

    purpose: str
    provider: str
    model: str
    usage: ModelUsage
    thread_id: str = ""
    generation_id: str = ""
    call_id: str = ""
    iteration: int | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    created_at: float | None = None


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return "".join(character for character in text if ord(character) >= 32)[:_MAX_TEXT]


def _clean_purpose(value: Any) -> str:
    purpose = str(value or "").strip().lower()
    return purpose if purpose in PURPOSES else "other"


def _non_negative(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=5)
    key = str(path)
    if key not in _schema_ready:
        with _schema_lock:
            if key not in _schema_ready:
                try:
                    connection.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError:
                    logger.debug("Usage ledger could not switch to WAL", exc_info=True)
                connection.execute(_SCHEMA)
                for statement in _INDEXES:
                    connection.execute(statement)
                connection.commit()
                _schema_ready.add(key)
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _existing_ledger() -> Path | None:
    path = get_usage_db_path(create_parent=False)
    return path if path.is_file() else None


def record_model_usage(record: UsageRecord) -> bool:
    """Append one call to the ledger. Never raises; returns whether it was saved."""
    try:
        usage = record.usage
        row = (
            float(record.created_at if record.created_at is not None else time.time()),
            _clean_text(record.thread_id),
            _clean_text(record.generation_id),
            _clean_text(record.call_id),
            _non_negative(record.iteration),
            _clean_purpose(record.purpose),
            _clean_text(record.provider),
            _clean_text(record.model),
            _non_negative(usage.input_tokens),
            _non_negative(usage.cache_read_tokens),
            _non_negative(usage.cache_write_tokens),
            _non_negative(usage.cache_write_1h_tokens),
            _non_negative(usage.output_tokens),
            _non_negative(usage.reasoning_tokens),
            _non_negative(record.latency_ms),
            _non_negative(record.ttft_ms),
            usage.cost if usage.cost is not None and usage.cost >= 0 else None,
        )
        with closing(_connect(get_usage_db_path())) as connection, connection:
            connection.execute(
                "INSERT INTO model_usage (created_at, thread_id, generation_id, call_id, iteration, "
                "purpose, provider, model, input_tokens, cache_read_tokens, cache_write_tokens, "
                "cache_write_1h_tokens, output_tokens, reasoning_tokens, latency_ms, ttft_ms, cost) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        return True
    except Exception:
        logger.debug("Usage ledger write skipped", exc_info=True)
        # A replaced or removed file gets its table back on the next write.
        _schema_ready.discard(str(get_usage_db_path(create_parent=False)))
        return False


def delete_thread_usage(thread_id: str) -> int:
    """Remove a deleted conversation's rows. Returns the number removed."""
    clean = _clean_text(thread_id)
    path = _existing_ledger()
    if not clean or path is None:
        return 0
    with closing(_connect(path)) as connection, connection:
        return int(connection.execute("DELETE FROM model_usage WHERE thread_id = ?", (clean,)).rowcount or 0)


def prune_usage(
    *,
    now: float | None = None,
    retention_days: int = RETENTION_DAYS,
    max_rows: int = MAX_ROWS,
) -> int:
    """Drop rows past the retention window, then the oldest beyond the cap."""
    path = _existing_ledger()
    if path is None:
        return 0
    cutoff = float(now if now is not None else time.time()) - retention_days * 86_400
    with closing(_connect(path)) as connection, connection:
        removed = int(connection.execute(
            "DELETE FROM model_usage WHERE created_at < ?", (cutoff,)
        ).rowcount or 0)
        removed += int(connection.execute(
            "DELETE FROM model_usage WHERE id <= ("
            "SELECT id FROM model_usage ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (max(0, int(max_rows)),),
        ).rowcount or 0)
    return removed


def _cache_percent(cache_read: Any, cache_basis: Any) -> int | None:
    read = _non_negative(cache_read)
    basis = _non_negative(cache_basis)
    if read is None or not basis:
        return None
    return max(0, min(100, round(read * 100 / basis)))


_TOTALS_SELECT = (
    "SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), "
    "SUM(cache_read_tokens), SUM(CASE WHEN cache_read_tokens IS NOT NULL THEN input_tokens END), "
    "SUM(cost), SUM(CASE WHEN input_tokens IS NULL AND output_tokens IS NULL THEN 1 ELSE 0 END) "
    "FROM model_usage"
)


def _totals(row: Any) -> dict[str, Any]:
    calls, input_tokens, output_tokens, cache_read, cache_basis, cost, unreported = row
    return {
        "calls": int(calls or 0),
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cache_read_tokens": None if cache_read is None else int(cache_read),
        "cache_read_percent": _cache_percent(cache_read, cache_basis),
        "cost": None if cost is None else float(cost),
        "unreported_calls": int(unreported or 0),
    }


def conversation_usage(thread_id: str) -> dict[str, Any] | None:
    """Totals for one conversation, or ``None`` before its first recorded call."""
    clean = _clean_text(thread_id)
    path = _existing_ledger()
    if not clean or path is None:
        return None
    try:
        with closing(_connect(path)) as connection:
            row = connection.execute(_TOTALS_SELECT + " WHERE thread_id = ?", (clean,)).fetchone()
    except sqlite3.Error:
        logger.debug("Usage ledger read failed", exc_info=True)
        return None
    totals = _totals(row)
    return totals if totals["calls"] else None


def usage_summary(*, since: float | None = None, thread_id: str | None = None) -> dict[str, Any]:
    """Totals plus breakdowns by model and by purpose for a window or conversation."""
    clauses: list[str] = []
    params: list[Any] = []
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(float(since))
    if thread_id is not None:
        clauses.append("thread_id = ?")
        params.append(_clean_text(thread_id))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    empty = _totals((0, 0, 0, None, None, None, 0))
    path = _existing_ledger()
    if path is None:
        return {**empty, "by_model": [], "by_purpose": []}
    with closing(_connect(path)) as connection:
        totals = _totals(connection.execute(_TOTALS_SELECT + where, params).fetchone())
        by_model = [
            {"provider": str(row[0] or ""), "model": str(row[1] or ""), **_totals(row[2:])}
            for row in connection.execute(
                "SELECT provider, model, " + _TOTALS_SELECT.removeprefix("SELECT ") + where
                + " GROUP BY provider, model ORDER BY COALESCE(SUM(input_tokens), 0) "
                "+ COALESCE(SUM(output_tokens), 0) DESC LIMIT 12",
                params,
            ).fetchall()
        ]
        by_purpose = [
            {"purpose": str(row[0] or "other"), **_totals(row[1:])}
            for row in connection.execute(
                "SELECT purpose, " + _TOTALS_SELECT.removeprefix("SELECT ") + where
                + " GROUP BY purpose ORDER BY COALESCE(SUM(input_tokens), 0) "
                "+ COALESCE(SUM(output_tokens), 0) DESC",
                params,
            ).fetchall()
        ]
    return {**totals, "by_model": by_model, "by_purpose": by_purpose}


# ── Attribution ─────────────────────────────────────────────────────────────


def with_usage_purpose(
    runnable: Any,
    purpose: str,
    *,
    thread_id: str = "",
    generation_id: str = "",
) -> Any:
    """Tag a model runnable's calls with a purpose and conversation.

    Returns ``runnable.with_config(...)``; anything that is not a LangChain
    runnable (a test double, say) is returned unchanged.
    """
    try:
        from langchain_core.runnables import Runnable

        if not isinstance(runnable, Runnable):
            return runnable
        metadata = {PURPOSE_KEY: _clean_purpose(purpose)}
        if thread_id:
            metadata[THREAD_KEY] = _clean_text(thread_id)
        if generation_id:
            metadata[GENERATION_KEY] = _clean_text(generation_id)
        return runnable.with_config(metadata=metadata)
    except Exception:
        logger.debug("Usage purpose tag skipped", exc_info=True)
        return runnable


def _agent_runtime(thread_id: str) -> Mapping[str, Any]:
    """The running turn's facts, only when they describe this same conversation."""
    agent = sys.modules.get("row_bot.agent")
    getter = getattr(agent, "get_active_runtime_context", None)
    if not callable(getter) or not thread_id:
        return {}
    try:
        runtime = getter()
    except Exception:
        return {}
    if not isinstance(runtime, Mapping) or str(runtime.get("thread_id") or "") != thread_id:
        return {}
    return runtime


@dataclass
class _CallIdentity:
    purpose: str
    thread_id: str
    generation_id: str
    iteration: int | None


def _call_identity(metadata: Mapping[str, Any] | None) -> _CallIdentity:
    values = metadata if isinstance(metadata, Mapping) else {}
    thread_id = _clean_text(values.get(THREAD_KEY) or values.get("thread_id"))
    generation_id = _clean_text(values.get(GENERATION_KEY))
    in_graph = "langgraph_node" in values
    surface = ""
    if in_graph:
        runtime = _agent_runtime(thread_id)
        generation_id = generation_id or _clean_text(runtime.get("generation_id"))
        surface = str(runtime.get("runtime_surface") or "")
    purpose = values.get(PURPOSE_KEY)
    if not purpose:
        # The graph's model node is the turn itself; a model called from
        # inside a tool or another node is some other, untagged side call.
        if values.get("langgraph_node") == "agent":
            purpose = "child" if surface == "agent_child" else "main"
        else:
            purpose = "other"
    iteration = _non_negative(values.get("langgraph_step")) if in_graph else None
    return _CallIdentity(_clean_purpose(purpose), thread_id, generation_id, iteration)


@dataclass
class _OpenRun:
    identity: _CallIdentity
    started: float
    created_at: float
    first_output: float | None = None


def _has_output(token: Any, chunk: Any) -> bool:
    if token:
        return True
    message = getattr(chunk, "message", None)
    if message is None:
        return False
    if getattr(message, "tool_call_chunks", None):
        return True
    extra = getattr(message, "additional_kwargs", None) or {}
    return bool(isinstance(extra, Mapping) and extra.get("reasoning_content"))


def _usage_from_result(response: Any) -> ModelUsage:
    generations = getattr(response, "generations", None) or []
    llm_output = getattr(response, "llm_output", None)
    for group in generations:
        for generation in group or []:
            message = getattr(generation, "message", None)
            if message is None:
                continue
            metadata: dict[str, Any] = {}
            if isinstance(llm_output, Mapping):
                metadata.update(llm_output)
            response_metadata = getattr(message, "response_metadata", None)
            if isinstance(response_metadata, Mapping):
                metadata.update(response_metadata)
            usage_metadata = getattr(message, "usage_metadata", None)
            return normalize_model_usage(
                metadata,
                dict(usage_metadata) if isinstance(usage_metadata, Mapping) else None,
            )
    if isinstance(llm_output, Mapping):
        return normalize_model_usage(dict(llm_output))
    return ModelUsage()


class UsageLedgerCallback(BaseCallbackHandler):
    """Writes one ledger row when a model call ends. Never changes the call."""

    run_inline = True
    raise_error = False
    ignore_chain = True
    ignore_agent = True
    ignore_retriever = True
    ignore_retry = True
    ignore_custom_event = True

    def __init__(self, *, provider_id: str, model_name: str) -> None:
        self.provider_id = _clean_text(provider_id)
        self.model_name = _clean_text(model_name)
        self._runs: OrderedDict[UUID, _OpenRun] = OrderedDict()
        self._lock = threading.Lock()

    def __copy__(self) -> UsageLedgerCallback:
        # One handler per model: a copied model keeps recording to the same ledger.
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> UsageLedgerCallback:
        return self

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            run = _OpenRun(_call_identity(metadata), time.perf_counter(), time.time())
            with self._lock:
                self._runs[run_id] = run
                while len(self._runs) > _MAX_OPEN_RUNS:
                    self._runs.popitem(last=False)
        except Exception:
            logger.debug("Usage ledger could not open a call", exc_info=True)

    def on_llm_new_token(self, token: Any, *, chunk: Any = None, run_id: UUID, **kwargs: Any) -> None:
        run = self._runs.get(run_id)
        if run is not None and run.first_output is None and _has_output(token, chunk):
            run.first_output = time.perf_counter()

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, response, failed=False)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._finish(run_id, kwargs.get("response"), failed=True)

    def _finish(self, run_id: UUID, response: Any, *, failed: bool) -> None:
        try:
            with self._lock:
                run = self._runs.pop(run_id, None)
            if run is None:
                return
            usage = _usage_from_result(response)
            if failed and not usage.reported:
                # A stopped or failed call reports nothing we could count.
                return
            ended = time.perf_counter()
            record_model_usage(UsageRecord(
                purpose=run.identity.purpose,
                provider=self.provider_id,
                model=self.model_name,
                usage=usage,
                thread_id=run.identity.thread_id,
                generation_id=run.identity.generation_id,
                call_id=str(run_id),
                iteration=run.identity.iteration,
                latency_ms=round((ended - run.started) * 1000),
                ttft_ms=(
                    round((run.first_output - run.started) * 1000)
                    if run.first_output is not None
                    else None
                ),
                created_at=run.created_at,
            ))
        except Exception:
            logger.debug("Usage ledger could not close a call", exc_info=True)


def attach_usage_ledger(model: Any, *, provider_id: str, model_name: str) -> Any:
    """Give a chat model the ledger callback once. Returns the same model."""
    try:
        from langchain_core.language_models import BaseLanguageModel

        if not isinstance(model, BaseLanguageModel):
            return model
        callbacks = model.callbacks
        if callbacks is not None and not isinstance(callbacks, list):
            return model
        if any(isinstance(handler, UsageLedgerCallback) for handler in callbacks or ()):
            return model
        model.callbacks = [
            *(callbacks or ()),
            UsageLedgerCallback(provider_id=provider_id, model_name=model_name),
        ]
    except Exception:
        logger.debug("Usage ledger not attached", exc_info=True)
    return model
