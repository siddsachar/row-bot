"""Who may administer the server: the one admin check.

Roles: **admin** and **member** are stored per principal (``principals.role``, migration 0005; anyone who
signs in is a member). **Space owner** is a role on a space, stored with the spaces (B1); only its name is
defined here.

A principal is an admin when its stored role is ``admin``, or when it is in one of ``ROW_BOT_ORG_IDP``'s
``admin_groups``. Those are Row-Bot groups that ``group_map`` maps a provider group to (``identity.config``
refuses any other), so a provider group that merely has the name never makes an admin.

``enter_admin`` is what every admin route runs first, through ``admin_connection``. When the caller is an
admin it marks the transaction as an admin's (``row_bot.admin``), which ``row_bot_org.row_bot_is_admin()``
(migration 0003) reads for the tables' admin policies, so a route that forgets the check reads and writes as
a member. Like ``row_bot.principal`` it guards against application code that forgets a check, not against
SQL the runtime role runs on its own.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
from fastapi import Depends, Request

from row_bot_org.db.rls import principal_transaction
from row_bot_org.identity.gate import RequestPrincipal, request_principal
from row_bot_org.settings import OrgSettings

ADMIN = "admin"
MEMBER = "member"
SPACE_OWNER = "space_owner"
ROLES = (ADMIN, MEMBER, SPACE_OWNER)


class AdminRequired(Exception):
    """The caller isn't an admin: the app answers 403 ``{"error": "admin_required"}``."""


def admin_groups(settings: OrgSettings) -> frozenset[str]:
    """The groups whose members are admins (``admin_groups`` in ``ROW_BOT_ORG_IDP``)."""
    return frozenset(settings.idp.admin_groups) if settings.idp is not None else frozenset()


def is_admin(conn: psycopg.Connection, principal: RequestPrincipal, settings: OrgSettings) -> bool:
    """Whether ``principal`` is an admin: its stored role, or an admin group (``conn`` is inside its
    ``principal_transaction``)."""
    if admin_groups(settings) & set(principal.groups):
        return True
    row = conn.execute("SELECT role FROM row_bot_org.principals WHERE id = %s",
                       (principal.principal_id,)).fetchone()
    return row is not None and row[0] == ADMIN


def enter_admin(conn: psycopg.Connection, principal: RequestPrincipal, settings: OrgSettings) -> bool:
    """Whether ``principal`` is an admin; when it is, this transaction (``conn``, inside the principal's
    ``principal_transaction``) acts as one. Only from a browser session: a device's bearer token never acts as an
    admin (the gate already keeps it off the admin routes)."""
    if principal.device_id is not None or not is_admin(conn, principal, settings):
        return False
    conn.execute("SELECT set_config('row_bot.admin', 'on', true)")
    return True


def admin_connection(request: Request,
                     principal: RequestPrincipal = Depends(request_principal)) -> Iterator[psycopg.Connection]:
    """The caller's transaction, acting as an admin; ``AdminRequired`` (403) before anything else runs when the
    caller isn't one. Use with ``scope="function"`` so it commits before the response."""
    with principal_transaction(request.app.state.pool, principal.principal_id, principal.groups) as conn:
        if not enter_admin(conn, principal, request.app.state.settings):
            raise AdminRequired
        yield conn


__all__ = ["ADMIN", "MEMBER", "ROLES", "SPACE_OWNER", "AdminRequired", "admin_connection", "admin_groups",
           "enter_admin", "is_admin"]
