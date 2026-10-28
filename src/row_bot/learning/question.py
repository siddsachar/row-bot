"""The quick question (L4b; UX §3 "Quick question"): active collection, at most once a day.

**When one is asked.** Only when the answer would settle real uncertainty, and Learning decides that from its own
night: the look for things to improve diagnosed an issue and said **"needs more examples"** (its evidence isn't
enough to know what to change). Then one ``learning_diagnosis`` call is asked for a question with exactly two short
answers that would settle it; when the model can't write one, nothing is asked. At most one a day, never two open,
never again about an issue the person answered or waved away (✕), and only in a build with the quick question on
(:func:`row_bot.learning.quick_question_enabled`).

**Where.** It is part of Learning's snapshot, so Overview's Needs you shows it on every device the person uses
(desktop, phone, a remote browser), as soon as it is asked; the morning summary says one is waiting. It never blocks
anything: the night goes on, and the question waits until it is answered, waved away, or replaced the next day.

**The answer** is the person's own feedback on that issue: the next night's diagnosis shows it ("The person answered
a quick question: … → …"), counts it as their feedback (E9) and as new evidence. It is not a Signal, so nothing in
capture, the audit vault's eligibility or the held-out corrections changes (H2's corrections stay the person's own).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from row_bot import learning as gate

logger = logging.getLogger(__name__)

PURPOSE = "learning_diagnosis"
OPEN_KEY = "question.open"
ASKED_KEY = "question.asked_on"
CONSIDERED_KEY = "question.considered"
DONE_KEY = "question.done"
ANSWERS_KEY = "question.answers"
#: Each issue keeps its newest answers (the diagnosis is shown them).
ANSWERS_KEPT = 3
QUESTION_LIMIT = 160
ANSWER_LIMIT = 40
#: How far back the night's "needs more examples" are looked for.
LOOKBACK_SECONDS = 24 * 3600.0

SYSTEM = """Row-Bot, an AI assistant, couldn't tell from the person's feedback what it should do differently in \
one kind of work. Write one short question for the person whose answer would settle it, with exactly two short \
answers they could tap.

Plain words: the question under 20 words, each answer under 5 words. Never include names, addresses, links or \
numbers from their work. If no single question with two answers would settle it, answer {"none": true}.

Answer only with JSON: {"question": "...", "answers": ["...", "..."]}"""


class QuestionError(ValueError):
    """``changed``: the question was answered, waved away or replaced; ``unknown_answer``: not one of its answers."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def enabled() -> bool:
    return gate.quick_question_enabled()


def _meta(store: Any, key: str, default: Any) -> Any:
    try:
        found = json.loads(store.meta_value(key, "") or "null")
    except ValueError:
        return default
    return found if isinstance(found, type(default)) else default


def _day(moment: float) -> str:
    return datetime.fromtimestamp(float(moment)).date().isoformat()


def current(store: Any) -> dict[str, Any] | None:
    """The question waiting for the person (id, question, its two answers, the kind of work), or None."""

    if not enabled() or store is None or not store.exists():
        return None
    found = _meta(store, OPEN_KEY, {})
    if not found.get("id") or len(found.get("answers") or ()) != 2:
        return None
    return {key: found[key] for key in ("id", "question", "answers", "kind", "asked_at")}


def answers_for(store: Any) -> dict[str, list[dict[str, Any]]]:
    """Every issue's answers to quick questions (issue key → [{question, answer, at}]), for the diagnosis."""

    if store is None or not store.exists():
        return {}
    return {str(key): [dict(item) for item in value if isinstance(item, dict)]
            for key, value in _meta(store, ANSWERS_KEY, {}).items() if isinstance(value, list)}


def _uncertain(store: Any, now: float) -> list[tuple[str, str]]:
    """The issues the last day's looks left at "needs more examples" (issue key, what was found), newest first."""

    from row_bot.learning.contracts import Attempt

    found: dict[str, str] = {}
    for item in store.find("attempt", newest_first=True, limit=500):
        if not isinstance(item, Attempt) or item.created_at < now - LOOKBACK_SECONDS or not item.issue:
            continue
        if str(item.reason or "").startswith("needs_examples") and item.issue not in found:
            found[item.issue] = str(item.hypothesis or "")
    return list(found.items())


def _read(reply: str) -> tuple[str, list[str]] | None:
    match = re.search(r"\{.*\}", str(reply or ""), flags=re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("none"):
        return None
    question = " ".join(str(data.get("question") or "").split())
    answers = [" ".join(str(item or "").split()) for item in data.get("answers") or ()]
    if not question or len(question) > QUESTION_LIMIT or len(answers) != 2:
        return None
    if any(not item or len(item) > ANSWER_LIMIT for item in answers) or answers[0].lower() == answers[1].lower():
        return None
    from row_bot import evolution

    if evolution.injection_problems("\n".join([question, *answers])):
        return None
    return question, answers


def maybe_ask(store: Any, *, client: Any, night: str, now: float | None = None,
              meter: Any = None) -> dict[str, Any] | None:
    """Ask today's quick question when one would settle real uncertainty (see the module's docstring); returns the
    question asked, else None. Looked at once a night, one call at most."""

    if not enabled() or not store.exists() or not gate.is_enabled(store.root):
        return None
    if store.meta_value(CONSIDERED_KEY, "") == night:
        return None
    store.set_meta_values({CONSIDERED_KEY: night})
    moment = float(now if now is not None else store.now())
    today = _day(moment)
    if store.meta_value(ASKED_KEY, "") == today:
        return None
    done = _meta(store, DONE_KEY, {})
    candidates = [(key, finding) for key, finding in _uncertain(store, moment) if key not in done]
    if not candidates:
        return None
    from row_bot.learning import corrections, diagnosis

    book = corrections.Book(store)
    issues = {issue.key: issue for issue in diagnosis.collect(store, now=moment, book=book)[0]}
    chosen = next(((issues[key], finding) for key, finding in candidates if key in issues), None)
    if chosen is None:
        return None
    issue, finding = chosen
    from langchain_core.messages import HumanMessage, SystemMessage

    from row_bot.learning.calls import estimate_tokens, text_of
    from row_bot.learning.spending import TokenMeter

    shown = diagnosis.describe(store, book, issue, now=moment)
    messages = [SystemMessage(content=SYSTEM),
                HumanMessage(content=shown + (f"\n\nWhat Learning found: {finding}" if finding else ""))]
    meter = meter if meter is not None else TokenMeter.for_month()
    if not meter.allows(estimate_tokens(messages), local=client.is_local(None)):
        return None
    store.set_meta_values({ASKED_KEY: today})  # one call a day, whatever it answers
    try:
        reply = client.invoke(messages, tools=None, model_ref=None, purpose=PURPOSE)
    except Exception as error:  # noqa: BLE001 - no question today
        logger.info("Learning couldn't write a quick question (%s)", type(error).__name__)
        return None
    meter.charge(reply.tokens, local=reply.local)
    read = _read(text_of(reply.message))
    if read is None:
        return None
    from row_bot.learning.contracts import new_id

    question = {"id": new_id("qq"), "issue": issue.key, "kind": issue.kind, "question": read[0],
                "answers": read[1], "asked_at": round(moment, 3)}
    store.set_meta_values({OPEN_KEY: json.dumps(question, sort_keys=True)})
    _announce()
    return current(store)


def _open(store: Any, question_id: str) -> dict[str, Any]:
    found = _meta(store, OPEN_KEY, {}) if store.exists() else {}
    if not question_id or found.get("id") != question_id:
        raise QuestionError("changed")
    return found


def answer(store: Any, question_id: str, choice: str) -> None:
    """The person tapped one of the question's answers: it becomes their feedback on the issue."""

    found = _open(store, question_id)
    if choice not in found.get("answers", ()):
        raise QuestionError("unknown_answer")
    moment = round(float(store.now()), 3)
    answers = _meta(store, ANSWERS_KEY, {})
    answers[found["issue"]] = [*(answers.get(found["issue"]) or []),
                               {"question": found["question"], "answer": choice, "at": moment}][-ANSWERS_KEPT:]
    done = {**_meta(store, DONE_KEY, {}), found["issue"]: {"state": "answered", "at": moment}}
    store.set_meta_values({ANSWERS_KEY: json.dumps(answers, sort_keys=True), DONE_KEY: json.dumps(done, sort_keys=True),
                           OPEN_KEY: ""})
    _announce()


def dismiss(store: Any, question_id: str) -> None:
    """✕: this question goes, and Learning never asks about that issue again."""

    found = _open(store, question_id)
    done = {**_meta(store, DONE_KEY, {}), found["issue"]: {"state": "dismissed", "at": round(float(store.now()), 3)}}
    store.set_meta_values({DONE_KEY: json.dumps(done, sort_keys=True), OPEN_KEY: ""})
    _announce()


def _announce() -> None:
    try:
        from row_bot.application.live_updates import publish

        publish("learning")
    except Exception:  # noqa: BLE001 - the views refresh on their own
        logger.debug("Learning couldn't announce its quick question", exc_info=True)


__all__ = ["QuestionError", "answer", "answers_for", "current", "dismiss", "enabled", "maybe_ask"]
