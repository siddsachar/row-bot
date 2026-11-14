"""Ingest a benchmark's sessions through Row-Bot's real memory path, then ask its questions as turns.

Ingestion is what the app does with a conversation it holds:

1. each session becomes a saved conversation (``threads.create_thread`` and
   ``threads.append_checkpoint_messages``: the person's messages and the other side's);
2. background memory extraction reads it (``memory_extraction.run_extraction``: the
   extraction prompt, the knowledge graph's reconcile and save, the projection repair),
   run until nothing is left unread, with the extraction's "Today is" pinned to the
   session's own date;
3. optionally, one Dream Cycle pass (``dream_cycle.run_dream_cycle``) after the last session.

Each question is then one turn in a new incognito conversation (so it is never extracted
and later questions' conversation search never sees it), with the turn context's date
pinned to the benchmark's question date. The turn has the pinned tools (by default
Row-Bot's memory tools: ``memory`` and ``conversation_search``) and recalls memories as
every turn does.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from ..ledger import BudgetStop
from ..runtime import BenchRuntime, pinned_clock
from ..trajectory import Step
from .datasets import MemoryTask, Session

#: Extraction runs read at most ``memory_extraction._CALLS_PER_RUN`` windows; a session is
#: read by as many runs as it needs, up to this many.
MAX_EXTRACTION_RUNS = 40
QUESTION_TEMPLATE = "{question}"


class StopRequested(RuntimeError):
    """The run's stop file appeared."""


def session_messages(task_id: str, session: Session) -> list[Any]:
    from langchain_core.messages import AIMessage, HumanMessage

    messages = []
    for index, turn in enumerate(session.turns):
        kind = HumanMessage if turn.role == "user" else AIMessage
        messages.append(kind(id=f"{task_id}-{session.session_id}-{index}", content=turn.text))
    return messages


def extract_until_read(runtime: BenchRuntime) -> dict[str, Any]:
    """Run background extraction until every conversation is read to its end."""
    import row_bot.memory_extraction as extraction

    saved = runs = no_reply = 0
    errors: list[str] = []
    while runs < MAX_EXTRACTION_RUNS:
        before = len(extraction._load_extraction_journal())  # noqa: SLF001
        refused = len(runtime.ledger.refused)
        saved += extraction.run_extraction()
        journal = extraction._load_extraction_journal()  # noqa: SLF001
        if len(runtime.ledger.refused) > refused:
            raise BudgetStop(runtime.ledger.refused[-1])
        if len(journal) == before:
            break  # nothing was due: every conversation is read to its end
        runs += 1
        entry = journal[-1]
        errors.extend(str(item) for item in entry.get("errors") or [])
        if any("did not reply" in str(item) for item in entry.get("errors") or []):
            no_reply += 1
            if no_reply >= 3:
                break  # the model is not answering; the trajectory says so
            continue
        waiting = entry.get("threads_waiting") or any(item.get("read_to_end") is False
                                                       for item in entry.get("thread_details") or [])
        if not waiting:
            break
    return {"saved": saved, "extraction_runs": runs, "no_reply_runs": no_reply, "errors": errors[:5]}


def memory_count() -> int:
    from row_bot import memory

    return len(memory.list_memories(limit=100_000))


def ingest(runtime: BenchRuntime, task: MemoryTask, *, stop: Callable[[], bool] = lambda: False,
           on_step: Callable[[Step], None] = lambda step: None) -> list[Step]:
    from row_bot import threads

    steps: list[Step] = []
    for session in task.sessions:
        if stop():
            raise StopRequested(f"stopped before {session.session_id}")
        step = Step(kind="ingest", name=session.session_id)
        started = time.monotonic()
        thread_id = runtime.new_thread(f"{task.benchmark} {task.task_id} {session.session_id}")
        threads.append_checkpoint_messages(thread_id, session_messages(task.task_id, session))
        try:
            with pinned_clock(session.when):
                found = extract_until_read(runtime)
        except BudgetStop as exc:
            step.status, step.errors = "budget", [str(exc)]
            found = {}
        step.seconds = round(time.monotonic() - started, 3)
        step.metrics = {"when": session.when.isoformat(), "turns": len(session.turns),
                        "chars": sum(len(turn.text) for turn in session.turns), **found,
                        "memories_total": memory_count()}
        runtime.drain_calls(step, phase="ingest")
        steps.append(step)
        on_step(step)
        if step.status == "budget":
            raise BudgetStop(step.errors[0])
    return steps


def dream(runtime: BenchRuntime, task: MemoryTask) -> Step:
    """One Dream Cycle pass after the last session (merges, supersedes, enrichment, inferred links)."""
    from row_bot import dream_cycle

    step = Step(kind="dream", name="dream")
    started = time.monotonic()
    when = task.sessions[-1].when if task.sessions else task.ask_at
    with pinned_clock(when):
        found = dream_cycle.run_dream_cycle() or {}
    step.seconds = round(time.monotonic() - started, 3)
    step.metrics = {key: (len(value) if isinstance(value, list) else value) for key, value in found.items()
                    if key in {"merges", "superseded", "enrichments", "inferred_relations", "errors", "duration_s"}}
    step.metrics["memories_total"] = memory_count()
    runtime.drain_calls(step, phase="dream")
    return step


def ask(runtime: BenchRuntime, task: MemoryTask, *, template: str = QUESTION_TEMPLATE,
        stop: Callable[[], bool] = lambda: False, on_step: Callable[[Step], None] = lambda step: None) -> list[Step]:
    steps: list[Step] = []
    with pinned_clock(task.ask_at):
        for question in task.questions:
            if stop():
                raise StopRequested(f"stopped before {question.question_id}")
            thread_id = runtime.new_thread(f"{task.benchmark} {question.question_id}", incognito=True)
            step = runtime.turn(thread_id, template.format(question=question.question), name=question.question_id)
            step.metrics["category"] = question.category
            steps.append(step)
            on_step(step)
            if step.status == "budget":
                raise BudgetStop(step.errors[0] if step.errors else "budget")
    return steps


def stop_file(run_dir: Path) -> Callable[[], bool]:
    """The pause-on-request check: ``<run>/STOP`` exists."""
    return lambda: (run_dir / "STOP").exists()
