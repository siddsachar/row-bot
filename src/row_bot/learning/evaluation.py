"""Learning's evaluation (E11, E12, E10): the one evaluator.

Testing a change (L3b), comparing model versions (L2c) and strategy trials (L4a) all
call this module. It runs **arms** (change vs none, version vs version) on the same
tasks with the same budget and returns a :class:`~row_bot.learning.contracts.TestRun`
per arm with counts and their denominator.

**Two kinds of test (E11)**

- **Replay**: held-out model-call decisions from the person's own work, re-asked at the
  captured prefix (``capture.read_request``: the messages, the tools, the model) and
  checked against the corrected action when there is one, else against what the model
  did then. Only ``exact`` and ``redacted`` decisions replay exactly; a ``partial`` one
  (past chats, D26) is used only with a correction and only for checks that don't need
  the exact prefix (which tool was chosen). Decisions that rest on tool or web content
  (``untrusted``) are left out unless the caller says E9 doesn't matter for its use.
- **Task packs and practice**: each task runs in a throwaway sandbox (``sandbox``) with
  its own files and fake apps, through a bounded tool loop, and its verifier checks the
  result (``packs.verify``). Nothing here opens the person's conversations, memory or
  settings: the run's only traces are the usage ledger rows of its model calls and,
  when asked, its TestRun records.

**Verifiers first, judges second**: every pack task has an executable check. A judge
model is used only where nothing executable can decide (a long free-text replay
answer), only after :func:`calibrate` measured its agreement with executable checks on
tasks that have both, and that calibration is reported with the result.

**Equal budgets (E12)**: every arm gets the same :class:`Budget` (tokens and steps per
task, tokens per arm), runs the same tasks in the same order (interleaved, so a stop
cuts all arms at the same task), and its use is recorded.

**Spending (E13)**: model calls are ``learning_test`` calls on the default chat model at
the lowest effort unless an arm names its model, and count against the monthly token
limit (``spending``). When the limit is reached the run stops before the next call and
says so; local models don't count.

**The audit vault (E10)**: only this module reads ``audit``. What it hands back to its
callers is aggregates (:func:`audit_check`) and a yes/no (:func:`is_novel`).

The metric definitions below are fixed points (E4): no strategy can change them.
"""

from __future__ import annotations

import json
import logging
import math
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from row_bot import learning as gate
from row_bot.learning import audit
from row_bot.learning.contracts import Cost, Decision, TaskFixture, TestRun, new_id
from row_bot.learning.packs import PackTask, visible_tasks
from row_bot.learning.packs.fake_apps import FakeApps
from row_bot.learning.packs.verify import Outcome, normalize, solve, verify
from row_bot.learning.sandbox import Sandbox, SandboxError
from row_bot.learning.spending import TokenMeter, is_local_provider
from row_bot.learning.store import MARK_HELD_OUT

logger = logging.getLogger(__name__)

# ── Fixed points (E4): metric definitions no strategy can change ────────────

METRIC_VERSION = 1
# "21 of 24 passed": passed over passed + failed. A task that ran out of its budget or
# steps failed; one the provider couldn't run (an error) or no check could decide
# (unverified) is reported but left out of the denominator.
SMALL_SAMPLE = 30
SMALL_SAMPLE_HALF_WIDTH = 0.15
VARIANCE_NOTE = "Small test, so expect some variation."
JUDGE_MIN_CASES = 10
JUDGE_MIN_AGREEMENT = 0.85
SHORT_TEXT = 160
RESULTS_FORMAT = "row_bot.learning.test_results"
PURPOSE = "learning_test"
_CHARS_PER_TOKEN = 3.5
_OUTPUT_ALLOWANCE = 4_096

Status = Literal["passed", "failed", "budget", "error", "unverified", "skipped", "not_run"]
Check = Literal["exact_action", "tool_choice"]


class EvaluationError(RuntimeError):
    """A refused evaluation; ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


class _LimitReached(Exception):
    """The monthly token limit would be passed by the next call."""


# A pack runner (the real-runtime worker, L2c2) raises this to stop a comparison at the limit.
LimitReached = _LimitReached


# ── Arms, budgets, results ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Budget:
    """What every arm may spend: tokens per task, steps per task (None: the task's own), tokens per arm."""

    task_tokens: int = 150_000
    max_steps: int | None = None
    arm_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"task_tokens": self.task_tokens, "max_steps": self.max_steps, "arm_tokens": self.arm_tokens}


@dataclass(frozen=True)
class Arm:
    """One side of a comparison: a model (None: the default chat model, D1) and the guidance
    the change adds at the start of every request (a lesson, a way of working)."""

    name: str
    model_ref: str | None = None
    guidance: tuple[str, ...] = ()
    subject_kind: Literal["lesson", "release", "attempt", "strategy", "baseline"] = "baseline"
    subject_id: str | None = None
    # The main model's arm (O31): its pack results are kept for :data:`CACHE_DAYS` per model, task
    # (by its content), metric version, budget and runner, so a comparison doesn't spend tokens again
    # on a result it already has. Replayed decisions are never cached.
    cache: bool = False


@dataclass(frozen=True)
class CaseResult:
    """One task (a pack task or a replayed decision) for one arm. ``tokens`` is everything the
    task used (the equal budget counts it all); ``local_tokens`` is the part local models used
    (it doesn't count against the monthly limit, D25)."""

    task_id: str
    suite: str
    kind_of_work: str
    status: Status
    check: str
    tokens: int = 0
    local_tokens: int = 0
    steps: int = 0
    seconds: float = 0.0
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def counted(self) -> bool:
        return self.status in ("passed", "failed", "budget")


@dataclass(frozen=True)
class Calibration:
    """How often a judge agreed with executable checks on tasks that have both."""

    cases: int
    agreed: int
    false_pass: int
    false_fail: int
    tokens: int = 0

    @property
    def agreement(self) -> float:
        return self.agreed / self.cases if self.cases else 0.0

    @property
    def reliable(self) -> bool:
        return self.cases >= JUDGE_MIN_CASES and self.agreement >= JUDGE_MIN_AGREEMENT

    def describe(self) -> str:
        return (f"The judge agreed with executable checks on {self.agreed} of {self.cases} tasks "
                f"({self.agreement:.0%}).")

    def to_dict(self) -> dict[str, Any]:
        return {"cases": self.cases, "agreed": self.agreed, "false_pass": self.false_pass,
                "false_fail": self.false_fail, "agreement": round(self.agreement, 4), "reliable": self.reliable,
                "tokens": self.tokens}


@dataclass
class ArmResult:
    arm: Arm
    run: TestRun
    cases: list[CaseResult]
    tokens: int
    local_tokens: int
    model_ref: str | None
    by_kind: dict[str, tuple[int, int]]
    # Aggregates that hold for audit runs too (no case leaves an audit, E10): tasks that ran (or came
    # from the cache), their wall time, the tokens they measured (cached ones included) and how many
    # of those were cloud tokens; how many results came from the cache (O31).
    ran: int = 0
    seconds: float = 0.0
    measured_tokens: int = 0
    measured_cloud_tokens: int = 0
    cached: int = 0

    @property
    def passed(self) -> int:
        return self.run.passed

    @property
    def total(self) -> int:
        return self.run.total

    def describe(self) -> str:
        return f"{self.passed} of {self.total} passed"


@dataclass
class Comparison:
    """Every arm's result on the same tasks with the same budget."""

    comparison_set: str
    suite: str
    arms: list[ArmResult]
    budget: Budget
    stopped: str = ""
    judge: Calibration | None = None
    paired: dict[str, Any] = field(default_factory=dict)
    # What the judge used while deciding this comparison's results (its calibration is counted apart).
    judge_tokens: int = 0

    @property
    def tokens(self) -> int:
        """Tokens counted against the monthly limit (local models left out)."""

        return (sum(arm.tokens - arm.local_tokens for arm in self.arms) + (self.judge.tokens if self.judge else 0)
                + self.judge_tokens)

    @property
    def local_tokens(self) -> int:
        return sum(arm.local_tokens for arm in self.arms)

    @property
    def small_sample(self) -> bool:
        return any(_small(arm.passed, arm.total) for arm in self.arms)

    @property
    def note(self) -> str:
        used = f"Testing used {_short_tokens(self.tokens)} tokens"
        if self.local_tokens:
            used += f" (and {_short_tokens(self.local_tokens)} on this computer)"
        parts = [used]
        if self.small_sample:
            parts.append(VARIANCE_NOTE.rstrip("."))
        text = " · ".join(parts) + "."
        return f"{text} {self.stopped}".strip() if self.stopped else text


def _short_tokens(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M".replace(".0M", "M")
    if tokens >= 1_000:
        return f"{round(tokens / 1_000)}K"
    return str(tokens)


def wilson_half_width(passed: int, total: int, z: float = 1.96) -> float:
    """Half the width of the 95% Wilson interval of a pass rate (1.0 with no tasks)."""

    if total <= 0:
        return 1.0
    rate = passed / total
    denominator = 1 + z * z / total
    spread = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
    return spread / denominator


def _small(passed: int, total: int) -> bool:
    return total < SMALL_SAMPLE or wilson_half_width(passed, total) > SMALL_SAMPLE_HALF_WIDTH


def sign_test(better: int, worse: int) -> float:
    """Two-sided exact sign test over the tasks where two arms differ."""

    n = better + worse
    if n == 0:
        return 1.0
    low = min(better, worse)
    tail = sum(math.comb(n, i) for i in range(low + 1)) / 2 ** n
    return min(1.0, 2 * tail)


# ── The model ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelReply:
    message: Any
    input_tokens: int
    output_tokens: int
    local: bool
    model_ref: str

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelClient(Protocol):
    """How evaluation asks a model; tests pass a fake."""

    def invoke(self, messages: Sequence[Any], *, tools: Sequence[Mapping[str, Any]] | None, model_ref: str | None,
               purpose: str) -> ModelReply: ...

    def is_local(self, model_ref: str | None) -> bool: ...


def estimate_tokens(messages: Sequence[Any], tools: Sequence[Mapping[str, Any]] | None = None) -> int:
    """An upper-leaning token estimate of one call: what is sent plus room for the answer."""

    chars = 0
    for message in messages:
        content = getattr(message, "content", message.get("content") if isinstance(message, Mapping) else "")
        chars += len(content if isinstance(content, str) else json.dumps(content, default=str)) + 16
        calls = getattr(message, "tool_calls", None)
        if calls:
            chars += len(json.dumps(calls, default=str))
    if tools:
        chars += len(json.dumps(list(tools), default=str))
    return math.ceil(chars / _CHARS_PER_TOKEN) + _OUTPUT_ALLOWANCE


class LedgerModelClient:
    """Learning's own model calls (E13): the default chat model at the lowest effort unless the
    arm names its model (D1), tagged ``learning_*`` in the usage ledger."""

    def __init__(self) -> None:
        self._models: dict[tuple[str, str], tuple[Any, str, str]] = {}

    def _resolve(self, model_ref: str | None, purpose: str) -> tuple[Any, str, str]:
        key = (str(model_ref or ""), purpose)
        cached = self._models.get(key)
        if cached is not None:
            return cached
        from row_bot import models
        from row_bot.providers import background
        from row_bot.providers.reasoning import canonical_reasoning_model_ref
        from row_bot.providers.resolution import resolve_provider_config

        ref = background.background_model_ref(purpose, task_model=model_ref)
        if not ref:
            raise EvaluationError("no_model", "choose a default model first")
        resolved = resolve_provider_config(ref, allow_legacy_local=True)
        canonical = canonical_reasoning_model_ref(resolved.provider_id, resolved.runtime_model)
        reasoning = background.background_reasoning(canonical)
        llm = models.get_llm_for(resolved.selection_ref, reasoning_plan=reasoning.plan)
        found = (llm, resolved.selection_ref, resolved.provider_id)
        self._models[key] = found
        return found

    def is_local(self, model_ref: str | None) -> bool:
        try:
            return is_local_provider(self._resolve(model_ref, PURPOSE)[2])
        except Exception:
            return False

    def invoke(self, messages: Sequence[Any], *, tools: Sequence[Mapping[str, Any]] | None, model_ref: str | None,
               purpose: str = PURPOSE) -> ModelReply:
        from row_bot.providers.usage_ledger import with_usage_purpose

        llm, selection_ref, provider = self._resolve(model_ref, purpose)
        runnable: Any = llm.bind_tools(list(tools)) if tools else llm
        if provider == "ollama" and getattr(llm, "reasoning", None) is True:
            runnable = runnable.bind(reasoning=False)
        message = with_usage_purpose(runnable, purpose).invoke(list(messages))
        usage = getattr(message, "usage_metadata", None) or {}
        estimate = estimate_tokens(messages, tools) - _OUTPUT_ALLOWANCE
        input_tokens = int(usage.get("input_tokens") or estimate)
        output_tokens = int(usage.get("output_tokens") or math.ceil(len(_text_of(message)) / _CHARS_PER_TOKEN))
        return ModelReply(message, input_tokens, output_tokens, is_local_provider(provider), selection_ref)


def _text_of(message: Any) -> str:
    from row_bot.providers.background import reply_text

    return reply_text(message)


def _ask(client: ModelClient, messages: Sequence[Any], tools: Sequence[Mapping[str, Any]] | None, arm: Arm,
         meter: TokenMeter | None, allowance: int) -> ModelReply:
    """One call within the task's allowance and the month's limit (checked before it is sent)."""

    estimate = estimate_tokens(messages, tools)
    if estimate > allowance:
        raise _BudgetSpent()
    if meter is not None and not meter.allows(estimate, local=client.is_local(arm.model_ref)):
        raise _LimitReached()
    reply = client.invoke(messages, tools=tools, model_ref=arm.model_ref, purpose=PURPOSE)
    if meter is not None:
        meter.charge(reply.tokens, local=reply.local)
    return reply


class _BudgetSpent(Exception):
    """The task's (or arm's) token allowance can't cover the next call."""


# ── Judges ──────────────────────────────────────────────────────────────────


class Judge(Protocol):
    """Decides whether a free-text answer does what the reference does. None: can't tell."""

    model_ref: str | None

    def judge(self, task: str, reference: str, candidate: str) -> bool | None: ...


class ModelJudge:
    """A judge model: Learning's default model at the lowest effort (or a named one)."""

    def __init__(self, client: ModelClient | None = None, model_ref: str | None = None,
                 meter: TokenMeter | None = None) -> None:
        self.client = client or LedgerModelClient()
        self.model_ref = model_ref
        self.meter = meter
        self.tokens = 0

    def judge(self, task: str, reference: str, candidate: str) -> bool | None:
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content="You grade answers. Reply with one word: pass or fail."),
            HumanMessage(content=f"Task:\n{task}\n\nA correct answer:\n{reference}\n\nAnswer to grade:\n{candidate}\n\n"
                                 "Does the answer to grade give the same result as the correct answer? "
                                 "Reply with only pass or fail."),
        ]
        estimate = estimate_tokens(messages)
        if self.meter is not None and not self.meter.allows(estimate, local=self.client.is_local(self.model_ref)):
            raise _LimitReached()
        reply = self.client.invoke(messages, tools=None, model_ref=self.model_ref, purpose=PURPOSE)
        self.tokens += reply.tokens
        if self.meter is not None:
            self.meter.charge(reply.tokens, local=reply.local)
        words = normalize(_text_of(reply.message)).split()
        if not words or words[0] not in {"pass", "fail"}:
            return None
        return words[0] == "pass"


@dataclass(frozen=True)
class CalibrationCase:
    task: str
    reference: str
    candidate: str
    executable: bool


def calibration_cases(tasks: Iterable[PackTask] | None = None) -> list[CalibrationCase]:
    """Answer tasks with an executable check: their reference (passes) and decoy (fails), with the
    executable verdict computed, not assumed."""

    found: list[CalibrationCase] = []
    for task in tasks if tasks is not None else visible_tasks():
        if task.verifier.kind not in ("answer", "json_answer"):
            continue
        for solution in (task.reference, task.decoy):
            verdict = verify(task, solve(task, solution))
            found.append(CalibrationCase(task.prompt, task.reference.answer, solution.answer, verdict.passed))
    return found


def calibrate(judge: Judge, cases: Sequence[CalibrationCase] | None = None) -> Calibration:
    """Measure a judge against executable checks; reported with every result it decides."""

    agreed = false_pass = false_fail = 0
    items = list(cases if cases is not None else calibration_cases())
    for case in items:
        verdict = judge.judge(case.task, case.reference, case.candidate)
        if verdict is case.executable:
            agreed += 1
        elif verdict:
            false_pass += 1
        else:
            false_fail += 1
    return Calibration(len(items), agreed, false_pass, false_fail, int(getattr(judge, "tokens", 0) or 0))


# ── Pack tasks: a bounded tool loop in a throwaway sandbox ──────────────────

_HARNESS = ("You are Row-Bot, doing a task in a test workspace. Use the tools to look at the files and apps; "
            "don't guess. When you are done, give the final answer exactly as the task asks.")


def _system_text(arm: Arm) -> str:
    if not arm.guidance:
        return _HARNESS
    return _HARNESS + "\n\nGuidance from earlier work:\n" + "\n".join(f"- {item}" for item in arm.guidance)


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[..., str]

    def schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


def _file_tools(box: Sandbox) -> list[_Tool]:
    def list_files(folder: str = ".") -> str:
        root = box.root if str(folder or ".").strip() in ("", ".") else box.path(folder)
        if not root.is_dir():
            return f"Error: {folder} is not a folder"
        names = sorted(item.relative_to(box.root).as_posix() + ("/" if item.is_dir() else "")
                       for item in root.iterdir() if not item.name.startswith("_row_bot_sandbox"))
        return "\n".join(names) or "(empty)"

    def read_file(path: str) -> str:
        text = box.read(path)
        return f"Error: {path} does not exist" if text is None else text

    def write_file(path: str, content: str) -> str:
        box.write(path, content)
        return f"Wrote {path}"

    string = {"type": "string"}
    return [
        _Tool("list_files", "List the files in a folder of the workspace ('.' is the top).",
              {"type": "object", "properties": {"folder": string}, "required": []}, list_files),
        _Tool("read_file", "Read a text file from the workspace.",
              {"type": "object", "properties": {"path": string}, "required": ["path"]}, read_file),
        _Tool("write_file", "Write a text file in the workspace (it replaces the file).",
              {"type": "object", "properties": {"path": string, "content": string}, "required": ["path", "content"]},
              write_file),
    ]


def _app_tools(apps: FakeApps, toolsets: Iterable[str]) -> list[_Tool]:
    return [_Tool(tool.name, tool.description, tool.parameters(), lambda _name=tool.name, **args: apps.call(_name, args))
            for tool in apps.tools(toolsets)]


def run_pack_task(task: PackTask, arm: Arm, *, client: ModelClient, budget: Budget = Budget(),
                  meter: TokenMeter | None = None, arm_left: int | None = None, backend: str = "auto") -> CaseResult:
    """Run one pack task for one arm in a fresh sandbox and verify what it left."""

    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    started = time.monotonic()
    steps = tokens = local_tokens = 0
    status: Status = "failed"
    detail = ""
    allowance = budget.task_tokens if arm_left is None else min(budget.task_tokens, max(0, arm_left))
    with Sandbox() as box:
        for name, content in task.files.items():
            box.write(name, content)
        apps = FakeApps(task.apps)
        tools = (_file_tools(box) if "files" in task.tools else []) + _app_tools(apps, task.tools)
        by_name = {tool.name: tool for tool in tools}
        schemas = [tool.schema() for tool in tools]
        messages: list[Any] = [SystemMessage(content=_system_text(arm)), HumanMessage(content=task.prompt)]
        answer = ""
        finished = False
        try:
            for _ in range(budget.max_steps or task.max_steps):
                reply = _ask(client, messages, schemas, arm, meter, allowance - tokens)
                steps += 1
                tokens += reply.tokens
                local_tokens += reply.tokens if reply.local else 0
                messages.append(reply.message)
                calls = list(getattr(reply.message, "tool_calls", None) or [])
                if not calls:
                    answer = _text_of(reply.message)
                    finished = True
                    break
                for call in calls:
                    tool = by_name.get(str(call.get("name") or ""))
                    try:
                        result = tool.run(**dict(call.get("args") or {})) if tool else f"Error: no tool {call.get('name')!r}"
                    except (SandboxError, TypeError, ValueError, OSError) as error:
                        result = f"Error: {error}"
                    messages.append(ToolMessage(content=str(result)[:20_000], tool_call_id=str(call.get("id") or "")))
        except _BudgetSpent:
            status, detail = "budget", "ran out of its token budget"
        except _LimitReached:
            raise
        except Exception as error:  # noqa: BLE001 - a provider failure is reported, not fatal
            status, detail = "error", f"the model call failed ({type(error).__name__})"
        if finished:
            try:
                verdict = verify(task, Outcome(answer, box.files(), apps.state()), backend=backend)
                status, detail = ("passed" if verdict.passed else "failed"), verdict.detail
            except SandboxError as error:
                status, detail = "error", f"the check could not run ({error})"
        elif status == "failed":
            status, detail = "budget", "ran out of steps"
    return CaseResult(task.id, "pack", task.kind_of_work, status, task.verifier.kind, tokens, local_tokens, steps,
                      round(time.monotonic() - started, 3), detail[:300])


# ── Replay: re-asking a captured decision at its prefix ─────────────────────


@dataclass(frozen=True)
class Action:
    """What a model decided: its tool calls (name and arguments) or its answer text."""

    tool_calls: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    text: str = ""

    @classmethod
    def from_message(cls, message: Any) -> Action:
        import re

        calls = tuple((str(call.get("name") or ""), dict(call.get("args") or {}))
                      for call in (getattr(message, "tool_calls", None) or []))
        text = re.sub(r"<think>.*?</think>", "", _text_of(message), flags=re.S).strip()
        return cls(calls, "" if calls else text)

    def to_dict(self) -> dict[str, Any]:
        return {"tool_calls": [{"name": name, "args": dict(args)} for name, args in self.tool_calls],
                "text": self.text}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Action:
        return cls(tuple((str(item["name"]), dict(item.get("args") or {})) for item in data.get("tool_calls") or ()),
                   str(data.get("text") or ""))


def _same_value(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        return normalize(expected) == normalize(actual)
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(expected), float(actual), rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(expected, (str, int, float)) and isinstance(actual, (str, int, float)):
        # An id or amount given as text on one side and as a number on the other ("12" and 12).
        return normalize(str(expected)) == normalize(str(actual))
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        return all(key in actual and _same_value(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(_same_value(a, b) for a, b in zip(expected, actual))
    return expected == actual


def compare_action(candidate: Action, reference: Action, *, check: Check = "exact_action",
                   judge: Judge | None = None, task_text: str = "") -> tuple[bool | None, str]:
    """Whether a new action matches the reference. None: nothing executable could decide.

    Tool calls match by name (as a set, for parallel calls) and, for ``exact_action``,
    by every argument the reference gave (extra arguments are allowed). An answer
    matches a short reference answer by its normalized text; a long one needs a
    calibrated judge.
    """

    if reference.tool_calls:
        if not candidate.tool_calls:
            return False, "answered instead of calling a tool"
        if sorted(name for name, _ in candidate.tool_calls) != sorted(name for name, _ in reference.tool_calls):
            return False, "called a different tool"
        if check == "tool_choice":
            return True, ""
        remaining = list(candidate.tool_calls)
        for name, args in reference.tool_calls:
            match = next((item for item in remaining if item[0] == name and _same_value(dict(args), dict(item[1]))),
                         None)
            if match is None:
                return False, f"called {name} with different arguments"
            remaining.remove(match)
        return True, ""
    if candidate.tool_calls:
        return False, "called a tool instead of answering"
    if check == "tool_choice":
        return True, ""
    if len(reference.text) <= SHORT_TEXT:
        same = normalize(candidate.text) == normalize(reference.text)
        return same, "" if same else "a different answer"
    if judge is None:
        return None, "a long answer and no calibrated judge"
    verdict = judge.judge(task_text, reference.text, candidate.text)
    return verdict, "" if verdict else ("the judge could not tell" if verdict is None else "the judge failed it")


@dataclass(frozen=True)
class CorrectedAction:
    """What the person said the model should have done (Teach, L1a), as an action."""

    tool_calls: tuple[tuple[str, Mapping[str, Any]], ...] = ()
    text: str = ""

    def action(self) -> Action:
        return Action(self.tool_calls, "" if self.tool_calls else self.text)


@dataclass(frozen=True)
class ReplayCase:
    """A decision ready to re-ask: its captured prefix and the action to compare against."""

    decision_id: str
    thread_id: str
    conversation_id: str
    created_at: float
    kind_of_work: str
    fidelity: str
    untrusted: bool
    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[Mapping[str, Any], ...] | None
    model_ref: str | None
    reference: Action
    reference_source: Literal["captured", "corrected"]
    check: Check
    text: str = ""

    def to_content(self) -> dict[str, Any]:
        return {"messages": [dict(item) for item in self.messages],
                "tools": None if self.tools is None else [dict(item) for item in self.tools],
                "model_ref": self.model_ref, "reference": self.reference.to_dict(),
                "reference_source": self.reference_source, "check": self.check, "text": self.text,
                "fidelity": self.fidelity, "untrusted": self.untrusted}

    @classmethod
    def from_content(cls, data: Mapping[str, Any]) -> ReplayCase:
        tools = data.get("tools")
        return cls(
            decision_id=str(data["id"]), thread_id=str(data.get("thread_id") or ""),
            conversation_id=str(data.get("conversation_id") or ""), created_at=float(data.get("created_at") or 0),
            kind_of_work=str(data.get("kind_of_work") or ""), fidelity=str(data.get("fidelity") or "exact"),
            untrusted=bool(data.get("untrusted")), messages=tuple(dict(item) for item in data.get("messages") or ()),
            tools=None if tools is None else tuple(dict(item) for item in tools),
            model_ref=data.get("model_ref"), reference=Action.from_dict(data.get("reference") or {}),
            reference_source=data.get("reference_source") or "captured", check=data.get("check") or "exact_action",
            text=str(data.get("text") or ""),
        )


@dataclass
class ReplaySet:
    cases: list[ReplayCase]
    skipped: Counter[str]


def _last_person_text(messages: Sequence[Mapping[str, Any]]) -> str:
    for item in reversed(messages):
        if item.get("type") == "human":
            content = (item.get("data") or {}).get("content")
            return content if isinstance(content, str) else json.dumps(content, default=str)
    return ""


def _signalled(store: Any) -> set[str]:
    return {str(item.decision_id) for item in store.find("signal", limit=100_000)
            if getattr(item, "decision_id", None) and getattr(item, "withdrawn_at", None) is None}


def collect_replay(store: Any | None = None, *, corrections: Mapping[str, CorrectedAction] | None = None,
                   include_untrusted: bool = False, decisions: Iterable[Decision] | None = None,
                   limit: int = 500, refresh: bool = True) -> ReplaySet:
    """The person's decisions that can be re-asked now, newest first, with why others can't.

    Decisions the audit vault holds are never offered; with ``refresh`` the vault takes
    its share of new decisions first, so a decision is never tested here and then hidden.
    """

    from langchain_core.messages import messages_from_dict

    from row_bot.learning import capture
    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    if refresh and gate.is_enabled(saved.root):
        refresh_audit(store=saved, asked_by="replay selection")
    corrections = dict(corrections or {})
    held = saved.held_out_ids()
    bad = _signalled(saved)
    episodes: dict[str, str] = {}
    skipped: Counter[str] = Counter()
    cases: list[ReplayCase] = []
    pool = list(decisions) if decisions is not None else saved.find(
        "decision", subkind="model_call", newest_first=True, limit=100_000)
    for decision in pool:
        if len(cases) >= limit:
            break
        if not isinstance(decision, Decision) or decision.kind != "model_call":
            continue
        if decision.status != "ok":
            skipped["not_ok"] += 1
            continue
        if decision.id in held:
            skipped["held_out"] += 1
            continue
        if decision.untrusted and not include_untrusted:
            skipped["untrusted"] += 1
            continue
        correction = corrections.get(decision.id)
        check: Check = "exact_action"
        if decision.fidelity == "partial":
            if correction is None:
                skipped["partial_without_correction"] += 1
                continue
            if not correction.tool_calls:
                skipped["partial_needs_exact_prefix"] += 1
                continue
            check = "tool_choice"
        if correction is not None:
            reference, source = correction.action(), "corrected"
        else:
            if decision.id in bad:
                skipped["known_bad"] += 1
                continue
            raw = saved.read_blob(decision.model_generated_blob or "")
            if raw is None:
                skipped["no_reference"] += 1
                continue
            try:
                reference = Action.from_message(messages_from_dict([json.loads(raw.decode("utf-8"))])[0])
            except Exception:  # noqa: BLE001 - an unreadable output is not a reference
                skipped["no_reference"] += 1
                continue
            source = "captured"
        request = capture.read_request(saved, decision)
        if request is None:
            skipped["purged"] += 1
            continue
        if request.get("reconstructed") and check == "exact_action":
            skipped["partial_needs_exact_prefix"] += 1
            continue
        if decision.episode_id not in episodes:
            episode = saved.get("episode", decision.episode_id)
            episodes[decision.episode_id] = str(getattr(episode, "kind_of_work", None) or "Your work")
        messages = tuple(request["messages"])
        cases.append(ReplayCase(
            decision.id, decision.thread_id, decision.conversation_id, decision.created_at,
            episodes[decision.episode_id], decision.fidelity, decision.untrusted, messages,
            None if request.get("tools") is None else tuple(request["tools"]), request.get("model_ref"),
            reference, source, check, _last_person_text(messages)[:4_000],
        ))
    return ReplaySet(cases, skipped)


def _with_guidance(messages: list[Any], arm: Arm) -> list[Any]:
    from langchain_core.messages import SystemMessage

    if not arm.guidance:
        return messages
    block = SystemMessage(content="Guidance from earlier work:\n" + "\n".join(f"- {item}" for item in arm.guidance))
    index = 1 if messages and getattr(messages[0], "type", "") == "system" else 0
    return messages[:index] + [block] + messages[index:]


def run_replay_case(case: ReplayCase, arm: Arm, *, client: ModelClient, budget: Budget = Budget(),
                    meter: TokenMeter | None = None, judge: Judge | None = None,
                    arm_left: int | None = None) -> CaseResult:
    """Re-ask one decision at its captured prefix and compare the new action."""

    from langchain_core.messages import messages_from_dict

    started = time.monotonic()
    allowance = budget.task_tokens if arm_left is None else min(budget.task_tokens, max(0, arm_left))
    messages = _with_guidance(list(messages_from_dict([dict(item) for item in case.messages])), arm)
    try:
        reply = _ask(client, messages, case.tools, arm, meter, allowance)
    except _BudgetSpent:
        return CaseResult(case.decision_id, "replay", case.kind_of_work, "budget", case.check,
                          detail="ran out of its token budget")
    except _LimitReached:
        raise
    except Exception as error:  # noqa: BLE001
        return CaseResult(case.decision_id, "replay", case.kind_of_work, "error", case.check,
                          detail=f"the model call failed ({type(error).__name__})")
    verdict, detail = compare_action(Action.from_message(reply.message), case.reference, check=case.check,
                                     judge=judge, task_text=case.text)
    status: Status = "unverified" if verdict is None else ("passed" if verdict else "failed")
    judged = not case.reference.tool_calls and case.check == "exact_action" and len(case.reference.text) > SHORT_TEXT
    return CaseResult(case.decision_id, "replay", case.kind_of_work, status, "judge" if judged else case.check,
                      reply.tokens, reply.tokens if reply.local else 0, 1, round(time.monotonic() - started, 3), detail)


# ── Pack runners and the main model's cache ─────────────────────────────────


class PackRunner(Protocol):
    """Runs one pack task for one arm somewhere else than the in-process tool loop (the real Agent
    runtime in an owned worker, ``eval_worker.RuntimeRunner``, O56). Raises :data:`LimitReached` to
    stop the comparison at the monthly limit. ``signature`` names how it runs (for the cache)."""

    signature: str

    def run(self, task: PackTask, arm: Arm, *, budget: Budget, meter: TokenMeter | None,
            arm_left: int | None) -> CaseResult: ...


CACHE_FILE = "arm_cache.json"
CACHE_DAYS = 30
_CACHE_ENTRIES = 20_000


class ArmCache:
    """Pack results of cached arms (the main model, O31), kept :data:`CACHE_DAYS` days.

    Keys are hashes of (model, the task's own content, metric version, budget, runner): a changed
    pack task, metric or budget is a new key. Values are counts only (status, tokens, steps, time),
    never a task or its id, so the file says nothing about the hidden tasks (E10).
    """

    def __init__(self, root: Any = None, *, clock: Callable[[], float] = time.time) -> None:
        self._root = root
        self.clock = clock
        self._entries: dict[str, dict[str, Any]] | None = None
        self._dirty = False
        self.hits = 0

    @property
    def path(self) -> Any:
        return gate.folder_path(self._root) / CACHE_FILE

    @staticmethod
    def key(arm: Arm, task: PackTask, budget: Budget, signature: str) -> str:
        import hashlib

        content = json.dumps(task.to_dict(), sort_keys=True, ensure_ascii=False)
        material = json.dumps([str(arm.model_ref or ""), hashlib.sha256(content.encode("utf-8")).hexdigest(),
                               METRIC_VERSION, budget.to_dict(), signature], sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._entries is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            entries = raw.get("entries") if isinstance(raw, dict) and raw.get("version") == 1 else None
            oldest = float(self.clock()) - CACHE_DAYS * 86_400
            self._entries = {key: value for key, value in (entries or {}).items()
                             if isinstance(value, dict) and float(value.get("at") or 0) >= oldest}
        return self._entries

    def get(self, key: str, task: PackTask) -> CaseResult | None:
        found = self._load().get(key)
        if found is None or found.get("status") not in ("passed", "failed", "budget"):
            return None
        self.hits += 1
        return CaseResult(task.id, "pack", task.kind_of_work, found["status"], str(found.get("check") or ""),
                          int(found.get("tokens") or 0), int(found.get("local_tokens") or 0),
                          int(found.get("steps") or 0), float(found.get("seconds") or 0.0), "kept from an earlier test")

    def put(self, key: str, case: CaseResult) -> None:
        if case.status not in ("passed", "failed", "budget"):
            return  # an error or a stop is never kept: it runs again next time
        entries = self._load()
        entries[key] = {"at": round(float(self.clock()), 3), "status": case.status, "check": case.check,
                        "tokens": case.tokens, "local_tokens": case.local_tokens, "steps": case.steps,
                        "seconds": case.seconds}
        if len(entries) > _CACHE_ENTRIES:
            for old in sorted(entries, key=lambda item: entries[item]["at"])[: len(entries) - _CACHE_ENTRIES]:
                entries.pop(old, None)
        self._dirty = True

    def save(self) -> None:
        if not self._dirty or self._entries is None:
            return
        from row_bot.learning import atomic

        try:
            atomic.write_text(self.path, json.dumps({"version": 1, "entries": self._entries}, sort_keys=True))
            self._dirty = False
        except OSError:
            logger.info("Learning couldn't keep its main-model results")


# ── Comparisons ─────────────────────────────────────────────────────────────


def _caller() -> str:
    frame = sys._getframe(2)
    return str(frame.f_globals.get("__name__", "")) if frame is not None else ""


def _by_kind(cases: Sequence[CaseResult]) -> dict[str, tuple[int, int]]:
    table: dict[str, list[int]] = {}
    for case in cases:
        if case.counted:
            row = table.setdefault(case.kind_of_work or "Other", [0, 0])
            row[0] += int(case.passed)
            row[1] += 1
    return {kind: (row[0], row[1]) for kind, row in sorted(table.items())}


def _paired(first: Sequence[CaseResult], second: Sequence[CaseResult]) -> dict[str, Any]:
    results = {case.task_id: case for case in second if case.counted}
    better = worse = same = 0
    for case in first:
        other = results.get(case.task_id)
        if other is None or not case.counted:
            continue
        if other.passed and not case.passed:
            better += 1
        elif case.passed and not other.passed:
            worse += 1
        else:
            same += 1
    return {"better": better, "worse": worse, "same": same, "p_value": round(sign_test(better, worse), 4)}


def _save(store: Any, runs: Sequence[tuple[TestRun, dict[str, Any]]],
          fixtures: Sequence[tuple[TaskFixture, bytes, bytes | None]]) -> list[TestRun]:
    """Put the runs' result blobs and records (and the fixtures that were run) in the store."""

    from row_bot.learning.store import LearningStoreError

    saved: list[TestRun] = []
    try:
        blobs = [json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8") for _, body in runs]
        hashes = store.put_blobs(blobs)
        saved = [replace(run, results_blob=sha) for (run, _), sha in zip(runs, hashes)]
        fixture_records = []
        for fixture, input_bytes, expected_bytes in fixtures:
            store.put_blobs([input_bytes] + ([expected_bytes] if expected_bytes else []))
            fixture_records.append(fixture)
        store.put_many([*fixture_records, *saved])
    except LearningStoreError as error:
        logger.info("Learning did not save its test results (%s)", error.code)
        return [run for run, _ in runs]
    return saved


def fixture_for(task: PackTask, *, created_at: float,
                origin: Literal["pack", "practice"] = "pack") -> tuple[TaskFixture, bytes, bytes]:
    """A visible pack task (or a practice task) as a TaskFixture (its input and expected blobs alongside)."""

    import hashlib

    data = json.dumps(task.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    expected = json.dumps(task.reference.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    fixture = TaskFixture(
        id=f"fix_{task.id}", origin=origin, input_blob=hashlib.sha256(data).hexdigest(),
        verifier=task.verifier.kind, created_at=created_at, pack=task.pack, kind_of_work=task.kind_of_work,
        expected_blob=hashlib.sha256(expected).hexdigest(),
    )
    return fixture, data, expected


def replay_fixture_for(case: ReplayCase, *, created_at: float) -> tuple[TaskFixture, bytes, bytes]:
    """A replayed decision as a TaskFixture: it belongs to its chat, so deleting the chat removes it."""

    import hashlib

    data = json.dumps(case.to_content(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    expected = json.dumps(case.reference.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    fixture = TaskFixture(
        id=f"fix_{case.decision_id}", origin="replay", input_blob=hashlib.sha256(data).hexdigest(),
        verifier=case.check, created_at=created_at, kind_of_work=case.kind_of_work,
        expected_blob=hashlib.sha256(expected).hexdigest(), source_decision_id=case.decision_id,
        thread_id=case.thread_id, conversation_id=case.conversation_id or case.thread_id,
    )
    return fixture, data, expected


def compare(arms: Sequence[Arm], *, tasks: Sequence[PackTask] = (), replay: Sequence[ReplayCase] = (),
            client: ModelClient | None = None, judge: Judge | None = None, calibration: Calibration | None = None,
            budget: Budget = Budget(), meter: TokenMeter | None = None, store: Any | None = None,
            save: bool = True, clock: Callable[[], float] = time.time, backend: str = "auto",
            suite: Literal["replay", "pack", "practice"] | None = None, runner: PackRunner | None = None,
            cache: ArmCache | None = None, on_case: Callable[[str, CaseResult], None] | None = None) -> Comparison:
    """Run every arm on the same pack tasks and replayed decisions, with the same budget.

    The first arm is the baseline the others are compared with. A judge is used only
    with its calibration and only when that calibration is reliable. ``runner`` runs pack tasks
    through the real Agent runtime (O56) instead of the in-process tool loop; ``cache`` keeps
    and reuses the results of arms marked ``cache`` (O31); ``on_case(arm, case)`` follows progress.
    """

    return _compare(arms, list(tasks), list(replay), client=client, judge=judge, calibration=calibration,
                    budget=budget, meter=meter, store=store, save=save, clock=clock, backend=backend,
                    suite=suite or ("replay" if replay and not tasks else "pack"), aggregate_only=False,
                    runner=runner, cache=cache, on_case=on_case)


def _compare(arms: Sequence[Arm], tasks: list[PackTask], replay: list[ReplayCase], *, client: ModelClient | None,
             judge: Judge | None, calibration: Calibration | None, budget: Budget, meter: TokenMeter | None,
             store: Any | None, save: bool, clock: Callable[[], float], backend: str, suite: str,
             aggregate_only: bool, runner: PackRunner | None = None, cache: ArmCache | None = None,
             on_case: Callable[[str, CaseResult], None] | None = None) -> Comparison:
    if not arms:
        raise EvaluationError("no_arms")
    if len({arm.name for arm in arms}) != len(arms):
        raise EvaluationError("arm_names_repeat")
    if judge is not None:
        if calibration is None or not calibration.reliable:
            judge = None  # an uncalibrated or unreliable judge decides nothing
        elif judge.model_ref and any(arm.model_ref == judge.model_ref and arm.subject_kind == "release"
                                     for arm in arms):
            raise EvaluationError("judge_is_a_subject", "a model never judges its own version (D21)")
    client = client or LedgerModelClient()
    if meter is None:
        meter = TokenMeter.for_month()
    judge_before = int(getattr(judge, "tokens", 0) or 0)
    started = float(clock())
    results: dict[str, list[CaseResult]] = {arm.name: [] for arm in arms}
    spent: dict[str, int] = {arm.name: 0 for arm in arms}
    cached_count: dict[str, int] = {arm.name: 0 for arm in arms}
    signature = str(getattr(runner, "signature", "") or "in-process-1")
    stopped = ""
    items: list[PackTask | ReplayCase] = [*tasks, *replay]
    for index, item in enumerate(items):
        round_results: dict[str, CaseResult] = {}
        try:
            for arm in arms:
                left = None if budget.arm_tokens is None else budget.arm_tokens - spent[arm.name]
                if left is not None and left <= 0:
                    case_id = item.id if isinstance(item, PackTask) else item.decision_id
                    kind = item.kind_of_work
                    round_results[arm.name] = CaseResult(case_id, "pack" if isinstance(item, PackTask) else "replay",
                                                         kind, "budget", "budget", detail="the arm's budget was spent")
                    continue
                key = (ArmCache.key(arm, item, budget, signature)
                       if cache is not None and arm.cache and isinstance(item, PackTask) else None)
                kept = cache.get(key, item) if key is not None and cache is not None else None
                if kept is not None:
                    round_results[arm.name] = kept
                    cached_count[arm.name] += 1
                    if on_case is not None:
                        on_case(arm.name, kept)
                    continue  # nothing spent: the result is from an earlier test (O31)
                if isinstance(item, PackTask) and runner is not None:
                    result = runner.run(item, arm, budget=budget, meter=meter, arm_left=left)
                elif isinstance(item, PackTask):
                    result = run_pack_task(item, arm, client=client, budget=budget, meter=meter, arm_left=left,
                                           backend=backend)
                else:
                    result = run_replay_case(item, arm, client=client, budget=budget, meter=meter, judge=judge,
                                             arm_left=left)
                if key is not None and cache is not None:
                    cache.put(key, result)
                round_results[arm.name] = result
                spent[arm.name] += result.tokens
                if on_case is not None:
                    on_case(arm.name, result)
        except _LimitReached:
            stopped = meter.note()
            for later in items[index:]:
                case_id = later.id if isinstance(later, PackTask) else later.decision_id
                for arm in arms:
                    results[arm.name].append(CaseResult(case_id, "pack" if isinstance(later, PackTask) else "replay",
                                                        later.kind_of_work, "not_run", "none", detail=stopped))
            break
        for arm in arms:
            results[arm.name].append(round_results[arm.name])
    ended = max(float(clock()), started)
    comparison_set = new_id("cmp")
    packs = sorted({task.pack for task in tasks})
    # An audit run names no conversation: which chats the vault holds tasks from is not told (E10).
    threads = [] if aggregate_only else sorted({case.conversation_id or case.thread_id for case in replay
                                                if case.thread_id})
    calibration_dict = calibration.to_dict() if calibration is not None else None
    arm_results: list[ArmResult] = []
    runs: list[tuple[TestRun, dict[str, Any]]] = []
    baseline_id: str | None = None
    for arm in arms:
        cases = results[arm.name]
        passed = sum(case.passed for case in cases)
        counted = [case for case in cases if case.counted]
        local = sum(case.local_tokens for case in cases)
        run = TestRun(
            id=new_id("run"), subject_kind=arm.subject_kind, suite=suite, started_at=started,  # type: ignore[arg-type]
            subject_id=arm.subject_id, baseline_id=baseline_id, pack=packs[0] if len(packs) == 1 and not replay else None,
            model_ref=arm.model_ref, status="stopped" if stopped else "completed", passed=passed,
            failed=len(counted) - passed, total=len(counted),
            budget_tokens=budget.arm_tokens if budget.arm_tokens is not None else budget.task_tokens * len(items),
            cost=Cost(tokens=spent[arm.name] - local), comparison_set=comparison_set, ended_at=ended,
            source_thread_ids=tuple(threads),
        )
        baseline_id = baseline_id or run.id
        by_kind = _by_kind(cases)
        body: dict[str, Any] = {
            "format": RESULTS_FORMAT, "version": 1, "metric_version": METRIC_VERSION, "arm": arm.name,
            "guidance": len(arm.guidance), "suite": suite, "budget": budget.to_dict(), "tokens": spent[arm.name],
            "local_tokens": local, "by_kind": {k: list(v) for k, v in by_kind.items()},
            "statuses": dict(Counter(case.status for case in cases)), "small_sample": _small(passed, len(counted)),
            "stopped": stopped, "judge": calibration_dict,
        }
        if not aggregate_only:
            body["cases"] = [{"task": case.task_id, "kind_of_work": case.kind_of_work, "status": case.status,
                              "check": case.check, "tokens": case.tokens, "steps": case.steps,
                              "seconds": case.seconds, "detail": case.detail} for case in cases]
        runs.append((run, body))
        ran = [case for case in cases if case.status != "not_run"]
        arm_results.append(ArmResult(
            arm, run, [] if aggregate_only else cases, spent[arm.name], local, arm.model_ref, by_kind,
            ran=len(ran), seconds=round(sum(case.seconds for case in ran), 3),
            measured_tokens=sum(case.tokens for case in ran),
            measured_cloud_tokens=sum(max(0, case.tokens - case.local_tokens) for case in ran),
            cached=cached_count[arm.name]))
    if cache is not None:
        cache.save()
    if save:
        from row_bot.learning.store import LearningStore

        saved_store = store if store is not None else LearningStore()
        fixtures: list[tuple[TaskFixture, bytes, bytes | None]] = []
        if not aggregate_only:
            origin = "practice" if suite == "practice" else "pack"
            fixtures.extend(fixture_for(task, created_at=started, origin=origin) for task in tasks)
            fixtures.extend(replay_fixture_for(case, created_at=started) for case in replay)
        saved_runs = _save(saved_store, runs, fixtures)
        for arm_result, run in zip(arm_results, saved_runs):
            arm_result.run = run
    paired = _paired(results[arms[0].name], results[arms[1].name]) if len(arms) == 2 else {}
    judge_used = int(getattr(judge, "tokens", 0) or 0) - judge_before if judge is not None else 0
    return Comparison(comparison_set, suite, arm_results, budget, stopped, calibration, paired, judge_used)


# ── The audit vault: only this module reads it ──────────────────────────────


def _vault(store: Any | None = None) -> Any:
    root = getattr(store, "root", None)
    return audit.AuditVault(root)


def _eligible_for_audit(store: Any, horizon: float) -> list[audit.ReplayCandidate]:
    newer = [decision for decision in store.find("decision", subkind="model_call", newest_first=True, limit=100_000)
             if decision.created_at > horizon]
    found = collect_replay(store, refresh=False, decisions=newer, limit=100_000)
    return [audit.ReplayCandidate(case.decision_id, case.created_at, case.thread_id,
                                  case.conversation_id or case.thread_id, case.kind_of_work, case.to_content())
            for case in found.cases if case.reference_source == "captured" and case.check == "exact_action"]


def refresh_audit(*, store: Any | None = None, asked_by: str = "") -> dict[str, int]:
    """Move the vault's share of new eligible decisions into it (by time), and hold them out
    of everything else. Returns counts only."""

    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    if not gate.is_enabled(saved.root) and not audit.vault_exists(saved.root):
        raise EvaluationError("learning_off")
    vault = _vault(saved)
    report = vault.refresh(_eligible_for_audit(saved, vault.horizon()), asked_by=f"{_caller()}: {asked_by}".strip(": "))
    if report.added:
        by_id = {decision.id: decision for decision in saved.find("decision", subkind="model_call", limit=100_000)
                 if decision.id in set(report.added)}
        saved.add_marks([("decision", item, by_id[item].conversation_id if item in by_id else "")
                         for item in report.added], MARK_HELD_OUT)
    return {"considered": report.considered, "added": len(report.added), "retired": report.retired,
            "held": report.held}


def audit_check(arms: Sequence[Arm], *, asked_by: str, client: ModelClient | None = None,
                judge: Judge | None = None, calibration: Calibration | None = None, budget: Budget = Budget(),
                meter: TokenMeter | None = None, packs: Iterable[str] | None = None, include_replay: bool = True,
                store: Any | None = None, save: bool = True, clock: Callable[[], float] = time.time,
                backend: str = "auto", runner: PackRunner | None = None, cache: ArmCache | None = None,
                verifiers: Iterable[str] | None = None,
                on_progress: Callable[[int, int], None] | None = None) -> Comparison:
    """Run the arms on the vault's hidden tasks. Only aggregates come back: counts per kind of
    work and tokens per arm; no task, id or text (E10).

    ``verifiers`` leaves out hidden tasks checked any other way (``code_tests`` need a container
    for automatic runs, O21); ``on_progress(done, total)`` counts tasks, nothing more."""

    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    if not gate.is_enabled(saved.root) and not audit.vault_exists(saved.root):
        raise EvaluationError("learning_off")
    who = f"{_caller()}: {asked_by}".strip(": ")
    vault = _vault(saved)
    allowed = None if verifiers is None else set(verifiers)
    tasks = [task for task in vault.hidden_tasks(asked_by=who, packs=packs)
             if allowed is None or task.verifier.kind in allowed]
    replay = [ReplayCase.from_content(item) for item in vault.replay_tasks(asked_by=who)] if include_replay else []
    total = (len(tasks) + len(replay)) * len(arms)
    done = [0]

    def counted(_arm: str, _case: CaseResult) -> None:
        done[0] += 1
        if on_progress is not None:
            on_progress(done[0], total)

    return _compare(arms, tasks, replay, client=client, judge=judge, calibration=calibration, budget=budget,
                    meter=meter, store=saved, save=save, clock=clock, backend=backend, suite="audit",
                    aggregate_only=True, runner=runner, cache=cache, on_case=counted)


def audit_size(*, asked_by: str, store: Any | None = None, packs: Iterable[str] | None = None,
               verifiers: Iterable[str] | None = None, include_replay: bool = True) -> int:
    """How many hidden tasks an :func:`audit_check` with the same choices would run (a count only)."""

    allowed = None if verifiers is None else set(verifiers)
    vault = _vault(store)
    who = f"{_caller()}: {asked_by}".strip(": ")
    count = sum(1 for task in vault.hidden_tasks(asked_by=who, packs=packs)
                if allowed is None or task.verifier.kind in allowed)
    return count + (len(vault.replay_tasks(asked_by=who)) if include_replay else 0)


def is_novel(candidate: PackTask | str, *, asked_by: str, store: Any | None = None) -> bool:
    """Whether a proposed practice task is far enough from every hidden test (L4b). Yes or no only."""

    text = candidate.text() if isinstance(candidate, PackTask) else str(candidate)
    return bool(_vault(store).is_novel(text, asked_by=f"{_caller()}: {asked_by}".strip(": ")))


def audit_summary(*, asked_by: str, store: Any | None = None) -> dict[str, int]:
    """How many hidden tasks the vault holds, by kind (counts only)."""

    return dict(_vault(store).summary(asked_by=f"{_caller()}: {asked_by}".strip(": ")))


@dataclass(frozen=True)
class AuditLook:
    """One entry of the vault's look log: who asked, when, and what kind of answer they got."""

    at: float
    asked_by: str
    caller: str
    kind: str
    answer: str


def audit_looks(*, limit: int = 200, store: Any | None = None) -> list[AuditLook]:
    """The vault's look log, newest first (no task content)."""

    return [AuditLook(look.at, look.asked_by, look.caller, look.kind, look.answer)
            for look in _vault(store).looks(limit=limit)]


def audit_selfcheck(*, asked_by: str, store: Any | None = None, backend: str = "auto",
                    include_code: bool = True) -> dict[str, int]:
    """Every hidden task's verifier passes its reference and fails its decoy: counts only.
    ``include_code=False`` leaves out the coding tasks (their checks run processes)."""

    tasks = [task for task in _vault(store).hidden_tasks(asked_by=f"{_caller()}: {asked_by}".strip(": "))
             if include_code or task.verifier.kind != "code_tests"]
    references = sum(verify(task, solve(task, task.reference), backend=backend).passed for task in tasks)
    decoys = sum(not verify(task, solve(task, task.decoy), backend=backend).passed for task in tasks)
    return {"tasks": len(tasks), "references_pass": references, "decoys_fail": decoys}


__all__ = [
    "Action",
    "Arm",
    "ArmCache",
    "ArmResult",
    "AuditLook",
    "Budget",
    "CACHE_DAYS",
    "LimitReached",
    "PackRunner",
    "audit_size",
    "CalibrationCase",
    "Calibration",
    "CaseResult",
    "Comparison",
    "CorrectedAction",
    "EvaluationError",
    "Judge",
    "LedgerModelClient",
    "MARK_HELD_OUT",
    "METRIC_VERSION",
    "ModelClient",
    "ModelJudge",
    "ModelReply",
    "ReplayCase",
    "ReplaySet",
    "VARIANCE_NOTE",
    "audit_check",
    "audit_looks",
    "audit_selfcheck",
    "audit_summary",
    "calibrate",
    "calibration_cases",
    "collect_replay",
    "compare",
    "compare_action",
    "estimate_tokens",
    "fixture_for",
    "is_novel",
    "refresh_audit",
    "replay_fixture_for",
    "run_pack_task",
    "run_replay_case",
    "sign_test",
    "wilson_half_width",
]
