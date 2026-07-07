"""Token and pairing helpers for Row-Bot mobile companion access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
import uuid

from row_bot.mobile.store import MobileAuthStore, MobileDevice, PairingCode, parse_iso, utc_now

TOKEN_HASH_ITERATIONS = 200_000
PAIRING_CODE_TTL = timedelta(minutes=10)
PAIRING_FAILURE_LIMIT = 5
PAIRING_LOCK_DURATION = timedelta(minutes=5)
DEVICE_TOKEN_PREFIX = "rbd"
PAIRING_CODE_PREFIX = "rbp"

_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_ID_RE = re.compile(r"^[a-f0-9]{32}$")


@dataclass(frozen=True)
class SecretHash:
    secret_hash: str
    salt: str


@dataclass(frozen=True)
class PairingTicket:
    id: str
    code: str
    expires_at: str
    intended_origin: str | None
    access_mode: str | None

    def pairing_url(self, origin: str | None = None) -> str:
        base = (origin or self.intended_origin or "").rstrip("/")
        path = f"/mobile/pair?code={self.code}"
        return f"{base}{path}" if base else path


@dataclass(frozen=True)
class PairingConfirmation:
    device: MobileDevice
    token: str


class PairingError(ValueError):
    """Raised when a pairing code cannot be confirmed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now(value: datetime | None = None) -> datetime:
    if value is None:
        return utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def hash_secret(secret: str, *, salt: str | None = None) -> SecretHash:
    """Hash a token secret using stdlib PBKDF2 and a per-token salt."""
    if not secret:
        raise ValueError("secret is required")
    salt_hex = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        bytes.fromhex(salt_hex),
        TOKEN_HASH_ITERATIONS,
    )
    return SecretHash(secret_hash=digest.hex(), salt=salt_hex)


def verify_secret(secret: str, *, salt: str, expected_hash: str) -> bool:
    """Return True when a raw secret matches the stored hash."""
    try:
        candidate = hash_secret(secret, salt=salt).secret_hash
    except Exception:
        return False
    return hmac.compare_digest(candidate, expected_hash)


def _join(prefix: str, item_id: str, secret: str) -> str:
    return f"{prefix}_{item_id}.{secret}"


def _split(value: str, prefix: str) -> tuple[str, str] | None:
    text = str(value or "").strip()
    expected = f"{prefix}_"
    if not text.startswith(expected) or "." not in text:
        return None
    item_id, secret = text[len(expected):].split(".", 1)
    if not _ID_RE.match(item_id) or not _SECRET_RE.match(secret):
        return None
    return item_id, secret


def parse_pairing_code(code: str) -> tuple[str, str] | None:
    return _split(code, PAIRING_CODE_PREFIX)


def parse_device_token(token: str) -> tuple[str, str] | None:
    return _split(token, DEVICE_TOKEN_PREFIX)


def create_pairing_ticket(
    store: MobileAuthStore,
    *,
    intended_origin: str | None = None,
    access_mode: str | None = None,
    ttl: timedelta = PAIRING_CODE_TTL,
    now: datetime | None = None,
) -> PairingTicket:
    """Create a short-lived, single-use pairing code."""
    code_id = uuid.uuid4().hex
    secret = secrets.token_urlsafe(32)
    hashed = hash_secret(secret)
    current = _now(now)
    expires_at = current + ttl
    pairing = store.create_pairing_code(
        code_hash=hashed.secret_hash,
        code_salt=hashed.salt,
        expires_at=expires_at,
        intended_origin=intended_origin,
        access_mode=access_mode,
        code_id=code_id,
        now=current,
    )
    return PairingTicket(
        id=pairing.id,
        code=_join(PAIRING_CODE_PREFIX, code_id, secret),
        expires_at=pairing.expires_at,
        intended_origin=pairing.intended_origin,
        access_mode=pairing.access_mode,
    )


def _pairing_is_expired(pairing: PairingCode, now: datetime) -> bool:
    expires_at = parse_iso(pairing.expires_at)
    return expires_at is None or expires_at <= now


def _pairing_is_locked(pairing: PairingCode, now: datetime) -> bool:
    locked_until = parse_iso(pairing.locked_until)
    return locked_until is not None and locked_until > now


def confirm_pairing(
    store: MobileAuthStore,
    *,
    code: str,
    display_name: str,
    user_agent: str | None = None,
    paired_from: str | None = None,
    access_mode: str | None = None,
    now: datetime | None = None,
) -> PairingConfirmation:
    """Claim a pairing code and create a mobile device token."""
    parsed = parse_pairing_code(code)
    current = _now(now)
    if parsed is None:
        raise PairingError("invalid_code")
    code_id, secret = parsed
    pairing = store.get_pairing_code(code_id)
    if pairing is None:
        raise PairingError("invalid_code")
    if pairing.claimed_at:
        raise PairingError("already_claimed")
    if _pairing_is_expired(pairing, current):
        raise PairingError("expired")
    if _pairing_is_locked(pairing, current):
        raise PairingError("locked")
    if not verify_secret(secret, salt=pairing.code_salt, expected_hash=pairing.code_hash):
        next_attempts = pairing.failed_attempts + 1
        locked_until = current + PAIRING_LOCK_DURATION if next_attempts >= PAIRING_FAILURE_LIMIT else None
        store.record_pairing_failure(code_id, locked_until=locked_until)
        raise PairingError("invalid_code")

    if not store.mark_pairing_claimed(code_id, now=current):
        raise PairingError("already_claimed")

    device_id = uuid.uuid4().hex
    token_secret = secrets.token_urlsafe(48)
    token = _join(DEVICE_TOKEN_PREFIX, device_id, token_secret)
    token_hash = hash_secret(token_secret)
    label = str(display_name or "").strip() or "Mobile device"
    device = store.create_device(
        device_id=device_id,
        display_name=label[:80],
        token_hash=token_hash.secret_hash,
        token_salt=token_hash.salt,
        user_agent=user_agent,
        paired_from=paired_from,
        access_mode=access_mode or pairing.access_mode,
        now=current,
    )
    return PairingConfirmation(device=device, token=token)


def validate_device_token(
    store: MobileAuthStore,
    token: str,
    *,
    now: datetime | None = None,
    touch: bool = True,
) -> MobileDevice | None:
    """Validate a mobile device token without ever storing raw token text."""
    parsed = parse_device_token(token)
    if parsed is None:
        return None
    device_id, secret = parsed
    device = store.get_device(device_id)
    if device is None or device.revoked_at:
        return None
    if not verify_secret(secret, salt=device.token_salt, expected_hash=device.token_hash):
        return None
    if touch:
        store.touch_device(device_id, now=_now(now))
        device = store.get_device(device_id) or device
    return device
