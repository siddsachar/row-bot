"""Testing and using changes (L3b): every drafted change is tested before it's offered, and goes into use
only with Undo (E4, E11, E12, D24; designs/learning-ux.md §4, §7).

L3a drafts **lessons**, **ways of working** and **skill** creates or patches as evolution proposals with a
``learning`` block (``state: waiting_to_test``). This module:

1. **Tests a draft** against the same assistant without it, at equal budget (E12), on

   - the person's own **hidden work** of its kind of work, held-out corrections first (H2's hidden corrected
     problems, ``evaluation.audit_check(focus=...)``), plus a few of other kinds: replayed at their captured
     prefix with the change given as guidance, 3 runs per arm (O84, O88), aggregates only (E10);
   - the packs' **visible** tasks and their **hidden** tasks of its kind, and the **harder split** (O31),
     through the **real runtime** (the evaluation worker, O56): a lesson as the memory recall finds, a way of
     working as the turn's overlay, with the lessons and ways of working already in use on both sides. The
     main model's arm without the change is kept for 30 days (O31), so a test pays mostly for the change.

   A change is **better** by the pre-registered rule (§6.1): its mean paired difference is above 0 on its
   kind of work and not below -10 points on the rest. Anything else is not used. Calls are ``learning_test``
   on the default model (E13), stopped at the monthly limit with Learning's own words; small tests say so.

2. **Offers it**: a better change is *Ready* (Overview's Needs you, Changes › Ready) with its test table.
   **Use this change** puts it into use: a lesson becomes a ``self_knowledge`` memory (E5), a way of working a
   new overlay version (``behaviour``, E7), a skill edit is applied with its backup. **Undo** takes it back
   out at once. **Not now** keeps it Ready without asking; **Don't suggest this again** turns it down
   (rejection memory, so it isn't proposed again without new evidence); **Edit** marks it "Edited: test
   again before using" and **Test again** tests the new words.

3. **Automatic mode** (D24: "Use changes that pass their tests"): a better lesson, way of working or skill
   edit goes into use by itself and is listed under Overnight with Undo; one resting only on tool or web
   content never does (E9). Personal-model versions always ask (``use``), and approvals, tools, permissions,
   spending and who can reach Row-Bot are outside every change (E4).

**E9 at use time**: a change's words may not add recipients, links, credentials or permissions
(``evolution.injection_problems``), checked again when it is used and after every Edit.

What the person's changes are lives with evolution's proposals (``controlled_evolution.json``) and the
ways-of-working file, both outside ``learning.db``: changes in use stay in use while Learning is off or
paused and after "Delete learning data" (UX §8). ``learning.db`` keeps the provenance: each lesson's
:class:`~row_bot.learning.contracts.LessonRecord`, each way of working's ``behaviour`` Release, each test's
TestRuns and the Attempt's outcome.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from row_bot import learning as gate
from row_bot.learning import kinds

logger = logging.getLogger(__name__)

ASKED_BY = "change test"
PROGRESS_FILE = "change_test.json"
NIGHT_KEY = "changes.night"
LESSONS_FILE = "learning_lessons.json"
#: How many drafts a night tests (the rest wait for the next night; the monthly limit stops it sooner).
MAX_TESTS_PER_NIGHT = 2
#: Non-inferiority margin on the rest (pre-registration §6.1): not below -10 points.
MARGIN = 0.10

# A Learning change's own state (``payload.learning.state``), beside evolution's status.
WAITING = "waiting_to_test"
TESTING = "testing"
TESTED = "tested"  # better: Ready (status ready), or in use (status applied)
NOT_BETTER = "not_better"
EDITED = "edited"
TURNED_DOWN = "turned_down"
UNDONE = "undone"

_TYPES = {"lesson": "lesson", "behaviour": "way_of_working", "create_skill": "skill", "patch_skill": "skill"}


class ChangeError(ValueError):
    """A refused action; ``code`` is stable (``not_found``, ``not_ready``, ``edited``, ``not_in_use``,
    ``e9``, ``never_automatic``, ``running``, ``claude_plan``, ``empty``, ``too_long``, ``unknown_slot``,
    ``slot_full``, ``apply_failed``)."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class TestPlan:
    """How much one change's test runs (the product's defaults; the paper's own runs set more).

    Own work: up to ``own`` hidden problems of its kind (held-out corrections first) and ``other`` of other
    kinds, ``repeats`` runs per arm. Packs: up to ``visible_own`` visible tasks of its kind and
    ``visible_other`` of each other pack, ``hidden_own`` of its pack's hidden tasks (``repeats`` runs) and
    ``harder_own`` of the harder split (O31), through the real runtime unless ``runtime`` is off. Practice (L4b, in
    a build with practice on): up to ``practice`` of the practice tasks of its kind, newest first."""

    own: int = 8
    other: int = 4
    repeats: int = 3
    visible_own: int = 4
    visible_other: int = 1
    hidden_own: int = 4
    harder_own: int = 4
    visible_repeats: int = 1
    task_tokens: int = 150_000
    runtime: bool = True
    practice: int = 0

    @classmethod
    def of(cls, method: Any = None, **overrides: Any) -> TestPlan:
        """The test plan a learning method's budget split asks for (``improver.strategy``); the repeats and each
        task's budget are fixed points and stay as they are."""

        from row_bot.learning.improver import strategy

        method = method or strategy.FIRST
        budget = method.budget
        practice = method.practice.tested if strategy.practising(method) else 0
        return cls(own=budget.own_problems, other=budget.other_problems, visible_own=budget.visible_own,
                   visible_other=budget.visible_other, hidden_own=budget.hidden_own, harder_own=budget.harder_own,
                   **{"practice": practice, **overrides})


@dataclass
class Result:
    """One change's test, in counts (what the review sheet shows and the paper's tables count)."""

    verdict: str  # better · no_difference · worse · worse_elsewhere · too_few · stopped · unavailable
    rows: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""
    tokens: int = 0
    local_tokens: int = 0
    own: dict[str, Any] = field(default_factory=dict)
    rest: dict[str, Any] = field(default_factory=dict)
    runs: list[str] = field(default_factory=list)
    small: bool = False
    stopped: str = ""
    tested_at: float = 0.0
    problems: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "rows": self.rows, "note": self.note, "tokens": int(self.tokens),
                "local_tokens": int(self.local_tokens), "own": self.own, "rest": self.rest,
                "runs": list(self.runs)[:64], "small": bool(self.small), "stopped": self.stopped,
                "tested_at": round(float(self.tested_at), 3), "problems": int(self.problems)}


# ── Reading changes ─────────────────────────────────────────────────────────


def _evolution() -> Any:
    from row_bot import evolution

    return evolution


def learning_changes() -> list[dict[str, Any]]:
    """Every change Learning drafted (evolution proposals with a ``learning`` block), newest first."""

    found = [item for item in _evolution().list_proposals() if _evolution().learning_draft(item)]
    return sorted(found, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def get(change_id: str) -> dict[str, Any]:
    proposal = _evolution().get_proposal(str(change_id or ""))
    if proposal is None or not _evolution().learning_draft(proposal):
        raise ChangeError("not_found")
    return proposal


def block(proposal: Mapping[str, Any]) -> dict[str, Any]:
    found = (proposal.get("payload") or {}).get("learning")
    return dict(found) if isinstance(found, dict) else {}


def change_kind(proposal: Mapping[str, Any]) -> str:
    """``lesson`` · ``way_of_working`` · ``skill``."""

    return _TYPES.get(str(proposal.get("proposal_type") or ""), "lesson")


def view_state(proposal: Mapping[str, Any]) -> str:
    """Where a change stands for the person: ``waiting_to_test`` · ``testing`` · ``ready`` · ``edited`` ·
    ``in_use`` · ``not_used``."""

    status = str(proposal.get("status") or "")
    state = str(block(proposal).get("state") or WAITING)
    if status in ("applied", "verified"):
        return "in_use"
    if status in ("rejected", "failed"):
        return "not_used"
    if state == TESTING:
        return "testing"
    if state == EDITED:
        return "edited"
    if state == TESTED and status == "ready":
        return "ready"
    return "waiting_to_test"


def words_of(proposal: Mapping[str, Any]) -> str:
    """Everything of a change that would reach the model's context (E9 checks it)."""

    payload = proposal.get("payload") or {}
    parts = [str(proposal.get("title") or "")]
    for key in ("text", "name", "description", "instructions", "target_skill", "updated_instructions"):
        if payload.get(key):
            parts.append(str(payload[key]))
    return "\n".join(part for part in parts if part)


def guidance_of(proposal: Mapping[str, Any]) -> str:
    """The change as the words a model is given (replay's guidance, the in-process loop)."""

    payload = proposal.get("payload") or {}
    kind = change_kind(proposal)
    if kind in ("lesson", "way_of_working"):
        return " ".join(str(payload.get("text") or "").split())
    name = str(payload.get("name") or payload.get("target_skill") or "")
    description = str(payload.get("description") or "")
    steps = str(payload.get("instructions") or payload.get("updated_instructions") or "")
    return f"Skill {name}: {description}\n{steps}".strip()


def e9_problems(proposal: Mapping[str, Any]) -> list[str]:
    """E9 at use time: what the change's words would add (recipient, url, credential, permission)."""

    return _evolution().injection_problems(words_of(proposal))


# ── What is in use now ──────────────────────────────────────────────────────


def lessons_in_use() -> list[dict[str, Any]]:
    """Lessons Learning put into use (newest first): their change, memory and words."""

    found = []
    for proposal in learning_changes():
        if proposal.get("proposal_type") == "lesson" and view_state(proposal) == "in_use":
            meta = block(proposal)
            found.append({"change_id": proposal["id"], "memory_id": str(meta.get("memory_id") or ""),
                          "title": str(proposal.get("title") or ""), "text": str((proposal.get("payload") or {}).get("text") or "")})
    return found


def context_in_use(*, leave_out: str = "") -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """The lessons ((title, text)) and ways of working ((slot, text)) in use now: what both arms of a test
    carry in the real runtime, so they differ only by the change (``leave_out``: the change itself)."""

    from row_bot.learning import behaviour

    lessons = tuple((item["title"], item["text"]) for item in lessons_in_use() if item["change_id"] != leave_out)
    ways = tuple((entry.slot, entry.text) for entry in behaviour.read().entries if entry.change_id != leave_out)
    return lessons, ways


def _index_path(root: Path | None = None) -> Path:
    return (root if root is not None else gate.data_dir()) / LESSONS_FILE


def _write_index(root: Path | None = None) -> None:
    """``learning_lessons.json``: the memories that are lessons in use (memory id → change), and how often
    recall used each this month. Outside ``learning.db``: a lesson in use stays one when Learning is off."""

    from row_bot.learning import atomic

    path = _index_path(root)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    lessons = {item["memory_id"]: item["change_id"] for item in lessons_in_use() if item["memory_id"]}
    used = current.get("used") if isinstance(current.get("used"), dict) else {}
    body = {"format": "row_bot.learning.lessons", "lessons": lessons, "month": current.get("month") or "",
            "used": {key: int(value) for key, value in used.items() if key in lessons}}
    if not lessons and not path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(body, sort_keys=True))


def lesson_memories(root: Path | None = None) -> dict[str, str]:
    """Memory id → change id of the lessons in use (read from the index; empty when there is none)."""

    try:
        found = json.loads(_index_path(root).read_text(encoding="utf-8")).get("lessons")
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(key): str(value) for key, value in found.items()} if isinstance(found, dict) else {}


def note_recalled(memory_ids: Sequence[str], *, root: Path | None = None,
                  clock: Callable[[], float] = time.time) -> int:
    """A turn's recall showed these memories: count the lessons among them for "used N times this month".
    Returns how many were lessons."""

    from datetime import datetime

    from row_bot.learning import atomic

    path = _index_path(root)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    lessons = current.get("lessons") if isinstance(current.get("lessons"), dict) else {}
    hits = [str(item) for item in memory_ids if str(item) in lessons]
    if not hits:
        return 0
    month = datetime.fromtimestamp(float(clock())).strftime("%Y-%m")
    used = current.get("used") if isinstance(current.get("used"), dict) and current.get("month") == month else {}
    for item in hits:
        used[item] = int(used.get(item) or 0) + 1
    current.update({"month": month, "used": used})
    atomic.write_text(path, json.dumps(current, sort_keys=True))
    return len(hits)


def used_this_month(memory_id: str, *, root: Path | None = None, clock: Callable[[], float] = time.time) -> int:
    from datetime import datetime

    try:
        current = json.loads(_index_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if current.get("month") != datetime.fromtimestamp(float(clock())).strftime("%Y-%m"):
        return 0
    return int((current.get("used") or {}).get(str(memory_id)) or 0)


# ── Testing ─────────────────────────────────────────────────────────────────


def _family_key(label: str) -> str:
    return kinds.from_label(label)


def _rest(by_kind: Mapping[str, Mapping[str, Any]], kind: str) -> dict[str, Any]:
    """Every kind of work but the change's own, together: problems, each arm's mean and the mean paired
    difference (weighted by problems)."""

    parts = [value for key, value in by_kind.items() if key != kind and value.get("problems")]
    problems = sum(int(item["problems"]) for item in parts)
    if not problems:
        return {"problems": 0, "difference": None, "baseline": None, "arm": None}

    def mean(name: str) -> float:
        return sum(float(item.get(name) or 0.0) * int(item["problems"]) for item in parts) / problems

    return {"problems": problems, "difference": round(mean("difference"), 6), "baseline": round(mean("baseline"), 6),
            "arm": round(mean("arm"), 6)}


def rest_of(by_kind: Mapping[str, Mapping[str, Any]], kind: str) -> dict[str, Any]:
    """Every kind of work but ``kind``, together (the "rest" the keeping rule reads)."""

    return _rest(by_kind, kind)


def verdict(own: Mapping[str, Any], rest: Mapping[str, Any]) -> str:
    """The pre-registered rule (§6.1): better when the mean paired difference is above 0 on the change's kind
    of work and not below -10 points on the rest; ``too_few`` when nothing of its kind was counted."""

    if not own or not own.get("problems") or own.get("difference") is None:
        return "too_few"
    difference = float(own["difference"])
    rest_difference = rest.get("difference") if rest else None
    elsewhere = rest_difference is not None and float(rest_difference) < -MARGIN
    if difference > 0:
        return "worse_elsewhere" if elsewhere else "better"
    if difference < 0:
        return "worse"
    return "worse_elsewhere" if elsewhere else "no_difference"


def _row(label: str, part: Mapping[str, Any]) -> dict[str, Any]:
    problems = int(part.get("problems") or 0)
    return {"label": label, "total": problems,
            "without": int(round(float(part.get("baseline") or 0.0) * problems + 1e-9)),
            "with": int(round(float(part.get("arm") or 0.0) * problems + 1e-9))}


class _Stop(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class Tester:
    """Tests drafted changes (see the module's docstring). Every collaborator can be replaced in tests:
    ``client`` (the model, for replay and the in-process loop), ``runner_factory`` (the real runtime),
    ``meter``, ``main_ref``, ``container`` and the ``plan``."""

    def __init__(self, root: Path | None = None, *, store: Any = None, client: Any = None,
                 runner_factory: Callable[..., Any] | None = None, meter: Callable[[], Any] | None = None,
                 main_ref: Callable[[], str] | None = None, container: Callable[[], bool] | None = None,
                 plan: TestPlan = TestPlan(), clock: Callable[[], float] = time.time,
                 check_model: Callable[[], None] | None = None, purpose: str = "learning_test") -> None:
        self._root = root
        self._store = store
        self.client = client
        self.runner_factory = runner_factory
        self._meter = meter
        self._main_ref = main_ref
        self._container = container
        self.plan = plan
        self.clock = clock
        self.check_model = check_model
        # ``learning_meta`` for a strategy trial's own tests (they count toward the trials' share, E13).
        self.purpose = purpose

    @property
    def store(self) -> Any:
        if self._store is None:
            from row_bot.learning.store import LearningStore

            self._store = LearningStore(self._root)
        return self._store

    def _main(self) -> str:
        if self._main_ref is not None:
            return self._main_ref()
        from row_bot.learning import routing

        return routing.main_model_ref()

    def _has_container(self) -> bool:
        probe = self._container
        if probe is None:
            from row_bot.learning.sandbox import container_runtime as probe
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001
            return False

    def _progress(self, **values: Any) -> None:
        path = gate.folder_path(self._root) / PROGRESS_FILE
        try:
            current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            current = {}
        current.update(values)
        current["updated_at"] = round(float(self.clock()), 3)
        try:
            from row_bot.learning import atomic

            path.parent.mkdir(parents=True, exist_ok=True)
            atomic.write_text(path, json.dumps(current, sort_keys=True))
        except OSError:
            logger.debug("Learning couldn't save its test's progress", exc_info=True)

    # ── One change ─────────────────────────────────────────────────────

    def arms(self, proposal: Mapping[str, Any], main_ref: str) -> list[Any]:
        """Without and with the change, at equal budget; both carry what is in use now (real runtime)."""

        from row_bot.learning import evaluation

        lessons, ways = context_in_use(leave_out=str(proposal.get("id") or ""))
        kind = change_kind(proposal)
        payload = proposal.get("payload") or {}
        words = guidance_of(proposal)
        with_lessons, with_ways = lessons, ways
        if kind == "lesson":
            with_lessons = (*lessons, (str(proposal.get("title") or "Lesson"), words))
        elif kind == "way_of_working":
            with_ways = (*ways, (str(payload.get("slot") or "guidance"), words))
        else:  # a skill is given as its instructions (the runtime's turn overlay, in the guidance slot)
            with_ways = (*ways, ("guidance", words))
        ref = main_ref or None
        meta = block(proposal)
        return [
            evaluation.Arm("without", model_ref=ref, cache=True, lessons=lessons, ways=ways),
            evaluation.Arm("with", model_ref=ref, guidance=(words,), lessons=with_lessons, ways=with_ways,
                           subject_kind="attempt", subject_id=str(meta.get("attempt_id") or "") or None),
        ]

    def _visible(self, kind: str, container: bool, practice: Sequence[Any] | None = None) -> list[Any]:
        from row_bot.learning.packs import PACKS, visible_tasks

        chosen = []
        for pack in PACKS:
            if pack == "small_coding" and not container:
                continue
            take = self.plan.visible_own if pack == kind else self.plan.visible_other
            chosen.extend(visible_tasks([pack])[: max(0, take)])
        if kind in PACKS and self.plan.harder_own:
            from row_bot.learning.packs import harder

            chosen.extend([task for task in harder.tasks([kind])
                           if container or task.verifier.kind != "code_tests"][: self.plan.harder_own])
        if self.plan.practice > 0 and kind in PACKS:
            from row_bot.learning import practice as practice_module

            made = list(practice) if practice is not None else practice_module.pool(self.store, kind)
            chosen.extend([task for task in made if container or task.verifier.kind != "code_tests"][
                : self.plan.practice])
        return chosen

    def test(self, change_id: str, *, should_stop: Callable[[], bool] = lambda: False) -> Result:
        """Test one drafted change now and keep the result on it (and its Attempt). Raises
        :class:`ChangeError` when it can't be tested (``claude_plan``, ``not_found``)."""

        proposal = get(change_id)
        meta = block(proposal)
        if view_state(proposal) not in ("waiting_to_test", "edited"):
            raise ChangeError("not_ready")
        if self.check_model is not None:
            try:
                self.check_model()
            except Exception as error:  # noqa: BLE001 - the Claude plan (O99) or no model: it waits
                raise ChangeError("claude_plan" if type(error).__name__ == "ClaudePlanRefused" else "no_model") from None
        started = float(self.clock())
        _evolution().update_learning(change_id, {"state": TESTING})
        try:
            result = self.measure(proposal, should_stop=should_stop, progress=True)
        except Exception:
            _evolution().update_learning(change_id, {"state": meta.get("state") or WAITING})
            self._progress(state="failed")
            raise
        if result.verdict == "stopped" and result.stopped == "stopped":
            _evolution().update_learning(change_id, {"state": WAITING if meta.get("state") != EDITED else EDITED})
            return result
        self._record(change_id, proposal, result, started)
        return result

    def measure(self, proposal: Mapping[str, Any], *, should_stop: Callable[[], bool] = lambda: False,
                progress: bool = False, meter: Any = None, leave_out: str | None = None,
                practice: Sequence[Any] | None = None) -> Result:
        """One change against the same assistant without it, at equal budget, by the plan; nothing about the
        change is kept (``test`` keeps it). A strategy trial's arm measures its own drafts this way with its own
        ``meter``, its own ``practice`` tasks, and leaves its fresh problems out (``leave_out``: the trial whose
        problems judge it)."""

        from row_bot.learning import evaluation

        meta = block(proposal)
        kind = kinds.from_label(str(meta.get("kind_of_work") or "")) if meta.get("kind_of_work") else kinds.OTHER
        main_ref = self._main()
        arms = self.arms(proposal, main_ref)
        container = self._has_container()
        visible = self._visible(kind, container, practice)
        meter = meter if meter is not None else (self._meter or evaluation.TokenMeter.for_month)()
        budget = evaluation.Budget(task_tokens=self.plan.task_tokens)
        # A trial's arms each pay for their whole test: a result one arm kept would make the next arm's cheaper
        # (equal budget, pre-registration §4).
        cache = evaluation.ArmCache(self._root, clock=self.clock) if leave_out is None else None
        focus = evaluation.Focus(kind, own=self.plan.own, other=self.plan.other)
        packs = [kind] if kind in kinds.FAMILIES and (container or kind != "small_coding") else []
        verifiers = None if container else ["answer", "json_answer", "file", "app_state"]
        focus = _with_pack_limit(focus, self.plan.hidden_own)
        hidden_total = 0
        if self.store.exists():
            try:
                hidden_total = evaluation.audit_size(asked_by=ASKED_BY, store=self.store, packs=packs, verifiers=verifiers,
                                                     focus=focus, leave_out=leave_out)
            except Exception:  # noqa: BLE001 - no vault yet: nothing hidden to run
                hidden_total = 0
        total = (len(visible) * self.plan.visible_repeats + hidden_total * self.plan.repeats) * len(arms)
        if progress:
            self._progress(change_id=str(proposal.get("id") or ""), title=str(proposal.get("title") or ""),
                           state="running", done=0, total=total, started_at=float(self.clock()))
        done = [0]

        def counted(_arm: str, _case: Any) -> None:
            done[0] += 1
            if progress:
                self._progress(done=done[0])
            if getattr(_case, "local_tokens", 0) and hasattr(meter, "charge_gpu"):
                meter.charge_gpu(float(getattr(_case, "seconds", 0.0) or 0.0))  # a trial arm's card time
            if should_stop():
                raise _Stop("stopped")

        client = self.client
        if self.purpose != evaluation.PURPOSE:
            from row_bot.learning.calls import Purposed

            client = Purposed(client or evaluation.LedgerModelClient(), self.purpose)
        runner, close = (None, None)
        if self.plan.runtime and visible:
            runner, close = self._runner(arms, main_ref)
        try:
            shown = evaluation.compare(arms, tasks=visible, runner=runner, cache=cache, meter=meter, store=self.store,
                                       client=client, clock=self.clock, on_case=counted, budget=budget,
                                       repeats=self.plan.visible_repeats, kind_key=_family_key,
                                       save=leave_out is None) if visible else None
            hidden = None
            if hidden_total and not (shown is not None and shown.stopped):
                def hidden_progress(_done: int, _total: int) -> None:
                    counted("", None)

                hidden = evaluation.audit_check(
                    arms, asked_by=ASKED_BY, store=self.store, client=client, runner=runner, cache=cache,
                    meter=meter, clock=self.clock, packs=packs, verifiers=verifiers, budget=budget,
                    on_progress=hidden_progress, repeats=self.plan.repeats, combine_with=shown, kind_key=_family_key,
                    focus=focus, leave_out=leave_out, save=leave_out is None)
        except _Stop:
            if progress:
                self._progress(state="stopped")
            return Result("stopped", stopped="stopped", tested_at=float(self.clock()))
        finally:
            if close is not None:
                try:
                    close()
                except Exception:  # noqa: BLE001
                    logger.debug("Learning couldn't stop its evaluation worker", exc_info=True)
        result = self._result(kind, shown, hidden)
        if progress:
            self._progress(state="done", done=done[0])
        return result

    def _runner(self, arms: Sequence[Any], main_ref: str) -> tuple[Any, Callable[[], None] | None]:
        """The real runtime for the packs (O56): one worker, both arms with what they carry. Without a
        worker (no sign-in it can be handed, or it couldn't start) the packs run in-process instead."""

        if self.runner_factory is not None:
            return self.runner_factory(arms, main_ref)
        from row_bot.learning import eval_worker

        local = _local(main_ref)
        worker_arms = {arm.name: eval_worker.WorkerArm(arm.name, main_ref, local) for arm in arms}
        try:
            credentials, providers, unsupported = eval_worker.gather(list(worker_arms.values()))
        except Exception:  # noqa: BLE001
            logger.info("Learning's test couldn't hand the main model to its worker; packs run in-process")
            return None, None
        if unsupported:
            return None, None
        spec = {"arms": [{"name": arm.name, "model_ref": main_ref, "lessons": [list(item) for item in arm.lessons],
                          "ways": [list(item) for item in arm.ways]} for arm in arms],
                "tools": eval_worker.safe_tools(eval_worker.person_tools()), "providers": providers,
                "code_backend": "container"}
        worker = eval_worker.EvalWorker(spec, credentials=credentials, person_data=gate.data_dir())
        try:
            worker.start()
        except Exception as error:  # noqa: BLE001 - the packs run in-process instead
            logger.info("Learning's evaluation worker didn't start (%s); packs run in-process", getattr(error, "code", ""))
            worker.stop()
            return None, None
        runner = eval_worker.RuntimeRunner(worker, worker_arms, record_usage=eval_worker.ledger_recorder(self.purpose))
        return runner, worker.stop

    def _result(self, kind: str, shown: Any, hidden: Any) -> Result:
        from row_bot.learning import evaluation

        analysis: Mapping[str, Any] = {}
        if hidden is not None:
            analysis = (hidden.analysis.get("combined") or {}).get("with") or (hidden.analysis.get("arms") or {}).get("with") or {}
        if not analysis and shown is not None:
            analysis = (shown.analysis.get("arms") or {}).get("with") or {}
        by_kind = dict(analysis.get("by_kind") or {})
        own = dict(by_kind.get(kind) or {})
        rest = _rest(by_kind, kind)
        found = verdict(own, rest)
        parts = [item for item in (shown, hidden) if item is not None]
        stopped = next((item.stopped for item in parts if item.stopped), "")
        if stopped and found != "better":
            found = "stopped"
        tokens = sum(item.tokens for item in parts)
        local = sum(item.local_tokens for item in parts)
        rows = []
        if own.get("problems"):
            rows.append(_row(f"{kinds.label(kind)} it hadn’t seen", own))
        if rest.get("problems"):
            rows.append(_row("Other tasks", rest))
        small = any(item.small_sample for item in parts) or int(own.get("problems") or 0) < evaluation.SMALL_SAMPLE
        used = f"Testing used {evaluation._short_tokens(tokens)} tokens"  # noqa: SLF001 - the shared wording
        if local:
            used += f" (and {evaluation._short_tokens(local)} on this computer)"  # noqa: SLF001
        note = used + (" · " + evaluation.VARIANCE_NOTE.rstrip(".") if small else "") + "."
        if stopped:
            note = f"{note} {stopped}"
        keep = ("problems", "difference", "low", "high", "p_value", "baseline", "arm", "interval_verdict")
        runs = [arm.run.id for item in parts for arm in item.arms]
        return Result(found, rows, note, tokens, local, {key: own.get(key) for key in keep if key in own}, rest, runs,
                      small, stopped, float(self.clock()), int(own.get("problems") or 0) + int(rest.get("problems") or 0))

    def _record(self, change_id: str, proposal: Mapping[str, Any], result: Result, started: float) -> None:
        meta = block(proposal)
        values: dict[str, Any] = {"test": result.to_dict(), "tested_at": result.tested_at}
        if result.verdict == "better":
            values["state"] = TESTED
            _evolution().update_learning(change_id, values, status="ready")
        elif result.verdict in ("too_few", "stopped", "unavailable"):
            # Nothing to tell yet: it waits for more of the person's work (or the next night).
            values["state"] = EDITED if meta.get("state") == EDITED else WAITING
            _evolution().update_learning(change_id, values)
        else:
            values["state"] = NOT_BETTER
            _evolution().update_learning(change_id, values)
            _evolution().reject_proposal(change_id, f"not better in testing ({result.verdict})")
        self._attempt(meta, result)
        if result.verdict == "better":
            self._maybe_automatic(change_id)

    def _attempt(self, meta: Mapping[str, Any], result: Result) -> None:
        from row_bot.learning.contracts import Attempt, Cost

        attempt_id = str(meta.get("attempt_id") or "")
        if not attempt_id or not self.store.exists():
            return
        found = self.store.get("attempt", attempt_id)
        if not isinstance(found, Attempt):
            return
        outcome = {"better": "improved", "no_difference": "unclear", "worse": "worse", "worse_elsewhere": "worse",
                   "too_few": "pending", "stopped": "pending", "unavailable": "pending"}.get(result.verdict, "unclear")
        runs = tuple(dict.fromkeys((*found.test_run_ids, *(run for run in result.runs if run))))[:1024]
        updated = replace(found, outcome=outcome, test_run_ids=runs,  # type: ignore[arg-type]
                          cost=Cost(tokens=found.cost.tokens + max(0, int(result.tokens - result.local_tokens))),
                          finished_at=None if outcome == "pending" else result.tested_at)
        try:
            self.store.put(updated)
        except Exception:  # noqa: BLE001 - the archive is provenance; the change keeps its result
            logger.debug("Learning couldn't update the attempt", exc_info=True)

    def _maybe_automatic(self, change_id: str) -> None:
        settings = gate.read_settings(self._root)
        if settings.apply_mode != "automatic":
            return
        try:
            use(change_id, by="automatic", store=self.store, clock=self.clock)
        except ChangeError as error:
            logger.info("A change that passed its tests waits for the person (%s)", error.code)


def _with_pack_limit(focus: Any, count: int) -> Any:
    return replace(focus, packs=max(0, int(count)))


def _local(ref: str) -> bool:
    from row_bot.learning.spending import is_local_provider
    from row_bot.providers.selection import parse_model_ref

    parsed = parse_model_ref(ref) if ref else None
    return bool(parsed) and is_local_provider(parsed[0])


# ── Using and undoing ───────────────────────────────────────────────────────


def _lesson_record(store: Any, proposal: Mapping[str, Any], memory_id: str, now: float, *, status: str) -> str | None:
    from row_bot.learning.contracts import LessonRecord, Ref, new_id

    if store is None or not store.exists():
        return None
    meta = block(proposal)
    lesson_id = str(meta.get("lesson_id") or "") or new_id("les")
    existing = store.get("lesson", lesson_id)
    created = float(getattr(existing, "created_at", now) or now)
    evidence = (Ref("attempt", str(meta["attempt_id"])),) if meta.get("attempt_id") else ()
    record = LessonRecord(
        id=lesson_id, title=str(proposal.get("title") or "Lesson")[:200],
        text=str((proposal.get("payload") or {}).get("text") or "")[:2000],
        origin="agent_saved" if meta.get("origin") == "agent_saved" else "learning", created_at=created,
        updated_at=max(now, created), status=status, kind_of_work=str(meta.get("kind_of_work") or "") or None,  # type: ignore[arg-type]
        memory_id=memory_id or None, proposal_id=str(proposal.get("id") or "") or None, evidence=evidence,
        test_run_ids=tuple(str(item) for item in ((meta.get("test") or {}).get("runs") or ()) if item)[:1024],
    )
    try:
        store.put(record)
    except Exception:  # noqa: BLE001 - provenance only
        logger.debug("Learning couldn't keep a lesson's record", exc_info=True)
        return None
    return lesson_id


def _behaviour_release(store: Any, proposal: Mapping[str, Any], version: Any, now: float) -> None:
    from row_bot.learning.contracts import Release, new_id

    if store is None or not store.exists() or version is None:
        return
    try:
        blob = store.put_blob(json.dumps([entry.to_dict() for entry in version.entries], sort_keys=True).encode("utf-8"))
        meta = block(proposal)
        store.put(Release(id=new_id("rel"), kind="behaviour", version=int(version.number), created_at=now,
                          label=f"Ways of working v{int(version.number)}", status="in_use",
                          attempt_id=str(meta.get("attempt_id") or "") or None, content_blob=blob))
    except Exception:  # noqa: BLE001 - provenance only
        logger.debug("Learning couldn't keep the ways of working's version", exc_info=True)


def _store_or_none(store: Any) -> Any:
    if store is not None:
        return store
    if not gate.store_exists():
        return None
    from row_bot.learning.store import LearningStore

    return LearningStore()


def use(change_id: str, *, by: str = "person", store: Any = None, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Put a tested change into use (Use this change, or automatic mode with ``by="automatic"``)."""

    proposal = get(change_id)
    meta = block(proposal)
    state = view_state(proposal)
    if state == "in_use":
        return proposal
    if state == "edited":
        raise ChangeError("edited")
    if state != "ready":
        raise ChangeError("not_ready")
    problems = e9_problems(proposal)
    if problems:
        raise ChangeError("e9", ",".join(problems))
    if by == "automatic" and meta.get("never_automatic"):
        raise ChangeError("never_automatic")
    now = float(clock())
    saved = _store_or_none(store)
    kind = change_kind(proposal)
    payload = proposal.get("payload") or {}
    values: dict[str, Any] = {"used_at": now, "used_by": "automatic" if by == "automatic" else "person"}
    if kind == "lesson":
        from row_bot import knowledge_graph

        memory_id = str(meta.get("memory_id") or "") if meta.get("origin") == "agent_saved" else ""
        if not memory_id or knowledge_graph.get_entity(memory_id) is None:
            entity = knowledge_graph.save_entity("self_knowledge", str(proposal.get("title") or "Lesson")[:200],
                                                 str(payload.get("text") or ""), source="learning",
                                                 properties={"learning_change": change_id})
            memory_id = str(entity.get("id") or "")
        values["memory_id"] = memory_id
        lesson_id = _lesson_record(saved, proposal, memory_id, now, status="in_use")
        if lesson_id:
            values["lesson_id"] = lesson_id
    elif kind == "way_of_working":
        from row_bot.learning import behaviour

        try:
            version = behaviour.use(change_id, str(payload.get("slot") or ""), str(payload.get("text") or ""),
                                    title=str(proposal.get("title") or ""), clock=clock)
        except behaviour.BehaviourError as error:
            raise ChangeError(error.code) from None
        values["overlay_version"] = version.number
        _behaviour_release(saved, proposal, version, now)
    else:
        applied = _evolution().apply_proposal(change_id, require_approval=False, approved_by_user=by != "automatic")
        if not applied.get("ok"):
            raise ChangeError("apply_failed", str(applied.get("message") or "")[:200])
        values["rollback_ref"] = str((applied.get("action_run") or {}).get("rollback_ref") or "")
    updated = _evolution().update_learning(change_id, values, status="applied")
    _evolution().record_proposal_outcome(change_id, "applied", feedback=values["used_by"])
    _attempt_kept(saved, meta, True)
    if kind == "lesson":
        _write_index()
    logger.info("A Learning change went into use (%s, %s)", kind, values["used_by"])
    return updated or proposal


def undo(change_id: str, *, store: Any = None, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Take a change in use back out at once (Undo, Turn off)."""

    proposal = get(change_id)
    if view_state(proposal) != "in_use":
        raise ChangeError("not_in_use")
    meta = block(proposal)
    now = float(clock())
    saved = _store_or_none(store)
    kind = change_kind(proposal)
    payload = proposal.get("payload") or {}
    if kind == "lesson":
        memory_id = str(meta.get("memory_id") or "")
        # A lesson Row-Bot saved for itself was a memory before it was tested: Undo keeps it, untested.
        if memory_id and meta.get("origin") != "agent_saved":
            from row_bot import knowledge_graph

            knowledge_graph.delete_entity(memory_id)
        _lesson_record(saved, proposal, "", now, status="off")
    elif kind == "way_of_working":
        from row_bot.learning import behaviour

        try:
            behaviour.undo(change_id, clock=clock)
        except behaviour.BehaviourError:
            pass  # already out of the overlay
    elif proposal.get("proposal_type") == "create_skill":
        from row_bot import skills

        try:
            skills.set_enabled(_evolution().normalize_skill_name(str(payload.get("name") or "")), False)
        except Exception:  # noqa: BLE001
            logger.info("Learning couldn't switch off the skill it created")
    else:
        _restore_skill(str(payload.get("target_skill") or ""), str(meta.get("rollback_ref") or ""))
    _evolution().update_learning(change_id, {"state": UNDONE, "undone_at": now})
    _evolution().reject_proposal(change_id, "undone")
    _attempt_kept(saved, meta, False)
    if kind == "lesson":
        _write_index()
    logger.info("A Learning change was undone (%s)", kind)
    return get(change_id)


def _restore_skill(name: str, backup: str) -> None:
    if not name or not backup:
        return
    from row_bot import skills

    try:
        text = Path(backup).read_text(encoding="utf-8")
        parsed = skills._parse_skill_text(text, Path(backup))  # noqa: SLF001 - the backup is a SKILL.md
        if parsed is not None:
            skills.update_skill(name=name, instructions=parsed.instructions)
    except Exception:  # noqa: BLE001
        logger.info("Learning couldn't restore a skill from its backup")


def _attempt_kept(store: Any, meta: Mapping[str, Any], kept: bool) -> None:
    from row_bot.learning.contracts import Attempt

    attempt_id = str(meta.get("attempt_id") or "")
    if store is None or not attempt_id or not store.exists():
        return
    found = store.get("attempt", attempt_id)
    if isinstance(found, Attempt) and found.outcome == "improved" and found.kept != kept:
        try:
            store.put(replace(found, kept=kept))
        except Exception:  # noqa: BLE001
            logger.debug("Learning couldn't mark the attempt kept", exc_info=True)


def not_now(change_id: str, *, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Keep a Ready change Ready, without asking about it in Needs you."""

    get(change_id)
    return _evolution().update_learning(change_id, {"snoozed_at": float(clock())}) or get(change_id)


def dont_suggest(change_id: str, *, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Turn a change down for good: it isn't proposed again without new evidence (rejection memory)."""

    proposal = get(change_id)
    if view_state(proposal) == "in_use":
        raise ChangeError("not_ready")
    _evolution().update_learning(change_id, {"state": TURNED_DOWN, "turned_down_at": float(clock())})
    if proposal.get("status") != "rejected":
        _evolution().reject_proposal(change_id, "don't suggest this again")
    return get(change_id)


def edit(change_id: str, text: str, *, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """New words for a change that isn't in use: E9 checks them again, and it must be tested again before
    it can be used ("Edited: test again before using")."""

    proposal = get(change_id)
    if view_state(proposal) in ("in_use", "testing"):
        raise ChangeError("not_ready")
    clean = " ".join(str(text or "").split()) if change_kind(proposal) != "skill" else str(text or "").strip()
    if not clean:
        raise ChangeError("empty")
    kind = change_kind(proposal)
    payload = proposal.get("payload") or {}
    if kind == "way_of_working":
        from row_bot.learning import behaviour

        try:
            clean = behaviour.check(str(payload.get("slot") or ""), clean)
        except behaviour.BehaviourError as error:
            raise ChangeError(error.code) from None
    elif len(clean) > 1200 and kind == "lesson":
        raise ChangeError("too_long")
    key = "text" if kind in ("lesson", "way_of_working") else (
        "instructions" if proposal.get("proposal_type") == "create_skill" else "updated_instructions")
    problems = _evolution().injection_problems(str(proposal.get("title") or "") + "\n" + clean)
    if problems:
        raise ChangeError("e9", ",".join(problems))
    _evolution().update_learning(change_id, {"state": EDITED, "edited_at": float(clock())}, status="draft",
                                 payload={key: clean})
    return get(change_id)


def test_again(change_id: str) -> dict[str, Any]:
    """An edited (or untested) change waits to be tested again; :class:`Testing` runs it."""

    proposal = get(change_id)
    if view_state(proposal) not in ("edited", "waiting_to_test"):
        raise ChangeError("not_ready")
    return _evolution().update_learning(change_id, {"state": WAITING, "queued_at": time.time()}) or proposal


def draft_saved_lesson(memory_id: str, *, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """Test it: a lesson Row-Bot saved for itself during a chat (a ``self_knowledge`` memory nothing tested)
    becomes a lesson change waiting to be tested. It rests on no feedback from the person, so it is never
    used automatically (E9); its words are checked like any change's."""

    from row_bot import knowledge_graph

    entity = knowledge_graph.get_entity(str(memory_id or ""))
    if entity is None or str(entity.get("entity_type") or "") != "self_knowledge":
        raise ChangeError("not_found")
    for proposal in learning_changes():
        meta = block(proposal)
        if meta.get("origin") == "agent_saved" and meta.get("memory_id") == entity["id"]:
            if view_state(proposal) in ("waiting_to_test", "testing"):
                return proposal
            if view_state(proposal) == "in_use":
                raise ChangeError("not_ready")
            _evolution().update_learning(str(proposal["id"]), {"state": WAITING, "queued_at": float(clock())},
                                         status="draft")
            return get(str(proposal["id"]))
    title = str(entity.get("subject") or "Lesson")[:200]
    text = str(entity.get("description") or "")[:1200] or title
    if _evolution().injection_problems(title + "\n" + text):
        raise ChangeError("e9")
    kind = kinds.classify(kinds.Request(f"{title}. {text}")).kind
    learning = {"origin": "agent_saved", "memory_id": entity["id"], "kind_of_work": kind, "state": WAITING,
                "never_automatic": True, "why": {}, "queued_at": float(clock()), "issue": f"saved:{entity['id']}"}
    return _evolution().create_proposal(insight_ids=[], proposal_type="lesson", title=title,
                                        rationale="Row-Bot saved this lesson for itself during a chat.",
                                        payload={"text": text, "kind_of_work": kind, "learning": learning},
                                        verification_plan="Learning tests it on work it hasn't seen (L3b).",
                                        status="draft", dedupe=True)


def delete_lesson(lesson_id: str) -> None:
    """Delete a lesson (Lessons › Delete): a lesson Learning put into use is taken out of use and forgotten;
    a lesson Row-Bot saved for itself is a memory, deleted (with any test of it)."""

    proposal = _evolution().get_proposal(str(lesson_id or ""))
    if proposal is not None and _evolution().learning_draft(proposal) and proposal.get("proposal_type") == "lesson":
        meta = block(proposal)
        if view_state(proposal) == "in_use":
            undo(str(proposal["id"]))
        _evolution().update_learning(str(proposal["id"]), {"state": "deleted"})
        if meta.get("origin") == "agent_saved" and meta.get("memory_id"):
            from row_bot import knowledge_graph

            knowledge_graph.delete_entity(str(meta["memory_id"]))
        return
    from row_bot import knowledge_graph

    entity = knowledge_graph.get_entity(str(lesson_id or ""))
    if entity is None or str(entity.get("entity_type") or "") != "self_knowledge":
        raise ChangeError("not_found")
    for item in learning_changes():
        meta = block(item)
        if meta.get("memory_id") == entity["id"]:
            if view_state(item) == "in_use" and meta.get("origin") != "agent_saved":
                undo(str(item["id"]))
            _evolution().update_learning(str(item["id"]), {"state": "deleted"})
            if item.get("status") != "rejected":
                _evolution().reject_proposal(str(item["id"]), "deleted")
    knowledge_graph.delete_entity(entity["id"])


def relink_lesson(old_memory_id: str, new_memory_id: str) -> int:
    """O7: a Dream merge keeps another memory in place of a lesson's: the lesson follows it. Returns how
    many lessons moved."""

    if not old_memory_id or not new_memory_id or old_memory_id == new_memory_id:
        return 0
    moved = 0
    for proposal in learning_changes():
        meta = block(proposal)
        if proposal.get("proposal_type") == "lesson" and str(meta.get("memory_id") or "") == old_memory_id:
            _evolution().update_learning(str(proposal["id"]), {"memory_id": new_memory_id})
            moved += 1
            saved = _store_or_none(None)
            lesson_id = str(meta.get("lesson_id") or "")
            if saved is not None and lesson_id:
                found = saved.get("lesson", lesson_id)
                if found is not None:
                    try:
                        saved.put(replace(found, memory_id=new_memory_id, updated_at=max(found.updated_at, saved.now())))
                    except Exception:  # noqa: BLE001
                        logger.debug("Learning couldn't move a lesson's record", exc_info=True)
    if moved:
        _write_index()
    return moved


# ── The night and Test again ────────────────────────────────────────────────


def due() -> list[dict[str, Any]]:
    """Drafts waiting to be tested, the person's own requests first (queued by Test again), then oldest."""

    waiting = [item for item in learning_changes() if view_state(item) == "waiting_to_test"]
    return sorted(waiting, key=lambda item: (not block(item).get("queued_at"), str(item.get("created_at") or "")))


@dataclass
class NightTests:
    """One night's tests, in counts (the Overview's Overnight lines read it)."""

    night: str
    state: str = "done"  # done · nothing · stopped · limit · claude_plan · failed
    tested: int = 0
    ready: int = 0
    not_better: int = 0
    unclear: int = 0
    used: int = 0  # put into use by automatic mode
    tokens: int = 0
    used_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"night": self.night, "state": self.state, "tested": self.tested, "ready": self.ready,
                "not_better": self.not_better, "unclear": self.unclear, "used": self.used, "tokens": self.tokens,
                "used_ids": list(self.used_ids)[:20]}


def last_tests(store: Any) -> dict[str, Any] | None:
    if store is None or not store.exists():
        return None
    try:
        found = json.loads(store.meta_value(NIGHT_KEY, "") or "null")
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def test_waiting(tester: Tester, *, night: str, should_stop: Callable[[], bool] = lambda: False,
                 limit: int = MAX_TESTS_PER_NIGHT) -> NightTests:
    """Test tonight's waiting drafts (after the look, before training), at most ``limit`` (the learning method's
    budget split)."""

    report = NightTests(night)
    earlier = last_tests(tester.store)
    if earlier and earlier.get("night") == night and earlier.get("state") == "stopped":
        for key in ("tested", "ready", "not_better", "unclear", "used", "tokens"):
            setattr(report, key, int(earlier.get(key) or 0))
        report.used_ids = list(earlier.get("used_ids") or [])
    waiting = due()
    if not waiting:
        report.state = "nothing" if not report.tested else "done"
    for proposal in waiting[: max(0, int(limit))]:
        if should_stop():
            report.state = "stopped"
            break
        try:
            result = tester.test(str(proposal["id"]), should_stop=should_stop)
        except ChangeError as error:
            report.state = "claude_plan" if error.code == "claude_plan" else "failed"
            break
        except Exception:  # noqa: BLE001 - one failure ends tonight's tests, said plainly
            logger.exception("Learning couldn't test a change")
            report.state = "failed"
            break
        report.tokens += max(0, int(result.tokens - result.local_tokens))
        if result.verdict == "stopped":
            report.state = "limit" if result.stopped and result.stopped != "stopped" else "stopped"
            break
        if result.verdict == "too_few":
            report.unclear += 1
            continue
        report.tested += 1
        if result.verdict == "better":
            report.ready += 1
            if view_state(get(str(proposal["id"]))) == "in_use":
                report.used += 1
                report.used_ids.append(str(proposal["id"]))
        else:
            report.not_better += 1
    try:
        tester.store.set_meta_values({NIGHT_KEY: json.dumps(report.to_dict(), sort_keys=True)})
    except Exception:  # noqa: BLE001
        logger.debug("Learning couldn't save tonight's tests", exc_info=True)
    return report


def progress(root: Path | None = None) -> dict[str, Any] | None:
    """The test running now ("Testing 'Pick documents by ID' on 24 tasks · 9 done"), else None."""

    path = gate.folder_path(root) / PROGRESS_FILE
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) and found.get("state") == "running" else None


class Testing:
    """Test again (the person's own request) runs one change's test now, on its own thread, under the same
    stop rule as the night (someone using Row-Bot stops it at the next task; it goes on later)."""

    def __init__(self, tester_factory: Callable[[], Tester] | None = None, *, lease: Any = None,
                 start_threads: bool = True) -> None:
        self._factory = tester_factory or (lambda: Tester(check_model=_check_default_model))
        self._lease = lease
        self._start_threads = start_threads
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error: str | None = None

    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _busy(self) -> bool:
        if self._stop.is_set():
            return True
        try:
            if self._lease is None:
                from row_bot.learning.lease import ResourceLease

                return bool(getattr(ResourceLease().read(), "busy", False))
            return bool(getattr(self._lease.read(), "busy", False))
        except Exception:  # noqa: BLE001
            return False

    def start(self, change_id: str) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            if not self._start_threads:
                self._run(change_id)
                return True
            self._thread = threading.Thread(target=self._run, args=(change_id,), name="learning-change-test",
                                            daemon=True)
            self._thread.start()
            return True

    def _run(self, change_id: str) -> None:
        try:
            self._factory().test(change_id, should_stop=self._busy)
            self.last_error = None
        except ChangeError as error:
            self.last_error = error.code
        except Exception as error:  # noqa: BLE001
            self.last_error = type(error).__name__
            logger.exception("Learning couldn't test a change")

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)


def _check_default_model() -> None:
    from row_bot.learning import diagnosis

    diagnosis.check_default_model()


_TESTING: Testing | None = None
_TESTING_LOCK = threading.Lock()


def testing() -> Testing:
    """The app's one Test again service."""

    global _TESTING
    with _TESTING_LOCK:
        if _TESTING is None:
            _TESTING = Testing()
        return _TESTING


__all__ = [
    "ChangeError",
    "MAX_TESTS_PER_NIGHT",
    "NightTests",
    "Result",
    "TestPlan",
    "Tester",
    "Testing",
    "block",
    "change_kind",
    "context_in_use",
    "dont_suggest",
    "due",
    "e9_problems",
    "edit",
    "get",
    "guidance_of",
    "last_tests",
    "learning_changes",
    "lesson_memories",
    "lessons_in_use",
    "not_now",
    "note_recalled",
    "progress",
    "relink_lesson",
    "rest_of",
    "test_again",
    "test_waiting",
    "testing",
    "undo",
    "use",
    "used_this_month",
    "verdict",
    "view_state",
    "words_of",
]
