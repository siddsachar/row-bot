"""The review queue's database calls (migration 0010).

Each takes a connection already inside a principal's transaction (``principal_transaction``, perhaps acting as an
admin): row-level security decides which submissions it sees, and the migration's triggers which change it may make.
Every column list is fixed here; values always go as parameters.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

SHARE = "share"
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
STATUSES = (PENDING, APPROVED, REJECTED)
# Pending submissions one person may have at once.
MAX_PENDING = 20
# Shares one person may send in a (UTC) day, withdrawn ones included.
MAX_PER_DAY = 50
# The most one list answers with.
MAX_LISTED = 200

# The space's name as the transaction reads it, else the name an approval moved it to (kept for its submitter, who
# may not read that space); NULL for a space its submitter no longer reads that it was sent to.
_COLUMNS = ("s.id, s.kind, s.space_id, coalesce(sp.name, s.moved_to), s.sent_space_id, s.title, s.body,"
            " s.source_note, s.submitted_by, s.submitted_at, s.status, s.reviewed_by, s.decided_at, s.reason,"
            " s.fact_id")
_TODAY = "(now() AT TIME ZONE 'UTC')::date"
_FROM = "row_bot_org.brain_submissions s LEFT JOIN row_bot_org.spaces sp ON sp.id = s.space_id"


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _uuid(value: Any) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True)
class SubmissionRow:
    id: str
    kind: str
    space_id: str
    space_name: str | None
    sent_space_id: str
    title: str
    text: str
    source_note: str | None
    submitted_by: str
    submitted_at: datetime
    status: str
    reviewed_by: str | None
    decided_at: datetime | None
    reason: str | None
    fact_id: str | None

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "space_id": self.space_id, "space_name": self.space_name,
                "sent_space_id": self.sent_space_id, "title": self.title, "text": self.text, "source_note": self.source_note,
                "submitted_by": self.submitted_by, "submitted_at": _time(self.submitted_at), "status": self.status,
                "reviewed_by": self.reviewed_by, "decided_at": _time(self.decided_at), "reason": self.reason,
                "fact_id": self.fact_id}


def _row(row: tuple) -> SubmissionRow:
    return SubmissionRow(str(row[0]), row[1], str(row[2]), row[3], str(row[4]), row[5], row[6], row[7], str(row[8]),
                         row[9], row[10], _uuid(row[11]), row[12], row[13], _uuid(row[14]))


def get_submission(conn: psycopg.Connection, submission_id: str, *, lock: bool = False) -> SubmissionRow | None:
    """One submission the transaction may see; with ``lock``, held until the transaction ends (only a manager of
    its space, whose policy allows the update, gets it so)."""
    query = f"SELECT {_COLUMNS} FROM {_FROM} WHERE s.id = %s" + (" FOR UPDATE OF s" if lock else "")
    row = conn.execute(query, (submission_id,)).fetchone()
    return None if row is None else _row(row)


def pending_count(conn: psycopg.Connection, principal_id: str) -> int:
    (count,) = conn.execute("SELECT count(*) FROM row_bot_org.brain_submissions WHERE submitted_by = %s"
                            " AND status = 'pending'", (principal_id,)).fetchone()
    return count


def hold_submitter(conn: psycopg.Connection, principal_id: str) -> None:
    """Hold this person's submitting until the transaction ends, so two at once can't both pass the limits."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("row_bot_org.submit:" + principal_id,))


def shares_today(conn: psycopg.Connection, principal_id: str) -> int:
    row = conn.execute(f"SELECT shares FROM row_bot_org.brain_share_days WHERE submitted_by = %s AND day = {_TODAY}",
                       (principal_id,)).fetchone()
    return 0 if row is None else row[0]


def count_share(conn: psycopg.Connection, principal_id: str) -> None:
    """One more share today for this person (their earlier days' counts go)."""
    conn.execute(f"DELETE FROM row_bot_org.brain_share_days WHERE submitted_by = %s AND day < {_TODAY}",
                 (principal_id,))
    conn.execute(f"INSERT INTO row_bot_org.brain_share_days (day, shares) VALUES ({_TODAY}, 1)"
                 " ON CONFLICT (submitted_by, day) DO UPDATE SET shares = row_bot_org.brain_share_days.shares + 1")


def add_submission(conn: psycopg.Connection, *, space_id: str, title: str, text: str, source_note: str | None,
                   submitter_groups: Sequence[str]) -> SubmissionRow:
    (submission_id,) = conn.execute(
        "INSERT INTO row_bot_org.brain_submissions (kind, space_id, sent_space_id, title, body, source_note,"
        " submitter_groups) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (SHARE, space_id, space_id, title, text, source_note, list(submitter_groups))).fetchone()
    return get_submission(conn, str(submission_id))


def _listed(conn: psycopg.Connection, where: str, values: dict[str, Any], status: str | None) -> tuple[
        list[SubmissionRow], bool]:
    # The queue oldest first; decided ones the most recently decided first.
    order = "s.submitted_at, s.id" if status == PENDING else "coalesce(s.decided_at, s.submitted_at) DESC, s.id"
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM {_FROM} WHERE {where} AND (%(status)s::text IS NULL OR s.status = %(status)s)"
        f" ORDER BY {order} LIMIT %(limit)s", {**values, "status": status, "limit": MAX_LISTED + 1}).fetchall()
    return [_row(row) for row in rows[:MAX_LISTED]], len(rows) > MAX_LISTED


def list_own(conn: psycopg.Connection, principal_id: str, status: str | None) -> tuple[list[SubmissionRow], bool]:
    """The person's own submissions (and whether there were more than ``MAX_LISTED``)."""
    return _listed(conn, "s.submitted_by = %(me)s", {"me": principal_id}, status)


def list_for_review(conn: psycopg.Connection, space_ids: Sequence[str], status: str | None) -> tuple[
        list[SubmissionRow], bool]:
    """The submissions of these spaces (the ones the caller reviews: its own in other spaces stay out)."""
    return _listed(conn, "s.space_id = ANY(%(spaces)s::uuid[])", {"spaces": list(space_ids)}, status)


def count_pending(conn: psycopg.Connection, space_ids: Sequence[str]) -> int:
    (count,) = conn.execute("SELECT count(*) FROM row_bot_org.brain_submissions WHERE status = 'pending'"
                            " AND space_id = ANY(%s::uuid[])", (list(space_ids),)).fetchone()
    return count


def withdraw(conn: psycopg.Connection, submission_id: str, principal_id: str) -> bool:
    """Withdraw one's own pending submission, which deletes it: False when it isn't (any longer) one's own and
    pending."""
    return conn.execute("DELETE FROM row_bot_org.brain_submissions WHERE id = %s AND submitted_by = %s"
                        " AND status = 'pending'", (submission_id, principal_id)).rowcount == 1


def add_fact_from(conn: psycopg.Connection, submission: SubmissionRow, *, space_id: str, text: str,
                  writer_groups: Sequence[str]) -> str:
    """The fact an approval makes: the reviewer's (``written_by``, by default), with the share and its submitter
    as its source. Its id."""
    (fact_id,) = conn.execute(
        "INSERT INTO row_bot_org.brain_facts (space_id, body, writer_groups, source_submission_id, submitted_by)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (space_id, text, list(writer_groups), submission.id, submission.submitted_by)).fetchone()
    return str(fact_id)


def approve(conn: psycopg.Connection, submission_id: str, *, principal_id: str, space_id: str, moved_to: str | None,
            title: str, text: str, fact_id: str) -> SubmissionRow:
    """Approved as ``fact_id`` in ``space_id`` (``moved_to``: that space's name, when it isn't the one it was sent
    to)."""
    conn.execute(
        "UPDATE row_bot_org.brain_submissions SET status = 'approved', reviewed_by = %s, decided_at = now(),"
        " space_id = %s, moved_to = %s, title = %s, body = %s, fact_id = %s WHERE id = %s AND status = 'pending'",
        (principal_id, space_id, moved_to, title, text, fact_id, submission_id))
    return get_submission(conn, submission_id)


def reject(conn: psycopg.Connection, submission_id: str, *, principal_id: str, reason: str) -> SubmissionRow:
    conn.execute(
        "UPDATE row_bot_org.brain_submissions SET status = 'rejected', reviewed_by = %s, decided_at = now(),"
        " reason = %s WHERE id = %s AND status = 'pending'", (principal_id, reason, submission_id))
    return get_submission(conn, submission_id)


__all__ = ["APPROVED", "MAX_LISTED", "MAX_PENDING", "MAX_PER_DAY", "PENDING", "REJECTED", "SHARE", "STATUSES",
           "SubmissionRow", "add_fact_from", "add_submission", "approve", "count_pending", "count_share",
           "get_submission", "hold_submitter", "list_for_review", "list_own", "pending_count", "reject",
           "shares_today", "withdraw"]
