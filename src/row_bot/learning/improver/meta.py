"""Strategy trials: how Row-Bot gets better at getting better (L4a; D15, D16, O82; pre-registration §4, §6.1, §7).

**When**: whenever at least :data:`~row_bot.learning.evaluation.MIN_FRESH` fresh hidden problems have built up
since the last trial (O82), in quiet hours after the night's look and tests, and only when the trials' share of
the month allows it: all trials together use at most :data:`TRIAL_SHARE` of the monthly Learning limit.

**Arms**: the method in use (the incumbent), :data:`CHALLENGERS` new method(s) grown from a stepping stone of the
archive, and the **frozen control** (the method in use two trials back; the first method for the first two trials).
When the control is the incumbent it is the same arm, run once. Every arm starts from the same assistant (the
lessons and ways of working in use now), sees the same evidence, uses the same tools, and gets the **same
budget**: tokens (cloud and local) and graphics-card time. Its calls are ``learning_meta``.

**Each arm runs the improvement loop its own way** (its issue priority, method choice, examples, budget split,
prompts and, with practice on, its practice: L4b) and keeps its drafts to itself: they are never shown to the person
or used. It tests them on the person's hidden work **except the trial's fresh problems** (and on practice tasks it
made for them), and accepts those its tests call better (at most :data:`MAX_ACCEPTED`).

**Judging** (§6.1): every accepted change against the same assistant without it on the trial's fresh problems,
:data:`REPEATS` runs each; a change is better by the keeping rule (above 0 on its kind of work, not below -10
points on the rest). An arm's **improvement yield** is its better changes per 1M tokens its loop spent. The
winner is inherited and **switches automatically** (D16), with Undo and a line in the morning summary; a tie
keeps the incumbent. Every method, kept or not, stays in the archive with its results.

**The final set** (§7): at the freeze, every generation and the frozen control at equal budget on the fresh
problems no trial used (:func:`run_final`), judged the same way, with no switch.

Nothing here reads the hidden tests: ``evaluation`` draws the fresh problems, runs them and keeps the per-problem
judgments sealed; only counts come back (E10).
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from row_bot import learning as gate
from row_bot.learning.contracts import Attempt, Cost, Ref, TestRun, new_id
from row_bot.learning.improver import archive
from row_bot.learning.improver import strategy as strategy_module
from row_bot.learning.improver.strategy import Strategy, StrategyError

logger = logging.getLogger(__name__)

PURPOSE = "learning_meta"
#: Trials together use at most this share of the monthly Learning limit (pre-registration §4).
TRIAL_SHARE = 0.20
#: Runs per arm of every judged problem (pre-registration §4).
REPEATS = 3
CHALLENGERS = 1
#: The changes an arm may accept in one trial: each is judged on every fresh problem, 3 runs each, so this bounds
#: a trial's judging (with GPT-6.1 Sol about 1.8M tokens a change on 30 problems of S1's seeded work).
MAX_ACCEPTED = 1
#: Each arm's equal budget: at most this many tokens (cloud and local), and no trial when fewer would be left.
ARM_TOKENS_MAX = 2_000_000
ARM_TOKENS_MIN = 150_000
#: Each arm's equal graphics-card time (a local main model's inference); trial arms never train.
ARM_GPU_SECONDS = 3600.0
STATE_KEY = "meta.trial"
NIGHT_KEY = "meta.night"
ASKED_BY = "strategy trial"


class ArmMeter:
    """One arm's equal budget inside the trial's share: tokens, cloud and local alike, and graphics-card time.
    Cloud calls are also counted against the trial's share (``parent``)."""

    def __init__(self, parent: Any, tokens: int, gpu_seconds: float = ARM_GPU_SECONDS) -> None:
        self.parent = parent
        self.cap = max(0, int(tokens))
        self.gpu_cap = max(0.0, float(gpu_seconds))
        self.spent = 0
        self.local_tokens = 0
        self.gpu_seconds = 0.0
        self.refused = 0
        self.limit = getattr(parent, "limit", 0)

    @property
    def used(self) -> int:
        return self.spent + self.local_tokens

    @property
    def remaining(self) -> int:
        return max(0, min(self.cap - self.used, int(getattr(self.parent, "remaining", self.cap))))

    @property
    def exhausted(self) -> bool:
        return self.refused > 0 or self.used >= self.cap or self.gpu_seconds >= self.gpu_cap

    def allows(self, estimated_tokens: int, *, local: bool = False) -> bool:
        if self.used + max(0, int(estimated_tokens)) > self.cap or (local and self.gpu_seconds >= self.gpu_cap):
            self.refused += 1
            return False
        if not local and not self.parent.allows(estimated_tokens, local=False):
            self.refused += 1
            return False
        return True

    def charge(self, tokens: int, *, local: bool = False) -> None:
        if local:
            self.local_tokens += max(0, int(tokens))
        else:
            self.spent += max(0, int(tokens))
            self.parent.charge(tokens, local=False)

    def charge_gpu(self, seconds: float) -> None:
        self.gpu_seconds += max(0.0, float(seconds))

    def note(self) -> str:
        return "Stopped: this method's share of the trial was spent."


class _TimedClient:
    """A diagnosis client whose local calls charge the arm's graphics-card time."""

    def __init__(self, client: Any, meter: ArmMeter) -> None:
        self.client = client
        self.meter = meter

    def invoke(self, messages: Sequence[Any], *, tools: Any, model_ref: str | None, purpose: str = PURPOSE) -> Any:
        started = time.monotonic()
        reply = self.client.invoke(messages, tools=tools, model_ref=model_ref, purpose=PURPOSE)
        if getattr(reply, "local", False):
            self.meter.charge_gpu(time.monotonic() - started)
        return reply

    def is_local(self, model_ref: str | None) -> bool:
        return self.client.is_local(model_ref)


# ── When a trial is due ─────────────────────────────────────────────────────


def trial_room(store: Any, *, now: float | None = None) -> int:
    """Tokens the trials may still use this month: their share of the monthly limit less what trials used, and
    never more than the month has left."""

    from row_bot.learning import spending

    limit = int(gate.read_settings(store.root).monthly_token_limit)
    used = spending.used_this_month(now=now, purposes=(PURPOSE,))
    left = spending.allowance(now=now, limit=limit).remaining
    return max(0, min(int(limit * TRIAL_SHARE) - used, left))


@dataclass(frozen=True)
class Plan:
    """Whether a trial can start now, and how big each arm's equal budget is."""

    state: str  # ready · resume · fewer_fresh · limit · off
    fresh: int = 0
    arms: int = 0
    arm_tokens: int = 0
    reserve: int = 0
    room: int = 0


def plan(store: Any, *, local: bool = False, now: float | None = None, quick: bool = False) -> Plan:
    """A trial's plan: ``resume`` when one is half done; ``fewer_fresh`` below 30 fresh problems; ``limit`` when
    the trials' share can't give every arm at least :data:`ARM_TOKENS_MIN` after keeping enough for the judging.
    ``local``: the main model runs on this computer (its tokens don't count toward the month). ``quick`` (the
    screen): counts only, the judging's share estimated as no larger than the arms'."""

    from row_bot.learning import evaluation

    if not store.exists() or not gate.is_enabled(store.root):
        return Plan("off")
    if _state(store):
        return Plan("resume")
    if quick:
        found = {"fresh": evaluation.fresh_count(store=store), "tokens_per_run": 0}
    else:
        found = evaluation.fresh_problems(asked_by=ASKED_BY, store=store)
    fresh = int(found["fresh"])
    incumbent = archive.current(store, create=False)
    control = archive.frozen_control(store, create=False)
    arms = 1 + CHALLENGERS + (incumbent is not None and control is not None and control.id != incumbent.id)
    if fresh < evaluation.MIN_FRESH:
        return Plan("fewer_fresh", fresh, arms)
    if local:
        return Plan("ready", fresh, arms, ARM_TOKENS_MAX)
    reserve = (1 + arms * MAX_ACCEPTED) * int(found["tokens_per_run"]) * REPEATS
    room = trial_room(store, now=now)
    per_arm = min(ARM_TOKENS_MAX, max(0, room - reserve) // arms)
    if per_arm < ARM_TOKENS_MIN:
        return Plan("limit", fresh, arms, per_arm, reserve, room)
    return Plan("ready", fresh, arms, per_arm, reserve, room)


def _state(store: Any) -> dict[str, Any] | None:
    try:
        found = json.loads(store.meta_value(STATE_KEY, "") or "null")
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _save_state(store: Any, state: Mapping[str, Any] | None) -> None:
    store.set_meta_values({STATE_KEY: json.dumps(state, sort_keys=True) if state is not None else "null"})


# ── Growing a challenger ────────────────────────────────────────────────────

PROMPT_SYSTEM = ("You improve how Row-Bot's Learning reads a recurring mistake and drafts one fix for it. Write up to "
                 "three short, general sentences of guidance for that step: how to read the evidence and which kind of "
                 "fix to draft. Never mention money, limits, approvals, tools, permissions, tests, scores or anyone's "
                 "data. Reply with the guidance only.")


def _clamp(value: float, item: Any) -> Any:
    low, high = item.metadata["low"], item.metadata["high"]
    value = min(high, max(low, value))
    return int(round(value)) if item.metadata.get("integer") else round(float(value), 3)


def mutate(parent: Strategy, rng: random.Random) -> Strategy:
    """One change to one evolvable part of ``parent`` (numbers, a preference or a switch), within bounds; its
    practice only in a build that practises (L4b)."""

    from dataclasses import fields

    parts = ("issues", "methods", "examples", "budget") + (("practice",) if gate.practice_enabled() else ())
    for _attempt in range(20):
        part_name = rng.choice(parts)
        part = getattr(parent, part_name)
        if part_name == "methods":
            prefer = list(part.prefer)
            move = rng.choice(("front", "swap", "drop", "add"))
            if move == "front" and len(prefer) > 1:
                prefer.insert(0, prefer.pop(rng.randrange(1, len(prefer))))
            elif move == "swap" and len(prefer) > 1:
                index = rng.randrange(len(prefer) - 1)
                prefer[index], prefer[index + 1] = prefer[index + 1], prefer[index]
            elif move == "drop" and len(prefer) > 2:
                prefer.pop(rng.randrange(len(prefer)))
            elif move == "add":
                missing = [item for item in strategy_module.METHODS if item not in prefer]
                if missing:
                    prefer.append(rng.choice(missing))
            changed = replace(part, prefer=tuple(prefer))
        else:
            item = rng.choice(fields(part))
            value = getattr(part, item.name)
            if isinstance(value, bool):
                changed = replace(part, **{item.name: not value})
            elif item.name == "order":
                options = [option for option in strategy_module.EXAMPLE_ORDERS if option != value]
                changed = replace(part, **{item.name: rng.choice(options)})
            elif item.metadata:
                if item.metadata.get("integer"):
                    step = max(1, round((item.metadata["high"] - item.metadata["low"]) / 4))
                    proposed = value + rng.choice((-step, step))
                else:
                    proposed = value * rng.choice((0.5, 1.5, 2.0)) if value else rng.choice((0.5, 1.0))
                changed = replace(part, **{item.name: _clamp(proposed, item)})
            else:
                continue
        candidate = replace(parent, **{part_name: changed})
        if strategy_module.differences(parent, candidate):
            return candidate
    return parent


def propose(store: Any, *, seed: str, client: Any = None, meter: Any = None) -> tuple[Strategy, archive.Entry]:
    """A challenger and the stepping stone it grew from: the method in use half the time, else another niche's
    best (quality-diversity). One change: a mutation of its numbers or preferences, or (when a model is given and
    the coin says so) new words for its own instructions, written by one ``learning_meta`` call."""

    rng = random.Random(seed)
    stones = archive.stepping_stones(store)
    incumbent = archive.current(store)
    others = [entry for entry in stones if entry.id != incumbent.id]
    parent = incumbent if not others or rng.random() < 0.5 else rng.choice(others)
    if client is not None and rng.random() < 0.25:
        words = _new_words(store, parent, client=client, meter=meter)
        if words:
            try:
                return replace(parent.strategy, prompts=replace(parent.strategy.prompts, diagnosis=words)), parent
            except StrategyError:
                logger.info("A proposed learning method's own words weren't allowed; it changes its numbers instead")
    return mutate(parent.strategy, rng), parent


def _new_words(store: Any, parent: archive.Entry, *, client: Any, meter: Any) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    from row_bot.learning.calls import estimate_tokens, text_of

    record = archive.yields(store).get(parent.id) or []
    track = (f"In {len(record)} earlier trials it kept {sum(record) / len(record):.2f} ideas per million tokens."
             if record else "It hasn't been tried yet.")
    current = parent.strategy.prompts.diagnosis or "(none: it follows the standard instructions only)"
    messages = [SystemMessage(content=PROMPT_SYSTEM),
                HumanMessage(content=f"The method's current guidance: {current}\n{track}\nWrite new guidance.")]
    if meter is not None and not meter.allows(estimate_tokens(messages), local=client.is_local(None)):
        return ""
    try:
        reply = client.invoke(messages, tools=None, model_ref=None, purpose=PURPOSE)
    except Exception:  # noqa: BLE001 - no new words: the challenger changes its numbers instead
        logger.info("Learning couldn't ask for a learning method's new words")
        return ""
    if meter is not None:
        meter.charge(reply.tokens, local=reply.local)
    return " ".join(text_of(reply.message).split())[: strategy_module.PROMPT_LIMIT]


# ── One arm's improvement loop ──────────────────────────────────────────────


@dataclass
class ArmRun:
    """What one arm did with its budget (its drafts stay with the trial)."""

    role: str
    release: str
    version: int
    diagnosed: int = 0
    drafted: int = 0
    refused: int = 0
    tested: int = 0
    accepted: list[dict[str, Any]] = field(default_factory=list)
    tokens: int = 0
    local_tokens: int = 0
    gpu_seconds: float = 0.0
    stopped: bool = False

    @property
    def spent(self) -> int:
        return self.tokens + self.local_tokens

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "release": self.release, "version": self.version, "diagnosed": self.diagnosed,
                "drafted": self.drafted, "refused": self.refused, "tested": self.tested, "accepted": self.accepted,
                "tokens": self.tokens, "local_tokens": self.local_tokens, "gpu_seconds": round(self.gpu_seconds, 1),
                "stopped": self.stopped}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArmRun:
        return cls(str(data["role"]), str(data["release"]), int(data["version"]), int(data.get("diagnosed") or 0),
                   int(data.get("drafted") or 0), int(data.get("refused") or 0), int(data.get("tested") or 0),
                   [dict(item) for item in data.get("accepted") or ()], int(data.get("tokens") or 0),
                   int(data.get("local_tokens") or 0), float(data.get("gpu_seconds") or 0.0),
                   bool(data.get("stopped")))


def _draft(issue: Any, found: Any, key: str) -> dict[str, Any]:
    """A trial arm's draft, shaped like Learning's proposals so its own tests read it the same way (never saved)."""

    from row_bot import evolution
    from row_bot.learning import kinds

    learning = {"issue": issue.key, "kind_of_work": issue.kind, "facet": issue.facet, "trial_draft": True}
    title = found.title or found.skill_name or kinds.label(issue.kind)
    types = {"lesson": "lesson", "way_of_working": "behaviour", "create_skill": "create_skill", "patch_skill": "patch_skill"}
    if found.change in ("lesson", "way_of_working"):
        payload: dict[str, Any] = {"text": found.text, "kind_of_work": issue.kind, "learning": learning}
        if found.change == "way_of_working":
            payload["slot"] = found.slot
    elif found.change == "create_skill":
        payload = {"name": evolution.normalize_skill_name(found.skill_name or title), "description": found.skill_description,
                   "instructions": found.instructions, "learning": learning}
    else:
        payload = {"target_skill": found.skill_name, "updated_instructions": found.instructions, "learning": learning}
    return {"id": key, "proposal_type": types[found.change], "title": title, "payload": payload}


def run_arm(store: Any, entry: archive.Entry, *, role: str, trial_id: str, budget: int, parent_meter: Any,
            client: Any, tester_factory: Callable[..., Any], should_stop: Callable[[], bool], now: float,
            check_model: Callable[[], None] | None = None) -> ArmRun:
    """One arm's improvement loop under its method and its equal budget: issues, a diagnosis each, its drafts
    (E9 as always) and, with practice on, practice tasks for each draft's issue by its method (L4b); its own tests
    of them on hidden work outside the trial's fresh problems; better ones are accepted. Nothing it drafts or makes
    is saved as a change or shown."""

    from row_bot.learning import diagnosis
    from row_bot.learning.improver.strategy import practising

    method = entry.strategy
    meter = ArmMeter(parent_meter, budget)
    run = ArmRun(role, entry.id, entry.version)
    drafts: list[dict[str, Any]] = []
    practice: dict[str, list[Any]] = {}
    practises = practising(method) and method.practice.made > 0

    def sink(issue: Any, found: Any, tokens: int) -> Any:
        if found is None or found.change not in diagnosis.DRAFTS or not diagnosis._complete(found):  # noqa: SLF001
            return diagnosis.Outcome(issue.key, issue.kind, "unreadable" if found is None else found.change,
                                     tokens=tokens)
        problems = diagnosis.e9_problems(issue, found)
        if problems:
            run.refused += 1
            return diagnosis.Outcome(issue.key, issue.kind, "refused", refused=tuple(problems), tokens=tokens)
        draft = _draft(issue, found, f"{trial_id}-{role}-{len(drafts) + 1}")
        draft["payload"]["learning"]["never_automatic"] = all(item.untrusted for item in issue.evidence)
        drafts.append(draft)
        if practises and len(drafts) <= method.budget.tests and not meter.exhausted:
            from row_bot.learning.practice import generate

            made = generate.make(store, issue, client=_TimedClient(client, meter), count=method.practice.made,
                                 now=now, meter=meter, purpose=PURPOSE, method=method,
                                 asked_by=f"trial {trial_id} {role}")
            practice[draft["id"]] = made.kept
        return diagnosis.Outcome(issue.key, issue.kind, found.change, proposal_id=draft["id"], tokens=tokens)

    report = diagnosis.run(store, client=_TimedClient(client, meter), night=f"trial:{trial_id}:{role}", now=now,
                           meter=meter, should_stop=should_stop, save=False, method=method, sink=sink,
                           purpose=PURPOSE, check_model=check_model, dedupe=False)
    run.diagnosed, run.drafted = report.diagnosed, len(drafts)
    run.stopped = report.state == "stopped"
    tester = tester_factory(method)
    for draft in drafts[: method.budget.tests]:
        if run.stopped or should_stop() or len(run.accepted) >= MAX_ACCEPTED or meter.exhausted:
            run.stopped = run.stopped or should_stop()
            break
        result = tester.measure(draft, should_stop=should_stop, meter=meter, leave_out=trial_id,
                                practice=practice.get(draft["id"]) if practises else None)
        if result.verdict == "stopped":
            run.stopped = result.stopped == "stopped"
            break
        run.tested += 1
        if result.verdict == "better":
            run.accepted.append({"key": draft["id"], "kind_of_work": draft["payload"]["learning"]["kind_of_work"],
                                 "proposal": draft})
    run.tokens, run.local_tokens, run.gpu_seconds = meter.spent, meter.local_tokens, meter.gpu_seconds
    return run


def _ledger() -> Any:
    from row_bot.learning.calls import LedgerModelClient

    return LedgerModelClient()


# ── Judging ─────────────────────────────────────────────────────────────────


def judge(store: Any, trial_id: str, runs: Sequence[ArmRun], *, tester: Any, meter: Any, client: Any,
          should_stop: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """Every accepted change against the same assistant without it on the trial's fresh problems (§6.1). Returns
    per change its verdict, per arm its better changes, and what judging spent."""

    from row_bot.learning import changes, evaluation, kinds
    from row_bot.learning.calls import Purposed

    main_ref = tester._main()  # noqa: SLF001 - the main model the arms were tested on
    lessons, ways = changes.context_in_use()
    baseline = evaluation.Arm("without", model_ref=main_ref or None, lessons=lessons, ways=ways,
                              subject_kind="strategy")
    arms: list[tuple[str, Any]] = []
    for run in runs:
        for number, item in enumerate(run.accepted, 1):
            with_arm = tester.arms(item["proposal"], main_ref)[1]
            arms.append((item["key"], replace(with_arm, name=f"{run.role}-{number}", subject_kind="strategy",
                                              subject_id=run.release)))
    if not arms:
        return {"problems": evaluation.trial_size(trial_id, asked_by=ASKED_BY, store=store), "verdicts": {},
                "tokens": 0, "local_tokens": 0, "stopped": ""}

    def stop(_done: int, _total: int) -> None:
        if should_stop():
            raise _Stopped()

    try:
        judged = evaluation.judge_trial(trial_id, baseline, arms, asked_by=ASKED_BY,
                                        client=Purposed(client or _ledger(), PURPOSE),
                                        budget=evaluation.Budget(task_tokens=tester.plan.task_tokens), meter=meter,
                                        store=store, repeats=REPEATS, on_progress=stop)
    except _Stopped:
        return {"stopped": "stopped"}
    verdicts: dict[str, str] = {}
    by_key = {item["key"]: item for run in runs for item in run.accepted}
    for key, part in judged["changes"].items():
        kind = kinds.from_label(str(by_key[key]["kind_of_work"] or "")) if by_key[key].get("kind_of_work") else kinds.OTHER
        by_kind = part.get("by_kind") or {}
        verdicts[key] = changes.verdict(dict(by_kind.get(kind) or {}), changes.rest_of(by_kind, kind))
    return {"problems": judged["problems"], "verdicts": verdicts, "tokens": judged["tokens"],
            "local_tokens": judged["local_tokens"], "stopped": judged["stopped"]}


class _Stopped(Exception):
    pass


def improvement_yield(better: int, tokens: int) -> float:
    """Better changes per 1M tokens the loop spent (§6.1; 0 when it spent nothing)."""

    return round(better / (tokens / 1e6), 6) if tokens > 0 else 0.0


# ── A trial ─────────────────────────────────────────────────────────────────


@dataclass
class TrialReport:
    """How tonight's trial went (counts only; the Overnight line and the morning summary read it)."""

    night: str
    state: str  # done · stopped · fewer_fresh · limit · off · failed
    trial: str = ""
    problems: int = 0
    switched: dict[str, Any] | None = None
    kept: int = 0
    tokens: int = 0
    plan: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"night": self.night, "state": self.state, "trial": self.trial, "problems": self.problems,
                "switched": self.switched, "kept": self.kept, "tokens": self.tokens, "plan": dict(self.plan)}


def last_night(store: Any) -> dict[str, Any] | None:
    if store is None or not store.exists():
        return None
    try:
        found = json.loads(store.meta_value(NIGHT_KEY, "") or "null")
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def _note(store: Any, report: TrialReport) -> TrialReport:
    store.set_meta_values({NIGHT_KEY: json.dumps(report.to_dict(), sort_keys=True)})
    return report


def run_trial(store: Any, *, night: str, client: Any, tester_factory: Callable[..., Any],
              should_stop: Callable[[], bool] = lambda: False, now: float | None = None,
              check_model: Callable[[], None] | None = None, local: bool = False,
              meter: Any = None) -> TrialReport:
    """Tonight's trial, when one is due: draw the fresh problems, run every arm at equal budget, judge, inherit the
    winner. A stop (the person came back, Learning paused) keeps what is done and goes on next time."""

    from row_bot.learning.spending import TokenMeter

    moment = float(now if now is not None else store.now())
    state = _state(store)
    if state is None:
        found = plan(store, local=local, now=moment)
        if found.state != "ready":
            return _note(store, TrialReport(night, found.state, plan={"fresh": found.fresh, "arms": found.arms,
                                                                      "arm_tokens": found.arm_tokens}))
        state = _start(store, found, client=client, now=moment, meter=meter)
        if state is None:
            return _note(store, TrialReport(night, "fewer_fresh", plan={"fresh": found.fresh}))
    trial_meter = meter if meter is not None else TokenMeter(10**12 if local else max(0, trial_room(store, now=moment)))
    runs = {item["role"]: ArmRun.from_dict(item) for item in state.get("runs") or ()}
    for arm in state["arms"]:
        if arm["role"] in runs and not runs[arm["role"]].stopped:
            continue
        if should_stop():
            return _note(store, TrialReport(night, "stopped", state["id"], int(state["problems"])))
        entry = archive.get(store, arm["release"])
        if entry is None:
            continue
        run = run_arm(store, entry, role=arm["role"], trial_id=state["id"], budget=int(state["arm_tokens"]),
                      parent_meter=trial_meter, client=client, tester_factory=tester_factory, should_stop=should_stop,
                      now=moment, check_model=check_model)
        if run.stopped:
            return _note(store, TrialReport(night, "stopped", state["id"], int(state["problems"])))
        runs[arm["role"]] = run
        state["runs"] = [item.to_dict() for item in runs.values()]
        _save_state(store, state)
    ordered = [runs[arm["role"]] for arm in state["arms"] if arm["role"] in runs]
    tester = tester_factory(archive.current(store).strategy)
    judged = judge(store, state["id"], ordered, tester=tester, meter=trial_meter, client=client, should_stop=should_stop)
    if judged.get("stopped") == "stopped":
        return _note(store, TrialReport(night, "stopped", state["id"], int(state["problems"])))
    if judged.get("stopped"):
        # The trials' share ran out while judging: nothing is decided on part of the problems; the judging runs
        # again when the share allows (the arms' work is kept).
        return _note(store, TrialReport(night, "limit", state["id"], int(state["problems"])))
    report = _finish(store, state, ordered, judged, now=moment, night=night)
    _save_state(store, None)
    from row_bot.learning import changed

    changed()
    return _note(store, report)


def _start(store: Any, found: Plan, *, client: Any, now: float, meter: Any = None) -> dict[str, Any] | None:
    from row_bot.learning import evaluation

    trial_id = new_id("trial")
    drawn = evaluation.draw_trial(trial_id, asked_by=ASKED_BY, store=store)
    if not drawn:
        return None
    incumbent = archive.current(store)
    control = archive.frozen_control(store)
    arms = [{"role": "incumbent", "release": incumbent.id}]
    for number in range(1, CHALLENGERS + 1):
        method, parent = propose(store, seed=f"{trial_id}:{number}", client=client, meter=meter)
        entry = archive.add(store, method, parents=(parent.id,), status="candidate", at=now)
        arms.append({"role": f"challenger{number if CHALLENGERS > 1 else ''}", "release": entry.id,
                     "parent": parent.id})
    if control.id != incumbent.id:
        arms.append({"role": "control", "release": control.id})
    state = {"id": trial_id, "at": round(now, 3), "problems": drawn, "arm_tokens": int(found.arm_tokens),
             "arms": arms, "incumbent": incumbent.id, "control": control.id, "runs": []}
    _save_state(store, state)
    return state


def _side(run: ArmRun | None) -> dict[str, Any]:
    if run is None:
        return {"changes": [], "tokens": 0}
    from row_bot.learning import kinds

    return {"changes": [[item["key"], kinds.from_label(str(item.get("kind_of_work") or "")) if item.get("kind_of_work")
                         else kinds.OTHER] for item in run.accepted], "tokens": run.spent, "release": run.release}


def _finish(store: Any, state: Mapping[str, Any], runs: Sequence[ArmRun], judged: Mapping[str, Any], *, now: float,
            night: str, final: bool = False) -> TrialReport:
    """Yields, the winner (inherited unless this is the final set), the archive's records and the H3 entry."""

    verdicts = judged.get("verdicts") or {}
    arms = []
    for run in runs:
        better = sum(1 for item in run.accepted if verdicts.get(item["key"]) == "better")
        arms.append({"role": run.role, "release": run.release, "version": run.version, "diagnosed": run.diagnosed,
                     "drafted": run.drafted, "tested": run.tested, "accepted": len(run.accepted), "better": better,
                     "tokens": run.spent, "cloud_tokens": run.tokens, "gpu_hours": round(run.gpu_seconds / 3600, 4),
                     "yield": improvement_yield(better, run.spent)})
    incumbent = next(item for item in arms if item["role"] == "incumbent") if not final else None
    winner = incumbent
    if not final:
        for item in arms:
            if item["role"] != "incumbent" and item["yield"] > winner["yield"]:
                winner = item
    summary: dict[str, Any] = {
        "id": state["id"], "at": state["at"], "finished_at": round(now, 3), "problems": int(judged.get("problems") or
                                                                                         state["problems"]),
        "arm_tokens": int(state["arm_tokens"]), "repeats": REPEATS, "arms": arms, "final": final,
        "incumbent": state.get("incumbent"), "control": state.get("control"),
        "winner": winner["release"] if winner else None, "switched": False,
        "judging_tokens": int(judged.get("tokens") or 0) + int(judged.get("local_tokens") or 0),
    }
    done = [item for item in archive.trials(store) if not item.get("final")]
    # H3 (§7): this trial judges the method the previous trial left in use against the frozen control (the final
    # set judges the last one); the first trial judges no generation.
    if final or done:
        inherited = next((run for run in runs if run.release == state.get("incumbent")), None)
        control = next((run for run in runs if run.release == state.get("control")), None)
        if inherited is not None and control is not None:
            # The control is the incumbent itself for the first two trials: the same arm, so the same yield.
            summary["judged"] = {"inherited": _side(inherited), "control": _side(control),
                                 "same": control is inherited}
    report = TrialReport(night, "done", state["id"], summary["problems"],
                         kept=sum(item["better"] for item in arms),
                         tokens=sum(item["tokens"] for item in arms) + summary["judging_tokens"])
    for item in arms:
        _record_run(store, state["id"], item, summary, now=now)
    if not final:
        for arm in state["arms"]:
            if arm["role"].startswith("challenger"):
                entry = archive.get(store, arm["release"])
                won = winner is not None and winner["release"] == arm["release"]
                if entry is not None and not won:
                    archive.set_status(store, entry, "not_used")
                _record_attempt(store, state, arm, next(item for item in arms if item["role"] == arm["role"]),
                                incumbent, won=won, now=now)
        if winner is not None and winner["release"] != state.get("incumbent"):
            entry = archive.get(store, winner["release"])
            before = archive.get(store, str(state.get("incumbent")))
            if entry is not None:
                archive.switch(store, entry, by="trial", trial=state["id"], at=now)
                summary["switched"] = True
                words = strategy_module.change_words(before.strategy, entry.strategy) if before else ""
                report.switched = {"from": before.version if before else None, "to": entry.version, "words": words}
                logger.info("Learning switched to learning method v%s after a trial", entry.version)
    archive.record_trial(store, summary)
    return report


def _record_run(store: Any, trial_id: str, arm: Mapping[str, Any], summary: Mapping[str, Any], *, now: float) -> None:
    body = json.dumps({"format": "row_bot.learning.strategy_trial", "arm": dict(arm), "problems": summary["problems"],
                       "repeats": REPEATS}, sort_keys=True).encode("utf-8")
    try:
        blob = store.put_blob(body)
        store.put(TestRun(id=new_id("run"), subject_kind="strategy", suite="audit", started_at=float(summary["at"]),
                          subject_id=str(arm["release"]), status="completed", passed=int(arm["better"]),
                          failed=int(arm["accepted"]) - int(arm["better"]), total=int(arm["accepted"]),
                          budget_tokens=int(summary["arm_tokens"]),
                          cost=Cost(tokens=int(arm["cloud_tokens"]), gpu_seconds=float(arm["gpu_hours"]) * 3600),
                          comparison_set=trial_id, results_blob=blob, ended_at=max(float(now), float(summary["at"]))))
    except Exception:  # noqa: BLE001 - provenance only; the trial's summary is kept either way
        logger.debug("Learning couldn't keep a trial arm's record", exc_info=True)


def _record_attempt(store: Any, state: Mapping[str, Any], arm: Mapping[str, Any], result: Mapping[str, Any],
                    incumbent: Mapping[str, Any] | None, *, won: bool, now: float) -> None:
    entry = archive.get(store, arm["release"])
    parent = archive.get(store, str(arm.get("parent") or ""))
    words = strategy_module.change_words(parent.strategy, entry.strategy) if entry and parent else ""
    try:
        store.put(Attempt(id=new_id("att"), change_kind="strategy", hypothesis=(words or "a new learning method")[:4000],
                          created_at=float(state["at"]), strategy_release_id=str(arm["release"]),
                          release_id=str(arm["release"]), outcome="improved" if won else "not_better", kept=won,
                          reason=(f"yield {result['yield']} vs {incumbent['yield'] if incumbent else 0} per 1M tokens on "
                                  f"{state['problems']} fresh problems")[:2000],
                          cost=Cost(tokens=int(result["cloud_tokens"]), gpu_seconds=float(result["gpu_hours"]) * 3600),
                          evidence=(Ref("release", str(arm["release"])),), finished_at=float(now)))
    except Exception:  # noqa: BLE001 - provenance only
        logger.debug("Learning couldn't keep a trial's attempt", exc_info=True)


# ── The final set (the freeze) ──────────────────────────────────────────────


def run_final(store: Any, *, label: str, client: Any, tester_factory: Callable[..., Any], arm_tokens: int,
              meter: Any, should_stop: Callable[[], bool] = lambda: False, now: float | None = None,
              check_model: Callable[[], None] | None = None) -> TrialReport:
    """The final set (§7): every generation (each trial's winner) and the frozen control, at equal budget, on the
    fresh problems no trial used; judged the same way, recorded as a final trial, and nothing switches."""

    from row_bot.learning import evaluation

    moment = float(now if now is not None else store.now())
    trial_id = new_id("final")
    drawn = evaluation.draw_trial(trial_id, asked_by=ASKED_BY, store=store)
    if not drawn:
        return TrialReport(label, "fewer_fresh")
    current = archive.current(store)
    control = archive.frozen_control(store)
    generations = []
    for item in archive.trials(store):
        if not item.get("final") and item.get("winner") and item["winner"] not in generations:
            generations.append(str(item["winner"]))
    if current.id not in generations:
        generations.append(current.id)
    arms = [{"role": "incumbent" if release == current.id else f"generation{number}", "release": release}
            for number, release in enumerate(generations, 1)]
    if control.id not in generations:
        arms.append({"role": "control", "release": control.id})
    state = {"id": trial_id, "at": round(moment, 3), "problems": drawn, "arm_tokens": int(arm_tokens), "arms": arms,
             "incumbent": current.id, "control": control.id}
    runs = []
    for arm in arms:
        entry = archive.get(store, arm["release"])
        if entry is None:
            continue
        run = run_arm(store, entry, role=arm["role"], trial_id=trial_id, budget=int(arm_tokens), parent_meter=meter,
                      client=client, tester_factory=tester_factory, should_stop=should_stop, now=moment,
                      check_model=check_model)
        if run.stopped:
            return TrialReport(label, "stopped", trial_id, drawn)
        runs.append(run)
    tester = tester_factory(current.strategy)
    judged = judge(store, trial_id, runs, tester=tester, meter=meter, client=client, should_stop=should_stop)
    if judged.get("stopped") == "stopped":
        return TrialReport(label, "stopped", trial_id, drawn)
    return _finish(store, state, runs, judged, now=moment, night=label, final=True)


def undo(store: Any, *, at: float | None = None) -> archive.Entry | None:
    """Undo the last automatic switch (How Row-Bot learns, the morning summary): the method before it is back."""

    entry = archive.undo(store, at=at)
    if entry is not None:
        from row_bot.learning import changed

        changed()
    return entry


__all__ = [
    "ARM_GPU_SECONDS",
    "ARM_TOKENS_MAX",
    "ARM_TOKENS_MIN",
    "ArmMeter",
    "ArmRun",
    "CHALLENGERS",
    "MAX_ACCEPTED",
    "PURPOSE",
    "Plan",
    "REPEATS",
    "TRIAL_SHARE",
    "TrialReport",
    "improvement_yield",
    "judge",
    "last_night",
    "mutate",
    "plan",
    "propose",
    "run_arm",
    "run_final",
    "run_trial",
    "trial_room",
    "undo",
]
