"""The database side of the policy service (migration 0004). Every call takes a connection inside the
caller's ``principal_transaction``; row-level security keeps a member to the company policy and its own
groups' overrides, and lets only an admin's transaction (``roles.enter_admin``) read the rest or write."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from row_bot.policy import Document, Policy, document_to_dict, parse_override, parse_policy, resolve

# pg_advisory_xact_lock key for policy writes, so a version check and its write can't interleave with
# another's: the bytes of "RBOrgPol".
WRITE_LOCK = int.from_bytes(b"RBOrgPol", "big")


class VersionConflict(Exception):
    """The document named a version that is no longer the newest for its scope."""

    def __init__(self, current: int):
        super().__init__(f"the newest version is {current}")
        self.current = current


@dataclass(frozen=True)
class Revision:
    """One stored version: the company policy (``group`` None) or one group's override."""

    version: int
    group: str | None
    document: Document
    changed_by: str
    changed_at: datetime


def _document(version: int, group: str | None, stored: dict) -> Document:
    parsed = parse_policy(stored) if group is None else parse_override(stored)
    return replace(parsed, version=version)


def _revision(row: tuple) -> Revision:
    version, group, stored, changed_by, changed_at = row
    return Revision(version, group, _document(version, group, stored), str(changed_by), changed_at)


_COLUMNS = "version, group_name, document, changed_by, changed_at"


def effective(conn: psycopg.Connection, groups: Iterable[str]) -> Policy:
    """The effective policy of a member of ``groups``: the newest company policy and the newest override of
    each of its groups, merged (``row_bot.policy.resolve``)."""
    rows = conn.execute(
        "SELECT DISTINCT ON (group_name) version, group_name, document FROM row_bot_org.policies"
        " WHERE group_name IS NULL OR group_name = ANY(%s) ORDER BY group_name, version DESC",
        (sorted(set(groups)),)).fetchall()
    company = None
    overrides = []
    for version, group, stored in rows:
        if group is None:
            company = _document(version, None, stored)
        else:
            overrides.append(_document(version, group, stored))
    return resolve(company, overrides)


def current(conn: psycopg.Connection) -> tuple[Revision | None, list[Revision]]:
    """The newest company policy and the newest override of every group (an admin's transaction)."""
    revisions = [_revision(row) for row in conn.execute(
        f"SELECT DISTINCT ON (group_name) {_COLUMNS} FROM row_bot_org.policies"
        " ORDER BY group_name, version DESC").fetchall()]
    company = next((revision for revision in revisions if revision.group is None), None)
    return company, [revision for revision in revisions if revision.group is not None]


def history(conn: psycopg.Connection, *, limit: int, before: int | None = None) -> list[Revision]:
    """Every version, newest first, ``limit`` at a time; ``before`` continues below a version."""
    return [_revision(row) for row in conn.execute(
        f"SELECT {_COLUMNS} FROM row_bot_org.policies WHERE %(before)s::bigint IS NULL OR version < %(before)s"
        " ORDER BY version DESC LIMIT %(limit)s", {"before": before, "limit": limit}).fetchall()]


def put(conn: psycopg.Connection, group: str | None, document: Document, *, changed_by: str) -> Revision:
    """Store ``document`` as the newest version of the company policy (``group`` None) or of a group's
    override. A ``document.version`` must be the newest version of that scope (0 when there is none), or
    ``VersionConflict``."""
    if document.override != (group is not None):
        raise ValueError("a group takes an override, the company a company policy")
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK,))
    (newest,) = conn.execute("SELECT COALESCE(max(version), 0) FROM row_bot_org.policies"
                             " WHERE group_name IS NOT DISTINCT FROM %s::text", (group,)).fetchone()
    if document.version is not None and document.version != newest:
        raise VersionConflict(newest)
    stored = document_to_dict(replace(document, version=None))
    row = conn.execute(
        f"INSERT INTO row_bot_org.policies (group_name, document, changed_by) VALUES (%s, %s, %s)"
        f" RETURNING {_COLUMNS}", (group, Jsonb(stored), changed_by)).fetchone()
    return _revision(row)


__all__ = ["WRITE_LOCK", "Revision", "VersionConflict", "current", "effective", "history", "put"]
