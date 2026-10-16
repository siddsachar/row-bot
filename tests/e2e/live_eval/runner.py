"""Run eval tasks through the real Row-Bot Agent runtime, inside one worker process.

The worker (``worker.py``) sets an isolated data folder before anything from
Row-Bot is imported, then hands control to :class:`Runtime`. Nothing here
reads a person's data folder or keychain: keys come from the environment, and
the OS keychain is replaced by a backend that refuses every call.
"""
from __future__ import annotations

import json
import logging
import os
import struct
import threading
import time
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import ledger as ledger_reader
from .budget import BudgetExceeded, BudgetGuard, Caps, Spend, estimate_call
from .capture import HttpCapture
from .pricing import Rates, price_call, rates_for, upper_bound_usd
from .report import BUDGET, ERROR, FAIL, PASS, SKIPPED, TaskRecord
from .runner_limits import FREE_PROVIDERS
from .tasks import (
    MEMORY_FACT,
    SAFE_DEFAULT_EXCLUDE,
    Context,
    Outcome,
    Task,
    ToolCall,
    ToolResult,
    TurnResult,
    estimate,
)

logger = logging.getLogger("row_bot.live_eval")

OLLAMA_VISION_ENV = "ROW_BOT_LIVE_EVAL_OLLAMA_VISION"
OLLAMA_RELOAD_MS_ENV = "ROW_BOT_LIVE_EVAL_OLLAMA_RELOAD_MS"
WORKFLOW_TIMEOUT_S = 240.0
MAX_RESUMES = 3
_TOOL_TEXT = 400

# Log lines worth counting per task. Each is a fixed message Row-Bot writes.
LOG_TALLIES: dict[str, str] = {
    "prefix_stable": "stable prefix:",
    "prefix_broken": "prefix broken",
    "hygiene_jumps": "History hygiene: cleared earlier tool output",
    "thinking_dropped_logs": "anthropic_thinking_binding: API dropped",
    "thinking_signature_retries": "anthropic_thinking_binding: thinking signature rejected",
    "thinking_controls_rejected": "anthropic_thinking_binding: endpoint rejected the binding controls",
    "cache_marker_retries": "rejected the cache instructions",
    "reasoning_replay_retries": "replayed reasoning was rejected",
    "reasoning_fallbacks": "reasoning_fallback: retrying provider default",
    "output_truncated": "Model output truncated",
    "background_schema_refused": "refused a JSON-schema reply",
}


class LogTally(logging.Handler):
    """Counts known Row-Bot log lines (never stores message text)."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.counts: dict[str, int] = {}
        self._lock_counts = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            return
        for name, needle in LOG_TALLIES.items():
            if needle in message:
                with self._lock_counts:
                    self.counts[name] = self.counts.get(name, 0) + 1

    def take(self) -> dict[str, int]:
        with self._lock_counts:
            counts, self.counts = self.counts, {}
        return counts


def install_log_tally() -> LogTally:
    tally = LogTally()
    logging.getLogger("row_bot").addHandler(tally)
    # The prefix diff only runs when its logger is at DEBUG (A1).
    logging.getLogger("row_bot.prompt_prefix_diff").setLevel(logging.DEBUG)
    if logging.getLogger("row_bot").level in (logging.NOTSET,) or logging.getLogger("row_bot").level > logging.INFO:
        logging.getLogger("row_bot").setLevel(logging.INFO)
    return tally




# ── Budget gate on every model call ────────────────────────────────────────


def _flatten(messages: Any) -> list[Any]:
    out: list[Any] = []
    for item in messages or []:
        if isinstance(item, (list, tuple)):
            out.extend(item)
        else:
            out.append(item)
    return out


@dataclass
class BudgetGate:
    """Refuses a model call that would take the run past a cap, before it is sent."""

    caps: Caps
    rates: Mapping[str, Rates]
    ledger_path: Callable[[], Path]
    baseline_id: int = 0
    carried: Spend = field(default_factory=Spend)
    refused: int = 0

    def spent(self) -> Spend:
        spend = Spend(self.carried.tokens, self.carried.usd, self.carried.unpriced_tokens, self.carried.calls)
        for call in ledger_reader.rows_after(self.ledger_path(), self.baseline_id):
            if call.provider in FREE_PROVIDERS:
                continue
            tokens = call.tokens()
            price = price_call(tokens, rates_for(self.rates, call.provider, call.model))
            spend.calls += 1
            spend.tokens += tokens.processed
            if price.usd is None:
                spend.unpriced_tokens += tokens.processed
            else:
                spend.usd += price.usd
        return spend

    def before_call(self, provider: str, model: str, messages: Any, invocation: Mapping[str, Any]) -> None:
        if provider in FREE_PROVIDERS:
            return
        max_output = invocation.get("max_tokens") or invocation.get("max_output_tokens") or invocation.get("num_predict")
        estimated_in, estimated_out = estimate_call(
            _flatten(messages), tools=invocation.get("tools"), max_output=max_output if isinstance(max_output, int) else None
        )
        usd = upper_bound_usd(estimated_in, estimated_out, rates_for(self.rates, provider, model))
        guard = BudgetGuard(self.caps, self.spent())
        reason = guard.over_by(estimated_in + estimated_out, usd)
        if reason:
            self.refused += 1
            raise BudgetExceeded(f"live eval budget: model call not sent ({reason})")


def install_budget_gate(gate: BudgetGate) -> Callable[[], None]:
    """Check every model call Row-Bot makes (all go through the usage-ledger callback)."""
    from row_bot.providers import usage_ledger

    callback = usage_ledger.UsageLedgerCallback
    original = callback.on_chat_model_start
    original_raise = callback.raise_error

    def on_chat_model_start(self: Any, serialized: Any, messages: Any, *, run_id: Any, **kwargs: Any) -> None:
        gate.before_call(self.provider_id, self.model_name, messages, kwargs.get("invocation_params") or {})
        return original(self, serialized, messages, run_id=run_id, **kwargs)

    callback.on_chat_model_start = on_chat_model_start
    # LangChain re-raises a handler's exception only when the handler asks for it.
    callback.raise_error = True

    def undo() -> None:
        callback.on_chat_model_start = original
        callback.raise_error = original_raise

    return undo


# ── One task ───────────────────────────────────────────────────────────────


def _tool_name(payload: Any) -> str:
    for attribute in ("runtime_name", "raw_name"):
        value = str(getattr(payload, attribute, "") or "")
        if value:
            return value
    if isinstance(payload, Mapping):
        return str(payload.get("raw_name") or payload.get("name") or "")
    return str(payload or "")


def _consume(events: Iterable[tuple[str, Any]], turn: TurnResult) -> list[dict[str, Any]]:
    interrupts: list[dict[str, Any]] = []
    for event_type, payload in events:
        turn.event_counts[event_type] = turn.event_counts.get(event_type, 0) + 1
        if event_type == "done":
            turn.answer = str(payload or "")
            if turn.status == "unknown":
                turn.status = "done"
        elif event_type == "error":
            turn.status = "error"
            turn.errors.append(str(payload or "")[:500])
        elif event_type == "tool_call":
            turn.tool_calls.append(ToolCall(_tool_name(payload), dict(getattr(payload, "args", {}) or {})))
        elif event_type == "tool_done":
            data = payload if isinstance(payload, Mapping) else {}
            content = str(data.get("content") or "")
            turn.tool_results.append(ToolResult(
                name=str(data.get("raw_name") or data.get("name") or ""),
                content=content[:_TOOL_TEXT],
                error=content.strip().lower().startswith("error"),
            ))
        elif event_type == "interrupt":
            turn.interrupts += 1
            interrupts.extend(item for item in (payload or []) if isinstance(item, Mapping))
        elif event_type == "terminal":
            turn.status = "error"
            message = payload.get("message") if isinstance(payload, Mapping) else payload
            turn.errors.append(f"terminal: {message}"[:500])
    return interrupts


@dataclass
class Runtime:
    """One provider's model inside one worker process."""

    provider_id: str
    model_id: str
    workspace_root: Path
    caps: Caps
    rates: Mapping[str, Rates] = field(default_factory=dict)
    context: Context = field(default_factory=Context)
    tally: LogTally | None = None
    gate: BudgetGate | None = None
    setup_notes: list[str] = field(default_factory=list)
    _undo: list[Callable[[], None]] = field(default_factory=list)

    @property
    def model_ref(self) -> str:
        return f"model:{self.provider_id}:{self.model_id}"

    def setup(self, variant: Any = None) -> list[str]:
        """Import Row-Bot, close the keychain, load the catalog, pick the model, apply the variant."""
        from row_bot import data_paths, secret_store

        data_dir = Path(data_paths.get_row_bot_data_dir())
        if ledger_reader.everyday_data_folder(data_dir):
            raise RuntimeError(f"refusing to run in the everyday data folder {data_dir}")
        # Test mode keeps the OS keychain closed (keys come from the environment only).
        if not secret_store._test_mode_active():
            raise RuntimeError("the live eval worker must run with ROW_BOT_TEST_MODE set")
        import row_bot.agent  # noqa: F401 - binds the runtime before patches
        from row_bot import models

        self.tally = install_log_tally()
        from .learning import register_tools

        register_tools()
        if self.provider_id == "codex" and self.model_id in {"", "auto"}:
            # The ChatGPT lane (D31): GPT-6.1 Sol if the live catalog lists it, otherwise GPT-5.6 Sol.
            from row_bot.providers import codex

            from .eval_profile import choose_model

            listed = [info.model_id for info in codex.fetch_codex_model_infos()]
            self.model_id = choose_model(listed)
            self.setup_notes.append(f"model {self.model_id} (the live catalog lists {len(listed)} models)")
        if self.provider_id != "ollama":
            try:
                result = models.refresh_cloud_provider_models(self.provider_id)
                self.setup_notes.append(f"catalog refresh: {getattr(result, 'status', None) or 'done'}")
            except Exception as exc:  # the readiness error on the first task tells the rest
                self.setup_notes.append(f"catalog refresh failed: {type(exc).__name__}: {exc}"[:300])
        else:
            self.setup_notes.append(refresh_local_catalog())
        models.adopt_saved_default(self.model_ref)
        changes: list[str] = []
        if variant is not None:
            from . import variants

            applied = variants.apply(variant)
            self._undo.append(applied.restore)
            changes = applied.changes
        self.gate = BudgetGate(self.caps, self.rates, lambda: Path(data_paths.get_usage_db_path(create_parent=False)))
        self._undo.append(install_budget_gate(self.gate))
        return changes

    def close(self) -> None:
        while self._undo:
            try:
                self._undo.pop()()
            except Exception:
                logger.debug("live eval: undo failed", exc_info=True)

    # -- tools and threads ---------------------------------------------------

    def _tool_names(self, task: Task) -> list[str]:
        if task.tools == "default":
            from row_bot.tools import registry

            return [tool.name for tool in registry.get_enabled_tools() if tool.name not in SAFE_DEFAULT_EXCLUDE]
        return list(task.tools)

    def _new_thread(self, task: Task) -> str:
        from row_bot import threads

        thread_id = f"le_{task.task_id[:20]}_{uuid.uuid4().hex[:8]}"
        threads.create_thread(
            f"Live eval {task.task_id}", thread_id=thread_id, approval_mode="full",
            model_override=self.model_ref, seed_default_skills=False,
        )
        return thread_id

    def _run_turn(self, prompt: str, mode: str, thread_id: str, tool_names: list[str]) -> TurnResult:
        import row_bot.agent as agent

        turn = TurnResult(prompt=prompt, thread_id=thread_id)
        config = {"configurable": {
            "thread_id": thread_id,
            "model_override": self.model_ref,
            "runtime_mode": "chat_only" if mode == "chat_only" else "agent",
            "runtime_surface": "normal_chat",
            "approval_mode": "full",
            "generation_id": uuid.uuid4().hex,
        }}
        try:
            interrupts = _consume(agent.stream_agent(prompt, tool_names, config), turn)
            resumes = 0
            # Anything that still stops for a person is declined: the eval never approves on anyone's behalf.
            while interrupts and resumes < MAX_RESUMES:
                resumes += 1
                ids = [str(item.get("__interrupt_id")) for item in interrupts if item.get("__interrupt_id")]
                interrupts = _consume(
                    agent.resume_stream_agent(tool_names, config, False, interrupt_ids=ids or None), turn
                )
        except BudgetExceeded as exc:
            turn.status = "budget"
            turn.errors.append(str(exc))
        except Exception as exc:
            turn.status = "exception"
            turn.errors.append(f"{type(exc).__name__}: {exc}"[:500])
        finally:
            agent._set_active_runtime_context()
        if turn.status == "unknown":
            turn.status = "error"
            turn.errors.append("the turn ended without a final answer")
        if any("live eval budget" in error for error in turn.errors):
            turn.status = "budget"
        return turn

    def _context_window(self, size: int | None) -> Callable[[], None]:
        if not size or self.provider_id == "ollama":
            return lambda: None
        from row_bot import models

        models.set_cloud_context_size(int(size))
        return models.clear_cloud_context_override

    # -- the task ------------------------------------------------------------

    def run_task(self, task: Task, *, label: str = "baseline") -> TaskRecord:
        record = TaskRecord(task.task_id, task.category, self.provider_id, self.model_id, variant=label)
        workspace = self.workspace_root / task.task_id
        workspace.mkdir(parents=True, exist_ok=True)
        if task.seed is not None:
            task.seed(workspace, self.context)
        from row_bot.tools import registry

        registry.set_tool_config("filesystem", "workspace_root", str(workspace))
        assert self.gate is not None and self.tally is not None
        baseline = ledger_reader.max_id(self.gate.ledger_path())
        self.tally.take()
        outcome = Outcome(workspace=workspace)
        started = time.time()
        restore_window = self._context_window(task.context_window(self.context) if task.context_window else None)
        capture = HttpCapture(lambda request: "/api/chat" in str(request.url)) if "ollama_requests" in task.collect else None
        try:
            if capture is not None:
                capture.__enter__()
            tool_names = self._tool_names(task)
            thread_id = self._new_thread(task)
            for turn in task.turns(self.context):
                if turn.new_thread:
                    thread_id = self._new_thread(task)
                result = self._run_turn(turn.prompt, turn.mode, thread_id, tool_names)
                outcome.turns.append(result)
                if result.status != "done":
                    break
            if capture is not None and outcome.failed_turn() is None:
                self._extra_ollama_calls(thread_id)
            outcome.collected.update(self._collect(task, outcome))
        finally:
            if capture is not None:
                capture.__exit__(None, None, None)
            restore_window()
        record.duration_s = round(time.time() - started, 2)
        record.calls = ledger_reader.rows_after(self.gate.ledger_path(), baseline)
        outcome.collected["child_calls"] = sum(call.purpose == "child" for call in record.calls)
        outcome.collected["compaction_calls"] = sum(call.purpose == "compaction" for call in record.calls)
        if capture is not None:
            outcome.collected["ollama"] = analyze_ollama(capture, self.model_id)
        record.log_counts = self.tally.take()
        record.answer_excerpt = outcome.answer
        record.tool_calls = sum(len(turn.tool_calls) for turn in outcome.turns)
        record.tool_results = sum(len(turn.tool_results) for turn in outcome.turns)
        record.tools_used = sorted({name for name in outcome.tool_names() if name})
        record.errors = [error for turn in outcome.turns for error in turn.errors][:10]
        if any(turn.status == "budget" for turn in outcome.turns):
            record.status, record.reason = BUDGET, "a model call would have passed the budget cap"
        else:
            try:
                check = task.check(outcome, self.context)
                record.status = PASS if check.passed else FAIL
                record.reason = check.reason
                record.metrics.update(dict(check.metrics))
            except Exception as exc:
                record.status, record.reason = ERROR, f"check raised {type(exc).__name__}: {exc}"[:300]
            failed = outcome.failed_turn()
            if failed is not None and failed.status in {"exception", "error"} and record.status == FAIL:
                record.status = ERROR
        record.metrics["turns"] = len(outcome.turns)
        workflow = outcome.collected.get("workflow")
        if workflow:
            # D3: a workflow run is a back-to-back loop, so its cache writes should be 5-minute ones.
            run_calls = [call for call in record.calls if workflow.get("run_thread") and call.thread.startswith(
                str(workflow["run_thread"])[:12])]
            workflow["calls"] = len(run_calls)
            workflow["cache_write_1h_tokens"] = sum(int(call.cache_write_1h_tokens or 0) for call in run_calls)
            workflow["cache_write_tokens"] = sum(int(call.cache_write_tokens or 0) for call in run_calls)
            record.metrics["workflow"] = workflow
        return record.finish(self.rates)

    # -- collectors (Row-Bot lookups after the turns) ------------------------

    def _collect(self, task: Task, outcome: Outcome) -> dict[str, Any]:
        collected: dict[str, Any] = {}
        if "memory_fact" in task.collect:
            from row_bot import memory

            items = memory.list_memories(limit=500)
            collected["memory_has_fact"] = any(MEMORY_FACT in json.dumps(item, default=str) for item in items)
        if "workflow" in task.collect:
            collected["workflow"] = self._wait_for_workflow()
        if "learning_apps" in task.collect:
            from .learning import CURRENT

            collected["fake_apps"] = CURRENT.apps.state() if CURRENT.apps is not None else {}
        if "learning_replay" in task.collect:
            from .learning import run_replay

            collected["replay"] = run_replay(task.task_id, self.model_ref)
        return collected

    def _wait_for_workflow(self) -> dict[str, Any]:
        from row_bot import tasks as row_bot_tasks
        from row_bot.threads import get_latest_checkpoint_messages

        found = next(
            (item for item in row_bot_tasks.list_tasks() if "live eval digest" in str(item.get("name") or "").lower()),
            None,
        )
        if found is None:
            return {}
        task_id = str(found.get("id"))
        deadline = time.time() + WORKFLOW_TIMEOUT_S
        run: dict[str, Any] = {}
        final = {"completed", "completed_delivery_failed", "failed", "stopped", "blocked"}
        while time.time() < deadline:
            history = row_bot_tasks.get_run_history(task_id, 1)
            run = history[0] if history else {}
            if str(run.get("status") or "") in final:
                break
            time.sleep(2.0)
        output_ok = False
        if run.get("thread_id"):
            for message in get_latest_checkpoint_messages(str(run["thread_id"])):
                if getattr(message, "type", "") == "ai" and "digest ready" in str(getattr(message, "content", "")).lower():
                    output_ok = True
        return {
            "task_id": task_id,
            "status": str(run.get("status") or "not started"),
            "output_ok": output_ok,
            "run_thread": str(run.get("thread_id") or "")[:12],
        }

    def _extra_ollama_calls(self, thread_id: str) -> None:
        """The other call paths that must reuse the loaded model: Chat Only, a title, a background call, vision."""
        self._run_turn("Reply with exactly: still warm", "chat_only", thread_id, [])
        try:
            from row_bot.application import conversation_naming

            conversation_naming._ask(self.model_ref, "Plan a picnic", "Here is a picnic plan.")
        except Exception:
            logger.debug("live eval: title call failed", exc_info=True)
        try:
            from langchain_core.messages import HumanMessage

            from row_bot.providers.background import invoke_background

            invoke_background("condition", [HumanMessage(content="Is 7 greater than 3? Answer yes or no.")],
                              thread_model=self.model_ref)
        except Exception:
            logger.debug("live eval: background call failed", exc_info=True)
        if str(os.environ.get(OLLAMA_VISION_ENV, "")).strip() == "1":
            try:
                from row_bot.vision import VisionService

                VisionService().analyze(tiny_png(), "What colour is this image?", chat_model=self.model_ref)
            except Exception:
                logger.debug("live eval: vision call failed", exc_info=True)


def refresh_local_catalog() -> str:
    """Record what the local Ollama says each installed model can do (``/api/show``: tools, thinking,
    context), as the app's catalog refresh does, without its network calls. Without it a model whose name
    isn't in Row-Bot's family list (a personal model, say) reads as not tool-capable."""
    from dataclasses import replace

    from row_bot.providers.model_catalog import load_ollama_catalog_rows
    from row_bot.providers.model_catalog_cache import read_model_catalog_cache, write_model_catalog_cache

    try:
        rows = [dict(row) for row in load_ollama_catalog_rows()]
        write_model_catalog_cache(replace(read_model_catalog_cache(), ollama_rows=rows, generated_at=time.time(),
                                          reason="live eval"))
    except Exception as exc:  # the readiness error on the first task tells the rest
        return f"local catalog refresh failed: {type(exc).__name__}: {exc}"[:300]
    return f"local catalog: {len(rows)} Ollama models with their own capability answers"


def tiny_png(colour: tuple[int, int, int] = (200, 30, 30), size: int = 8) -> bytes:
    """A small solid-colour PNG, built without image libraries."""
    raw = b"".join(b"\x00" + bytes(colour) * size for _ in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def analyze_ollama(capture: HttpCapture, model_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """B2's local checks from the captured /api/chat exchanges plus /api/ps."""
    reload_ms = float(os.environ.get(OLLAMA_RELOAD_MS_ENV, "") or 1000)
    requests: list[dict[str, Any]] = []
    for exchange in capture.matching("/api/chat"):
        body = exchange.request_json if isinstance(exchange.request_json, Mapping) else {}
        options = body.get("options") if isinstance(body.get("options"), Mapping) else {}
        final = next((line for line in reversed(exchange.ndjson()) if line.get("done")), {})
        requests.append({
            "model": body.get("model"),
            "num_ctx": options.get("num_ctx"),
            "keep_alive": body.get("keep_alive"),
            "load_ms": round(int(final.get("load_duration") or 0) / 1e6, 1) if final else None,
            "prompt_eval_count": final.get("prompt_eval_count"),
        })
    problems: list[str] = []
    same_model = [item for item in requests if str(item.get("model") or "") == model_id]
    contexts = {item["num_ctx"] for item in same_model}
    if len(contexts) > 1:
        problems.append(f"num_ctx differs across calls: {sorted(map(str, contexts))}")
    expected_keep_alive = None if os.environ.get("OLLAMA_KEEP_ALIVE") else "30m"
    wrong = [item["keep_alive"] for item in same_model if item["keep_alive"] != expected_keep_alive]
    if wrong:
        problems.append(f"keep_alive {wrong[:3]} (expected {expected_keep_alive!r})")
    reloads = [item for item in same_model[1:] if (item.get("load_ms") or 0) > reload_ms]
    if reloads:
        problems.append(f"{len(reloads)} later call(s) loaded the model again (load > {reload_ms:.0f} ms)")
    expires_minutes = _ollama_expiry_minutes(model_id, now=now)
    if expected_keep_alive and expires_minutes is not None and not 20 <= expires_minutes <= 31:
        problems.append(f"ollama ps expiry is {expires_minutes:.1f} minutes away, expected about 30")
    return {"requests": requests, "problems": problems, "expires_in_minutes": expires_minutes}


def _ollama_expiry_minutes(model_id: str, *, now: datetime | None = None) -> float | None:
    import httpx

    host = os.environ.get("OLLAMA_HOST", "") or "127.0.0.1:11434"
    base = host if host.startswith("http") else f"http://{host.replace('0.0.0.0', '127.0.0.1')}"
    try:
        loaded = httpx.get(f"{base}/api/ps", timeout=5).json().get("models") or []
    except Exception:
        return None
    for item in loaded:
        if model_id in {item.get("name"), item.get("model")}:
            expires = str(item.get("expires_at") or "")
            try:
                moment = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            except ValueError:
                return None
            return (moment - (now or datetime.now(timezone.utc))).total_seconds() / 60
    return None


def run_tasks(runtime: Runtime, tasks: list[Task], guard: BudgetGuard, *, label: str = "baseline",
              on_record: Callable[[TaskRecord], None] | None = None) -> list[TaskRecord]:
    """Run tasks in order; stop the run before a task whose estimate would pass a cap."""
    records: list[TaskRecord] = []
    passed: set[str] = set()
    stopped = ""
    for task in tasks:
        record = TaskRecord(task.task_id, task.category, runtime.provider_id, runtime.model_id, variant=label)
        if stopped:
            record.status, record.reason = BUDGET, stopped
        elif task.depends_on and task.depends_on not in passed:
            record.status, record.reason = SKIPPED, f"needs {task.depends_on} to pass first"
        else:
            tokens, usd = estimate(task, runtime.context, runtime.rates, runtime.provider_id, runtime.model_id)
            reason = "" if runtime.provider_id in FREE_PROVIDERS else guard.over_by(tokens, usd)
            if reason:
                stopped = f"run stopped before {task.task_id}: {reason}"
                record.status, record.reason = BUDGET, stopped
            else:
                record = runtime.run_task(task, label=label)
                for call in record.calls:
                    if call.provider in FREE_PROVIDERS:
                        continue
                    tokens_used = call.tokens()
                    guard.charge(tokens_used, price_call(tokens_used, rates_for(runtime.rates, call.provider, call.model)))
                if record.status == PASS:
                    passed.add(task.task_id)
                if record.status == BUDGET:
                    stopped = f"run stopped during {task.task_id}: a model call would have passed a cap"
        records.append(record)
        if on_record is not None:
            on_record(record)
    return records
