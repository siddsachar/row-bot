"""What Settings › Accounts says about a Google or X token file (B263).

The last real check of a token file (an explicit Check, Monitor, the start-up
and six-hourly checks, or a sign-in that just wrote it) is remembered with the
file's size and modification time, as GitHub remembers its last verified
status. While the file is exactly as that check left it, the account reads as
connected or as needing a reconnect; once anything rewrites it, it reads as
saved and not checked again. Without a check, only the file's expiry metadata
is read: an access token that ran out is fine while a refresh token exists.
Nothing here refreshes a token or contacts a provider.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

_MAX_TOKEN_BYTES = 64 * 1024
_LOCK = Lock()
# Token file → (its size and mtime when checked, whether the check passed).
_CHECKS: dict[str, tuple[tuple[int, int], bool]] = {}


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _stamp(path: str | Path) -> tuple[int, int] | None:
    try:
        state = os.stat(path)
    except OSError:
        return None
    return state.st_size, state.st_mtime_ns


def record_token_check(path: str | Path, status: str) -> None:
    """Remember a check's verdict for *path* as the check left the file.

    ``valid`` and ``refreshed`` pass and ``expired`` fails; any other status
    (an error, a missing file) says nothing about the token. Documentation
    captures never probe, so their suppressed answers are not remembered.
    """
    from row_bot.docs_capture import is_docs_capture

    if status not in {"valid", "refreshed", "expired"} or is_docs_capture():
        return
    stamp = _stamp(path)
    with _LOCK:
        if stamp is None:
            _CHECKS.pop(_key(path), None)
        else:
            _CHECKS[_key(path)] = (stamp, status != "expired")


def _expired(value: object) -> bool | None:
    """Whether an ``expiry``/``expires_at`` value has passed; None if unreadable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # X saves a Unix time.
        try:
            moment = datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        # Google saves an ISO time, in UTC.
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    else:
        return None
    return moment <= datetime.now(timezone.utc)


def token_file_state(path: Path) -> str:
    """One token file's account state, from its last check or its metadata.

    ``connected`` or ``invalid`` while the file is as its last check left it;
    otherwise ``not_authenticated`` (no file), ``unavailable`` (unreadable),
    ``expired`` (the access token ran out and there is no refresh token) or
    ``saved_unchecked``.
    """
    stamp = _stamp(path)
    if stamp is None:
        return "not_authenticated"
    with _LOCK:
        checked = _CHECKS.get(_key(path))
    if checked is not None and checked[0] == stamp:
        return "connected" if checked[1] else "invalid"
    if stamp[0] > _MAX_TOKEN_BYTES:
        return "unavailable"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return "unavailable"
    if not isinstance(raw, dict):
        return "unavailable"
    value = raw.get("expiry", raw.get("expires_at"))
    if value is None:
        return "saved_unchecked"
    expired = _expired(value)
    if expired is None:
        return "unavailable"
    return "expired" if expired and not raw.get("refresh_token") else "saved_unchecked"
