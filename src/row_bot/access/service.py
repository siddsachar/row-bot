"""High-level invitation, device, and session operations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from datetime import datetime, timedelta
import threading
import time
from typing import Mapping
from urllib.parse import quote, urlsplit
import uuid

from row_bot.access.models import (
    AccessDevice,
    AccessInvitation,
    AccessSession,
    AuthenticatedSession,
    SessionLifetime,
    TokenFormat,
)
from row_bot.access.request_context import safe_relative_next
from row_bot.access.store import (
    AccessStore,
    InvitationClaimError,
    normalize_datetime,
)
from row_bot.access.tokens import (
    issue_invitation_token,
    issue_session_token,
    parse_invitation_token,
    parse_legacy_device_token,
    parse_session_token,
    verify_secret,
)

INVITATION_TTL = timedelta(minutes=10)
TRUSTED_SESSION_TTL = timedelta(days=30)
TEMPORARY_SESSION_TTL = timedelta(hours=12)
TRUSTED_SESSION_RENEWAL_WINDOW = timedelta(days=7)
SESSION_REFRESH_POLL_INTERVAL = timedelta(hours=12)
REMOTE_CLIENT_PATH = "/app-v2/"
# A session's "last seen" is written at most this often: every request used to write it.
SESSION_TOUCH_SECONDS = 30.0
# The access route of a sign-in the launcher makes for its own window or browser (A0).
THIS_COMPUTER_ROUTE = "this_computer"
# A browser signed in with a setup code (H1); Settings › Devices lets the owner rename it.
SETUP_CODE_ROUTE = "setup_code"
SETUP_CODE_DEVICE_NAME = "Connected browser"


@dataclass(frozen=True)
class CreatedInvitation:
    invitation: AccessInvitation
    token: str
    next_path: str = "/"

    def invitation_url(self, origin: str | None = None) -> str:
        selected_origin = canonicalize_origin(origin or self.invitation.intended_origin)
        result = f"{selected_origin}/connect?invitation={self.token}"
        if self.next_path != "/":
            result += f"&next={quote(self.next_path, safe='')}"
        return result


@dataclass(frozen=True)
class InvitationInspection:
    invitation: AccessInvitation
    status: str


@dataclass(frozen=True)
class InvitationClaim:
    """Successful claim result; ``session_token`` is returned only once."""

    invitation: AccessInvitation
    device: AccessDevice
    session: AccessSession
    session_token: str

    @property
    def session_lifetime(self) -> SessionLifetime:
        return self.invitation.session_lifetime

    @property
    def intended_origin(self) -> str:
        return self.invitation.intended_origin


@dataclass(frozen=True)
class SetupCodeClaim:
    """A browser signed in with a setup code; ``session_token`` is returned only once."""

    device: AccessDevice
    session: AccessSession
    session_token: str
    recovered: bool


@dataclass(frozen=True)
class SessionRefreshRecord:
    """Non-secret session state returned by a trusted-session refresh."""

    id: str
    device_id: str
    expires_at: datetime
    lifetime: SessionLifetime


@dataclass(frozen=True)
class SessionRefreshResult:
    """Outcome of an atomic, bounded trusted-session refresh."""

    session: SessionRefreshRecord | None
    renewed: bool


class AccessService:
    """Security-sensitive access operations over an :class:`AccessStore`."""

    def __init__(self, store: AccessStore | None = None) -> None:
        self.store = store or AccessStore()
        # Short-lived HTTP admission state is intentionally memory-only. It
        # contains no secrets and never replaces durable authorization state.
        self._request_budget_lock = threading.Lock()
        self._request_budgets: dict[tuple[str, str], tuple[float, int]] = {}
        # Tokens already checked against their stored verifier, by the SHA-256 of the token: a window
        # makes many requests a second, and each paid 200,000 PBKDF2 rounds (a tenth of a second or
        # more). The session's state is still read on every request, so revoking, expiring or rotating
        # a session takes effect at once. Memory only, and no secret: the tokens are random.
        self._verified_lock = threading.Lock()
        self._verified: dict[str, tuple[str, str]] = {}
        self._touched: dict[str, float] = {}

    @property
    def instance_id(self) -> str:
        return self.store.instance_id

    def consume_request_budget(
        self,
        bucket: str,
        subject: str,
        *,
        limit: int,
        window_seconds: float = 60.0,
        now: float | None = None,
    ) -> bool:
        """Consume one privacy-safe, instance-local HTTP admission slot."""

        if limit < 1 or window_seconds <= 0:
            raise ValueError("request budget bounds must be positive")
        current = time.monotonic() if now is None else float(now)
        key = (str(bucket)[:64], str(subject or "unidentified")[:160])
        with self._request_budget_lock:
            if len(self._request_budgets) > 2048:
                self._request_budgets = {
                    item_key: value
                    for item_key, value in self._request_budgets.items()
                    if current - value[0] < window_seconds
                }
                if len(self._request_budgets) > 1792:
                    newest = sorted(
                        self._request_budgets.items(),
                        key=lambda item: item[1][0],
                        reverse=True,
                    )[:1792]
                    self._request_budgets = dict(newest)
            started, count = self._request_budgets.get(key, (current, 0))
            if current - started >= window_seconds:
                started, count = current, 0
            if count >= limit:
                self._request_budgets[key] = (started, count)
                return False
            self._request_budgets[key] = (started, count + 1)
            return True

    def create_invitation(
        self,
        *,
        intended_origin: str,
        session_lifetime: SessionLifetime | str = SessionLifetime.TRUSTED,
        next_path: str = "/",
        ttl: timedelta = INVITATION_TTL,
        created_by: str | None = None,
        access_route: str | None = None,
        now: datetime | None = None,
    ) -> CreatedInvitation:
        current = normalize_datetime(now)
        normalized_lifetime = SessionLifetime(session_lifetime)
        if normalized_lifetime is SessionLifetime.MIGRATED:
            raise ValueError("migrated is reserved for legacy compatibility sessions")
        if ttl <= timedelta(0) or ttl > INVITATION_TTL:
            raise ValueError(
                "invitation lifetime must be positive and at most 10 minutes"
            )
        origin = canonicalize_origin(intended_origin)
        invitation_id = uuid.uuid4().hex
        issued = issue_invitation_token(invitation_id)
        invitation = self.store.create_invitation_record(
            invitation_id=invitation_id,
            secret_hash=issued.secret_hash,
            secret_salt=issued.secret_salt,
            session_lifetime=normalized_lifetime,
            intended_origin=origin,
            expires_at=current + ttl,
            created_by=created_by,
            access_route=access_route,
            now=current,
        )
        self.store.log_event(
            "invitation_created",
            invitation_id=invitation.id,
            detail={
                "session_lifetime": invitation.session_lifetime.value,
            },
            now=current,
        )
        return CreatedInvitation(
            invitation=invitation,
            token=issued.token,
            next_path=safe_relative_next(next_path),
        )

    def inspect_invitation(
        self,
        token: str,
        *,
        effective_client: str | None = None,
        now: datetime | None = None,
    ) -> InvitationInspection:
        """Inspect invitation state without claiming or changing it."""
        invitation, secret = self._load_token_invitation(token)
        if not verify_secret(
            secret,
            salt=invitation.secret_salt,
            expected_hash=invitation.secret_hash,
        ):
            raise InvitationClaimError("invalid_invitation")
        current = normalize_datetime(now)
        if invitation.claimed_at is not None:
            status = "already_claimed"
        elif invitation.cancelled_at is not None:
            status = "cancelled"
        elif invitation.expires_at <= current:
            status = "expired"
        elif (
            invitation.locked_until is not None and invitation.locked_until > current
        ) or self.store.invitation_failure_locked(
            invitation.id,
            effective_client=effective_client,
            now=current,
        ):
            status = "locked"
        else:
            status = "available"
        return InvitationInspection(invitation=invitation, status=status)

    def claim_invitation(
        self,
        token: str,
        *,
        intended_origin: str,
        display_name: str,
        user_agent: str | None = None,
        effective_client: str | None = None,
        access_route: str | None = None,
        session_lifetime: SessionLifetime | str | None = None,
        now: datetime | None = None,
    ) -> InvitationClaim:
        current = normalize_datetime(now)
        parsed = parse_invitation_token(token)
        if parsed is None:
            raise InvitationClaimError("invalid_invitation")
        invitation_id, invitation_secret = parsed
        invitation = self.store.get_invitation(invitation_id)
        if invitation is None:
            raise InvitationClaimError("invalid_invitation")
        requested_lifetime = (
            SessionLifetime(session_lifetime) if session_lifetime is not None else None
        )

        device, session, session_token = _new_device_session(
            display_name=display_name,
            user_agent=user_agent,
            effective_client=effective_client,
            access_route=access_route or invitation.access_route,
            lifetime=invitation.session_lifetime,
            now=current,
        )
        device, session, claimed_invitation = self.store.claim_invitation_atomic(
            invitation_id=invitation_id,
            invitation_secret=invitation_secret,
            expected_origin=canonicalize_origin(intended_origin),
            device=device,
            session=session,
            requested_lifetime=requested_lifetime,
            effective_client=effective_client,
            user_agent=user_agent,
            now=current,
        )
        return InvitationClaim(
            invitation=claimed_invitation,
            device=device,
            session=session,
            session_token=session_token,
        )

    def claim_setup_code(
        self,
        *,
        used_verifier: Mapping[str, str],
        allow_recovery: bool,
        user_agent: str | None = None,
        effective_client: str | None = None,
        now: datetime | None = None,
    ) -> SetupCodeClaim | None:
        """Sign in the browser that brought a valid setup code (H1), as a trusted session.

        ``None`` when someone owns this Row-Bot and the code is not a recovery code.
        """
        current = normalize_datetime(now)
        device, session, session_token = _new_device_session(
            display_name=SETUP_CODE_DEVICE_NAME,
            user_agent=user_agent,
            effective_client=effective_client,
            access_route=SETUP_CODE_ROUTE,
            lifetime=SessionLifetime.TRUSTED,
            now=current,
        )
        result = self.store.claim_setup_code_atomic(
            device=device,
            session=session,
            used_verifier=used_verifier,
            allow_recovery=allow_recovery,
            effective_client=effective_client,
            user_agent=user_agent,
            now=current,
        )
        if result is None:
            return None
        device, session, recovered = result
        return SetupCodeClaim(
            device=device,
            session=session,
            session_token=session_token,
            recovered=recovered,
        )

    def owned(self) -> bool:
        """Whether any device is signed in to this Row-Bot, or could sign in again."""
        return bool(self.store.list_devices(include_revoked=False))

    def validate_session(
        self,
        token: str,
        *,
        now: datetime | None = None,
        touch: bool = True,
        address: str | None = None,
    ) -> AuthenticatedSession | None:
        parsed = parse_session_token(token)
        if parsed is None:
            return None
        session_id, secret = parsed
        current = normalize_datetime(now)
        session = self.store.get_session(session_id)
        if (
            session is None
            or session.token_format is not TokenFormat.SESSION_V1
            or not session.is_active(current)
            or not self._token_matches(token, secret, session)
        ):
            return None
        device = self.store.get_device(session.device_id)
        if device is None or device.revoked_at is not None:
            return None
        if touch and self._touch_due(session.id):
            self.store.touch_session(session.id, now=current, address=address)
            session = self.store.get_session(session.id) or session
            device = self.store.get_device(device.id) or device
        return AuthenticatedSession(device=device, session=session)

    def _token_matches(self, token: str, secret: str, session: AccessSession) -> bool:
        """Whether *token* is this session's, checking the slow verifier once per token and verifier."""
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._verified_lock:
            if self._verified.get(key) == (session.id, session.token_hash):
                return True
        if not verify_secret(secret, salt=session.token_salt, expected_hash=session.token_hash):
            return False
        with self._verified_lock:
            if len(self._verified) >= 1024:
                self._verified.clear()
            self._verified[key] = (session.id, session.token_hash)
        return True

    def _touch_due(self, session_id: str) -> bool:
        clock = time.monotonic()
        with self._verified_lock:
            if clock - self._touched.get(session_id, float("-inf")) < SESSION_TOUCH_SECONDS:
                return False
            if len(self._touched) >= 1024:
                self._touched.clear()
            self._touched[session_id] = clock
            return True

    def validate_legacy_session(
        self,
        token: str,
        *,
        now: datetime | None = None,
        touch: bool = True,
        address: str | None = None,
    ) -> AuthenticatedSession | None:
        """Validate only a reviewed, migrated legacy-mobile ``rbd`` session."""
        parsed = parse_legacy_device_token(token)
        if parsed is None:
            return None
        legacy_device_id, secret = parsed
        current = normalize_datetime(now)
        session = self.store.get_legacy_session_for_device(legacy_device_id)
        if (
            session is None
            or session.token_format is not TokenFormat.LEGACY_RBD
            or not session.is_active(current)
            or not verify_secret(
                secret,
                salt=session.token_salt,
                expected_hash=session.token_hash,
            )
        ):
            return None
        device = self.store.get_device(session.device_id)
        if (
            device is None
            or device.legacy_source_id is None
            or device.revoked_at is not None
        ):
            return None
        if touch:
            self.store.touch_session(session.id, now=current, address=address)
            session = self.store.get_session(session.id) or session
            device = self.store.get_device(device.id) or device
        return AuthenticatedSession(device=device, session=session)

    def refresh_trusted_session(
        self,
        session_id: str,
        *,
        now: datetime | None = None,
    ) -> SessionRefreshResult:
        """Extend an eligible trusted session without issuing a new secret."""

        session, renewed = self.store.renew_trusted_session_if_due(
            session_id,
            now=now,
        )
        if session is None:
            return SessionRefreshResult(session=None, renewed=False)
        return SessionRefreshResult(
            session=SessionRefreshRecord(
                id=session.id,
                device_id=session.device_id,
                expires_at=session.expires_at,
                lifetime=session.lifetime,
            ),
            renewed=renewed,
        )

    # The windows and browsers the launcher signs in on this computer (A0) are the
    # computer itself, not devices to manage: lists leave them out unless asked.

    def list_devices(
        self, *, include_revoked: bool = True, include_this_computer: bool = False
    ) -> list[AccessDevice]:
        devices = self.store.list_devices(include_revoked=include_revoked)
        if include_this_computer:
            return devices
        return [device for device in devices if device.access_route != THIS_COMPUTER_ROUTE]

    def list_sessions(
        self,
        *,
        device_id: str | None = None,
        include_revoked: bool = True,
        include_this_computer: bool = False,
    ) -> list[AccessSession]:
        sessions = self.store.list_sessions(
            device_id=device_id,
            include_revoked=include_revoked,
        )
        if include_this_computer:
            return sessions
        hidden = {
            device.id
            for device in self.store.list_devices(include_revoked=True)
            if device.access_route == THIS_COMPUTER_ROUTE
        }
        return [session for session in sessions if session.device_id not in hidden]

    def list_invitations(
        self, *, limit: int = 100, include_this_computer: bool = False
    ) -> list[AccessInvitation]:
        invitations = self.store.list_invitations(limit=limit)
        if include_this_computer:
            return invitations
        return [item for item in invitations if item.access_route != THIS_COMPUTER_ROUTE]

    def claimed_device_ids(self, invitation_ids: list[str]) -> dict[str, str]:
        return self.store.claimed_device_ids(invitation_ids)

    def rename_device(self, device_id: str, display_name: str) -> AccessDevice | None:
        """Rename an active device. Returns ``None`` when it is gone or revoked."""
        name = normalize_device_name(display_name)
        if name is None:
            raise ValueError("invalid device name")
        return self.store.rename_device(device_id, name)

    def cancel_invitation(
        self,
        invitation_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        return self.store.cancel_invitation(invitation_id, now=now)

    def revoke_device(
        self,
        device_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        return self.store.revoke_device(device_id, now=now)

    def revoke_session(
        self,
        session_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        return self.store.revoke_session(session_id, now=now)

    def revoke_all(
        self,
        *,
        except_session_id: str | None = None,
        now: datetime | None = None,
    ) -> int:
        return self.store.revoke_all(
            except_session_id=except_session_id,
            now=now,
        )

    def prune(
        self,
        *,
        now: datetime | None = None,
        retention: timedelta = timedelta(days=30),
        max_events: int = 2000,
    ) -> dict[str, int]:
        return self.store.prune(
            now=now,
            retention=retention,
            max_events=max_events,
        )

    def _load_token_invitation(self, token: str) -> tuple[AccessInvitation, str]:
        parsed = parse_invitation_token(token)
        if parsed is None:
            raise InvitationClaimError("invalid_invitation")
        invitation_id, secret = parsed
        invitation = self.store.get_invitation(invitation_id)
        if invitation is None:
            raise InvitationClaimError("invalid_invitation")
        return invitation, secret


def _new_device_session(
    *,
    display_name: str,
    user_agent: str | None,
    effective_client: str | None,
    access_route: str | None,
    lifetime: SessionLifetime,
    now: datetime,
) -> tuple[AccessDevice, AccessSession, str]:
    """A new device and its first session; the session token is returned only here."""
    device_id = uuid.uuid4().hex
    session_id = uuid.uuid4().hex
    issued_session = issue_session_token(session_id)
    device = AccessDevice(
        id=device_id,
        display_name=(str(display_name or "").strip() or "Connected device")[:80],
        created_at=now,
        last_seen_at=now,
        revoked_at=None,
        user_agent=(str(user_agent)[:256] if user_agent else None),
        paired_from=(str(effective_client)[:128] if effective_client else None),
        access_route=(str(access_route)[:80] if access_route else None),
        legacy_source_id=None,
    )
    session = AccessSession(
        id=session_id,
        device_id=device_id,
        token_hash=issued_session.secret_hash,
        token_salt=issued_session.secret_salt,
        token_format=TokenFormat.SESSION_V1,
        created_at=now,
        last_seen_at=now,
        expires_at=now + session_ttl_for(lifetime),
        revoked_at=None,
        lifetime=lifetime,
        replaced_by_session_id=None,
    )
    return device, session, issued_session.token


def normalize_device_name(value: object) -> str | None:
    """A device name of 1–80 printable characters, trimmed; else ``None``."""
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > 80 or not name.isprintable():
        return None
    return name


def session_ttl_for(lifetime: SessionLifetime | str) -> timedelta:
    normalized = SessionLifetime(lifetime)
    if normalized is SessionLifetime.TRUSTED:
        return TRUSTED_SESSION_TTL
    if normalized is SessionLifetime.TEMPORARY:
        return TEMPORARY_SESSION_TTL
    raise ValueError("migrated session expiry is established during migration")


def canonicalize_origin(origin: str) -> str:
    """Validate and normalize an exact HTTP(S) origin."""
    text = str(origin or "").strip()
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid intended origin") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("intended origin must use http or https")
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("intended origin must contain only scheme, host, and port")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("invalid intended origin host") from exc
    if ":" in host:
        host = f"[{host}]"
    scheme = parsed.scheme.lower()
    default_port = 80 if scheme == "http" else 443
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{scheme}://{authority}"


__all__ = [
    "AccessService",
    "CreatedInvitation",
    "InvitationClaim",
    "InvitationClaimError",
    "InvitationInspection",
    "SESSION_REFRESH_POLL_INTERVAL",
    "SessionRefreshRecord",
    "SessionRefreshResult",
    "SetupCodeClaim",
    "TEMPORARY_SESSION_TTL",
    "TRUSTED_SESSION_RENEWAL_WINDOW",
    "TRUSTED_SESSION_TTL",
    "canonicalize_origin",
    "normalize_device_name",
    "session_ttl_for",
]
