"""The database side of sign-in (migration 0002).

Before a principal is known (a sign-in, a request's cookie) the runtime role calls the ``SECURITY DEFINER``
functions, which take one secret's verifier and return at most one row. After, everything runs inside
``principal_transaction`` and row-level security keeps each principal to its own rows. The operator
commands (``row-bot-org principals ...``) run as the migrating role, which the tables' ``_definer``
policies let through. The people functions (``list_people``, ``set_role``, ``set_status``) work in both: an
admin's transaction (``roles.admin_connection``, migration 0005's admin policies) or the migrating role.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg
from psycopg_pool import ConnectionPool

from row_bot_org.identity.oidc import VerifiedIdentity

SIGN_IN_LIFETIME = timedelta(minutes=10)


@dataclass(frozen=True)
class SignInAttempt:
    browser: bytes
    nonce: bytes
    code_verifier: str


@dataclass(frozen=True)
class SessionPrincipal:
    session_id: str
    principal_id: str
    groups: tuple[str, ...]


def begin_sign_in(pool: ConnectionPool, *, state: bytes, browser: bytes, nonce: bytes, code_verifier: str) -> None:
    with pool.connection() as conn:
        conn.execute("SELECT row_bot_org.begin_sign_in(%s, %s, %s, %s, %s)",
                     (state, browser, nonce, code_verifier, SIGN_IN_LIFETIME))


def take_sign_in(pool: ConnectionPool, state: bytes) -> SignInAttempt | None:
    """The unexpired attempt for ``state``, deleted as it is read; None when there is none."""
    with pool.connection() as conn:
        row = conn.execute("SELECT browser, nonce, code_verifier FROM row_bot_org.take_sign_in(%s)",
                           (state,)).fetchone()
    return None if row is None else SignInAttempt(bytes(row[0]), bytes(row[1]), row[2])


def sign_in(conn: psycopg.Connection, identity: VerifiedIdentity, *, verifier: bytes, lifetime: int, idle: int,
            user_agent: str, address: str, replaces: bytes | None) -> tuple[str, str]:
    """Make or update the principal and, when it is active, a session. Returns (principal id, status)."""
    principal_id, status = conn.execute(
        "SELECT principal_id, status FROM row_bot_org.sign_in(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (identity.issuer, identity.subject, identity.display_name, identity.email, list(identity.groups),
         verifier, timedelta(seconds=lifetime), timedelta(seconds=idle), user_agent, address, replaces),
    ).fetchone()
    return str(principal_id), status


def resume_session(pool: ConnectionPool, verifier: bytes) -> SessionPrincipal | None:
    with pool.connection() as conn:
        row = conn.execute("SELECT session_id, principal_id, groups FROM row_bot_org.resume_session(%s)",
                           (verifier,)).fetchone()
    return None if row is None else SessionPrincipal(str(row[0]), str(row[1]), tuple(row[2]))


def revoke_session(conn: psycopg.Connection, session_id: str) -> None:
    """Revoke one of the current principal's sessions (``conn`` is inside its principal_transaction)."""
    conn.execute("UPDATE row_bot_org.server_sessions SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
                 (session_id,))


def current_principal(conn: psycopg.Connection, principal_id: str) -> dict[str, object] | None:
    """The principal and its groups (``conn`` is inside its principal_transaction)."""
    row = conn.execute("SELECT id, display_name, email, status FROM row_bot_org.principals WHERE id = %s",
                       (principal_id,)).fetchone()
    if row is None:
        return None
    groups = [name for (name,) in conn.execute(
        "SELECT name FROM row_bot_org.principal_groups WHERE principal_id = %s ORDER BY name", (principal_id,))]
    return {"id": str(row[0]), "display_name": row[1], "email": row[2], "status": row[3], "groups": groups}


def revoke_sessions(conn: psycopg.Connection, principal_id: str) -> int:
    """Revoke every live session of ``principal_id`` (an operator's command, or an admin's transaction)."""
    return conn.execute(
        "UPDATE row_bot_org.server_sessions SET revoked_at = now() WHERE principal_id = %s AND revoked_at IS NULL",
        (principal_id,)).rowcount


# -- device credentials (migration 0007) ----------------------------------------------------------

@dataclass(frozen=True)
class DevicePrincipal:
    device_id: str
    principal_id: str
    groups: tuple[str, ...]


@dataclass(frozen=True)
class Lifetimes:
    access: timedelta
    refresh_idle: timedelta
    refresh: timedelta
    # How long after a refresh token's use the same token still gets a pair (its answer was lost).
    reuse: timedelta


@dataclass(frozen=True)
class Traded:
    """A token request's outcome: its device, and whether a reused credential revoked its tokens (then nothing
    was issued)."""

    device_id: str
    reused: bool


def issue_device_code(conn: psycopg.Connection, *, device_id: str, verifier: bytes, code_challenge: str,
                      redirect_uri: str, form_nonce: bytes) -> None:
    """A one-time code for one of the current principal's own devices (``conn`` is inside its
    principal_transaction); it lasts 60 seconds. A second code for the same form nonce is a UniqueViolation."""
    conn.execute("INSERT INTO row_bot_org.device_codes (device_id, verifier, code_challenge, redirect_uri, form_nonce)"
                 " VALUES (%s, %s, %s, %s, %s)", (device_id, verifier, code_challenge, redirect_uri, form_nonce))


def _traded(row: tuple | None) -> Traded | None:
    return None if row is None else Traded(str(row[0]), bool(row[1]))


def redeem_device_code(conn: psycopg.Connection, *, code: bytes, challenge: str, redirect_uri: str, access: bytes,
                       refresh: bytes, lifetimes: Lifetimes) -> Traded | None:
    """Trade a code for the two tokens' verifiers; None when nothing matched."""
    return _traded(conn.execute(
        "SELECT device_id, reused FROM row_bot_org.redeem_device_code(%s, %s, %s, %s, %s, %s, %s, %s)",
        (code, challenge, redirect_uri, access, refresh, lifetimes.access, lifetimes.refresh_idle,
         lifetimes.refresh)).fetchone())


def refresh_device_token(conn: psycopg.Connection, *, refresh: bytes, access: bytes, new_refresh: bytes,
                         lifetimes: Lifetimes) -> Traded | None:
    """Rotate a refresh token; None when nothing matched."""
    return _traded(conn.execute(
        "SELECT device_id, reused FROM row_bot_org.refresh_device_token(%s, %s, %s, %s, %s, %s)",
        (refresh, access, new_refresh, lifetimes.access, lifetimes.refresh_idle, lifetimes.reuse)).fetchone())


def resume_device_token(pool: ConnectionPool, verifier: bytes) -> DevicePrincipal | None:
    with pool.connection() as conn:
        row = conn.execute("SELECT device_id, principal_id, groups FROM row_bot_org.resume_device_token(%s)",
                           (verifier,)).fetchone()
    return None if row is None else DevicePrincipal(str(row[0]), str(row[1]), tuple(row[2]))


def revoke_device_tokens(conn: psycopg.Connection, device_id: str) -> None:
    """Revoke every token of one of the current principal's devices (``conn`` is inside its principal_transaction)."""
    conn.execute("UPDATE row_bot_org.device_tokens SET revoked_at = now() WHERE device_id = %s"
                 " AND revoked_at IS NULL", (device_id,))


def leave_device(conn: psycopg.Connection, device_id: str) -> bool:
    """Revoke one of the current principal's devices and its tokens. Whether the device was live."""
    left = conn.execute("UPDATE row_bot_org.devices SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
                        (device_id,)).rowcount
    revoke_device_tokens(conn, device_id)
    return left == 1


# -- people (admins and operators) ----------------------------------------------------------------

_PERSON = ("SELECT p.id, p.display_name, p.email, p.role, p.status, p.last_sign_in_at,"
           " ARRAY(SELECT g.name FROM row_bot_org.principal_groups g WHERE g.principal_id = p.id ORDER BY g.name)"
           " FROM row_bot_org.principals p")


@dataclass(frozen=True)
class Person:
    id: str
    display_name: str
    email: str | None
    role: str
    status: str
    last_sign_in_at: datetime
    groups: tuple[str, ...]


def _person(row: tuple) -> Person:
    return Person(str(row[0]), row[1], row[2], row[3], row[4], row[5], tuple(row[6]))


def list_people(conn: psycopg.Connection) -> list[Person]:
    return [_person(row) for row in conn.execute(_PERSON + " ORDER BY p.display_name, p.email, p.id").fetchall()]


def get_person(conn: psycopg.Connection, principal_id: str) -> Person | None:
    row = conn.execute(_PERSON + " WHERE p.id = %s", (principal_id,)).fetchone()
    return None if row is None else _person(row)


def find_people(conn: psycopg.Connection, id_or_email: str) -> list[Person]:
    """The principal with this id, or every principal with this email (compared without case)."""
    try:
        principal_id = str(uuid.UUID(id_or_email))
    except ValueError:
        return [_person(row) for row in conn.execute(
            _PERSON + " WHERE lower(p.email) = lower(%s) ORDER BY p.id", (id_or_email.strip(),)).fetchall()]
    person = get_person(conn, principal_id)
    return [] if person is None else [person]


def active_admins(conn: psycopg.Connection) -> int:
    """Stored admins who aren't suspended: the ones who can act."""
    return conn.execute("SELECT count(*) FROM row_bot_org.principals "
                        "WHERE role = 'admin' AND status = 'active'").fetchone()[0]


def set_role(conn: psycopg.Connection, principal_id: str, role: str) -> str | None:
    """Store ``role`` (admin or member) for a principal. Returns its previous role, None when there is none."""
    row = conn.execute("SELECT role FROM row_bot_org.principals WHERE id = %s FOR UPDATE", (principal_id,)).fetchone()
    if row is not None and row[0] != role:
        conn.execute("UPDATE row_bot_org.principals SET role = %s WHERE id = %s", (role, principal_id))
    return None if row is None else row[0]


def set_status(conn: psycopg.Connection, principal_id: str, status: str) -> str | None:
    """Make a principal active or suspended (sign-in refuses a suspended one; its caller revokes its sessions).
    Returns its previous status, None when there is none."""
    row = conn.execute("SELECT status FROM row_bot_org.principals WHERE id = %s FOR UPDATE",
                       (principal_id,)).fetchone()
    if row is None:
        return None
    if row[0] != status:
        conn.execute("UPDATE row_bot_org.principals SET status = %s WHERE id = %s", (status, principal_id))
    return row[0]


__all__ = ["SIGN_IN_LIFETIME", "DevicePrincipal", "Lifetimes", "Person", "SessionPrincipal", "SignInAttempt",
           "Traded", "active_admins", "begin_sign_in", "current_principal", "find_people", "get_person", "issue_device_code",
           "leave_device", "list_people", "redeem_device_code", "refresh_device_token", "resume_device_token",
           "resume_session", "revoke_device_tokens", "revoke_session", "revoke_sessions", "set_role", "set_status", "sign_in",
           "take_sign_in"]
