"""The full-context baseline: the same model reads the whole history, with no memory at all.

For each question, one call to the reader model through Row-Bot's own provider transport (the
same model, the same reasoning setting as Row-Bot ships it), with no tools, no recall and no
saved conversations: the prompt is the history, oldest first, the date and the question
(:data:`TEMPLATE`, recorded with its SHA-256 in every trajectory). This is the honest
comparison for the memory path: same reader, same questions, same judge.

When the history does not fit the model's context window (less room for the answer), the
benchmark's own published rule cuts it:

- LongMemEval: the first tokens are kept (``run_generation.py``: ``tokens[:max_length]``);
- LongMemEval-V2: the longest prefix of whole trajectories that fits, and never more than
  200,000 tokens (``harness.truncate_memory_context``);
- BEAM: the largest recent segment that fits (the paper's rule for its 10M scale; it gives
  none for 1M);
- LoCoMo: every conversation fits; were one not to, the latest sessions would be kept.

Tokens are counted with tiktoken's ``o200k_base`` (the GPT family's); for a local model the
count is an approximation, and the report says so.
"""
from __future__ import annotations

import hashlib
import time
from functools import lru_cache
from typing import Any, Callable

from ..trajectory import Step
from .datasets import BEAM_1M, LOCOMO, LONGMEMEVAL_S, LONGMEMEVAL_V2, MemoryTask, Question, Session

TEMPLATE = ("Below is the full history of my past conversations, oldest first. Use it to answer my question "
            "at the end.\n\n{history}\n\n{date_line}Question: {question}")
TEMPLATE_SHA256 = hashlib.sha256(TEMPLATE.encode("utf-8")).hexdigest()
KEEP_EARLIEST_TOKENS = "keep the first tokens (LongMemEval)"
KEEP_EARLIEST_ITEMS = "keep the longest prefix of whole trajectories, at most 200,000 tokens (LongMemEval-V2)"
KEEP_LATEST = "keep the largest recent segment that fits (BEAM)"
KEEP_LATEST_UNPUBLISHED = "keep the latest sessions that fit (no published rule; every LoCoMo conversation fits)"
RULES = {LONGMEMEVAL_S: KEEP_EARLIEST_TOKENS, LONGMEMEVAL_V2: KEEP_EARLIEST_ITEMS, BEAM_1M: KEEP_LATEST,
         LOCOMO: KEEP_LATEST_UNPUBLISHED}
_LATEST = (KEEP_LATEST, KEEP_LATEST_UNPUBLISHED)
V2_MAX_TOKENS = 200_000
#: Room kept for the answer (and its reasoning) and the prompt's own words.
ANSWER_ROOM_TOKENS = 16_000


@lru_cache(maxsize=1)
def _encoding() -> Any:
    import tiktoken

    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text, disallowed_special=()))


def render_session(number: int, session: Session) -> str:
    lines = [f"{turn.speaker or turn.role}: {turn.text}" for turn in session.turns]
    return f"### Session {number} ({session.when.strftime('%d %B %Y, %H:%M')})\n" + "\n".join(lines)


def build_history(task: MemoryTask, limit_tokens: int) -> tuple[str, dict[str, Any]]:
    """The task's history cut to *limit_tokens* by its benchmark's rule, and what was cut."""
    rule = RULES.get(task.benchmark, KEEP_LATEST)
    limit = min(limit_tokens, V2_MAX_TOKENS) if rule == KEEP_EARLIEST_ITEMS else limit_tokens
    parts = [render_session(number, session) for number, session in enumerate(task.sessions, start=1)]
    sizes = [count_tokens(part) + 2 for part in parts]
    total = sum(sizes)
    info: dict[str, Any] = {"rule": rule, "limit_tokens": limit, "history_tokens": total, "sessions": len(parts),
                            "truncated": total > limit}
    if total <= limit:
        info["kept_sessions"] = len(parts)
        return "\n\n".join(parts), info
    if rule == KEEP_EARLIEST_TOKENS:
        encoding = _encoding()
        text = encoding.decode(encoding.encode("\n\n".join(parts), disallowed_special=())[:limit])
        info["kept_tokens"] = limit
        return text, info
    kept: list[str] = []
    used = 0
    order = range(len(parts)) if rule == KEEP_EARLIEST_ITEMS else range(len(parts) - 1, -1, -1)
    for index in order:
        if used + sizes[index] > limit:
            break
        kept.append(parts[index])
        used += sizes[index]
    if rule in _LATEST:
        kept.reverse()
        if not kept:  # even the newest session is longer than the window: its last tokens
            encoding = _encoding()
            kept = [encoding.decode(encoding.encode(parts[-1], disallowed_special=())[-limit:])]
            used = limit
    info.update({"kept_sessions": len(kept), "kept_tokens": used})
    return "\n\n".join(kept), info


def prompt(task: MemoryTask, question: Question, history: str) -> str:
    date_line = "" if task.benchmark == LONGMEMEVAL_V2 else f"Current date: {task.ask_at.strftime('%A, %d %B %Y, %H:%M')}\n"
    return TEMPLATE.format(history=history, date_line=date_line, question=question.question)


def ask(runtime: Any, task: MemoryTask, call: Callable[[str], str], *, context_tokens: int,
        stop: Callable[[], bool] = lambda: False, on_step: Callable[[Step], None] = lambda step: None) -> list[Step]:
    """Each question with the whole (or cut) history; *call* sends one prompt and returns the reply."""
    from .adapter import StopRequested

    history, info = build_history(task, max(1_000, context_tokens - ANSWER_ROOM_TOKENS))
    steps: list[Step] = []
    for question in task.questions:
        if stop():
            raise StopRequested(f"stopped before {question.question_id}")
        step = Step(kind="turn", name=question.question_id, prompt=question.question)
        started = time.monotonic()
        try:
            step.answer = call(prompt(task, question, history))
        except Exception as exc:  # noqa: BLE001 - the step records it; budget stops are raised by the caller's check
            step.status, step.errors = "error", [f"{type(exc).__name__}: {exc}"[:500]]
            if "benchmark budget" in str(exc):
                step.status = "budget"
        step.seconds = round(time.monotonic() - started, 3)
        step.metrics = {"category": question.category, "mode": "full-context", **info,
                        "template_sha256": TEMPLATE_SHA256}
        runtime.drain_calls(step)
        steps.append(step)
        on_step(step)
        if step.status == "budget":
            from ..ledger import BudgetStop

            raise BudgetStop(step.errors[0])
        from ..runtime import ProviderLimit, limit_text

        if step.errors and limit_text(step.errors[0]):
            raise ProviderLimit(step.errors[0][:300])
    return steps
