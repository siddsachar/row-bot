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
    """Result of an install/update/uninstall operation.

    ``code`` names why an install or update failed, for logs without paths.
    """
    success: bool
    plugin_id: str
    message: str
    version: str = ""
    code: str = ""


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
def _owned_package(plugin_id: str) -> pathlib.Path:
    from row_bot.package_files import contained_path
    _preparation_id(plugin_id)
    return contained_path(PLUGINS_DIR, plugin_id)


def _prepare_package(plugin_id: str, staged: pathlib.Path, *, source_dir, source_ref,
                     archive_url, archive_path, expected_checksum):
    from row_bot.package_files import check_package_tree
    from row_bot.plugins.manifest import parse_manifest
    if source_dir:
        check_package_tree(source_dir)
        shutil.copytree(source_dir, staged, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    elif archive_url:
        _download_plugin_archive(plugin_id, staged, archive_url, archive_path)
    else:
        _download_plugin(plugin_id, staged)
    check_package_tree(staged)
    error = _verify_checksum(staged, expected_checksum)
    if error:
        raise ValueError("plugin_checksum_mismatch")
    manifest = parse_manifest(staged, source_identity=source_ref)
    if manifest.id != plugin_id:
        raise ValueError("plugin_manifest_invalid")
    if manifest.package_format == "row-bot-v2":
        from row_bot.plugins.loader import _security_scan
        if _security_scan(staged):
            raise ValueError("plugin_security_check_failed")
    return manifest


@_environment_serialized
def install_plugin(
    plugin_id: str, *, source_dir: pathlib.Path | None = None,
    source: str | None = None, source_ref: str = "", archive_url: str = "",
    archive_path: str = "", expected_checksum: str | None = None,
    operation_id: str = "", source_pin: str = "", _updating: bool = False,
    validate=lambda: None,
) -> InstallResult:
    """Prepare exact bytes outside the inventory, then publish under its owner.

    Native remote checksums remain mandatory. A portable preview supplies the
    reviewed tree digest; it is consistency evidence, not publisher authenticity.
    """
    from row_bot.plugins import state
    from row_bot.plugins.devtools import compute_plugin_checksum
    from row_bot.package_files import contained_path
    operation_id = operation_id or str(uuid.uuid4())
    try:
        dest = _owned_package(plugin_id)
        previous_state = state.get_plugin_package_state(plugin_id)
        if previous_state.get("pending"):
            return InstallResult(False, plugin_id, "An interrupted publication needs recovery before another install.", code="plugin_publication_pending")
        if dest.exists() != _updating:
            return InstallResult(False, plugin_id, "Plugin is already installed; use Update." if dest.exists() else "Plugin is not installed; use Add.", code="plugin_already_installed" if dest.exists() else "plugin_not_installed")
        if not source_dir and not (expected_checksum or "").strip():
            return InstallResult(False, plugin_id, "The marketplace lists no checksum; nothing was downloaded.", code="plugin_checksum_unavailable")
        install_ref = source_ref or (str(source_dir.resolve()) if source_dir else archive_url or DEFAULT_REPO_URL)
        retained = state.retained_source(plugin_id)
        if not _updating and retained not in (None, install_ref) and not install_ref.startswith(retained + "#"):
            # Another package left settings and keys under this id: they are never handed to a different one.
            return InstallResult(False, plugin_id, "Saved data from a different package with this name is still kept. "
                                 "Delete it from that package's page first.", code="plugin_data_retained")
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        staging_root = contained_path(DATA_DIR, "plugin_staging")
        staging_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="publish-", dir=staging_root) as tmp:
            staged = pathlib.Path(tmp) / "package"
            manifest = _prepare_package(plugin_id, staged, source_dir=source_dir, source_ref=install_ref,
                archive_url=archive_url, archive_path=archive_path, expected_checksum=expected_checksum)
            digest = compute_plugin_checksum(staged)
            validate()
            if state.get_plugin_package_state(plugin_id) != previous_state:
                raise ValueError("plugin_changed")
            if _updating and previous_state.get("digest") and compute_plugin_checksum(dest) != previous_state["digest"]:
                raise ValueError("plugin_changed")
            previous = contained_path(DATA_DIR, "plugin_revisions/" + plugin_id + "/previous")
            old_package = {k: v for k, v in previous_state.items() if k not in {"previous", "pending"}}
            candidate = {"format": manifest.package_format, "source_identity": install_ref,
                "source": source or ("local" if source_dir else "marketplace"),
                "version": manifest.version, "digest": digest, "adapter_version": 1, "pin": source_pin,
                "operation_id": operation_id, "upstream_name": manifest.name,
                "children": {"skills": [v["name"] for v in manifest.provides.skills],
                             "mcp": [v["id"] for v in manifest.provides.mcp_servers]},
                "previous": old_package if _updating else {}, "removed": False}
            before = staged.stat()
            pending = {"operation_id": operation_id, "candidate": candidate,
                       "file_identity": [before.st_dev, before.st_ino]}
            # A pending marker is outside upstream bytes and precedes publication.
            state.set_plugin_package_state(plugin_id, {**previous_state, "pending": pending})
            if _updating:
                previous.parent.mkdir(parents=True, exist_ok=True)
                if previous.exists():
                    shutil.rmtree(previous)
                dest.rename(previous)
            try:
                staged.rename(dest)
            except BaseException:
                if _updating and previous.exists() and not dest.exists():
                    previous.rename(dest)
                state.set_plugin_package_state(plugin_id, previous_state)
                raise
            state.publish_plugin_package(plugin_id, candidate, updating=_updating)
        return InstallResult(True, plugin_id, "Updated plugin; configuration and access choices were preserved." if _updating else "Added plugin and kept it off. Configure, test, then enable it in Integrations.", version=manifest.version)
    except Exception as exc:
        # Do not delete a published tree or its last good predecessor on a state
        # failure. The pending inode/digest cut makes recovery explicit.
        logger.warning("Plugin publication failed for %s (%s)", plugin_id, type(exc).__name__)
        code = str(exc) if str(exc) in {"plugin_checksum_mismatch", "plugin_manifest_invalid", "plugin_security_check_failed"} else "plugin_install_failed"
        from row_bot.plugins.manifest import ManifestError
        message = ("plugin.json is missing or invalid." if isinstance(exc, ManifestError) else
                   "Checksum mismatch; the reviewed files were not published." if code == "plugin_checksum_mismatch" else
                   "The archive has no folder at the reviewed package path." if isinstance(exc, FileNotFoundError) else
                   "Publication did not complete. Inspect the installed revision and use recovery if offered.")
        return InstallResult(False, plugin_id, message, code=code)


@_environment_serialized
def update_plugin(plugin_id: str, **kwargs) -> InstallResult:
    """Stage and validate before replacing the working revision."""
    return install_plugin(plugin_id, _updating=True, **kwargs)


@_environment_serialized
def recover_plugin_publication(plugin_id: str) -> InstallResult:
    """Observe and finish metadata only; never reacquire, execute, or enable."""
    from row_bot.plugins import state
    from row_bot.plugins.devtools import compute_plugin_checksum
    from row_bot.package_files import contained_path
    dest = _owned_package(plugin_id)
    package = state.get_plugin_package_state(plugin_id)
    pending = package.get("pending", {})
    if not pending:
        return InstallResult(True, plugin_id, "No incomplete publication.")
    candidate = pending["candidate"]
    if dest.exists():
        info = dest.stat()
        if [info.st_dev, info.st_ino] == pending["file_identity"] and compute_plugin_checksum(dest) == candidate["digest"]:
            state.publish_plugin_package(plugin_id, candidate, updating=bool(candidate["previous"]))
            return InstallResult(True, plugin_id, "Recovered the published package metadata.", version=candidate["version"])
        if package.get("digest") and compute_plugin_checksum(dest) == package["digest"]:
            state.set_plugin_package_state(plugin_id, {k: v for k, v in package.items() if k != "pending"})
            return InstallResult(True, plugin_id, "The previous package remains installed.")
    previous = contained_path(DATA_DIR, "plugin_revisions/" + plugin_id + "/previous")
    if not dest.exists() and previous.exists() and compute_plugin_checksum(previous) == package.get("digest"):
        previous.rename(dest)
        state.set_plugin_package_state(plugin_id, {k: v for k, v in package.items() if k != "pending"})
        return InstallResult(True, plugin_id, "Restored the interrupted previous package.")
    return InstallResult(False, plugin_id, "Publication remains uncertain. Inspect local package files before retrying.", code="plugin_publication_uncertain")


@_environment_serialized
def restore_plugin(plugin_id: str, *, operation_id: str = "", validate=lambda: None) -> InstallResult:
    """Explicit previous-code recovery; persistent data and current secrets stay."""
    from row_bot.plugins import state
    from row_bot.package_files import contained_path
    package = state.get_plugin_package_state(plugin_id)
    old = package.get("previous", {})
    if not old or package.get("pending"):
        return InstallResult(False, plugin_id, "No proven previous revision is available.", code="plugin_recovery_unavailable")
    previous = contained_path(DATA_DIR, "plugin_revisions/" + plugin_id + "/previous")
    return update_plugin(plugin_id, source_dir=previous, source=old.get("source", "local"),
        source_ref=old.get("source_identity", ""), source_pin=old.get("pin", ""), expected_checksum=old.get("digest"), operation_id=operation_id, validate=validate)


@_environment_serialized
def uninstall_plugin(plugin_id: str, *, purge_data: bool = False, operation_id: str = "") -> InstallResult:
    """Withdraw only owned capabilities; retain data/credentials by default."""
    from row_bot.plugins import registry, state
    from row_bot.package_files import contained_path
    try:
        dest = _owned_package(plugin_id)
        state.set_plugin_enabled(plugin_id, False)
        registry.unregister_plugin(plugin_id)
        from row_bot.mcp_client import runtime
        runtime.disconnect_plugin_servers(plugin_id)
        if dest.exists():
            shutil.rmtree(dest)
        package = state.get_plugin_package_state(plugin_id)
        if purge_data:
            # Only references in this parent's child overrides are owned here.
            from row_bot.mcp_client.auth import delete_credentials
            record = state._environment_state_document().get(plugin_id, {})
            for child in record.get("mcp", {}).values():
                ref = child.get("auth", {}).get("credential_ref")
                if ref:
                    delete_credentials(ref)
            data = contained_path(DATA_DIR, "plugin_data/" + plugin_id)
            if data.exists():
                shutil.rmtree(data)
            state.remove_plugin_state(plugin_id)
            state.set_plugin_package_state(plugin_id, {"removed": True, "purged": True,
                "operation_id": operation_id, "retained_data": False})
        else:
            state.set_plugin_package_state(plugin_id, {**package, "removed": True,
                "operation_id": operation_id, "retained_data": True})
        previous = contained_path(DATA_DIR, "plugin_revisions/" + plugin_id + "/previous")
        if previous.exists():
            shutil.rmtree(previous)
        return InstallResult(True, plugin_id, "Removed plugin. Saved data and credentials retained." if not purge_data else "Removed plugin and its saved data/credentials.")
    except Exception as exc:
        logger.warning("Plugin removal incomplete for %s (%s)", plugin_id, type(exc).__name__)
        return InstallResult(False, plugin_id, "Removal is incomplete. Owned capabilities were withdrawn; inspect retained data.", code="plugin_remove_incomplete")


def is_installed(plugin_id: str) -> bool:
    """Check if a plugin is installed."""
    return (PLUGINS_DIR / plugin_id).is_dir()


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


def _download_plugin_archive(
    plugin_id: str, dest: pathlib.Path, archive_url: str, archive_path: str = ""
) -> None:
    """Download or read a zip archive and extract one plugin directory."""

    logger.info("Downloading plugin '%s' from %s", plugin_id, archive_url)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="plugin-acquire-", dir=DATA_DIR) as tmp:
        zip_path = pathlib.Path(tmp) / "repo.zip"
        _download_to_file(archive_url, zip_path)

        with zipfile.ZipFile(zip_path, "r") as zf:
            extract_dir = pathlib.Path(tmp) / "extracted"
            _safe_extract_zip(zf, extract_dir)

        extracted_plugin = (
            _repository_folder(extract_dir, archive_path) if archive_path
            else _find_extracted_plugin_dir(extract_dir, plugin_id)
        )
        shutil.copytree(extracted_plugin, dest)

    logger.info("Downloaded plugin '%s' to %s", plugin_id, dest)


def _repository_folder(extract_dir: pathlib.Path, archive_path: str) -> pathlib.Path:
    """The *archive_path* folder of a repository archive (one top-level folder)."""
    tops = list(extract_dir.iterdir())
    if len(tops) != 1 or not tops[0].is_dir():
        raise ValueError("The plugin archive isn't a repository archive")
    root = tops[0].resolve()
    folder = root.joinpath(*archive_path.split("/")).resolve()
    if not folder.is_relative_to(root) or not folder.is_dir():
        raise FileNotFoundError(f"The repository archive has no folder '{archive_path}'")
    return folder


def _download_to_file(ref: str, dest: pathlib.Path) -> None:
    local_path = _local_path_from_ref(ref)
    if local_path is not None:
        if local_path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Plugin archive exceeds the download limit")
        shutil.copyfile(local_path, dest)
        return

    from row_bot.integrations.safe import fetch
    dest.write_bytes(fetch(ref, hosts=None, max_bytes=64 * 1024 * 1024, timeout=60, redirects=3,
        too_large="Plugin archive exceeds the download limit"))


def _safe_extract_zip(zf: zipfile.ZipFile, dest: pathlib.Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    members = zf.infolist()
    if len(members) > 2048 or sum(member.file_size for member in members) > 128 * 1024 * 1024:
        raise ValueError("Plugin archive exceeds the extraction limit")
    from row_bot.package_files import relative_package_path, contained_path
    seen = set()
    for member in members:
        relative_package_path(member.orig_filename.rstrip("/"))
        name = relative_package_path(member.filename.rstrip("/"))
        if name.casefold() in seen:
            raise ValueError("Plugin archive contains duplicate or case-colliding paths")
        seen.add(name.casefold())
        if stat.S_ISLNK(member.external_attr >> 16):
            raise ValueError("Plugin archive contains a symbolic link")
        target = contained_path(dest, name)
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as source, target.open("xb") as stream:
                shutil.copyfileobj(source, stream)


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
