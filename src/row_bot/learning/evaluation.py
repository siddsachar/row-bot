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
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from row_bot import learning as gate
from row_bot.learning import audit
from row_bot.learning.calls import (
    LedgerModelClient,
    ModelClient,
    ModelReply,
    estimate_tokens,
    text_of,
)
from row_bot.learning.contracts import Cost, Decision, TaskFixture, TestRun, new_id
from row_bot.learning.packs import PackTask, visible_tasks
from row_bot.learning.packs.fake_apps import FakeApps
from row_bot.learning.packs.verify import GUARD_DETAIL, Outcome, normalize, solve, verify
from row_bot.learning.sandbox import Sandbox, SandboxError
from row_bot.learning.spending import TokenMeter
from row_bot.learning.store import MARK_HELD_OUT

logger = logging.getLogger(__name__)

# ── Fixed points (E4): metric definitions no strategy can change ────────────

# 2 (L2v): an answer check also passes a reply that states the expected value in a sentence without
# giving the task's known wrong value first (``packs.verify``), and the real-runtime worker checks
# the reply's last message. Results under 1 are never mixed with these (the arm cache keys on it).
# 3 (L4a, O67): a final message that offers two or more distinct answers of the expected kind fails
# (``packs.verify.offers_several``). Each result records whether it failed only by that guard
# (``CaseResult.guarded``), so the freeze also reports version 2's reading (§7's sensitivity analysis)
# without running anything twice; results under different versions are never mixed.
METRIC_VERSION = 3
#: The version before the guard, reported beside the primary one (pre-registration §7).
PREVIOUS_METRIC_VERSION = 2
# "21 of 24 passed": passed over passed + failed. A task that ran out of its budget or
# steps failed; one the provider couldn't run (an error) or no check could decide
# (unverified) is reported but left out of the denominator.
# O70 narrows "the provider couldn't run it": a runtime's rejection of the model's own malformed
# tool call (Ollama's tool-call parser answering 500) is the model's output, so the task failed
# (cause ``malformed_call``) and counts. Real outages (refused connections, timeouts, other server
# errors) stay outside.
MALFORMED_CALL = "malformed_call"
MALFORMED_DETAIL = "the model's tool call was malformed (the runtime refused it)"
MALFORMED_CALL_MARKERS = ("xml syntax error", "error parsing tool call", "failed to parse tool call",
                          "tool call could not be parsed", "invalid tool call format")
SMALL_SAMPLE = 30
SMALL_SAMPLE_HALF_WIDTH = 0.15
VARIANCE_NOTE = "Small test, so expect some variation."
JUDGE_MIN_CASES = 10
JUDGE_MIN_AGREEMENT = 0.85
SHORT_TEXT = 160
RESULTS_FORMAT = "row_bot.learning.test_results"
PURPOSE = "learning_test"

Status = Literal["passed", "failed", "budget", "error", "unverified", "skipped", "not_run"]
# ``stated_answer`` (L3b): a held-out Teach whose person said what the answer should have been, with no redo
# to take a corrected action from; the re-asked reply passes when it states that answer (``compare_action``).
Check = Literal["exact_action", "tool_choice", "stated_answer"]


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
    # The context a local model serves at (a personal model: its recipe's, 32K on the 8 GB tier).
    # Replayed decisions reach it in that serving shape (O71, pre-registration §4): Row-Bot's
    # small-context prompt and tools, deferred tools behind ``tool_search`` / ``tool_invoke``. None:
    # asked as captured (a cloud main model is served as it was captured).
    serve_context: int | None = None
    # What the real runtime (the evaluation worker, O56) runs this arm with (L3b): lessons as the
    # memories recall finds, (title, text); ways of working as the turn's overlay, (slot, text). The
    # in-process tool loop and replay use ``guidance`` instead.
    lessons: tuple[tuple[str, str], ...] = ()
    ways: tuple[tuple[str, str], ...] = ()
    # Whether the arm is served the page's picture after a browser or computer action (A8). A text-only
    # arm (the personal model and its starting model) is asked without it, as training leaves it out; the
    # main model, asked as captured, gets it as it was sent.
    sees_images: bool = False


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
    # Why a task that ran didn't pass, when the runner can tell (``eval_worker.CAUSES``): a code, never task text.
    cause: str = ""
    # It failed only because its final message offered more than one answer (METRIC_VERSION 3's guard).
    guarded: bool = False

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def passed_under(self, version: int) -> bool:
        """Whether it passed under ``version`` of the checker (2: before the commits-to-one-answer guard)."""

        return self.passed or (version < METRIC_VERSION and self.status == "failed" and self.guarded)

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
    # Why tasks didn't pass (cause code → count), for audit runs too: codes only, never a task (E10).
    causes: dict[str, int] = field(default_factory=dict)

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
    # How many times each problem ran per arm, and per arm how many repeated problems ran the same
    # every time (``problems``, ``identical``: greedy decoding shows up here).
    repeats: int = 1
    repeated: dict[str, dict[str, int]] = field(default_factory=dict)
    # The pre-registered paired analysis of each arm against the first (O88): aggregates only, so an
    # audit run's per-problem results never leave this module (E10).
    analysis: dict[str, Any] = field(default_factory=dict)
    # Each problem's cluster (its source chat; a pack task is its own), kept only where the problems
    # aren't hidden, so a later audit run can analyse visible and hidden problems together.
    clusters: dict[str, str] = field(default_factory=dict, repr=False)

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


def is_malformed_call(error: object) -> bool:
    """Whether a failed model call is the runtime refusing the model's own malformed tool call (O70)."""

    text = str(error or "").lower()
    return any(marker in text for marker in MALFORMED_CALL_MARKERS)


def sign_test(better: int, worse: int) -> float:
    """Two-sided exact sign test over the tasks where two arms differ."""

    n = better + worse
    if n == 0:
        return 1.0
    low = min(better, worse)
    tail = sum(math.comb(n, i) for i in range(low + 1)) / 2 ** n
    return min(1.0, 2 * tail)


# ── The model ───────────────────────────────────────────────────────────────


def _text_of(message: Any) -> str:
    return text_of(message)


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
    detail = cause = ""
    guarded = False
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
            if is_malformed_call(error):
                status, detail, cause = "failed", MALFORMED_DETAIL, MALFORMED_CALL  # the model's own output (O70)
            else:
                status, detail = "error", f"the model call failed ({type(error).__name__})"
        if finished:
            try:
                verdict = verify(task, Outcome(answer, box.files(), apps.state()), backend=backend)
                status, detail, guarded = ("passed" if verdict.passed else "failed"), verdict.detail, verdict.guarded
            except SandboxError as error:
                status, detail = "error", f"the check could not run ({error})"
        elif status == "failed" and not cause:
            status, detail = "budget", "ran out of steps"
    return CaseResult(task.id, "pack", task.kind_of_work, status, task.verifier.kind, tokens, local_tokens, steps,
                      round(time.monotonic() - started, 3), detail[:300], cause, guarded)


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


_KEY_TOKEN = re.compile(r"\b(?=[\w.,:/-]*\d)[\w.,:/-]+\b|\b[A-Z]{2,}[\w-]*\b")


def _states(answer: str, reply: str) -> tuple[bool | None, str]:
    """Whether ``reply`` states the person's ``answer`` without a judge (``stated_answer``)."""

    wanted, said = normalize(answer), normalize(reply)
    if not wanted:
        return None, "no answer to compare with"
    if wanted in said:
        return True, ""
    keys = {normalize(token).strip(".,:") for token in _KEY_TOKEN.findall(str(answer or ""))}
    keys.discard("")
    if not keys:
        return None, "an answer in words and no calibrated judge"
    missing = sorted(key for key in keys if key not in said)
    return (True, "") if not missing else (False, "a different answer")


def compare_action(candidate: Action, reference: Action, *, check: Check = "exact_action",
                   judge: Judge | None = None, task_text: str = "") -> tuple[bool | None, str]:
    """Whether a new action matches the reference. None: nothing executable could decide.

    Tool calls match by name (as a set, for parallel calls) and, for ``exact_action``,
    by every argument the reference gave (extra arguments are allowed). An answer
    matches a short reference answer by its normalized text; a long one needs a
    calibrated judge.

    ``stated_answer`` (a Teach's own answer as the reference): the reply passes when it states it. A
    calibrated judge decides when there is one; otherwise the reply must hold the answer's words, or
    every number and identifier the answer gives. A reply that calls a tool first can't be followed by
    replay, so no check decides it.
    """

    if check == "stated_answer":
        if candidate.tool_calls:
            return None, "called a tool first, which replay can't follow"
        if judge is not None:
            verdict = judge.judge(task_text, reference.text, candidate.text)
        else:
            verdict, detail = _states(reference.text, candidate.text)
            if not verdict:
                return verdict, detail
        if verdict and _offers_alternatives(candidate.text, reference.text):
            return False, GUARD_DETAIL
        return verdict, "" if verdict else ("the judge could not tell" if verdict is None else "the judge failed it")

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
    if verdict and _offers_alternatives(candidate.text, reference.text):
        return False, GUARD_DETAIL
    return verdict, "" if verdict else ("the judge could not tell" if verdict is None else "the judge failed it")


def _offers_alternatives(reply: str, reference: str) -> bool:
    """A reply that offers a reference's number, date or identifier as one of several joined as a choice ("85 or
    60 EUR") doesn't commit to one answer (METRIC_VERSION 3): the guard on answers that are judged or compared
    with a person's stated answer."""

    from row_bot.learning.packs.verify import offers_several

    keys = [(float(value.replace(",", "")), "number") for value in _NUMBER_KEY.findall(_unkeyed(reference))]
    keys += [(token, "exact") for token in _DATE_OR_ID.findall(str(reference or ""))]
    return any(offers_several(reply, value, match=match, hedges=False) for value, match in keys)


_DATE_OR_ID = re.compile(r"(?<![\w-])(?:\d{4}-\d{1,2}-\d{1,2}|[A-Za-z]{1,8}[-_]?\d+(?:[-_][A-Za-z0-9]+)*)(?![\w-])")
_NUMBER_KEY = re.compile(r"(?<![\w.,-])\d[\d,]*(?:\.\d+)?(?![\w])")


def _unkeyed(text: str) -> str:
    return _DATE_OR_ID.sub(" ", str(text or ""))


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

    @property
    def has_picture(self) -> bool:
        """Whether its prefix carries the page's picture after a browser or computer action (A8)."""

        from row_bot.learning.pictures import is_page_picture

        return any(is_page_picture(item) for item in self.messages)

    @classmethod
    def from_content(cls, data: Mapping[str, Any]) -> ReplayCase:
        tools = data.get("tools")
        case = cls(
            decision_id=str(data["id"]), thread_id=str(data.get("thread_id") or ""),
            conversation_id=str(data.get("conversation_id") or ""), created_at=float(data.get("created_at") or 0),
            kind_of_work=str(data.get("kind_of_work") or ""), fidelity=str(data.get("fidelity") or "exact"),
            untrusted=bool(data.get("untrusted")), messages=tuple(dict(item) for item in data.get("messages") or ()),
            tools=None if tools is None else tuple(dict(item) for item in tools),
            model_ref=data.get("model_ref"), reference=Action.from_dict(data.get("reference") or {}),
            reference_source=data.get("reference_source") or "captured", check=data.get("check") or "exact_action",
            text=str(data.get("text") or ""),
        )
        # A8: a task held before this rule whose prefix carries a page picture took the picture's JSON as its
        # text; it is read with the person's words.
        return replace(case, text=_last_person_text(case.messages)[:4_000]) if case.has_picture else case


@dataclass
class ReplaySet:
    cases: list[ReplayCase]
    skipped: Counter[str]


def _last_person_text(messages: Sequence[Mapping[str, Any]]) -> str:
    """The text of the person's last message: its text parts, never image data, and never the page's
    picture after a browser or computer action (A8), nor a note Row-Bot sent the model itself."""

    from row_bot.learning.corrections import is_row_bot_note
    from row_bot.learning.pictures import is_page_picture

    for item in reversed(messages):
        if item.get("type") != "human" or is_page_picture(item) or is_row_bot_note(item):
            continue
        content = (item.get("data") or {}).get("content")
        if isinstance(content, str):
            return content
        parts: list[str] = []
        for part in content if isinstance(content, list) else ():
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, Mapping) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "\n".join(part for part in parts if part)
    return ""


def _signalled(store: Any) -> set[str]:
    """Decisions the person pushed back on, and every decision of a turn they did (a corrected, stopped
    or retried turn's steps aren't uncorrected work: none is a captured reference)."""

    signals = [item for item in store.find("signal", limit=100_000) if getattr(item, "withdrawn_at", None) is None]
    found = {str(item.decision_id) for item in signals if getattr(item, "decision_id", None)}
    episodes = {str(item.episode_id) for item in signals if getattr(item, "episode_id", None)}
    if episodes:
        found |= {item.id for item in store.find("decision", limit=100_000) if item.episode_id in episodes}
    return found


def collect_replay(store: Any | None = None, *, corrections: Mapping[str, CorrectedAction] | None = None,
                   include_untrusted: bool = False, decisions: Iterable[Decision] | None = None,
                   limit: int = 500, refresh: bool = True) -> ReplaySet:
    """The person's decisions that can be re-asked now, newest first, with why others can't.

    Decisions the audit vault holds are never offered; with ``refresh`` the vault takes
    its share of new decisions first, so a decision is never tested here and then hidden.
    """

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
        if decision.id in held and decision.status == "ok":
            skipped["held_out"] += 1
            continue
        found = _case_for(saved, decision, corrections.get(decision.id), include_untrusted=include_untrusted,
                          bad=bad, episodes=episodes)
        if isinstance(found, str):
            skipped[found] += 1
        else:
            cases.append(found)
    return ReplaySet(cases, skipped)


def _case_for(saved: Any, decision: Decision, correction: CorrectedAction | None, *, include_untrusted: bool,
              bad: set[str], episodes: dict[str, str], case_id: str | None = None) -> ReplayCase | str:
    """One decision as a replay case, or why it can't be one (a skip reason)."""

    from langchain_core.messages import messages_from_dict

    from row_bot.learning import capture

    if decision.status != "ok":
        return "not_ok"
    if decision.untrusted and not include_untrusted:
        return "untrusted"
    check: Check = "exact_action"
    if decision.fidelity == "partial":
        if correction is None:
            return "partial_without_correction"
        if not correction.tool_calls:
            return "partial_needs_exact_prefix"
        check = "tool_choice"
    if correction is not None:
        reference, source = correction.action(), "corrected"
    else:
        if decision.id in bad:
            return "known_bad"
        raw = saved.read_blob(decision.model_generated_blob or "")
        if raw is None:
            return "no_reference"
        try:
            reference = Action.from_message(messages_from_dict([json.loads(raw.decode("utf-8"))])[0])
        except Exception:  # noqa: BLE001 - an unreadable output is not a reference
            return "no_reference"
        source = "captured"
    request = capture.read_request(saved, decision)
    if request is None:
        return "purged"
    if request.get("reconstructed") and check == "exact_action":
        return "partial_needs_exact_prefix"
    if request.get("pictures_dropped"):
        # A picture it was sent went under the size cap: its starting point can't be rebuilt (§8.6).
        return "picture_dropped"
    if decision.episode_id not in episodes:
        episode = saved.get("episode", decision.episode_id)
        episodes[decision.episode_id] = str(getattr(episode, "kind_of_work", None) or "Your work")
    messages = tuple(request["messages"])
    return ReplayCase(
        case_id or decision.id, decision.thread_id, decision.conversation_id, decision.created_at,
        episodes[decision.episode_id], decision.fidelity, decision.untrusted, messages,
        None if request.get("tools") is None else tuple(request["tools"]), request.get("model_ref"),
        reference, source, check, _last_person_text(messages)[:4_000],
    )


def _with_guidance(messages: list[Any], arm: Arm) -> list[Any]:
    from langchain_core.messages import SystemMessage

    if not arm.guidance:
        return messages
    block = SystemMessage(content="Guidance from earlier work:\n" + "\n".join(f"- {item}" for item in arm.guidance))
    index = 1 if messages and getattr(messages[0], "type", "") == "system" else 0
    return messages[:index] + [block] + messages[index:]


UNSERVABLE_DETAIL = "the decision couldn't be put in the model's serving shape"


def _unbridged(action: Action) -> Action:
    """The action with each ``tool_invoke(name, arguments)`` written as the call it makes: the bridge
    carries a call, it doesn't change the decision."""

    calls = []
    for name, args in action.tool_calls:
        if name == "tool_invoke" and str(args.get("name") or ""):
            inner = args.get("arguments")
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner or "{}")
                except ValueError:
                    inner = {}
            calls.append((str(args["name"]), dict(inner) if isinstance(inner, Mapping) else {}))
        else:
            calls.append((name, args))
    return Action(tuple(calls), action.text)


def _langchain(system: Sequence[str], messages: Sequence[Mapping[str, Any]]) -> list[Any]:
    """OpenAI-format messages (a rendered prefix) as the messages a model client is asked with."""

    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    out: list[Any] = [SystemMessage(content="\n\n".join(text for text in system if text))] if system else []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            calls = []
            for call in message.get("tool_calls") or ():
                function = call.get("function") or {}
                arguments = function.get("arguments")
                try:
                    args = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
                except ValueError:
                    args = {}
                calls.append({"name": str(function.get("name") or ""), "args": args if isinstance(args, dict) else {},
                              "id": str(call.get("id") or ""), "type": "tool_call"})
            out.append(AIMessage(content=content or "", tool_calls=calls))
        elif role == "tool":
            out.append(ToolMessage(content=content, tool_call_id=str(message.get("tool_call_id") or "")))
        elif role == "system":
            out.append(SystemMessage(content=content))
    return out


def _as_seen(case: ReplayCase, arm: Arm) -> tuple[Mapping[str, Any], ...]:
    """The case's prefix as ``arm`` is served it (A8): a text-only arm without the page's picture after a
    browser or computer action, as training leaves it out (the action's result gives it in words)."""

    from row_bot.learning.pictures import is_page_picture

    if arm.sees_images:
        return case.messages
    return tuple(item for item in case.messages if not is_page_picture(item))


def _served(case: ReplayCase, arm: Arm) -> tuple[list[Any], list[dict[str, Any]] | None, Any] | None:
    """The case as ``arm`` is served (its prefix rendered for ``arm.serve_context``, O71 and §4):
    (messages, tools, the rendered problem), or None when the arm is asked as captured. Raises
    ``ValueError`` when the prefix can't be rendered."""

    from row_bot.learning.training import dataset as dataset_module
    from row_bot.learning.training import shape

    if not arm.serve_context:
        return None
    envelope = shape.envelope_for(int(arm.serve_context))
    if envelope.profile == "full":
        return None
    system: list[str] = []
    messages: list[dict[str, Any]] = []
    for item in _as_seen(case, arm):
        converted = dataset_module.openai_message(item)  # raises ValueError on content it can't send
        if converted is None:
            continue
        if converted["role"] == "system":
            system.append(str(converted["content"]))  # one system message first, as rows have it
        else:
            messages.append(converted)
    reference = _unbridged(case.reference)
    problem = shape.serve_problem(system, messages, None if case.tools is None else [dict(item) for item in case.tools],
                                  [name for name, _ in reference.tool_calls], envelope)
    return _langchain(problem.system, problem.messages), problem.tools, problem


def run_replay_case(case: ReplayCase, arm: Arm, *, client: ModelClient, budget: Budget = Budget(),
                    meter: TokenMeter | None = None, judge: Judge | None = None,
                    arm_left: int | None = None) -> CaseResult:
    """Re-ask one decision at its captured prefix and compare the new action.

    An arm served at a small context (``Arm.serve_context``) is asked the prefix in its serving shape
    (O71): a call through ``tool_invoke`` counts as the call it carries, and where the served model
    must first find the reference's tool, a ``tool_search`` that finds it is the right move."""

    from langchain_core.messages import messages_from_dict

    started = time.monotonic()
    allowance = budget.task_tokens if arm_left is None else min(budget.task_tokens, max(0, arm_left))
    try:
        served = _served(case, arm)
    except (ValueError, TypeError, KeyError):
        return CaseResult(case.decision_id, "replay", case.kind_of_work, "unverified", case.check,
                          detail=UNSERVABLE_DETAIL)
    if served is None:
        messages, tools, problem = list(messages_from_dict([dict(item) for item in _as_seen(case, arm)])), case.tools, None
    else:
        messages, tools, problem = served
    messages = _with_guidance(messages, arm)
    try:
        reply = _ask(client, messages, tools, arm, meter, allowance)
    except _BudgetSpent:
        return CaseResult(case.decision_id, "replay", case.kind_of_work, "budget", case.check,
                          detail="ran out of its token budget")
    except _LimitReached:
        raise
    except Exception as error:  # noqa: BLE001
        if is_malformed_call(error):  # the model's own output, so it failed (O70)
            return CaseResult(case.decision_id, "replay", case.kind_of_work, "failed", case.check,
                              detail=MALFORMED_DETAIL, cause=MALFORMED_CALL)
        return CaseResult(case.decision_id, "replay", case.kind_of_work, "error", case.check,
                          detail=f"the model call failed ({type(error).__name__})")
    candidate = Action.from_message(reply.message)
    if problem is None:
        verdict, detail = compare_action(candidate, case.reference, check=case.check, judge=judge, task_text=case.text)
    else:
        searched = [args for name, args in candidate.tool_calls if name == "tool_search"]
        found = set().union(*(problem.search(args) for args in searched)) if searched and problem.search else set()
        if problem.search_first and problem.search_first <= found:
            verdict, detail = True, "searched for the tool it needed and found it"
        else:
            verdict, detail = compare_action(_unbridged(candidate), _unbridged(case.reference), check=case.check,
                                             judge=judge, task_text=case.text)
    status: Status = "unverified" if verdict is None else ("passed" if verdict else "failed")
    judged = not case.reference.tool_calls and case.check == "exact_action" and len(case.reference.text) > SHORT_TEXT
    return CaseResult(case.decision_id, "replay", case.kind_of_work, status, "judge" if judged else case.check,
                      reply.tokens, reply.tokens if reply.local else 0, 1, round(time.monotonic() - started, 3), detail,
                      guarded=status == "failed" and detail == GUARD_DETAIL)


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
    pack task, metric or budget is a new key. Values are counts and codes only (status, why it didn't
    pass, tokens, steps, time), never a task or its id, so the file says nothing about the hidden
    tasks (E10).
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
    def key(arm: Arm, task: PackTask, budget: Budget, signature: str, *, repeat: int = 0) -> str:
        """A repeated run of a task is its own entry (``repeat``), so repeats are never one result
        copied."""

        import hashlib

        content = json.dumps(task.to_dict(), sort_keys=True, ensure_ascii=False)
        parts: list[Any] = [str(arm.model_ref or ""), hashlib.sha256(content.encode("utf-8")).hexdigest(),
                            METRIC_VERSION, budget.to_dict(), signature]
        if repeat:
            parts.append(int(repeat))
        if arm.lessons or arm.ways:
            # The main model with the lessons and ways of working in use is its own entry (L3b).
            parts.append({"lessons": [list(item) for item in arm.lessons], "ways": [list(item) for item in arm.ways]})
        material = json.dumps(parts, sort_keys=True)
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
                          int(found.get("steps") or 0), float(found.get("seconds") or 0.0), "kept from an earlier test",
                          str(found.get("cause") or ""), bool(found.get("guarded")))

    def put(self, key: str, case: CaseResult) -> None:
        if case.status not in ("passed", "failed", "budget"):
            return  # an error or a stop is never kept: it runs again next time
        entries = self._load()
        entries[key] = {"at": round(float(self.clock()), 3), "status": case.status, "check": case.check,
                        "tokens": case.tokens, "local_tokens": case.local_tokens, "steps": case.steps,
                        "seconds": case.seconds, "cause": case.cause, "guarded": case.guarded}
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


def pass_fractions(cases: Sequence[CaseResult]) -> dict[str, tuple[str, float]]:
    """Each problem's pass fraction over its counted runs (``s(i, a)``, pre-registration §6.1), with
    its kind of work. A problem with no counted run has none."""

    runs: dict[str, list[int]] = {}
    kinds_of_work: dict[str, str] = {}
    for case in cases:
        if case.counted:
            runs.setdefault(case.task_id, []).append(int(case.passed))
            kinds_of_work.setdefault(case.task_id, case.kind_of_work or "Other")
    return {task: (kinds_of_work[task], sum(found) / len(found)) for task, found in runs.items()}


def _by_kind(cases: Sequence[CaseResult]) -> dict[str, tuple[int, int]]:
    """Passed of problems per kind of work. With repeated runs a problem counts as its pass fraction,
    and the kind's total is rounded to whole problems ("2 of 8": the verdict rule's own units)."""

    table: dict[str, list[float]] = {}
    for kind, fraction in pass_fractions(cases).values():
        row = table.setdefault(kind, [0.0, 0])
        row[0] += fraction
        row[1] += 1
    return {kind: (int(round(row[0] + 1e-9)), int(row[1])) for kind, row in sorted(table.items())}


def _paired(first: Sequence[CaseResult], second: Sequence[CaseResult]) -> dict[str, Any]:
    base = pass_fractions(first)
    other = pass_fractions(second)
    better = worse = same = 0
    for task, (_, fraction) in base.items():
        if task not in other:
            continue
        if other[task][1] > fraction:
            better += 1
        elif other[task][1] < fraction:
            worse += 1
        else:
            same += 1
    return {"better": better, "worse": worse, "same": same, "p_value": round(sign_test(better, worse), 4)}


# ── The pre-registered paired analysis (§7, O88): only aggregates leave ─────

#: Fixed by the pre-registration: a 95% percentile interval from 10,000 cluster resamples, a two-sided
#: paired permutation test by cluster with 10,000 sign flips, and a recorded seed.
RESAMPLES = 10_000
PERMUTATIONS = 10_000
ANALYSIS_SEED = 20_261_022
#: Fewer counted problems than this: no confirmatory test (reported as descriptive only).
MIN_PROBLEMS = 30
#: Non-inferiority margin: worse by at most 10 points.
MARGIN = 0.10


def _percentile(ordered: Sequence[float], share: float) -> float:
    if not ordered:
        return 0.0
    position = share * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def paired_analysis(differences: Sequence[tuple[str, float]], *, resamples: int = RESAMPLES,
                    permutations: int = PERMUTATIONS, seed: int = ANALYSIS_SEED) -> dict[str, Any]:
    """The pre-registered analysis of paired differences ``d(i) = s(i, A) - s(i, B)``, given as
    (cluster, d) per problem: the mean difference, a 95% cluster-bootstrap percentile interval
    (source chats resampled with replacement; a pack task is its own cluster) and a two-sided
    paired permutation p-value (each cluster's summed difference flips sign; exact when there are
    few enough clusters to list every sign pattern). Deterministic for a seed."""

    import itertools
    import random

    clusters: dict[str, list[float]] = {}
    for cluster, difference in differences:
        clusters.setdefault(str(cluster), []).append(float(difference))
    keys = sorted(clusters)
    sums = [sum(clusters[key]) for key in keys]
    counts = [len(clusters[key]) for key in keys]
    problems = sum(counts)
    base = {"problems": problems, "clusters": len(keys), "resamples": int(resamples),
            "permutations": int(permutations), "seed": int(seed)}
    if not problems:
        return {**base, "difference": None, "low": None, "high": None, "p_value": None, "exact": False}
    total = sum(sums)
    mean = total / problems
    rng = random.Random(int(seed))
    indexes = range(len(keys))
    boots = []
    for _ in range(int(resamples)):
        picks = rng.choices(indexes, k=len(keys))
        boots.append(sum(sums[index] for index in picks) / sum(counts[index] for index in picks))
    boots.sort()
    observed = abs(total) - 1e-12
    exact = 2 ** len(keys) <= int(permutations)
    if exact:
        hits = sum(1 for signs in itertools.product((1, -1), repeat=len(keys))
                   if abs(sum(sign * value for sign, value in zip(signs, sums))) >= observed)
        p_value = hits / 2 ** len(keys)
    else:
        hits = sum(1 for _ in range(int(permutations))
                   if abs(sum(value if rng.random() < 0.5 else -value for value in sums)) >= observed)
        p_value = (hits + 1) / (int(permutations) + 1)
    return {**base, "difference": round(mean, 6), "low": round(_percentile(boots, 0.025), 6),
            "high": round(_percentile(boots, 0.975), 6), "p_value": round(p_value, 6), "exact": exact}


def holm(p_values: Mapping[str, float | None], *, family: Sequence[str] | None = None) -> dict[str, float]:
    """Holm-adjusted p-values across a confirmatory family (§7). The family is the pre-registered one
    (``family``, else the given names); a member with no p-value (not tested) enters as 1, so it is never
    rejected and still counts in the family's size."""

    members = list(dict.fromkeys(family if family is not None else p_values))
    raw = {name: 1.0 if p_values.get(name) is None else min(1.0, max(0.0, float(p_values[name])))
           for name in members}
    order = sorted(members, key=lambda name: (raw[name], members.index(name)))
    adjusted: dict[str, float] = {}
    running = 0.0
    for rank, name in enumerate(order):
        running = max(running, min(1.0, (len(order) - rank) * raw[name]))
        adjusted[name] = round(running, 6)
    return {name: adjusted[name] for name in members}


def interval_verdict(analysis: Mapping[str, Any]) -> str:
    """What the interval says: ``better`` (it lies above 0), ``worse`` (below 0) or ``unclear``; the
    product's own verdict rule is unchanged (a promotion rule, E4) and reported beside it."""

    low, high = analysis.get("low"), analysis.get("high")
    if low is None or high is None:
        return "unclear"
    if low > 0:
        return "better"
    if high < 0:
        return "worse"
    return "unclear"


def _analysis(first: Sequence[CaseResult], second: Sequence[CaseResult], clusters: Mapping[str, str], *,
              seed: int, kind_key: Callable[[str], str] | None = None) -> dict[str, Any]:
    """``second`` against ``first`` on the problems both counted, overall and per kind of work
    (``kind_key`` groups the kinds, as a caller's table does)."""

    base, other = pass_fractions(first), pass_fractions(second)
    rows: dict[str, list[tuple[str, float]]] = {}
    means: dict[str, list[tuple[float, float]]] = {}
    for task, (kind, fraction) in base.items():
        if task not in other:
            continue  # a problem with no counted run in one arm leaves the pair (§8.3)
        difference = other[task][1] - fraction
        for key in ("overall", kind_key(kind) if kind_key is not None else kind):
            rows.setdefault(key, []).append((clusters.get(task, task), difference))
            means.setdefault(key, []).append((fraction, other[task][1]))

    def summary(key: str) -> dict[str, Any]:
        return _summarized(paired_analysis(rows.get(key, []), resamples=RESAMPLES, permutations=PERMUTATIONS,
                                           seed=seed), means.get(key, []))

    return {"overall": summary("overall"),
            "by_kind": {kind: summary(kind) for kind in sorted(key for key in rows if key != "overall")}}


def _summarized(found: dict[str, Any], pairs: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """A paired analysis with each arm's mean over the paired problems, the minimum-size flag (§7), what
    the interval says and the non-inferiority flag (lower bound above -10 points)."""

    found["baseline"] = round(sum(item[0] for item in pairs) / len(pairs), 6) if pairs else None
    found["arm"] = round(sum(item[1] for item in pairs) / len(pairs), 6) if pairs else None
    found["enough"] = found["problems"] >= MIN_PROBLEMS
    found["interval_verdict"] = interval_verdict(found)
    found["not_worse"] = found["low"] is not None and found["low"] > -MARGIN
    return found


def _replay_shape(arm: Arm) -> str:
    from row_bot.learning.training.shape import RENDERING_VERSION

    return f"serving-v{RENDERING_VERSION}@{int(arm.serve_context)}" if arm.serve_context else "captured"


def _repeat_record(cases: Sequence[CaseResult]) -> dict[str, int]:
    """How many repeated problems gave the same run every time (status, tokens and steps): a runtime
    that decodes greedily answers identically, and the report says so."""

    runs: dict[str, list[tuple[str, int, int]]] = {}
    for case in cases:
        if case.counted:
            runs.setdefault(case.task_id, []).append((case.status, case.tokens, case.steps))
    repeated = [found for found in runs.values() if len(found) > 1]
    return {"problems": len(repeated), "identical": sum(1 for found in repeated if len(set(found)) == 1)}


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
            cache: ArmCache | None = None, on_case: Callable[[str, CaseResult], None] | None = None,
            repeats: int = 1, seed: int = ANALYSIS_SEED, kind_key: Callable[[str], str] | None = None,
            pairs: Sequence[tuple[str, str]] = (), order: str = "interleaved") -> Comparison:
    """Run every arm on the same pack tasks and replayed decisions, with the same budget.

    The first arm is the baseline the others are compared with. A judge is used only
    with its calibration and only when that calibration is reliable. ``runner`` runs pack tasks
    through the real Agent runtime (O56) instead of the in-process tool loop; ``cache`` keeps
    and reuses the results of arms marked ``cache`` (O31); ``on_case(arm, case)`` follows progress.
    ``repeats`` runs every problem that many times per arm (O88); the paired analysis uses ``seed``
    and groups kinds of work with ``kind_key``. ``pairs`` ((baseline, arm) names) adds the analysis
    of more pairs than each arm against the first (an ablation's own contrasts). ``order="model"`` runs one
    arm's problems after the other's, for models that can't stay loaded together (the same results).
    """

    return _compare(arms, list(tasks), list(replay), client=client, judge=judge, calibration=calibration,
                    budget=budget, meter=meter, store=store, save=save, clock=clock, backend=backend,
                    suite=suite or ("replay" if replay and not tasks else "pack"), aggregate_only=False,
                    runner=runner, cache=cache, on_case=on_case, repeats=repeats, seed=seed, kind_key=kind_key,
                    pairs=pairs, order=order)


def _compare(arms: Sequence[Arm], tasks: list[PackTask], replay: list[ReplayCase], *, client: ModelClient | None,
             judge: Judge | None, calibration: Calibration | None, budget: Budget, meter: TokenMeter | None,
             store: Any | None, save: bool, clock: Callable[[], float], backend: str, suite: str,
             aggregate_only: bool, runner: PackRunner | None = None, cache: ArmCache | None = None,
             on_case: Callable[[str, CaseResult], None] | None = None, repeats: int = 1,
             seed: int = ANALYSIS_SEED, combine_with: Comparison | None = None,
             kind_key: Callable[[str], str] | None = None,
             pairs: Sequence[tuple[str, str]] = (),
             inspect: Callable[[Mapping[str, list[CaseResult]]], None] | None = None,
             order: str = "interleaved") -> Comparison:
    """``inspect`` sees every arm's per-problem results before they are dropped: only this module's own
    analyses pass it (the freeze evaluation), so no per-problem result leaves an audit run (E10)."""

    if not arms:
        raise EvaluationError("no_arms")
    repeats = max(1, int(repeats))
    if len({arm.name for arm in arms}) != len(arms):
        raise EvaluationError("arm_names_repeat")
    contrasts = [(str(base), str(other)) for base, other in pairs]
    if {name for pair in contrasts for name in pair} - {arm.name for arm in arms}:
        raise EvaluationError("unknown_arm")
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
    # What this comparison spent per arm, and the part of it local models used: a result kept from an
    # earlier test (O31) spent nothing now, so it adds to neither.
    spent: dict[str, int] = {arm.name: 0 for arm in arms}
    spent_local: dict[str, int] = {arm.name: 0 for arm in arms}
    cached_count: dict[str, int] = {arm.name: 0 for arm in arms}
    signature = str(getattr(runner, "signature", "") or "in-process-1")
    stopped = ""
    items: list[PackTask | ReplayCase] = [*tasks, *replay]
    # Every problem runs ``repeats`` times per arm, the arms interleaved run by run (O88), or (``order="model"``,
    # when the models can't stay loaded together) every run of one arm, then the next arm's: the same runs, kept
    # in the same order per arm, so the results and their pairing are exactly the interleaved ones (C1).
    units = [(item, repeat) for item in items for repeat in range(repeats)]

    def run_one(arm: Arm, item: PackTask | ReplayCase, repeat: int) -> CaseResult:
        left = None if budget.arm_tokens is None else budget.arm_tokens - spent[arm.name]
        if left is not None and left <= 0:
            case_id = item.id if isinstance(item, PackTask) else item.decision_id
            return CaseResult(case_id, "pack" if isinstance(item, PackTask) else "replay", item.kind_of_work,
                              "budget", "budget", detail="the arm's budget was spent")
        key = (ArmCache.key(arm, item, budget, signature, repeat=repeat)
               if cache is not None and arm.cache and isinstance(item, PackTask) else None)
        kept = cache.get(key, item) if key is not None and cache is not None else None
        if kept is not None:
            cached_count[arm.name] += 1
            if on_case is not None:
                on_case(arm.name, kept)
            return kept  # nothing spent: the result is from an earlier test (O31)
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
        spent[arm.name] += result.tokens
        spent_local[arm.name] += result.local_tokens
        if on_case is not None:
            on_case(arm.name, result)
        return result

    rows: list[dict[str, CaseResult]] = [{} for _unit in units]
    reached = len(units)  # the first run the token limit stopped (every arm's later runs are not run)
    if order == "model":
        for arm in arms:
            for index, (item, repeat) in enumerate(units[:reached]):
                try:
                    rows[index][arm.name] = run_one(arm, item, repeat)
                except _LimitReached:
                    stopped, reached = meter.note(), index
                    break
    else:
        for index, (item, repeat) in enumerate(units):
            try:
                rows[index] = {arm.name: run_one(arm, item, repeat) for arm in arms}
            except _LimitReached:
                stopped, reached = meter.note(), index
                break
    for index, (item, _repeat) in enumerate(units):
        for arm in arms:
            if index < reached:
                results[arm.name].append(rows[index][arm.name])
            else:
                case_id = item.id if isinstance(item, PackTask) else item.decision_id
                results[arm.name].append(CaseResult(case_id, "pack" if isinstance(item, PackTask) else "replay",
                                                    item.kind_of_work, "not_run", "none", detail=stopped))
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
        local = spent_local[arm.name]
        run = TestRun(
            id=new_id("run"), subject_kind=arm.subject_kind, suite=suite, started_at=started,  # type: ignore[arg-type]
            subject_id=arm.subject_id, baseline_id=baseline_id, pack=packs[0] if len(packs) == 1 and not replay else None,
            model_ref=arm.model_ref, status="stopped" if stopped else "completed", passed=passed,
            failed=len(counted) - passed, total=len(counted),
            budget_tokens=budget.arm_tokens if budget.arm_tokens is not None else budget.task_tokens * len(units),
            cost=Cost(tokens=spent[arm.name] - local), comparison_set=comparison_set, ended_at=ended,
            source_thread_ids=tuple(threads),
        )
        baseline_id = baseline_id or run.id
        by_kind = _by_kind(cases)
        body: dict[str, Any] = {
            "format": RESULTS_FORMAT, "version": 1, "metric_version": METRIC_VERSION, "arm": arm.name,
            "guidance": len(arm.guidance), "suite": suite, "budget": budget.to_dict(), "tokens": spent[arm.name],
            "local_tokens": local, "by_kind": {k: list(v) for k, v in by_kind.items()},
            "statuses": dict(Counter(case.status for case in cases)),
            # Why tasks didn't pass, as codes (``eval_worker.CAUSES``): counts only, so audit runs keep them too.
            "causes": dict(Counter(case.cause for case in cases if case.cause)),
            "small_sample": _small(passed, len(counted)),
            "stopped": stopped, "judge": calibration_dict,
            "repeats": repeats, "repeated": _repeat_record(cases),
            # How replayed decisions reached this arm: as captured, or in its serving shape (O71).
            "replay_shape": _replay_shape(arm) if replay else None,
        }
        if not aggregate_only:
            body["cases"] = [{"task": case.task_id, "kind_of_work": case.kind_of_work, "status": case.status,
                              "check": case.check, "tokens": case.tokens, "steps": case.steps,
                              "seconds": case.seconds, "detail": case.detail, "cause": case.cause}
                             for case in cases]
        runs.append((run, body))
        ran = [case for case in cases if case.status != "not_run"]
        arm_results.append(ArmResult(
            arm, run, [] if aggregate_only else cases, spent[arm.name], local, arm.model_ref, by_kind,
            ran=len(ran), seconds=round(sum(case.seconds for case in ran), 3),
            measured_tokens=sum(case.tokens for case in ran),
            measured_cloud_tokens=sum(max(0, case.tokens - case.local_tokens) for case in ran),
            cached=cached_count[arm.name], causes=dict(body["causes"])))
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
    # The pre-registered paired analysis of each arm against the first, inside this module (O88, E10).
    clusters = {item.id: item.id for item in tasks}
    clusters.update({case.decision_id: case.conversation_id or case.thread_id or case.decision_id
                     for case in replay})
    analysis: dict[str, Any] = {"seed": int(seed), "resamples": RESAMPLES, "permutations": PERMUTATIONS,
                                "arms": {arm.name: _analysis(results[arms[0].name], results[arm.name], clusters,
                                                             seed=seed, kind_key=kind_key) for arm in arms[1:]}}
    if contrasts:
        analysis["pairs"] = {f"{other} vs {base}": _analysis(results[base], results[other], clusters, seed=seed,
                                                             kind_key=kind_key) for base, other in contrasts}
    if combine_with is not None:
        # Visible and hidden problems together (a visible comparison of the same arms run before).
        joined = {**combine_with.clusters, **clusters}
        visible = {item.arm.name: list(item.cases) for item in combine_with.arms}
        if arms[0].name in visible:
            analysis["combined"] = {
                arm.name: _analysis([*visible[arms[0].name], *results[arms[0].name]],
                                    [*visible.get(arm.name, []), *results[arm.name]], joined, seed=seed,
                                    kind_key=kind_key)
                for arm in arms[1:] if arm.name in visible}
        both = [(base, other) for base, other in contrasts if base in visible and other in visible]
        if both:
            analysis["combined_pairs"] = {
                f"{other} vs {base}": _analysis([*visible[base], *results[base]], [*visible[other], *results[other]],
                                                joined, seed=seed, kind_key=kind_key)
                for base, other in both}
    if inspect is not None:
        inspect(results)
    return Comparison(comparison_set, suite, arm_results, budget, stopped, calibration, paired, judge_used,
                      repeats=repeats, repeated={arm.name: _repeat_record(results[arm.name]) for arm in arms},
                      analysis=analysis, clusters={} if aggregate_only else clusters)


# ── The audit vault: only this module reads it ──────────────────────────────


def _vault(store: Any | None = None) -> Any:
    root = getattr(store, "root", None)
    return audit.AuditVault(root)


#: When this build first applied the vault's rules (A4: pushed-back turns aren't uncorrected work; A5: references
#: are the main model's), kept in ``learning.db``'s meta (``holdout.record_rules``).
AUDIT_RULES_KEY = "audit.rules"
AUDIT_RULES = ("A4", "A5")


def _personal_model_decisions(store: Any) -> set[str]:
    """Model calls the personal model made, and every later call of a turn it took part in (amendment A5): its
    actions are in their prefix, so none of them is the main model's own work."""

    from row_bot.learning import holdout

    calls = store.find("decision", subkind="model_call", limit=100_000)
    first: dict[str, float] = {}
    for decision in calls:
        if holdout.personal(decision):
            at = first.get(decision.episode_id)
            first[decision.episode_id] = decision.created_at if at is None else min(at, decision.created_at)
    return {decision.id for decision in calls
            if decision.episode_id in first and decision.created_at >= first[decision.episode_id]}


def _eligible_for_audit(store: Any, horizon: float, personal: set[str] | None = None) -> list[audit.ReplayCandidate]:
    # Amendment A5: a decision the personal model answered is never a reference (it would make the personal model's
    # own action the answer H1 scores it against); only the main model's work is offered.
    personal = _personal_model_decisions(store) if personal is None else personal
    newer = [decision for decision in store.find("decision", subkind="model_call", newest_first=True, limit=100_000)
             if decision.created_at > horizon and decision.id not in personal]
    # Amendment A7: untrusted input doesn't make a decision ineligible for the vault. Replay runs no tools, and each
    # task keeps its untrusted mark for the trusted-only sensitivity analysis. Visible tests still leave them out (E9).
    # Amendment A8: a decision whose picture went under the size cap can't be rebuilt (``picture_dropped``), so it
    # isn't offered; one with its picture is held with it and asked as each arm is served.
    found = collect_replay(store, refresh=False, decisions=newer, limit=100_000, include_untrusted=True)
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
    # O87: every 2nd confirmed correction is held out first, so nothing tests or trains on it in the open.
    from row_bot.learning import holdout

    holdout.mark_corrections(saved)
    if saved.exists():
        holdout.record_rules(saved, AUDIT_RULES, key=AUDIT_RULES_KEY)
    vault = _vault(saved)
    who = f"{_caller()}: {asked_by}".strip(": ")
    personal_calls = _personal_model_decisions(saved)
    report = vault.refresh(_eligible_for_audit(saved, vault.horizon(), personal_calls), asked_by=who)
    if report.added:
        by_id = {decision.id: decision for decision in saved.find("decision", subkind="model_call", limit=100_000)
                 if decision.id in set(report.added)}
        saved.add_marks([("decision", item, by_id[item].conversation_id if item in by_id else "")
                         for item in report.added], MARK_HELD_OUT)
    waiting = holdout.waiting_for_vault(saved)
    # A task held as uncorrected that the person corrected, stopped or retried later has no reference
    # any more (O19: no signal); one held out as a correction becomes a correction task below.
    retired = vault.retire_corrected(_signalled(saved) - waiting, asked_by=who)
    # A5: a task already held whose reference is the personal model's (held before this rule, of any origin) is
    # retired and counted; it stays held out of training and drafting.
    personal = vault.retire_personal(personal_calls | _personal_corrections(saved, waiting, personal_calls),
                                     asked_by=who)
    stored = vault.correction_ids()
    candidates, missing = _correction_candidates(saved, waiting - stored, personal_calls)
    added = vault.hold_corrections(candidates, asked_by=who) if candidates else ()
    if report.added or report.retired or retired or personal or added:
        gate.changed()
    return {"considered": report.considered, "added": len(report.added),
            "retired": report.retired + retired + personal, "retired_personal": personal, "held": report.held,
            "corrections_added": len(added), "corrections_held": len(stored) + len(added),
            "corrections_waiting": missing}


def _personal_corrections(saved: Any, waiting: set[str], personal: set[str]) -> set[str]:
    """Held-out corrections that would rest on the personal model's work (A5): the call they correct was the
    personal model's (``personal``), or the redo the person accepted started with its move."""

    if not waiting:
        return set()
    from row_bot.learning import corrections, holdout

    book = corrections.Book(saved)
    found: set[str] = set()
    for decision_id in waiting:
        decision = book.decisions.get(decision_id)
        if decision is None:
            continue
        problem = corrections.problem_call(book, decision)
        redo = corrections.accepted_redo(saved, decision, book=book)
        first = corrections.first_call(book, redo) if redo is not None else None
        if (problem is not None and problem.id in personal) or (first is not None and holdout.personal(first)):
            found.add(decision_id)
    return found


def _correction_candidates(saved: Any, waiting: set[str],
                          personal: set[str] | None = None) -> tuple[list[audit.ReplayCandidate], int]:
    """Held-out corrections (O87) ready for the vault, each with its corrected action: the first move of
    the redo the person accepted (``corrections.accepted_redo``), or, when they didn't redo a reply they
    taught about, the answer they said it should have given (Teach's "What should it have done?", L3b,
    checked as ``stated_answer``). Returns them and how many wait for one."""

    if not waiting:
        return [], 0
    from row_bot.learning import corrections, holdout

    book = corrections.Book(saved)
    bad = _signalled(saved)
    answers = _taught_answers(saved)
    personal = _personal_model_decisions(saved) if personal is None else personal
    episodes: dict[str, str] = {}
    found: list[audit.ReplayCandidate] = []
    missing = 0
    for decision_id in sorted(waiting):
        decision = book.decisions.get(decision_id)
        problem = corrections.problem_call(book, decision) if decision is not None else None
        if problem is not None and problem.id in personal:
            continue  # A5: the personal model's reply is never a hidden problem (it stays held out)
        redo = corrections.accepted_redo(saved, decision, book=book) if decision is not None else None
        first = corrections.first_call(book, redo) if redo is not None else None
        if first is not None and holdout.personal(first):
            continue  # A5: a redo the personal model gave is never the reference
        action = _action_of(saved, first) if first is not None else None
        check: Check = "exact_action"
        if action is None and problem is not None and decision is not None and decision.kind == "model_call":
            said = answers.get(decision_id)
            if said:
                action, check = Action((), said), "stated_answer"
        if problem is None or action is None:
            missing += 1
            continue
        case = _case_for(saved, problem, CorrectedAction(action.tool_calls, action.text), include_untrusted=True,
                         bad=bad, episodes=episodes, case_id=decision_id)
        if isinstance(case, str) or case.check != "exact_action":
            missing += 1
            continue
        if check != case.check:
            case = replace(case, check=check)
        found.append(audit.ReplayCandidate(decision_id, case.created_at, case.thread_id,
                                           case.conversation_id or case.thread_id, case.kind_of_work,
                                           case.to_content()))
    return found, missing


def _taught_answers(saved: Any) -> dict[str, str]:
    """The answer each Teach on a reply said it should have given (the newest per decision), by decision."""

    found: dict[str, tuple[float, str]] = {}
    for item in saved.find("signal", limit=100_000):
        answer = str(getattr(item, "answer", None) or "").strip()
        if getattr(item, "kind", "") != "teach" or not answer or getattr(item, "withdrawn_at", None) is not None:
            continue
        decision_id = str(getattr(item, "decision_id", None) or "")
        if decision_id and (decision_id not in found or item.created_at >= found[decision_id][0]):
            found[decision_id] = (float(item.created_at), answer)
    return {key: value for key, (_at, value) in found.items()}


def _action_of(saved: Any, decision: Decision) -> Action | None:
    """What a model call did (its own output), the bridge's calls written as the calls they carry."""

    from langchain_core.messages import messages_from_dict

    raw = saved.read_blob(decision.model_generated_blob or "")
    if raw is None:
        return None
    try:
        action = Action.from_message(messages_from_dict([json.loads(raw.decode("utf-8"))])[0])
    except Exception:  # noqa: BLE001 - an unreadable output is no action
        return None
    found = _unbridged(action)
    return found if found.tool_calls or found.text else None


def audit_check(arms: Sequence[Arm], *, asked_by: str, client: ModelClient | None = None,
                judge: Judge | None = None, calibration: Calibration | None = None, budget: Budget = Budget(),
                meter: TokenMeter | None = None, packs: Iterable[str] | None = None, include_replay: bool = True,
                store: Any | None = None, save: bool = True, clock: Callable[[], float] = time.time,
                backend: str = "auto", runner: PackRunner | None = None, cache: ArmCache | None = None,
                verifiers: Iterable[str] | None = None,
                on_progress: Callable[[int, int], None] | None = None, repeats: int = 3,
                seed: int = ANALYSIS_SEED, combine_with: Comparison | None = None,
                kind_key: Callable[[str], str] | None = None, pairs: Sequence[tuple[str, str]] = (),
                corrected: bool | None = None, focus: Focus | None = None,
                leave_out: str | None = None, order: str = "interleaved") -> Comparison:
    """Run the arms on the vault's hidden tasks. Only aggregates come back: counts per kind of
    work and tokens per arm, and the paired analysis; no task, id or text (E10).

    ``leave_out`` (a strategy trial's id) leaves out the fresh problems that trial drew: an arm of the trial
    tests its own drafts on everything else, never on the problems that judge it (L4a).

    ``corrected``: True runs only the held-out corrections (O87, H2's hidden corrected problems),
    False only the uncorrected decisions, None both.

    Every hidden problem runs ``repeats`` times per arm (3, O84/O88). ``combine_with`` (a visible
    comparison of the same arms) adds the analysis of visible and hidden problems together, and
    ``pairs`` the analysis of more (baseline, arm) pairs.
    ``verifiers`` leaves out hidden tasks checked any other way (``code_tests`` need a container
    for automatic runs, O21); ``on_progress(done, total)`` counts tasks, nothing more.

    ``focus`` (a change's test, L3b) runs only some of the person's hidden work, chosen here: up to
    ``own`` problems of its kind of work (held-out corrections first, then the newest) and up to
    ``other`` of other kinds, each labelled with its kind of work (a problem whose episode had none is
    classified from the person's own request, ``kinds``)."""

    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    if not gate.is_enabled(saved.root) and not audit.vault_exists(saved.root):
        raise EvaluationError("learning_off")
    who = f"{_caller()}: {asked_by}".strip(": ")
    vault = _vault(saved)
    allowed = None if verifiers is None else set(verifiers)
    tasks = [task for task in vault.hidden_tasks(asked_by=who, packs=packs)
             if allowed is None or task.verifier.kind in allowed]
    replay = ([ReplayCase.from_content(item) for item in vault.replay_tasks(asked_by=who, origin=_origin(corrected))]
              if include_replay else [])
    if leave_out:
        drawn = vault.drawn_ids(leave_out)
        replay = [case for case in replay if case.decision_id not in drawn]
    if focus is not None:
        replay = _focused(replay, focus)
        tasks = _per_pack(tasks, focus.packs)
    total = (len(tasks) + len(replay)) * len(arms) * max(1, int(repeats))
    done = [0]

    def counted(_arm: str, _case: CaseResult) -> None:
        done[0] += 1
        if on_progress is not None:
            on_progress(done[0], total)

    return _compare(arms, tasks, replay, client=client, judge=judge, calibration=calibration, budget=budget,
                    meter=meter, store=saved, save=save, clock=clock, backend=backend, suite="audit",
                    aggregate_only=True, runner=runner, cache=cache, on_case=counted, repeats=repeats, seed=seed,
                    combine_with=combine_with, kind_key=kind_key, pairs=pairs, order=order)


def audit_size(*, asked_by: str, store: Any | None = None, packs: Iterable[str] | None = None,
               verifiers: Iterable[str] | None = None, include_replay: bool = True,
               corrected: bool | None = None, focus: Focus | None = None, leave_out: str | None = None) -> int:
    """How many hidden problems an :func:`audit_check` with the same choices would run (a count only)."""

    allowed = None if verifiers is None else set(verifiers)
    vault = _vault(store)
    who = f"{_caller()}: {asked_by}".strip(": ")
    hidden = [task for task in vault.hidden_tasks(asked_by=who, packs=packs)
              if allowed is None or task.verifier.kind in allowed]
    count = len(_per_pack(hidden, focus.packs) if focus is not None else hidden)
    if not include_replay:
        return count
    replay = vault.replay_tasks(asked_by=who, origin=_origin(corrected))
    if leave_out:
        drawn = vault.drawn_ids(leave_out)
        replay = [item for item in replay if str(item.get("id")) not in drawn]
    if focus is not None:
        return count + len(_focused([ReplayCase.from_content(item) for item in replay], focus))
    return count + len(replay)


@dataclass(frozen=True)
class Focus:
    """Which of the person's hidden problems a change's test runs (chosen inside this module, E10)."""

    kind: str
    own: int = 8
    other: int = 4
    # The packs' hidden tasks: at most this many of each pack asked for (None: all of them).
    packs: int | None = None


def kind_label_of(case: ReplayCase) -> str:
    """A replayed decision's kind of work: its episode's, else what the person asked (``kinds``, no call)."""

    from row_bot.learning import kinds

    kind = kinds.from_label(case.kind_of_work)
    if kind == kinds.OTHER and case.text:
        kind = kinds.classify(kinds.Request(case.text)).kind
    return kinds.label(kind)


def _per_pack(tasks: Sequence[PackTask], limit: int | None) -> list[PackTask]:
    if limit is None:
        return list(tasks)
    seen: Counter[str] = Counter()
    kept = []
    for task in tasks:
        seen[task.pack] += 1
        if seen[task.pack] <= max(0, int(limit)):
            kept.append(task)
    return kept


def _focused(replay: Sequence[ReplayCase], focus: Focus) -> list[ReplayCase]:
    from row_bot.learning import kinds

    labelled = [replace(case, kind_of_work=kind_label_of(case)) for case in replay]
    mine = kinds.label(focus.kind)
    own = sorted((case for case in labelled if case.kind_of_work == mine),
                 key=lambda case: (case.reference_source != "corrected", -case.created_at, case.decision_id))
    others = sorted((case for case in labelled if case.kind_of_work != mine),
                    key=lambda case: (-case.created_at, case.decision_id))
    chosen = own[: max(0, int(focus.own))] + others[: max(0, int(focus.other))]
    return sorted(chosen, key=lambda case: (case.created_at, case.decision_id))


def _origin(corrected: bool | None) -> str | None:
    return None if corrected is None else ("correction" if corrected else "replay")


# ── The paper's freeze evaluation (pre-registration §7): only aggregates leave ──

#: The hidden problems' splits (§3.3, §5): real work nobody corrected (the captured action is the
#: reference), real work with a held-out correction (the corrected action, O87) and the packs' hidden tasks.
SPLITS: tuple[str, ...] = ("real_uncorrected", "real_corrected", "packs")


def _scores(cases: Sequence[CaseResult], *, majority: bool = False, version: int = METRIC_VERSION) -> dict[str, float]:
    """Each problem's score over its counted runs: the pass fraction ``s(i, a)`` (§6.1), or with
    ``majority`` 1 when more than half of its counted runs passed, else 0 (§7's sensitivity analysis).
    ``version`` reads the runs under that checker version (2: before the commits-to-one-answer guard)."""

    runs: dict[str, list[int]] = {}
    for case in cases:
        if case.counted:
            runs.setdefault(case.task_id, []).append(int(case.passed_under(version)))
    if majority:
        return {task: 1.0 if 2 * sum(found) > len(found) else 0.0 for task, found in runs.items()}
    return {task: sum(found) / len(found) for task, found in runs.items()}


def _pair_summary(base: Mapping[str, float], other: Mapping[str, float], problems: Sequence[str],
                  clusters: Mapping[str, str], *, seed: int) -> dict[str, Any]:
    """``other`` against ``base`` on the ``problems`` both counted (a problem with no counted run in an
    arm leaves the pair, §8.3)."""

    paired = [task for task in problems if task in base and task in other]
    rows = [(clusters.get(task, task), other[task] - base[task]) for task in paired]
    return _summarized(paired_analysis(rows, seed=seed), [(base[task], other[task]) for task in paired])


def _freeze_analysis(arms: Sequence[Arm], results: Mapping[str, Sequence[CaseResult]], tasks: Sequence[PackTask],
                     replay: Sequence[ReplayCase], contrasts: Sequence[tuple[str, str]], *,
                     seed: int) -> dict[str, Any]:
    """Per arm and split: problems, counted problems, the mean score, run statuses and causes. Per contrast
    and split: the primary analysis and §7's sensitivity analyses (exact fidelity only, a majority vote,
    without clustering, per main model), per kind of work, and how many problems left the pair."""

    from row_bot.learning import kinds

    split = {task.id: "packs" for task in tasks}
    clusters = {task.id: task.id for task in tasks}
    kind = {task.id: kinds.from_label(task.kind_of_work) for task in tasks}
    exact: set[str] = set()
    trusted: set[str] = set()
    pictured: set[str] = set()
    model: dict[str, str] = {}
    ordinals: dict[str, str] = {}
    for case in sorted(replay, key=lambda item: (item.created_at, item.decision_id)):
        split[case.decision_id] = "real_corrected" if case.reference_source == "corrected" else "real_uncorrected"
        clusters[case.decision_id] = case.conversation_id or case.thread_id or case.decision_id
        kind[case.decision_id] = kinds.from_label(case.kind_of_work)
        if case.fidelity == "exact":
            exact.add(case.decision_id)
        if not case.untrusted:
            trusted.add(case.decision_id)
        if case.has_picture:
            pictured.add(case.decision_id)
        # The captured main model, named only by its order of first use (no model name leaves).
        model[case.decision_id] = ordinals.setdefault(str(case.model_ref or ""), f"model_{len(ordinals) + 1}")
    by_split = {name: [task for task, found in split.items() if found == name] for name in SPLITS}
    fractions = {arm.name: _scores(results[arm.name]) for arm in arms}
    majority = {arm.name: _scores(results[arm.name], majority=True) for arm in arms}
    # §7: H1 and H2 also under the checker version before the guard (the same runs, read without it).
    previous = {arm.name: _scores(results[arm.name], version=PREVIOUS_METRIC_VERSION) for arm in arms}

    per_arm: dict[str, Any] = {}
    for arm in arms:
        rows: dict[str, Any] = {}
        for name, problems in by_split.items():
            wanted = set(problems)
            cases = [case for case in results[arm.name] if case.task_id in wanted]
            scored = [fractions[arm.name][task] for task in problems if task in fractions[arm.name]]
            before = [previous[arm.name][task] for task in problems if task in previous[arm.name]]
            rows[name] = {
                "problems": len(problems), "counted": len(scored),
                "mean": round(sum(scored) / len(scored), 6) if scored else None,
                "mean_metric_v2": round(sum(before) / len(before), 6) if before else None,
                "guarded_runs": sum(1 for case in cases if case.counted and case.guarded),
                "runs": dict(sorted(Counter(case.status for case in cases).items())),
                "causes": dict(sorted(Counter(case.cause for case in cases if case.cause).items())),
            }
        per_arm[arm.name] = {"splits": rows}

    def summary(scores: Mapping[str, Mapping[str, float]], base: str, other: str, problems: Sequence[str],
                grouping: Mapping[str, str]) -> dict[str, Any]:
        return _pair_summary(scores[base], scores[other], problems, grouping, seed=seed)

    unclustered = {task: task for task in split}
    found: dict[str, Any] = {}
    for base, other in contrasts:
        entry: dict[str, Any] = {}
        for name, problems in by_split.items():
            part: dict[str, Any] = {
                "primary": summary(fractions, base, other, problems, clusters),
                "metric_v2": summary(previous, base, other, problems, clusters),
                "majority_vote": summary(majority, base, other, problems, clusters),
                "unclustered": summary(fractions, base, other, problems, unclustered),
                "left_pair": sum(1 for task in problems
                                 if task not in fractions[base] or task not in fractions[other]),
                "by_kind": {key: summary(fractions, base, other, [task for task in problems if kind[task] == key],
                                         clusters)
                            for key in kinds.KINDS if any(kind[task] == key for task in problems)},
            }
            if name != "packs":
                part["exact_only"] = summary(fractions, base, other, [task for task in problems if task in exact],
                                             clusters)
                # A7: trusted input only. A8: without the problems whose prefix carries a page picture.
                part["trusted_only"] = summary(fractions, base, other,
                                               [task for task in problems if task in trusted], clusters)
                part["without_pictures"] = summary(fractions, base, other,
                                                   [task for task in problems if task not in pictured], clusters)
                part["per_main_model"] = {
                    label: summary(fractions, base, other, [task for task in problems if model[task] == label],
                                   clusters)
                    for label in sorted({model[task] for task in problems})}
            entry[name] = part
        found[f"{other} vs {base}"] = entry
    return {"arms": per_arm, "contrasts": found, "main_models": len(ordinals)}


def freeze_check(arms: Sequence[Arm], *, as_of: float, asked_by: str, contrasts: Sequence[tuple[str, str]],
                 client: ModelClient | None = None, budget: Budget = Budget(), meter: TokenMeter | None = None,
                 store: Any | None = None, runner: PackRunner | None = None, cache: ArmCache | None = None,
                 packs: Iterable[str] | None = None, verifiers: Iterable[str] | None = None, repeats: int = 3,
                 seed: int = ANALYSIS_SEED, on_progress: Callable[[int, int], None] | None = None,
                 clock: Callable[[], float] = time.time, order: str = "interleaved") -> dict[str, Any]:
    """The pre-registered freeze evaluation (§7): the arms on every hidden problem the vault holds from
    before ``as_of`` (real work, by its captured time) and the packs' hidden tasks, ``repeats`` runs per arm,
    one budget, this module's checker. Returns aggregates only: per arm and split, counts, mean scores, run
    statuses and causes; per contrast ((baseline, arm) names) and split, the primary paired analysis and §7's
    sensitivity analyses. No problem, id, text or per-problem result leaves (E10)."""

    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    if not gate.is_enabled(saved.root) and not audit.vault_exists(saved.root):
        raise EvaluationError("learning_off")
    names = {arm.name for arm in arms}
    if not contrasts or {name for pair in contrasts for name in pair} - names:
        raise EvaluationError("unknown_arm")
    who = f"{_caller()}: {asked_by}".strip(": ")
    vault = _vault(saved)
    allowed = None if verifiers is None else set(verifiers)
    tasks = [task for task in vault.hidden_tasks(asked_by=who, packs=packs)
             if allowed is None or task.verifier.kind in allowed]
    everything = [ReplayCase.from_content(item) for item in vault.replay_tasks(asked_by=who)]
    replay = [case for case in everything if case.created_at <= float(as_of)]
    total = (len(tasks) + len(replay)) * len(arms) * max(1, int(repeats))
    done = [0]

    def counted(_arm: str, _case: CaseResult) -> None:
        done[0] += 1
        if on_progress is not None:
            on_progress(done[0], total)

    found: dict[str, Any] = {}

    def inspect(results: Mapping[str, list[CaseResult]]) -> None:
        found.update(_freeze_analysis(arms, results, tasks, replay, contrasts, seed=seed))

    comparison = _compare(arms, tasks, replay, client=client, judge=None, calibration=None, budget=budget,
                          meter=meter, store=saved, save=False, clock=clock, backend="auto", suite="audit",
                          aggregate_only=True, runner=runner, cache=cache, on_case=counted, repeats=repeats,
                          seed=seed, inspect=inspect, order=order)
    for item in comparison.arms:
        found["arms"][item.arm.name].update({
            "tokens": int(item.measured_tokens), "cloud_tokens": int(item.measured_cloud_tokens),
            "seconds": round(float(item.seconds), 1), "ran": int(item.ran), "cached": int(item.cached),
            "identical": dict(comparison.repeated.get(item.arm.name, {})),
        })
    splits = {name: sum(1 for case in replay if (case.reference_source == "corrected") == (name == "real_corrected"))
              for name in SPLITS[:2]}
    return {"metric_version": METRIC_VERSION, "previous_metric_version": PREVIOUS_METRIC_VERSION,
            "repeats": comparison.repeats, "seed": int(seed),
            "resamples": RESAMPLES, "permutations": PERMUTATIONS, "stopped": bool(comparison.stopped),
            "budget": budget.to_dict(),
            "problems": {**splits, "packs": len(tasks), "after_as_of": len(everything) - len(replay),
                         "untrusted": sum(1 for case in replay if case.untrusted),
                         "with_picture": sum(1 for case in replay if case.has_picture)}, **found}


# ── Strategy trials (L4a, pre-registration §3.3, §6.1, §7): only aggregates leave ──

#: A trial runs once at least this many fresh hidden problems have built up since the last (O82), and draws this
#: many (the newest); H3 counts a generation only when it was judged on at least this many (§7's minimum size).
MIN_FRESH = 30


def _replay_tokens(case: ReplayCase) -> int:
    """About how many tokens one run of a replayed problem sends (its prefix and tools) and answers."""

    from row_bot.learning.calls import CHARS_PER_TOKEN, OUTPUT_ALLOWANCE

    sent = len(json.dumps(list(case.messages), default=str)) + len(json.dumps(list(case.tools or ()), default=str))
    return math.ceil(sent / CHARS_PER_TOKEN) + OUTPUT_ALLOWANCE


def fresh_problems(*, asked_by: str, store: Any | None = None) -> dict[str, int]:
    """How many fresh hidden problems wait for a strategy trial (real work no trial has drawn) and about how many
    tokens one run of the ones a trial would draw costs together: counts only. Never creates a vault."""

    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    if not audit.vault_exists(saved.root):
        return {"fresh": 0, "tokens_per_run": 0}
    who = f"{_caller()}: {asked_by}".strip(": ")
    cases = [ReplayCase.from_content(item) for item in _vault(saved).fresh_tasks(asked_by=who)]
    drawn = sorted(cases, key=lambda case: (case.created_at, case.decision_id))[-MIN_FRESH:]
    return {"fresh": len(cases), "tokens_per_run": sum(_replay_tokens(case) for case in drawn)}


def fresh_count(*, store: Any | None = None) -> int:
    """How many fresh hidden problems wait for a strategy trial: a count only (How Row-Bot learns shows it)."""

    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    return int(_vault(saved).fresh_count()) if audit.vault_exists(saved.root) else 0


def draw_trial(trial_id: str, *, asked_by: str, store: Any | None = None, minimum: int = MIN_FRESH,
               take: int = MIN_FRESH) -> int:
    """Give a strategy trial the newest ``take`` fresh hidden problems (when at least ``minimum`` are fresh, else
    none): from now on they are that trial's, and the next trial's fresh problems are those that arrive after.
    Returns how many."""

    who = f"{_caller()}: {asked_by}".strip(": ")
    return int(_vault(store).draw(str(trial_id), asked_by=who, take=int(take), minimum=int(minimum)))


def trial_size(trial_id: str, *, asked_by: str, store: Any | None = None) -> int:
    """How many of a trial's problems are still held (a count only)."""

    who = f"{_caller()}: {asked_by}".strip(": ")
    return len(_vault(store).drawn_tasks(str(trial_id), asked_by=who))


def judge_trial(trial_id: str, baseline: Arm, changes: Sequence[tuple[str, Arm]], *, asked_by: str,
                client: ModelClient | None = None, budget: Budget = Budget(), meter: TokenMeter | None = None,
                store: Any | None = None, repeats: int = 3, seed: int = ANALYSIS_SEED,
                on_progress: Callable[[int, int], None] | None = None,
                clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Judge a strategy trial's accepted changes on its fresh problems (§6.1): every change against the same
    assistant without it (``baseline``, run once for all of them), ``repeats`` runs per arm, equal budget per
    task. Returns per change (its key) the paired analysis by kind of work (kinds' keys) and overall, and what the
    judging spent; the per-problem pass fractions stay in the vault for the paper's H3 estimate (E10)."""

    from row_bot.learning import kinds
    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    who = f"{_caller()}: {asked_by}".strip(": ")
    vault = _vault(saved)
    replay = [replace(case, kind_of_work=kind_label_of(case))
              for case in (ReplayCase.from_content(item) for item in vault.drawn_tasks(str(trial_id), asked_by=who))]
    arms = [baseline, *(arm for _key, arm in changes)]
    if len({arm.name for arm in arms}) != len(arms):
        raise EvaluationError("arm_names_repeat")
    total = len(replay) * len(arms) * max(1, int(repeats))
    done = [0]

    def counted(_arm: str, _case: CaseResult) -> None:
        done[0] += 1
        if on_progress is not None:
            on_progress(done[0], total)

    kept: dict[str, list[tuple[str, str, str, float, float]]] = {}
    clusters = {case.decision_id: case.conversation_id or case.thread_id or case.decision_id for case in replay}
    family = {case.decision_id: kinds.from_label(case.kind_of_work) for case in replay}

    def inspect(results: Mapping[str, list[CaseResult]]) -> None:
        base = pass_fractions(results[baseline.name])
        for key, arm in changes:
            other = pass_fractions(results[arm.name])
            kept[key] = [(task, clusters.get(task, task), family.get(task, kinds.OTHER), base[task][1], other[task][1])
                         for task in sorted(base) if task in other]

    comparison = _compare(arms, [], replay, client=client, judge=None, calibration=None, budget=budget, meter=meter,
                          store=saved, save=False, clock=clock, backend="auto", suite="audit", aggregate_only=True,
                          on_case=counted, repeats=repeats, seed=seed, kind_key=kinds.from_label, inspect=inspect)
    if not comparison.stopped:
        for key, rows in kept.items():
            vault.put_judgments(str(trial_id), key, rows)
    by_name = {item.arm.name: item for item in comparison.arms}
    found: dict[str, Any] = {}
    for key, arm in changes:
        analysis = (comparison.analysis.get("arms") or {}).get(arm.name) or {}
        found[key] = {"overall": analysis.get("overall") or {}, "by_kind": dict(analysis.get("by_kind") or {})}
    return {"problems": len(replay), "changes": found, "stopped": comparison.stopped,
            "tokens": comparison.tokens, "local_tokens": comparison.local_tokens,
            "measured_tokens": sum(item.measured_tokens for item in by_name.values()),
            "repeats": comparison.repeats, "metric_version": METRIC_VERSION}


def _verdicts(rows: Sequence[tuple[str, str, str, float, float]], weights: Mapping[str, int] | None,
              kind: str) -> str:
    """The keeping rule (§6.1, ``changes.verdict``) on one change's per-problem pairs, the problems weighted by how
    often a resample drew them (None: once each)."""

    from row_bot.learning import changes

    own_sum = own_n = rest_sum = rest_n = 0.0
    for task, _cluster, family, base, arm in rows:
        weight = 1 if weights is None else weights.get(task, 0)
        if not weight:
            continue
        if family == kind:
            own_sum += weight * (arm - base)
            own_n += weight
        else:
            rest_sum += weight * (arm - base)
            rest_n += weight
    own = {"problems": own_n, "difference": own_sum / own_n if own_n else None}
    rest = {"problems": rest_n, "difference": rest_sum / rest_n if rest_n else None}
    return changes.verdict(own, rest)


def h3_estimate(generations: Sequence[Mapping[str, Any]], *, store: Any | None = None, resamples: int = RESAMPLES,
                seed: int = ANALYSIS_SEED, minimum: int = MIN_FRESH) -> dict[str, Any]:
    """The paper's H3 estimate (§7): over generations 1..G, the mean of each generation's improvement yield per
    1M tokens minus the frozen control's, on the fresh problems that judged it (generation g on trial g+1's, the
    last on the final set), with a 95% percentile bootstrap stratified by trial (each trial's problems resampled by
    their source chat, every change's keeping verdict recomputed) and a p-value from the bootstrap distribution.

    ``generations``: per generation, ``{"trial": its judging trial's id, "inherited": side, "control": side}``
    where a side is ``{"changes": [(key, kind of work key)], "tokens": its improvement loop's tokens}``.
    Aggregates only: counts, yields, the estimate and its interval."""

    import random

    vault = _vault(store)
    rows: list[dict[str, Any]] = []
    tables: list[tuple[dict[str, list[tuple[str, str, str, float, float]]], list[str], dict[str, list[str]]]] = []
    for generation in generations:
        judged = vault.judgments(str(generation["trial"]))
        problems = sorted({task for found in judged.values() for task, *_rest in found})
        by_cluster: dict[str, list[str]] = {}
        for found in judged.values():
            for task, cluster, *_rest in found:
                if task not in by_cluster.setdefault(cluster, []):
                    by_cluster[cluster].append(task)
        tables.append((judged, problems, by_cluster))
        row: dict[str, Any] = {"problems": len(problems), "clusters": len(by_cluster)}
        for side in ("inherited", "control"):
            part = generation[side]
            better = sum(_verdicts(judged.get(key, []), None, kind) == "better" for key, kind in part["changes"])
            tokens = int(part["tokens"])
            row[side] = {"accepted": len(part["changes"]), "better": better, "tokens": tokens,
                         "yield": round(better / (tokens / 1e6), 6) if tokens else 0.0}
        row["difference"] = round(row["inherited"]["yield"] - row["control"]["yield"], 6)
        rows.append(row)
    count = len(rows)
    base = {"generations": rows, "resamples": int(resamples), "seed": int(seed), "minimum": int(minimum)}
    if not count:
        return {**base, "estimate": None, "low": None, "high": None, "p_value": None, "enough": False}
    estimate = sum(row["difference"] for row in rows) / count
    rng = random.Random(int(seed))
    boots: list[float] = []
    for _ in range(int(resamples)):
        total = 0.0
        for generation, (judged, _problems, by_cluster) in zip(generations, tables):
            keys = sorted(by_cluster)
            weights: dict[str, int] = {}
            for cluster in (rng.choice(keys) for _ in keys) if keys else ():
                for task in by_cluster[cluster]:
                    weights[task] = weights.get(task, 0) + 1
            yields = []
            for side in ("inherited", "control"):
                part = generation[side]
                better = sum(_verdicts(judged.get(key, []), weights, kind) == "better" for key, kind in part["changes"])
                tokens = int(part["tokens"])
                yields.append(better / (tokens / 1e6) if tokens else 0.0)
            total += yields[0] - yields[1]
        boots.append(total / count)
    boots.sort()
    below = sum(1 for value in boots if value <= 0)
    above = sum(1 for value in boots if value >= 0)
    p_value = min(1.0, 2 * min(below, above) / len(boots)) if boots else None
    enough = count >= 3 and all(row["problems"] >= int(minimum) for row in rows)
    return {**base, "estimate": round(estimate, 6), "low": round(_percentile(boots, 0.025), 6),
            "high": round(_percentile(boots, 0.975), 6), "p_value": None if p_value is None else round(p_value, 6),
            "enough": enough}


def audit_counts(*, asked_by: str, store: Any | None = None) -> dict[str, int]:
    """How many hidden problems the vault holds (packs, real work uncorrected and corrected, by fidelity,
    retired) and how many held-out corrections wait for it: counts only. Never creates a vault."""

    from row_bot.learning import holdout
    from row_bot.learning.store import LearningStore

    saved = store if store is not None else LearningStore()
    waiting = holdout.waiting_for_vault(saved) if saved.exists() else set()
    counts = {"vault": 0, "pack_tasks": 0, "uncorrected": 0, "corrected": 0, "exact": 0, "redacted": 0,
              "retired": 0, "corrections_waiting": len(waiting), "trusted": 0, "untrusted": 0, "with_picture": 0,
              "without_picture": 0}
    if not audit.vault_exists(saved.root):
        return counts
    who = f"{_caller()}: {asked_by}".strip(": ")
    vault = _vault(saved)
    summary = vault.summary(asked_by=who)
    held = [ReplayCase.from_content(item) for item in vault.replay_tasks(asked_by=who)]
    corrected = sum(1 for case in held if case.reference_source == "corrected")
    counts.update({
        "vault": 1, "pack_tasks": int(summary.get("pack_tasks", 0)), "uncorrected": len(held) - corrected,
        "corrected": corrected, "exact": sum(1 for case in held if case.fidelity == "exact"),
        "redacted": sum(1 for case in held if case.fidelity == "redacted"),
        "retired": int(summary.get("retired_replay_tasks", 0)),
        "corrections_waiting": len(waiting - {case.decision_id for case in held}),
        # A7's and A8's splits of the real hidden problems: trusted or not, with a page picture or not.
        "untrusted": sum(1 for case in held if case.untrusted),
        "trusted": sum(1 for case in held if not case.untrusted),
        "with_picture": sum(1 for case in held if case.has_picture),
        "without_picture": sum(1 for case in held if not case.has_picture),
    })
    return counts


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
    "ANALYSIS_SEED",
    "Action",
    "Arm",
    "ArmCache",
    "ArmResult",
    "AuditLook",
    "Budget",
    "CACHE_DAYS",
    "LimitReached",
    "PackRunner",
    "SPLITS",
    "audit_counts",
    "audit_size",
    "MIN_FRESH",
    "draw_trial",
    "fresh_count",
    "fresh_problems",
    "h3_estimate",
    "judge_trial",
    "trial_size",
    "Focus",
    "kind_label_of",
    "freeze_check",
    "holm",
    "CalibrationCase",
    "Calibration",
    "CaseResult",
    "Comparison",
    "CorrectedAction",
    "EvaluationError",
    "Judge",
    "LedgerModelClient",
    "MALFORMED_CALL",
    "MARK_HELD_OUT",
    "METRIC_VERSION",
    "PREVIOUS_METRIC_VERSION",
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
    "interval_verdict",
    "is_malformed_call",
    "is_novel",
    "paired_analysis",
    "pass_fractions",
    "refresh_audit",
    "replay_fixture_for",
    "run_pack_task",
    "run_replay_case",
    "sign_test",
    "wilson_half_width",
]
