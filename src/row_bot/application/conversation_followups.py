"""Turns the server starts to continue a conversation after a reply ends.

A goal keeps working turn after turn (up to its limit, if it has one), and work the assistant
set up in a reply (a new design or code folder) continues in a follow-up turn
once that reply has finished, when the new resource is bound for the turn.

A follow-up starts only after a completed turn: never after Stop, an error or
an approval pause, and a message the person queued goes first. Its prompt is
stored as an internal input whose public text is a short note ("Goal · turn 3
of 10"), so the transcript explains why the assistant continues.

Pending follow-ups live in memory: a restart drops them. Goals that were
working when Row-Bot closed come back paused with one notice, and the person
resumes them (decision D4, ``goals.settle_interrupted_goals``). Only after a
restart Row-Bot caused does a goal take its next turn from its last saved state
by itself (``continue_goals_after_restart``).

A turn stopped by a provider's rate or usage limit makes the goal wait and
continue by itself when the provider said when the limit resets; without a
reset time the goal pauses and says so (B244).
"""
from __future__ import annotations

import logging
import math
import re
import threading
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal

_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
# Goals waiting for a provider limit to reset: conversation -> seconds.
_LIMIT_WAITS: dict[str, float] = {}
# Goals whose last turn the provider cut off and that already tried again once (B324).
_RETRIED_AFTER_CUT: set[str] = set()
_CUT_RETRY_SECONDS = 5.0


def _schedule(delay: float, run: Callable[[], None]) -> None:
    """Run ``run`` once, ``delay`` seconds from now."""
    timer = threading.Timer(delay, run)
    timer.daemon = True
    timer.start()


def _about(seconds: float) -> str:
    """"about 2 minutes" for a wait, rounded up."""
    if seconds < 60:
        count, unit = max(1, math.ceil(seconds)), "second"
    elif seconds < 3600:
        count, unit = math.ceil(seconds / 60), "minute"
    else:
        count, unit = math.ceil(seconds / 3600), "hour"
    return f"{count} {unit}{'' if count == 1 else 's'}"

# "proactive": the person acted on a Proactive Row-Bot moment that carries on in this chat. "watch": a watch saw a
# change (B4), apart from a one-time wait's "wait".
FollowupKind = Literal["goal", "resource", "workflow", "linked", "wait", "carry_on", "proactive", "watch"]

# A reply that says what it will do next and then ends gets one turn to do it (R1, 12 Nov): a model writes
# "Next I'll …" with no tool call, which ends its turn, and nothing was left to bring it back.
CARRY_ON_NOTE = "Carrying on with the next step"
#: Carry-ons in a row, in the app and in ``row-bot run``: a repeat comes only after a carry-on turn that used
#: tools, on the stop check's clear YES; past this the reply waits for the person (lane E's DeepSWE traces, 26 Nov).
MAX_CARRY_ONS = 10
CARRY_ON_PROMPT = (
    "This is Row-Bot, not the person: your last reply said what you'd do next, then ended your turn, so "
    "nothing ran. Carry on with it now. If you need the person for something, ask them; if the work is "
    "done, say so in a line.")
# The same for a headless run (``row-bot run``), which has nobody to ask (Learning reviewed, 23 Nov).
HEADLESS_CARRY_ON_PROMPT = (
    "This is Row-Bot, not the person: your last reply said what you'd do next, then ended your turn, so "
    "nothing ran. Carry on with it now. If the work is done, say so in a line.")
# A word rule finds a likely stop; the chat's own model then decides (S1, 18 Nov): "I'll reshoot …", "the film
# isn't finished yet" and "I should have kept going" ended turns the narrower rule missed, 7 of 7 since 5.3.3.
_NEXT_STEP = re.compile(
    r"\bI(?:'|’)ll\s+(?!be\b|wait\b|need\b|let you know\b|keep an eye\b|check back\b|report back\b)[a-z]+"
    r"|\b(?:isn(?:'|’)t|is not|aren(?:'|’)t|hasn(?:'|’)t been|haven(?:'|’)t been|not)\s+(?:\w+\s+)?"
    r"(?:finished|done|rendered|complete|built|made|ready)\s+yet\b"
    r"|\bI should have (?:kept going|continued|carried on)\b"
    r"|\bI(?:'|’)m\s+(?:now\s+)?(?:going to|about to|carrying on|moving on|starting)\b"
    r"|\b(?:carrying on|moving on) (?:now|with)\b"
    # Work announced as under way, or still owed, when the turn has already ended (lane E's τ²-bench traces,
    # 19 Nov: "I'm checking the flight statuses before …", "I've checked two and will review the remaining
    # two …", "I still need to verify …").
    r"|\bI(?:'|’)m\s+(?:now\s+)?(?:checking|reviewing|verifying|looking into|working on|processing)\b"
    r"|\b(?:I|and)\s+will\s+(?:now\s+)?(?:check|review|verify|finish|continue|go through|look)\b"
    r"|\bI still (?:need|have) to\b"
    # Work announced with no "I'll" (lane E's DeepSWE traces, 26 Nov: "I'm now finishing the exports before ...",
    # "Next I'm wiring interface dispatch ..." ended runs with nothing committed).
    r"|\bI(?:'|’)m\s+now\s+[a-z]+ing\b|\bnext,?\s+I(?:'|’)m\s+[a-z]+ing\b",
    re.IGNORECASE)
# The narrower R1 rule: what carries on when the check can't be asked (no model, a provider error), as before.
_SURE_NEXT_STEP = re.compile(
    r"\b(?:next|now|then),?\s+I(?:'|’)ll\b|\bI(?:'|’)ll\s+(?:now|next|then)\b"
    r"|\bI(?:'|’)m\s+(?:now\s+)?(?:going to|about to|carrying on|moving on|starting)\b"
    r"|\b(?:carrying on|moving on) (?:now|with)\b",
    re.IGNORECASE)
_STOP_CHECK = (
    "You check one reply from an assistant that works with tools. Its turn has ended, so it does nothing more "
    "unless it is woken. Answer YES if the reply says it will do more of the work now, or that the work isn't "
    "finished, and it needs nothing from the person. Answer NO if the work is done, or the reply asks the "
    "person something, waits for them or for something else (a sign-in, an approval, a set time), or only "
    "offers more. Answer with one word: YES or NO.")
# The reply hands over to the person: their answer, OK or choice is what comes next.
_HANDS_OVER = re.compile(
    r"\?|\b(?:let me know|once you|when you|if you(?:'|’)d like|if you want|want me to|shall I|should I"
    r"|your call|say the word|up to you)\b",
    re.IGNORECASE)
_TAIL_CHARS = 400  # Of the reply's end: where a "next I'll" that ends the turn is.


def stopped_on_next_step(reply: str) -> bool:
    """The reply ends saying what it will do next, not handing over to the person."""
    tail = str(reply or "").strip()[-_TAIL_CHARS:]
    return bool(_NEXT_STEP.search(tail)) and not _HANDS_OVER.search(tail)


# A reply that says it saved a file that isn't there gets one turn to make it or correct itself (A2 WP4).
FILE_CHECK_NOTE = "Checking the files it named"


def file_check_prompt(names: list[str]) -> str:
    """The follow-up's prompt for files the last reply says it saved and that aren't there."""
    shown = [f"`{name}`" for name in names]
    listed = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]
    if len(shown) == 1:
        return (f"This is Row-Bot, not the person: your last reply says you saved {listed}, but there's no "
                "file there. Make it now, or correct your reply in a line.")
    return (f"This is Row-Bot, not the person: your last reply says you saved {listed}, but those files "
            "aren't there. Make them now, or correct your reply in a line.")


def _carry_on(conversation_id: str, *, status: str, assistant_text: str, followup_kind: str,
              last_reply: str = "", depth: int = 0, used_tools: bool = False) -> None:
    """Schedule a carry-on turn after a reply that stopped on its next step, or that names files it says it
    saved and that aren't there. After a carry-on (*depth* in a row) only a next-step carry-on can follow, when
    that turn *used_tools*, up to ``MAX_CARRY_ONS``, and only on the stop check's clear YES. *last_reply* is
    what the model wrote after its last tool call: how the turn ended, without the progress lines before its
    tools ("I'll paint …", F7)."""
    from row_bot.file_claims import could_claim

    if status != "completed" or followup_kind == "watch":
        return  # A watch's turn says what changed and stops there.
    again = followup_kind == "carry_on"
    if again and (not used_tools or depth >= MAX_CARRY_ONS):
        return  # A carry-on that ran nothing didn't take; past the cap the person decides.
    ending = last_reply if last_reply.strip() else assistant_text
    next_step = stopped_on_next_step(ending)
    if again and not next_step:
        return  # The file check comes once.
    if not next_step and not could_claim(assistant_text):
        return
    from row_bot import tasks, waits
    # A workflow's run moves on by itself, and a wait already brings the conversation back. A watch (B4) only speaks
    # up on a change, which may never come: it doesn't count.
    if tasks.active_run_for_conversation(conversation_id) is not None or any(
            record["kind"] != "watch" for record in waits.waiting(conversation_id)):
        return
    if next_step:
        reply = str(ending or "").strip()
        followup = Followup(kind="carry_on", prompt=CARRY_ON_PROMPT, note=CARRY_ON_NOTE, check_reply=reply[-1500:],
                            unchecked=bool(_SURE_NEXT_STEP.search(reply[-_TAIL_CHARS:])),
                            depth=depth + 1, strict=again)
    else:
        missing = _missing_files(conversation_id, assistant_text)
        if not missing:
            return
        followup = Followup(kind="carry_on", prompt=file_check_prompt(missing), note=FILE_CHECK_NOTE)
    with _LOCK:
        if conversation_id in _PENDING:
            return
        _PENDING[conversation_id] = followup


def _missing_files(conversation_id: str, reply: str) -> list[str]:
    """Files the reply says it saved that are in none of the chat's folders or its own files; [] on doubt."""
    from row_bot.file_claims import claimed_missing

    try:
        return claimed_missing(reply, _file_roots(conversation_id), known_names=_chat_file_names(conversation_id))
    except Exception:
        _LOG.warning("The file check after a reply was skipped for %s", conversation_id, exc_info=True)
        return []


def _file_roots(conversation_id: str) -> list:
    """Where a reply's files are: the workspace (as set, and the default one Designer exports to) and the
    conversation's code folder."""
    from pathlib import Path

    from row_bot.application.conversation_creation import configured_workspace_root
    from row_bot.data_paths import default_workspace_root

    roots: list[Path] = []
    for find in (configured_workspace_root, default_workspace_root):
        try:
            root = Path(find()).resolve()
        except (OSError, ValueError):
            continue
        if root not in roots:
            roots.append(root)
    try:
        from row_bot.developer import tool_context
        from row_bot.developer.storage import get_workspace

        workspace_id = tool_context.infer_workspace_id_from_thread(conversation_id)
        folder = get_workspace(workspace_id) if workspace_id else None
        if folder is not None and folder.path and Path(folder.path).is_dir():
            roots.append(Path(folder.path).resolve())
    except Exception:
        _LOG.debug("No code folder for the file check of %s", conversation_id, exc_info=True)
    return roots


def _chat_file_names(conversation_id: str) -> set[str]:
    """The names of the conversation's own files (attachments, made pictures and videos)."""
    from row_bot.application.attachments import list_conversation_files

    try:
        return {str(item.get("name") or "") for item in list_conversation_files(conversation_id)}
    except Exception:
        _LOG.debug("The chat's files were not listed for the file check of %s", conversation_id, exc_info=True)
        return set()


@dataclass(frozen=True)
class Followup:
    kind: FollowupKind
    prompt: str
    note: str
    goal_id: str = ""
    # A message from another conversation (5.3.1 C): where it came from, and the text the chat shows.
    linked_from: dict | None = None
    shown: str = ""
    # A workflow run's first turn: the messages other conversations left for it ({id, title, text}).
    held: tuple[dict, ...] = ()
    # A carry-on the word rule found: the reply's end, which the chat's model checks before it starts (S1).
    check_reply: str = ""
    # Whether it carries on when the check can't be asked: only on the narrower rule's words, as before S1.
    unchecked: bool = False
    # Which carry-on in a row this is (1 for the first). A repeat needs the check's clear YES (``strict``).
    depth: int = 0
    strict: bool = False


def _still_working(conversation_id: str, reply: str, model_ref: str, *, unchecked: bool,
                   strict: bool = False) -> bool:
    """The chat's own model says whether *reply* stopped with work it means to do; *unchecked* when it can't.
    Anything but NO counts as yes, unless *strict*: then only a YES does, and a check that can't run says no."""
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        from row_bot.models import get_llm_for
        from row_bot.providers.resolution import resolve_provider_config
        from row_bot.providers.usage_ledger import with_usage_purpose

        model = with_usage_purpose(
            get_llm_for(resolve_provider_config(model_ref, allow_legacy_local=True).selection_ref),
            "verifier", thread_id=conversation_id)
        answer = model.invoke([SystemMessage(content=_STOP_CHECK), HumanMessage(content=f"The reply:\n{reply}")])
        said = str(getattr(answer, "text", "") or getattr(answer, "content", "")).strip().upper()
        if not said.startswith(("YES", "NO")):
            first = said.split(maxsplit=1)[0][:12] if said else ""
            _LOG.info("The stop check for %s answered neither YES nor NO (it began %r)", conversation_id, first)
        return said.startswith("YES") if strict else not said.startswith("NO")
    except Exception as exc:
        _LOG.info("The stop check for %s couldn't run (%s)", conversation_id, type(exc).__name__)
        return False if strict else unchecked


def followup_after(conversation_id: str, reply: str, model_ref: str, *, headless: bool = False,
                   strict: bool = False) -> Followup | None:
    """The one follow-up the app would start after a completed turn that ended with *reply*, decided now (the
    stop check included), or None. For a caller that runs its own turns (``row-bot run``, *headless*: its
    carry-on doesn't send the model to a person). The app never starts one after a follow-up; a caller that
    does passes *strict*, so the stop check must say YES."""
    _carry_on(conversation_id, status="completed", assistant_text=reply, followup_kind="")
    followup = take(conversation_id)
    if followup is None or (followup.check_reply and not _still_working(
            conversation_id, followup.check_reply, model_ref, unchecked=followup.unchecked, strict=strict)):
        return None
    if headless and followup.prompt == CARRY_ON_PROMPT:
        return replace(followup, prompt=HEADLESS_CARRY_ON_PROMPT)
    return followup


_PENDING: dict[str, Followup] = {}


def after_tool_turned_on(conversation_id: str, label: str) -> None:
    """A tool turned on mid-turn reaches the model only in its next turn: Row-Bot starts that turn itself."""
    if not conversation_id:
        return
    schedule(conversation_id, Followup(
        kind="carry_on",
        prompt=(f"This is Row-Bot, not the person: {label} is now on and ready. Carry on with what the "
                "person asked, using it."),
        note=f"Carrying on with {label}"))


def schedule(conversation_id: str, followup: Followup) -> None:
    """Remember the next follow-up; resource work goes before a goal step."""
    with _LOCK:
        current = _PENDING.get(conversation_id)
        if current is not None and current.kind == "resource" and followup.kind == "goal":
            return
        _PENDING[conversation_id] = followup


def discard(conversation_id: str, kind: FollowupKind | None = None) -> None:
    with _LOCK:
        current = _PENDING.get(conversation_id)
        if current is not None and (kind is None or current.kind == kind):
            _PENDING.pop(conversation_id, None)


def pending(conversation_id: str) -> Followup | None:
    with _LOCK:
        return _PENDING.get(conversation_id)


def take(conversation_id: str) -> Followup | None:
    with _LOCK:
        return _PENDING.pop(conversation_id, None)


def start(service: Any, conversation_id: str, followup: Followup, *, model_ref: str = "",
          runtime_surface: str = "normal_chat") -> dict | None:
    """Start a follow-up turn now; returns the submit receipt or ``None``.

    The model is the one the last turn used, else the conversation's saved
    choice. Bindings are read fresh, so a resource created in the previous
    turn is available to this one.
    """
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.providers.selection import model_choice_value, parse_model_ref

    model = model_choice_value(model_ref or _conversation_model(service, conversation_id))
    parsed = parse_model_ref(model) if model else None
    if not parsed:
        _LOG.info("Follow-up for %s skipped: no model", conversation_id)
        return None
    payload = {
        "submission_id": str(uuid.uuid4()),
        "text": followup.prompt,
        "model_selection": {"provider_id": parsed[0], "model_ref": model},
    }
    try:
        return service._start(conversation_id, payload, resume=False, followup=followup,
                              runtime_surface=runtime_surface)
    except ClientPlatformError as error:
        _LOG.info("Follow-up for %s not started: %s", conversation_id, error)
        return None


def _conversation_model(service: Any, conversation_id: str) -> str:
    try:
        row = service._metadata(conversation_id)
    except Exception:
        return ""
    from row_bot.models import get_current_model
    selected = str(row.get("model_override") or "")
    if selected:
        return selected
    try:
        return str(get_current_model() or "")
    except Exception:
        return ""


def after_finish(service: Any, handle: Any, status: str) -> None:
    """Run the pending follow-up once a platform turn has fully finished."""
    conversation_id = handle.conversation_id
    if status != "completed":
        discard(conversation_id)
        with _LOCK:
            wait = _LIMIT_WAITS.pop(conversation_id, None)
        if wait:
            _schedule(wait, lambda: _after_limit_reset(service, conversation_id))
            return
        goal = live_goal(conversation_id)
        # Stop can land while the goal step was still deciding.
        if goal is not None and goal.get("status") == "active" and status != "waiting_approval":
            from row_bot import goals
            goals.set_goal_status(str(goal["id"]), "paused",
                                  reason="You stopped the reply." if status == "stopped"
                                  else "The reply didn't finish.",
                                  verdict="paused", expected_revision=int(goal.get("revision") or 0))
        return
    if service.registry.active(conversation_id):
        return  # A message the person queued went first; the follow-up waits.
    followup = take(conversation_id)
    if followup is None:
        return
    goal = live_goal(conversation_id)
    if followup.kind == "goal" and (goal is None or goal.get("status") != "active"):
        return  # Paused, stopped or done since it was scheduled.
    if followup.kind == "resource" and goal is None and _goal_waits_on_person(conversation_id):
        # A goal that asked the person something starts no work until they answer (B316): the hand-off
        # waits and runs after their answer turn, once the goal is active again.
        schedule(conversation_id, followup)
        return
    if followup.check_reply:
        # The check asks a model, so it runs on its own thread: this one holds the command lock (S1).
        _schedule(0, lambda: _carry_on_if_still_working(service, handle, followup))
        return
    start(service, conversation_id, followup, model_ref=handle.model_ref,
          runtime_surface=handle.runtime_surface)


def _carry_on_if_still_working(service: Any, handle: Any, followup: Followup) -> None:
    """Start the carry-on turn the word rule found, if the chat's model agrees the reply stopped mid-work."""
    conversation_id = handle.conversation_id
    if not _still_working(conversation_id, followup.check_reply, handle.model_ref, unchecked=followup.unchecked,
                          strict=followup.strict):
        return  # Done, or handing over to the person: nothing to carry on.
    from row_bot.application.client_platform import _COMMAND_LOCK

    with _COMMAND_LOCK:
        if service.registry.active(conversation_id) or pending(conversation_id) is not None:
            return  # The person wrote in the meantime: their message goes first.
        start(service, conversation_id, followup, model_ref=handle.model_ref,
              runtime_surface=handle.runtime_surface)


# ------------------------------------------------------------------ goals


def goal_note(goal: dict[str, Any]) -> str:
    used = int(goal.get("turns_used") or 0)
    limit = int(goal.get("max_turns") or 0)
    return f"Goal · turn {min(used + 1, limit)} of {limit}" if limit else f"Goal · turn {used + 1}"


def _goal_waits_on_person(conversation_id: str) -> bool:
    from row_bot import goals
    goal = goals.get_current_goal(conversation_id, include_terminal=True)
    return bool(goal) and goal.get("status") in {"blocked", "paused"}


def live_goal(conversation_id: str) -> dict[str, Any] | None:
    from row_bot import goals
    goal = goals.get_current_goal(conversation_id)
    if goal and str(goal.get("status") or "") in goals.GOAL_ACTIVE_STATUSES:
        return goal
    return None


def after_platform_turn(conversation_id: str, *, generation_id: str, status: str,
                        assistant_text: str, model_ref: str, goal_id: str = "",
                        error_text: str = "", followup_kind: str = "", service: Any = None,
                        last_reply: str = "", followup_depth: int = 0, used_tools: bool = False) -> None:
    """Advance the conversation's goal after one of its turns ended.

    Completed turns count toward the goal and may schedule the next step;
    an approval pause marks the goal as waiting; Stop and failures pause it
    with the reason, so it never reads as working while nothing runs. A turn
    in which the model finished the goal (``goal_id`` was live when it began)
    still counts, so the card reads "Done · Turn 3 of 3". A provider limit
    with a reset time makes the goal wait instead (``error_text``). A goal
    that looks done and has a check runs its check now instead of a next
    turn (B3).
    """
    from row_bot import goals
    if goal_id:
        try:
            _count_turn_tokens(conversation_id, goal_id)
        except Exception:
            _LOG.warning("The goal's tokens were not counted for %s", conversation_id, exc_info=True)
    goal = live_goal(conversation_id)
    if goal is None:
        finished = (goals.get_current_goal(conversation_id, include_terminal=True)
                    if goal_id and status == "completed" else None)
        if finished and str(finished.get("id") or "") == goal_id:
            try:
                goals.after_turn(thread_id=conversation_id, turn_id=generation_id,
                                 assistant_text=assistant_text, model_override=model_ref)
            except Exception:
                _LOG.exception("Counting the goal's last turn failed for %s", conversation_id)
        else:
            _carry_on(conversation_id, status=status, assistant_text=assistant_text, followup_kind=followup_kind,
                      last_reply=last_reply, depth=followup_depth, used_tools=used_tools)
        return
    try:
        if status == "completed":
            _RETRIED_AFTER_CUT.discard(str(goal["id"]))
            decision = goals.after_turn(thread_id=conversation_id, turn_id=generation_id,
                                        assistant_text=assistant_text, model_override=model_ref)
            if decision.goal and decision.goal.get("check_state") == "pending" and service is not None:
                start_goal_check(service, conversation_id, decision.goal)
            elif decision.should_continue and decision.goal:
                schedule(conversation_id, Followup(
                    kind="goal", prompt=decision.continuation_prompt,
                    note=goal_note(decision.goal), goal_id=str(decision.goal.get("id") or "")))
            else:
                discard(conversation_id, "goal")
        elif status == "waiting_approval":
            goals.after_turn(thread_id=conversation_id, turn_id=generation_id,
                             assistant_text=assistant_text, model_override=model_ref,
                             pending_approval=True)
            discard(conversation_id, "goal")
        else:
            discard(conversation_id, "goal")
            wait = goals.provider_limit_wait(error_text) if status == "interrupted" else None
            if wait:
                # It goes on by itself once the limit resets (after_finish).
                with _LOCK:
                    _LIMIT_WAITS[conversation_id] = wait
                goals.set_goal_status(
                    str(goal["id"]), "active", verdict="continue",
                    reason=f"The provider's usage limit was reached. Continuing in about {_about(wait)}.",
                    expected_revision=int(goal.get("revision") or 0))
                return
            from row_bot.agent import PROVIDER_CUT_MESSAGE
            cut = status == "interrupted" and error_text.strip() == PROVIDER_CUT_MESSAGE
            if cut and str(goal["id"]) not in _RETRIED_AFTER_CUT:
                # A provider can end a long reply part-way (a large tool call can pass its time limit): the goal
                # tries the step again once, by itself (B324).
                _RETRIED_AFTER_CUT.add(str(goal["id"]))
                with _LOCK:
                    _LIMIT_WAITS[conversation_id] = _CUT_RETRY_SECONDS
                goals.set_goal_status(
                    str(goal["id"]), "active", verdict="continue",
                    reason="The provider ended the reply before it finished. Trying the step again.",
                    expected_revision=int(goal.get("revision") or 0))
                return
            reason = ("You stopped the reply." if status == "stopped"
                      else "The provider's rate or usage limit stopped the goal. "
                           "Resume it once the limit resets." if wait == 0.0
                      else "The provider ended the reply before it finished, twice (it may have been writing "
                           "something very long). Resume to try again." if cut
                      else "The reply didn't finish.")
            goals.set_goal_status(str(goal["id"]), "paused", reason=reason, verdict="paused",
                                  expected_revision=int(goal.get("revision") or 0))
    except Exception:
        _LOG.exception("Goal step after a turn failed for %s", conversation_id)


def _count_turn_tokens(conversation_id: str, goal_id: str) -> None:
    """Add the tokens this turn's replies used to the goal's total (B244).

    The turn's replies are the assistant messages after its input; a turn
    resumed after an approval skips the ones its first part already counted.
    """
    from row_bot import goals, threads
    from row_bot.turn_context import is_person_message
    goal = goals.get_goal(goal_id)
    if not goal:
        return
    saved = threads.checkpointer.get_tuple({"configurable": {"thread_id": conversation_id, "checkpoint_ns": ""}})
    messages = list(saved.checkpoint.get("channel_values", {}).get("messages", []) or []) if saved else []
    start = next((index + 1 for index in range(len(messages) - 1, -1, -1)
                  if is_person_message(messages[index])), 0)
    turn = messages[start:]
    ids = [str(getattr(message, "id", "") or "") for message in turn]
    counted = str(goal.get("tokens_counted_through") or "")
    if counted in ids:
        turn = turn[ids.index(counted) + 1:]
    replies = [message for message in turn if getattr(message, "type", "") == "ai"]
    if replies:
        tokens = sum(int((getattr(message, "usage_metadata", None) or {}).get("total_tokens") or 0)
                     for message in replies)
        goals.record_goal_tokens(goal_id, tokens, str(getattr(replies[-1], "id", "") or ""))


def _after_limit_reset(service: Any, conversation_id: str) -> None:
    """The provider's limit has reset: the waiting goal takes its next turn."""
    goal = live_goal(conversation_id)
    if goal is None or goal.get("status") != "active":
        return  # Paused, stopped or done while it waited.
    start_goal_turn(service, conversation_id, goal, initial=False)


def start_goal_check(service: Any, conversation_id: str, goal: dict[str, Any]) -> None:
    """The goal looks done and has a check: run it (B3). Exit 0 completes the goal; another exit code starts
    the turn that fixes what it reported; a check that couldn't run pauses the goal."""
    from row_bot import goal_checks

    discard(conversation_id, "goal")
    goal_checks.start(str(goal["id"]), lambda decision: _after_goal_check(service, conversation_id, decision))


def _after_goal_check(service: Any, conversation_id: str, decision: Any) -> None:
    if not decision.should_continue or not decision.goal:
        return
    from row_bot import goals
    from row_bot.application.client_platform import _COMMAND_LOCK

    goal = decision.goal
    followup = Followup(kind="goal", prompt=decision.continuation_prompt,
                        note=f"{goal_note(goal)} · fixing what {goal.get('check_command')} reported",
                        goal_id=str(goal.get("id") or ""))
    # A turn that is still finishing (or one the person started) goes first: the fix turn follows it.
    with _COMMAND_LOCK:
        if service.registry.active(conversation_id):
            schedule(conversation_id, followup)
            return
        started = start(service, conversation_id, followup)
    if started is None:
        goals.set_goal_status(str(goal["id"]), "paused",
                              reason="The goal couldn't continue after its check. Choose a model, then resume it.",
                              verdict="paused", expected_revision=int(goal.get("revision") or 0))


def start_goal_turn(service: Any, conversation_id: str, goal: dict[str, Any], *,
                    initial: bool) -> None:
    """Begin working on a goal now, or right after the running turn; a goal whose check is pending (Row-Bot
    closed while it ran) runs its check instead (B3)."""
    from row_bot import goals
    if not initial and goal.get("check_state") == "pending":
        start_goal_check(service, conversation_id, goal)
        return
    prompt = (goals.build_initial_goal_prompt(goal) if initial
              else goals.build_continuation_prompt(goal))
    followup = Followup(kind="goal", prompt=prompt, note=goal_note(goal),
                        goal_id=str(goal.get("id") or ""))
    if service.registry.active(conversation_id):
        schedule(conversation_id, followup)
        return
    if start(service, conversation_id, followup) is None:
        goals.set_goal_status(str(goal["id"]), "paused",
                              reason="The goal couldn't start. Choose a model, then resume it.",
                              verdict="paused", expected_revision=int(goal.get("revision") or 0))


def continue_goals_after_restart(service: Any) -> int:
    """After a restart Row-Bot caused, goals left working go on from their last saved turn.

    Their next turn gets the continuation prompt with the saved progress; a goal
    waiting for an approval keeps waiting for it. After a normal launch nothing
    starts here: start-up paused the goals with one notice and the person
    resumes them (decision D4, ``goals.settle_interrupted_goals``), as it
    always pauses channel goals.
    """
    from row_bot import goals
    from row_bot.application.startup_state import last_exit_was_app_initiated
    if not last_exit_was_app_initiated():
        return 0
    goals_left = goals.goals_left_working()
    for goal in goals_left:
        start_goal_turn(service, str(goal["thread_id"]), goal, initial=False)
    return len(goals_left)


def after_goal_change(service: Any, conversation_id: str, operation: str,
                      goal: dict[str, Any] | None) -> None:
    """Start, resume, pause or end the goal's turns after a goal command."""
    if operation in {"start", "pause", "complete", "clear"}:
        # The person decides: a check running for the goal stops, a pending one is dropped (B3).
        from row_bot import goal_checks
        goal_checks.stop_conversation(conversation_id)
    if operation == "start" and goal:
        from row_bot import threads
        from row_bot.application.conversation_naming import name_first_message

        if not threads.get_latest_checkpoint_revision(conversation_id):
            # A goal started in an empty conversation names it, as a first message would (B319).
            name_first_message(conversation_id, str(goal.get("objective") or ""))
    if operation in {"start", "resume"} and goal and goal.get("status") == "active":
        start_goal_turn(service, conversation_id, goal, initial=operation == "start")
    elif operation in {"pause", "complete", "clear"}:
        discard(conversation_id, "goal")


def after_approval(conversation_id: str, *, approved: bool) -> None:
    """A decided approval lets a waiting goal go on, or ends it when denied."""
    from row_bot import goals
    goal = goals.get_current_goal(conversation_id)
    if not goal or goal.get("status") != "waiting_approval":
        return
    if approved:
        goals.set_goal_status(str(goal["id"]), "active", reason="Approved. Continuing.",
                              verdict="continue", expected_revision=int(goal.get("revision") or 0))
    else:
        goals.set_goal_status(str(goal["id"]), "blocked", reason="You denied the approval it needed.",
                              verdict="blocked", finish_run_status="blocked",
                              expected_revision=int(goal.get("revision") or 0))
