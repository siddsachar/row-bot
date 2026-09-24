"""Explicit local-owner Google and X account setup over existing OAuth owners."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock, Thread
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import UUID

from row_bot.application.client_platform import ClientPlatformError
from row_bot.data_paths import get_row_bot_data_dir

_LOCK = RLock()
_JOBS: dict[str, dict[str, Any]] = {}
_ACTIVE: set[str] = set()


def _root() -> Path:
    return get_row_bot_data_dir(create=False)


def _paths(account: str) -> tuple[Path, ...]:
    root = _root()
    if account == "google":
        return root / "gmail" / "token.json", root / "calendar" / "token.json"
    if account == "x":
        return (root / "x" / "token.json",)
    raise ClientPlatformError("invalid_account_command")


def _google_credentials_path() -> Path:
    root = _root()
    canonical = root / "gmail" / "credentials.json"
    try:
        raw = json.loads((root / "tools_config.json").read_text(encoding="utf-8"))
        configured = raw.get("tool_configs", {}).get("gmail", {}).get("credentials_path")
        if isinstance(configured, str) and configured:
            candidate = Path(configured).expanduser()
            if candidate.is_absolute():
                return candidate
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return canonical


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        state = path.stat()
        return state.st_size, state.st_mtime_ns
    except OSError:
        return None


def read_account_auth(*, account: str) -> dict[str, Any]:
    """Read local file metadata only; no provider or refresh call."""
    paths = _paths(account)
    if account == "google":
        configured = _google_credentials_path().is_file()
    else:
        from row_bot.api_keys import get_key
        configured = bool(get_key("X_CLIENT_ID") and get_key("X_CLIENT_SECRET"))
    states = [_stamp(path) for path in paths]
    present = sum(value is not None for value in states)
    state = "not_configured" if not configured else "not_authenticated" if not present else "saved_unchecked" if present == len(paths) else "partial"
    revision = hashlib.sha256(json.dumps({
        "account": account, "configured": configured,
        "credentials": _stamp(_google_credentials_path()) if account == "google" else None,
        "tokens": states,
    }, sort_keys=True).encode()).hexdigest()
    return {
        "schema_version": 1, "account": account, "revision": revision,
        "configured": configured, "state": state,
        "token_files": present,
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
        if auth.scheme != "https" or auth.hostname != "accounts.google.com" or token.scheme != "https" or token.hostname != "oauth2.googleapis.com":
            raise ValueError
        redirects = client.get("redirect_uris")
        if not isinstance(redirects, list) or not any(
            isinstance(value, str) and value in {"http://localhost", "http://127.0.0.1"}
            for value in redirects
        ):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ClientPlatformError("account_credentials_invalid") from None
    canonical = _root() / "gmail" / "credentials.json"
    _atomic_write(canonical, json.dumps(parsed, separators=(",", ":")).encode())
    from row_bot.tools import registry
    registry.set_tool_config("gmail", "credentials_path", str(canonical))
    registry.set_tool_config("calendar", "credentials_path", str(canonical))


def _google_flow() -> bytes:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from row_bot.tools.gmail_tool import GMAIL_SCOPES
    from row_bot.tools.calendar_tool import CALENDAR_SCOPES

    flow = InstalledAppFlow.from_client_secrets_file(
        str(_google_credentials_path()), GMAIL_SCOPES + CALENDAR_SCOPES,
    )
    credentials = flow.run_local_server(host="127.0.0.1", port=0, timeout_seconds=180)
    return credentials.to_json().encode()


def _x_flow() -> bytes:
    from row_bot.api_keys import get_key
    from row_bot.tools.x_tool import _run_oauth_flow

    token = _run_oauth_flow(get_key("X_CLIENT_ID"), get_key("X_CLIENT_SECRET"), persist=False)
    return json.dumps(token, separators=(",", ":")).encode()


def _write_tokens(account: str, content: bytes) -> None:
    paths = _paths(account)
    originals = [path.read_bytes() if path.is_file() else None for path in paths]
    written: list[int] = []
    try:
        for index, path in enumerate(paths):
            _atomic_write(path, content)
            written.append(index)
    except OSError:
        for index in reversed(written):
            original = originals[index]
            if original is None:
                paths[index].unlink(missing_ok=True)
            else:
                _atomic_write(paths[index], original)
        raise


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
                _write_tokens(account, content)
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
            for path in _paths(account):
                path.unlink(missing_ok=True)
            phase, message = "completed", "Local account tokens removed. Provider authorization may still exist."
        else:
            validate()
            if account == "google":
                from row_bot.tools.gmail_tool import _check_google_token as gmail_check
                from row_bot.tools.calendar_tool import _check_google_token as calendar_check
                checks = [gmail_check(str(_paths(account)[0])), calendar_check(str(_paths(account)[1]))]
                good = all(status in {"valid", "refreshed"} for status, _ in checks)
            else:
                from row_bot.tools.x_tool import XTool
                good = XTool().check_token_health()[0] in {"valid", "refreshed"}
            phase, message = "completed", "Account token is healthy." if good else "Account token needs authentication."
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
