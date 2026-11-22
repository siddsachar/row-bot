"""The Org server's Postgres layer: the pool, forward-only migrations and row-level security.

Two roles, by design:

- the **migrating role** runs ``row-bot-org migrate`` and owns the schema ``row_bot_org`` and its tables;
- the **runtime role** is what ``serve`` connects as: a member of ``row_bot_org_runtime`` (which the
  first migration creates and grants DML to), never a superuser, never the owner, without
  ``BYPASSRLS``. ``check-config`` warns when it is any of those.

Every principal-scoped table enables *and forces* row-level security, with policies written against
``row_bot_org.row_bot_principal()`` and ``row_bot_org.row_bot_in_group(text)``, and is read and written
only inside ``principal_transaction`` (``row_bot_org.db.rls``). Outside one those functions return
nothing, so a query sees no scoped rows even when application code forgets its ``WHERE``.

This needs the ``org`` dependency group (psycopg); nothing outside this package imports it at start.
"""
