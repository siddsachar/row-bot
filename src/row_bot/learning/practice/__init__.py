"""Practice (L4b; D6, D15, D17, O18, O21): tasks Row-Bot makes up from its own mistakes.

In a build with practice on (:func:`row_bot.learning.practice_enabled`), once a night after the look for things to
improve (``runner``), Learning

1. **makes practice tasks** (``generate``) for the issues in a kind of work the task packs cover whose evidence is
   new since it last practised them, as many as the learning method in use says (``practice.made``), kept only
   when verifiable, novel against the audit vault and new;
2. **runs them** once on the assistant as it is now, in throwaway sandboxes (``sandbox``), each checked by its own
   check ("Practised 12 document tasks it made up: 9 passed");
3. **keeps them** as one ``TestRun`` (suite ``practice``) whose results hold the tasks, how each did and, for a
   passing task run through the real runtime, its last request and reply as sent. The run names the chats its
   issues came from (``source_thread_ids``): deleting or switching off one of them marks the run, and its tasks and
   turns are no longer used.

**What practice feeds** (never the audit vault, and it never touches the hidden problems):

- **the tests of drafted changes** (``changes.Tester``): up to the method's ``practice.tested`` tasks of the
  change's kind, newest first, in both arms;
- **strategy trials** (``improver.meta``): each arm makes its own practice for its drafts at its own budget, by its
  own method, and keeps it to itself;
- **the training data** (``training.dataset``): the passing turns, as their own slice of the mix.

Everything here leaves out what is held for testing without reading the vault: issues are made from evidence that
leaves out the held-out decisions (``diagnosis.collect``), and each task is checked novel by the vault's yes or no.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from row_bot import learning as gate
from row_bot.learning.packs import PACKS, PackError, PackTask

logger = logging.getLogger(__name__)

RESULTS_FORMAT = "row_bot.learning.practice_run"
RUNS_KEY = "practice.runs"
NIGHT_KEY = "practice.night"
#: Each issue's newest evidence when practice was last made from it: nothing is made again without new evidence.
MADE_KEY = "practice.made"
#: The practice tasks in use, newest first, and the nights' runs remembered.
POOL_LIMIT = 200
RUNS_KEPT = 120
_SUMMED = ("issues", "asked", "made", "unreadable", "unverifiable", "repeats", "not_novel", "ran", "passed",
           "coding_left_out", "needs_container", "tokens")


def enabled() -> bool:
    """Whether this build practises (its switch, L4b)."""

    return gate.practice_enabled()


def _meta(store: Any, key: str, default: Any) -> Any:
    try:
        found = json.loads(store.meta_value(key, "") or "null")
    except ValueError:
        return default
    return found if isinstance(found, type(default)) else default


def runs(store: Any) -> list[tuple[Any, dict[str, Any]]]:
    """The practice runs still in use, newest first, with their results: a run whose chats were deleted or
    switched off is left out."""

    from row_bot.learning.contracts import TestRun

    if not store.exists():
        return []
    found: list[tuple[Any, dict[str, Any]]] = []
    for run_id in reversed(_meta(store, RUNS_KEY, [])):
        run = store.get("test_run", str(run_id))
        if not isinstance(run, TestRun) or run.suite != "practice" or store.marks("test_run", run.id):
            continue
        raw = store.read_blob(run.results_blob or "") if run.results_blob else None
        try:
            body = json.loads(raw.decode("utf-8")) if raw is not None else None
        except (UnicodeDecodeError, ValueError):
            body = None
        if isinstance(body, dict) and body.get("format") == RESULTS_FORMAT:
            found.append((run, body))
    return found


def pool(store: Any, kind: str | None = None, *, limit: int | None = None) -> list[PackTask]:
    """The practice tasks in use (of one kind of work), newest first."""

    from row_bot.learning.practice.generate import fingerprint

    tasks: list[PackTask] = []
    seen: set[str] = set()
    for _run, body in runs(store):
        for raw in body.get("tasks") or ():
            try:
                task = PackTask.from_dict(raw)
            except (PackError, KeyError, TypeError, ValueError):
                continue
            mark = fingerprint(task)
            if task.split != "practice" or mark in seen:
                continue
            seen.add(mark)
            if kind is None or task.pack == kind:
                tasks.append(task)
        if len(seen) >= POOL_LIMIT:
            break
    return tasks[: limit if limit is not None else POOL_LIMIT]


def turns(store: Any) -> list[dict[str, Any]]:
    """The passing practice turns in use (the training data's practice slice), newest first: each with its task's
    id and kind of work, the assistant that ran it, when, and the chats its issue came from."""

    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run, body in runs(store):
        kinds = {str(raw.get("id") or ""): str(raw.get("pack") or "") for raw in body.get("tasks") or ()
                 if isinstance(raw, dict)}
        for task_id, turn in sorted((body.get("turns") or {}).items()):
            if task_id in seen or task_id not in kinds or not isinstance(turn, dict):
                continue
            seen.add(task_id)
            found.append({"task_id": task_id, "kind": kinds[task_id], "turn": turn, "model_ref": run.model_ref,
                          "at": float(run.started_at), "threads": list(run.source_thread_ids)})
    return found


def last_night(store: Any) -> dict[str, Any] | None:
    """The last night's practice, in counts (Overview's Overnight line), or None before the first."""

    if not store.exists():
        return None
    found = _meta(store, NIGHT_KEY, {})
    return found or None


def due_issues(store: Any, *, now: float, method: Any, book: Any = None) -> list[Any]:
    """Tonight's issues to practise: in a kind of work the packs cover, with enough evidence by the method, and
    evidence newer than when practice was last made from them; in the method's issue order, at most its
    ``budget.issues``."""

    from row_bot.learning import corrections, diagnosis

    issues, _left_out = diagnosis.collect(store, now=now, book=book or corrections.Book(store))
    made = _meta(store, MADE_KEY, {})
    due = [issue for issue in issues if issue.kind in PACKS and issue.enough(method)
           and issue.newest() > float(made.get(issue.key) or 0.0)]
    first = method.issues.feedback_first
    due.sort(key=lambda item: (-(item.feedback > 0) if first else 0, -item.weight(method), -item.newest(), item.key))
    return due[: max(0, int(method.budget.issues))]


def practise(store: Any, *, night: str, client: Any, tester: Any, method: Any,
             should_stop: Callable[[], bool] = lambda: False, now: float | None = None,
             meter: Any = None) -> dict[str, Any] | None:
    """Tonight's practice (see the module's docstring): make, run, keep. ``tester`` is the changes' tester (the
    main model, its runner and the container check); returns tonight's report, or None when practice is off."""

    if not enabled() or not store.exists() or not gate.is_enabled(store.root):
        return None
    from row_bot.learning import corrections, evaluation
    from row_bot.learning.practice import generate, sandbox

    moment = float(now if now is not None else store.now())
    meter = meter if meter is not None else evaluation.TokenMeter.for_month()
    report: dict[str, Any] = {"night": night, "state": "done", "by_kind": {}, **{key: 0 for key in _SUMMED}}
    book = corrections.Book(store)
    container = tester._has_container()  # noqa: SLF001 - the tester's own check (O21)
    known = {generate.fingerprint(task) for task in pool(store)}
    made_from = _meta(store, MADE_KEY, {})
    # Tasks made earlier tonight that didn't run before the person came back run now, with the new ones.
    tasks, issue_of, threads = _not_run(store, night)
    for issue in due_issues(store, now=moment, method=method, book=book):
        if should_stop():
            report["state"] = "stopped"
            break
        made = generate.make(store, issue, client=client, count=method.practice.made, now=moment, meter=meter,
                             container=lambda: container, known=known, method=method, book=book)
        report["tokens"] += made.tokens
        if made.state == "limit":
            report["state"] = "limit"
            break
        if made.state == "unavailable":
            report["state"] = "unavailable"
            break
        report["needs_container"] += made.state == "needs_container"
        if made.state != "done":
            continue
        report["issues"] += 1
        for key in ("asked", "unreadable", "unverifiable", "repeats", "not_novel"):
            report[key] += int(getattr(made, key))
        report["made"] += len(made.kept)
        made_from[issue.key] = issue.newest()
        for task in made.kept:
            known.add(generate.fingerprint(task))
            issue_of[task.id] = issue.key
            tasks.append(task)
        if made.kept:
            threads |= {name for item in issue.evidence for name in (item.thread_id, item.conversation_id) if name}
    store.set_meta_values({MADE_KEY: json.dumps(made_from, sort_keys=True)})
    ran = sandbox.Ran()
    if tasks and report["state"] == "done":
        ran = _run(tasks, tester=tester, container=container, meter=meter, should_stop=should_stop)
        report["state"] = ran.state
    report["ran"], report["passed"], report["coding_left_out"] = ran.ran, ran.passed, ran.coding_left_out
    for case in ran.cases:
        if case.counted:
            counts = report["by_kind"].setdefault(next(task.pack for task in tasks if task.id == case.task_id), [0, 0])
            counts[0] += 1
            counts[1] += int(case.passed)
    if tasks:
        _keep(store, tasks, ran, issue_of=issue_of, threads=threads, model_ref=tester._main(), started=moment,  # noqa: SLF001
              night=night)
    _note(store, report)
    try:
        from row_bot.application.live_updates import publish

        publish("learning")
    except Exception:  # noqa: BLE001 - the views refresh on their own
        logger.debug("Learning couldn't announce tonight's practice", exc_info=True)
    return last_night(store)


def _not_run(store: Any, night: str) -> tuple[list[PackTask], dict[str, str], set[str]]:
    """Tonight's practice tasks that were made but not run (the night stopped), with their issues and chats."""

    tasks: list[PackTask] = []
    issue_of: dict[str, str] = {}
    threads: set[str] = set()
    ran = {str(case.get("task") or "") for run, body in runs(store) if body.get("night") == night
           for case in body.get("cases") or () if isinstance(case, dict)}
    for run, body in runs(store):
        if body.get("night") != night:
            continue
        for raw in body.get("tasks") or ():
            try:
                task = PackTask.from_dict(raw)
            except (PackError, KeyError, TypeError, ValueError):
                continue
            if task.id in ran or task.id in issue_of:
                continue
            tasks.append(task)
            issue_of[task.id] = str((body.get("issues") or {}).get(task.id) or "")
            threads |= set(run.source_thread_ids)
    return tasks, issue_of, threads


def _run(tasks: list[PackTask], *, tester: Any, container: bool, meter: Any,
         should_stop: Callable[[], bool]) -> Any:
    from row_bot.learning import changes, evaluation
    from row_bot.learning.practice import sandbox

    main = tester._main()  # noqa: SLF001
    lessons, ways = changes.context_in_use()
    arm = evaluation.Arm("practice", model_ref=main or None, lessons=lessons, ways=ways)
    runner, close = tester._runner([arm], main) if tester.plan.runtime else (None, None)  # noqa: SLF001
    if runner is not None and hasattr(runner, "keep_turns"):
        runner.keep_turns = True
    try:
        return sandbox.run(tasks, arm, container=container, client=tester.client, runner=runner, meter=meter,
                           should_stop=should_stop, budget=evaluation.Budget(task_tokens=tester.plan.task_tokens))
    finally:
        if close is not None:
            try:
                close()
            except Exception:  # noqa: BLE001
                logger.debug("Learning couldn't stop its practice worker", exc_info=True)


def _keep(store: Any, tasks: list[PackTask], ran: Any, *, issue_of: dict[str, str], threads: set[str],
          model_ref: str, started: float, night: str) -> None:
    from row_bot.learning.contracts import Cost, TestRun, new_id

    cases = [{"task": case.task_id, "status": case.status, "tokens": case.tokens} for case in ran.cases]
    body = {"format": RESULTS_FORMAT, "version": 1, "night": night, "tasks": [task.to_dict() for task in tasks],
            "issues": issue_of, "cases": cases, "turns": ran.turns, "coding_left_out": ran.coding_left_out}
    try:
        [sha] = store.put_blobs([json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")])
        counted = [case for case in ran.cases if case.counted]
        run = TestRun(id=new_id("run"), subject_kind="baseline", suite="practice", started_at=started,
                      model_ref=model_ref or None, status="stopped" if ran.state != "done" else "completed",
                      passed=ran.passed, failed=len(counted) - ran.passed, total=len(counted),
                      cost=Cost(tokens=max(0, ran.tokens - ran.local_tokens)), results_blob=sha,
                      ended_at=max(started, float(store.now())), source_thread_ids=tuple(sorted(threads))[:50_000])
        store.put(run)
    except Exception:  # noqa: BLE001 - tonight's practice is lost, not the night
        logger.warning("Learning couldn't keep tonight's practice tasks", exc_info=True)
        return
    kept = [*_meta(store, RUNS_KEY, []), run.id][-RUNS_KEPT:]
    store.set_meta_values({RUNS_KEY: json.dumps(kept)})


def note_failed(store: Any, night: str) -> None:
    """Tonight's practice couldn't run: the night goes on to its tests and tries again tomorrow."""

    _note(store, {"night": night, "state": "failed", "by_kind": {}, **{key: 0 for key in _SUMMED}})


def _note(store: Any, report: dict[str, Any]) -> None:
    earlier = last_night(store)
    if earlier and earlier.get("night") == report["night"] and earlier.get("state") == "stopped":
        # The same night going on after the person came back: its counts add up.
        for key in _SUMMED:
            report[key] = int(report.get(key) or 0) + int(earlier.get(key) or 0)
        for kind, (ran, passed) in (earlier.get("by_kind") or {}).items():
            counts = report["by_kind"].setdefault(kind, [0, 0])
            counts[0] += int(ran)
            counts[1] += int(passed)
    store.set_meta_values({NIGHT_KEY: json.dumps(report, sort_keys=True)})


__all__ = ["NIGHT_KEY", "POOL_LIMIT", "RESULTS_FORMAT", "due_issues", "enabled", "last_night", "note_failed", "pool",
           "practise", "runs", "turns"]
