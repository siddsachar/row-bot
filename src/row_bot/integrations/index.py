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
from typing import TYPE_CHECKING
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

if TYPE_CHECKING:
    from row_bot.mcp_client.marketplace import MarketplaceEntry

SCHEMA = 3
LIMIT = 200
_GENERATION = re.compile(r"registry-[0-9a-f]{16}\.sqlite3")
_LOCK = threading.RLock()
_READY: dict = {}
_TABLES = """
CREATE TABLE entries(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, title TEXT NOT NULL, app TEXT NOT NULL,
    verified INTEGER NOT NULL, featured INTEGER, setup INTEGER NOT NULL, updated INTEGER NOT NULL, icon TEXT,
    quality INTEGER NOT NULL, row BLOB NOT NULL);
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


# Placeholder and test records: template titles and descriptions, staging namespaces. Hidden unless asked for.
_TEMPLATE_TITLES = {"my mcp server", "my mcp serv", "my server", "my first mcp server", "my mcp", "mcp", "mcp server", "server",
                    "test", "test server", "test mcp server", "mcp test server", "demo", "demo server", "example server",
                    "example mcp server", "sample server", "sample mcp server", "hello world", "untitled"}
_TEMPLATE = re.compile(r"(description of|this is) (my|the|your|an?) (mcp )?server.*|non functional server.*|"
                       r"(an? |my |the )?(simple )?(mcp )?(test|demo|example|sample) (mcp )?server( for testing)?|"
                       r"todo|tbd|lorem ipsum.*|placeholder|description|hello world|(an? |my )?mcp server")


def owner_label(namespace: str) -> str:
    """The part of a Registry namespace that names its owner: ``io.github.acme`` and ``com.acme`` both give
    ``acme``. Prefixes like ``io.github`` or ``com`` would otherwise match half the Registry."""
    parts = namespace.casefold().split(".")
    if parts[:2] == ["io", "github"] and len(parts) > 2:
        return ".".join(parts[2:])
    return parts[1] if len(parts) > 1 else parts[0]


def _words_of(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", str(text or "").casefold()))


def low_quality(entry: MarketplaceEntry) -> bool:
    """A placeholder or test record: a template title or description, or a staging namespace."""
    name = (entry.metadata or {}).get("canonical_name") or entry.id
    title, description = _words_of(entry.name), _words_of(entry.description)
    return (not description or bool(_TEMPLATE.fullmatch(description)) or title in _TEMPLATE_TITLES
            or ".staging." in "." + name.split("/", 1)[0] + ".")


def derive(entry: MarketplaceEntry) -> dict:
    """What ranking needs about one record: its app, vendor verification, setup tier and freshness."""
    metadata = entry.metadata or {}
    refs = apps.registry_refs(metadata.get("canonical_name", ""))  # The namespace only; see sources._McpCatalog.row.
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
        derived = {name: derive(entry) for name, entry in by_name.items()}
        # Copies of one listing (same title and description) collapse into the best: vendor, featured, ready, fresh.
        best: dict = {}
        for name in sorted(by_name, key=lambda n: (not derived[n]["verified"], derived[n]["featured"] is None,
                                                   derived[n]["setup"], -derived[n]["updated"], n)):
            best.setdefault((_words_of(by_name[name].name), _words_of(by_name[name].description)), name)
        digest = hashlib.sha256()
        root = folder(create=True)
        previous = (pointer() or {}).get("file", "")
        name = "registry-" + uuid4().hex[:16] + ".sqlite3"
        building = root / (name + ".building")
        try:
            db = sqlite3.connect(building)
            try:
                db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + _TABLES)
                for count, name_key in enumerate(sorted(by_name)):
                    if cancelled():
                        raise ValueError("integration_search_cancelled")
                    entry, found = by_name[name_key], derived[name_key]
                    line = json.dumps(compact(entry), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                    digest.update((b"\n" if count else b"") + line.encode("utf-8"))
                    copy = best[(_words_of(entry.name), _words_of(entry.description))] != name_key
                    quality = 1 if low_quality(entry) else 2 if copy else 0
                    # Rows are stored compressed (their text repeats); icons get a column for updates.
                    cursor = db.execute("INSERT INTO entries(name, title, app, verified, featured, setup, updated, icon, quality, row)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (name_key, entry.name, found["app"], int(found["verified"]),
                        found["featured"], found["setup"], found["updated"], (entry.metadata or {}).get("icon"), quality,
                        zlib.compress(line.encode("utf-8"))))
                    # Only words people search for: the server part, a real title and the namespace's owner,
                    # never "io", "github" or "com" from the namespace itself.
                    namespace, _, server = name_key.rpartition("/")
                    db.execute("INSERT INTO fts(rowid, name, title, publisher, app_text, description) VALUES (?, ?, ?, ?, ?, ?)",
                               (cursor.lastrowid, server, server if entry.name == name_key else entry.name,
                                owner_label(namespace), found["app_text"], entry.description))
                db.commit()
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("catalog_index_corrupt")
            finally:
                db.close()
            with open(building, "rb+") as stream:
                os.fsync(stream.fileno())
            if cancelled():
                raise ValueError("integration_search_cancelled")
            os.replace(building, root / name)
            value = {"schema": SCHEMA, "file": name, "apps": apps.digest(), "captured_at": captured_at, "watermark": watermark,
                     "etag": etag, "digest": digest.hexdigest(), "count": len(by_name), "updated_at": updated_at,
                     "built_at": time.time()}
            for attempt in range(5):  # A search may hold the pointer open for a moment (Windows).
                try:
                    write_atomic(root / "registry.json", json.dumps(value, indent=2))
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
        except BaseException:
            for leftover in (building, root / name):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        _READY.clear()
        # The previous generation stays one more build, for searches that already read the old pointer.
        for old in root.glob("registry-*.sqlite3*"):
            if old.name not in {name, previous}:
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
        if current and not _intact(folder() / current["file"]):
            current = None  # Torn by a crash or damaged: rebuilt from the release snapshot.
        if current and (shipped is None or current["digest"] == shipped["digest"]
                        or registry_snapshot.instant(current["watermark"]) > registry_snapshot.instant(shipped["watermark"])):
            if current["apps"] == apps.digest():
                _READY["key"] = (current["file"], shipped_key)
                return current
            return build(list(rows(current)), captured_at=current["captured_at"], watermark=current["watermark"],
                         etag=current["etag"], updated_at=current["updated_at"])
        if shipped is None:
            raise ValueError("catalog_snapshot_unavailable")
        snapshot = registry_snapshot.read_snapshot()
        return build(snapshot["entries"], captured_at=snapshot["captured_at"], watermark=snapshot["watermark"])


def _intact(path: Path) -> bool:
    try:
        db = _connect(path)
        try:
            return db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            db.close()
    except sqlite3.Error:
        return False


def current() -> dict:
    """The index in use, for reading. Raises ``catalog_index_preparing`` before start-up built it."""
    value = pointer()
    if value is None:
        raise LookupError("catalog_index_preparing")
    return value


def rows(index: dict | None = None) -> Iterator[MarketplaceEntry]:
    """Every record of the mirror in use."""
    from row_bot.mcp_client.registry_snapshot import expand
    index = index or current()
    db = _connect(folder() / index["file"])
    try:
        for (row,) in db.execute("SELECT row FROM entries ORDER BY name"):
            yield expand(json.loads(zlib.decompress(row)))
    finally:
        db.close()


def lookup(entry_id: str) -> MarketplaceEntry | None:
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
    """Declared raster icons to cache during an update, only those served from their publisher's
    own domain: app records and vendor-verified ones first, then the freshest."""
    from row_bot.integrations.icons import publisher_host
    index = index or current()
    db = _connect(folder() / index["file"])
    try:
        found = db.execute("SELECT icon, name FROM entries WHERE icon IS NOT NULL "
                           "ORDER BY app = '', NOT verified, updated DESC, name").fetchall()
    finally:
        db.close()
    return [url for url, name in found if publisher_host(url, name.split("/", 1)[0])]


def _words(query: str) -> list[str]:
    words = [word for word in re.findall(r"\w+", query.casefold()) if re.search(r"[^\W_]", word)]
    return [word for word in words if len(word) > 1] or words


def _match(words: list[str], columns: str = "") -> str:
    terms = " AND ".join('"' + word.replace('"', '') + '"' + ("*" if len(word) > 1 else "") for word in words)
    return f"{{{columns}}} : ({terms})" if columns else terms


# ``sources.order`` in SQL, so SQLite keeps only the top rows however many match.
_ORDER = """ORDER BY NOT (e.featured IS NOT NULL OR e.verified),
    NOT (lower(e.title) = :wanted OR lower(e.name) = :wanted OR e.app IN (SELECT value FROM json_each(:apps))),
    NOT ({strong}), COALESCE(e.featured, 1000000), e.setup,
    CASE WHEN e.updated = 0 THEN 0 WHEN :now - e.updated <= 7776000 THEN -2 WHEN :now - e.updated <= 31536000 THEN -1 ELSE 0 END,
    lower(e.title), e.name LIMIT :limit"""
_NAMESPACE = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+(/[a-z0-9._-]*)?")


def search(query: str, *, limit: int = LIMIT, now: float | None = None, everything: bool = False) -> tuple[list[tuple], int, dict, int]:
    """Ranked records for a query (the empty query lists app records only), the number that
    matched, the index in use, and how many placeholder or duplicate records were left out
    (``everything`` keeps them). A query that is itself a namespace matches that namespace
    exactly. Every result is ``(entry, derived)``."""
    from row_bot.mcp_client.registry_snapshot import expand
    index, words, wanted = current(), _words(query), query.casefold().strip()
    named = [app.id for app in apps.catalog()[0].values() if wanted and app.name.casefold() == wanted]
    values = {"wanted": wanted or "\0", "apps": json.dumps(named), "now": time.time() if now is None else now, "limit": limit,
              "prefix": wanted.rstrip("/") + "/"}
    select = "SELECT e.id, e.app, e.verified, e.setup, e.updated, e.row FROM entries e "
    floor = "" if everything else " AND e.quality = 0"
    db = _connect(folder() / index["file"])
    try:
        where = "WHERE (e.name = :wanted OR substr(e.name, 1, length(:prefix)) = :prefix)"
        if _NAMESPACE.fullmatch(wanted) and db.execute("SELECT 1 FROM entries e " + where + " LIMIT 1", values).fetchone():
            matched, strong = where, "1"  # "io.github.zoom" or "com.notion/mcp" names a namespace: exactly that.
        elif words:
            values |= {"query": _match(words), "strong": _match(words, "name title publisher app_text")}
            matched, strong = "JOIN fts ON fts.rowid = e.id WHERE fts MATCH :query", "e.id IN (SELECT rowid FROM fts WHERE fts MATCH :strong)"
        else:
            matched, strong = "WHERE e.app != ''", "1"
        top = db.execute(select + matched + floor + " " + _ORDER.format(strong=strong), values).fetchall()
        # One count for both: everything that matched, and how much of it the floor leaves out.
        found, low = db.execute("SELECT count(*), total(e.quality > 0) FROM entries e " + matched, values).fetchone()
        hidden = 0 if everything or not (words or matched == where) else int(low)
        total = found - (0 if everything else int(low))
        results = [(expand(json.loads(zlib.decompress(row[5]))), {"app": row[1], "verified": bool(row[2]), "setup": row[3],
                                                                   "updated": row[4]}) for row in top]
        return results, total, index, hidden
    finally:
        db.close()


def by_app(app_id: str, *, limit: int = 32) -> list[MarketplaceEntry]:
    """An app's own records (its ways to connect), best first; never placeholders or copies."""
    from row_bot.mcp_client.registry_snapshot import expand
    try:
        db = _connect(folder() / current()["file"])
    except LookupError:
        return []
    try:
        found = db.execute("SELECT row FROM entries WHERE app = ? AND quality = 0 ORDER BY NOT verified, setup, updated DESC, name "
                           "LIMIT ?", (app_id, limit)).fetchall()
    finally:
        db.close()
    return [expand(json.loads(zlib.decompress(row[0]))) for row in found]
