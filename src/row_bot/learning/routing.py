"""Routing a turn to the personal model, with fallback to the main model (D11; UX §5).

At a turn's start, ``client_platform`` asks :func:`route` (through ``learning.route_turn``, a path check
while nothing is in use). A turn is routed only when:

- the person is chatting in the app (or a signed-in browser or phone), sending a new message;
- the turn would run on the person's **main model** (their default chat model): a chat where they
  picked another model, the personal model included, runs on that model as chosen;
- the request's **kind of work** (``kinds.classify``, no model call) is one the person gave a
  version (``use.py``: a trial or in use), and that version is still ready where it runs.

A routed turn runs on the personal model. :func:`stream` watches it: when it **gets stuck**

- an error (the model or its runtime failed),
- a tool loop (the same call with the same arguments three times),
- the step budget (more than :data:`MAX_TOOL_CALLS` tool calls, or the agent's own budget or
  no-progress stop),
- an empty answer,

its work so far stays in the conversation (nothing it did is done twice), and the **main model
finishes the turn** from there, as a continuation of the same turn. The chat says so in one quiet
line: "Your personal model got stuck; GPT-6.1 Sol finished this." (the main model's real name). The
request counts as a **cloud finish** in the trial's numbers. A personal model that answers gets the
quiet "Answered by your personal model" line (``checkpoint_reader`` reads it from the reply's own
model name). Stop is the person's: a stopped turn falls back to nothing.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from row_bot.learning import kinds, use

logger = logging.getLogger(__name__)

#: A routed turn may make this many tool calls before it counts as stuck (its step budget).
MAX_TOOL_CALLS = 16
#: The same call with the same arguments this many times is a loop.
LOOP_REPEATS = 3
SURFACES = frozenset({"normal_chat", "remote_client"})
FALLBACK_EVENT = "learning_fallback"
STUCK_REASONS = ("error", "tool_loop", "step_budget", "empty_answer")
# What the main model is told (hidden from the transcript; the person sees the note below instead).
# The request is repeated: a smaller main model loses it behind the personal model's steps (the 5070 e2e).
_HANDOVER = ("[Row-Bot] The personal model started this request of mine but couldn't finish it ({why}):\n\n"
             "{request}\n\nIts work so far is above. Answer the request now. Use tools only for what is still "
             "left to do, and don't redo what is already done.")
_WHY = {"error": "it failed", "tool_loop": "it repeated the same step", "step_budget": "it ran out of steps",
        "empty_answer": "it gave no answer"}


@dataclass(frozen=True)
class Route:
    """A turn routed to a personal-model version for a kind of work."""

    kind: str
    version: int
    state: str
    personal_ref: str
    main_ref: str
    main_name: str
    reason: str = "signals"
    #: The turn's configuration on the personal model (set by the platform once the turn's own is built).
    configurable: Mapping[str, Any] = field(default_factory=dict)

    def with_configurable(self, configurable: Mapping[str, Any]) -> Route:
        return Route(self.kind, self.version, self.state, self.personal_ref, self.main_ref, self.main_name,
                     self.reason, dict(configurable))


def _canonical(ref: str) -> str:
    try:
        from row_bot.providers.model_visibility import canonical_ref

        return canonical_ref(ref) or str(ref or "")
    except Exception:
        return str(ref or "")


def main_model_ref() -> str:
    """The person's main model: their default chat model ("" when none is chosen)."""

    try:
        from row_bot.application.client_first_run import saved_default_model

        return str(saved_default_model() or "")
    except Exception:
        logger.debug("Learning couldn't read the default model", exc_info=True)
        return ""


def model_name(ref: str) -> str:
    """A model's name as the catalog knows it ("GPT-6.1 Sol"), else its id."""

    from row_bot.providers.selection import parse_model_ref

    parsed = parse_model_ref(ref) if ref else None
    if not parsed:
        return "your main model"
    provider, model_id = parsed
    try:
        from row_bot.providers.model_catalog_cache import read_model_catalog_cache

        cache = read_model_catalog_cache(allow_runtime_bootstrap=False, max_bytes=16 * 1024 * 1024)
        info = cache.cloud_cache.get(model_id) or cache.cloud_cache.get(f"{provider}:{model_id}") or {}
        for key in ("display_name", "label", "name"):
            value = info.get(key) if isinstance(info, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()[:128]
    except Exception:
        logger.debug("Learning couldn't read the model catalog", exc_info=True)
    return str(model_id)[:128]


def _ready_ref(version: int, root: Path | None = None) -> str | None:
    """The version's model reference while it is ready where it runs, else None."""

    from row_bot.learning.training import releases

    state = releases.read_state(releases.version_dir(int(version), root))
    if state is None or state.phase != releases.READY or not state.model_ref:
        return None
    return str(state.model_ref)


def route(*, conversation_id: str, text: str, model_ref: str, surface: str = "normal_chat",
          attachments: Iterable[str] = (), apps: Iterable[str] = (), code_folder: bool = False,
          root: Path | None = None, main_ref: str | None = None, now: float | None = None) -> Route | None:
    """Where a new turn goes: a :class:`Route` to the personal model, or None (the main model)."""

    if surface not in SURFACES or not str(text or "").strip():
        return None
    state = use.read(root)
    if not state.assignments:
        return None
    main = main_ref if main_ref is not None else main_model_ref()
    if not main or _canonical(model_ref) != _canonical(main):
        return None  # the person chose another model for this chat
    found = kinds.classify_turn(conversation_id, text, attachments=attachments, apps=apps, code_folder=code_folder,
                                now=now)
    item = state.assignments.get(found.kind)
    if item is None:
        return None
    ref = _ready_ref(item.version, root)
    if ref is None:
        logger.info("Personal model v%s isn't ready; the main model answers", item.version)
        return None
    return Route(found.kind, item.version, item.state, ref, main, model_name(main), found.reason)


# ── Watching a routed turn ──────────────────────────────────────────────────


class StuckWatch:
    """Decides, from a routed turn's events, whether the personal model got stuck."""

    def __init__(self, *, max_calls: int = MAX_TOOL_CALLS, repeats: int = LOOP_REPEATS) -> None:
        self.max_calls = max_calls
        self.repeats = repeats
        self.calls = 0
        self._seen: Counter[str] = Counter()

    @staticmethod
    def _key(payload: Any) -> str:
        get = getattr(payload, "get", None)
        name = str(getattr(payload, "runtime_name", "") or getattr(payload, "raw_name", "")
                   or (get("raw_name") or get("name") if callable(get) else "") or "")
        args = getattr(payload, "args", None)
        if args is None and callable(get):
            args = get("args")
        try:
            text = json.dumps(args or {}, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(args)
        return f"{name}:{text}"

    def tool_call(self, payload: Any) -> str | None:
        self.calls += 1
        key = self._key(payload)
        self._seen[key] += 1
        if self._seen[key] >= self.repeats:
            return "tool_loop"
        if self.calls > self.max_calls:
            return "step_budget"
        return None


class _LinkedStop(threading.Event):
    """Set by the person's Stop *or* by Learning (to end a stuck personal model's run)."""

    def __init__(self, parent: threading.Event | None) -> None:
        super().__init__()
        self._parent = parent

    def is_set(self) -> bool:  # noqa: D401 - Event's own name
        return super().is_set() or bool(self._parent is not None and self._parent.is_set())

    isSet = is_set

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                return False
            super().wait(0.1 if left is None else min(0.1, left))
        return True


def fallback_note(route_: Route) -> str:
    """What the person sees when the main model finished a routed turn."""

    return f"Your personal model got stuck; {route_.main_name} finished this."


def _quoted(text: str, limit: int = 4_000) -> str:
    """The person's request, as a quoted block (shortened when long)."""

    clean = str(text or "").strip()
    if len(clean) > limit:
        clean = clean[:limit].rstrip() + " …"
    return "\n".join(f"> {line}" if line else ">" for line in clean.splitlines()) or "> (the request above)"


def _handover_config(route_: Route, main_config: Mapping[str, Any], text: str, reason: str) -> dict[str, Any]:
    config = copy.deepcopy(dict(main_config))
    configurable = config.setdefault("configurable", {})
    configurable.update({
        "model_override": route_.main_ref,
        "thread_event_context": True,
        "thread_event_new_turn": True,
        "root_objective": str(text or "")[:20_000],
        # A thread event in the person's place (never their words: the approval reviewer, memory and
        # Learning's capture all skip it), so every provider reads it as the next thing to answer.
        "thread_event_messages": [{
            "role": "human", "content": _HANDOVER.format(why=_WHY.get(reason, "it got stuck"),
                                                         request=_quoted(text)),
            "row_bot_ui": {"learning_fallback": {"text": fallback_note(route_), "version": route_.version,
                                                 "kind": route_.kind, "reason": reason}},
        }],
    })
    return config


def stream(route_: Route, factory: Callable[..., Iterator[tuple]], text: str, enabled: list[str],
           main_config: Mapping[str, Any], *, stop_event: threading.Event | None = None,
           root: Path | None = None, clock: Callable[[], float] = time.monotonic,
           answer_open_calls: Callable[[str, str], Any] | None = None) -> Iterator[tuple]:
    """Run a routed turn on the personal model; on a stuck run, the main model finishes it."""

    started = clock()
    stop = _LinkedStop(stop_event)
    watch = StuckWatch()
    stuck: str | None = None
    answered = False
    personal = copy.deepcopy(dict(main_config))
    personal["configurable"] = dict(route_.configurable) or {**dict(main_config.get("configurable") or {}),
                                                             "model_override": route_.personal_ref}
    events = factory(text, enabled, personal, stop_event=stop)
    try:
        for event in events:
            kind = event[0]
            payload = event[1] if len(event) > 1 else None
            if stuck:
                continue  # draining the stopped run: nothing more of it is shown
            if kind == "error":
                stuck = "error"
            elif kind == "terminal":
                stuck = "step_budget"  # the agent's own budget, or no progress
            elif kind == "done":
                if str(payload or "").strip():
                    answered = True
                    yield event
                else:
                    stuck = "empty_answer"
            elif kind == "tool_call":
                yield event
                found = watch.tool_call(payload)
                if found:
                    stuck = found
                    stop.set()  # ends the personal model's run at its next step
            else:
                yield event
    except InterruptedError:
        if stop_event is not None and stop_event.is_set():
            raise
        stuck = stuck or "step_budget"
    except Exception as error:  # noqa: BLE001 - a failed personal model is a stuck one
        logger.info("Personal model v%s failed a routed turn (%s)", route_.version, type(error).__name__)
        stuck = "error"
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
    seconds = clock() - started
    if answered:
        _record(route_, "answered", seconds, root)
        return
    if not stuck or (stop_event is not None and stop_event.is_set()):
        return  # waiting for an approval, or the person stopped it
    thread_id = str((main_config.get("configurable") or {}).get("thread_id") or "")
    if thread_id:
        try:
            (answer_open_calls or _answer_open_calls)(thread_id, "The personal model stopped here.")
        except Exception:  # noqa: BLE001
            logger.debug("Learning couldn't close the personal model's open steps", exc_info=True)
    logger.info("Personal model v%s got stuck (%s); %s finishes the turn", route_.version, stuck, route_.main_name)
    yield (FALLBACK_EVENT, {"version": route_.version, "kind": route_.kind, "reason": stuck,
                            "note": fallback_note(route_)})
    finished = False
    for event in factory(fallback_note(route_), enabled, _handover_config(route_, main_config, text, stuck),
                         stop_event=stop_event):
        if event[0] == "done":
            finished = True
        yield event
    if finished:
        _record(route_, "fallback", clock() - started, root)


def _answer_open_calls(thread_id: str, reason: str) -> None:
    from row_bot import threads

    threads.answer_open_tool_calls(thread_id, reason)


def _record(route_: Route, outcome: str, seconds: float, root: Path | None) -> None:
    try:
        use.record(route_.kind, route_.version, outcome, seconds, root=root)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 - counting never breaks a turn
        logger.debug("Learning couldn't count a routed turn", exc_info=True)


__all__ = [
    "FALLBACK_EVENT",
    "LOOP_REPEATS",
    "MAX_TOOL_CALLS",
    "Route",
    "STUCK_REASONS",
    "StuckWatch",
    "fallback_note",
    "main_model_ref",
    "model_name",
    "route",
    "stream",
]
