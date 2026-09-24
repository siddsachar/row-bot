"""Plugin installer — download, validate, install, update, uninstall.

Downloads plugin archives from the monorepo, validates before install,
checks dependency conflicts against core, and manages the local
``~/.row-bot/installed_plugins/`` directory.
"""

from __future__ import annotations

import json
import copy
import functools
import hashlib
import re
import stat
import threading
import uuid
import logging
import os
import pathlib
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

DATA_DIR = get_row_bot_data_dir()
PLUGINS_DIR = DATA_DIR / "installed_plugins"

# Base URL for downloading plugins from the monorepo
# Each plugin directory is downloaded as: {BASE_URL}/plugins/{plugin_id}/
DEFAULT_REPO_URL = os.environ.get(
    "ROW_BOT_PLUGIN_REPO_URL",
    "https://github.com/siddsachar/row-bot-plugins",
)


@dataclass
class InstallResult:
    """Result of an install/update/uninstall operation."""
    success: bool
    plugin_id: str
    message: str
    version: str = ""


_environment_lock = threading.RLock()


def _environment_serialized(function):
    @functools.wraps(function)
    def guarded(*args, **kwargs):
        with _environment_lock:
            return function(*args, **kwargs)
    return guarded


@dataclass(frozen=True)
class EnvironmentPreparation:
    """Private preparation outcome; paths never become public status fields."""

    ready: bool
    plugin_id: str
    plugin_revision: str
    operation_id: str
    environment_revision: str = ""
    changed: bool = False
    error_code: str | None = None


def _preparation_id(plugin_id: str) -> None:
    if type(plugin_id) is not str or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", plugin_id):
        raise ValueError("invalid_plugin_id")


def _source_for_preparation(plugin_id: str) -> pathlib.Path:
    from row_bot.plugins.devtools import iter_linked_plugin_dirs
    from row_bot.plugins.sandbox import _checked_path

    _preparation_id(plugin_id)
    # Explicit linked roots remain supported through their existing owner.
    linked = iter_linked_plugin_dirs()
    if plugin_id in linked:
        source = linked[plugin_id]
    else:
        source = PLUGINS_DIR / plugin_id
        _checked_path(pathlib.Path(source.absolute().anchor), source.absolute())
    source = source.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("plugin_unavailable")
    return source


def _tree_revision(root: pathlib.Path, *, source: bool) -> str:
    """Bounded nonexecuting content/identity cut; reject links and changed reads."""
    from row_bot.plugins.sandbox import _checked_path, _no_link

    digest = hashlib.sha256()
    before_root = _no_link(root)
    digest.update(str(root).encode("utf-8"))
    digest.update(f"\0{before_root.st_dev}:{before_root.st_ino}\0".encode())
    byte_limit = (64 if source else 512) * 1024 * 1024
    file_limit = 8192 if source else 65536
    consumed = 0
    entries = 0
    pending = [root]
    directories = []
    while pending:
        directory = pending.pop()
        directory_stat = _checked_path(root, directory)
        directories.append((directory, directory_stat))
        children = []
        for child in directory.iterdir():
            entries += 1
            if entries > file_limit:
                raise ValueError("environment_capacity_exceeded")
            children.append(child)
        for path in sorted(children):
            value = _no_link(path)
            if path.name == "__pycache__" or (source and path.name == ".git"):
                continue
            if stat.S_ISDIR(value.st_mode):
                pending.append(path)
            elif stat.S_ISREG(value.st_mode):
                if path.suffix == ".pyc":
                    continue
                consumed += value.st_size
                if consumed > byte_limit:
                    raise ValueError("environment_capacity_exceeded")
                digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
                with path.open("rb") as stream:
                    opened = os.fstat(stream.fileno())
                    if (opened.st_dev, opened.st_ino) != (value.st_dev, value.st_ino):
                        raise ValueError("environment_changed")
                    read = 0
                    while block := stream.read(1024 * 1024):
                        read += len(block)
                        if read > value.st_size:
                            raise ValueError("environment_changed")
                        digest.update(block)
                    after = os.fstat(stream.fileno())
                current = _checked_path(root, path)
                if read != value.st_size or (after.st_size, after.st_mtime_ns) != (value.st_size, value.st_mtime_ns) or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns):
                    raise ValueError("environment_changed")
                digest.update(b"\0")
            else:
                raise ValueError("environment_path_invalid")
    for directory, before in directories:
        after = _checked_path(root, directory)
        if (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns):
            raise ValueError("environment_changed")
    after_root = _no_link(root)
    if (before_root.st_dev, before_root.st_ino, before_root.st_mtime_ns) != (after_root.st_dev, after_root.st_ino, after_root.st_mtime_ns):
        raise ValueError("environment_changed")
    return "sha256:" + digest.hexdigest()


def get_plugin_source_revision(plugin_id: str) -> str:
    """Read the installed/explicitly linked source cut without importing it."""
    from row_bot.plugins.manifest import parse_manifest

    source = _source_for_preparation(plugin_id)
    revision = _tree_revision(source, source=True)
    if parse_manifest(source).id != plugin_id:
        raise ValueError("plugin_identity_changed")
    if _source_for_preparation(plugin_id) != source or _tree_revision(source, source=True) != revision:
        raise ValueError("plugin_changed")
    return revision


def _generation_path(plugin_id: str, operation_id: str, *, create: bool = False) -> pathlib.Path:
    from row_bot.plugins.sandbox import _checked_path, _no_link

    _preparation_id(plugin_id)
    if type(operation_id) is not str or str(uuid.UUID(operation_id)) != operation_id:
        raise ValueError("invalid_operation_id")
    owner = DATA_DIR.absolute()
    if owner.resolve() != get_row_bot_data_dir(create=False).resolve():
        raise ValueError("environment_owner_changed")
    _checked_path(pathlib.Path(owner.anchor), owner)
    root = owner
    for name in ("plugin_environments", plugin_id):
        root /= name
        if create:
            root.mkdir(exist_ok=True)
        _no_link(root)
    generation = root / operation_id
    if create:
        # Never adopt an unrelated preexisting candidate, even after a crash.
        generation.mkdir()
    _checked_path(owner, generation)
    return generation / "environment"


def _check_preparation_cancelled() -> None:
    from row_bot.cancellation import current_cancellation_scope

    scope = current_cancellation_scope()
    if scope is not None and scope.is_cancelled():
        raise ValueError("cancelled")


def _safe_preparation_code(error: Exception) -> str:
    permitted = {
        "cancelled", "invalid_plugin_id", "invalid_operation_id", "invalid_requirements",
        "plugin_changed", "plugin_identity_changed", "plugin_unavailable",
        "environment_changed", "environment_owner_changed", "environment_path_invalid",
        "environment_capacity_exceeded", "environment_already_exists", "host_environment_blocked",
        "environment_process_timeout", "environment_process_failed", "environment_verification_failed",
        "dependency_plan_unverified", "dependency_plan_incomplete", "dependency_core_conflict",
        "dependency_source_unavailable", "direct_reference_unavailable", "host_constraints_unavailable",
        "environment_state_unavailable", "environment_state_changed",
    }
    return str(error) if str(error) in permitted else "environment_preparation_failed"


@_environment_serialized
def prepare_plugin_environment(
    plugin_id: str,
    requirements: list[str],
    *,
    expected_plugin_revision: str,
    operation_id: str,
) -> EnvironmentPreparation:
    """Explicitly prepare an isolated immutable generation, without enabling it.

    Installation is the sole effectful entry point. Reads/load never call it.
    Repeating a completed operation returns its recorded outcome. An interrupted
    unverified operation is retained, not automatically replayed; a fresh explicit
    operation is required. Verified-but-unpublished candidates can finish their
    metadata publication without repeating installation.
    """
    from row_bot.plugins import sandbox, state as plugin_state

    records: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    try:
        _preparation_id(plugin_id)
        if type(operation_id) is not str or str(uuid.UUID(operation_id)) != operation_id:
            raise ValueError("invalid_operation_id")
        if type(expected_plugin_revision) is not str or not re.fullmatch(r"sha256:[a-f0-9]{64}", expected_plugin_revision):
            raise ValueError("plugin_changed")
        values = sandbox._requirement_values(requirements)
        revision = get_plugin_source_revision(plugin_id)
        if revision != expected_plugin_revision:
            raise ValueError("plugin_changed")
        request_revision = hashlib.sha256(json.dumps([revision, values], separators=(",", ":")).encode()).hexdigest()
        records = plugin_state.get_plugin_environment_state(plugin_id)
        operations = records.get("operations", {})
        if type(operations) is not dict or len(operations) > 128:
            raise ValueError("environment_state_unavailable")
        existing = operations.get(operation_id)
        if existing is not None:
            if type(existing) is not dict or existing.get("request_revision") != request_revision:
                return EnvironmentPreparation(False, plugin_id, revision, operation_id, error_code="operation_conflict")
            stage = existing.get("stage")
            if stage == "failed":
                return EnvironmentPreparation(False, plugin_id, revision, operation_id, error_code=_safe_preparation_code(ValueError(existing.get("error_code"))))
            if stage not in {"verified", "ready"}:
                return EnvironmentPreparation(False, plugin_id, revision, operation_id, error_code="operation_incomplete")
            environment = _generation_path(plugin_id, operation_id)
            sandbox._target(environment)
            environment_revision = _tree_revision(environment, source=False)
            if environment_revision != existing.get("environment_revision"):
                raise ValueError("environment_changed")
            if stage == "ready":
                return EnvironmentPreparation(True, plugin_id, revision, operation_id, environment_revision)
            receipt = dict(existing)
        else:
            if len(operations) >= 128:
                raise ValueError("environment_capacity_exceeded")
            _check_preparation_cancelled()
            receipt = {"stage": "preparing", "plugin_revision": revision, "request_revision": request_revision}
            updated = copy.deepcopy(records)
            updated.setdefault("operations", {})[operation_id] = receipt
            plugin_state.set_plugin_environment_state(plugin_id, updated, expected=records)
            records = updated
            environment = _generation_path(plugin_id, operation_id, create=True)
            sandbox.create_environment(environment)
            _check_preparation_cancelled()
            result = _install_plugin_deps(list(values), environment=environment)
            if not result.success:
                raise ValueError(result.message)
            _check_preparation_cancelled()
            environment_revision = _tree_revision(environment, source=False)
            if get_plugin_source_revision(plugin_id) != revision:
                raise ValueError("plugin_changed")
            receipt = dict(receipt, stage="verified", environment_revision=environment_revision)
            updated = copy.deepcopy(records)
            updated["operations"][operation_id] = receipt
            plugin_state.set_plugin_environment_state(plugin_id, updated, expected=records)
            records = updated
        _check_preparation_cancelled()
        if get_plugin_source_revision(plugin_id) != revision:
            raise ValueError("plugin_changed")
        updated = copy.deepcopy(records)
        updated["operations"][operation_id] = dict(receipt, stage="ready")
        updated["active_operation_id"] = operation_id
        plugin_state.set_plugin_environment_state(plugin_id, updated, expected=records)
        return EnvironmentPreparation(True, plugin_id, revision, operation_id, environment_revision, True)
    except Exception as exc:
        code = _safe_preparation_code(exc)
        # A verified receipt is recoverable after publication failure. Never
        # downgrade it or clear the previous active generation on an exception.
        if records is not None and receipt is not None and receipt.get("stage") == "preparing":
            try:
                updated = copy.deepcopy(records)
                updated.setdefault("operations", {})[operation_id] = dict(receipt, stage="failed", error_code=code)
                plugin_state.set_plugin_environment_state(plugin_id, updated, expected=records)
            except Exception:
                logger.warning("Plugin environment failure receipt could not be persisted")
        return EnvironmentPreparation(False, plugin_id, expected_plugin_revision, operation_id, error_code=code)


# ── Public API ───────────────────────────────────────────────────────────────
@_environment_serialized
def install_plugin(
    plugin_id: str,
    *,
    source_dir: pathlib.Path | None = None,
    source: str | None = None,
    source_ref: str = "",
    archive_url: str = "",
    expected_checksum: str | None = None,
) -> InstallResult:
    """Install a plugin.

    If *source_dir* is provided, copies from that directory (local install).
    Otherwise, downloads from the marketplace repo.
    """
    dest = PLUGINS_DIR / plugin_id
    if dest.exists():
        return InstallResult(
            success=False, plugin_id=plugin_id,
            message=f"Plugin '{plugin_id}' is already installed. Use update instead.",
        )

    try:
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

        if source_dir:
            # Local install (copy directory)
            if not source_dir.is_dir():
                return InstallResult(
                    success=False, plugin_id=plugin_id,
                    message=f"Source directory not found: {source_dir}",
                )
            shutil.copytree(source_dir, dest)
        elif archive_url:
            _download_plugin_archive(plugin_id, dest, archive_url)
        else:
            # Download from repo
            _download_plugin(plugin_id, dest)

        checksum_error = _verify_checksum(dest, expected_checksum)
        if checksum_error:
            shutil.rmtree(dest, ignore_errors=True)
            return InstallResult(
                success=False, plugin_id=plugin_id,
                message=checksum_error,
            )

        # Validate manifest exists and conforms to the v2 contract.
        try:
            from row_bot.plugins.manifest import parse_manifest

            manifest = parse_manifest(dest)
        except Exception as exc:
            shutil.rmtree(dest, ignore_errors=True)
            return InstallResult(
                success=False, plugin_id=plugin_id,
                message=f"Installed plugin manifest is invalid: {exc}",
            )
        if manifest.id != plugin_id:
            shutil.rmtree(dest, ignore_errors=True)
            return InstallResult(
                success=False, plugin_id=plugin_id,
                message=(
                    f"Manifest id '{manifest.id}' does not match requested "
                    f"plugin id '{plugin_id}'"
                ),
            )
        version = manifest.version

        # Security scan
        from row_bot.plugins.loader import _security_scan
        sec_err = _security_scan(dest)
        if sec_err:
            shutil.rmtree(dest, ignore_errors=True)
            return InstallResult(
                success=False, plugin_id=plugin_id,
                message=f"Security check failed: {sec_err}",
            )

        from row_bot.plugins import state as plugin_state

        install_source = source or ("local" if source_dir else "marketplace")
        install_ref = source_ref or (str(source_dir) if source_dir else archive_url or DEFAULT_REPO_URL)
        plugin_state.mark_plugin_installed(
            plugin_id,
            version=version,
            source=install_source,
            source_ref=install_ref,
        )

        logger.info("Plugin '%s' v%s installed to %s", plugin_id, version, dest)
        return InstallResult(
            success=True, plugin_id=plugin_id, version=version,
            message=(
                f"Installed '{plugin_id}' v{version} and kept it off. "
                "Configure, test, then enable it in Plugin Center."
            ),
        )

    except Exception as exc:
        # Cleanup on failure
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        logger.error("Install failed for '%s': %s", plugin_id, exc, exc_info=True)
        return InstallResult(
            success=False, plugin_id=plugin_id,
            message=f"Install failed: {exc}",
        )


@_environment_serialized
def update_plugin(
    plugin_id: str,
    *,
    source_dir: pathlib.Path | None = None,
    source: str | None = None,
    source_ref: str = "",
    archive_url: str = "",
    expected_checksum: str | None = None,
) -> InstallResult:
    """Update an installed plugin.

    Backs up the current version, installs the new one, and rolls back
    on failure.
    """
    dest = PLUGINS_DIR / plugin_id
    if not dest.exists():
        return InstallResult(
            success=False, plugin_id=plugin_id,
            message=f"Plugin '{plugin_id}' is not installed",
        )

    # Never remove a pre-existing backup from another interrupted update.
    # A unique sibling lets an interrupted operation be inspected and restored.
    from uuid import uuid4

    backup = dest.with_name(f"{plugin_id}.bak-{uuid4().hex}")
    try:
        # Backup current version
        shutil.move(str(dest), str(backup))

        # Install new version
        result = install_plugin(
            plugin_id,
            source_dir=source_dir,
            source=source,
            source_ref=source_ref,
            archive_url=archive_url,
            expected_checksum=expected_checksum,
        )

        if result.success:
            # Remove backup
            shutil.rmtree(backup, ignore_errors=True)
            return result
        else:
            # Rollback
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(backup), str(dest))
            return InstallResult(
                success=False, plugin_id=plugin_id,
                message=f"Update failed, rolled back: {result.message}",
            )

    except Exception as exc:
        # Attempt rollback
        if backup.exists():
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.move(str(backup), str(dest))
        logger.error("Update failed for '%s': %s", plugin_id, exc, exc_info=True)
        return InstallResult(
            success=False, plugin_id=plugin_id,
            message=f"Update failed: {exc}",
        )


@_environment_serialized
def uninstall_plugin(plugin_id: str) -> InstallResult:
    """Uninstall a plugin — remove files and clean state."""
    dest = PLUGINS_DIR / plugin_id
    if not dest.exists():
        return InstallResult(
            success=False, plugin_id=plugin_id,
            message=f"Plugin '{plugin_id}' is not installed",
        )

    try:
        # Unregister from runtime
        from row_bot.plugins import registry as reg
        reg.unregister_plugin(plugin_id)

        # Remove state and secrets
        from row_bot.plugins import state
        state.remove_plugin_state(plugin_id)

        # Remove files
        shutil.rmtree(dest)

        logger.info("Plugin '%s' uninstalled", plugin_id)
        return InstallResult(
            success=True, plugin_id=plugin_id,
            message=f"Plugin '{plugin_id}' uninstalled successfully",
        )

    except Exception as exc:
        logger.error("Uninstall error for '%s': %s", plugin_id, exc, exc_info=True)
        return InstallResult(
            success=False, plugin_id=plugin_id,
            message=f"Uninstall error: {exc}",
        )


def is_installed(plugin_id: str) -> bool:
    """Check if a plugin is installed."""
    return (PLUGINS_DIR / plugin_id).is_dir()


def get_installed_version(plugin_id: str) -> str | None:
    """Get the installed version of a plugin, or None."""
    manifest_path = PLUGINS_DIR / plugin_id / "plugin.json"
    if not manifest_path.exists():
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("version")
    except Exception:
        return None


# ── Download ─────────────────────────────────────────────────────────────────
def _verify_checksum(plugin_dir: pathlib.Path, expected_checksum: str | None) -> str | None:
    expected = (expected_checksum or "").strip()
    if not expected:
        return None
    if not expected.lower().startswith("sha256:"):
        return f"Unsupported plugin checksum format: {expected}"
    from row_bot.plugins.devtools import compute_plugin_checksum

    actual = compute_plugin_checksum(plugin_dir)
    if actual.lower() != expected.lower():
        return f"Checksum mismatch: expected {expected}, got {actual}"
    return None


def _download_plugin(plugin_id: str, dest: pathlib.Path) -> None:
    """Download a plugin from the monorepo.

    Downloads the plugin directory as a zip from GitHub's archive API
    and extracts just the plugin's subdirectory.
    """
    # GitHub archive URL: downloads entire repo as zip
    archive_url = f"{DEFAULT_REPO_URL}/archive/refs/heads/main.zip"
    _download_plugin_archive(plugin_id, dest, archive_url)


def _download_plugin_archive(plugin_id: str, dest: pathlib.Path, archive_url: str) -> None:
    """Download or read a zip archive and extract one plugin directory."""

    logger.info("Downloading plugin '%s' from %s", plugin_id, archive_url)

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = pathlib.Path(tmp) / "repo.zip"
        _download_to_file(archive_url, zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            extract_dir = pathlib.Path(tmp) / "extracted"
            _safe_extract_zip(zf, extract_dir)

        extracted_plugin = _find_extracted_plugin_dir(extract_dir, plugin_id)
        shutil.copytree(extracted_plugin, dest)

    logger.info("Downloaded plugin '%s' to %s", plugin_id, dest)


def _download_to_file(ref: str, dest: pathlib.Path) -> None:
    local_path = _local_path_from_ref(ref)
    if local_path is not None:
        if local_path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Plugin archive exceeds the download limit")
        shutil.copyfile(local_path, dest)
        return

    import urllib.request

    req = urllib.request.Request(
        ref, headers={"User-Agent": "Row-Bot-Plugin-Installer"}
    )
    maximum = 64 * 1024 * 1024
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(dest, "wb") as f:
            total = 0
            while chunk := resp.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise ValueError("Plugin archive exceeds the download limit")
                f.write(chunk)


def _safe_extract_zip(zf: zipfile.ZipFile, dest: pathlib.Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    members = zf.infolist()
    if len(members) > 2048 or sum(member.file_size for member in members) > 128 * 1024 * 1024:
        raise ValueError("Plugin archive exceeds the extraction limit")
    for member in members:
        target = (dest / member.filename).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError:
            raise ValueError(f"Unsafe zip member path: {member.filename}")
        if member.create_system == 3 and (member.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError(f"Plugin archive contains a symbolic link: {member.filename}")
        zf.extract(member, dest)


def _find_extracted_plugin_dir(extract_dir: pathlib.Path, plugin_id: str) -> pathlib.Path:
    candidates = sorted({path.parent for path in extract_dir.rglob("plugin.json")})
    matching: list[pathlib.Path] = []
    for candidate in candidates:
        try:
            raw = json.loads((candidate / "plugin.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(raw, dict) and str(raw.get("id", "")) == plugin_id:
            matching.append(candidate)
    if len(matching) == 1:
        return matching[0]
    if len(matching) > 1:
        raise ValueError(f"Archive contains multiple plugin.json files for '{plugin_id}'")
    if len(candidates) == 1:
        return candidates[0]
    raise FileNotFoundError(f"Plugin '{plugin_id}' not found in archive")


def _local_path_from_ref(ref: str) -> pathlib.Path | None:
    if not ref:
        return None
    candidate = pathlib.Path(ref).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    parsed = urlparse(ref)
    if parsed.scheme != "file":
        return None
    raw_path = url2pathname(parsed.path)
    if parsed.netloc:
        raw_path = f"//{parsed.netloc}{raw_path}"
    path = pathlib.Path(raw_path).expanduser()
    return path.resolve() if path.is_file() else None


# ── Dependency Installation ──────────────────────────────────────────────────
def _install_plugin_deps(deps: list[str], *, environment: pathlib.Path | None = None) -> InstallResult:
    """The single installer seam; explicit isolated target required."""
    from row_bot.plugins.sandbox import install_dependencies

    success, message = install_dependencies(deps, environment=environment)
    return InstallResult(success=success, plugin_id="", message=message)
