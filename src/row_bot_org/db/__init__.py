"""The Org server's Postgres layer: the pool, forward-only migrations and row-level security.

Two roles, by design:

- the **migrating role** runs ``row-bot-org migrate`` (``ROW_BOT_ORG_MIGRATION_DATABASE_URL``) and owns
  the database, the schema ``row_bot_org`` and its tables;
- the **runtime role** is what ``serve`` connects as (``ROW_BOT_ORG_DATABASE_URL``): a member of the
  database's own runtime group (``row_bot_org.runtime_role()``, made by the first migration), never a
  superuser, never ``BYPASSRLS``, never able to ``SET ROLE`` to either (``serve``, the pool and ``/readyz``
  refuse it), and not the owner (``check-config`` warns).

Every principal-scoped table enables *and forces* row-level security, with policies written against
``row_bot_org.row_bot_principal()`` and ``row_bot_org.row_bot_in_group(text)``, reaches the runtime group
only through ``CALL row_bot_org.grant_runtime_access(...)``, and is read and written only inside
``principal_transaction`` (``row_bot_org.db.rls``). Outside one those functions return nothing, so a
query sees no scoped rows even when application code forgets its ``WHERE``.

This needs the ``org`` dependency group (psycopg); nothing outside this package imports it at start.
"""
