"""Who may use the admin API, for now: a placeholder until people, groups and roles (S2) replace it.

``is_admin`` is the only check. Today it is membership of the server's group ``row-bot-org-admins``
(after the identity provider's ``group_map``, so IT decides who that is). ``admin_transaction`` runs a
request as the admin: the principal's transaction with ``row_bot.admin`` set, which the admin policies
(``row_bot_org.row_bot_is_admin()``, migration 0003) read. Like ``row_bot.principal`` it guards against
application code that forgets a ``WHERE``, not against SQL the runtime role runs on its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request

from row_bot_org.identity.gate import RequestPrincipal, request_principal

if TYPE_CHECKING:
    import psycopg
    from psycopg_pool import ConnectionPool

ADMIN_GROUP = "row-bot-org-admins"


def is_admin(principal: RequestPrincipal) -> bool:
    """Placeholder (S2 replaces it): a member of ``ADMIN_GROUP``."""
    return ADMIN_GROUP in principal.groups


@contextmanager
def admin_transaction(pool: ConnectionPool, principal: RequestPrincipal) -> Iterator[psycopg.Connection]:
    """The principal's transaction, acting as an admin. Only after ``is_admin(principal)``."""
    from row_bot_org.db.rls import principal_transaction

    if not is_admin(principal):
        raise PermissionError("not an admin")
    with principal_transaction(pool, principal.principal_id, principal.groups) as conn:
        conn.execute("SELECT set_config('row_bot.admin', 'on', true)")
        yield conn


def require_admin(principal: RequestPrincipal = Depends(request_principal)) -> RequestPrincipal:
    """The route's caller, when an admin; 403 otherwise."""
    if not is_admin(principal):
        raise HTTPException(status_code=403, detail="admin_required")
    return principal


def admin_connection(request: Request,
                     principal: RequestPrincipal = Depends(require_admin)) -> Iterator[psycopg.Connection]:
    """An ``admin_transaction`` connection; use with ``scope="function"`` so it commits before the response."""
    with admin_transaction(request.app.state.pool, principal) as conn:
        yield conn


__all__ = ["ADMIN_GROUP", "admin_connection", "admin_transaction", "is_admin", "require_admin"]
