"""Explicit local-owner marketplace plugin lifecycle commands.

No marketplace fetch, download, install, or removal occurs on a passive read.
The existing installer and plugin state remain the canonical owners.
"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
from urllib.parse import urlsplit

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions


_ACTIONS = frozenset({"install", "update", "remove", "refresh"})


def _entry(plugin_id: str):
    from row_bot.plugins import marketplace

    index = marketplace.get_cached_index(allow_stale=True)
    if index is None:
        raise ClientPlatformError("plugin_marketplace_unavailable")
    entry = marketplace.get_entry(plugin_id, index=index)
    if entry is None:
        raise ClientPlatformError("plugin_marketplace_entry_unavailable")
    return entry


def _source(entry) -> str:
    from row_bot.plugins.installer import DEFAULT_REPO_URL

    source = entry.archive_url or f"{DEFAULT_REPO_URL}/archive/refs/heads/main.zip"
    parsed = urlsplit(source)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ClientPlatformError("plugin_source_unavailable")
    return source


def _local_source(entry):
    from row_bot.plugins import marketplace

    if not getattr(entry, "path", ""):
        return None
    source = marketplace.source_dir_for_entry(entry)
    if source is None:
        raise ClientPlatformError("plugin_source_unavailable")
    return source


def review_plugin_lifecycle(
    action: str, plugin_id: str, *, validate: Callable[[], None]
) -> dict:
    from row_bot.application import plugin_commands

    validate()
    if action not in _ACTIONS or (action != "refresh" and not plugin_commands._ID.fullmatch(plugin_id)):
        raise ClientPlatformError("invalid_plugin_lifecycle_command")
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
        if action in {"update", "remove"} and not item["installed"]:
            raise ClientPlatformError("plugin_not_installed")
        if action == "update" and not item["update_version"]:
            raise ClientPlatformError("plugin_update_unavailable")
        entry = _entry(plugin_id) if action in {"install", "update"} else None
        local_source = _local_source(entry) if entry else None
        source = (
            f"Local directory: {local_source.name}" if local_source else
            _source(entry) if entry else "installed local plugin"
        )
        version = entry.version if entry else item["version"]
        checksum = str(entry.checksum or "") if entry else ""
        if local_source is not None:
            from row_bot.plugins.installer import _tree_revision

            checksum = _tree_revision(local_source, source=True)
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
                    "The marketplace index does not pin this package with a checksum; its content may change before download."
                    if not checksum else "The downloaded package is checked against the displayed checksum.",
                ]
                if action in {"install", "update"}
                else ["Removal deletes plugin files, settings, and secret metadata. This cannot be undone."]
            ),
        }
    data["revision"] = sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
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
    else:
        entry = _entry(plugin_id)
        local_source = _local_source(entry)
        source = _source(entry) if local_source is None else str(local_source)
        kwargs = {
            "source": "marketplace",
            "source_ref": source,
            "source_dir": local_source,
            "archive_url": source if local_source is None else "",
            "expected_checksum": (entry.checksum or None) if local_source is None else None,
        }
        outcome = (
            installer.install_plugin(plugin_id, **kwargs)
            if action == "install"
            else installer.update_plugin(plugin_id, **kwargs)
        )
        success = outcome.success
        message = (
            f"Installed {plugin_id} and kept it disabled."
            if success and action == "install"
            else f"Updated {plugin_id}." if success else
            "Plugin operation failed; inspect the local installation."
        )
    if success and action in {"install", "update", "remove"}:
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
