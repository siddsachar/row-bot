"""Who may use the admin API, for now: a placeholder until people, groups and roles (S2) replace it.

``is_admin`` is the only check. An admin is a member of the server group ``row-bot-org-admins``, and only
when the identity provider's ``group_map`` has an explicit entry mapping one of its groups to it (with a
map, every unmapped group is dropped at sign-in). A provider group that merely has that name never makes
an admin. ``admin_transaction`` runs a request as the admin: the principal's transaction with
``row_bot.admin`` set, which the admin policies (``row_bot_org.row_bot_is_admin()``, migration 0003) read.
Like ``row_bot.principal`` it guards against application code that forgets a ``WHERE``, not against SQL
the runtime role runs on its own.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request

from row_bot_org.identity.gate import RequestPrincipal, request_principal
from row_bot_org.settings import OrgSettings

if TYPE_CHECKING:
    import psycopg
    from psycopg_pool import ConnectionPool

ADMIN_GROUP = "row-bot-org-admins"


def is_admin(principal: RequestPrincipal, settings: OrgSettings) -> bool:
    """Placeholder (S2 replaces it): in ``ADMIN_GROUP``, which an explicit ``group_map`` entry grants."""
    mapped = settings.idp is not None and ADMIN_GROUP in {group for _, group in settings.idp.group_map}
    return mapped and ADMIN_GROUP in principal.groups


@contextmanager
def admin_transaction(pool: ConnectionPool, principal: RequestPrincipal,
                      settings: OrgSettings) -> Iterator[psycopg.Connection]:
    """The principal's transaction, acting as an admin; ``PermissionError`` unless ``is_admin``."""
    from row_bot_org.db.rls import principal_transaction

    if not is_admin(principal, settings):
        raise PermissionError("not an admin")
    with principal_transaction(pool, principal.principal_id, principal.groups) as conn:
        conn.execute("SELECT set_config('row_bot.admin', 'on', true)")
        yield conn


def require_admin(request: Request, principal: RequestPrincipal = Depends(request_principal)) -> RequestPrincipal:
    """The route's caller, when an admin; 403 otherwise."""
    if not is_admin(principal, request.app.state.settings):
        raise HTTPException(status_code=403, detail="admin_required")
    return principal


def admin_connection(request: Request,
                     principal: RequestPrincipal = Depends(require_admin)) -> Iterator[psycopg.Connection]:
    """An ``admin_transaction`` connection; use with ``scope="function"`` so it commits before the response."""
    with admin_transaction(request.app.state.pool, principal, request.app.state.settings) as conn:
        yield conn


__all__ = ["ADMIN_GROUP", "admin_connection", "admin_transaction", "is_admin", "require_admin"]
