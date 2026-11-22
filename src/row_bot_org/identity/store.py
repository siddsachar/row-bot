"""The database side of sign-in (migration 0002).

Before a principal is known (a sign-in, a request's cookie) the runtime role calls the ``SECURITY DEFINER``
functions, which take one secret's verifier and return at most one row. After, everything runs inside
``principal_transaction`` and row-level security keeps each principal to its own rows. The operator
command (``row-bot-org principals revoke-sessions``) runs as the migrating role, which the tables'
``_definer`` policies let through.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import psycopg
from psycopg_pool import ConnectionPool

from row_bot_org.identity.oidc import VerifiedIdentity

SIGN_IN_LIFETIME = timedelta(minutes=10)


@dataclass(frozen=True)
class SignInAttempt:
    browser: bytes
    nonce: bytes
    code_verifier: str
    idp: str


@dataclass(frozen=True)
class SessionPrincipal:
    session_id: str
    principal_id: str
    groups: tuple[str, ...]


def begin_sign_in(pool: ConnectionPool, *, state: bytes, browser: bytes, nonce: bytes, code_verifier: str,
                  idp: str) -> None:
    with pool.connection() as conn:
        conn.execute("SELECT row_bot_org.begin_sign_in(%s, %s, %s, %s, %s, %s)",
                     (state, browser, nonce, code_verifier, idp, SIGN_IN_LIFETIME))


def take_sign_in(pool: ConnectionPool, state: bytes) -> SignInAttempt | None:
    """The unexpired attempt for ``state``, deleted as it is read; None when there is none."""
    with pool.connection() as conn:
        row = conn.execute("SELECT browser, nonce, code_verifier, idp FROM row_bot_org.take_sign_in(%s)",
                           (state,)).fetchone()
    return None if row is None else SignInAttempt(bytes(row[0]), bytes(row[1]), row[2], row[3])


def sign_in(pool: ConnectionPool, identity: VerifiedIdentity, *, verifier: bytes, lifetime: int, idle: int,
            user_agent: str, address: str, replaces: bytes | None) -> tuple[str, str]:
    """Make or update the principal and, when it is active, a session. Returns (principal id, status)."""
    with pool.connection() as conn:
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
    """Revoke every live session of ``principal_id`` (an operator's command, as the migrating role)."""
    return conn.execute(
        "UPDATE row_bot_org.server_sessions SET revoked_at = now() WHERE principal_id = %s AND revoked_at IS NULL",
        (principal_id,)).rowcount


__all__ = ["SIGN_IN_LIFETIME", "SessionPrincipal", "SignInAttempt", "begin_sign_in", "current_principal",
           "resume_session", "revoke_session", "revoke_sessions", "sign_in", "take_sign_in"]
