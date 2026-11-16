"""Each editor's connection and the chats editors reach (``editors.db`` in the data folder).

An editor's token lives only in the keychain (namespace ``editors``), where ``row-bot mcp`` reads
it; ``editors.db`` keeps its SHA-256, so the app can check a token without keeping it. Revoking an
editor deletes both: its next call is refused at once. Tokens are never logged.

A chat an editor made ("From Claude Code · row-bot") or continued is "reached from an editor": it
runs at its own level but never Hands-off, as a chat other people share (``full_autonomy``).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from row_bot.editors.catalog import editor as catalog_editor

logger = logging.getLogger(__name__)

DB_NAME = "editors.db"
SECRET_NAMESPACE = "editors"
TOKEN_PREFIX = "rbe1_"

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS editors ("
    " editor_id TEXT PRIMARY KEY, token_sha256 TEXT NOT NULL UNIQUE,"
    " connected_at TEXT NOT NULL, last_used_at TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS editor_threads ("
    " thread_id TEXT PRIMARY KEY, editor_id TEXT NOT NULL, folder_key TEXT NOT NULL DEFAULT '',"
    " made INTEGER NOT NULL DEFAULT 0, last_used_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS editor_threads_chat ON editor_threads(editor_id, folder_key) WHERE made=1",
)


class EditorError(RuntimeError):
    """A connection change that couldn't happen; ``code`` says why in a stable word."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def db_path() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / DB_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    for statement in _SCHEMA:
        connection.execute(statement)
    return connection


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _known(editor_id: str) -> str:
    if catalog_editor(editor_id) is None:
        raise EditorError("unknown_editor")
    return editor_id


# --- Connections -----------------------------------------------------------------------------------------

def connect(editor_id: str) -> None:
    """A new token for this editor in the keychain (replacing any earlier one, which stops working)."""
    from row_bot import secret_store

    _known(editor_id)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    try:
        secret_store.set_secret(editor_id, token, namespace=SECRET_NAMESPACE)
    except secret_store.SecretStoreError:
        logger.warning("The keychain couldn't keep a token for %s", editor_id)
        raise EditorError("keychain_unavailable") from None
    try:
        with closing(_connect()) as connection, connection:
            connection.execute(
                "INSERT INTO editors(editor_id, token_sha256, connected_at, last_used_at) VALUES(?,?,?,'')"
                " ON CONFLICT(editor_id) DO UPDATE SET token_sha256=excluded.token_sha256,"
                " connected_at=excluded.connected_at, last_used_at=''",
                (editor_id, _digest(token), _now()),
            )
    except sqlite3.Error:
        _forget_token(editor_id)
        raise EditorError("dependency_unavailable") from None


def revoke(editor_id: str) -> bool:
    """The editor's token stops working now, and leaves the keychain."""
    _known(editor_id)
    with closing(_connect()) as connection, connection:
        removed = connection.execute("DELETE FROM editors WHERE editor_id=?", (editor_id,)).rowcount
    _forget_token(editor_id)
    return bool(removed)


def _forget_token(editor_id: str) -> None:
    from row_bot import secret_store

    try:
        secret_store.delete_secret(editor_id, namespace=SECRET_NAMESPACE)
    except secret_store.SecretStoreError:
        logger.warning("The keychain couldn't remove the token for %s; it no longer works", editor_id)


def connections() -> list[dict[str, str]]:
    """The connected editors, with when each was connected and last used."""
    if not db_path().exists():
        return []
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT editor_id, connected_at, last_used_at FROM editors ORDER BY connected_at").fetchall()
    return [{"editor_id": row[0], "connected_at": row[1], "last_used_at": row[2]}
            for row in rows if catalog_editor(row[0]) is not None]


def authenticate(token: object) -> str | None:
    """The connected editor this token belongs to (its use recorded), or None."""
    if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX) or len(token) > 128:
        return None
    if not db_path().exists():
        return None
    digest = _digest(token)
    with closing(_connect()) as connection, connection:
        row = connection.execute("SELECT editor_id, token_sha256 FROM editors WHERE token_sha256=?",
                                 (digest,)).fetchone()
        if row is None or not hmac.compare_digest(row[1], digest) or catalog_editor(row[0]) is None:
            return None
        connection.execute("UPDATE editors SET last_used_at=? WHERE editor_id=?", (_now(), row[0]))
    return str(row[0])


def read_token(editor_id: str) -> str | None:
    """This editor's token from the keychain (``row-bot mcp`` sends it with each call), or None."""
    from row_bot import secret_store

    if catalog_editor(editor_id) is None:
        return None
    try:
        return secret_store.get_secret(editor_id, namespace=SECRET_NAMESPACE)
    except secret_store.SecretStoreError:
        return None


# --- Chats reached from editors ----------------------------------------------------------------------------

def chat_for(editor_id: str, folder_key: str) -> str | None:
    """The chat this editor made for this folder ("" for none), if it made one."""
    if not db_path().exists():
        return None
    with closing(_connect()) as connection:
        row = connection.execute("SELECT thread_id FROM editor_threads WHERE editor_id=? AND folder_key=? AND made=1",
                                 (editor_id, folder_key)).fetchone()
    return str(row[0]) if row else None


def remember(thread_id: str, editor_id: str, *, folder_key: str = "", made: bool = False) -> None:
    """Record that an editor made or continued this chat (made: the one chat for that editor and folder)."""
    _known(editor_id)
    with closing(_connect()) as connection, connection:
        if made:
            connection.execute("DELETE FROM editor_threads WHERE editor_id=? AND folder_key=? AND made=1",
                               (editor_id, folder_key))
            connection.execute(
                "INSERT INTO editor_threads(thread_id, editor_id, folder_key, made, last_used_at) VALUES(?,?,?,1,?)"
                " ON CONFLICT(thread_id) DO UPDATE SET editor_id=excluded.editor_id, folder_key=excluded.folder_key,"
                " made=1, last_used_at=excluded.last_used_at",
                (thread_id, editor_id, folder_key, _now()))
        else:
            connection.execute(
                "INSERT INTO editor_threads(thread_id, editor_id, folder_key, made, last_used_at) VALUES(?,?,'',0,?)"
                " ON CONFLICT(thread_id) DO UPDATE SET last_used_at=excluded.last_used_at",
                (thread_id, editor_id, _now()))


def reached_from_editor(thread_id: str) -> bool:
    """Whether an editor made or continued this chat. Raises when ``editors.db`` can't be read."""
    if not thread_id or not db_path().exists():
        return False
    with closing(_connect()) as connection:
        return connection.execute("SELECT 1 FROM editor_threads WHERE thread_id=?", (thread_id,)).fetchone() is not None
