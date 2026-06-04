"""Plugin installer — download, validate, install, update, uninstall.

Downloads plugin archives from the monorepo, validates before install,
checks dependency conflicts against core, and manages the local
``~/.thoth/installed_plugins/`` directory.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

DATA_DIR = get_row_bot_data_dir()
PLUGINS_DIR = DATA_DIR / "installed_plugins"

# Base URL for downloading plugins from the monorepo
# Each plugin directory is downloaded as: {BASE_URL}/plugins/{plugin_id}/
DEFAULT_REPO_URL = os.environ.get(
    "THOTH_PLUGIN_REPO_URL",
    "https://github.com/siddsachar/row-bot-plugins",
)


@dataclass
class InstallResult:
    """Result of an install/update/uninstall operation."""
    success: bool
    plugin_id: str
    message: str
    version: str = ""


# ── Public API ───────────────────────────────────────────────────────────────
def install_plugin(plugin_id: str, *, source_dir: pathlib.Path | None = None) -> InstallResult:
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
        else:
            # Download from repo
            _download_plugin(plugin_id, dest)

        # Validate manifest exists
        manifest_path = dest / "plugin.json"
        if not manifest_path.exists():
            shutil.rmtree(dest, ignore_errors=True)
            return InstallResult(
                success=False, plugin_id=plugin_id,
                message="Downloaded plugin is missing plugin.json",
            )

        # Parse manifest for version
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        version = manifest_data.get("version", "?")

        # Security scan
        from row_bot.plugins.loader import _security_scan
        sec_err = _security_scan(dest)
        if sec_err:
            shutil.rmtree(dest, ignore_errors=True)
            return InstallResult(
                success=False, plugin_id=plugin_id,
                message=f"Security check failed: {sec_err}",
            )

        # Check and install dependencies
        deps = manifest_data.get("python_dependencies", [])
        if deps:
            dep_result = _install_plugin_deps(deps)
            if not dep_result.success:
                shutil.rmtree(dest, ignore_errors=True)
                return InstallResult(
                    success=False, plugin_id=plugin_id,
                    message=f"Dependency conflict: {dep_result.message}",
                )

        logger.info("Plugin '%s' v%s installed to %s", plugin_id, version, dest)
        return InstallResult(
            success=True, plugin_id=plugin_id, version=version,
            message=f"Plugin '{plugin_id}' v{version} installed successfully",
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


def update_plugin(plugin_id: str, *, source_dir: pathlib.Path | None = None) -> InstallResult:
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

    backup = dest.with_suffix(".bak")
    try:
        # Backup current version
        if backup.exists():
            shutil.rmtree(backup)
        shutil.move(str(dest), str(backup))

        # Install new version
        result = install_plugin(plugin_id, source_dir=source_dir)

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
def _download_plugin(plugin_id: str, dest: pathlib.Path) -> None:
    """Download a plugin from the monorepo.

    Downloads the plugin directory as a zip from GitHub's archive API
    and extracts just the plugin's subdirectory.
    """
    import urllib.request

    # GitHub archive URL: downloads entire repo as zip
    archive_url = f"{DEFAULT_REPO_URL}/archive/refs/heads/main.zip"
    logger.info("Downloading plugin '%s' from %s", plugin_id, archive_url)

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = pathlib.Path(tmp) / "repo.zip"

        req = urllib.request.Request(
            archive_url, headers={"User-Agent": "Row-Bot-Plugin-Installer"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(zip_path, "wb") as f:
                f.write(resp.read())

        # Extract the plugin directory from the zip
        with zipfile.ZipFile(zip_path, "r") as zf:
            # GitHub zips have a top-level dir like "row-bot-plugins-main/"
            top_dirs = {name.split("/")[0] for name in zf.namelist() if "/" in name}
            if len(top_dirs) != 1:
                raise ValueError(f"Unexpected zip structure: {top_dirs}")
            top = top_dirs.pop()

            # The plugin should be at top/plugins/{plugin_id}/
            prefix = f"{top}/plugins/{plugin_id}/"
            members = [n for n in zf.namelist() if n.startswith(prefix)]
            if not members:
                raise FileNotFoundError(
                    f"Plugin '{plugin_id}' not found in marketplace repo"
                )

            # Extract to temp, then move to dest
            extract_dir = pathlib.Path(tmp) / "extracted"
            for member in members:
                zf.extract(member, extract_dir)

            extracted_plugin = extract_dir / top / "plugins" / plugin_id
            if not extracted_plugin.is_dir():
                raise FileNotFoundError(
                    f"Extracted plugin directory not found: {extracted_plugin}"
                )

            shutil.copytree(extracted_plugin, dest)

    logger.info("Downloaded plugin '%s' to %s", plugin_id, dest)


# ── Dependency Installation ──────────────────────────────────────────────────
def _install_plugin_deps(deps: list[str]) -> InstallResult:
    """Install plugin Python dependencies with core freeze protection."""
    from row_bot.plugins.sandbox import check_dependencies, install_dependencies

    # Pre-check for conflicts
    check_result = check_dependencies(deps)
    if not check_result.ok:
        conflicts = "; ".join(check_result.conflicts)
        return InstallResult(
            success=False, plugin_id="",
            message=f"Dependency conflicts with core: {conflicts}",
        )

    # Install
    try:
        success, message = install_dependencies(deps)
        if success:
            return InstallResult(success=True, plugin_id="", message="Dependencies installed")
        else:
            logger.warning("Plugin dependency install failed: %s", message)
            return InstallResult(
                success=False, plugin_id="",
                message=f"Install blocked: {message}",
            )
    except Exception as exc:
        logger.error("Plugin dependency install error: %s", exc, exc_info=True)
        return InstallResult(
            success=False, plugin_id="",
            message=f"Dependency install failed: {exc}",
        )
