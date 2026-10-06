"""Google and X sign-ins, kept only in the system keychain.

Each account's saved sign-in is one keychain entry (namespace ``accounts``): ``google`` (Google's
authorised-user token, shared by Gmail and Calendar), ``google_client`` (the person's own Desktop
client) and ``x``. Earlier versions kept them as files in the data folder. ``migrate`` copies each
into the keychain, reads it back and compares, and only then deletes the files; until that copy is
verified (the keychain unavailable, say) the old file is still read, so a sign-in keeps working, but
nothing writes a token file again.

The last real check of a sign-in (an explicit Check, Monitor, the start-up and six-hourly checks, or a
sign-in that just saved it) is remembered with a digest of what it checked. While the saved sign-in is
unchanged the account reads as connected or as needing a reconnect; once anything replaces it, it reads
as saved and not checked again. Reading a state never refreshes a token or contacts a provider.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from row_bot import secret_store
from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

NAMESPACE = "accounts"
# Account sign-in -> the files earlier versions kept it in (inside the data folder).
LEGACY = {"google": ("gmail/token.json", "calendar/token.json"), "google_client": ("gmail/credentials.json",),
          "x": ("x/token.json",)}
MAX_BYTES = 64 * 1024
_LOCK = threading.RLock()
_GOOGLE_REFRESH = threading.Lock()  # One refresh at a time; others use the token it saved.
_CHECKS: dict[str, tuple[str, bool]] = {}  # Account -> (digest of the sign-in checked, whether it passed).
RETRY_SECONDS = 300
_RETRY_AT: dict[str, float] = {}  # Account -> when a read may try the keychain copy again after it failed.


class AccountTokenError(RuntimeError):
    """The keychain could not keep an account's sign-in; nothing was written to a file instead."""


def _legacy(name: str) -> list[Path]:
    root = get_row_bot_data_dir(create=False)
    return [root / relative for relative in LEGACY[name] if (root / relative).is_file()]


def _parse(text: str | None) -> dict | None:
    if not text or len(text.encode("utf-8")) > MAX_BYTES:
        return None
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) and value else None


def _stored(name: str) -> str | None:
    try:
        return secret_store.get_secret(name, namespace=NAMESPACE)
    except secret_store.SecretStoreError:
        return None


def _file_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _moment(value: object) -> datetime | None:
    """An ``expiry`` (Google: ISO, UTC) or ``expires_at`` (X: Unix time) as a time; None if unreadable."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc)
        if isinstance(value, str):
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return None


def _newest(texts: list[str]) -> str:
    """Of several copies of one sign-in (Gmail and Calendar each refreshed their own), the one whose
    access lasts longest."""
    floor = datetime.min.replace(tzinfo=timezone.utc)

    def lasts(text: str) -> datetime:
        value = _parse(text) or {}
        return _moment(value.get("expiry", value.get("expires_at"))) or floor
    return max(texts, key=lasts)


def _remove_files(name: str) -> None:
    for path in _legacy(name):
        try:
            path.unlink()
        except OSError:
            logger.warning("An old %s sign-in file could not be removed; it is retried on the next start", name)


def _migrate(name: str) -> bool:
    """Copy an old file's sign-in into the keychain; delete the files only once the copy reads back the same."""
    files = _legacy(name)
    if not files:
        return False
    texts = [text for text in (_file_text(path) for path in files) if _parse(text)]
    if not texts:
        logger.warning("An old %s sign-in file could not be read; it was left in place", name)
        return False
    current = _stored(name)
    if _parse(current) is not None:
        chosen = current  # The keychain's copy is this version's own: the files are what earlier versions left.
    else:
        # Gmail and Calendar each refreshed their own copy of one sign-in: the longest-lasting of the copies of
        # the first file's sign-in (same client and refresh token). A different account's file is not merged in.
        first = _parse(texts[0]) or {}
        same = [text for text in texts if all((_parse(text) or {}).get(key) == first.get(key)
                                              for key in ("client_id", "refresh_token"))]
        if len(same) < len(texts):
            logger.warning("Old %s sign-in files held different sign-ins; the first is kept", name)
        chosen = _newest(same)
    try:
        if chosen != current:
            secret_store.set_secret(name, chosen, namespace=NAMESPACE)
        if _stored(name) != chosen:
            raise secret_store.SecretStoreError("verification failed")
    except secret_store.SecretStoreError:
        logger.warning("The %s sign-in stays in its old file until the system keychain can keep it", name)
        _RETRY_AT[name] = time.monotonic() + RETRY_SECONDS
        return False
    _RETRY_AT.pop(name, None)
    _remove_files(name)
    return True


def migrate() -> dict[str, int]:
    """Move every sign-in still kept in a file into the keychain (start-up, and the first read of each)."""
    with _LOCK:
        moved = sum(_migrate(name) for name in LEGACY)
        return {"migrated": moved, "kept": sum(1 for name in LEGACY if _legacy(name))}


def _text(name: str) -> str | None:
    """The saved sign-in's text: the keychain's, or (not copied yet) the old file's."""
    text = _stored(name)
    if _parse(text) is None and _legacy(name):
        if time.monotonic() >= _RETRY_AT.get(name, 0.0):  # A failed copy is tried again after a while, not per read.
            _migrate(name)
        text = _stored(name)
        if _parse(text) is None:  # The keychain can't keep it yet: the old file still works, read-only.
            text = next((found for found in (_file_text(path) for path in _legacy(name)) if _parse(found)), None)
    return text if _parse(text) is not None else None


def read(name: str) -> dict | None:
    """One account's saved sign-in (``google``, ``google_client`` or ``x``), or None."""
    with _LOCK:
        return _parse(_text(name))


def write(name: str, value: dict[str, Any]) -> None:
    """Save a sign-in in the keychain only, checked by reading it back; an old file beside it is removed."""
    text = json.dumps(value, separators=(",", ":"))
    if name not in LEGACY or not isinstance(value, dict) or len(text.encode("utf-8")) > MAX_BYTES:
        raise AccountTokenError("invalid_account_token")
    with _LOCK:
        try:
            secret_store.set_secret(name, text, namespace=NAMESPACE)
            if _stored(name) != text:
                raise secret_store.SecretStoreError("verification failed")
        except secret_store.SecretStoreError:
            raise AccountTokenError("secure_storage_unavailable") from None
        _remove_files(name)


def delete(name: str) -> None:
    """Forget a sign-in: its keychain entry and any old file."""
    with _LOCK:
        _remove_files(name)
        try:
            secret_store.delete_secret(name, namespace=NAMESPACE)
        except secret_store.SecretStoreError:
            raise AccountTokenError("secure_storage_unavailable") from None
        _CHECKS.pop(name, None)


def digest(name: str) -> str:
    """Identifies the saved sign-in without revealing it ("" when there is none)."""
    with _LOCK:
        text = _text(name)
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def record_check(name: str, status: str) -> None:
    """Remember a check's verdict for the sign-in as the check left it. ``valid`` and ``refreshed`` pass and
    ``expired`` fails; any other status (an error, nothing saved) says nothing. Documentation captures never
    probe, so their suppressed answers are not remembered."""
    from row_bot.docs_capture import is_docs_capture
    if status not in {"valid", "refreshed", "expired"} or is_docs_capture():
        return
    current = digest(name)
    with _LOCK:
        if current:
            _CHECKS[name] = (current, status != "expired")
        else:
            _CHECKS.pop(name, None)


def state(name: str) -> str:
    """One sign-in's state from its last check or its own expiry: ``connected`` or ``invalid`` while it is as
    its last check left it; otherwise ``not_authenticated`` (none saved), ``expired`` (the access ran out
    and there is no refresh token) or ``saved_unchecked``."""
    with _LOCK:
        text = _text(name)
        checked = _CHECKS.get(name)
    if text is None:
        return "not_authenticated"
    if checked is not None and checked[0] == hashlib.sha256(text.encode("utf-8")).hexdigest():
        return "connected" if checked[1] else "invalid"
    value = _parse(text) or {}
    expiry = value.get("expiry", value.get("expires_at"))
    if expiry is None:
        return "saved_unchecked"
    moment = _moment(expiry)
    if moment is None:
        return "unavailable"
    return "expired" if moment <= datetime.now(timezone.utc) and not value.get("refresh_token") else "saved_unchecked"


def google_client(own_file: str | None = None) -> dict | None:
    """The person's own Google Desktop client: from the keychain, or a client file they keep themselves
    outside Row-Bot's data folder (theirs, so it is read where it is and never moved). ``own_file`` is that
    file's path as Gmail's settings name it (read from them when not given)."""
    found = read("google_client")
    if found is not None:
        return found
    root = get_row_bot_data_dir(create=False)
    configured = own_file
    if configured is None:
        try:
            raw = json.loads((root / "tools_config.json").read_text(encoding="utf-8"))
            configured = raw.get("tool_configs", {}).get("gmail", {}).get("credentials_path")
        except (OSError, ValueError, AttributeError, TypeError):
            return None
    if not isinstance(configured, str) or not configured:
        return None
    path = Path(configured).expanduser()
    try:
        inside = path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not path.is_absolute() or inside:
        return None
    return _parse(_file_text(path))


def google_credentials() -> Any:
    """Google's saved sign-in as credentials for Gmail and Calendar, refreshed (once, whoever asks) when its
    access has run out; the refreshed token goes back to the keychain only."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    info = read("google")
    if info is None:
        raise AccountTokenError("not_signed_in")
    # The token's own scopes (Gmail and Calendar together): a refresh never narrows what the other one needs.
    credentials = Credentials.from_authorized_user_info(info)
    if credentials.valid:
        return credentials
    with _GOOGLE_REFRESH:
        info = read("google")  # Another request may have refreshed it while this one waited.
        if info is None:
            raise AccountTokenError("not_signed_in")
        credentials = Credentials.from_authorized_user_info(info)
        if credentials.valid:
            return credentials
        if not credentials.refresh_token:
            raise RuntimeError("The Google sign-in has run out and has no refresh token")
        credentials.refresh(Request())
        if not credentials.valid:
            raise RuntimeError("Refreshing the Google sign-in did not produce valid credentials")
        try:
            write("google", json.loads(credentials.to_json()))
        except AccountTokenError:
            logger.warning("The refreshed Google sign-in could not be saved; it is used for this request only")
        return credentials


def check_google() -> tuple[str, str]:
    """Probe Google's saved sign-in, refreshing it if its access has run out; the verdict is remembered."""
    result = _probe_google()
    record_check("google", result[0])
    return result


def _probe_google() -> tuple[str, str]:
    info = read("google")
    if info is None:
        return ("missing", "No saved Google sign-in")
    try:
        from google.oauth2.credentials import Credentials

        existing = Credentials.from_authorized_user_info(info)
        if existing.valid:
            return ("valid", "Token is valid")
        if not existing.refresh_token:
            return ("expired", "Token expired and no refresh token available")
        from row_bot.docs_capture import is_docs_real_data_capture
        if is_docs_real_data_capture():
            return ("expired", "Token refresh suppressed during authorized capture")
        google_credentials()
        return ("refreshed", "Token refreshed successfully")
    except Exception as exc:
        error = str(exc).lower()
        if "invalid_grant" in error or "revoked" in error:
            return ("expired", "Refresh token expired or revoked — re-authenticate in Settings")
        return ("error", f"Token check failed: {type(exc).__name__}")
