"""Designer — project persistence to ~/.row-bot/designer/projects/."""

from __future__ import annotations

import errno
import json
import logging
import os
import pathlib
import re
import shutil
import tempfile
import threading
import time
from typing import Optional
from collections.abc import Callable

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.designer.state import DesignerProject

logger = logging.getLogger(__name__)

DATA_DIR = get_row_bot_data_dir()
DESIGNER_DIR = DATA_DIR / "designer"
PROJECTS_DIR = DESIGNER_DIR / "projects"
REFERENCES_DIR = DESIGNER_DIR / "references"
ASSETS_DIR = DESIGNER_DIR / "assets"

_REPLACE_RETRIES = 5
_REPLACE_BACKOFF_SECONDS = 0.05
_REPLACE_RETRY_WINERRORS = {5, 32}
_READ_RETRIES = 5
_READ_BACKOFF_SECONDS = 0.05
_READ_RETRY_ERRNOS = {errno.EACCES, errno.EPERM}
_READ_RETRY_WINERRORS = {5, 32, 33}
_MAX_PERSISTED_STEM_LENGTH = 64
_PROJECT_SAVE_LOCKS: dict[str, threading.RLock] = {}
_PROJECT_SAVE_LOCKS_GUARD = threading.Lock()


class StaleDesignerProjectError(RuntimeError):
    """Raised when an older in-memory project would overwrite newer data."""


def _project_save_lock(project_id: str) -> threading.RLock:
    with _PROJECT_SAVE_LOCKS_GUARD:
        return _PROJECT_SAVE_LOCKS.setdefault(str(project_id), threading.RLock())


def _persisted_project_version(path: pathlib.Path) -> str:
    if not path.exists():
        return ""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return str(data.get("updated_at") or "") if isinstance(data, dict) else ""


def _ensure_dirs() -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def _project_reference_dir(project_id: str) -> pathlib.Path:
    return REFERENCES_DIR / project_id


def _project_asset_dir(project_id: str) -> pathlib.Path:
    return ASSETS_DIR / project_id


def _sanitize_reference_stem(original_name: str) -> str:
    stem = pathlib.Path(original_name).stem or "reference"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    if len(safe) > _MAX_PERSISTED_STEM_LENGTH:
        safe = safe[:_MAX_PERSISTED_STEM_LENGTH].rstrip("-._")
    return safe or "reference"


def _sanitize_asset_stem(original_name: str) -> str:
    stem = pathlib.Path(original_name).stem or "asset"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    if len(safe) > _MAX_PERSISTED_STEM_LENGTH:
        safe = safe[:_MAX_PERSISTED_STEM_LENGTH].rstrip("-._")
    return safe or "asset"


def _cleanup_temp_file(path: pathlib.Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug("Failed to clean up temp file %s", path, exc_info=True)


def _reserve_temp_path(path: pathlib.Path) -> tuple[int, pathlib.Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    return fd, pathlib.Path(tmp_name)


def _replace_with_retry(tmp: pathlib.Path, dest: pathlib.Path) -> None:
    for attempt in range(_REPLACE_RETRIES):
        try:
            tmp.replace(dest)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _REPLACE_RETRY_WINERRORS or attempt >= _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))


def _write_bytes_atomic(path: pathlib.Path, data: bytes) -> None:
    fd: int | None = None
    tmp: pathlib.Path | None = None
    try:
        fd, tmp = _reserve_temp_path(path)
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(data)
        _replace_with_retry(tmp, path)
    except Exception:
        logger.exception("Failed to write designer asset file %s", path)
        if tmp is not None:
            _cleanup_temp_file(tmp)
        raise
    finally:
        if fd is not None:
            os.close(fd)


def _write_json_atomic(path: pathlib.Path, payload: dict) -> None:
    fd: int | None = None
    tmp: pathlib.Path | None = None
    try:
        fd, tmp = _reserve_temp_path(path)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            json.dump(payload, f, ensure_ascii=False, indent=2)
        _replace_with_retry(tmp, path)
    except Exception:
        logger.exception("Failed to write designer JSON file %s", path)
        if tmp is not None:
            _cleanup_temp_file(tmp)
        raise
    finally:
        if fd is not None:
            os.close(fd)


def _read_json_with_access_retry(path: pathlib.Path) -> object:
    """Read JSON through a bounded retry for transient access denial.

    Windows scanners and indexers can briefly retain a handle immediately
    after an atomic replacement.  Retry only access and sharing violations;
    malformed JSON and every other filesystem failure keep their existing
    behavior.
    """

    for attempt in range(_READ_RETRIES):
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except PermissionError as exc:
            retryable = (
                exc.errno in _READ_RETRY_ERRNOS
                or getattr(exc, "winerror", None) in _READ_RETRY_WINERRORS
            )
            if not retryable or attempt >= _READ_RETRIES - 1:
                raise
            time.sleep(_READ_BACKOFF_SECONDS * (attempt + 1))
    raise AssertionError("unreachable")


def save_reference_bytes(project_id: str, reference_id: str, original_name: str, data: bytes) -> str:
    """Persist one project reference file and return its stored filename."""
    _ensure_dirs()
    ref_dir = _project_reference_dir(project_id)
    ref_dir.mkdir(parents=True, exist_ok=True)
    suffix = pathlib.Path(original_name).suffix.lower()[:16]
    stored_name = f"{reference_id}-{_sanitize_reference_stem(original_name)}{suffix}"
    _write_bytes_atomic(ref_dir / stored_name, data)
    return stored_name


def save_asset_bytes(project_id: str, asset_id: str, original_name: str, data: bytes, *,
                     require_absent: bool = False, validate: Callable[[], None] | None = None) -> str:
    """Persist one project asset file and return its stored filename."""
    if require_absent:
        from row_bot.thread_cleanup import resolve_managed_path
        from row_bot.developer.client_workspace import _empty_parent_guard, _directory_identity
        from row_bot.developer.edits import _rename_edit_no_replace
        import stat

        if validate is None or not isinstance(data, bytes) or not 0 < len(data) <= 32 * 1024 * 1024:
            raise ValueError('asset_admission_required')
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', asset_id)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', project_id)
                or not isinstance(original_name, str) or any(char in original_name for char in '/\\:\x00\r\n')):
            raise ValueError('asset_identity_invalid')
        asset_dir = resolve_managed_path(ASSETS_DIR, project_id)
        suffix = pathlib.Path(original_name).suffix.lower()[:16]
        stored_name = f'{asset_id}-{_sanitize_asset_stem(original_name)}{suffix}'
        destination = resolve_managed_path(asset_dir, stored_name)
        validate()
        if os.name != 'nt':
            from uuid import uuid4
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            with _empty_parent_guard(ASSETS_DIR.parent, _directory_identity(ASSETS_DIR.parent, parent=True)) as parent_fd:
                try:
                    os.mkdir(ASSETS_DIR.name, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                assets_fd = os.open(ASSETS_DIR.name, flags, dir_fd=parent_fd)
                try:
                    try:
                        os.mkdir(project_id, dir_fd=assets_fd)
                    except FileExistsError:
                        pass
                    directory_fd = os.open(project_id, flags, dir_fd=assets_fd)
                    try:
                        directory_info = os.fstat(directory_fd)
                        if not os.path.samestat(directory_info, asset_dir.lstat()):
                            raise ValueError('asset_publication_changed')
                        # Every effect is relative to the admitted directory,
                        # including publication after an external parent swap.
                        temporary_name = f'.asset-{uuid4().hex}.tmp'
                        fd = os.open(temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=directory_fd)
                        with os.fdopen(fd, 'wb') as handle:
                            handle.write(data)
                            handle.flush()
                            os.fsync(handle.fileno())
                            created = os.fstat(handle.fileno())
                        validate()
                        if not os.path.samestat(directory_info, asset_dir.lstat()):
                            raise ValueError('asset_publication_changed')
                        _rename_edit_no_replace(pathlib.Path(temporary_name), pathlib.Path(stored_name),
                                                src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                        fd = os.open(stored_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                        with os.fdopen(fd, 'rb') as handle:
                            if not os.path.samestat(created, os.fstat(handle.fileno())):
                                raise ValueError('asset_publication_changed')
                            captured = handle.read(len(data) + 1)
                            finished = os.fstat(handle.fileno())
                        named = os.stat(stored_name, dir_fd=directory_fd, follow_symlinks=False)
                        if (not os.path.samestat(created, named) or captured != data or named.st_nlink != 1
                                or not os.path.samestat(directory_info, asset_dir.lstat())
                                or (created.st_size, created.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)):
                            raise ValueError('asset_publication_changed')
                        return stored_name
                    finally:
                        os.close(directory_fd)
                finally:
                    os.close(assets_fd)
        with _empty_parent_guard(ASSETS_DIR.parent, _directory_identity(ASSETS_DIR.parent, parent=True)):
            ASSETS_DIR.mkdir(exist_ok=True)
            with _empty_parent_guard(ASSETS_DIR, _directory_identity(ASSETS_DIR, parent=True)):
                asset_dir.mkdir(exist_ok=True)
                with _empty_parent_guard(asset_dir, _directory_identity(asset_dir, parent=True)):
                    if os.path.lexists(destination):
                        raise FileExistsError('asset_already_exists')
                    fd, temporary = _reserve_temp_path(destination)
                    with os.fdopen(fd, 'wb') as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                        created = os.fstat(handle.fileno())
                    validate()
                    named = temporary.lstat()
                    if (not os.path.samestat(created, named) or not stat.S_ISREG(named.st_mode)
                            or named.st_nlink != 1 or named.st_size != len(data)):
                        raise ValueError('asset_publication_changed')
                    _rename_edit_no_replace(temporary, destination)
                    with destination.open('rb') as handle:
                        if not os.path.samestat(created, os.fstat(handle.fileno())):
                            raise ValueError('asset_publication_changed')
                        captured = handle.read(len(data) + 1)
                        finished = os.fstat(handle.fileno())
                    named = destination.lstat()
                    if (not os.path.samestat(created, named) or captured != data or named.st_nlink != 1
                            or (created.st_size, created.st_mtime_ns) != (finished.st_size, finished.st_mtime_ns)):
                        raise ValueError('asset_publication_changed')
                    return stored_name
    _ensure_dirs()
    asset_dir = _project_asset_dir(project_id)
    asset_dir.mkdir(parents=True, exist_ok=True)
    suffix = pathlib.Path(original_name).suffix.lower()[:16]
    stored_name = f"{asset_id}-{_sanitize_asset_stem(original_name)}{suffix}"
    _write_bytes_atomic(asset_dir / stored_name, data)
    return stored_name


def load_reference_bytes(project_id: str, stored_name: str) -> Optional[bytes]:
    """Load a persisted reference file by project id and stored filename."""
    from row_bot.thread_cleanup import resolve_managed_path

    project_root = resolve_managed_path(REFERENCES_DIR, project_id)
    path = resolve_managed_path(project_root, stored_name)
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except Exception:
        logger.exception("Failed to load designer reference %s for project %s", stored_name, project_id)
        return None


def load_asset_bytes(project_id: str, stored_name: str) -> Optional[bytes]:
    """Load a persisted asset file by project id and stored filename."""
    from row_bot.thread_cleanup import resolve_managed_path

    project_root = resolve_managed_path(ASSETS_DIR, project_id)
    path = resolve_managed_path(project_root, stored_name)
    if not path.exists():
        return None
    try:
        return path.read_bytes()
    except Exception:
        logger.exception("Failed to load designer asset %s for project %s", stored_name, project_id)
        return None


def delete_reference_bytes(project_id: str, stored_name: str) -> bool:
    """Delete a persisted reference file. Returns True if removed."""
    from row_bot.thread_cleanup import resolve_managed_path

    path = resolve_managed_path(REFERENCES_DIR, _project_reference_dir(project_id) / stored_name)
    if not path.exists():
        return False
    path.unlink()
    return True


def delete_asset_bytes(project_id: str, stored_name: str) -> bool:
    """Delete a persisted asset file. Returns True if removed."""
    from row_bot.thread_cleanup import resolve_managed_path

    path = resolve_managed_path(ASSETS_DIR, _project_asset_dir(project_id) / stored_name)
    if not path.exists():
        return False
    path.unlink()
    return True


def delete_project_references(project_id: str) -> bool:
    """Delete the entire persisted reference directory for a project."""
    from row_bot.thread_cleanup import resolve_managed_path

    ref_dir = resolve_managed_path(REFERENCES_DIR, str(project_id or ""))
    if not ref_dir.exists():
        return False
    shutil.rmtree(ref_dir)
    return True


def delete_project_assets(project_id: str) -> bool:
    """Delete the entire persisted asset directory for a project."""
    from row_bot.thread_cleanup import resolve_managed_path

    asset_dir = resolve_managed_path(ASSETS_DIR, str(project_id or ""))
    if not asset_dir.exists():
        return False
    shutil.rmtree(asset_dir)
    return True


def save_project(project: DesignerProject) -> None:
    """Persist a project atomically without overwriting a newer loaded copy."""

    _ensure_dirs()
    path = PROJECTS_DIR / f"{project.id}.json"
    with _project_save_lock(project.id):
        expected_version = getattr(
            project,
            "_row_bot_persisted_updated_at",
            None,
        )
        try:
            current_version = _persisted_project_version(path)
            if (
                expected_version is not None
                and current_version
                and str(expected_version) != current_version
            ):
                raise StaleDesignerProjectError(
                    f"Designer project {project.id} changed after this copy was loaded. "
                    "Reload it before saving so newer work is not overwritten."
                )
            project.touch()
            _write_json_atomic(path, project.to_dict())
            project._row_bot_persisted_updated_at = project.updated_at
        except StaleDesignerProjectError:
            raise
        except Exception:
            logger.exception("Failed to save designer project %s", project.id)
            raise


def get_project_metadata(project_id: str) -> dict[str, str] | None:
    """Read identity/version without creating folders or normalizing assets."""
    from row_bot.thread_cleanup import resolve_managed_path

    path = resolve_managed_path(PROJECTS_DIR, f"{project_id}.json")
    if not path.is_file():
        return None
    data = _read_json_with_access_retry(path)
    if not isinstance(data, dict) or str(data.get("id", "")) != project_id:
        raise ValueError("Artifact metadata does not match its registered identity")
    return {key: str(data.get(key) or "") for key in ("id", "name", "updated_at")}


def load_project(project_id: str) -> Optional[DesignerProject]:
    """Load a single project by ID. Returns None if not found."""
    _ensure_dirs()
    path = PROJECTS_DIR / f"{project_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        project = DesignerProject.from_dict(data)
        project._row_bot_persisted_updated_at = str(data.get("updated_at") or "")
        try:
            from row_bot.designer.render_assets import normalize_project_inline_assets

            if normalize_project_inline_assets(project):
                save_project(project)
        except Exception:
            logger.exception("Failed to normalize inline designer assets for project %s", project_id)
        return project
    except Exception:
        logger.exception("Failed to load designer project %s", project_id)
        return None


def list_projects() -> list[dict]:
    """Return lightweight summaries of all projects (newest first).

    Each dict has: id, name, page_count, aspect_ratio, updated_at, created_at,
    and enough first-page preview data for the gallery.
    """
    _ensure_dirs()
    summaries = []
    for p in PROJECTS_DIR.glob("*.json"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            _pages = data.get("pages", [])
            _preview_page = _pages[0] if _pages else {}
            summaries.append({
                "id": data.get("id", p.stem),
                "name": data.get("name", "Untitled"),
                "page_count": len(_pages),
                "aspect_ratio": data.get("aspect_ratio", "16:9"),
                "canvas_width": data.get("canvas_width", 1920),
                "canvas_height": data.get("canvas_height", 1080),
                "brand": data.get("brand"),
                "preview_html": _preview_page.get("html", ""),
                "preview_title": _preview_page.get("title", "Untitled"),
                "updated_at": data.get("updated_at", ""),
                "created_at": data.get("created_at", ""),
            })
        except Exception:
            logger.warning("Skipping corrupt designer project: %s", p.name)
    summaries.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return summaries


def detach_thread(project_id: str, thread_id: str) -> bool:
    """Atomically clear a project's matching conversation binding."""

    from row_bot.thread_cleanup import resolve_managed_path

    clean_project_id = str(project_id or "").strip()
    clean_thread_id = str(thread_id or "").strip()
    if not clean_project_id or not clean_thread_id:
        return False
    path = resolve_managed_path(PROJECTS_DIR, f"{clean_project_id}.json")
    if not path.exists():
        return False
    with _project_save_lock(clean_project_id):
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or str(data.get("thread_id") or "") != clean_thread_id:
            return False
        project = DesignerProject.from_dict(data)
        project._row_bot_persisted_updated_at = str(data.get("updated_at") or "")
        if project.thread_ownership == "resume":
            project.missing_origin_thread_id = clean_thread_id
        project.thread_id = None
        save_project(project)
    try:
        from row_bot.designer.session import clear_thread_session

        clear_thread_session(clean_thread_id)
    except Exception:
        logger.debug("Could not clear Designer thread session", exc_info=True)
    return True


def delete_project(project_id: str) -> bool:
    """Delete a design, all owned files, and every linked conversation."""

    from row_bot.thread_cleanup import resolve_managed_path

    clean_project_id = str(project_id or "").strip()
    path = resolve_managed_path(PROJECTS_DIR, f"{clean_project_id}.json")
    linked_thread_ids: set[str] = set()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                metadata = json.load(f) or {}
                linked = str(metadata.get("thread_id", "") or "")
                if linked and metadata.get("thread_ownership", "legacy") == "legacy":
                    linked_thread_ids.add(linked)
        except Exception:
            logger.debug("Could not read thread_id from %s", path, exc_info=True)
    try:
        from row_bot.threads import _list_project_thread_ids

        linked_thread_ids.update(_list_project_thread_ids(clean_project_id))
    except Exception:
        logger.debug("Could not enumerate linked Designer threads", exc_info=True)

    # Explicit additive relationships are not legacy project ownership. Never
    # cascade through one, even when compatibility metadata mirrors project_id.
    if linked_thread_ids:
        import sqlite3
        from contextlib import closing
        from row_bot import threads

        with closing(sqlite3.connect(threads.DB_PATH)) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(thread_meta)")}
            if "resource_bindings_json" in columns:
                additive = {str(row[0]) for row in connection.execute(
                    "SELECT thread_id FROM thread_meta WHERE "
                    "COALESCE(resource_bindings_json, '') != ''"
                )}
                linked_thread_ids.difference_update(additive)

    deleted = False
    try:
        from row_bot.designer.publish import delete_published_project

        deleted = delete_published_project(clean_project_id) or deleted
    except Exception:
        logger.exception("Failed to remove published design %s", clean_project_id)
        raise
    try:
        from row_bot.designer.history import delete_history

        history_path = pathlib.Path(DESIGNER_DIR) / "history" / clean_project_id
        history_existed = history_path.exists()
        delete_history(clean_project_id)
        deleted = history_existed or deleted
    except Exception:
        logger.exception("Failed to remove design history %s", clean_project_id)
        raise
    try:
        from row_bot.designer.session import clear_project_session

        clear_project_session(clean_project_id)
    except Exception:
        logger.debug("Failed to clear Designer session for %s", clean_project_id, exc_info=True)
    if path.exists():
        path.unlink()
        deleted = True
    if delete_project_references(clean_project_id):
        deleted = True
    if delete_project_assets(clean_project_id):
        deleted = True

    if linked_thread_ids:
        try:
            from row_bot.thread_cleanup import delete_threads

            result = delete_threads(sorted(linked_thread_ids))
            if result.failures:
                logger.warning(
                    "Designer project %s thread cleanup had %d failure(s)",
                    clean_project_id,
                    len(result.failures),
                )
        except Exception:
            logger.exception(
                "Failed to cascade thread deletion for project %s",
                clean_project_id,
            )
    return deleted


def delete_projects(project_ids: list[str]) -> tuple[int, list[tuple[str, str]]]:
    """Delete several designer projects at once.

    Wraps :func:`delete_project` so the JSON file, references dir, and
    assets dir are all cleaned up per project. Returns
    ``(deleted_count, failures)``. A project whose JSON was already
    missing (returns False) is not counted.
    """
    deleted = 0
    failures: list[tuple[str, str]] = []
    for pid in project_ids:
        try:
            if delete_project(pid):
                deleted += 1
        except Exception as exc:
            failures.append((pid, str(exc)))
    return deleted, failures


def _fork_thread_for_duplicate(
    old_thread_id: str,
    new_thread_id: str,
    new_project_id: str,
    new_project_name: str,
) -> None:
    """Copy a LangGraph thread's checkpoints/writes and metadata under a new
    ``thread_id`` so the duplicated project gets an independent conversation
    that starts from the original history but diverges afterwards.

    If any step fails the new project simply ends up without a thread link;
    the first message sent in the copy will then create a fresh thread.
    """
    if not old_thread_id or not new_thread_id:
        return
    import sqlite3
    try:
        from row_bot.threads import (
            DB_PATH,
            _save_thread_meta,
            _set_thread_project_id,
            copy_validated_summary_state,
        )
        from row_bot.threads import _thread_ui_media_path, _MEDIA_DIR
    except Exception:
        logger.debug("Thread module unavailable; skipping thread fork", exc_info=True)
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            # Copy LangGraph checkpoint rows under the new thread_id.
            # Both tables have thread_id as the first column of the
            # primary key, so a straight INSERT…SELECT with a column
            # substitution is safe and keeps every other column intact.
            for table in ("checkpoints", "writes"):
                try:
                    cols = [
                        r[1] for r in conn.execute(
                            f"PRAGMA table_info({table})"
                        ).fetchall()
                    ]
                except sqlite3.OperationalError:
                    # Table hasn't been created yet (no messages sent).
                    continue
                if not cols or "thread_id" not in cols:
                    continue
                select_exprs = ", ".join(
                    "?" if c == "thread_id" else c for c in cols
                )
                col_list = ", ".join(cols)
                try:
                    conn.execute(
                        f"INSERT OR IGNORE INTO {table} ({col_list}) "
                        f"SELECT {select_exprs} FROM {table} "
                        f"WHERE thread_id = ?",
                        (new_thread_id, old_thread_id),
                    )
                except sqlite3.OperationalError:
                    logger.debug(
                        "Checkpoint fork failed for table %s", table, exc_info=True,
                    )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception(
            "Failed to fork checkpoint tables for thread %s -> %s",
            old_thread_id, new_thread_id,
        )

    # Register the new thread in thread_meta, linked to the new project.
    try:
        _save_thread_meta(new_thread_id, new_project_name)
        _set_thread_project_id(new_thread_id, new_project_id)
        copy_validated_summary_state(old_thread_id, new_thread_id)
    except Exception:
        logger.exception(
            "Failed to register thread_meta for forked thread %s", new_thread_id,
        )

    # Copy the media sidecar + per-thread media directory so inline
    # images/docs referenced in the history still resolve for the copy.
    try:
        old_sidecar = _thread_ui_media_path(old_thread_id)
        if old_sidecar.exists():
            new_sidecar = _thread_ui_media_path(new_thread_id)
            new_sidecar.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old_sidecar, new_sidecar)
        old_media_dir = _MEDIA_DIR / old_thread_id
        if old_media_dir.exists():
            shutil.copytree(
                old_media_dir, _MEDIA_DIR / new_thread_id, dirs_exist_ok=True,
            )
    except Exception:
        logger.exception(
            "Failed to copy thread media for fork %s -> %s",
            old_thread_id, new_thread_id,
        )


def duplicate_project(project_id: str, new_name: Optional[str] = None) -> Optional[DesignerProject]:
    """Duplicate an existing project with a new ID.

    The copy gets its own filesystem id, its own reference and asset
    directories, and — crucially — its own conversation thread. Without
    this fork every duplicate would share the original's ``thread_id``
    and new messages in any copy would accumulate into every sibling's
    history.
    """
    original = load_project(project_id)
    if not original:
        return None
    import uuid
    from datetime import datetime, timezone
    new_project = DesignerProject.from_dict(original.to_dict())
    new_project.id = str(uuid.uuid4())
    new_project.name = new_name or f"{original.name} (Copy)"
    new_project.created_at = datetime.now(timezone.utc).isoformat()
    new_project.updated_at = datetime.now(timezone.utc).isoformat()

    # Fork the conversation thread so the copy has its own independent
    # history starting from the original's checkpoint.
    old_thread_id = (original.thread_id or "").strip()
    if old_thread_id:
        new_thread_id = f"designer_{new_project.id}"
        new_project.thread_id = new_thread_id
        _fork_thread_for_duplicate(
            old_thread_id, new_thread_id, new_project.id, new_project.name,
        )
    else:
        # No prior thread — leave thread_id empty so a fresh one is
        # created the first time the user sends a message.
        new_project.thread_id = None

    save_project(new_project)
    original_ref_dir = _project_reference_dir(project_id)
    if original_ref_dir.exists():
        shutil.copytree(original_ref_dir, _project_reference_dir(new_project.id), dirs_exist_ok=True)
    original_asset_dir = _project_asset_dir(project_id)
    if original_asset_dir.exists():
        shutil.copytree(original_asset_dir, _project_asset_dir(new_project.id), dirs_exist_ok=True)
    return new_project
