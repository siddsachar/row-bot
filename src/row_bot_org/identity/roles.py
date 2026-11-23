"""Who may administer the server.

**A placeholder until S2 (people, groups, roles):** ``is_admin`` says no to everyone, so the admin routes
answer 403 on a real server until S2 replaces it with the roles it stores.

``enter_admin`` is what an admin route calls first. When the caller is an admin it also marks the
transaction as an admin's (``row_bot.admin``), which ``row_bot_org.row_bot_is_admin()`` (migration 0003)
reads for the tables' admin policies, so a route that forgets the check can't read or write as one.
"""

from __future__ import annotations

import psycopg

from row_bot_org.identity.gate import RequestPrincipal


def is_admin(principal: RequestPrincipal) -> bool:
    """Whether ``principal`` is an admin. Placeholder: nobody is, until S2."""
    return False


def enter_admin(conn: psycopg.Connection, principal: RequestPrincipal) -> bool:
    """Whether ``principal`` is an admin; when it is, this transaction (``conn``, inside the principal's
    ``principal_transaction``) acts as one."""
    if not is_admin(principal):
        return False
    conn.execute("SELECT set_config('row_bot.admin', 'on', true)")
    return True


__all__ = ["enter_admin", "is_admin"]
