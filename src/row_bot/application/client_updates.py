"""Explicit updater checks and preferences for the local owner client."""

from __future__ import annotations

import hashlib
import json
from threading import RLock
from threading import Thread
from typing import Any, Callable
from uuid import UUID

from row_bot.application.client_platform import ClientPlatformError

_LOCK = RLock()
_RECEIPTS: dict[str, dict[str, Any]] = {}
_INSTALLS: dict[str, dict[str, Any]] = {}
_ACTIVE_INSTALL: str | None = None


def _owner():
    # The updater is imported only for an explicit updates view or action.
    from row_bot import updater

    return updater


def read_updates() -> dict[str, Any]:
    """Read cached release state; never check, download, or install on render."""
    updater = _owner()
    with _LOCK:
        state = updater.get_update_state()
        release = state.available
        values = {
            "channel": state.channel if state.channel in {"stable", "beta"} else "stable",
            "current_version": state.current_version[:64],
            "last_check": state.last_check[:64] if state.last_check else None,
            "last_success": state.last_success[:64] if state.last_success else None,
            "skipped_versions": [item[:64] for item in state.skipped_versions[:64]],
            "available": (
                {
                    "version": release.version[:64],
                    "channel": release.channel if release.channel in {"stable", "beta"} else "stable",
                    "published_at": release.published_at[:64],
                    "notes": release.notes_md[:16000],
                    "html_url": release.html_url[:2048],
                    "asset_size": min(1_000_000_000_000, max(0, release.asset_size)),
                    "verified_manifest": bool(release.sha256),
                }
                if release
                else None
            ),
            "dev_install": updater.is_dev_install(),
        }
        revision = hashlib.sha256(
            json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return {"schema_version": 1, "revision": revision, **values}


def execute_update_choice(
    *, owner_id: str, command_id: str, expected_revision: str, action: str, version: str = "",
) -> dict[str, Any]:
    """Check or skip the current release with an idempotent command identity."""
    try:
        if str(UUID(command_id)) != command_id:
            raise ValueError
    except ValueError:
        raise ClientPlatformError("invalid_update_command") from None
    if action not in {"check", "skip", "clear_skipped"}:
        raise ClientPlatformError("invalid_update_command")
    with _LOCK:
        receipt_key = f"{owner_id}:{command_id}"
        prior = _RECEIPTS.get(receipt_key)
        if prior:
            if prior["action"] != action or prior["version"] != version:
                raise ClientPlatformError("update_command_conflict")
            return prior["receipt"]
        before = read_updates()
        if before["revision"] != expected_revision:
            raise ClientPlatformError("update_changed")
        updater = _owner()
        if before["dev_install"]:
            raise ClientPlatformError("update_unavailable")
        if action == "check":
            if version:
                raise ClientPlatformError("invalid_update_command")
            updater.check_for_updates(force=True)
            after = read_updates()
            status = "completed" if after["last_success"] != before["last_success"] else "failed"
        elif action == "skip":
            available = before["available"]
            if not available or not version or version != available["version"]:
                raise ClientPlatformError("update_changed")
            updater.skip_version(version)
            after = read_updates()
            status = "completed"
        else:
            if version:
                raise ClientPlatformError("invalid_update_command")
            updater.clear_skipped_versions()
            after = read_updates()
            status = "completed"
        receipt = {
            "schema_version": 1,
            "command_id": command_id,
            "status": status,
            "snapshot": after,
        }
        if len(_RECEIPTS) >= 64:
            _RECEIPTS.pop(next(iter(_RECEIPTS)))
        _RECEIPTS[receipt_key] = {"action": action, "version": version, "receipt": receipt}
        return receipt


def _install_view(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "command_id": job["command_id"],
        "version": job["version"],
        "phase": job["phase"],
        "downloaded": job["downloaded"],
        "total": job["total"],
        "message": job["message"],
    }


def read_update_install(*, owner_id: str, command_id: str) -> dict[str, Any]:
    """Read only the initiating owner's bounded install progress."""
    with _LOCK:
        job = _INSTALLS.get(f"{owner_id}:{command_id}")
        if not job:
            raise ClientPlatformError("update_job_missing")
        return _install_view(job)


def cancel_update_install(*, owner_id: str, command_id: str) -> dict[str, Any]:
    """Stop a download from handing off after its current read returns."""
    with _LOCK:
        job = _INSTALLS.get(f"{owner_id}:{command_id}")
        if not job:
            raise ClientPlatformError("update_job_missing")
        if job["phase"] == "downloading":
            job["cancelled"] = True
            job["phase"] = "cancel_requested"
            job["message"] = "Cancellation requested. Waiting for the download to stop."
        return _install_view(job)


def _finish_install(key: str, phase: str, message: str) -> None:
    global _ACTIVE_INSTALL
    with _LOCK:
        job = _INSTALLS[key]
        job["phase"] = phase
        job["message"] = message
        if _ACTIVE_INSTALL == key:
            _ACTIVE_INSTALL = None


def _run_install(
    key: str, release: Any, validate: Callable[[], None],
) -> None:
    updater = _owner()

    def progress(done: int, total: int) -> None:
        with _LOCK:
            job = _INSTALLS[key]
            job["downloaded"] = min(1_000_000_000_000, max(0, done))
            job["total"] = min(1_000_000_000_000, max(0, total))

    try:
        path = updater.download_update(release, progress=progress)
    except Exception:
        _finish_install(key, "failed", "Download or checksum verification failed. Retry after checking the release source.")
        return
    with _LOCK:
        if _INSTALLS[key]["cancelled"]:
            _finish_install(key, "cancelled", "Update cancelled before installer handoff.")
            return
    try:
        validate()
        current = updater.get_update_state().available
        if not current or (
            current.version, current.channel, current.sha256
        ) != (release.version, release.channel, release.sha256):
            raise ClientPlatformError("update_changed")
    except Exception:
        _finish_install(key, "failed", "Access or release state changed before installer handoff.")
        return
    with _LOCK:
        if _INSTALLS[key]["cancelled"]:
            _finish_install(key, "cancelled", "Update cancelled before installer handoff.")
            return
        _INSTALLS[key]["phase"] = "handoff"
        _INSTALLS[key]["message"] = "Verified download complete. Starting the platform installer."
    try:
        updater.install_and_restart(path)
    except Exception:
        _finish_install(key, "failed", "Installer verification or handoff failed. Retry from the release card.")


def start_update_install(
    *, owner_id: str, command_id: str, expected_revision: str,
    version: str, validate: Callable[[], None],
) -> dict[str, Any]:
    """Start one explicit, fake-testable download and installer handoff."""
    global _ACTIVE_INSTALL
    try:
        if str(UUID(command_id)) != command_id:
            raise ValueError
    except ValueError:
        raise ClientPlatformError("invalid_update_command") from None
    key = f"{owner_id}:{command_id}"
    with _LOCK:
        existing = _INSTALLS.get(key)
        if existing:
            if existing["version"] != version or existing["expected_revision"] != expected_revision:
                raise ClientPlatformError("update_command_conflict")
            return _install_view(existing)
        if _ACTIVE_INSTALL:
            raise ClientPlatformError("update_install_busy")
        before = read_updates()
        if before["revision"] != expected_revision:
            raise ClientPlatformError("update_changed")
        if before["dev_install"]:
            raise ClientPlatformError("update_unavailable")
        release = _owner().get_update_state().available
        if not release or release.version != version or not release.sha256 or not release.asset_url:
            raise ClientPlatformError("update_unavailable")
        validate()
        job = {
            "command_id": command_id,
            "expected_revision": expected_revision,
            "version": version,
            "phase": "downloading",
            "downloaded": 0,
            "total": min(1_000_000_000_000, max(0, release.asset_size)),
            "message": "Downloading and verifying the selected release.",
            "cancelled": False,
        }
        if len(_INSTALLS) >= 32:
            for old_key, old in list(_INSTALLS.items()):
                if old["phase"] in {"failed", "cancelled", "handoff"}:
                    _INSTALLS.pop(old_key)
                    break
        _INSTALLS[key] = job
        _ACTIVE_INSTALL = key
        Thread(target=_run_install, args=(key, release, validate), daemon=True).start()
        return _install_view(job)
