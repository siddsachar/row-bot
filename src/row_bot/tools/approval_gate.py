"""One place where an action that needs approval gets it, by the approval level.

- **Ask**: the person approves it on the approval card (a LangGraph interrupt).
- **Auto**: the reviewer (``approval_review``) decides first; it approves what
  clearly matches the person's request and otherwise asks the person, with its
  reason on the card.
- **Hands-off**: approved without asking, and recorded.

Some risks are the person's own under Auto, whatever the reviewer would say:
setting Git's identity, posting on X in their name (N7), and a calendar change
that invites or tells other people, its guests (W21).

A workflow's "Always ask me" rule (N9) makes an action the person's own to
approve under every level: the reviewer never decides it, Hands-off never skips
it, and the approval is bound to the exact arguments it was given for
(``person_approval``).

Every decision is recorded on the action's result as its approval line (B235),
so the conversation's activity shows "Approved by the reviewer: …" or "Allowed
by Hands-off". Tools that cannot lead their own result with it get it from
the agent's tool wrapper (``collect_decisions`` / ``with_decisions``).

Each decision is also noted, as data, for the activity log (D1): who decided
(the person, the reviewer, Hands-off, Block) and why. The tool-call middleware
collects the notes (``collect_activity``) and writes one row per call; noting
never changes a decision, a result or what Learning sees.

Under Ask and Auto, a question nobody has decided yet is first checked against
the person's own allowances, before the reviewer: one of their standing
"Always allow" rules (``approval_rules``), then what they allowed for the rest
of the chat or turn ("Allow in this chat", ``approval_grants``). Neither ever
covers an action only the person approves, and a rule never applies in a
conversation other people share. Neither is kept in the call's answer list, so
an allowance switched off while the call waits is decided afresh.
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
APPROVAL_CHANGED = "Approval: asked; what you approved has changed since — did not run"
APPROVAL_ALLOWED_IN_CHAT = "Approval: allowed by you for this chat"
# The person's company allows no action that needs an approval (its ceiling is Block, C3b).
APPROVAL_COMPANY_BLOCKED = "Approval: needed; your company's rules don't allow it — did not run"
_APPROVAL_PREFIX = "Approval: "
# Why a person-only card asks: the person's own rule, set on purpose.
PERSON_RULE_REASON = "You asked to approve this yourself."
# An incognito chat's memory write (N11): only the person saves from it, under every level.
INCOGNITO_MEMORY_QUESTION = "Save to memory? This chat is incognito."
INCOGNITO_MEMORY_REASON = "This chat is incognito: only you can save from it to memory."
# A reply Proactive Row-Bot drafted in the background: its words weren't the person's, so only they send it,
# under every level (Hands-off included).
BACKGROUND_DRAFT_REASON = "Row-Bot wrote this reply on its own, so only you can send it."
# A headless run (``row-bot run``) has nobody to ask: what the person would have been asked about is refused, and
# the action's whole result says so (Learning reviewed, 23 Nov). The app's own lines never change.
NO_PERSON_REFUSAL = "Approval: needed; no person is available to approve it in this run — did not run"
_nobody_to_ask = False


@contextlib.contextmanager
def nobody_to_ask() -> Iterator[None]:
    """While a headless run answers every approval with no: each refused action's result is
    :data:`NO_PERSON_REFUSAL` (``refused_for_nobody``)."""

    global _nobody_to_ask
    earlier, _nobody_to_ask = _nobody_to_ask, True
    try:
        yield
    finally:
        _nobody_to_ask = earlier


def refused_for_nobody(notes: Iterable[dict[str, Any]]) -> bool:
    """Whether one tool call's decisions (``collect_activity``) refused what nobody was there to approve."""

    return _nobody_to_ask and any(note.get("stage") == "decided" and note.get("by") == "person"
                                  and note.get("outcome") in {"deny", "changed"} for note in notes)



def approved_by_reviewer(reason: str) -> str:
    """The approval line of an action the reviewer approved under Auto."""

    return f"Approval: approved by the reviewer: {_reason(reason)}"


def allowed_by_rule(words: str) -> str:
    """The approval line of an action one of the person's standing rules allowed."""

    return f"Approval: allowed by your rule: {_reason(words)}"


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


# ── Noting decisions for the activity log (D1) ───────────────────────────────

_activity_var: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "approval_activity", default=None
)
# Who made the decision ``_decide`` just returned, why, and the rule when one did:
# ("person" | "reviewer" | "hands_off" | "rule" | "chat_grant", reason, rule id).
_decider_var: contextvars.ContextVar[tuple[str, str, str]] = contextvars.ContextVar(
    "approval_decider", default=("", "", ""))
# The action ``_decide`` is deciding, as a standing rule would cover it (None when none may): for its
# allowance, its card and its activity row.
_scope_var: contextvars.ContextVar[Any] = contextvars.ContextVar("approval_rule_scope", default=None)


@contextlib.contextmanager
def collect_activity() -> Iterator[list[dict[str, Any]]]:
    """Collect, for the activity log, the decisions made while one tool call runs (the tool-call middleware)."""

    notes: list[dict[str, Any]] = []
    token = _activity_var.set(notes)
    try:
        yield notes
    finally:
        _activity_var.reset(token)


def _activity_note(stage: str, payload: dict[str, Any], mode: object, **fields: Any) -> None:
    notes = _activity_var.get()
    if notes is None:
        return
    try:
        notes.append({
            "stage": stage,
            "level": normalize_approval_mode(mode),
            "tool": str(payload.get("tool") or ""),
            "label": str(payload.get("label") or "")[:300],
            "description": str(payload.get("description") or "")[:600],
            "risk": str(payload.get("risk_class") or payload.get("risk") or "unknown"),
            "person_only": bool(payload.get("person_only")),
            **(scope.note() if (scope := _scope_var.get()) is not None else {}),
            **fields,
        })
    except Exception as exc:  # Noting never fails the action.
        logger.debug("The activity log did not see an approval decision (%s)", type(exc).__name__)


def _decided_by(who: str, reason: str = "", rule_id: str = "") -> None:
    _decider_var.set((who, str(reason or ""), str(rule_id or "")))


def decided_by() -> str:
    """Who gave the approval just decided: "person", "reviewer" (Auto), "hands_off", "rule" (the person's standing
    rule) or "chat_grant" (their allowance for this chat); "" when nobody did."""

    return _decider_var.get()[0]


def _asking(payload: dict[str, Any], mode: object, reason: str) -> None:
    """The person is about to be asked: if the call pauses here, its row says "Waiting for you"."""

    _activity_note("asking", payload, mode, reason=str(reason or ""))
    _decided_by("person", reason)


def _note_for_activity(payload: dict[str, Any], mode: object, outcome: str, *, by: str = "") -> None:
    """The decision just made, for the activity log; never part of it (like ``_note_for_learning``)."""

    who, reason, rule_id = _decider_var.get()
    _activity_note("decided", payload, mode, outcome=str(outcome), by=by or who or "person", reason=reason,
                   **({"rule_id": rule_id} if rule_id else {}))


def _level(mode: object) -> str:
    """A level as it runs: normalised, and at most the person's company's ceiling (C3b)."""

    from row_bot.company.enforce import cap

    return cap(normalize_approval_mode(mode))


def _capped(mode: object) -> object:
    """``mode`` as given, unless the person's company's ceiling lowers it (C3b): then the ceiling."""

    level = _level(mode)
    return mode if level == normalize_approval_mode(mode) else level


def _standing_allowed() -> bool:
    """Whether the person's own allowances may approve for them: not below a company ceiling of Auto (C3b)."""

    from row_bot.company.enforce import allows_standing_approvals

    return allows_standing_approvals()


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
    """A stable id for one approval question: the tool, and what its card says.

    A question bound to its arguments (``binding``) is another question when they change.
    """

    import hashlib
    import json

    parts = [str(payload.get("tool") or ""), str(payload.get("label") or ""),
             str(payload.get("description") or "")]
    if payload.get("binding"):
        parts.append(str(payload["binding"]))
    raw = json.dumps(parts, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


# ── The person's own approval, bound to its arguments ────────────────────────


def call_binding(tool: str, arguments: Any, media: Iterable[str] = ()) -> str:
    """The hash an approval is bound to: the tool, its exact arguments and its media's contents."""

    import hashlib
    import json

    raw = json.dumps([str(tool or ""), arguments, sorted(str(item) for item in media)],
                     sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def bound_answer(approved: bool, binding: str) -> dict[str, Any]:
    """The answer a person's decision resumes a bound question with."""

    return {"approved": bool(approved), "binding": str(binding)}


def _bound_outcome(response: Any, binding: str) -> str:
    """'allow' only for the person's approval of exactly this binding; 'changed' for an approval of
    other details; else 'deny' (a plain True never counts: nobody but the person answers these)."""

    if not isinstance(response, dict) or response.get("approved") is not True or not response.get("binding"):
        return "deny"
    return "allow" if response["binding"] == binding else "changed"


_approved_call_var: contextvars.ContextVar[str] = contextvars.ContextVar("approved_call", default="")


@contextlib.contextmanager
def approved_by_person(binding: str) -> Iterator[None]:
    """While one call the person approved runs: what it asks inside is already approved by them."""

    token = _approved_call_var.set(str(binding))
    try:
        yield
    finally:
        _approved_call_var.reset(token)


def person_approval(
    payload: dict[str, Any],
    *,
    binding: str,
    reason: str = PERSON_RULE_REASON,
    approval_callback: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[bool, str]:
    """Ask the person themselves, whatever the level: (approved, its approval line).

    The reviewer never decides it and Hands-off never skips it. It is approved only by
    the person's answer for exactly *binding*; an answer for other details never
    runs it, and changed arguments are a new question, asked again.
    """

    payload = {**payload, "person_only": True, "binding": str(binding)}
    response, _line = _decide(payload, approval_mode=current_approval_mode(), approval_callback=approval_callback,
                              person_only=reason)
    outcome = _bound_outcome(response, binding)
    line = {"allow": APPROVAL_GIVEN, "changed": APPROVAL_CHANGED}.get(outcome, APPROVAL_DENIED)
    _record(line)
    _note_for_activity(payload, current_approval_mode(), outcome)
    return outcome == "allow", line


def asked_alone(interrupt: Any) -> dict[str, Any] | None:
    """The card a step's waiting approvals ask first and by itself: the first only the person answers.

    Such an answer is bound to exactly what its card showed, so it never shares a card with another
    action: two memory writes of an incognito chat (N11) made in one step are two cards, one after
    the other (B412). What waits beside it asks again, each for itself, once it is answered.
    """
    items = interrupt if isinstance(interrupt, list) else [interrupt]
    return next((item for item in items
                 if isinstance(item, dict) and item.get("person_only") and item.get("binding")), None)


def _is_record(value: Any) -> bool:
    return isinstance(value, dict) and _RECORD_KEY in value


def _has_record(ledger: "_Ledger", payload: dict[str, Any]) -> bool:
    """Whether this question was already decided once in this tool call (a reviewer's verdict, a card)."""

    question = question_id(payload)
    return any(_is_record(entry) and entry.get(_RECORD_KEY) == question for entry in ledger.kept)


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
    """The card the person is asked with: the reviewer's reason, and the scope a rule from it would cover."""

    card = {**payload, "review": {"decision": "ask", "reason": reason}} if reason else payload
    scope = _scope_var.get()
    return {**card, "always_allow": scope.card()} if scope is not None else card


def _rule_scope(payload: dict[str, Any], risks: Iterable[str], person_only: str) -> Any:
    """The action as a standing rule would cover it, or None (never an error: then no rule applies)."""

    try:
        from row_bot import approval_rules

        return approval_rules.candidate(payload, risks, person_only=bool(person_only))
    except Exception as exc:
        logger.debug("No rule scope for this action (%s)", type(exc).__name__)
        return None


def _conversations_here() -> list[str]:
    """This conversation, and the ones behind it when this is a helper agent's: where a chat grant counts."""

    try:
        from row_bot.approval_rules import conversations_here

        return conversations_here()
    except Exception as exc:
        logger.debug("No conversation for a chat grant (%s)", type(exc).__name__)
        return []


def _allowance(payload: dict[str, Any], mode: str, risks: tuple[str, ...]) -> str:
    """The approval line when one of the person's own allowances covers this action under Ask or Auto, else ''.

    Their standing rule first, then what they allowed for the rest of this chat or turn. Never an action only
    the person approves: the gate's caller has already ruled those out, and their risks are checked here.
    """

    if mode not in {"approve", "auto"} or payload.get("person_only") or any(risk in _PERSON_ONLY_RISKS
                                                                             for risk in risks):
        return ""
    if not _standing_allowed():
        return ""  # Below a company ceiling of Auto every action is asked (C3b).
    scope = _scope_var.get()
    if scope is not None:
        from row_bot import approval_rules

        rule = approval_rules.match(scope)  # Fails closed: an unreadable store matches nothing.
        if rule is not None:
            _decided_by("rule", "", rule.rule_id)
            approval_rules.note_used(rule.rule_id)
            return allowed_by_rule(rule.words)
    try:
        from row_bot.application import approval_grants

        if any(approval_grants.granted(conversation, payload) for conversation in _conversations_here()):
            _decided_by("chat_grant")
            return APPROVAL_ALLOWED_IN_CHAT
    except Exception as exc:
        logger.debug("Chat grants unavailable; asking (%s)", type(exc).__name__)
    return ""


def _decide(
    payload: dict[str, Any],
    *,
    approval_mode: object,
    risks: Iterable[str] = (),
    approval_callback: Callable[[dict[str, Any]], Any] | None = None,
    person_only: str = "",
) -> tuple[Any, str]:
    """(the answer, its approval line) for an action that needs approval under the level.

    The answer is True when the level gave it; otherwise the person's own
    (True, False or "take over"). The line is empty when the person answered
    under Ask (callers keep their own lines for that). *person_only*: why only
    the person may answer (their own rule); no level answers it for them.
    """

    mode = _level(approval_mode)
    _decided_by("")
    _scope_var.set(None)
    if mode == "block":
        from row_bot.company.enforce import ceiling_blocks

        if ceiling_blocks():
            # The company's ceiling is Block: nobody approves it, not even the person (C3b).
            _decided_by("block")
            return False, APPROVAL_COMPANY_BLOCKED
    if not person_only and _approved_call_var.get():
        _decided_by("person")
        return True, APPROVAL_GIVEN  # Inside a call the person approved with its exact details.
    if mode == "full" and not person_only:
        _decided_by("hands_off")
        return True, APPROVAL_FULL_AUTONOMY
    risks = tuple(risks)
    if mode in {"approve", "auto"} and _standing_allowed():
        _scope_var.set(_rule_scope(payload, risks, person_only))
    ask = approval_callback or _interrupt
    ledger = _ledger()
    if ledger is _UNKNOWN_LEDGER:
        # LangGraph changed how it keeps answers: the person decides every question, in order, so
        # none can take another's answer.
        logger.warning("Approval answers can't be bound to their questions here; asking the person")
        _asking(payload, mode, person_only)
        response = ask(_with_review(payload, person_only))
        return response, _person_line(mode, response, person_only)
    if not person_only and not (isinstance(ledger, _Ledger) and _has_record(ledger, payload)):
        # The person's own allowance, before the reviewer. Like Hands-off it is never kept in the answer list,
        # so one switched off while the call waits is decided afresh; and a question already put to the
        # reviewer or the person keeps that answer.
        line = _allowance(payload, mode, risks)
        if line:
            return True, line
    if isinstance(ledger, _Ledger):
        return _decide_in_graph(payload, mode=mode, risks=risks, ask=ask, ledger=ledger, person_only=person_only)
    return _decide_afresh(payload, mode=mode, risks=risks, ask=ask, ledger=None, person_only=person_only)


def _decide_in_graph(payload: dict[str, Any], *, mode: str, risks: Iterable[str],
                     ask: Callable[[dict[str, Any]], Any], ledger: _Ledger,
                     person_only: str = "") -> tuple[Any, str]:
    """Decide one question of a graph task by its own record, if it has one; else afresh."""

    question = question_id(payload)
    kept = ledger.kept
    index = ledger.advance()
    while index < len(kept):
        entry = kept[index]
        if _is_record(entry) and entry.get(_RECORD_KEY) == question:
            reason = str(entry.get("reason") or "")
            if entry.get("review") == "approve":
                if mode == "auto" and not person_only:
                    _decided_by("reviewer", reason)
                    return True, approved_by_reviewer(reason)  # The reviewer's verdict, never asked again.
                # The level is stricter now than when the reviewer approved it: ask afresh.
            elif index + 1 >= len(kept) or not _is_record(kept[index + 1]):
                # The person was asked: their answer is the next entry (or waiting), and is this question's.
                _asking(payload, mode, reason)
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
        return _decide_afresh(payload, mode=mode, risks=risks, ask=ask, ledger=None, person_only=person_only)
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
    return _decide_afresh(payload, mode=mode, risks=risks, ask=ask, ledger=ledger, person_only=person_only)


GIT_IDENTITY_RISK = "sets the name and email Git records on commits, which only the person can choose"
X_POST_RISK = "posts, replies, quotes or deletes on X as one of the person's accounts, which only the person approves"
SYSTEM_INSTALL_RISK = "installs software on this computer for everyone, outside Row-Bot, which only the person decides"
SECURITY_CAPABILITY_RISK = ("turns on a tool that acts on this computer (commands, files, the browser, the screen or "
                            "MCP apps), which only the person decides")
CALENDAR_GUESTS_RISK = ("invites other people to an event, or tells its guests of a change or cancellation, "
                        "which only the person approves")
# Risks the Auto reviewer never accepts for the person: they go straight to the person's card.
_PERSON_ONLY_RISKS = {GIT_IDENTITY_RISK: "Only you can choose the name and email your commits are from.",
                      X_POST_RISK: "Only you can approve what goes out on X in your name.",
                      SYSTEM_INSTALL_RISK: "Only you can decide what software is installed on this computer.",
                      SECURITY_CAPABILITY_RISK: "Only you can turn on a tool that acts on this computer.",
                      CALENDAR_GUESTS_RISK: "Only you can approve what your calendar sends to other people."}


def _decide_afresh(payload: dict[str, Any], *, mode: str, risks: Iterable[str], ask: Callable[[dict[str, Any]], Any],
                   ledger: _Ledger | None, person_only: str = "") -> tuple[Any, str]:
    """Decide a question nobody has decided yet: the reviewer first under Auto, then the person.

    A person-only question (the person's own rule) goes straight to the person under every level.
    """

    reason = ""
    risks = tuple(risks)
    if not person_only and mode == "auto":
        person_only = next((_PERSON_ONLY_RISKS[risk] for risk in risks if risk in _PERSON_ONLY_RISKS), "")
    if person_only:
        reason = person_only
        if ledger is not None:
            ledger.append({_RECORD_KEY: question_id(payload), "review": "ask", "reason": reason})
    elif mode == "auto":
        from row_bot.approval_review import review

        verdict = review(payload, risks=risks)
        reason = verdict.reason
        if ledger is not None:
            ledger.append({_RECORD_KEY: question_id(payload), "review": "approve" if verdict.approved else "ask",
                           "reason": reason})
        if verdict.approved:
            _decided_by("reviewer", reason)
            return True, approved_by_reviewer(reason)
    elif ledger is not None:
        ledger.append({_RECORD_KEY: question_id(payload), "review": None, "reason": ""})
    _asking(payload, mode, reason)
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

    mode = current_approval_mode() if approval_mode is None else _capped(approval_mode)
    response, line = _decide(payload, approval_mode=mode, risks=risks, approval_callback=approval_callback)
    _record(line)
    _note_for_learning(payload, mode, response, line)
    _note_for_activity(payload, mode, _outcome(response))
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

    approval_mode = _capped(approval_mode)
    decision = decision_for_action(normalize_approval_mode(approval_mode), read_only=read_only)
    if decision == "allow":
        return "allow", APPROVAL_NOT_NEEDED_READ_ONLY
    if decision == "block":
        _note_for_learning(payload, approval_mode, False, "", blocked=True)
        _decided_by("block")
        _scope_var.set(None)
        _note_for_activity(payload, approval_mode, "block", by="block")
        return "block", ""
    response, line = _decide(payload, approval_mode=approval_mode, risks=risks,
                             approval_callback=approval_callback)
    outcome = _outcome(response)
    if not line:
        line = APPROVAL_GIVEN if outcome == "allow" else APPROVAL_DENIED
    _record(line)
    _note_for_learning(payload, approval_mode, response, line)
    _note_for_activity(payload, approval_mode, outcome)
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
        from row_bot.company.enforce import blocked_words

        return blocked_words(label) or blocked_message or (
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
