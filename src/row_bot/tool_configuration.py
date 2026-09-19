"""Canonical tools_config.json ownership without tool construction on reads.

The existing registry owns live tools. This module owns only their existing
saved file, its strict snapshots and metadata-preserving publication path.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import copy
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from uuid import uuid4

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.file_ownership import directory_identity, guard_directory

BYTE_LIMIT = 8 * 1024 * 1024
LOCK = threading.RLock()


class ToolConfigurationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SavedToolConfiguration:
    document: dict
    digest: str
    exists: bool
    identity: str = ""


def configuration_path() -> Path:
    return get_row_bot_data_dir(create=False) / "tools_config.json"


@contextmanager
def transaction() -> Iterator[None]:
    with LOCK:
        yield


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(_value):
    raise ValueError


def read_saved(path: Path | None = None) -> SavedToolConfiguration:
    """Read exact bounded bytes through the existing native directory guard."""
    path = (path or configuration_path()).absolute()
    try:
        if not path.parent.exists():
            return SavedToolConfiguration({}, "missing", False)
        with guard_directory(path.parent, directory_identity(path.parent, parent=True)) as directory:
            leaf = path.name if directory is not None else path
            try:
                before = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                return SavedToolConfiguration({}, "missing", False)
            if not stat.S_ISREG(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400 or before.st_size > BYTE_LIMIT:
                raise ValueError
            descriptor = os.open(leaf, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=directory)
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(descriptor)
                if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ValueError
                data = stream.read(BYTE_LIMIT + 1)
                finished = os.fstat(descriptor)
                current = os.stat(leaf, dir_fd=directory, follow_symlinks=False)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                        info.st_ctime_ns if os.name != "nt" else None)
            if len(data) > BYTE_LIMIT or identity(opened) != identity(finished) or identity(finished) != identity(current):
                raise ValueError
        document = json.loads(data.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
        if type(document) is not dict or any(key in document and type(document[key]) is not dict for key in ("tools", "tool_configs", "global")):
            raise ValueError
        return SavedToolConfiguration(document, hashlib.sha256(data).hexdigest(), True, f"{opened.st_dev}:{opened.st_ino}")
    except (ValueError, OSError, UnicodeError, RecursionError):
        raise ToolConfigurationError("tool_configuration_unavailable") from None


def tools_map(document: dict) -> dict:
    return document["tools"] if "tools" in document else document


def editable_document(saved: SavedToolConfiguration) -> dict:
    return copy.deepcopy(saved.document) if saved.exists else {"tools": {}, "tool_configs": {}}


def recovery_required(*, excluding: tuple[str, str] | None = None) -> bool:
    from row_bot.runtime import admissions
    try:
        pending = admissions.read_unfinished_target_commands("settings:tools")
    except admissions.AdmissionError:
        raise ToolConfigurationError("tool_configuration_unavailable") from None
    return pending["overflow"] or any((row["owner_id"], row["key"]) != excluding for row in pending["items"])


def require_write_available(*, excluding: tuple[str, str] | None = None) -> None:
    if recovery_required(excluding=excluding):
        raise ToolConfigurationError("tool_configuration_recovery_required")


def publish_saved(document: dict, *, expected_digest: str, command_id: str,
                  persist_recovery: Callable, validate: Callable[[], None] = lambda: None,
                  path: Path | None = None) -> SavedToolConfiguration:
    """The sole write path, reusing the canonical retained file publication."""
    from row_bot.developer.edits import publish_text_revision
    path = (path or configuration_path()).absolute()
    content = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if len(content.encode("utf-8")) > BYTE_LIMIT:
        raise ToolConfigurationError("tool_configuration_too_large")
    with LOCK:
        validate()
        if read_saved(path).digest != expected_digest:
            raise ToolConfigurationError("revision_conflict")
        path.parent.mkdir(parents=True, exist_ok=True)
        publish_text_revision(path.parent, path.name, content, expected_digest=expected_digest,
            command_id=command_id, persist_recovery=persist_recovery, validate=validate, max_bytes=BYTE_LIMIT)
        return read_saved(path)


def legacy_publish(path: Path, document: dict, *, expected_digest: str | None = None) -> SavedToolConfiguration:
    """Retain interrupted legacy writes in the existing admissions database."""
    from row_bot.runtime import admissions
    with LOCK:
        require_write_available()
        saved = read_saved(path)
        if expected_digest is not None and saved.digest != expected_digest:
            raise ToolConfigurationError("revision_conflict")
        command_id, owner_id = str(uuid4()), "tools:legacy-settings"
        command = {"command_id": command_id, "type": "tool.configuration.legacy_save",
            "expected_revision": saved.digest, "intent_digest": admissions.keyed_digest(document)}
        admissions.claim_command(owner_id, command_id, command, "settings:tools")
        proposed = copy.deepcopy(document)
        proposed["_client_publication"] = {"owner_id": owner_id, "key": command_id, "command_id": command_id}
        progress = {"command_id": command_id, "status": "admitting"}
        def checkpoint(proof):
            progress["_native_tool_configuration"] = {"publication": asdict(proof)}
            admissions.command_progress(owner_id, command_id, progress)
        try:
            result = publish_saved(proposed, expected_digest=saved.digest, command_id=command_id,
                                   persist_recovery=checkpoint, path=path)
        except Exception:
            if "_native_tool_configuration" not in progress:
                admissions.reject_command(owner_id, command_id, "tool_configuration_save_failed")
            raise
        admissions.complete_command(owner_id, command_id, {"command_id": command_id, "status": "completed"})
        return result


def confirmed_publication(saved: SavedToolConfiguration, publication: dict, *,
                          owner_id: str, key: str, command_id: str) -> bool:
    """Confirm an original retained publication, never adopt equal replacement bytes."""
    from row_bot.file_ownership import confirmed_edit_publication
    return saved.exists and confirmed_edit_publication(configuration_path(), saved.digest, saved.identity,
        publication, owner_id=owner_id, key=key, command_id=command_id, max_bytes=BYTE_LIMIT)
