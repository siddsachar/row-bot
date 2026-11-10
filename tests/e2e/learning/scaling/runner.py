"""One run of a task through Row-Bot's real Agent runtime, as every arm of the study is measured (§2, §7).

A **run** is the task's own first message as one Agent turn in a fresh thread and a fresh workspace, from the world
state the task carries (its input files and its apps as a correct assistant left them), on the arm's model. Nothing
one run leaves is seen by the next: its thread (and with it what Learning captured of it) and every memory go after
it, as in the product's evaluation worker (``eval_worker._forget_task``). The profile is the study's scratch one.

- **The person** (a held-out request): their own tools and approval level; their fixed policy approves an action on
  what the request named and denies the rest (``person.approval_decision``), and stops a turn that runs past their
  patience (counted as failed, cause ``stopped``). No correction turns: the outcome measures the model. A pack task
  has no person: the tools the three people share that the evaluation worker allows, Hands-off.
- **The budget** (every arm alike): 150K tokens (the evaluation worker's own gate, refusing a model call before it
  would pass them), the task's step limit (its tool calls, ``eval_worker.TOOL_CALL_FACTOR`` per step) and 900 s.
  Out of budget counts as failed (``budget``).
- **The outcome**: the product's verifier on what the turn left (its last message, the workspace, the apps). A
  provider outage is ``error`` (outside the denominator; the caller reruns it once), a malformed call the model wrote
  is ``failed`` (O70), and a run no check could decide is ``unverified`` (outside the denominator).
- **The graphics card**: a local arm's run books it first (``GpuGate``, waiting through quiet hours and while the
  participant's own Row-Bot trains) and gives it back between runs when a quiet hour comes, the participant trains or
  another lane wrote that it waits.

:class:`StudyRunner` is also the vault's pack runner (``evaluation.audit_check``'s ``runner``), so the hidden pack
tasks run exactly as the held-out requests do; for the retrieval arm it finds the examples for the task it is given.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.e2e.learning.scaling import retrieval

logger = logging.getLogger("row_bot.scaling")

#: The tools a pack task gets: those the three seeded people share that the evaluation worker allows.
PACK_TOOLS = ("filesystem", "memory", "calculator")
#: An approval asked more times than this in one turn ends it (the release check's own bound).
MAX_APPROVAL_ROUNDS = 6
#: The ChatGPT lane answered with a usage or rate limit: the process stops at once (the release check's markers).
LIMIT_MARKERS = ("rate limit", "rate_limit", "usage limit", "usage_limit", "429", "too many requests")
#: The marker the retrieval arm carries in its ways (the vault's result cache keys on it, so its runs are its own).
RETRIEVAL_MARK = "retrieval"


class ProviderLimit(RuntimeError):
    """The ChatGPT lane said its usage or rate limit was reached."""


@dataclass(frozen=True)
class Person:
    """A seeded person as a run needs them: tools, approval level and patience."""

    id: str
    tools: tuple[str, ...]
    approval_mode: str
    patience_calls: int
    patience_seconds: float

    @classmethod
    def load(cls, person_id: str) -> Person:
        from tests.e2e.learning.seeded import generator

        person = generator.load_person(person_id)
        policy = person.get("policy") or {}
        return cls(person_id, tuple(person["tools"]), str(person.get("approval_mode") or "approve"),
                   int(policy.get("patience_tool_calls", 16)), float(policy.get("patience_seconds", 300)))


@dataclass(frozen=True)
class Spec:
    """An arm as the runner runs it: its model, whether it is local, and whether it retrieves."""

    name: str
    model_ref: str
    local: bool
    retrieves: bool = False


@dataclass
class Run:
    case: Any  # evaluation.CaseResult
    thread: str
    approvals: list[dict[str, Any]] = field(default_factory=list)
    #: The tool calls refused or failed that the turn never recovered, as (tool name, problem class) pairs.
    unrecovered: list[tuple[str, str]] = field(default_factory=list)


#: What an unrecovered call keeps: a tool name and a problem code that are plain identifiers. Anything else (a tool
#: error's free text, say) is kept as ``other`` / ``refused``, so no argument, output or task text is kept.
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_PROBLEM_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,39}$")


def unrecovered_calls(record: Any) -> list[tuple[str, str]]:
    """The refused or failed tool calls a turn never got right afterwards (``eval_worker.TurnRecord.unrecovered``,
    the record ``bad_call`` is decided from), as (tool name, problem class) pairs only."""

    return [(name if _TOOL_NAME.match(name or "") else "other",
             problem if _PROBLEM_CODE.match(problem or "") else "refused")
            for name, problem in record.unrecovered()]


class StudyRunner:
    """Runs tasks for the arms (see the module's docstring). ``owners`` maps a seeded request's id to its person and
    the paths they approve; ``after(arm, task, thread)`` sees each run's thread before it is forgotten."""

    signature = "scaling-study-1"

    def __init__(self, *, data: Path, arms: Mapping[str, Spec], owners: Mapping[str, tuple[Person, tuple[str, ...]]],
                 gpu: Any, task_tokens: int, seconds: float, index: retrieval.Index | None = None, k: int = 4,
                 unload: Callable[[str], None] = lambda _arm: None) -> None:
        self.data = data
        self.arms = dict(arms)
        if any(spec.retrieves for spec in arms.values()) and index is None:
            raise ValueError("the retrieval arm needs its index")
        self.owners = dict(owners)
        self.gpu = gpu
        self.task_tokens = int(task_tokens)
        self.seconds = float(seconds)
        self.index = index
        self.k = int(k)
        self.unload = unload
        self.after: Callable[[str, Any, str], None] | None = None
        #: Sees every run's result (its tokens are spent whether the run came from the vault or not).
        self.on_run: Callable[[str, Any], None] | None = None
        self.work = data.parent / "work"
        self.gpu_seconds: dict[str, float] = {}
        self._arm: str | None = None
        self._gate: Any = None

    # ── Once per process ────────────────────────────────────────────────

    def boot(self, people: Sequence[Person]) -> None:
        """The fake apps as tools, the budget gate on every model call, and the tools that would act outside the
        workspace or reach the network left bound but refusing (as the release check does)."""

        import row_bot.agent  # noqa: F401 - binds the runtime before the gate patches the ledger
        from row_bot.learning import eval_worker
        from row_bot.tools import registry

        eval_worker._register_fake_apps()  # noqa: SLF001 - the packs' apps, as the evaluation worker registers them
        self._gate = eval_worker._TaskGate()  # noqa: SLF001 - the evaluation worker's own budget gate
        eval_worker._install_gate(self._gate)  # noqa: SLF001
        inert = (set(eval_worker.UNSAFE_TOOLS) - {"task"}) & {tool for person in people for tool in person.tools}

        def refuse(*_a: Any, **_k: Any) -> str:
            return "This tool isn't available in this run."

        async def refuse_async(*_a: Any, **_k: Any) -> str:
            return refuse()

        for name in inert:
            tool = registry.get_tool(name)
            if tool is None:
                continue
            build = tool.as_langchain_tools

            def inert_tools(build: Any = build) -> list:
                items = build()
                for item in items:
                    item.func = refuse
                    item.coroutine = refuse_async
                return items

            try:
                object.__setattr__(tool, "as_langchain_tools", inert_tools)
            except Exception:  # noqa: BLE001
                tool.as_langchain_tools = inert_tools
        self.work.mkdir(parents=True, exist_ok=True)

    def reset_gate(self, allowance: int) -> None:
        """Before a model call outside a run (relabelling, replay): the gate's allowance for it."""

        self._gate.reset(int(allowance), None)

    # ── The card ────────────────────────────────────────────────────────

    def card(self, arm: str) -> None:
        """Before ``arm``'s next run: book the card for a local arm (giving it back first when another lane waits,
        a quiet hour comes or the participant trains); give it back for the teacher."""

        if self._arm and self._arm != arm:
            self.unload(self._arm)
        self._arm = arm
        if not self.arms[arm].local:
            self.gpu.release()
            return
        if self.gpu.held is not None and yield_card(self.gpu):
            self.gpu.release()
        self.gpu.acquire(f"scaling study: {arm} runs",
                         expect="hours, one arm's runs (the card goes back between runs when asked), up to 24 GB")

    def finish(self) -> None:
        if self._arm:
            self.unload(self._arm)
        self._arm = None
        self.gpu.release()

    # ── The vault's pack runner ─────────────────────────────────────────

    def run(self, task: Any, arm: Any, *, budget: Any, meter: Any, arm_left: int | None) -> Any:
        """``evaluation.PackRunner``: one run of a pack task (or a held-out request) for ``arm``."""

        from row_bot.learning.evaluation import LimitReached, estimate_tokens

        spec = self.arms[arm.name]
        allowance = budget.task_tokens if arm_left is None else min(budget.task_tokens, max(0, arm_left))
        if meter is not None and not spec.local:
            if not meter.allows(min(allowance, estimate_tokens([{"content": task.prompt}]) * 3), local=False):
                raise LimitReached()
        found = self.run_task(task, arm.name, allowance=allowance).case
        if meter is not None:
            meter.charge(found.tokens - found.local_tokens, local=False)
            meter.charge(found.local_tokens, local=True)
        return found

    # ── One run ─────────────────────────────────────────────────────────

    def run_task(self, task: Any, arm: str, *, allowance: int | None = None) -> Run:
        from row_bot import models, threads
        from row_bot.learning import eval_worker
        from row_bot.learning.packs.fake_apps import FakeApps

        spec = self.arms[arm]
        self.card(arm)
        person, approvals = self.owners.get(task.id, (None, ()))
        workspace = Path(tempfile.mkdtemp(prefix="run-", dir=str(self.work)))
        for name, content in task.files.items():
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        eval_worker._CURRENT.apps = FakeApps(task.apps)  # noqa: SLF001 - the apps the fake app tools act on
        tools = self._tools(person.tools if person is not None else eval_worker.safe_tools(PACK_TOOLS))
        from row_bot.tools import registry

        registry.set_tool_config("filesystem", "workspace_root", str(workspace))
        models.adopt_saved_default(spec.model_ref)  # Row-Bot's own calls in the turn stay on the arm's model
        if spec.retrieves:
            eval_worker.seed_arm({"ways": retrieval.ways(self.index.pick(task.prompt, self.k))})
        self._gate.reset(int(allowance or self.task_tokens), None)
        _, baseline = eval_worker._ledger_rows(0)  # noqa: SLF001 - the worker's own reading of the ledger
        thread = f"sc-{uuid.uuid4().hex[:20]}"
        mode = person.approval_mode if person is not None else "full"
        threads.create_thread(f"Study {task.id}", thread_id=thread, seed_default_skills=False, approval_mode=mode)
        started = time.monotonic()
        try:
            outcome = self._turn(task, spec.model_ref, tools, thread, mode, person, approvals)
            case = self._case(task, spec, outcome, workspace, baseline, started)
            if self.after is not None:
                self.after(arm, task, thread)
        finally:
            eval_worker._forget_task(thread)  # noqa: SLF001 - nothing one run leaves is seen by the next
            eval_worker.unseed()
            shutil.rmtree(workspace, ignore_errors=True)
            if spec.local:
                self.gpu_seconds[arm] = self.gpu_seconds.get(arm, 0.0) + time.monotonic() - started
        if self.on_run is not None:
            self.on_run(arm, case)
        text = " ".join([*outcome["errors"], *self._gate.errors]).lower()
        if case.status == "error" and any(marker in text for marker in LIMIT_MARKERS):
            raise ProviderLimit(case.detail)
        return Run(case, thread, outcome["approvals"], unrecovered_calls(outcome["record"]))

    def _tools(self, wanted: Sequence[str]) -> list[str]:
        from row_bot.learning import eval_worker
        from row_bot.tools import registry

        names = set(wanted) | set(eval_worker.APP_TOOLS.values())
        for tool in registry.get_all_tools():
            if registry.is_enabled(tool.name) != (tool.name in names):
                registry.set_enabled(tool.name, tool.name in names)
        return [tool.name for tool in registry.get_enabled_tools()]

    def _turn(self, task: Any, model_ref: str, tools: list[str], thread: str, mode: str, person: Person | None,
              approvals: Sequence[str]) -> dict[str, Any]:
        from row_bot import agent
        from row_bot.cancellation import CancellationScope, use_cancellation_scope
        from row_bot.learning import eval_worker
        from tests.e2e.learning.seeded.person import approval_decision

        stop = threading.Event()
        scope = CancellationScope(stop)
        out: dict[str, Any] = {"answer": "", "final": "", "calls": 0, "status": "unknown", "errors": [],
                               "approvals": [], "why": "", "record": eval_worker.TurnRecord()}
        max_calls = int(task.max_steps) * eval_worker.TOOL_CALL_FACTOR

        def end(reason: str) -> None:
            if not out["why"]:
                out["why"] = reason
            scope.cancel(reason)

        timers = [threading.Timer(self.seconds, end, args=("time",))]
        if person is not None:
            timers.append(threading.Timer(person.patience_seconds, end, args=("person",)))
        for timer in timers:
            timer.daemon = True
            timer.start()

        def consume(events: Any) -> list[Any]:
            last: list[str] = []
            for event in events:
                kind = event[0]
                payload = event[1] if len(event) > 1 else None
                if kind == "token":
                    last.append(str(payload or ""))
                elif kind in ("tool_call", "tool_done"):
                    last = []
                elif kind == "done":
                    out["answer"] = str(payload or "")
                    out["final"] = "".join(last).strip() or out["answer"]
                    out["status"] = "done" if out["status"] == "unknown" else out["status"]
                out["record"].add(kind, out["final"] if kind == "done" else payload)
                if kind == "tool_call":
                    out["calls"] += 1
                    if out["calls"] > max_calls:
                        end("steps")
                    elif person is not None and out["calls"] > person.patience_calls:
                        end("person")
                elif kind == "terminal":
                    out["status"] = "steps"
                elif kind == "error":
                    out["status"] = "error" if out["status"] == "unknown" else out["status"]
                    out["errors"].append(str(payload or "")[:160])
                elif kind == "interrupt":
                    return list(payload or [])
            return []

        config = {"configurable": {"thread_id": thread, "model_override": model_ref, "runtime_mode": "agent",
                                   "runtime_surface": "normal_chat", "approval_mode": mode,
                                   "generation_id": uuid.uuid4().hex}}
        try:
            with use_cancellation_scope(scope):
                interrupts = consume(agent.stream_agent(task.prompt, tools, config, stop_event=stop))
                rounds = 0
                while interrupts and not stop.is_set() and rounds < MAX_APPROVAL_ROUNDS:
                    rounds += 1
                    decisions = [approval_decision(item, tuple(approvals)) for item in interrupts]
                    out["approvals"].extend({"tool": str(item.get("tool") or ""), "approved": decision}
                                            for item, decision in zip(interrupts, decisions))
                    ids = [str(item.get("__interrupt_id")) for item in interrupts if item.get("__interrupt_id")]
                    interrupts = consume(agent.resume_stream_agent(tools, config, all(decisions),
                                                                   interrupt_ids=ids or None, stop_event=stop))
                if interrupts:
                    out["status"] = "asked"
        except InterruptedError:
            pass  # a stop: ``why`` says whose
        except Exception as error:  # noqa: BLE001 - a failed turn is a result
            out["status"] = "error"
            out["errors"].append(type(error).__name__)
            out["record"].add("error", type(error).__name__)
        finally:
            for timer in timers:
                timer.cancel()
            try:
                agent._set_active_runtime_context()  # noqa: SLF001 - leave no turn context behind
            except Exception:  # noqa: BLE001
                pass
        return out

    def _case(self, task: Any, spec: Spec, outcome: Mapping[str, Any], workspace: Path, baseline: int,
              started: float) -> Any:
        """The run as a ``CaseResult``: the evaluation worker's own reading of a turn, and the person's stop."""

        from row_bot.learning import eval_worker
        from row_bot.learning.evaluation import MALFORMED_DETAIL, CaseResult
        from row_bot.learning.packs.verify import Outcome, verify, workspace_files
        from row_bot.learning.spending import is_local_provider

        rows, _ = eval_worker._ledger_rows(baseline)  # noqa: SLF001 - before the thread goes (its rows go with it)
        tokens = sum(row["input"] + row["output"] for row in rows)
        local = sum(row["input"] + row["output"] for row in rows if is_local_provider(row["provider"]))
        gate = self._gate
        if gate.estimated and tokens < gate.spent and spec.local:
            tokens = local = gate.spent  # a local runtime that reports no usage: what was sent (estimated)
        why, status, cause, guarded = outcome["why"], "failed", "", False
        if gate.refused == "task":
            status, detail = "budget", "ran out of its token budget"
        elif why == "time":
            status, detail = "budget", "ran out of time"
        elif why == "steps" or outcome["status"] == "steps":
            status, detail = "budget", "ran out of steps"
        elif why == "person":
            detail, cause = "the person stopped it", "stopped"
        elif outcome["status"] == "asked":
            detail = "stopped to ask for approval"
        elif outcome["status"] == "error" and gate.malformed_call:
            detail = MALFORMED_DETAIL
        elif outcome["status"] == "error":
            status, detail = "error", "the model call failed: " + "; ".join(outcome["errors"])[:200]
        elif outcome["status"] == "done":
            try:
                verdict = verify(task, Outcome(outcome["final"] or outcome["answer"], workspace_files(workspace),
                                               eval_worker._CURRENT.apps.state()), backend="auto")  # noqa: SLF001
                status, detail, guarded = ("passed" if verdict.passed else "failed"), verdict.detail, verdict.guarded
            except Exception as error:  # noqa: BLE001 - no check could decide it
                status, detail = "unverified", f"the check could not run ({type(error).__name__})"
        else:
            detail = "the turn ended without an answer"
        if not cause:
            cause = eval_worker.failure_cause(status, detail, outcome["record"], task_tools=task.tools,
                                              check=task.verifier.kind)
        return CaseResult(task.id, "pack", task.kind_of_work, status, task.verifier.kind, tokens,  # type: ignore[arg-type]
                          local, int(outcome["calls"]), round(time.monotonic() - started, 3), str(detail)[:300],
                          cause, bool(guarded) and status == "failed")


def yield_card(gpu: Any) -> bool:
    """Whether a local arm gives the card back between runs: a quiet hour, the participant's own training, or
    another lane that wrote it waits (O116)."""

    from tests.e2e.learning.seeded.scratch import waiting_lanes

    if gpu.training() or gpu.quiet():
        return True
    try:
        text = gpu.booking.read_text(encoding="utf-8") if gpu.booking else ""
    except OSError:
        text = ""
    return any(lane != gpu.lane for lane in waiting_lanes(text))


def decisions(thread: str) -> list[Any]:
    """The run's model calls as Learning captured them, in order."""

    from row_bot.learning import capture
    from row_bot.learning.store import LearningStore

    capture.flush(60.0)
    found = [item for item in LearningStore().find("decision", thread_id=thread, limit=10_000)
             if getattr(item, "kind", "") == "model_call"]
    return sorted(found, key=lambda item: (item.sequence, item.created_at, item.id))


__all__ = ["LIMIT_MARKERS", "PACK_TOOLS", "Person", "ProviderLimit", "RETRIEVAL_MARK", "Run", "Spec", "StudyRunner",
           "decisions", "unrecovered_calls", "yield_card"]
