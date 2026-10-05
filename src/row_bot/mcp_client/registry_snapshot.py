"""The full Registry mirror's shipped snapshot and its sync; searches never contact the Registry.

The snapshot is xz-compressed JSON lines: a header (source, capture time,
watermark, record count and the sha256 of the records), then one compact
normalized record per latest server, sorted by name. The same Registry data
always gives the same bytes. Only an explicit sync (the developer build or a
user's catalog update) reads the Registry: read-only, paginated and polite.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict
from typing import TYPE_CHECKING
import hashlib
import io
import json
import lzma
import math
from pathlib import Path
import re
import time
from urllib.parse import quote, urlencode

if TYPE_CHECKING:
    from row_bot.mcp_client.marketplace import MarketplaceEntry

SOURCE = "https://registry.modelcontextprotocol.io/v0.1/servers"
SHIPPED = Path(__file__).with_name("registry_snapshot.jsonl.xz")
SCHEMA = 4  # 4: recipes carry their declared inputs.
MAX_RECORDS = 250_000
MAX_BYTES = 160 * 1024 * 1024
MAX_AGE = 7 * 24 * 3600
_FIXED = {"source": "official", "classification": "official-registry"}


def compact(entry: MarketplaceEntry) -> dict:
    """A record without its fixed and empty fields (every default is empty); a declared input keeps
    only what differs from a declaration's defaults."""
    row = {key: value for key, value in asdict(entry).items() if key not in _FIXED and value not in ("", None, [], {}, False)}
    if (row.get("install") or {}).get("inputs"):
        row["install"] = {**row["install"], "inputs": [
            {key: value for key, value in item.items() if key in {"key", "target", "name"} or not (
                value in ("", [], False) or (key == "format" and value == "string") or (key == "label" and value == item["name"]))}
            for item in row["install"]["inputs"]]}
    return row


def expand(row: dict) -> MarketplaceEntry:
    from row_bot.integrations.inputs import declaration
    from row_bot.mcp_client.marketplace import MarketplaceEntry
    if (row.get("install") or {}).get("inputs"):
        row = {**row, "install": {**row["install"], "inputs": [
            declaration(item["key"], **{key: value for key, value in item.items() if key != "key"})
            for item in row["install"]["inputs"]]}}
    return MarketplaceEntry(**{"description": "", **row, **_FIXED})


def build_snapshot(entries: Iterable[MarketplaceEntry], *, captured_at: float, watermark: str = "",
                   complete: bool = True) -> bytes:
    """Reproducible normalized metadata, with no package contents or execution."""
    by_name = {(entry.metadata or {}).get("canonical_name") or entry.id: entry for entry in entries}
    lines = [json.dumps(compact(by_name[name]), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
             for name in sorted(by_name)]
    body = "\n".join(lines).encode("utf-8")
    header = {"schema_version": SCHEMA, "source": SOURCE, "api_version": "v0.1", "captured_at": captured_at,
              "watermark": watermark, "complete": complete, "count": len(lines), "digest": hashlib.sha256(body).hexdigest()}
    text = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n" + body + b"\n"
    return lzma.compress(text, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=6 | lzma.PRESET_EXTREME)


def instant(value: object) -> str:
    """A Registry timestamp in one fixed-width form that sorts as time ('' if it is not one)."""
    found = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(?:Z|\+00:00)", str(value or ""))
    return f"{found.group(1)}.{(found.group(2) or '').ljust(9, '0')}Z" if found else ""


def _header(line: bytes) -> dict:
    header = json.loads(line)
    if (not isinstance(header, dict) or header.get("schema_version") != SCHEMA or header.get("source") != SOURCE
            or not isinstance(header.get("count"), int) or not 0 <= header["count"] <= MAX_RECORDS
            or not isinstance(header.get("captured_at"), (int, float)) or not math.isfinite(header["captured_at"])
            or not isinstance(header.get("watermark"), str) or not isinstance(header.get("digest"), str)):
        raise ValueError("invalid_registry_snapshot")
    return header


def read_header(path: Path | None = None) -> dict:
    """The snapshot header alone, without reading its records."""
    path = path or SHIPPED
    if path.is_symlink():
        raise ValueError("invalid_registry_snapshot")
    with lzma.open(path, "rb") as stream:
        return _header(stream.readline(65536))


def read_snapshot(path: Path | None = None) -> dict:
    """Every record of an intact snapshot; a changed or oversized one is refused."""
    path = path or SHIPPED
    if path.is_symlink():
        raise ValueError("invalid_registry_snapshot")
    with lzma.open(path, "rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("invalid_registry_snapshot")
    first, _, body = data.partition(b"\n")
    header, body = _header(first), body.removesuffix(b"\n")
    if hashlib.sha256(body).hexdigest() != header["digest"]:
        raise ValueError("registry_snapshot_digest_changed")
    entries = [expand(json.loads(line)) for line in io.BytesIO(body)] if body else []
    if len(entries) != header["count"]:
        raise ValueError("invalid_registry_snapshot")
    stale = time.time() - header["captured_at"] > MAX_AGE
    return {**header, "entries": entries, "status": "stale" if stale else "cached"}


def _get(url: str, headers: dict, meta: dict) -> bytes:
    from row_bot.integrations.safe import fetch
    return fetch(url, hosts={"registry.modelcontextprotocol.io"}, max_bytes=4 * 1024 * 1024, timeout=30, headers=headers,
                 meta=meta, refused="registry_source_not_supported", too_large="registry_response_too_large")


def _retry_after(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying a throttled or failed page, or None to give up."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if attempt >= 4 or not (status in {429, 500, 502, 503, 504} or isinstance(exc, (ConnectionError, TimeoutError, OSError))):
        return None
    try:
        wait = float(exc.response.headers.get("retry-after", ""))  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        wait = 2.0 ** attempt
    return min(max(wait, 1.0), 60.0)


def sync(*, since: str = "", etag: str = "", cancelled: Callable[[], bool] = lambda: False,
         get: Callable[[str, dict, dict], bytes] | None = None, sleep: Callable[[float], None] | None = None,
         pause: float = 0.25, max_pages: int = 2500, deadline: float = 2700) -> dict:
    """Every latest Registry record, or those updated since a watermark (deleted ones included).

    Read-only and polite: one page at a time, a pause between pages, backoff on
    throttling. Returns ``{entries, deleted, watermark, etag, not_modified}``.
    """
    from row_bot.mcp_client.marketplace import registry_entries
    import httpx
    get, sleep = get or _get, sleep or time.sleep
    params = {"limit": "100", "version": "latest", **({"updated_since": since} if since else {})}
    entries: dict[str, MarketplaceEntry] = {}
    deleted: set[str] = set()
    cursor, seen, newest, saved_etag = "", set(), instant(since), etag
    stop = time.monotonic() + deadline
    for page in range(max_pages):
        meta: dict = {}
        for attempt in range(5):
            if cancelled() or time.monotonic() > stop:
                raise ValueError("integration_search_cancelled" if cancelled() else "registry_sync_timeout")
            try:
                headers = {"Accept": "application/json", **({"If-None-Match": etag} if etag and page == 0 else {})}
                data = json.loads(get(SOURCE + "?" + urlencode({**params, **({"cursor": cursor} if cursor else {})}), headers, meta))
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 304 and page == 0:
                    return {"entries": [], "deleted": [], "watermark": since, "etag": etag, "not_modified": True}
                wait = _retry_after(exc, attempt)
                if wait is None:
                    raise
                sleep(wait)
            except (ConnectionError, TimeoutError, httpx.TransportError) as exc:
                wait = _retry_after(exc, attempt)
                if wait is None:
                    raise
                sleep(wait)
        if page == 0:
            saved_etag = str((meta.get("headers") or {}).get("etag", ""))[:256]
        servers = data.get("servers") if isinstance(data, dict) else None
        if not isinstance(servers, list):
            raise ValueError("invalid_registry_response")
        for envelope in servers:
            server = envelope.get("server") if isinstance(envelope, dict) else None
            meta_block = envelope.get("_meta") if isinstance(envelope, dict) else None
            official = meta_block.get("io.modelcontextprotocol.registry/official") if isinstance(meta_block, dict) else None
            name = server.get("name") if isinstance(server, dict) else None
            if not isinstance(name, str) or not isinstance(official, dict):
                continue
            newest = max(newest, instant(official.get("updatedAt")))
            if official.get("status") == "deleted":
                deleted.add(name)
                entries.pop(name, None)
                continue
            for entry in registry_entries({"servers": [envelope]}):
                entries[name] = entry
                deleted.discard(name)
        cursor = (data.get("metadata") or {}).get("nextCursor") or ""
        if not cursor:
            return {"entries": list(entries.values()), "deleted": sorted(deleted), "watermark": newest,
                    "etag": saved_etag, "not_modified": False}
        if not isinstance(cursor, str) or len(cursor) > 2048 or cursor in seen:
            raise ValueError("invalid_registry_cursor")
        seen.add(cursor)
        sleep(pause)
    raise ValueError("registry_sync_too_large")


def revalidate_entry(entry: MarketplaceEntry) -> MarketplaceEntry:
    """An old listing cannot authorize a removed or materially changed recipe."""
    from row_bot.mcp_client.marketplace import _fetch_json, registry_entries
    metadata = entry.metadata or {}
    name, version = metadata.get("canonical_name"), metadata.get("version")
    if not name or not version:
        raise ValueError("registry_identity_missing")
    data = _fetch_json(SOURCE + "/" + quote(name, safe="") + "/versions/" + quote(version, safe=""))
    current = registry_entries({"servers": [data]})
    if not current or current[0].id != entry.id or (current[0].metadata or {}).get("status") != "active":
        raise ValueError("registry_entry_removed")
    if (not metadata.get("setup_digest") or metadata["setup_digest"] != (current[0].metadata or {}).get("setup_digest")
            or current[0].install != entry.install or current[0].requires_auth != entry.requires_auth):
        raise ValueError("registry_recipe_changed")
    if not current[0].install:
        raise ValueError("registry_recipe_unsupported")
    return current[0]


def revalidate_configuration(cfg: dict) -> None:
    """Recheck a registry import at the existing configuration publication gate."""
    from row_bot.mcp_client.marketplace import MarketplaceEntry
    source = cfg.get("source", {})
    if not isinstance(source, dict) or source.get("marketplace") != "official":
        return
    install = {key: cfg[key] for key in ("transport", "url", "command", "args", "headers", "env") if cfg.get(key)}
    # Remote normalized configuration includes empty argv but the recipe does not.
    entry = MarketplaceEntry(id=source.get("id", ""), name=source.get("name", ""), description="", source="official",
        install=install, requires_auth=source.get("requires_auth", False),
        metadata={"canonical_name": source.get("registry_name"), "version": source.get("registry_version"),
                  "setup_digest": source.get("registry_setup_digest")})
    revalidate_entry(entry)
