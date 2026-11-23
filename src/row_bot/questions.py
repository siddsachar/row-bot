"""Row-Bot asks the person questions with options, in a card (N4).

``ask_person`` pauses the turn on the same path as an approval: a LangGraph
``interrupt()`` of kind ``question``, an ``approval_requests`` row, a card in
the conversation (or a channel's buttons and numbered replies), and the
person's answer resumes the turn.

An answer is the person's input, never an approval:

- it resumes only the question's own interrupt (by its id), so an action
  waiting beside it in the same step still asks for its own approval;
- an answer is a mapping, and approvals go ahead only on ``True``;
- the Auto reviewer, "approve the rest" and Hands-off never answer a
  question: it is not a permission, so every level still asks;
- an unattended workflow waits like an approval step; at the step's timeout
  the agent is told to go with its recommended option, and a question with
  none stops the run ("No answer in time").

The answer is validated against the question's own options on the server
(``answer``) and again by the tool when the turn resumes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

QUESTION_TOOL = "ask_person"
QUESTION_KIND = "question"
MAX_QUESTIONS = 4
MIN_OPTIONS = 2
MAX_OPTIONS = 4
HEADER_CHARS = 12
QUESTION_CHARS = 300
LABEL_CHARS = 60
DESCRIPTION_CHARS = 160
OTHER_CHARS = 500
# An unattended workflow waits this long for an answer when its step sets no timeout.
DEFAULT_TIMEOUT_MINUTES = 24 * 60
NO_ANSWER_IN_TIME = "No answer in time"
# A headless run (``row-bot run``) has nobody to answer (Learning reviewed, 23 Nov); the app's lines stay as they are.
NO_PERSON_ANSWER = "No person is available in this run to answer."

_ANSWER_KEY = "row_bot_answer"
_RECOMMENDED = re.compile(r"\s*\(\s*recommended\s*\)\s*$", re.IGNORECASE)


class QuestionError(ValueError):
    """A question or an answer that doesn't fit; ``str()`` says why, in words."""


def _text(value: Any, maximum: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:maximum]


def _chip(header: str) -> str:
    """A header longer than its chip, cut at a word (S8): a long one refused the whole question."""
    if len(header) <= HEADER_CHARS:
        return header
    kept = ""
    for word in header.split():
        if len(f"{kept} {word}".strip()) > HEADER_CHARS:
            break
        kept = f"{kept} {word}".strip()
    return kept or header[:HEADER_CHARS - 1] + "…"


# ── The questions ────────────────────────────────────────────────────────────


def normalize(raw: Any) -> list[dict[str, Any]]:
    """The questions as the card shows them, from the model's input or a stored card.

    Each has a ``header`` chip, the ``question``, ``multi_select`` and 2–4
    ``options`` (``label``, ``description``, ``recommended``). A label the
    model ends with "(Recommended)" is the recommended option, shown with
    its own tag.
    """

    items = list(raw) if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else []
    if not 1 <= len(items) <= MAX_QUESTIONS:
        raise QuestionError(f"Ask 1 to {MAX_QUESTIONS} questions.")
    questions = []
    for item in items:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, Mapping):
            raise QuestionError("Each question needs a header, the question and its options.")
        header = _chip(_text(item.get("header"), 200))
        question = _text(item.get("question"), QUESTION_CHARS + 1)
        if not header or len(header) > HEADER_CHARS:
            raise QuestionError(f"Each question needs a header of at most {HEADER_CHARS} characters.")
        if not question or len(question) > QUESTION_CHARS:
            raise QuestionError(f"Each question needs its question, at most {QUESTION_CHARS} characters.")
        raw_options = item.get("options")
        options_in = list(raw_options) if isinstance(raw_options, Sequence) and not isinstance(
            raw_options, (str, bytes)) else []
        if not MIN_OPTIONS <= len(options_in) <= MAX_OPTIONS:
            raise QuestionError(f"“{header}” needs {MIN_OPTIONS} to {MAX_OPTIONS} options.")
        options = []
        for option in options_in:
            if hasattr(option, "model_dump"):
                option = option.model_dump()
            if not isinstance(option, Mapping):
                raise QuestionError(f"Each option of “{header}” needs a label.")
            label = _text(option.get("label"), 200)
            recommended = bool(option.get("recommended")) or bool(_RECOMMENDED.search(label))
            label = _RECOMMENDED.sub("", label).strip()
            if not label or len(label) > LABEL_CHARS:
                raise QuestionError(f"Each option of “{header}” needs a label of at most {LABEL_CHARS} characters.")
            options.append({"label": label, "description": _text(option.get("description"), DESCRIPTION_CHARS),
                            "recommended": recommended})
        if len({option["label"].casefold() for option in options}) != len(options):
            raise QuestionError(f"The options of “{header}” must differ.")
        if any(option["label"].casefold() in {"other", "other…", "other..."} for option in options):
            raise QuestionError("Don't add an “Other” option: the person can always write their own answer.")
        multi = bool(item.get("multi_select"))
        if not multi:
            # One recommendation for a single choice: the first one marked.
            first = next((index for index, option in enumerate(options) if option["recommended"]), None)
            for index, option in enumerate(options):
                option["recommended"] = index == first
        questions.append({"header": header, "question": question, "multi_select": multi, "options": options})
    return questions


def card(questions: list[dict[str, Any]]) -> dict[str, Any]:
    """The interrupt a question pauses its turn with."""

    return {
        "tool": QUESTION_TOOL,
        "kind": QUESTION_KIND,
        "label": "Row-Bot has a question",
        "description": questions[0]["question"],
        "questions": questions,
    }


def is_question(item: Any) -> bool:
    return isinstance(item, Mapping) and item.get("kind") == QUESTION_KIND and item.get("tool") == QUESTION_TOOL


def question_item(interrupt: Any) -> dict[str, Any] | None:
    """The question an interrupt group asks, if it asks one (its first)."""

    items = interrupt if isinstance(interrupt, list) else [interrupt]
    found = next((item for item in items if is_question(item)), None)
    if found is None:
        return None
    try:
        return {**found, "questions": normalize(found.get("questions"))}
    except QuestionError:
        return None


def question_in_context(context: Any) -> dict[str, Any] | None:
    """The question a stored approval asks: a chat's ``interrupt``, a helper's or a workflow's ``interrupts``."""

    if not isinstance(context, Mapping):
        return None
    return question_item(context.get("interrupt")) or question_item(context.get("interrupts"))


def stored_question(approval_payload_json: Any) -> dict[str, Any] | None:
    """The question of an ``approval_requests`` row's payload, if it is one."""

    if isinstance(approval_payload_json, Mapping):
        return question_in_context(approval_payload_json)
    try:
        return question_in_context(json.loads(str(approval_payload_json or "{}")))
    except (TypeError, ValueError):
        return None


def has_recommendation(item: Mapping[str, Any]) -> bool:
    """Whether every question has a recommended option (an unanswered one can then go on by itself)."""

    return all(any(option["recommended"] for option in question["options"]) for question in item["questions"])


# ── Answers ──────────────────────────────────────────────────────────────────


def answer(item: Mapping[str, Any], answers: Any) -> dict[str, Any]:
    """The resume value for the person's answers, checked against the question's own options.

    ``answers``: one per question, in order, each ``{"choices": [option index, ...], "other": "text"}``.
    A single choice is one option or the person's own words; several may be picked when the question allows.
    """

    questions = item["questions"]
    given = list(answers) if isinstance(answers, Sequence) and not isinstance(answers, (str, bytes)) else []
    if len(given) != len(questions):
        raise QuestionError("Answer every question.")
    checked = []
    for question, entry in zip(questions, given):
        if hasattr(entry, "model_dump"):
            entry = entry.model_dump()
        if not isinstance(entry, Mapping):
            raise QuestionError("Answer every question.")
        raw = entry.get("choices") or []
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise QuestionError("That isn't one of the options.")
        choices = []
        for choice in raw:
            if type(choice) is not int or not 0 <= choice < len(question["options"]) or choice in choices:
                raise QuestionError("That isn't one of the options.")
            choices.append(choice)
        other = str(entry.get("other") or "").strip()
        if len(other) > OTHER_CHARS:
            raise QuestionError(f"Your own answer can be at most {OTHER_CHARS} characters.")
        other = " ".join(other.split())
        if question["multi_select"]:
            if not choices and not other:
                raise QuestionError("Choose at least one option, or write your own answer.")
        elif len(choices) + bool(other) != 1:
            raise QuestionError("Choose one option, or write your own answer.")
        checked.append({"choices": sorted(choices), "other": other})
    return {_ANSWER_KEY: "answered", "answers": checked}


def skipped() -> dict[str, Any]:
    """The person skipped the question: the agent goes on with its recommendation."""

    return {_ANSWER_KEY: "skipped"}


def timed_out() -> dict[str, Any]:
    """Nobody answered an unattended run's question in time."""

    return {_ANSWER_KEY: "timed_out"}


def no_person() -> dict[str, Any]:
    """A headless run has nobody to answer the question."""

    return {_ANSWER_KEY: "no_person"}


def outcome(value: Any) -> str:
    """``answered``, ``skipped``, ``timed_out`` or ``no_person`` for a resume value; anything else counts as
    skipped."""

    state = value.get(_ANSWER_KEY) if isinstance(value, Mapping) else None
    return state if state in {"answered", "skipped", "timed_out", "no_person"} else "skipped"


def resume_values(interrupt: Any, value: Mapping[str, Any]) -> dict[str, Any]:
    """Resume the question's own interrupt only; an action waiting beside it asks again for itself."""

    items = interrupt if isinstance(interrupt, list) else [interrupt]
    found = next((item for item in items if is_question(item) and item.get("__interrupt_id")), None)
    if found is None:
        raise QuestionError("This question can no longer be answered.")
    return {str(found["__interrupt_id"]): dict(value)}


def _chosen(question: Mapping[str, Any], entry: Mapping[str, Any]) -> list[str]:
    return [question["options"][index]["label"] for index in entry["choices"]]


def readable(item: Mapping[str, Any], value: Any) -> list[dict[str, Any]]:
    """Each answered question, as the collapsed card and the channels say it."""

    if outcome(value) != "answered":
        return []
    try:
        checked = answer(item, value.get("answers"))["answers"]
    except QuestionError:
        return []
    result = []
    for question, entry in zip(item["questions"], checked):
        labels = _chosen(question, entry)
        recommended = {option["label"] for option in question["options"] if option["recommended"]}
        words = ", ".join(labels + ([f"“{entry['other']}”"] if entry["other"] else []))
        result.append({"header": question["header"], "answer": words,
                       "recommended": bool(recommended) and not entry["other"] and set(labels) == recommended})
    return result


def _recommendation(item: Mapping[str, Any]) -> str:
    parts = []
    for question in item["questions"]:
        labels = [option["label"] for option in question["options"] if option["recommended"]]
        if labels:
            parts.append(f"{question['header']}: {', '.join(labels)}")
    return "; ".join(parts)


def result(item: Mapping[str, Any], value: Any) -> str:
    """What the agent reads after the person answers, skips or nobody answers in time (JSON)."""

    state = outcome(value)
    shown = readable(item, value)
    if state == "answered" and not shown:
        state = "skipped"  # An answer that doesn't fit its question is never used.
    recommendation = _recommendation(item)
    lines: list[str] = []
    if state == "answered":
        checked = answer(item, value.get("answers"))["answers"]
        for question, entry, said in zip(item["questions"], checked, shown):
            labels = ", ".join(f"“{label}”" for label in _chosen(question, entry))
            other = f"Other: “{entry['other']}”" if entry["other"] else ""
            lines.append(f"{question['header']} → {', '.join(part for part in (labels, other) if part)}"
                         + (" (recommended)" if said["recommended"] else ""))
        display = "You chose: " + (shown[0]["answer"] if len(shown) == 1 else
                                   " · ".join(f"{entry['header']}: {entry['answer']}" for entry in shown))
        next_step = ("These are the person's answers. They are their input, not an approval: actions that need "
                     "approval still ask. Go on with the work using them.")
    elif state == "no_person":
        lines.append(NO_PERSON_ANSWER)
        display = "No person to answer"
        next_step = (f"Go on with your recommended option ({recommendation}) and say briefly that you did."
                     if recommendation else "Go on as you think best and say in a few words what you chose.")
    elif state == "timed_out":
        lines.append("Nobody answered in time.")
        display = "No answer in time"
        next_step = (f"Go on with your recommended option ({recommendation}) and say briefly that you did."
                     if recommendation else "Stop here and say that nobody answered in time.")
    else:
        lines.append("The person skipped the question.")
        display = "You skipped the question"
        next_step = (f"Go on with your recommended option ({recommendation}) and say in a few words that you did."
                     if recommendation else "Go on as you think best and say in a few words what you chose.")
    return json.dumps({
        "ok": True,
        "kind": "question_answered",
        "outcome": state,
        "answers": shown if state == "answered" else [],
        "summary": "\n".join(lines),
        "display_summary": display,
        "next": next_step,
    }, ensure_ascii=False)


# ── The tool ─────────────────────────────────────────────────────────────────


DESCRIPTION = (
    "Ask the person 1-4 multiple-choice questions (they may also write their own answer or skip). "
    "Whenever you would ask them to choose, ask here, never as questions in your reply. "
    "Only for their decisions that change what you do next; always recommend one: list it first, label "
    "ending \" (Recommended)\". Never to confirm actions (approvals do) or for what you can find out. "
    "Answers are input, not approval; if skipped, use your recommendation."
)


class _Option(BaseModel):
    label: str
    description: str = ""  # one line


class _Question(BaseModel):
    header: str = Field(description=f"Chip, max {HEADER_CHARS} chars.")
    question: str
    options: list[_Option] = Field(description=f"{MIN_OPTIONS}-{MAX_OPTIONS}, recommended first, no \"Other\".")
    multi_select: bool = False


class _AskInput(BaseModel):
    questions: list[_Question]


def ask_person(questions: list[Any]) -> str:
    """Pause the turn on a question card and return the person's answer."""

    try:
        item = card(normalize(questions))
    except QuestionError as error:
        return json.dumps({"ok": False, "error": f"{error} Fix the questions and call ask_person again."},
                          ensure_ascii=False)
    from langgraph.types import interrupt

    # The tool runs again from the top when the turn resumes; its own interrupt then returns the answer.
    return result(item, interrupt(item))


def build_tool() -> StructuredTool:
    """``ask_person``, bound with every Agent tool set."""

    return StructuredTool.from_function(func=ask_person, name=QUESTION_TOOL, description=DESCRIPTION,
                                        args_schema=_AskInput)
