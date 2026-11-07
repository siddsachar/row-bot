"""The backup model (#140): one model the person chose answers when the main model can't.

Off by default. A turn always tries its own model first. When that model fails for a reason that says
nothing about the request (the provider is down, busy, out of credit, signed out, not running or slow),
the backup model finishes the turn from where it stopped: the work so far stays in the conversation,
open tool calls are closed, and a hidden hand-over asks the backup to answer without redoing steps.
The chat says so in one quiet line ("qwen3:8b didn't answer (it couldn't be reached), so GPT-5 mini
took over."). There is one switch at most, and no memory that a model was down.

Never for a request-shaped failure (context too long, a refusal, tools or images the model can't take,
an error nobody recognises), the person's Stop, a caller's own limit, an incognito chat whose backup is
in the cloud, or Learning's personal models (Learning finishes their turns itself). The hand-over is a
small copy of Learning's finish-the-turn pattern (``learning/routing.py``); the two may merge after the
Learning v2 freeze.

``agent.stream_agent`` and ``agent.resume_stream_agent`` call :func:`stream`, so the app, channels,
plugin channels and workflows are covered in one place. The model call reports why it failed with
:func:`note_failure`.
"""
from __future__ import annotations

import copy
import logging
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from row_bot.providers.errors import ProviderErrorKind, normalize_provider_error

logger = logging.getLogger(__name__)

EVENT = "backup_model"
SETTINGS_KEY = "backup_model"
# Why the main model didn't answer, in the chat's words. Only these kinds switch.
WHY = {
    ProviderErrorKind.CONNECTION_FAILED: "it couldn't be reached",
    ProviderErrorKind.PROVIDER_OUTAGE: "it had a server error",
    ProviderErrorKind.TIMEOUT: "it timed out",
    ProviderErrorKind.RATE_LIMITED: "it's busy right now",
    ProviderErrorKind.QUOTA_EXHAUSTED: "it's out of credit",
    ProviderErrorKind.AUTHENTICATION: "its sign-in expired",
    ProviderErrorKind.CREDENTIAL_MISSING: "it isn't signed in",
    ProviderErrorKind.MODEL_UNAVAILABLE: "it isn't available",
}
_STATUS = {401: ProviderErrorKind.AUTHENTICATION, 402: ProviderErrorKind.QUOTA_EXHAUSTED,
           403: ProviderErrorKind.AUTHENTICATION, 404: ProviderErrorKind.MODEL_UNAVAILABLE,
           408: ProviderErrorKind.TIMEOUT, 429: ProviderErrorKind.RATE_LIMITED}
_PERSONAL = re.compile(r"row-bot-personal-v\d+", re.IGNORECASE)
# What the backup is told (hidden; the person sees the note instead). The request is repeated so a
# backup never loses it behind the steps already taken.
_HANDOVER = ("[Row-Bot] My main model couldn't answer this request of mine ({why}):\n\n{request}\n\n"
             "Any work so far is above. Answer the request now. Use tools only for what is still left to "
             "do, and don't redo what is already done.")


class MainModelNotReady(RuntimeError):
    """The turn's model couldn't start (its provider isn't running or isn't signed in)."""


@dataclass
class _Turn:
    failures: list[BaseException] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


_TURN: ContextVar[_Turn | None] = ContextVar("row_bot_backup_turn", default=None)


def note_failure(exc: BaseException) -> None:
    """The turn's model call failed with ``exc`` (kept only while a backup could answer)."""
    turn = _TURN.get()
    if turn is not None:
        turn.failures.append(exc)


def hold_notice(message: str) -> bool:
    """Keep an API-error notice while a backup could still answer; True when it was kept."""
    turn = _TURN.get()
    if turn is None:
        return False
    turn.notices.append(message)
    return True


# ── The setting ─────────────────────────────────────────────────────────────


def read_setting() -> dict[str, Any]:
    """``{"enabled": bool, "ref": str}`` as saved beside the main model (off and empty by default)."""
    from row_bot.providers.saved_model_settings import read_saved_model_settings

    try:
        saved, _revision, _exists = read_saved_model_settings()
    except Exception:
        logger.warning("The backup model setting could not be read")
        return {"enabled": False, "ref": ""}
    value = saved.get(SETTINGS_KEY)
    value = value if isinstance(value, dict) else {}
    ref = value.get("ref")
    return {"enabled": value.get("enabled") is True, "ref": ref.strip()[:647] if isinstance(ref, str) else ""}


def save_setting(*, enabled: bool | None = None, ref: str | None = None) -> dict[str, Any]:
    """Change the switch and/or the model, keeping everything else in ``model_settings.json``."""
    from row_bot.providers.saved_model_settings import update_saved_model_settings

    def merge(current: dict) -> dict:
        value = current.get(SETTINGS_KEY)
        value = dict(value) if isinstance(value, dict) else {}
        if enabled is not None:
            value["enabled"] = bool(enabled)
        if ref is not None:
            value["ref"] = str(ref)
        value.setdefault("enabled", False)
        value.setdefault("ref", "")
        return {**current, SETTINGS_KEY: value}

    update_saved_model_settings(merge)
    return read_setting()


def backup_model() -> str | None:
    """The backup model's reference while it is switched on and chosen, else None."""
    setting = read_setting()
    return setting["ref"] if setting["enabled"] and setting["ref"] else None


# ── When it answers ─────────────────────────────────────────────────────────


def failure_kind(exc: BaseException | None) -> ProviderErrorKind:
    """What kind of failure ``exc`` is (its HTTP status, its type, then its words)."""
    from row_bot.providers.runtime import ProviderSignInRequired

    chain: list[BaseException] = []
    current = exc
    while current is not None and len(chain) < 8:
        chain.append(current)
        current = current.__cause__ or current.__context__
    for current in chain:
        if isinstance(current, MainModelNotReady):
            return ProviderErrorKind.MODEL_UNAVAILABLE
        if isinstance(current, ProviderSignInRequired):
            return ProviderErrorKind.AUTHENTICATION
        status = getattr(current, "status_code", None)
        if isinstance(status, int):
            if status in _STATUS:
                return _STATUS[status]
            if status >= 500:
                return ProviderErrorKind.PROVIDER_OUTAGE
            if 400 <= status < 500:
                return ProviderErrorKind.UNKNOWN  # the request itself (bad request, too large)
        name = type(current).__name__.lower()
        if "timeout" in name:
            return ProviderErrorKind.TIMEOUT
        if isinstance(current, ConnectionError) or "connecterror" in name or "connectionerror" in name:
            return ProviderErrorKind.CONNECTION_FAILED
    for current in chain:
        kind = normalize_provider_error(current).kind
        if kind is not ProviderErrorKind.UNKNOWN:
            return kind
    return ProviderErrorKind.UNKNOWN


def reason(exc: BaseException | None) -> str | None:
    """Why the backup should answer, in the chat's words, or None when it must not."""
    return WHY.get(failure_kind(exc))


def model_name(ref: str) -> str:
    """A model's name as the catalog knows it ("GPT-5 mini"), else its id."""
    from row_bot.providers.selection import parse_model_ref

    parsed = parse_model_ref(ref)
    if not parsed:
        return str(ref or "the model")[:128]
    provider, model_id = parsed
    try:
        from row_bot.providers.model_catalog_cache import read_model_catalog_cache

        cache = read_model_catalog_cache(allow_runtime_bootstrap=False, max_bytes=16 * 1024 * 1024)
        info = (cache.cloud_cache.get(f"model:{provider}:{model_id}") or cache.cloud_cache.get(model_id)
                or cache.cloud_cache.get(f"{provider}:{model_id}") or {})
        for key in ("display_name", "label", "name"):
            value = info.get(key) if isinstance(info, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()[:128]
    except Exception:
        logger.debug("The model catalog could not be read for a name", exc_info=True)
    return str(model_id)[:128]


def note(main_name: str, backup_name: str, why: str) -> str:
    """The chat's one line when the backup took over."""
    return f"{main_name} didn't answer ({why}), so {backup_name} took over."


def is_local(ref: str) -> bool:
    """Whether a model runs on this computer (Ollama, or a custom endpoint marked local)."""
    from row_bot.providers.catalog import provider_billing
    from row_bot.providers.selection import parse_model_ref

    parsed = parse_model_ref(ref)
    return bool(parsed) and provider_billing(parsed[0]) == "local"


@dataclass(frozen=True)
class Plan:
    main_ref: str
    backup_ref: str


def plan(config: Mapping[str, Any]) -> Plan | None:
    """The backup for this turn, or None when it has none."""
    backup = backup_model()
    if not backup:
        return None
    from row_bot.providers.selection import model_choice_value

    configurable = config.get("configurable") or {}
    main = str(configurable.get("model_override") or "")
    if not main:
        from row_bot.models import get_current_model

        main = get_current_model() or ""
    main, backup = model_choice_value(main), model_choice_value(backup)
    if not main or main == backup or _PERSONAL.search(main):
        return None  # Learning's personal models finish their own turns
    thread_id = str(configurable.get("thread_id") or "")
    if thread_id and not is_local(backup):
        from row_bot.threads import incognito_state

        if incognito_state(thread_id).incognito:
            return None  # an incognito chat never goes to a cloud backup
    return Plan(main, backup)


# ── The turn ────────────────────────────────────────────────────────────────


def _quoted(text: str, limit: int = 4_000) -> str:
    clean = str(text or "").strip()
    if len(clean) > limit:
        clean = clean[:limit].rstrip() + " …"
    return "\n".join(f"> {line}" if line else ">" for line in clean.splitlines()) or "> (the request above)"


def handover_config(config: Mapping[str, Any], text: str, why: str, line: str, backup_ref: str) -> dict:
    """The turn's configuration on the backup model, starting with the hidden hand-over."""
    handed = copy.deepcopy(dict(config))
    configurable = handed.setdefault("configurable", {})
    configurable.pop("reasoning_snapshot", None)  # the main model's thinking choice may not fit
    configurable.update({
        "model_override": backup_ref,
        "thread_event_context": True,
        "thread_event_new_turn": True,
        "root_objective": str(text or configurable.get("root_objective") or "")[:20_000],
        # In the person's place but never their words (memory, the reviewer and Learning skip it).
        "thread_event_messages": [{
            "role": "human", "content": _HANDOVER.format(why=why, request=_quoted(text)),
            "row_bot_ui": {"backup_model": {"text": line}},
        }],
    })
    return handed


def _neither(main_name: str, backup_name: str, message: Any) -> str:
    detail = re.sub(r"^\s*(?:⚠️?\s*)+", "", str(message or "")).split(" — ")[0].strip().rstrip(".")
    return f"⚠️ Neither {main_name} nor {backup_name} answered — {detail or 'try again'}."


def stream(main: Callable[[], Iterator[tuple]], backup: Callable[[dict], Iterator[tuple]],
           config: Mapping[str, Any], text: str, *, stop_event: threading.Event | None = None,
           notify: Callable[[str], None] = lambda message: None,
           answer_open_calls: Callable[[str, str], Any] | None = None) -> Iterator[tuple]:
    """Run the turn on its own model; after an outage-shaped failure, the backup finishes it.

    ``main()`` streams the turn; ``backup(config)`` streams a turn from the given configuration.
    """
    chosen = plan(config)
    if chosen is None:
        yield from main()
        return
    turn, previous = _Turn(), _TURN.get()
    held: list[tuple] = []
    raised: BaseException | None = None
    _TURN.set(turn)
    try:
        try:
            for event in main():
                if event[0] in {"error", "provider_refusal"}:
                    held.append(event)  # shown only if the backup doesn't answer
                    continue
                yield event
        except InterruptedError:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed model is the backup's case, else raised below
            turn.failures.append(exc)
            raised = exc
    finally:
        _TURN.set(previous)
    stopped = stop_event is not None and stop_event.is_set()
    failed = raised is not None or any(event[0] == "error" for event in held)
    why = reason(turn.failures[-1]) if failed and turn.failures and not stopped else None
    if why is None:
        if failed and not stopped:
            # Visible next time: why a turn that had a backup ended in its error.
            cause = (f"{failure_kind(turn.failures[-1])}: {turn.failures[-1]}" if turn.failures
                     else "an error the backup doesn't cover")
            logger.warning("The backup model didn't take over (%s)", str(cause)[:300])
        for message in turn.notices:
            notify(message)
        if raised is not None:
            raise raised
        yield from held
        return
    configurable = config.get("configurable") or {}
    thread_id = str(configurable.get("thread_id") or "")
    if thread_id:
        try:
            (answer_open_calls or _answer_open_calls)(thread_id, "The main model stopped here.")
        except Exception:  # noqa: BLE001 - nothing open is the usual case
            logger.debug("The main model's open steps could not be closed", exc_info=True)
    main_name, backup_name = model_name(chosen.main_ref), model_name(chosen.backup_ref)
    line = note(main_name, backup_name, why)
    logger.info("The main model didn't answer (%s); the backup model takes over", failure_kind(turn.failures[-1]))
    yield (EVENT, {"note": line, "reason": str(failure_kind(turn.failures[-1])), "model_ref": chosen.backup_ref})
    for event in backup(handover_config(config, text, why, line, chosen.backup_ref)):
        yield ("error", _neither(main_name, backup_name, event[1])) if event[0] == "error" else event


def _answer_open_calls(thread_id: str, why: str) -> None:
    from row_bot import threads

    threads.answer_open_tool_calls(thread_id, why)


__all__ = ["EVENT", "MainModelNotReady", "Plan", "backup_model", "failure_kind", "handover_config",
           "hold_notice", "is_local", "model_name", "note", "note_failure", "plan", "read_setting", "reason",
           "save_setting", "stream"]
