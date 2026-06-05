"""
Row-Bot – Channel DM Pairing & Authentication
==============================================
Provides DM pairing codes so users on external channels (Slack, Discord,
WhatsApp, SMS) can authenticate themselves before the agent processes
their messages.

Auth modes
----------
``user_id``   – single pre-configured user ID (Telegram style).
``pairing``   – 8-char single-use code with 1-hour TTL + rate limiting.
``allowlist`` – pre-approved set of user IDs (future).
``open``      – no auth required (future, e.g. public bots).
"""

from __future__ import annotations

import logging
import secrets
import string
import time
from dataclasses import dataclass, field
from enum import Enum

from row_bot.channels import config as ch_config

log = logging.getLogger("row_bot.channels.auth")

_CODE_LENGTH = 8
_CODE_TTL_SECONDS = 3600          # 1 hour
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_SECONDS = 900            # 15-minute lockout after 5 fails


class AuthMode(str, Enum):
    """Supported authentication modes for channels."""
    USER_ID   = "user_id"
    PAIRING   = "pairing"
    ALLOWLIST = "allowlist"
    OPEN      = "open"


@dataclass
class _PairingState:
    """In-memory state for an active pairing session."""
    code: str
    created_at: float
    channel_name: str


@dataclass
class _FailTracker:
    """Track failed pairing attempts per (channel, user) pair."""
    attempts: int = 0
    last_attempt: float = 0.0


# ── Module state ─────────────────────────────────────────────────────

_active_codes: dict[str, _PairingState] = {}        # code → state
_fail_trackers: dict[str, _FailTracker] = {}          # "channel:user_id" → tracker


# ── Public API ───────────────────────────────────────────────────────

def generate_pairing_code(channel_name: str) -> str:
    """Generate a new 8-character pairing code for *channel_name*.

    Returns the code.  The code is valid for ``_CODE_TTL_SECONDS``.
    Any previous code for the same channel is invalidated.
    """
    # Invalidate any existing code for this channel
    _active_codes.pop(channel_name, None)
    for code, state in list(_active_codes.items()):
        if state.channel_name == channel_name:
            del _active_codes[code]

    alphabet = string.ascii_uppercase + string.digits
    code = "".join(secrets.choice(alphabet) for _ in range(_CODE_LENGTH))

    _active_codes[code] = _PairingState(
        code=code,
        created_at=time.time(),
        channel_name=channel_name,
    )
    log.info("Generated pairing code for %s (expires in %ds)", channel_name, _CODE_TTL_SECONDS)
    return code


def verify_pairing_code(channel_name: str, user_id: str, code: str,
                        display_name: str = "") -> bool:
    """Verify a pairing code submitted by *user_id* on *channel_name*.

    On success the user is added to the channel's approved list and
    the code is consumed.  Returns ``True`` on success, ``False`` on
    failure (bad code, expired, locked out).
    """
    tracker_key = f"{channel_name}:{user_id}"
    tracker = _fail_trackers.get(tracker_key, _FailTracker())

    # Check lockout
    if tracker.attempts >= _MAX_FAILED_ATTEMPTS:
        elapsed = time.time() - tracker.last_attempt
        if elapsed < _LOCKOUT_SECONDS:
            log.warning("Pairing locked out for %s (%ds remaining)",
                        tracker_key, int(_LOCKOUT_SECONDS - elapsed))
            return False
        # Lockout expired — reset
        tracker.attempts = 0

    code = code.strip().upper()
    state = _active_codes.get(code)

    if state is None or state.channel_name != channel_name:
        tracker.attempts += 1
        tracker.last_attempt = time.time()
        _fail_trackers[tracker_key] = tracker
        log.info("Invalid pairing code from %s (attempt %d/%d)",
                 tracker_key, tracker.attempts, _MAX_FAILED_ATTEMPTS)
        return False

    # Check TTL
    if time.time() - state.created_at > _CODE_TTL_SECONDS:
        del _active_codes[code]
        tracker.attempts += 1
        tracker.last_attempt = time.time()
        _fail_trackers[tracker_key] = tracker
        log.info("Expired pairing code from %s", tracker_key)
        return False

    # Success — consume code, register user
    del _active_codes[code]
    _add_approved_user(channel_name, user_id, display_name=display_name)
    # Reset fail tracker on success
    _fail_trackers.pop(tracker_key, None)
    log.info("Pairing successful: %s on %s", user_id, channel_name)
    return True


def is_user_approved(channel_name: str, user_id: str) -> bool:
    """Return ``True`` if *user_id* is approved on *channel_name*."""
    approved = _get_approved_users(channel_name)
    return str(user_id) in approved


def revoke_user(channel_name: str, user_id: str) -> bool:
    """Remove *user_id* from the approved list.  Returns ``True`` if found."""
    approved = _get_approved_users(channel_name)
    uid = str(user_id)
    if uid in approved:
        approved.remove(uid)
        ch_config.set(channel_name, "approved_users", approved)
        # Also remove stored display name
        names = ch_config.get(channel_name, "approved_user_names", {})
        if uid in names:
            del names[uid]
            ch_config.set(channel_name, "approved_user_names", names)
        log.info("Revoked user %s from %s", user_id, channel_name)
        return True
    return False


def get_approved_users(channel_name: str) -> list[str]:
    """Return the list of approved user IDs for *channel_name*."""
    return _get_approved_users(channel_name)


def get_user_names(channel_name: str) -> dict[str, str]:
    """Return {user_id: display_name} for *channel_name*.

    Only users that had a display name at pairing time are included.
    """
    return ch_config.get(channel_name, "approved_user_names", {})


def cleanup_expired_codes() -> int:
    """Remove expired pairing codes.  Returns count of removed codes."""
    now = time.time()
    expired = [c for c, s in _active_codes.items()
               if now - s.created_at > _CODE_TTL_SECONDS]
    for code in expired:
        del _active_codes[code]
    return len(expired)


# ── Helpers ──────────────────────────────────────────────────────────

def _get_approved_users(channel_name: str) -> list[str]:
    return ch_config.get(channel_name, "approved_users", [])


def _add_approved_user(channel_name: str, user_id: str,
                       display_name: str = "") -> None:
    approved = _get_approved_users(channel_name)
    uid = str(user_id)
    if uid not in approved:
        approved.append(uid)
        ch_config.set(channel_name, "approved_users", approved)
    if display_name:
        names = ch_config.get(channel_name, "approved_user_names", {})
        names[uid] = display_name
        ch_config.set(channel_name, "approved_user_names", names)
