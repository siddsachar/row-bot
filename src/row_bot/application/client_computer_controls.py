"""Computer use in the conversation: status, the latest picture, Stop/Pause/Resume.

One computer-use session exists at a time, and only the conversation whose
turn holds it sees it. The picture stays in memory: it is read from the
session on request and is never written to disk, logged or kept in a command
receipt. Pause hands the computer to the person (the NiceGUI "Take over");
Resume starts again from a fresh capture before the paused turn goes on;
Stop releases the computer, ends the turn and withdraws its waiting pause.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from contextlib import closing
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import logging
import re
import sqlite3
import sys
from typing import Any
import uuid

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions


ACTIONS = frozenset({"computer_use.stop", "computer_use.pause", "computer_use.resume"})
_REVISION = re.compile(r"[0-9a-f]{64}")
_MAX_PICTURE_BYTES = 1_500_000
_PICTURE_TYPES = frozenset({"image/png", "image/jpeg"})
# Session states in which Row-Bot is using the computer and Pause can apply.
_PAUSABLE = frozenset({"ready", "acquiring", "observing", "acting", "verifying"})
_LOG = logging.getLogger(__name__)


class ClientComputerControlError(ClientPlatformError):
    """Stable, client-safe computer-use error."""


def _error(code: str, revision: str | None = None) -> ClientComputerControlError:
    return ClientComputerControlError(code, current_revision=revision)


def _service(computer: Any | None) -> Any | None:
    """The computer-use service, only when something could have started it.

    A passive read must not construct the service: when its module was never
    loaded, no session can exist.
    """

    if computer is not None:
        return computer
    module = sys.modules.get("row_bot.computer_use.service")
    return module.get_computer_use_service() if module is not None else None


def _status(computer: Any | None) -> dict[str, Any]:
    raw = computer.status_snapshot() if computer is not None else {}
    return raw if isinstance(raw, dict) else {}


def _belongs(raw: dict[str, Any], conversation_id: str) -> bool:
    return raw.get("active") is True and raw.get("thread_id") == conversation_id


def _plain(value: object, maximum: int) -> str:
    text = str(value or "").strip()
    return "".join(character for character in text if ord(character) >= 32)[:maximum]


def _identifier(value: object) -> str:
    if type(value) is not str or not value or len(value) > 128:
        raise _error("invalid_computer_use_command")
    return value


def _uuid(value: object) -> str:
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise _error("invalid_computer_use_command") from None
    return value


def pause_item(interrupt: object) -> dict[str, Any] | None:
    """The computer-use pause ("computer_takeover") an interrupt waits on."""

    items = interrupt if isinstance(interrupt, list) else [interrupt]
    return next(
        (
            item
            for item in items
            if isinstance(item, dict)
            and item.get("tool") == "computer_use"
            and item.get("kind") == "computer_takeover"
        ),
        None,
    )


def approval_context(raw: object) -> dict[str, Any]:
    try:
        context = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return context if isinstance(context, dict) else {}


def _computer_approvals(conversation_id: str) -> list[dict[str, Any]]:
    """This conversation's waiting computer-use approvals, newest first."""

    from row_bot.tasks import _get_conn

    try:
        with closing(_get_conn()) as conn:
            rows = conn.execute(
                "SELECT id, approval_payload_json FROM approval_requests "
                "WHERE source_thread_id=? AND resume_kind='conversation' AND status='pending' "
                "ORDER BY requested_at DESC, id",
                (conversation_id,),
            ).fetchall()
    except sqlite3.Error:
        return []
    found = []
    for row in rows:
        context = approval_context(row["approval_payload_json"])
        interrupt = context.get("interrupt")
        items = interrupt if isinstance(interrupt, list) else [interrupt]
        if not any(isinstance(item, dict) and item.get("tool") == "computer_use" for item in items):
            continue
        pause = pause_item(interrupt)
        found.append({
            "id": str(row["id"]),
            "pause": pause is not None,
            "generation_id": str(context.get("generation_id") or (pause or {}).get("generation_id") or ""),
        })
    return found


def _withdraw(approval_ids: list[str]) -> list[str]:
    """Withdraw waiting approvals whose question no longer applies."""

    if not approval_ids:
        return []
    from row_bot.tasks import _get_conn

    withdrawn = []
    with closing(_get_conn()) as conn:
        for approval_id in approval_ids:
            if conn.execute(
                "UPDATE approval_requests SET status='cancelled', responded_at=? "
                "WHERE id=? AND status='pending'",
                (datetime.now().isoformat(), approval_id),
            ).rowcount:
                withdrawn.append(approval_id)
        conn.commit()
    return withdrawn


def stop_computer_use(
    conversation_id: str, *, generation_id: str = "", computer: Any | None = None
) -> list[str]:
    """Release the conversation's computer and withdraw its computer approvals.

    With ``generation_id`` only that turn's session and approvals are touched.
    Returns the withdrawn approval ids.
    """

    service = _service(computer)
    if service is not None:
        if not generation_id:
            service.close_for_thread(conversation_id)
        else:
            raw = _status(service)
            if _belongs(raw, conversation_id) and raw.get("generation_id") == generation_id:
                service.stop()
    return _withdraw([
        approval["id"]
        for approval in _computer_approvals(conversation_id)
        if not generation_id or approval["generation_id"] == generation_id
    ])


def release_after_turn(
    conversation_id: str, generation_id: str, status: str, *, computer: Any | None = None
) -> None:
    """A finished turn gives the computer back; a turn waiting on you keeps it."""

    if status == "waiting_approval":
        return
    service = _service(computer)
    raw = _status(service)
    if _belongs(raw, conversation_id) and raw.get("generation_id") == generation_id:
        service.stop()


def prepare_approval_resume(
    platform: Any,
    conversation_id: str,
    context: dict[str, Any],
    *,
    approved: bool,
    runtime_surface: str = "normal_chat",
    computer: Any | None = None,
) -> str:
    """Get the computer ready before an approved turn goes on.

    A paused turn continues only after its computer is running again from a
    fresh capture (the order the NiceGUI live panel used). Returns the
    generation whose session the continuing turn keeps, or "".
    """

    pause = pause_item(context.get("interrupt"))
    service = _service(computer)
    generation_id = str(context.get("generation_id") or (pause or {}).get("generation_id") or "")
    if pause is not None and approved:
        if runtime_surface != "normal_chat":
            raise _error("computer_use_local_only")
        raw = _status(service)
        if not _belongs(raw, conversation_id) or (
            generation_id and raw.get("generation_id") != generation_id
        ):
            raise _error("computer_use_not_paused")
        if platform.registry.active(conversation_id):
            raise ClientPlatformError("generation_active")
        if raw.get("state") == "waiting_user":
            try:
                service.resume_from_local_ui()
            except Exception:
                # A failed resume releases the session itself.
                _LOG.info("Computer use could not resume a paused turn")
                raise _error("computer_use_resume_failed") from None
    raw = _status(service)
    if generation_id and _belongs(raw, conversation_id) and raw.get("generation_id") == generation_id:
        return generation_id
    return ""


def _state(raw: dict[str, Any], belongs: bool) -> str:
    state = str(raw.get("state") or "")
    if not belongs or state == "stopping":
        return "stopped"
    if state == "waiting_user":
        return "paused"
    if state == "waiting_approval":
        return "waiting_approval"
    if state in {"needs_attention", "failed"}:
        return "needs_attention"
    return "working"


def _picture(service: Any | None) -> tuple[bytes, str, int] | None:
    reader = getattr(service, "ephemeral_picture", None)
    picture = reader() if callable(reader) else None
    if (
        not isinstance(picture, tuple)
        or len(picture) != 3
        or not isinstance(picture[0], bytes)
        or not picture[0]
        or len(picture[0]) > _MAX_PICTURE_BYTES
        or picture[1] not in _PICTURE_TYPES
    ):
        return None
    return picture


def _snapshot(
    platform: Any, conversation_id: str, service: Any | None
) -> tuple[dict[str, Any], tuple[bytes, str, int] | None]:
    raw = _status(service)
    belongs = _belongs(raw, conversation_id)
    state = _state(raw, belongs)
    picture = _picture(service) if belongs and state == "working" else None
    approvals = _computer_approvals(conversation_id)
    pause = next((approval for approval in approvals if approval["pause"]), None)
    raw_state = str(raw.get("state") or "")
    public: dict[str, Any] = {
        "schema_version": 1,
        "conversation_id": conversation_id,
        "active": belongs,
        "state": state,
        "app": _plain(raw.get("app"), 120) if belongs else "",
        "has_picture": picture is not None,
        "approval_id": pause["id"] if pause else None,
        "can_pause": belongs and raw_state in _PAUSABLE,
        "can_resume": belongs
        and raw_state == "waiting_user"
        and (pause is not None or bool(platform.registry.active(conversation_id))),
        "can_stop": belongs or bool(approvals),
    }
    try:
        activity = max(0, int(raw.get("revision") or 0))
    except (TypeError, ValueError, OverflowError):
        activity = 0
    proof = {
        **public,
        "activity_revision": activity,
        "picture_generation": picture[2] if picture else 0,
        "generation_id": str(raw.get("generation_id") or "") if belongs else "",
    }
    public["revision"] = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return public, picture


def read_computer_controls(
    platform: Any,
    conversation_id: str,
    *,
    validate: Callable[[], None],
    computer: Any | None = None,
) -> dict[str, Any]:
    """Read the card's state without starting, capturing or changing anything."""

    validate()
    result = _snapshot(platform, _identifier(conversation_id), _service(computer))[0]
    validate()
    return result


def read_computer_preview(
    platform: Any,
    conversation_id: str,
    expected_revision: str,
    *,
    validate: Callable[[], None],
    computer: Any | None = None,
) -> dict[str, Any]:
    """The latest picture for exactly the revision the card last read."""

    if type(expected_revision) is not str or not _REVISION.fullmatch(expected_revision):
        raise _error("invalid_computer_use_command")
    validate()
    conversation_id = _identifier(conversation_id)
    public, picture = _snapshot(platform, conversation_id, _service(computer))
    if public["revision"] != expected_revision:
        raise _error("computer_use_revision_conflict", public["revision"])
    state = (
        "inactive" if not public["active"]
        else "hidden" if public["state"] in {"paused", "waiting_approval"}
        else "available" if picture is not None
        else "waiting"
    )
    validate()
    return {
        "schema_version": 1,
        "conversation_id": conversation_id,
        "revision": expected_revision,
        "state": state,
        "mime_type": picture[1] if state == "available" else None,
        "image_base64": base64.b64encode(picture[0]).decode("ascii") if state == "available" else None,
    }


def _pause(conversation_id: str, service: Any | None) -> None:
    raw = _status(service)
    if not _belongs(raw, conversation_id):
        raise _error("computer_use_inactive")
    if str(raw.get("state") or "") not in _PAUSABLE:
        raise _error("computer_use_busy")
    try:
        token = service.take_over(
            thread_id=conversation_id, generation_id=str(raw.get("generation_id") or "")
        )
    except Exception:
        raise _error("computer_use_inactive") from None
    if not token:
        raise _error("computer_use_inactive")


def _resume(platform: Any, conversation_id: str, service: Any | None) -> None:
    raw = _status(service)
    if not _belongs(raw, conversation_id):
        raise _error("computer_use_inactive")
    if raw.get("state") != "waiting_user":
        raise _error("computer_use_not_paused")
    pause = next((item for item in _computer_approvals(conversation_id) if item["pause"]), None)
    if pause is None and not platform.registry.active(conversation_id):
        # Nothing is waiting to go on with the computer.
        raise _error("computer_use_resume_failed")
    # Resume the computer outside the platform's command lock: it restarts the
    # driver and captures, which must not hold up other conversations.
    try:
        service.resume_from_local_ui()
    except Exception:
        _LOG.info("Computer use could not resume from the card")
        raise _error("computer_use_resume_failed") from None
    if pause is None:
        return
    try:
        platform._resolve_approval(pause["id"], {"decision": "approve"})
    except ClientPlatformError:
        # The turn can't go on: don't leave the computer running for nobody.
        platform.stop_conversation(conversation_id)
        raise _error("computer_use_resume_failed") from None


def _perform(platform: Any, action: str, conversation_id: str, service: Any | None) -> None:
    if action == "computer_use.stop":
        # Stop is always safe: it releases the computer and ends the turn.
        platform.stop_conversation(conversation_id)
    elif action == "computer_use.pause":
        _pause(conversation_id, service)
    else:
        _resume(platform, conversation_id, service)


def _public_receipt(saved: object) -> dict[str, Any]:
    fields = {"schema_version", "command_id", "action", "conversation_id", "status", "code", "computer_use"}
    if not isinstance(saved, dict) or not fields <= set(saved):
        raise _error("computer_use_outcome_uncertain")
    return deepcopy({name: saved[name] for name in fields})


def execute_computer_command(
    platform: Any,
    command: dict[str, Any],
    conversation_id: str,
    *,
    owner_id: str,
    key: str,
    validate: Callable[[], None],
    computer: Any | None = None,
) -> dict[str, Any]:
    """Run one Stop, Pause or Resume; a repeated command reads its first outcome."""

    validate()
    conversation_id = _identifier(conversation_id)
    owner_id = _identifier(owner_id)
    command_id = _uuid(command.get("command_id"))
    action = command.get("type")
    if action not in ACTIONS or key != command_id:
        raise _error("invalid_computer_use_command")
    service = _service(computer)
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "command_id": command_id,
        "action": action,
        "conversation_id": conversation_id,
        "status": "partial",
        "code": "computer_use_outcome_uncertain",
        "computer_use": None,
    }
    try:
        prior = admissions.claim_command(
            owner_id,
            key,
            {"command_id": command_id, "type": action},
            f"computer-use:{conversation_id}",
            initial_result=receipt,
        )
    except admissions.AdmissionError as exc:
        if str(exc) != "operation_uncertain":
            raise _error(str(exc), exc.current_revision) from None
        # Interrupted before it finished: report that, never run it again.
        return _public_receipt(admissions.read_command_receipt(owner_id, command_id))
    if prior is not None:
        return _public_receipt(prior)
    try:
        validate()
    except Exception as exc:
        admissions.reject_command(owner_id, key, str(getattr(exc, "code", "authentication_required")))
        raise
    try:
        _perform(platform, action, conversation_id, service)
        receipt.update(status="completed", code=None)
    except ClientPlatformError as exc:
        receipt.update(status="rejected", code=exc.code)
    except Exception as exc:
        # Only the type: messages can carry app or window details.
        _LOG.warning("Computer-use %s did not finish cleanly (%s)", action, type(exc).__name__)
    try:
        receipt["computer_use"] = _snapshot(platform, conversation_id, _service(computer))[0]
    except Exception:
        _LOG.debug("Computer-use state after %s is unavailable", action, exc_info=True)
    admissions.complete_command(owner_id, key, receipt)
    return deepcopy(receipt)
