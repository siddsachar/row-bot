"""The gateway's database calls (migration 0003). Each takes a connection already inside a principal's
transaction (``principal_transaction``) or an admin's (``roles.admin_connection``); row-level security
decides which rows it sees.

Sealed keys stay in this module and ``web``: nothing here returns one to a caller outside the server.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql

from row_bot.policy import ALL_MODELS, Policy

ENDPOINT_COLUMNS = "id, name, api, base_url, key_sealed IS NOT NULL, timeout_seconds, enabled, created_at, updated_at"
MODEL_COLUMNS = ("id, endpoint_id, name, upstream_model, display_name, kind, context_window, input_price, output_price,"
                 " is_default")


@dataclass(frozen=True)
class Route:
    """Where a model's calls go: what the gateway needs, the sealed key included."""

    model: str
    upstream_model: str
    kind: str
    input_price: Decimal
    output_price: Decimal
    endpoint_id: str
    endpoint_name: str
    api: str
    base_url: str
    key_sealed: bytes | None
    timeout_seconds: int
    enabled: bool


@dataclass(frozen=True)
class BudgetState:
    scope: str
    subject: str
    soft_limit: Decimal | None
    hard_limit: Decimal | None
    spent: Decimal


def _assignments(changes: dict[str, Any]) -> list[sql.Composable]:
    return [sql.SQL("{} = %s").format(sql.Identifier(column)) for column in changes]


def _money(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _endpoint(row: tuple) -> dict[str, Any]:
    return {"id": str(row[0]), "name": row[1], "api": row[2], "base_url": row[3], "has_key": row[4],
            "timeout_seconds": row[5], "enabled": row[6], "created_at": row[7].isoformat(),
            "updated_at": row[8].isoformat()}


def _model(row: tuple) -> dict[str, Any]:
    return {"id": str(row[0]), "endpoint_id": str(row[1]), "name": row[2], "upstream_model": row[3],
            "display_name": row[4], "kind": row[5], "context_window": row[6], "input_price": _money(row[7]),
            "output_price": _money(row[8]), "is_default": row[9]}


# -- what a principal may use -----------------------------------------------------------------------

def allowed(policy: Policy, model: str) -> bool:
    """Whether the effective ``policy`` lets its person use the catalog model ``model`` (``models.allowed``)."""
    return ALL_MODELS in policy.models.allowed or model in policy.models.allowed


def available_models(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """The catalog's models on enabled endpoints (``allowed`` filters them by the caller's policy)."""
    rows = conn.execute(
        "SELECT m.name, m.display_name, m.kind, e.api, m.context_window, m.is_default"
        " FROM row_bot_org.catalog_models m JOIN row_bot_org.model_endpoints e ON e.id = m.endpoint_id"
        " WHERE e.enabled ORDER BY m.kind, m.display_name, m.name").fetchall()
    return [{"id": r[0], "display_name": r[1], "kind": r[2], "api": r[3], "context_window": r[4], "default": r[5]}
            for r in rows]


def route(conn: psycopg.Connection, model: str) -> Route | None:
    """The route for ``model``; None when it isn't in the catalog (the caller checks ``allowed`` first)."""
    row = conn.execute(
        "SELECT m.name, m.upstream_model, m.kind, m.input_price, m.output_price, e.id, e.name, e.api, e.base_url,"
        " e.key_sealed, e.timeout_seconds, e.enabled"
        " FROM row_bot_org.catalog_models m JOIN row_bot_org.model_endpoints e ON e.id = m.endpoint_id"
        " WHERE m.name = %s", (model,)).fetchone()
    if row is None:
        return None
    return Route(row[0], row[1], row[2], row[3], row[4], str(row[5]), row[6], row[7], row[8],
                 None if row[9] is None else bytes(row[9]), row[10], row[11])


def budget_state(conn: psycopg.Connection) -> list[BudgetState]:
    """The caller's own and its groups' budgets, with this month's spend."""
    rows = conn.execute("SELECT scope, subject, soft_limit, hard_limit, spent"
                        " FROM row_bot_org.current_budget_state() ORDER BY scope DESC, subject").fetchall()
    return [BudgetState(*row) for row in rows]


def record_usage(conn: psycopg.Connection, *, principal_id: str, groups: tuple[str, ...], model: str,
                 input_tokens: int, output_tokens: int, cost: Decimal, duration_ms: int, outcome: str) -> None:
    conn.execute(
        "INSERT INTO row_bot_org.usage_events (principal_id, groups, model, input_tokens, output_tokens, cost,"
        " duration_ms, outcome) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (principal_id, list(groups), model, input_tokens, output_tokens, cost, duration_ms, outcome))


# -- the admin's catalog ----------------------------------------------------------------------------

def list_endpoints(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return [_endpoint(r) for r in conn.execute(
        f"SELECT {ENDPOINT_COLUMNS} FROM row_bot_org.model_endpoints ORDER BY name").fetchall()]


def create_endpoint(conn: psycopg.Connection, *, endpoint_id: str, name: str, api: str, base_url: str,
                    key_sealed: bytes | None, timeout_seconds: int, enabled: bool) -> dict[str, Any]:
    return _endpoint(conn.execute(
        "INSERT INTO row_bot_org.model_endpoints (id, name, api, base_url, key_sealed, timeout_seconds, enabled)"
        f" VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING {ENDPOINT_COLUMNS}",
        (endpoint_id, name, api, base_url, key_sealed, timeout_seconds, enabled)).fetchone())


def update_endpoint(conn: psycopg.Connection, endpoint_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """Apply ``changes`` (column: value; ``key_sealed`` among them) to one endpoint."""
    query = sql.SQL("UPDATE row_bot_org.model_endpoints SET {} WHERE id = %s RETURNING " + ENDPOINT_COLUMNS).format(
        sql.SQL(", ").join([*_assignments(changes), sql.SQL("updated_at = now()")]))
    row = conn.execute(query, (*changes.values(), endpoint_id)).fetchone()
    return None if row is None else _endpoint(row)


def endpoint_target(conn: psycopg.Connection, endpoint_id: str) -> tuple[str, str] | None:
    """An endpoint's api and base URL, or None."""
    row = conn.execute("SELECT api, base_url FROM row_bot_org.model_endpoints WHERE id = %s", (endpoint_id,)).fetchone()
    return None if row is None else (row[0], row[1])


def delete_endpoint(conn: psycopg.Connection, endpoint_id: str) -> bool:
    return conn.execute("DELETE FROM row_bot_org.model_endpoints WHERE id = %s", (endpoint_id,)).rowcount == 1


def list_models(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return [_model(r) for r in conn.execute(
        f"SELECT {MODEL_COLUMNS} FROM row_bot_org.catalog_models ORDER BY kind, name").fetchall()]


def _clear_default(conn: psycopg.Connection, kind: str, keep: str | None) -> None:
    conn.execute("UPDATE row_bot_org.catalog_models SET is_default = false"
                 " WHERE kind = %s AND is_default AND id IS DISTINCT FROM %s", (kind, keep))


def create_model(conn: psycopg.Connection, fields: dict[str, Any]) -> dict[str, Any]:
    if fields.get("is_default"):
        _clear_default(conn, fields["kind"], None)
    query = sql.SQL("INSERT INTO row_bot_org.catalog_models ({}) VALUES ({}) RETURNING " + MODEL_COLUMNS).format(
        sql.SQL(", ").join(map(sql.Identifier, fields)), sql.SQL(", ").join([sql.Placeholder()] * len(fields)))
    return _model(conn.execute(query, tuple(fields.values())).fetchone())


def model_kind(conn: psycopg.Connection, model_id: str) -> str | None:
    row = conn.execute("SELECT kind FROM row_bot_org.catalog_models WHERE id = %s", (model_id,)).fetchone()
    return None if row is None else row[0]


def update_model(conn: psycopg.Connection, model_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    kind = model_kind(conn, model_id)
    if kind is None:
        return None
    if changes.get("is_default"):
        _clear_default(conn, kind, model_id)
    if not changes:
        return _model(conn.execute(f"SELECT {MODEL_COLUMNS} FROM row_bot_org.catalog_models WHERE id = %s",
                                   (model_id,)).fetchone())
    query = sql.SQL("UPDATE row_bot_org.catalog_models SET {} WHERE id = %s RETURNING " + MODEL_COLUMNS).format(
        sql.SQL(", ").join(_assignments(changes)))
    return _model(conn.execute(query, (*changes.values(), model_id)).fetchone())


def delete_model(conn: psycopg.Connection, model_id: str) -> bool:
    return conn.execute("DELETE FROM row_bot_org.catalog_models WHERE id = %s", (model_id,)).rowcount == 1


# -- budgets and usage ------------------------------------------------------------------------------

def _budget(row: tuple) -> dict[str, Any]:
    return {"id": str(row[0]), "scope": row[1], "subject": row[2], "soft_limit": _money(row[3]),
            "hard_limit": _money(row[4])}


def list_budgets(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return [_budget(r) for r in conn.execute(
        "SELECT id, scope, subject, soft_limit, hard_limit FROM row_bot_org.budgets ORDER BY scope, subject")]


def put_budget(conn: psycopg.Connection, *, scope: str, subject: str, soft_limit: Decimal | None,
               hard_limit: Decimal | None) -> dict[str, Any]:
    return _budget(conn.execute(
        "INSERT INTO row_bot_org.budgets (scope, subject, soft_limit, hard_limit) VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (scope, subject) DO UPDATE SET soft_limit = EXCLUDED.soft_limit,"
        " hard_limit = EXCLUDED.hard_limit RETURNING id, scope, subject, soft_limit, hard_limit",
        (scope, subject, soft_limit, hard_limit)).fetchone())


def delete_budget(conn: psycopg.Connection, budget_id: str) -> bool:
    return conn.execute("DELETE FROM row_bot_org.budgets WHERE id = %s", (budget_id,)).rowcount == 1


_USAGE_KEYS = {
    "person": "u.principal_id::text",
    "group": "g.name",
    "model": "u.model",
}


def usage_report(conn: psycopg.Connection, *, by: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Calls, tokens and cost from ``start`` up to ``end``, by person, group or model."""
    key = _USAGE_KEYS[by]
    groups = " CROSS JOIN LATERAL unnest(u.groups) AS g (name)" if by == "group" else ""
    rows = conn.execute(
        f"SELECT {key}, count(*), sum(u.input_tokens), sum(u.output_tokens), sum(u.cost)"
        f" FROM row_bot_org.usage_events u{groups} WHERE u.created_at >= %s AND u.created_at < %s"
        f" GROUP BY 1 ORDER BY 5 DESC, 1", (start, end)).fetchall()
    return [{by: r[0], "calls": r[1], "input_tokens": int(r[2]), "output_tokens": int(r[3]), "cost": float(r[4])}
            for r in rows]


__all__ = ["BudgetState", "Route", "allowed", "available_models", "budget_state", "create_endpoint", "create_model",
           "delete_budget", "delete_endpoint", "delete_model", "endpoint_target", "list_budgets", "list_endpoints",
           "list_models", "model_kind", "put_budget", "record_usage", "route", "update_endpoint", "update_model",
           "usage_report"]
