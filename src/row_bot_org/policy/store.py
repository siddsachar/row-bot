"""The database side of the policy service (migration 0004). Every call takes a connection inside the
caller's ``principal_transaction``; row-level security keeps a member to the company policy and its own
groups' overrides, and lets only an admin's transaction (``roles.enter_admin``) read the rest or write."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from row_bot.policy import (
    Document, Policy, document_to_dict, not_wider, parse_override, parse_policy, resolve, still_granted,
)

# pg_advisory_xact_lock key for policy writes, so a version check and its write can't interleave with
# another's: the bytes of "RBOrgPol". (Pooled connections are READ COMMITTED, so the check after the lock
# sees every write committed before it.)
WRITE_LOCK = int.from_bytes(b"RBOrgPol", "big")


class VersionConflict(Exception):
    """The document named a version that is no longer the newest for its scope."""

    def __init__(self, current: int):
        super().__init__(f"the newest version is {current}")
        self.current = current


class NotWider(Exception):
    """A group override sets something that adds nothing to the company policy (``not_wider``)."""

    def __init__(self, settings: list[str]):
        super().__init__(", ".join(settings))
        self.settings = settings


@dataclass(frozen=True)
class Revision:
    """One stored version: the company policy (``group`` None) or one group's override."""

    version: int
    group: str | None
    document: Document
    changed_by: str
    changed_at: datetime


@dataclass(frozen=True)
class Written:
    """A stored version and, for a company policy, what each group's override still grants of what the
    change took away (``still_granted``)."""

    revision: Revision
    still_granted_by: dict[str, list[str]]


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
    """The newest company policy and the newest override of every group, cleared ones (``{}``) included
    (an admin's transaction)."""
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


def put(conn: psycopg.Connection, group: str | None, document: Document, *, changed_by: str) -> Written:
    """Store ``document`` as the newest version of the company policy (``group`` None) or of a group's
    override. ``document.version`` must be the newest version of that scope (0 when there is none), or
    ``VersionConflict``. An override must widen the current company policy in everything it sets, or
    ``NotWider``."""
    if document.override != (group is not None):
        raise ValueError("a group takes an override, the company a company policy")
    if document.version is None:
        raise ValueError("a write names the version it replaces")
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK,))
    (newest,) = conn.execute("SELECT COALESCE(max(version), 0) FROM row_bot_org.policies"
                             " WHERE group_name IS NOT DISTINCT FROM %s::text", (group,)).fetchone()
    if document.version != newest:
        raise VersionConflict(newest)
    company, overrides = current(conn)
    previous = company.document if company else None
    still: dict[str, list[str]] = {}
    if group is not None:
        if narrow := not_wider(previous, document):
            raise NotWider(narrow)
    else:
        for override in overrides:
            if granted := still_granted(previous, document, override.document):
                still[override.group or ""] = granted
    stored = document_to_dict(replace(document, version=None))
    row = conn.execute(
        f"INSERT INTO row_bot_org.policies (group_name, document, changed_by) VALUES (%s, %s, %s)"
        f" RETURNING {_COLUMNS}", (group, Jsonb(stored), changed_by)).fetchone()
    return Written(_revision(row), still)


__all__ = ["WRITE_LOCK", "NotWider", "Revision", "VersionConflict", "Written", "current", "effective", "history",
           "put"]
