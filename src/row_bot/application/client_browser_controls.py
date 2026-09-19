"""Path-free managed-browser status and reviewed client commands.

This service is deliberately smaller than the agent Browser tool.  It exposes
only operations for which the managed browser already owns a stable,
conversation-scoped authority.  Page target tokens, typed values, screenshots,
and tab identities stay private until they have dedicated client contracts.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
from typing import Any
from urllib.parse import urlparse, urlunparse
import uuid

from row_bot.application.client_platform import ClientPlatformError
from row_bot.browser.policy import navigation_policy
from row_bot.runtime import admissions


_ACTIONS = frozenset(
    {
        "browser.navigate",
        "browser.take_over",
        "browser.check",
        "browser.back",
        "browser.end",
    }
)
_POLICY_ACTION = {
    "browser.navigate": "browser_navigate",
    "browser.take_over": "browser_snapshot",
    "browser.check": "browser_snapshot",
    "browser.back": "browser_back",
    "browser.end": "browser_snapshot",
}
_UNAVAILABLE = {
    "browser.click": "exact_page_target_contract_required",
    "browser.type": "exact_page_target_and_hidden_text_contract_required",
    "browser.scroll": "semantic_page_observation_contract_required",
    "browser.tab": "owned_tab_identity_contract_required",
    "browser.screenshot": "private_preview_export_unavailable",
    "browser.external.attach": "use_computer_use_for_external_browser",
}
_REVISION = re.compile(r"[0-9a-f]{64}")
_LOCK = threading.RLock()


class ClientBrowserControlError(ClientPlatformError):
    """Stable, client-safe managed-browser error."""


def _error(code: str, revision: str | None = None) -> ClientBrowserControlError:
    return ClientBrowserControlError(code, current_revision=revision)


def _text(value: object, maximum: int, *, required: bool = False) -> str:
    if type(value) is not str:
        raise _error("invalid_browser_command")
    try:
        if len(value.encode("utf-8")) > maximum or any(
            ord(char) < 32 for char in value
        ):
            raise ValueError
    except (UnicodeError, ValueError):
        raise _error("invalid_browser_command") from None
    value = value.strip()
    if required and not value:
        raise _error("invalid_browser_command")
    return value


def _identifier(value: object) -> str:
    return _text(value, 128, required=True)


def _uuid(value: object) -> str:
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise _error("invalid_browser_command") from None
    return value


def _public_url(value: object) -> str:
    """Return an HTTP(S) origin/path without query, fragment, or credentials."""

    try:
        text = _text(value, 8192)
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))[:2048]
    except (ClientBrowserControlError, ValueError):
        return ""


def _safe_status(
    raw: object, conversation_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw, dict):
        raise _error("browser_status_unavailable")
    if (
        raw.get("engine", "browser") != "browser"
        or raw.get("surface", "browser") != "browser"
    ):
        raise _error("browser_status_unavailable")
    raw_thread_id = raw.get("thread_id", conversation_id)
    if raw_thread_id != conversation_id:
        raise _error("browser_status_unavailable")
    active = raw.get("active") is True
    paused = active and raw.get("paused") is True
    state = raw.get("state")
    if state not in {
        "idle",
        "acting",
        "observing",
        "waiting_user",
        "waiting_approval",
        "needs_attention",
    }:
        state = "needs_attention" if active else "idle"
    if not active:
        state = "idle"
    raw_url = raw.get("url", "")
    if type(raw_url) is not str or len(raw_url.encode("utf-8")) > 8192:
        raise _error("browser_status_unavailable")
    site = ""
    public_url = _public_url(raw_url) if active else ""
    if public_url:
        site = (urlparse(public_url).hostname or "")[:120]
    try:
        last_action = _text(raw.get("last_action", ""), 160)
    except ClientBrowserControlError:
        raise _error("browser_status_unavailable") from None
    try:
        activity_revision = int(raw.get("revision", 0))
        if activity_revision < 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise _error("browser_status_unavailable") from None
    revision_proof = {
        "conversation_id": conversation_id,
        "active": active,
        "paused": paused,
        "state": state,
        "url": public_url,
        "last_action": last_action,
        "activity_revision": activity_revision,
    }
    revision = hashlib.sha256(
        json.dumps(revision_proof, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    proof = {**revision_proof, "raw_url": raw_url}
    public = {
        "schema_version": 1,
        "conversation_id": conversation_id,
        "revision": revision,
        "active": active,
        "paused": paused,
        "state": state,
        "site": site,
        "url": public_url,
        "last_action": last_action,
    }
    return public, proof


class CanonicalBrowserControlBackend:
    """Thin access to the existing managed-browser owner."""

    def __init__(self, manager: Any | None = None) -> None:
        self._injected_manager = manager

    def _manager(self) -> Any:
        if self._injected_manager is not None:
            return self._injected_manager
        # Lazy import avoids creating the tool registry during passive module import.
        from row_bot.tools.browser_tool import get_session_manager

        return get_session_manager()

    def status(self, conversation_id: str) -> dict[str, Any]:
        manager = self._manager()
        # BrowserSessionManager.status_snapshot creates a throwaway service when
        # no shared session exists.  Avoid that constructor on passive reads.
        if not manager.has_active_session():
            return {
                "engine": "browser",
                "surface": "browser",
                "active": False,
                "paused": False,
                "thread_id": conversation_id,
                "state": "idle",
                "url": "",
                "last_action": "",
                "revision": 0,
            }
        return dict(manager.status_snapshot(conversation_id))

    def execute(
        self, action: str, payload: dict[str, Any], conversation_id: str
    ) -> None:
        manager = self._manager()
        if action == "browser.navigate":
            session = manager.get_session(conversation_id)
            outcome = session.navigate(payload["url"], conversation_id)
            self._confirmed(outcome)
            from row_bot.browser.history import append_browser_history

            append_browser_history(
                conversation_id,
                {
                    "action": "navigate",
                    "url": _public_url(payload["url"]),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        elif action == "browser.take_over":
            if not manager.take_over(conversation_id):
                raise _error("browser_window_unavailable")
        elif action == "browser.check":
            if not manager.has_active_session():
                raise _error("browser_session_inactive")
            self._confirmed(
                manager.get_session(conversation_id).snapshot(conversation_id)
            )
        elif action == "browser.back":
            if not manager.has_active_session():
                raise _error("browser_session_inactive")
            outcome = manager.get_session(conversation_id).go_back(conversation_id)
            self._confirmed(outcome)
            from row_bot.browser.history import append_browser_history

            append_browser_history(
                conversation_id,
                {
                    "action": "back",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        elif action == "browser.end":
            if not manager.has_active_session():
                raise _error("browser_session_inactive")
            manager.end_activity(conversation_id)
        else:
            raise _error("browser_action_unavailable")

    @staticmethod
    def _confirmed(outcome: object) -> None:
        if type(outcome) is not str:
            raise _error("browser_action_failed")
        normalized = outcome.lstrip().casefold()
        if normalized.startswith(("error", "blocked")):
            raise _error("browser_action_failed")


def _backend(value: Any | None) -> Any:
    return value if value is not None else CanonicalBrowserControlBackend()


def _availability(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    active = snapshot["active"]
    values: dict[str, tuple[str, str | None]] = {
        "browser.navigate": ("available" if active else "check_on_use", None),
        "browser.take_over": (
            "check_on_use" if active else "unavailable",
            None if active else "browser_session_inactive",
        ),
        "browser.check": (
            "available" if active else "unavailable",
            None if active else "browser_session_inactive",
        ),
        "browser.back": (
            "available" if active else "unavailable",
            None if active else "browser_session_inactive",
        ),
        "browser.end": (
            "available" if active else "unavailable",
            None if active else "browser_session_inactive",
        ),
    }
    result = {
        action: {"state": state, "code": code}
        for action, (state, code) in values.items()
    }
    result.update(
        {
            action: {"state": "unavailable", "code": code}
            for action, code in _UNAVAILABLE.items()
        }
    )
    return result


def _snapshot(
    conversation_id: str, validate: Callable[[], None], backend: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    validate()
    conversation_id = _identifier(conversation_id)
    public, proof = _safe_status(backend.status(conversation_id), conversation_id)
    public["availability"] = _availability(public)
    validate()
    return public, proof


def read_browser_controls(
    conversation_id: str,
    *,
    validate: Callable[[], None],
    backend: Any | None = None,
) -> dict[str, Any]:
    """Read sanitized activity without launching or observing a page."""

    return _snapshot(conversation_id, validate, _backend(backend))[0]


def _normalize(
    action: str,
    payload: dict[str, Any],
    snapshot: dict[str, Any],
    proof: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    if not _REVISION.fullmatch(str(payload.get("revision", ""))):
        raise _error("invalid_browser_command")
    if payload["revision"] != snapshot["revision"]:
        raise _error("browser_revision_conflict", snapshot["revision"])
    availability = snapshot["availability"][action]
    if availability["state"] == "unavailable":
        raise _error(availability["code"] or "browser_action_unavailable")
    normalized: dict[str, Any] = {"revision": payload["revision"]}
    if action == "browser.navigate":
        if set(payload) != {"revision", "url"}:
            raise _error("invalid_browser_command")
        url = _text(payload.get("url"), 8192, required=True)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise _error("invalid_browser_url")
        decision, reason = navigation_policy(url, str(proof.get("raw_url") or ""))
        if decision == "block":
            raise _error("browser_navigation_denied")
        normalized["url"] = url
        return normalized, decision, _text(reason, 512)
    if set(payload) != {"revision"}:
        raise _error("invalid_browser_command")
    return normalized, "ask", "This live-control action requires explicit confirmation."


def _review(
    action: str,
    payload: dict[str, Any],
    conversation_id: str,
    validate: Callable[[], None],
    backend: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if action in _UNAVAILABLE:
        raise _error(_UNAVAILABLE[action])
    if action not in _ACTIONS or not isinstance(payload, dict):
        raise _error("invalid_browser_command")
    snapshot, proof = _snapshot(conversation_id, validate, backend)
    normalized, policy_decision, policy_reason = _normalize(
        action, payload, snapshot, proof
    )
    # The exact URL, including a possible sensitive query, must be bound without
    # exposing a reusable plain SHA-256 oracle to the browser client.
    digest = admissions.keyed_digest(
        {
            "kind": "browser-control-review",
            "action": action,
            "conversation_id": conversation_id,
            "payload": normalized,
        }
    )
    disclosures = {
        "browser.navigate": [
            "The managed browser will open this address using its local profile.",
            "Query values are included in the exact command but omitted from saved history and status.",
        ],
        "browser.take_over": [
            "The managed browser window will move to the foreground and automation will pause for you."
        ],
        "browser.check": [
            "The managed browser will read the current page for agent use; page content is not returned to this panel."
        ],
        "browser.back": [
            "The managed tab will navigate to its previous history entry."
        ],
        "browser.end": [
            "This conversation's live-control activity and preview will end; the shared browser profile remains local."
        ],
    }[action]
    review = {
        "schema_version": 1,
        "action": action,
        "conversation_id": conversation_id,
        "revision": snapshot["revision"],
        "policy_action": _POLICY_ACTION[action],
        "policy_decision": policy_decision,
        "policy_reason": policy_reason,
        "approval_required": True,
        "origin_and_path": (
            _public_url(normalized["url"])
            if action == "browser.navigate"
            else snapshot["url"]
        ),
        "query_present": bool(
            action == "browser.navigate" and urlparse(normalized["url"]).query
        ),
        "disclosures": disclosures,
        "action_digest": digest,
    }
    return review, normalized


def review_browser_command(
    action: str,
    payload: dict[str, Any],
    conversation_id: str,
    *,
    validate: Callable[[], None],
    backend: Any | None = None,
) -> dict[str, Any]:
    """Review one exact managed-browser command without performing it."""

    validate()
    result = _review(
        action,
        deepcopy(payload),
        _identifier(conversation_id),
        validate,
        _backend(backend),
    )[0]
    validate()
    return result


def _scope(
    owner_id: str,
    authority_id: str,
    conversation_id: str,
    *,
    read_only: bool = False,
) -> str:
    return admissions.keyed_digest(
        {
            "kind": "browser-control",
            "owner_id": owner_id,
            "authority_id": authority_id,
            "conversation_id": conversation_id,
        },
        read_only=read_only,
    )


def _public_receipt(saved: dict[str, Any]) -> dict[str, Any]:
    result = saved.get("result")
    if not isinstance(result, dict) or set(result) != {
        "schema_version",
        "command_id",
        "action",
        "conversation_id",
        "status",
        "code",
        "revision",
        "browser_control",
    }:
        raise _error("browser_receipt_unavailable")
    return deepcopy(result)


def read_browser_receipt(
    *,
    owner_id: str,
    authority_id: str,
    conversation_id: str,
    command_id: str,
    validate: Callable[[], None],
) -> dict[str, Any]:
    """Read one original receipt without launching or observing the browser."""

    validate()
    owner_id = _identifier(owner_id)
    authority_id = _identifier(authority_id)
    conversation_id = _identifier(conversation_id)
    command_id = _uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    saved = admissions.read_command_receipt(owner_id, command_id)
    private = saved.get("_browser_control") if isinstance(saved, dict) else None
    if (
        not metadata
        or metadata["target"] != f"browser-control:{conversation_id}"
        or metadata["type"] not in _ACTIONS
        or not isinstance(private, dict)
        or private.get("scope")
        != _scope(owner_id, authority_id, conversation_id, read_only=True)
    ):
        raise _error("browser_receipt_unavailable")
    result = _public_receipt(saved)
    validate()
    return result


def execute_browser_command(
    command: dict[str, Any],
    conversation_id: str,
    *,
    owner_id: str,
    authority_id: str,
    key: str,
    validate: Callable[[], None],
    validate_action: Callable[[str], None],
    validate_review: Callable[[dict[str, Any], dict[str, Any]], None],
    backend: Any | None = None,
) -> dict[str, Any]:
    """Execute exactly one reviewed browser command; retries read its receipt."""

    validate()
    backend = _backend(backend)
    command = deepcopy(command)
    conversation_id = _identifier(conversation_id)
    owner_id = _identifier(owner_id)
    authority_id = _identifier(authority_id)
    key = _identifier(key)
    command_id = _uuid(command.get("command_id"))
    action, raw = command.get("type"), command.get("payload")
    if (
        action not in _ACTIONS
        or not isinstance(raw, dict)
        or type(raw.get("nonce")) is not str
    ):
        raise _error("invalid_browser_command")
    payload = {name: value for name, value in raw.items() if name != "nonce"}
    target = f"browser-control:{conversation_id}"
    with _LOCK:
        if admissions.read_command_metadata(owner_id, command_id) is not None:
            try:
                admissions.claim_command(owner_id, key, command, target)
            except admissions.AdmissionError as exc:
                if str(exc) != "operation_uncertain":
                    raise _error(str(exc)) from None
            return read_browser_receipt(
                owner_id=owner_id,
                authority_id=authority_id,
                conversation_id=conversation_id,
                command_id=command_id,
                validate=validate,
            )
        review, normalized = _review(
            action, payload, conversation_id, validate, backend
        )

        def authority() -> None:
            validate()
            validate_action(review["policy_action"])
            validate_review(command, review)

        authority()
        result: dict[str, Any] = {
            "schema_version": 1,
            "command_id": command_id,
            "action": action,
            "conversation_id": conversation_id,
            "status": "partial",
            "code": "browser_outcome_uncertain",
            "revision": None,
            "browser_control": None,
        }
        progress = {
            "result": result,
            "_browser_control": {
                "scope": _scope(owner_id, authority_id, conversation_id),
                "action_digest": review["action_digest"],
            },
        }
        try:
            prior = admissions.claim_command(
                owner_id,
                key,
                command,
                target,
                exclusive_target=True,
                initial_result=progress,
            )
        except admissions.AdmissionError as exc:
            if str(exc) != "operation_uncertain":
                raise _error(str(exc)) from None
            return read_browser_receipt(
                owner_id=owner_id,
                authority_id=authority_id,
                conversation_id=conversation_id,
                command_id=command_id,
                validate=validate,
            )
        if prior is not None:
            return _public_receipt(prior)
        try:
            authority()
            # Revalidate the exact activity revision immediately before entering
            # the canonical browser owner's serialized operation.
            current, _proof = _snapshot(conversation_id, validate, backend)
            if current["revision"] != review["revision"]:
                raise _error("browser_revision_conflict", current["revision"])
            backend.execute(action, normalized, conversation_id)
            authority()
            current = read_browser_controls(
                conversation_id, validate=validate, backend=backend
            )
            result.update(
                status="completed",
                code=None,
                revision=current["revision"],
                browser_control=current,
            )
        except ClientBrowserControlError as exc:
            result.update(
                status="partial" if exc.code == "browser_action_failed" else "rejected",
                code=exc.code,
            )
        except Exception:
            result.update(status="partial", code="browser_outcome_uncertain")
        admissions.complete_command(owner_id, key, progress)
        return deepcopy(result)
