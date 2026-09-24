"""Explicit local-owner account health actions over the existing account owners."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from threading import RLock
from typing import Any
from uuid import UUID

from row_bot.application.client_platform import ClientPlatformError
from row_bot.developer.executables import resolve_github_cli
from row_bot import github_account

_LOCK = RLock()
_GITHUB: dict[str, github_account.GitHubAccountStatus] = {}
_RECEIPTS: dict[str, dict[str, Any]] = {}
_ACTIVE: set[str] = set()


def _revision(status: github_account.GitHubAccountStatus, cli_installed: bool) -> str:
    return hashlib.sha256(json.dumps({
        "fingerprint": status.fingerprint,
        "state": status.state,
        "source": status.source,
        "cli_installed": cli_installed,
        "last_checked": status.last_checked,
    }, sort_keys=True).encode()).hexdigest()


def _github_status(owner_id: str) -> tuple[github_account.GitHubAccountStatus, bool]:
    passive = github_account.get_passive_github_account_status()
    with _LOCK:
        cached = _GITHUB.get(owner_id)
    if cached and cached.fingerprint == passive.fingerprint:
        status = cached
    else:
        status = passive
    return status, bool(resolve_github_cli())


def read_github_access(*, owner_id: str) -> dict[str, Any]:
    """Return saved and last explicitly checked state without network access."""
    status, cli_installed = _github_status(owner_id)
    state = status.state if status.state in {
        "not_configured", "configured_unchecked", "connected", "anonymous",
        "invalid_token", "rate_limited", "secondary_limited", "offline",
    } else "offline"
    rate = status.rate_limit
    return {
        "schema_version": 1,
        "revision": _revision(status, cli_installed),
        "state": state,
        "credential_source": status.source if status.source in {"environment", "keyring", "github_cli"} else "none",
        "connected": status.connected,
        "anonymous_ok": status.anonymous_ok,
        "cli_installed": cli_installed,
        "cli_authenticated": status.gh_authenticated,
        "remaining": max(0, rate.remaining) if rate else None,
        "retry_after_seconds": max(0, min(rate.retry_after_seconds, 86400)) if rate else None,
    }


def _start_cli(mode: str) -> None:
    executable = resolve_github_cli()
    if not executable:
        raise ClientPlatformError("github_cli_missing")
    if os.name != "nt":
        raise ClientPlatformError("github_cli_host_terminal_required")
    subprocess.Popen(
        [executable, "auth", mode, "-h", "github.com"],
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


def execute_github_access(
    *, owner_id: str, command_id: str, expected_revision: str, action: str,
) -> dict[str, Any]:
    """Run only the requested probe or host CLI step, then retain its receipt."""
    try:
        if str(UUID(command_id)) != command_id:
            raise ValueError
    except ValueError:
        raise ClientPlatformError("invalid_account_command") from None
    if action not in {"check", "cli_login", "cli_refresh", "anonymous"}:
        raise ClientPlatformError("invalid_account_command")
    key = f"{owner_id}:{command_id}"
    with _LOCK:
        prior = _RECEIPTS.get(key)
        if prior:
            if prior["action"] != action or prior["expected_revision"] != expected_revision:
                raise ClientPlatformError("account_command_conflict")
            return prior["receipt"]
        if key in _ACTIVE:
            raise ClientPlatformError("account_busy")
        status = read_github_access(owner_id=owner_id)
        if status["revision"] != expected_revision:
            raise ClientPlatformError("account_changed")
        _ACTIVE.add(key)
    try:
        if action == "check":
            checked = github_account.check_github_access()
            with _LOCK:
                _GITHUB[owner_id] = checked
            phase = "completed"
        elif action in {"cli_login", "cli_refresh"}:
            _start_cli("login" if action == "cli_login" else "refresh")
            phase = "started"
        else:
            github_account.clear_github_status_cache()
            with _LOCK:
                _GITHUB.pop(owner_id, None)
            phase = "completed"
        receipt = {
            "schema_version": 1, "command_id": command_id, "action": action,
            "phase": phase, "snapshot": read_github_access(owner_id=owner_id),
        }
        with _LOCK:
            if len(_RECEIPTS) >= 64:
                _RECEIPTS.pop(next(iter(_RECEIPTS)))
            _RECEIPTS[key] = {"action": action, "expected_revision": expected_revision, "receipt": receipt}
        return receipt
    finally:
        with _LOCK:
            _ACTIVE.discard(key)


def read_github_receipt(*, owner_id: str, command_id: str) -> dict[str, Any]:
    with _LOCK:
        saved = _RECEIPTS.get(f"{owner_id}:{command_id}")
        if saved:
            return saved["receipt"]
        if f"{owner_id}:{command_id}" in _ACTIVE:
            raise ClientPlatformError("account_busy")
    raise ClientPlatformError("account_receipt_missing")
