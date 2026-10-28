"""Learning jobs the personal model may take over (L4c; D21, O153).

Two of Learning's own jobs can move from the main model to the person's personal model:

- **drafting** (the night's look: one diagnosis call per issue that drafts a change, ``learning_diagnosis``);
- **practice** (making practice tasks from mistakes, ``learning_teacher``; only in a build that practises, L4b).

**Qualifying.** A job moves only after the newest ready personal version passes that job's own test, run once a
night when due (a new version, or :data:`RETEST_DAYS` since the last test). The test is fixed before any run:

- its **items** are the job's own past work: the person's own pieces of feedback of the last 30 days, each as its
  own issue (``diagnosis.collect(single=True)``, which already leaves out held-out decisions and the turns after
  held-out corrections). Never the audit vault, a hidden problem or a held-out one. The newest :data:`MAX_ITEMS`, at
  least :data:`MIN_ITEMS`; fewer and there is no test;
- **both models do the same items at equal budget**: the same messages, the same token cap each;
- **each output is checked the way the job's real output is**: drafting, a *usable draft* (readable, a kind of
  change the method drafts, complete, E9-clean); practice, a *kept task* (its check passes its right answer and fails
  its wrong one, the vault says it's novel, it's new), :data:`PRACTICE_PER_ITEM` asked per item;
- **it matches the main model** when its rate is at least the main model's minus :data:`MARGIN` on the same items.

**Handing back.** The job goes back to the main model when a newer test fails, the newest ready version changes, or
the version isn't served (no longer ready, or a call fails: that call is made again on the main model). The main
model is always the fallback.

**Never its own judge** (D21): only these two jobs' calls are ever routed. Tests of changes, comparisons of versions,
strategy trials' judging and the checker stay with the main model and the executable checks.

**Budget**: the calls keep their ``learning_*`` tags in the usage ledger (a local model's tokens count as local) and
the time the personal model's calls take is added up per job and month (card time).

Only in a build started with the jobs switch (:func:`row_bot.learning.jobs_enabled`); without it every job is the
main model's and nothing here runs.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from row_bot import learning as gate

logger = logging.getLogger(__name__)

DRAFTING = "drafting"
PRACTICE = "practice"
JOBS: tuple[str, ...] = (DRAFTING, PRACTICE)
#: The test, fixed before any run: items, the margin, how often it is run again.
MIN_ITEMS = 20
MAX_ITEMS = 24
MARGIN = 0.05
PRACTICE_PER_ITEM = 2
RETEST_DAYS = 7
#: Each side's token cap for one test (equal budget).
TEST_TOKENS = 400_000
STATE_KEY = "jobs.state"
NIGHT_KEY = "jobs.night"


def enabled() -> bool:
    return gate.jobs_enabled()


def offered() -> tuple[str, ...]:
    """The jobs this build may hand over: drafting, and practice when it practises."""

    if not enabled():
        return ()
    return tuple(job for job in JOBS if job != PRACTICE or gate.practice_enabled())


@dataclass(frozen=True)
class Qualification:
    """One job's test: the same items for both models, each output checked the way the job's real output is."""

    job: str
    version: int
    model_ref: str
    at: float
    items: int
    personal_passed: int
    main_passed: int
    asked: int
    personal_tokens: int = 0
    main_tokens: int = 0
    personal_seconds: float = 0.0
    # qualified · not_matched · too_few · limit · unavailable
    verdict: str = "not_matched"

    @property
    def qualified(self) -> bool:
        return self.verdict == "qualified"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _meta(store: Any, key: str) -> dict[str, Any]:
    try:
        found = json.loads(store.meta_value(key, "") or "null")
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def state(store: Any) -> dict[str, Any]:
    """Each job: who does it (``personal`` or ``main``), the version and since when, the latest test and history."""

    return _meta(store, STATE_KEY) if store.exists() else {}


def _save(store: Any, found: Mapping[str, Any]) -> None:
    store.set_meta_values({STATE_KEY: json.dumps(dict(found), sort_keys=True)})
    try:
        from row_bot.application.live_updates import publish

        publish("learning")
    except Exception:  # noqa: BLE001 - the views refresh on their own
        logger.debug("Learning couldn't announce who does its jobs", exc_info=True)


def newest_ready(root: Any = None) -> tuple[int, str] | None:
    """The newest personal-model version that is ready where it runs, and its model reference."""

    from row_bot.learning import versions

    found = next((item for item in versions.all_versions(root) if item.ready), None)
    return (found.version, str(found.model_ref)) if found is not None else None


def _event(found: dict[str, Any], job: str, event: str, at: float, **extra: Any) -> None:
    history = [*found.get(job, {}).get("history", []), {"at": round(at, 3), "event": event, **extra}][-20:]
    found.setdefault(job, {})["history"] = history


def hand_back(store: Any, job: str, reason: str, *, now: float | None = None) -> None:
    """The job goes back to the main model (``reason``: not_matched · new_version · not_served · switched_off)."""

    found = state(store)
    held = found.get(job) or {}
    if held.get("by") != "personal":
        return
    moment = float(now if now is not None else store.now())
    held.update({"by": "main", "since": round(moment, 3), "reason": reason})
    found[job] = held
    _event(found, job, "handed_back", moment, version=held.get("version"), reason=reason)
    _save(store, found)
    logger.info("Learning's %s job went back to the main model (%s)", job, reason)


def model_for(store: Any, job: str) -> str | None:
    """The personal model's reference while it does ``job``, else None (the main model). A job held by a version
    that is no longer the newest ready one goes back first."""

    if job not in offered() or store is None or not store.exists():
        return None
    held = state(store).get(job) or {}
    if held.get("by") != "personal":
        return None
    ready = newest_ready(store.root)
    if ready is None or ready[1] != held.get("model_ref"):
        reason = "not_served" if ready is None or ready[0] == held.get("version") else "new_version"
        hand_back(store, job, reason)
        return None
    return str(held["model_ref"])


class JobClient:
    """A Learning client for one job: the personal model while it does the job, else the main model. A personal call
    that fails hands the job back and is made again on the main model. Card time of the personal calls is counted."""

    def __init__(self, store: Any, job: str, client: Any) -> None:
        self.store = store
        self.job = job
        self.client = client

    def is_local(self, model_ref: str | None) -> bool:
        ref = model_ref or model_for(self.store, self.job)
        return self.client.is_local(ref)

    def invoke(self, messages: Sequence[Any], *, tools: Any, model_ref: str | None, purpose: str = "") -> Any:
        personal = None if model_ref else model_for(self.store, self.job)
        if personal is None:
            return self.client.invoke(messages, tools=tools, model_ref=model_ref, purpose=purpose)
        started = time.monotonic()
        try:
            reply = self.client.invoke(messages, tools=tools, model_ref=personal, purpose=purpose)
        except Exception as error:  # noqa: BLE001 - the main model is the fallback
            logger.info("Learning's personal model couldn't do its %s job (%s)", self.job, type(error).__name__)
            hand_back(self.store, self.job, "not_served")
            return self.client.invoke(messages, tools=tools, model_ref=None, purpose=purpose)
        _count(self.store, self.job, int(reply.tokens), time.monotonic() - started)
        return reply


def client_for(store: Any, job: str, client: Any) -> Any:
    """``client`` as ``job`` uses it: unchanged without the jobs switch."""

    return JobClient(store, job, client) if job in offered() and store is not None else client


def month_key(moment: float) -> str:
    """The calendar month a moment is in ("2026-10"): what this month's use of a job counts against."""

    return datetime.fromtimestamp(float(moment)).strftime("%Y-%m")


def _count(store: Any, job: str, tokens: int, seconds: float) -> None:
    found = state(store)
    held = found.setdefault(job, {})
    month = month_key(store.now())
    usage = held.get("usage") if (held.get("usage") or {}).get("month") == month else {"month": month}
    usage["tokens"] = int(usage.get("tokens") or 0) + max(0, tokens)
    usage["seconds"] = round(float(usage.get("seconds") or 0.0) + max(0.0, seconds), 3)
    held["usage"] = usage
    store.set_meta_values({STATE_KEY: json.dumps(found, sort_keys=True)})


# ── The test ────────────────────────────────────────────────────────────────


class _Side:
    """One model's side of a test: its own equal token cap, what it spent and how long its calls took."""

    def __init__(self, client: Any, model_ref: str | None, parent: Any) -> None:
        from row_bot.learning.improver.meta import ArmMeter

        self.client = client
        self.model_ref = model_ref
        self.meter = ArmMeter(parent, TEST_TOKENS)
        self.seconds = 0.0

    def is_local(self, _model_ref: str | None) -> bool:
        return self.client.is_local(self.model_ref)

    def invoke(self, messages: Sequence[Any], *, tools: Any, model_ref: str | None, purpose: str = "") -> Any:
        started = time.monotonic()
        try:
            reply = self.client.invoke(messages, tools=tools, model_ref=self.model_ref, purpose=purpose)
        finally:
            self.seconds += time.monotonic() - started
        if getattr(reply, "local", False):
            self.meter.charge_gpu(time.monotonic() - started)
        return reply


def items(store: Any, *, now: float, job: str) -> list[Any]:
    """The job's own past work the test uses: the person's own feedback of the last 30 days, each its own issue
    (held-out work already left out), newest first; for practice, only kinds the task packs cover."""

    from row_bot.learning import diagnosis
    from row_bot.learning.packs import PACKS

    found, _left = diagnosis.collect(store, now=now, single=True)
    if job == PRACTICE:
        found = [issue for issue in found if issue.kind in PACKS]
    found.sort(key=lambda issue: (-issue.newest(), issue.key))
    return found[:MAX_ITEMS]


def _usable_draft(issue: Any, reply: Any, method: Any) -> bool:
    from row_bot.learning import diagnosis
    from row_bot.learning.calls import text_of

    found = diagnosis.parse(text_of(reply.message), method)
    return bool(found is not None and found.change in diagnosis.DRAFTS and diagnosis._complete(found)  # noqa: SLF001
                and not diagnosis.e9_problems(issue, found))


def _drafting(store: Any, work: Sequence[Any], side: _Side, *, now: float, method: Any) -> tuple[int, int, str]:
    """(usable drafts, items done, how it ended) for one side."""

    from row_bot.learning import corrections, diagnosis
    from row_bot.learning.calls import estimate_tokens

    book = corrections.Book(store)
    context = diagnosis._context()  # noqa: SLF001 - the same context the night's look gives
    passed = done = 0
    for issue in work:
        messages = diagnosis._messages(diagnosis.describe(store, book, issue, now=now, method=method),  # noqa: SLF001
                                       context, method)
        if not side.meter.allows(estimate_tokens(messages), local=side.is_local(None)):
            return passed, done, "limit"
        try:
            reply = side.invoke(messages, tools=None, model_ref=None, purpose=diagnosis.PURPOSE)
        except Exception as error:  # noqa: BLE001 - a call that fails is an item it didn't do
            logger.info("A drafting test call failed (%s)", type(error).__name__)
            done += 1
            continue
        side.meter.charge(reply.tokens, local=reply.local)
        done += 1
        passed += _usable_draft(issue, reply, method)
    return passed, done, "done"


def _practice(store: Any, work: Sequence[Any], side: _Side, *, now: float, method: Any) -> tuple[int, int, str]:
    """(kept tasks, tasks asked, how it ended) for one side."""

    from row_bot.learning import practice
    from row_bot.learning.practice import generate

    known = {generate.fingerprint(task) for task in practice.pool(store)}
    kept = asked = 0
    for issue in work:
        made = generate.make(store, issue, client=side, count=PRACTICE_PER_ITEM, now=now, meter=side.meter,
                             known=known, method=method, asked_by=f"job test ({PRACTICE})")
        if made.state == "limit":
            return kept, asked, "limit"
        if made.state in ("needs_container", "not_a_task_kind", "no_call"):
            continue
        asked += PRACTICE_PER_ITEM
        kept += len(made.kept)
        known |= {generate.fingerprint(task) for task in made.kept}  # a task made twice counts once
    return kept, asked, "done"


def qualify(store: Any, job: str, *, client: Any, version: int, model_ref: str, now: float | None = None,
            method: Any = None, meter: Any = None) -> Qualification:
    """Run ``job``'s test for one personal version against the main model (see the module's docstring)."""

    from row_bot.learning.improver import archive
    from row_bot.learning.spending import TokenMeter

    moment = float(now if now is not None else store.now())
    method = method or archive.current(store).strategy
    work = items(store, now=moment, job=job)
    blank = Qualification(job, int(version), model_ref, round(moment, 3), len(work), 0, 0, 0)
    if len(work) < MIN_ITEMS:
        return Qualification(**{**blank.to_dict(), "verdict": "too_few"})
    parent = meter if meter is not None else TokenMeter.for_month()
    personal, main = _Side(client, model_ref, parent), _Side(client, None, parent)
    run = _drafting if job == DRAFTING else _practice
    personal_passed, personal_done, personal_end = run(store, work, personal, now=moment, method=method)
    main_passed, main_done, main_end = run(store, work, main, now=moment, method=method)
    asked = min(personal_done, main_done)
    if "limit" in (personal_end, main_end):
        verdict = "limit"
    elif asked < MIN_ITEMS * (PRACTICE_PER_ITEM if job == PRACTICE else 1):
        verdict = "too_few"
    elif personal_passed / asked >= main_passed / asked - MARGIN:
        verdict = "qualified"
    else:
        verdict = "not_matched"
    return Qualification(job, int(version), model_ref, round(moment, 3), len(work), personal_passed, main_passed,
                         asked, personal.meter.used, main.meter.used, round(personal.seconds, 3), verdict)


def due(store: Any, job: str, version: int, *, now: float) -> bool:
    """Whether ``job`` should be tested tonight for ``version``: never tested on it, or not for a while."""

    last = (state(store).get(job) or {}).get("last") or {}
    if int(last.get("version") or 0) != int(version):
        return True
    return now - float(last.get("at") or 0.0) >= RETEST_DAYS * 86_400


def apply(store: Any, result: Qualification) -> str:
    """Take the job over, keep it, or hand it back, by ``result``; returns who does the job now."""

    found = state(store)
    held = found.get(result.job) or {}
    held["last"] = result.to_dict()
    found[result.job] = held
    if result.qualified:
        if held.get("by") != "personal" or held.get("version") != result.version:
            held.update({"by": "personal", "version": result.version, "model_ref": result.model_ref,
                         "since": result.at, "reason": ""})
            _event(found, result.job, "took_over", result.at, version=result.version)
    elif held.get("by") == "personal" and result.verdict == "not_matched":
        held.update({"by": "main", "since": result.at, "reason": "not_matched"})
        _event(found, result.job, "handed_back", result.at, version=held.get("version"), reason="not_matched")
    _save(store, found)
    return str(held.get("by") or "main")


def run_due(store: Any, *, client: Any, night: str, now: float | None = None,
            should_stop: Callable[[], bool] = lambda: False) -> dict[str, Any] | None:
    """Tonight's job tests (after the night's other work): each offered job due for the newest ready version."""

    jobs = offered()
    if not jobs or not store.exists() or not gate.is_enabled(store.root):
        return None
    moment = float(now if now is not None else store.now())
    ready = newest_ready(store.root)
    report: dict[str, Any] = {"night": night, "state": "done", "tested": {}}
    for job in jobs:
        held = state(store).get(job) or {}
        if held.get("by") == "personal" and (ready is None or ready[1] != held.get("model_ref")):
            hand_back(store, job, "not_served" if ready is None or ready[0] == held.get("version") else "new_version",
                      now=moment)
        if ready is None or not due(store, job, ready[0], now=moment):
            continue
        if should_stop():
            report["state"] = "stopped"
            break
        result = qualify(store, job, client=client, version=ready[0], model_ref=ready[1], now=moment)
        report["tested"][job] = {"verdict": result.verdict, "by": apply(store, result)}
    store.set_meta_values({NIGHT_KEY: json.dumps(report, sort_keys=True)})
    return report


def tested_tonight(store: Any, night: str) -> bool:
    if not offered() or not store.exists():
        return True
    found = _meta(store, NIGHT_KEY)
    return found.get("night") == night and found.get("state") != "stopped"


__all__ = ["DRAFTING", "JOBS", "JobClient", "MARGIN", "MAX_ITEMS", "MIN_ITEMS", "PRACTICE", "PRACTICE_PER_ITEM",
           "Qualification", "RETEST_DAYS", "apply", "client_for", "due", "enabled", "hand_back", "items",
           "model_for", "month_key", "newest_ready", "offered", "qualify", "run_due", "state", "tested_tonight"]
