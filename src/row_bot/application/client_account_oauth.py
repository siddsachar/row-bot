"""Explicit local-owner Google and X account setup over existing OAuth owners."""

from __future__ import annotations

import hashlib
import json
from threading import RLock, Thread
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import UUID

from row_bot import account_tokens
from row_bot.application.client_platform import ClientPlatformError

_LOCK = RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE: set[str] = set()


def _account(account: str) -> str:
    if account not in {"google", "x"}:
        raise ClientPlatformError("invalid_account_command")
    return account


def read_account_auth(*, account: str) -> dict[str, Any]:
    """Read the keychain's saved sign-in and remembered checks only; no provider or refresh call."""
    _account(account)
    if account == "google":
        configured = account_tokens.google_client() is not None
    else:
        from row_bot.api_keys import get_key
        configured = bool(get_key("X_CLIENT_ID") and get_key("X_CLIENT_SECRET"))
    signed_in = account_tokens.digest(account)
    state = "not_configured" if not configured else account_tokens.state(account)
    revision = hashlib.sha256(json.dumps({
        "account": account, "configured": configured, "token": signed_in,
        "client": account_tokens.digest("google_client") if account == "google" else None,
    }, sort_keys=True).encode()).hexdigest()
    return {
        "schema_version": 1, "account": account, "revision": revision,
        "configured": configured, "state": state,
        "token_files": int(bool(signed_in)),  # Saved sign-ins (in the keychain); the name predates that.
    }


def _install_google_credentials(raw: str) -> None:
    if not raw or len(raw.encode("utf-8")) > 65536:
        raise ClientPlatformError("account_credentials_invalid")
    try:
        parsed = json.loads(raw)
        client = parsed.get("installed")
        if not isinstance(client, dict) or any(not isinstance(client.get(key), str) or not client[key] for key in (
            "client_id", "client_secret", "auth_uri", "token_uri",
        )):
            raise ValueError
        auth = urlparse(client["auth_uri"])
        token = urlparse(client["token_uri"])
        # Google's own hosts only; older client files name the token endpoint on accounts.google.com. No
        # backslash, user or port: parsers disagree about where such an address goes.
        if (auth.scheme != "https" or auth.hostname != "accounts.google.com" or token.scheme != "https"
                or token.hostname not in {"oauth2.googleapis.com", "accounts.google.com"}
                or any(ch in client[key] for key in ("auth_uri", "token_uri") for ch in ("\\", "@"))
                or any(part.port is not None or part.username is not None for part in (auth, token))):
            raise ValueError
        # What is saved names Google's current endpoints, whatever the file spelled.
        client.update(auth_uri="https://accounts.google.com/o/oauth2/auth", token_uri="https://oauth2.googleapis.com/token")
        redirects = client.get("redirect_uris")
        if not isinstance(redirects, list) or not any(
            isinstance(value, str) and value in {"http://localhost", "http://127.0.0.1"}
            for value in redirects
        ):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ClientPlatformError("account_credentials_invalid") from None
    try:
        account_tokens.write("google_client", parsed)  # The client's secret too: keychain only.
    except account_tokens.AccountTokenError:
        raise ClientPlatformError("account_secure_storage_unavailable") from None
    from row_bot.tools import registry
    registry.set_tool_config("gmail", "credentials_path", "")  # Saved by Row-Bot now, not a file of the person's.
    registry.set_tool_config("calendar", "credentials_path", "")


def _google_flow() -> bytes:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from row_bot.tools.gmail_tool import GMAIL_SCOPES
    from row_bot.tools.calendar_tool import CALENDAR_SCOPES

    client = account_tokens.google_client()
    if client is None:
        raise ClientPlatformError("account_credentials_required")
    flow = InstalledAppFlow.from_client_config(client, GMAIL_SCOPES + CALENDAR_SCOPES)
    credentials = flow.run_local_server(host="127.0.0.1", port=0, timeout_seconds=180)
    return credentials.to_json().encode()


def _x_flow() -> bytes:
    from row_bot.api_keys import get_key
    from row_bot.tools.x_tool import _run_oauth_flow

    token = _run_oauth_flow(get_key("X_CLIENT_ID"), get_key("X_CLIENT_SECRET"), persist=False)
    return json.dumps(token, separators=(",", ":")).encode()


def _receipt(command_id: str, account: str, action: str, phase: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "command_id": command_id, "account": account,
        "action": action, "phase": phase, "message": message[:256],
        "snapshot": read_account_auth(account=account),
    }


def _finish_auth(job_key: str) -> None:
    with _LOCK:
        job = _JOBS.get(job_key)
    if not job:
        return
    account = job["account"]
    command_id = job["command_id"]
    try:
        content = _google_flow() if account == "google" else _x_flow()
        with _LOCK:
            cancelled = job["cancelled"]
            if not cancelled:
                job["validate"]()
                account_tokens.write(account, json.loads(content))  # Keychain only; a failure keeps the old sign-in.
                account_tokens.record_check(account, "valid")  # The provider has just issued it.
        phase = "cancelled" if cancelled else "completed"
        message = "Authentication cancelled." if cancelled else "Account authorization saved."
    except Exception:
        with _LOCK:
            cancelled = job["cancelled"]
        phase = "cancelled" if cancelled else "failed"
        message = "Authentication cancelled." if cancelled else "Authentication failed. Existing tokens were kept. Retry from this account."
    with _LOCK:
        try:
            job["receipt"] = _receipt(command_id, account, "start", phase, message)
        finally:
            _ACTIVE.discard(account)


def execute_account_auth(
    *, owner_id: str, command_id: str, account: str, action: str,
    expected_revision: str, confirmed: bool = False, credentials_json: str = "",
    validate: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Accept an exact one-shot account action; OAuth runs in a background job."""
    try:
        if str(UUID(command_id)) != command_id:
            raise ValueError
    except ValueError:
        raise ClientPlatformError("invalid_account_command") from None
    if action not in {"import_credentials", "check", "start", "disconnect"} or account not in {"google", "x"}:
        raise ClientPlatformError("invalid_account_command")
    key = f"{owner_id}:{command_id}"
    with _LOCK:
        prior = _JOBS.get(key)
        if prior:
            if prior["account"] != account or prior["action"] != action or prior["expected_revision"] != expected_revision:
                raise ClientPlatformError("account_command_conflict")
            return prior["receipt"]
        if account in _ACTIVE:
            raise ClientPlatformError("account_busy")
        current = read_account_auth(account=account)
        if current["revision"] != expected_revision:
            raise ClientPlatformError("account_changed")
        if action == "start" and not current["configured"]:
            raise ClientPlatformError("account_credentials_required")
        if action == "disconnect" and not confirmed:
            raise ClientPlatformError("account_confirmation_required")
        if action == "import_credentials" and account != "google":
            raise ClientPlatformError("invalid_account_command")
        _ACTIVE.add(account)
        if len(_JOBS) >= 64:
            finished = next((item for item, value in _JOBS.items() if value["receipt"] and value["receipt"]["phase"] not in {"running", "cancel_requested"}), None)
            if finished:
                _JOBS.pop(finished)
        job = {"owner_id": owner_id, "command_id": command_id, "account": account, "action": action,
               "expected_revision": expected_revision, "cancelled": False, "receipt": None,
               "validate": validate}
        _JOBS[key] = job
    try:
        if action == "start":
            job["receipt"] = _receipt(command_id, account, action, "running", "Complete sign-in in the browser on this computer.")
            Thread(target=_finish_auth, args=(key,), daemon=True, name=f"row-bot-{account}-auth").start()
            return job["receipt"]
        if action == "import_credentials":
            validate()
            _install_google_credentials(credentials_json)
            phase, message = "completed", "Google credentials saved locally."
        elif action == "disconnect":
            validate()
            account_tokens.delete(account)
            phase, message = "completed", "Local account tokens removed. Provider authorization may still exist."
        else:
            validate()
            # Each check remembers its verdict for Settings › Accounts.
            if account == "google":
                statuses = [account_tokens.check_google()[0]]  # Gmail and Calendar share one sign-in.
            else:
                from row_bot.tools.x_tool import XTool
                statuses = [XTool().check_token_health()[0]]
            if all(status in {"valid", "refreshed"} for status in statuses):
                phase, message = "completed", "Account token is healthy."
            elif any(status in {"expired", "missing"} for status in statuses):
                phase, message = "failed", "Account token needs authentication. Reconnect this account."
            else:
                phase, message = "failed", "The account token could not be checked. Try again later."
        job["receipt"] = _receipt(command_id, account, action, phase, message)
        return job["receipt"]
    except Exception:
        with _LOCK:
            _JOBS.pop(key, None)
            _ACTIVE.discard(account)
        raise
    finally:
        if action != "start":
            with _LOCK:
                _ACTIVE.discard(account)


def read_account_auth_receipt(*, owner_id: str, command_id: str) -> dict[str, Any]:
    with _LOCK:
        job = _JOBS.get(f"{owner_id}:{command_id}")
        if job and job["receipt"]:
            return job["receipt"]
    raise ClientPlatformError("account_receipt_missing")


def cancel_account_auth(*, owner_id: str, command_id: str) -> dict[str, Any]:
    with _LOCK:
        job = _JOBS.get(f"{owner_id}:{command_id}")
        if not job or job["action"] != "start" or not job["receipt"]:
            raise ClientPlatformError("account_receipt_missing")
        if job["receipt"]["phase"] == "running":
            job["cancelled"] = True
            job["receipt"] = _receipt(command_id, job["account"], "start", "cancel_requested", "Sign-in cancellation requested; the callback window may remain until it times out.")
        return job["receipt"]
