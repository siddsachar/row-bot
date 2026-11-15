"""Quality signals shipped beside the Registry snapshot; searches only ever read this file.

Release preparation writes it (``scripts/build_catalog_signals.py``): for each source
repository the snapshot names, its stars, last push, whether it is archived, the
repository it was forked from and its licence; for each Registry record, when its
first version was published and how many versions it has. It is xz-compressed JSON.
Signals are as fresh as the installed release: updating a catalog refreshes Registry
records only, and Row-Bot never asks GitHub for any of this itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import hashlib
import json
import logging
import lzma
from pathlib import Path

SHIPPED = Path(__file__).with_name("registry_signals.json.xz")
SCHEMA = 1
MAX_BYTES = 32 * 1024 * 1024
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Repository:
    stars: int
    pushed: int  # Seconds since the epoch; 0 when unknown.
    archived: bool
    parent: str  # The repository it was forked from (``host/owner/repo``), or "".
    licence: str

    @property
    def fork(self) -> bool:
        """A fork whose parent is listed too (the file holds only listed repositories)."""
        return bool(self.parent) and self.parent in loaded()[0]


def encode(*, captured_at: float, snapshot_digest: str, repositories: dict[str, Repository],
           records: dict[str, tuple[int, int]]) -> bytes:
    """The file's bytes: the same signals always give the same bytes."""
    body = {"schema_version": SCHEMA, "captured_at": captured_at, "snapshot_digest": snapshot_digest,
            "repositories": {name: [r.stars, r.pushed, int(r.archived), r.parent, r.licence]
                             for name, r in sorted(repositories.items())},
            "records": {name: list(found) for name, found in sorted(records.items())}}
    return lzma.compress(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"), preset=9)


def _read(path: Path) -> tuple[dict[str, Repository], dict[str, tuple[int, int]], str]:
    """Repositories, Registry records and the file's digest; nothing when the file is missing or unreadable."""
    try:
        if path.is_symlink():
            raise ValueError("invalid_registry_signals")
        raw = path.read_bytes()
        with lzma.open(path, "rb") as stream:
            text = stream.read(MAX_BYTES + 1)
        data = json.loads(text) if len(text) <= MAX_BYTES else None
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
            raise ValueError("invalid_registry_signals")
        repositories = {str(name): Repository(int(row[0]), int(row[1]), bool(row[2]), str(row[3]), str(row[4])[:64])
                        for name, row in data["repositories"].items()}
        records = {str(name): (int(row[0]), int(row[1])) for name, row in data["records"].items()}
    except FileNotFoundError:
        return {}, {}, ""
    except (OSError, ValueError, TypeError, KeyError, IndexError, lzma.LZMAError) as exc:
        log.warning("Registry signals are unreadable; search ranks without them: %s", type(exc).__name__)
        return {}, {}, ""
    return repositories, records, hashlib.sha256(raw).hexdigest()[:16]


@cache
def _cached(path: str, stamp: int) -> tuple[dict[str, Repository], dict[str, tuple[int, int]], str]:
    return _read(Path(path))


def loaded() -> tuple[dict[str, Repository], dict[str, tuple[int, int]], str]:
    """The shipped signals (read once per file version)."""
    try:
        stamp = SHIPPED.stat().st_mtime_ns
    except OSError:
        stamp = 0
    return _cached(str(SHIPPED), stamp)


def repository(name: str) -> Repository | None:
    """Signals for a normalised repository (``host/owner/repo``), if it was listed at release."""
    return loaded()[0].get(name) if name else None


def record(name: str) -> tuple[int, int]:
    """A Registry record's first publish time (seconds) and version count, or (0, 0)."""
    return loaded()[1].get(name, (0, 0))


def digest() -> str:
    """Changes with the shipped signals, so the index ranks with the current ones."""
    return loaded()[2]
