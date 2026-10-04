"""The local Registry mirror: a derived SQLite FTS5 index in the data folder.

It is built at start-up from the shipped snapshot (and rebuilt there when a
release ships a newer one), or from its own rows plus an update the user asked
for, always into a new generation file. The new file serves only after an atomic
pointer swap, so a failed build leaves the previous index in use. Searching only
reads: it never builds, writes or contacts a source.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4
import zlib

from row_bot.integrations import apps
from row_bot.integrations.safe import write_atomic

SCHEMA = 2
LIMIT = 200
_GENERATION = re.compile(r"registry-[0-9a-f]{16}\.sqlite3")
_LOCK = threading.RLock()
_READY: dict = {}
_TABLES = """
CREATE TABLE entries(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, title TEXT NOT NULL, app TEXT NOT NULL,
    verified INTEGER NOT NULL, featured INTEGER, setup INTEGER NOT NULL, updated INTEGER NOT NULL, icon TEXT, row BLOB NOT NULL);
CREATE INDEX entries_app ON entries(app) WHERE app != '';
CREATE VIRTUAL TABLE fts USING fts5(name, title, publisher, app_text, description, content='',
    tokenize='unicode61 remove_diacritics 2', prefix='2 3');
"""


def folder(*, create: bool = False) -> Path:
    from row_bot.data_paths import get_row_bot_data_dir
    path = get_row_bot_data_dir(create=create) / "catalogs"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def pointer() -> dict | None:
    """The generation in use, if its pointer is intact."""
    try:
        value = json.loads((folder() / "registry.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA or not _GENERATION.fullmatch(str(value.get("file")))
            or not (folder() / value["file"]).is_file()):
        return None
    return value


def epoch(day: str) -> int:
    try:
        return int(datetime.strptime(day[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return 0


def derive(entry) -> dict:
    """What ranking needs about one record: its app, vendor verification, setup tier and freshness."""
    metadata = entry.metadata or {}
    refs = apps.registry_refs(metadata.get("canonical_name", "")) + apps.recipe_refs(entry.install)
    app = apps.match(refs)
    installable = bool(entry.install) and metadata.get("status") == "active"
    # Authentication is known for local servers, and for hosted ones whose app documents it.
    known = not (entry.install or {}).get("url") or bool(app and app.auth in {"oauth", "api_key", "none"})
    setup = 0 if installable and known else 1 if installable else 2
    return {"app": app.id if app else "", "verified": apps.verified(app, refs), "featured": app.featured_rank if app else None,
            "setup": setup, "updated": epoch(metadata.get("updated_at", "")), "app_text": apps.text(app)}


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, check_same_thread=False)


def build(entries: Iterable, *, captured_at: float, watermark: str, etag: str = "", updated_at: float = 0,
          cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Write a new generation, check it, then swap the pointer. Returns the new pointer.

    ``digest`` is the sha256 of the sorted compact records, the same digest as a snapshot
    of the same records, so a mirror built from the shipped snapshot can be checked."""
    with _LOCK:
        from row_bot.mcp_client.registry_snapshot import compact
        by_name = {(entry.metadata or {}).get("canonical_name") or entry.id: entry for entry in entries}
        digest = hashlib.sha256()
        root = folder(create=True)
        name = "registry-" + uuid4().hex[:16] + ".sqlite3"
        building = root / (name + ".building")
        try:
            db = sqlite3.connect(building)
            try:
                db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + _TABLES)
                for count, name_key in enumerate(sorted(by_name)):
                    if cancelled():
                        raise ValueError("integration_search_cancelled")
                    entry = by_name[name_key]
                    found = derive(entry)
                    line = json.dumps(compact(entry), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    digest.update((b"\n" if count else b"") + line.encode("utf-8"))
                    # Rows are stored compressed (their text repeats); icons get a column for updates.
                    cursor = db.execute("INSERT INTO entries(name, title, app, verified, featured, setup, updated, icon, row)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (name_key, entry.name, found["app"], int(found["verified"]),
                        found["featured"], found["setup"], found["updated"], (entry.metadata or {}).get("icon"),
                        zlib.compress(line.encode("utf-8"))))
                    # Index the server part and the namespace; "io.github." alone would match half the Registry.
                    namespace, _, server = name_key.rpartition("/")
                    db.execute("INSERT INTO fts(rowid, name, title, publisher, app_text, description) VALUES (?, ?, ?, ?, ?, ?)",
                               (cursor.lastrowid, server, entry.name, namespace.removeprefix("io.github."), found["app_text"],
                                entry.description))
                db.commit()
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("catalog_index_corrupt")
            finally:
                db.close()
            if cancelled():
                raise ValueError("integration_search_cancelled")
            os.replace(building, root / name)
            value = {"schema": SCHEMA, "file": name, "apps": apps.digest(), "captured_at": captured_at, "watermark": watermark,
                     "etag": etag, "digest": digest.hexdigest(), "count": len(by_name), "updated_at": updated_at,
                     "built_at": time.time()}
            write_atomic(root / "registry.json", json.dumps(value, indent=2))
        except BaseException:
            for leftover in (building, root / name):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        _READY.clear()
        for old in root.glob("registry-*.sqlite3*"):
            if old.name != name:
                try:
                    old.unlink()
                except OSError:  # Still open in a reader (Windows); removed after a later build.
                    pass
        return value


def ensure() -> dict:
    """The index in use, built first from the shipped snapshot when it is missing, outdated
    by a newer release snapshot, or derived under other app rules."""
    from row_bot.mcp_client import registry_snapshot
    with _LOCK:
        current = pointer()
        try:
            shipped_key = (str(registry_snapshot.SHIPPED), registry_snapshot.SHIPPED.stat().st_mtime_ns)
        except OSError:
            shipped_key = None
        if current and _READY.get("key") == (current["file"], shipped_key):
            return current
        try:
            shipped = registry_snapshot.read_header()
        except (OSError, ValueError, EOFError, lzma.LZMAError):
            shipped = None
        # Keep a mirror that is newer than the release snapshot (or is that snapshot).
        if current and (shipped is None or current["watermark"] > shipped["watermark"] or current["digest"] == shipped["digest"]):
            if current["apps"] == apps.digest():
                _READY["key"] = (current["file"], shipped_key)
                return current
            return build(list(rows(current)), captured_at=current["captured_at"], watermark=current["watermark"],
                         etag=current["etag"], updated_at=current["updated_at"])
        if shipped is None:
            raise ValueError("catalog_snapshot_unavailable")
        snapshot = registry_snapshot.read_snapshot()
        return build(snapshot["entries"], captured_at=snapshot["captured_at"], watermark=snapshot["watermark"])


def current() -> dict:
    """The index in use, for reading. Raises ``catalog_index_preparing`` before start-up built it."""
    value = pointer()
    if value is None:
        raise LookupError("catalog_index_preparing")
    return value


def rows(index: dict | None = None) -> Iterator:
    """Every record of the mirror in use."""
    from row_bot.mcp_client.registry_snapshot import expand
    index = index or current()
    db = _connect(folder() / index["file"])
    try:
        for (row,) in db.execute("SELECT row FROM entries ORDER BY name"):
            yield expand(json.loads(zlib.decompress(row)))
    finally:
        db.close()


def lookup(entry_id: str):
    """One record by its ``name@version`` id, or None."""
    from row_bot.mcp_client.registry_snapshot import expand
    try:
        db = _connect(folder() / current()["file"])
    except LookupError:
        return None
    try:
        found = db.execute("SELECT row FROM entries WHERE name = ?", (entry_id.rpartition("@")[0],)).fetchone()
    finally:
        db.close()
    entry = expand(json.loads(zlib.decompress(found[0]))) if found else None
    return entry if entry and entry.id == entry_id else None


def icon_urls(index: dict | None = None) -> list[str]:
    """Declared raster icons to cache during an update: app records and vendor-verified ones
    first, then the freshest."""
    index = index or current()
    db = _connect(folder() / index["file"])
    try:
        return [url for (url,) in db.execute("SELECT icon FROM entries WHERE icon IS NOT NULL "
                                             "ORDER BY app = '', NOT verified, updated DESC, name")]
    finally:
        db.close()


def _words(query: str) -> list[str]:
    words = re.findall(r"\w+", query.casefold())
    return [word for word in words if len(word) > 1] or words


def _match(words: list[str], columns: str = "") -> str:
    terms = " AND ".join('"' + word.replace('"', '') + '"' + ("*" if len(word) > 1 else "") for word in words)
    return f"{{{columns}}} : ({terms})" if columns else terms


# ``sources.order`` in SQL, so SQLite keeps only the top rows however many match.
_ORDER = """ORDER BY NOT (lower(e.title) = :wanted OR lower(e.name) = :wanted OR e.app IN (SELECT value FROM json_each(:apps))),
    NOT (e.featured IS NOT NULL OR e.verified), NOT ({strong}), COALESCE(e.featured, 1000000), e.setup,
    CASE WHEN e.updated = 0 THEN 0 WHEN :now - e.updated <= 7776000 THEN -2 WHEN :now - e.updated <= 31536000 THEN -1 ELSE 0 END,
    lower(e.title), e.name LIMIT :limit"""


def search(query: str, *, limit: int = LIMIT, now: float | None = None) -> tuple[list[tuple], int, dict]:
    """Ranked records for a query (the empty query lists app records only), the number that
    matched, and the index in use. Every result is ``(entry, derived)``."""
    from row_bot.mcp_client.registry_snapshot import expand
    index, words, wanted = current(), _words(query), query.casefold().strip()
    named = [app.id for app in apps.catalog()[0].values() if wanted and app.name.casefold() == wanted]
    values = {"wanted": wanted or "\0", "apps": json.dumps(named), "now": time.time() if now is None else now, "limit": limit}
    select = "SELECT e.id, e.app, e.verified, e.setup, e.updated, e.row FROM entries e "
    db = _connect(folder() / index["file"])
    try:
        if words:
            values |= {"query": _match(words), "strong": _match(words, "name title publisher app_text")}
            strong = "e.id IN (SELECT rowid FROM fts WHERE fts MATCH :strong)"
            top = db.execute(select + "JOIN fts ON fts.rowid = e.id WHERE fts MATCH :query " + _ORDER.format(strong=strong),
                             values).fetchall()
            total = db.execute("SELECT count(*) FROM fts WHERE fts MATCH :query", values).fetchone()[0]
        else:
            top = db.execute(select + "WHERE e.app != '' " + _ORDER.format(strong="1"), values).fetchall()
            total = db.execute("SELECT count(*) FROM entries WHERE app != ''").fetchone()[0]
        results = [(expand(json.loads(zlib.decompress(row[5]))), {"app": row[1], "verified": bool(row[2]), "setup": row[3],
                                                                   "updated": row[4]}) for row in top]
        return results, total, index
    finally:
        db.close()
