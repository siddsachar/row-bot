"""SQLite storage for Row-Bot mobile companion sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any

from row_bot.data_paths import get_mobile_db_path


def utc_now() -> datetime:
    """Return the current timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_iso(value: datetime | None = None) -> str:
    """Serialize a datetime for SQLite storage."""
    dt = value or utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    """Parse a stored UTC datetime."""
    if not value:
        return None
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class MobileDevice:
    id: str
    display_name: str
    token_hash: str
    token_salt: str
    created_at: str
    last_seen_at: str | None
    revoked_at: str | None
    user_agent: str | None
    paired_from: str | None
    access_mode: str | None
    scopes: tuple[str, ...]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MobileDevice":
        return cls(
            id=str(row["id"]),
            display_name=str(row["display_name"]),
            token_hash=str(row["token_hash"]),
            token_salt=str(row["token_salt"]),
            created_at=str(row["created_at"]),
            last_seen_at=row["last_seen_at"],
            revoked_at=row["revoked_at"],
            user_agent=row["user_agent"],
            paired_from=row["paired_from"],
            access_mode=row["access_mode"],
            scopes=tuple(json.loads(row["scopes_json"] or "[]")),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "revoked_at": self.revoked_at,
            "user_agent": self.user_agent,
            "paired_from": self.paired_from,
            "access_mode": self.access_mode,
            "scopes": list(self.scopes),
        }


@dataclass(frozen=True)
class PairingCode:
    id: str
    code_hash: str
    code_salt: str
    created_at: str
    expires_at: str
    claimed_at: str | None
    intended_origin: str | None
    access_mode: str | None
    failed_attempts: int
    locked_until: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PairingCode":
        return cls(
            id=str(row["id"]),
            code_hash=str(row["code_hash"]),
            code_salt=str(row["code_salt"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            claimed_at=row["claimed_at"],
            intended_origin=row["intended_origin"],
            access_mode=row["access_mode"],
            failed_attempts=int(row["failed_attempts"]),
            locked_until=row["locked_until"],
        )


@dataclass(frozen=True)
class MobileAccessEvent:
    id: str
    device_id: str | None
    event_type: str
    ip: str | None
    user_agent: str | None
    created_at: str
    detail: dict[str, Any]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MobileAccessEvent":
        return cls(
            id=str(row["id"]),
            device_id=row["device_id"],
            event_type=str(row["event_type"]),
            ip=row["ip"],
            user_agent=row["user_agent"],
            created_at=str(row["created_at"]),
            detail=json.loads(row["detail_json"] or "{}"),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "device_id": self.device_id,
            "event_type": self.event_type,
            "ip": self.ip,
            "user_agent": self.user_agent,
            "created_at": self.created_at,
            "detail": self.detail,
        }


class MobileAuthStore:
    """Persistent store for mobile devices, pairing codes, and access events."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else get_mobile_db_path()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS mobile_devices(
                  id TEXT PRIMARY KEY,
                  display_name TEXT NOT NULL,
                  token_hash TEXT NOT NULL UNIQUE,
                  token_salt TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  last_seen_at TEXT,
                  revoked_at TEXT,
                  user_agent TEXT,
                  paired_from TEXT,
                  access_mode TEXT,
                  scopes_json TEXT NOT NULL DEFAULT '["chat","workflows","approvals","settings"]'
                );

                CREATE TABLE IF NOT EXISTS mobile_pairing_codes(
                  id TEXT PRIMARY KEY,
                  code_hash TEXT NOT NULL UNIQUE,
                  code_salt TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  claimed_at TEXT,
                  intended_origin TEXT,
                  access_mode TEXT,
                  failed_attempts INTEGER NOT NULL DEFAULT 0,
                  locked_until TEXT
                );

                CREATE TABLE IF NOT EXISTS mobile_access_events(
                  id TEXT PRIMARY KEY,
                  device_id TEXT,
                  event_type TEXT NOT NULL,
                  ip TEXT,
                  user_agent TEXT,
                  created_at TEXT NOT NULL,
                  detail_json TEXT
                );

                CREATE TABLE IF NOT EXISTS mobile_kv(
                  key TEXT PRIMARY KEY,
                  value_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_mobile_devices_revoked
                  ON mobile_devices(revoked_at);
                CREATE INDEX IF NOT EXISTS idx_mobile_pairing_expires
                  ON mobile_pairing_codes(expires_at);
                CREATE INDEX IF NOT EXISTS idx_mobile_events_created
                  ON mobile_access_events(created_at);
                """
            )

    def create_pairing_code(
        self,
        *,
        code_hash: str,
        code_salt: str,
        expires_at: datetime,
        intended_origin: str | None = None,
        access_mode: str | None = None,
        code_id: str | None = None,
        now: datetime | None = None,
    ) -> PairingCode:
        self.ensure_schema()
        row_id = code_id or uuid.uuid4().hex
        created = to_iso(now)
        expires = to_iso(expires_at)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mobile_pairing_codes(
                    id, code_hash, code_salt, created_at, expires_at,
                    intended_origin, access_mode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (row_id, code_hash, code_salt, created, expires, intended_origin, access_mode),
            )
        pairing = self.get_pairing_code(row_id)
        assert pairing is not None
        return pairing

    def get_pairing_code(self, code_id: str) -> PairingCode | None:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mobile_pairing_codes WHERE id = ?",
                (code_id,),
            ).fetchone()
        return PairingCode.from_row(row) if row else None

    def record_pairing_failure(
        self,
        code_id: str,
        *,
        locked_until: datetime | None = None,
    ) -> PairingCode | None:
        self.ensure_schema()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE mobile_pairing_codes
                   SET failed_attempts = failed_attempts + 1,
                       locked_until = COALESCE(?, locked_until)
                 WHERE id = ?
                """,
                (to_iso(locked_until) if locked_until else None, code_id),
            )
        return self.get_pairing_code(code_id)

    def mark_pairing_claimed(self, code_id: str, *, now: datetime | None = None) -> bool:
        self.ensure_schema()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mobile_pairing_codes
                   SET claimed_at = ?
                 WHERE id = ?
                   AND claimed_at IS NULL
                """,
                (to_iso(now), code_id),
            )
            return cursor.rowcount == 1

    def clear_expired_pairing_codes(self, *, now: datetime | None = None) -> int:
        self.ensure_schema()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM mobile_pairing_codes
                 WHERE expires_at <= ?
                   AND claimed_at IS NULL
                """,
                (to_iso(now),),
            )
            return int(cursor.rowcount or 0)

    def create_device(
        self,
        *,
        display_name: str,
        token_hash: str,
        token_salt: str,
        user_agent: str | None = None,
        paired_from: str | None = None,
        access_mode: str | None = None,
        scopes: tuple[str, ...] | list[str] | None = None,
        device_id: str | None = None,
        now: datetime | None = None,
    ) -> MobileDevice:
        self.ensure_schema()
        row_id = device_id or uuid.uuid4().hex
        scope_values = list(scopes or ("chat", "workflows", "approvals", "settings"))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mobile_devices(
                    id, display_name, token_hash, token_salt, created_at,
                    user_agent, paired_from, access_mode, scopes_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    display_name,
                    token_hash,
                    token_salt,
                    to_iso(now),
                    user_agent,
                    paired_from,
                    access_mode,
                    json.dumps(scope_values),
                ),
            )
        device = self.get_device(row_id)
        assert device is not None
        return device

    def get_device(self, device_id: str) -> MobileDevice | None:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mobile_devices WHERE id = ?",
                (device_id,),
            ).fetchone()
        return MobileDevice.from_row(row) if row else None

    def list_devices(self, *, include_revoked: bool = True) -> list[MobileDevice]:
        self.ensure_schema()
        query = "SELECT * FROM mobile_devices"
        params: tuple[Any, ...] = ()
        if not include_revoked:
            query += " WHERE revoked_at IS NULL"
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [MobileDevice.from_row(row) for row in rows]

    def touch_device(self, device_id: str, *, now: datetime | None = None) -> None:
        self.ensure_schema()
        with self._connect() as conn:
            conn.execute(
                "UPDATE mobile_devices SET last_seen_at = ? WHERE id = ? AND revoked_at IS NULL",
                (to_iso(now), device_id),
            )

    def revoke_device(self, device_id: str, *, now: datetime | None = None) -> bool:
        self.ensure_schema()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE mobile_devices
                   SET revoked_at = COALESCE(revoked_at, ?)
                 WHERE id = ?
                """,
                (to_iso(now), device_id),
            )
            return cursor.rowcount == 1

    def log_event(
        self,
        event_type: str,
        *,
        device_id: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        detail: dict[str, Any] | None = None,
        now: datetime | None = None,
        event_id: str | None = None,
    ) -> MobileAccessEvent:
        self.ensure_schema()
        row_id = event_id or uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mobile_access_events(
                    id, device_id, event_type, ip, user_agent, created_at,
                    detail_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    device_id,
                    event_type,
                    ip,
                    user_agent,
                    to_iso(now),
                    json.dumps(detail or {}),
                ),
            )
        event = self.get_event(row_id)
        assert event is not None
        return event

    def get_event(self, event_id: str) -> MobileAccessEvent | None:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mobile_access_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        return MobileAccessEvent.from_row(row) if row else None

    def recent_events(self, *, limit: int = 50) -> list[MobileAccessEvent]:
        self.ensure_schema()
        safe_limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mobile_access_events
                 ORDER BY created_at DESC
                 LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [MobileAccessEvent.from_row(row) for row in rows]

    def set_kv(self, key: str, value: dict[str, Any], *, now: datetime | None = None) -> None:
        self.ensure_schema()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mobile_kv(key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value), to_iso(now)),
            )

    def get_kv(self, key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        self.ensure_schema()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value_json FROM mobile_kv WHERE key = ?",
                (key,),
            ).fetchone()
        if not row:
            return dict(default or {})
        return json.loads(row["value_json"] or "{}")
