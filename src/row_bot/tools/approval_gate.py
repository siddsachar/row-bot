"""One place where an action that needs approval gets it, by the approval level.

- **Ask**: the person approves it on the approval card (a LangGraph interrupt).
- **Auto**: the reviewer (``approval_review``) decides first; it approves what
  clearly matches the person's request and otherwise asks the person, with its
  reason on the card.
- **Hands-off**: approved without asking, and recorded.

Every decision is recorded on the action's result as its approval line (B235),
so the conversation's activity shows "Approved by the reviewer: …" or "Allowed
by Hands-off". Tools that cannot lead their own result with it get it from
the agent's tool wrapper (``collect_decisions`` / ``with_decisions``).
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from collections.abc import Iterable, Iterator
from typing import Any, Callable, Literal

from row_bot.approval_policy import decision_for_action, normalize_approval_mode

logger = logging.getLogger(__name__)

GateOutcome = Literal["allow", "block", "deny", "take_over"]

# The first line of every approval-gated result, so the model can say whether
# approval was needed, given or refused instead of guessing (B235).
APPROVAL_NOT_NEEDED_SAFE = "Approval: not needed (safe command)"
APPROVAL_NOT_NEEDED_READ_ONLY = "Approval: not needed (read-only)"
APPROVAL_NOT_NEEDED_BY_PERSON = "Approval: not needed (you started it)"
APPROVAL_GIVEN = "Approval: asked; approved by you"
APPROVAL_DENIED = "Approval: asked; denied by you — did not run"
APPROVAL_FULL_AUTONOMY = "Approval: allowed by Hands-off"
_APPROVAL_PREFIX = "Approval: "


def approved_by_reviewer(reason: str) -> str:
    """The approval line of an action the reviewer approved under Auto."""

    return f"Approval: approved by the reviewer: {_reason(reason)}"


def after_review(line: str, reason: str) -> str:
    """The person's answer after the reviewer asked them, with the reviewer's reason."""

    return f"{line}. The reviewer asked you: {_reason(reason)}"


def _reason(reason: str) -> str:
    text = " ".join(str(reason or "").split()).rstrip(".")
    return text or "no reason given"


# ── Recording decisions on the result ────────────────────────────────────────

_decisions_var: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "approval_decisions", default=None
)


@contextlib.contextmanager
def collect_decisions() -> Iterator[list[str]]:
    """Collect the approval lines decided while one tool call runs."""

    decisions: list[str] = []
    token = _decisions_var.set(decisions)
    try:
        yield decisions
    finally:
        _decisions_var.reset(token)


def _record(line: str) -> None:
    decisions = _decisions_var.get()
    if decisions is not None and line:
        decisions.append(line)


def with_decisions(decisions: Iterable[str], result: Any) -> Any:
    """Lead a plain-text result with the approval lines decided for it, unless it has its own."""

    lines = [line[len(_APPROVAL_PREFIX):] if line.startswith(_APPROVAL_PREFIX) else line
             for line in decisions if line]
    if not lines or (isinstance(result, str) and result.startswith(_APPROVAL_PREFIX)):
        return result
    return with_approval(_APPROVAL_PREFIX + "; ".join(dict.fromkeys(lines)), result)


def with_approval(line: str, result: Any) -> Any:
    """Lead a plain-text result with its approval line.

    Structured results (JSON, media markers) are left as they are: the
    transcript reads them as data.
    """

    if not isinstance(result, str) or result.lstrip().startswith(("{", "[", "__")):
        return result
    return f"{line}\n{result}"


def current_approval_mode() -> str:
    """Return the active thread approval level, defaulting to Ask."""

    try:
        from row_bot.agent import get_approval_mode

        return get_approval_mode()
    except Exception:
        from row_bot.approval_policy import DEFAULT_APPROVAL_MODE

        return DEFAULT_APPROVAL_MODE


# ── Each answer bound to its question ────────────────────────────────────────
#
# A LangGraph tool that asks runs again from the start once the person answers,
# and LangGraph hands answers back by position: the first ``interrupt()`` of the
# re-run gets the first answer. Under Auto the reviewer approves some questions
# without a card, and a tool may not ask again what it already has (an app
# already allowed, a tool group already on), so on a re-run positions alone
# would put answers on the wrong questions. So every decision about a question
# is kept in the tool call's answer list under the question's id, in the order
# it was made: the person's answer always sits right after the record of the
# question it answers, and a question uses only its own record and answer. A
# re-run replays the reviewer's verdict instead of asking it again; a question
# with no record of its own is decided afresh.

_RECORD_KEY = "row_bot_approval_question"


def question_id(payload: dict[str, Any]) -> str:
    """A stable id for one approval question: the tool, and what its card says."""

    import hashlib
    import json

    raw = json.dumps([str(payload.get("tool") or ""), str(payload.get("label") or ""),
                      str(payload.get("description") or "")], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


def _is_record(value: Any) -> bool:
    return isinstance(value, dict) and _RECORD_KEY in value


def _asked_the_person(record: Any) -> bool:
    return _is_record(record) and record.get("review") != "approve"


class _Ledger:
    """One tool call's answer list as LangGraph keeps it, read the way ``interrupt()`` reads it."""

    def __init__(self, scratchpad: Any, send: Callable[[list[tuple[str, Any]]], None], resume_key: str) -> None:
        self._scratchpad = scratchpad
        self._send = send
        self._resume_key = resume_key

    @property
    def kept(self) -> list[Any]:
        return self._scratchpad.resume

    def _keep(self) -> None:
        self._send([(self._resume_key, self._scratchpad.resume)])

    def advance(self) -> int:
        """Take the next place in the list (the one the next ``interrupt()`` would have read)."""

        index = self._scratchpad.interrupt_counter()
        self._keep()
        return index

    def append(self, entry: Any) -> None:
        self._scratchpad.resume.append(entry)
        self._keep()

    def take_waiting_answer(self) -> Any:
        """Take the answer given since the last pause, if no card in this run has taken it yet (else None)."""

        return self._scratchpad.get_null_resume(True)


_UNKNOWN_LEDGER = object()


def _ledger() -> _Ledger | object | None:
    """This tool call's ledger; None outside a graph task; ``_UNKNOWN_LEDGER`` if LangGraph changed."""

    try:
        from langgraph._internal._constants import CONFIG_KEY_SCRATCHPAD, CONFIG_KEY_SEND, RESUME
        from langgraph.config import get_config
    except Exception:
        return _UNKNOWN_LEDGER
    try:
        configurable = get_config().get("configurable") or {}
    except RuntimeError:
        return None  # Not inside a run: nothing can be waiting.
    scratchpad = configurable.get(CONFIG_KEY_SCRATCHPAD)
    if scratchpad is None:
        return None  # Not a graph task (a tool called on its own): nothing to resume.
    send = configurable.get(CONFIG_KEY_SEND)
    if send is None or not all(hasattr(scratchpad, name) for name in
                               ("interrupt_counter", "resume", "get_null_resume")):
        return _UNKNOWN_LEDGER
    return _Ledger(scratchpad, send, RESUME)


# ── The decision ─────────────────────────────────────────────────────────────


def _interrupt(payload: dict[str, Any]) -> Any:
    from langgraph.types import interrupt

    return interrupt(payload)


def _person_line(mode: str, response: Any, reason: str) -> str:
    """The line for the person's answer: empty under Ask (callers keep their own), with the reviewer's reason."""

    if mode != "auto":
        return ""
    line = APPROVAL_GIVEN if response is True else APPROVAL_DENIED
    return after_review(line, reason) if reason else line


def _with_review(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    return {**payload, "review": {"decision": "ask", "reason": reason}} if reason else payload


def _decide(
    payload: dict[str, Any],
    *,
    approval_mode: object,
    risks: Iterable[str] = (),
    approval_callback: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[Any, str]:
    """(the answer, its approval line) for an action that needs approval under the level.

    The answer is True when the level gave it; otherwise the person's own
    (True, False or "take over"). The line is empty when the person answered
    under Ask (callers keep their own lines for that).
    """

    mode = normalize_approval_mode(approval_mode)
    if mode == "full":
        return True, APPROVAL_FULL_AUTONOMY
    ask = approval_callback or _interrupt
    ledger = _ledger()
    if ledger is _UNKNOWN_LEDGER:
        # LangGraph changed how it keeps answers: the person decides every question, in order, so
        # none can take another's answer.
        logger.warning("Approval answers can't be bound to their questions here; asking the person")
        response = ask(payload)
        return response, _person_line(mode, response, "")
    if isinstance(ledger, _Ledger):
        return _decide_in_graph(payload, mode=mode, risks=risks, ask=ask, ledger=ledger)
    return _decide_afresh(payload, mode=mode, risks=risks, ask=ask, ledger=None)


def _decide_in_graph(payload: dict[str, Any], *, mode: str, risks: Iterable[str],
                     ask: Callable[[dict[str, Any]], Any], ledger: _Ledger) -> tuple[Any, str]:
    """Decide one question of a graph task by its own record, if it has one; else afresh."""

    question = question_id(payload)
    kept = ledger.kept
    index = ledger.advance()
    while index < len(kept):
        entry = kept[index]
        if _is_record(entry) and entry.get(_RECORD_KEY) == question:
            reason = str(entry.get("reason") or "")
            if entry.get("review") == "approve":
                if mode == "auto":
                    return True, approved_by_reviewer(reason)  # The reviewer's verdict, never asked again.
                # The level is stricter now than when the reviewer approved it: ask afresh.
            elif index + 1 >= len(kept) or not _is_record(kept[index + 1]):
                # The person was asked: their answer is the next entry (or waiting), and is this question's.
                response = ask(_with_review(payload, reason))
                if _is_record(response):  # Never another question's record taken as an answer.
                    response = False
                return response, _person_line(mode, response, reason)
        # Another question's entry (one this run didn't ask again), or an answer kept for none: pass it.
        index = ledger.advance()
    if index != len(kept):
        # Can't happen while every card reads the list in order; if it does, no kept or waiting answer
        # is used: the question is decided afresh, without a record.
        logger.warning("Approval answers are out of step with this tool call; asking afresh")
        ledger.take_waiting_answer()
        return _decide_afresh(payload, mode=mode, risks=risks, ask=ask, ledger=None)
    if kept and _asked_the_person(kept[-1]):
        # The run paused at that question and hasn't reached it this time: the answer given since is
        # that question's own, so it goes right after it, where that question will find it.
        waiting = ledger.take_waiting_answer()
        if waiting is not None:
            ledger.append(waiting)
            index = ledger.advance()
    elif ledger.take_waiting_answer() is not None:
        # An answer given to a card this run no longer shows (or from before answers were bound to
        # their questions): it isn't this question's, so it is set aside and this question is asked.
        logger.info("An answer for a card no longer shown was set aside; asking this question afresh")
    return _decide_afresh(payload, mode=mode, risks=risks, ask=ask, ledger=ledger)


def _decide_afresh(payload: dict[str, Any], *, mode: str, risks: Iterable[str], ask: Callable[[dict[str, Any]], Any],
                   ledger: _Ledger | None) -> tuple[Any, str]:
    """Decide a question nobody has decided yet: the reviewer first under Auto, then the person."""

    reason = ""
    if mode == "auto":
        from row_bot.approval_review import review

        verdict = review(payload, risks=risks)
        reason = verdict.reason
        if ledger is not None:
            ledger.append({_RECORD_KEY: question_id(payload), "review": "approve" if verdict.approved else "ask",
                           "reason": reason})
        if verdict.approved:
            return True, approved_by_reviewer(reason)
    elif ledger is not None:
        ledger.append({_RECORD_KEY: question_id(payload), "review": None, "reason": ""})
    response = ask(_with_review(payload, reason))
    return response, _person_line(mode, response, reason)


def request_approval(
    payload: dict[str, Any],
    *,
    approval_mode: object = None,
    risks: Iterable[str] = (),
    approval_callback: Callable[[dict[str, Any]], Any] | None = None,
) -> Any:
    """Get the approval an action needs, by the level; the answer, as ``interrupt()`` returns it.

    For callers that already know the action needs approval (a Developer push,
    a settings change). True when Hands-off or the reviewer gave it, else
    the person's answer. The decision is recorded on the tool's result.
    ``risks``: the risk flags the caller already worked out, in plain words,
    for the reviewer.
    """

    mode = current_approval_mode() if approval_mode is None else approval_mode
    response, line = _decide(payload, approval_mode=mode, risks=risks, approval_callback=approval_callback)
    _record(line)
    _note_for_learning(payload, mode, response, line)
    return response


def _note_for_learning(payload: dict[str, Any], mode: object, response: Any, line: str, *,
                       blocked: bool = False) -> None:
    """Learning (preview) reads the decision made here; it never takes part in it (E8).

    Off: one cached check, nothing else imported.
    """

    from row_bot import learning

    if not learning.is_enabled():
        return
    try:
        from row_bot.agent import note_learning_approval

        note_learning_approval(payload, mode, response, line, blocked=blocked)
    except Exception as exc:
        logger.debug("Learning did not see an approval decision (%s)", type(exc).__name__)


def _outcome(response: Any) -> GateOutcome:
    if response is True:
        return "allow"
    if str(response or "").strip().casefold() == "take over":
        return "take_over"
    return "deny"


def resolve_approval_line(
    payload: dict[str, Any],
    *,
    approval_mode: object,
    read_only: bool = False,
    risks: Iterable[str] = (),
    approval_callback: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[GateOutcome, str]:
    """Resolve one action through the shared levels: its outcome and approval line."""

    decision = decision_for_action(normalize_approval_mode(approval_mode), read_only=read_only)
    if decision == "allow":
        return "allow", APPROVAL_NOT_NEEDED_READ_ONLY
    if decision == "block":
        _note_for_learning(payload, approval_mode, False, "", blocked=True)
        return "block", ""
    response, line = _decide(payload, approval_mode=approval_mode, risks=risks,
                             approval_callback=approval_callback)
    outcome = _outcome(response)
    if not line:
        line = APPROVAL_GIVEN if outcome == "allow" else APPROVAL_DENIED
    _record(line)
    _note_for_learning(payload, approval_mode, response, line)
    return outcome, line


def resolve_approval(
    payload: dict[str, Any],
    *,
    approval_mode: object,
    read_only: bool = False,
    risks: Iterable[str] = (),
    approval_callback: Callable[[dict[str, Any]], bool | str] | None = None,
) -> GateOutcome:
    """Resolve one action through the shared Block/Ask/Auto/Hands-off levels.

    The optional callback keeps deterministic subsystem tests independent from
    LangGraph. Shipped callers omit it and use the normal graph interrupt.
    """

    outcome, _line = resolve_approval_line(
        payload,
        approval_mode=approval_mode,
        read_only=read_only,
        risks=risks,
        approval_callback=approval_callback,
    )
    return outcome


def approval_check(
    payload: dict[str, Any],
    *,
    read_only: bool = False,
    risks: Iterable[str] = (),
    blocked_message: str | None = None,
    cancelled_message: str = "Action cancelled by user.",
) -> tuple[str | None, str]:
    """Resolve one action: its refusal (None when it may run) and approval line.

    A tool that runs the action leads its result with the line; a refusal
    after a denial already carries it.
    """

    mode = current_approval_mode()
    decision, line = resolve_approval_line(
        payload,
        approval_mode=mode,
        read_only=read_only,
        risks=risks,
    )
    if decision == "allow":
        return None, line
    label = str(payload.get("label") or payload.get("tool") or "Action")
    if decision == "block":
        return blocked_message or (
            f"BLOCKED: {label} is unavailable while this thread is in Block approval mode."
        ), ""
    return with_approval(line, cancelled_message), line


def gate_action(
    payload: dict[str, Any],
    *,
    read_only: bool = False,
    risks: Iterable[str] = (),
    blocked_message: str | None = None,
    cancelled_message: str = "Action cancelled by user.",
) -> str | None:
    """Return None when the action may run, otherwise a user-facing refusal."""

    refusal, _line = approval_check(
        payload,
        read_only=read_only,
        risks=risks,
        blocked_message=blocked_message,
        cancelled_message=cancelled_message,
    )
    return refusal
