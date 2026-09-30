"""Explicit local-owner marketplace plugin lifecycle commands.

No marketplace fetch, download, install, or removal occurs on a passive read.
The existing installer and plugin state remain the canonical owners.
"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
import logging
from uuid import uuid4

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions


_LOG = logging.getLogger(__name__)
_ACTIONS = frozenset({"install", "update", "remove", "refresh", "prepare"})

_PREPARE_DISCLOSURES = [
    "Row-Bot creates a private Python environment for this plugin in its data folder, then loads the plugin.",
    "Nothing is downloaded: the plugin declares no dependencies.",
]


def _prepare(plugin_id: str, operation_id: str) -> tuple[bool, str]:
    """Prepare a worker plugin's private environment (B129).

    Worker plugins (a ``plugin_main.py``) load only from a prepared
    environment. Plugins declare no dependencies, so nothing is downloaded.
    """
    from row_bot.plugins import installer

    outcome = installer.prepare_plugin_environment(
        plugin_id,
        [],
        expected_plugin_revision=installer.get_plugin_source_revision(plugin_id),
        operation_id=operation_id,
    )
    return outcome.ready, str(outcome.error_code or "")


def _entry(plugin_id: str):
    from row_bot.plugins import marketplace

    index = marketplace.get_cached_index(allow_stale=True)
    if index is None:
        raise ClientPlatformError("plugin_marketplace_unavailable")
    entry = marketplace.get_entry(plugin_id, index=index)
    if entry is None:
        raise ClientPlatformError("plugin_marketplace_entry_unavailable")
    return entry


def _origin(entry):
    """Where the entry installs from; refused with its code when it can't (B266)."""
    from row_bot.plugins import marketplace

    origin = marketplace.entry_source(entry)
    if origin.problem:
        raise ClientPlatformError(origin.problem)
    return origin


def _described(origin) -> str:
    if origin.local_dir is not None:
        return f"Local directory: {origin.local_dir.name}"
    if origin.archive_path:
        return f"{origin.archive_url} (folder {origin.archive_path})"
    return origin.archive_url


def _log_refusal(action: str, plugin_id: str, code: str) -> None:
    from row_bot.application import plugin_commands

    _LOG.warning(
        "Plugin %s for %s refused: %s",
        action if action in _ACTIONS else "action",
        plugin_id if plugin_commands._ID.fullmatch(plugin_id) else "marketplace",
        code,
    )


def review_plugin_lifecycle(
    action: str, plugin_id: str, *, validate: Callable[[], None]
) -> dict:
    try:
        return _review(action, plugin_id, validate=validate)
    except ClientPlatformError as exc:
        _log_refusal(action, plugin_id, exc.code)
        raise


def _review(action: str, plugin_id: str, *, validate: Callable[[], None]) -> dict:
    from row_bot.application import plugin_commands

    validate()
    if action not in _ACTIONS or (action != "refresh" and not plugin_commands._ID.fullmatch(plugin_id)):
        raise ClientPlatformError("invalid_plugin_lifecycle_command")
    reviewed_tree = ""
    if action == "refresh":
        data = {
            "action": action,
            "plugin_id": "",
            "name": "Plugin Marketplace",
            "version": "",
            "source": "configured marketplace index",
            "checksum": "",
            "permissions": [],
            "disclosures": ["Fetch the configured marketplace index over the network."],
        }
    else:
        items, _catalog_revision = plugin_commands._catalog(validate)
        item = next((row for row in items if row["plugin_id"] == plugin_id), None)
        if item is None:
            raise ClientPlatformError("plugin_not_found")
        if action == "install" and item["installed"]:
            raise ClientPlatformError("plugin_already_installed")
        if action in {"update", "remove", "prepare"} and not item["installed"]:
            raise ClientPlatformError("plugin_not_installed")
        if action == "prepare" and not item["capabilities"].get("prepare", {}).get("available"):
            raise ClientPlatformError("plugin_environment_ready")
        if action == "update" and not item["update_version"]:
            raise ClientPlatformError("plugin_update_unavailable")
        entry = _entry(plugin_id) if action in {"install", "update"} else None
        origin = _origin(entry) if entry else None
        source = _described(origin) if origin else "installed local plugin"
        version = entry.version if entry else item["version"]
        checksum = str(entry.checksum or "") if entry else ""
        if origin is not None and origin.local_dir is not None:
            from row_bot.plugins.installer import _tree_revision

            # A local folder installs only as reviewed, checksum or not.
            reviewed_tree = _tree_revision(origin.local_dir, source=True)
        data = {
            "action": action,
            "plugin_id": plugin_id,
            "name": item["name"],
            "version": version,
            "source": source,
            "checksum": checksum,
            "permissions": item["permissions"],
            "disclosures": (
                [
                    "Plugin code is copied locally and kept disabled until configuration and testing.",
                    "Third-party plugin code and dependencies may contact external services when enabled; inspect its source and permissions before proceeding.",
                    "The plugin's files are checked against the displayed checksum before they are installed."
                    if checksum else "The marketplace index lists no checksum for this local folder; it installs only if unchanged since this review.",
                    "A plugin with its own code gets a private Python environment in Row-Bot's data folder.",
                ]
                if action in {"install", "update"}
                else list(_PREPARE_DISCLOSURES)
                if action == "prepare"
                else ["Removal deletes plugin files, settings, and secret metadata. This cannot be undone."]
            ),
        }
    data["revision"] = sha256(
        json.dumps([data, reviewed_tree], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    validate()
    return data


def execute_plugin_lifecycle(
    command: dict,
    *,
    owner_id: str,
    validate: Callable[[], None],
) -> dict:
    from row_bot.plugins import installer, marketplace

    action = command["action"]
    plugin_id = command["plugin_id"]
    if action not in _ACTIONS:
        raise ClientPlatformError("invalid_plugin_lifecycle_command")
    target = f"settings:plugin:lifecycle:{plugin_id or 'marketplace'}"
    wire = {
        "command_id": command["command_id"],
        "type": f"plugin.lifecycle.{action}",
        "action": action,
        "plugin_id": plugin_id,
        "revision": command["revision"],
    }
    existing = admissions.read_command_metadata(owner_id, command["command_id"])
    if existing is not None:
        if existing["target"] != target or existing["type"] != wire["type"]:
            raise ClientPlatformError("idempotency_mismatch")
        return admissions.claim_command(owner_id, command["command_id"], wire, target)
    reviewed = review_plugin_lifecycle(action, plugin_id, validate=validate)
    if command["revision"] != reviewed["revision"]:
        _log_refusal(action, plugin_id, "plugin_lifecycle_changed")
        raise ClientPlatformError("plugin_lifecycle_changed")
    admissions.claim_command(
        owner_id,
        command["command_id"],
        wire,
        target,
        exclusive_target=True,
        initial_result={
            "command_id": command["command_id"],
            "action": action,
            "plugin_id": plugin_id,
            "version": reviewed["version"],
            "status": "accepted",
        },
    )
    validate()
    if action == "refresh":
        index = marketplace.fetch_index(force_refresh=True)
        success = bool(index.plugins)
        message = f"Marketplace has {len(index.plugins)} plugin(s)." if success else "Marketplace is unavailable; the saved catalog remains available."
    elif action == "remove":
        outcome = installer.uninstall_plugin(plugin_id)
        success, message = outcome.success, "Plugin removed." if outcome.success else "Plugin removal failed; inspect the local installation."
    elif action == "prepare":
        success, code = _prepare(plugin_id, command["command_id"])
        message = (
            f"Prepared {reviewed['name']}. It loads now."
            if success
            else f"Row-Bot couldn't prepare {reviewed['name']} ({code or 'environment_preparation_failed'})."
        )
    else:
        entry = _entry(plugin_id)
        origin = _origin(entry)
        kwargs = {
            "source": "marketplace",
            "source_ref": str(origin.local_dir) if origin.local_dir else _described(origin),
            "source_dir": origin.local_dir,
            "archive_url": origin.archive_url,
            "archive_path": origin.archive_path,
            # A local folder is checked too when the index lists a checksum (B208).
            "expected_checksum": entry.checksum or None,
        }
        outcome = (
            installer.install_plugin(plugin_id, **kwargs)
            if action == "install"
            else installer.update_plugin(plugin_id, **kwargs)
        )
        success = outcome.success
        if success:
            message = f"Installed {plugin_id} and kept it disabled." if action == "install" else f"Updated {plugin_id}."
        else:
            # The installer's own reason, bounded; the log names only its code.
            reason = " ".join(str(outcome.message).split())[:300]
            message = f"Couldn't {action} {plugin_id}: {reason}"
            if outcome.code == "plugin_checksum_mismatch":
                message += " Refresh the marketplace, then try again."
            _LOG.warning("Plugin %s for %s failed: %s", action, plugin_id, outcome.code)
        if success:
            # A worker plugin loads only from its prepared environment (B129).
            from row_bot.application.plugin_commands import environment_needed

            if environment_needed(plugin_id):
                prepared, code = _prepare(plugin_id, str(uuid4()))
                if not prepared:
                    message += f" Its environment isn't ready ({code}); use Prepare."
    if success and action in {"install", "update", "remove", "prepare"}:
        from row_bot.plugins import loader

        loader.refresh_plugin_runtime(f"plugin {action}")
    validate()
    result = {
        "command_id": command["command_id"],
        "status": "completed" if success else "failed",
        "action": action,
        "plugin_id": plugin_id,
        "message": message,
    }
    return admissions.complete_command(owner_id, command["command_id"], result)


def read_plugin_lifecycle_receipt(
    command_id: str, *, owner_id: str, validate: Callable[[], None]
) -> dict:
    validate()
    metadata = admissions.read_command_metadata(owner_id, command_id)
    if metadata is None or not metadata["type"].startswith("plugin.lifecycle."):
        raise ClientPlatformError("plugin_lifecycle_receipt_unavailable")
    result = admissions.read_command_receipt(owner_id, command_id)
    if result is None:
        raise ClientPlatformError("plugin_lifecycle_receipt_unavailable")
    if metadata["status"] == "admitting":
        return {
            "command_id": command_id,
            "status": "uncertain",
            "action": result.get("action", "refresh"),
            "plugin_id": result.get("plugin_id", ""),
            "message": "The outcome is unconfirmed. Inspect installed plugins before another action.",
        }
    validate()
    return result
