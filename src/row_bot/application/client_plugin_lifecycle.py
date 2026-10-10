"""Explicit local-owner marketplace plugin lifecycle commands.

No marketplace fetch, download, install, or removal occurs on a passive read.
The existing installer and plugin state remain the canonical owners.
"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
import logging
import os
import threading
from uuid import uuid4

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions


_LOG = logging.getLogger(__name__)
_ACTIONS = frozenset({"install", "update", "remove", "refresh", "prepare", "restore", "recover", "purge"})
_LIFECYCLE_LOCK = threading.RLock()
_ACTIVE: set[str] = set()

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
    action: str, plugin_id: str, *, validate: Callable[[], None], owner_id: str = "", preview_id: str = ""
) -> dict:
    try:
        if preview_id:
            return _review_source(action, plugin_id, owner_id, preview_id, validate)
        return _review(action, plugin_id, validate=validate, owner_id=owner_id)
    except ClientPlatformError as exc:
        _log_refusal(action, plugin_id, exc.code)
        raise


def _review_source(action: str, plugin_id: str, owner_id: str, preview_id: str, validate: Callable[[], None]) -> dict:
    from row_bot.plugins.hermes_catalog import get_preview
    from row_bot.plugins import installer, state
    validate()
    preview = get_preview(owner_id, preview_id)
    if action not in {"install", "update"} or plugin_id != preview.plugin_id:
        raise ClientPlatformError("invalid_plugin_lifecycle_command")
    if installer.is_installed(plugin_id) != (action == "update"):
        raise ClientPlatformError("plugin_lifecycle_changed")
    current = state.get_plugin_package_state(plugin_id)
    from row_bot.plugins.lifecycle_review import describe_package_changes
    changes = describe_package_changes(installer.PLUGINS_DIR / plugin_id if action == "update" else None,
        preview.root, source_identity=preview.source_identity)
    data = {"action": action, "plugin_id": plugin_id, "name": preview.summary["name"], "version": preview.summary["version"],
        "source": preview.summary["source"], "checksum": preview.digest, "permissions": preview.summary["permissions"],
        "disclosures": ["Add copies the reviewed package and keeps it off. Enablement is separate.",
            "Only supported declared components are available. Foreign hooks and host entry points never execute.",
            "Skill instructions and MCP processes can access local files and declared services when used; processes are not an OS sandbox.",
            "The tree digest binds this review to these bytes; it is not independent publisher authentication.",
            "Runtime downloads, credentials, and tools require their own setup reviews."],
        "changes": changes,
        "revision": sha256(json.dumps([preview_id, preview.digest, preview.pin, preview.source_identity, current, changes], sort_keys=True).encode()).hexdigest()}
    validate()
    return data


def _review(action: str, plugin_id: str, *, validate: Callable[[], None], owner_id: str = "") -> dict:
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
        package = _package_revision(plugin_id)
        if item is None and (action == "purge" and package.get("removed") or action == "recover" and package.get("pending")):
            item = {"plugin_id": plugin_id, "name": package.get("upstream_name", plugin_id),
                "version": package.get("version", ""), "permissions": [], "installed": False, "capabilities": {}}
        if item is None:
            raise ClientPlatformError("plugin_not_found")
        if action == "install" and item["installed"]:
            raise ClientPlatformError("plugin_already_installed")
        if action in {"update", "remove", "prepare", "restore"} and not item["installed"]:
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
                else ["Restore replaces local package code with its previous revision; current data and credentials are preserved. External changes cannot be undone."] if action == "restore"
                else ["Recover reconciles a prior publication without downloading or executing code."] if action == "recover"
                else ["Removal withdraws the package and all its children. Saved data and credentials are retained unless you choose Delete saved data."]
            ),
        }
    if action == "update":
        from row_bot.plugins import hermes_catalog, installer
        from row_bot.plugins.lifecycle_review import describe_package_changes
        preview = hermes_catalog.inspect_marketplace_update(owner_id=owner_id, plugin_id=plugin_id,
            origin=origin, source_identity=source, checksum=checksum, source_revision=reviewed_tree)
        data["preview_id"] = preview.preview_id
        data["changes"] = describe_package_changes(installer.PLUGINS_DIR / plugin_id, preview.root, source_identity=source)
    if action in {"remove", "purge"}:
        children = package.get("children") or {}
        data["changes"] = [f"Owned {kind}: {name}" for kind, names in children.items() for name in names]
        data["disclosures"] = (["Delete only this package's retained local data and protected credentials. Shared runtimes and other packages remain."]
            if action == "purge" else ["Remove this package and withdraw all its owned tools, skills and connections. Saved data, configuration, permissions and credentials are retained; Delete saved data is a separate explicit action."])
    data["revision"] = sha256(
        json.dumps([data, reviewed_tree, _package_revision(plugin_id)], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    validate()
    return data


def _package_revision(plugin_id: str) -> dict:
    from row_bot.plugins.state import get_plugin_package_state
    return get_plugin_package_state(plugin_id) if plugin_id else {}


def execute_plugin_lifecycle(
    command: dict,
    *,
    owner_id: str,
    validate: Callable[[], None],
) -> dict:
    # Serializes local callers. Admissions excludes other owner processes.
    with _LIFECYCLE_LOCK:
        try:
            return _execute_plugin_lifecycle(command, owner_id=owner_id, validate=validate)
        finally:
            _ACTIVE.discard(command["command_id"])


def _execute_plugin_lifecycle(command: dict, *, owner_id: str, validate: Callable[[], None]) -> dict:
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
        "preview_id": command.get("preview_id", ""),
    }
    existing = admissions.read_command_metadata(owner_id, command["command_id"])
    if existing is not None:
        if existing["target"] != target or existing["type"] != wire["type"]:
            raise ClientPlatformError("idempotency_mismatch")
        try:
            return admissions.claim_command(owner_id, command["command_id"], wire, target)
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise
            result = read_plugin_lifecycle_receipt(command["command_id"], owner_id=owner_id, validate=validate)
            if not _original_alive(owner_id, command["command_id"]):
                admissions.complete_command(owner_id, command["command_id"], result)
            return result
    # An explicit recovery review may settle interrupted bookkeeping. It never
    # resumes a live owner or silently repeats an installation.
    if action == "recover":
        pending = admissions.read_unfinished_target_commands(target)
        if pending["overflow"]:
            raise ClientPlatformError("operation_pending")
        for row in pending["items"]:
            if row["owner_id"] != owner_id or _original_alive(owner_id, row["command_id"]):
                raise ClientPlatformError("operation_pending")
            original = read_plugin_lifecycle_receipt(row["command_id"], owner_id=owner_id, validate=validate)
            admissions.complete_command(owner_id, row["command_id"], original)
    reviewed = review_plugin_lifecycle(action, plugin_id, validate=validate, owner_id=owner_id, preview_id=command.get("preview_id", ""))
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
            "_process": {"pid": os.getpid(), "birth": __import__("psutil").Process().create_time()},
        },
    )
    _ACTIVE.add(command["command_id"])
    validate()
    if action == "refresh":
        index = marketplace.fetch_index(force_refresh=True)
        success = bool(index.plugins)
        message = f"Marketplace has {len(index.plugins)} plugin(s)." if success else "Marketplace is unavailable; the saved catalog remains available."
    elif action in {"remove", "purge"}:
        outcome = installer.uninstall_plugin(plugin_id, purge_data=action == "purge", operation_id=command["command_id"])
        success, message = outcome.success, "Plugin removed." if outcome.success else "Plugin removal failed; inspect the local installation."
    elif action in {"restore", "recover"}:
        outcome = installer.restore_plugin(plugin_id, operation_id=command["command_id"], validate=validate) if action == "restore" else installer.recover_plugin_publication(plugin_id)
        success, message = outcome.success, outcome.message
    elif action == "prepare":
        success, code = _prepare(plugin_id, command["command_id"])
        message = (
            f"Prepared {reviewed['name']}. It loads now."
            if success
            else f"Row-Bot couldn't prepare {reviewed['name']} ({code or 'environment_preparation_failed'})."
        )
    else:
        if command.get("preview_id") or reviewed.get("preview_id"):
            from row_bot.plugins.hermes_catalog import get_preview
            preview = get_preview(owner_id, command.get("preview_id") or reviewed["preview_id"])
            kwargs = {"source": preview.summary.get("source_kind") or ("portable" if preview.summary["format"] != "row-bot-v2" else "local"),
                "source_ref": preview.source_identity, "source_dir": preview.root,
                "expected_checksum": preview.digest, "source_pin": preview.pin, "operation_id": command["command_id"]}
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
            "operation_id": command["command_id"],
            }
        outcome = (
            installer.install_plugin(plugin_id, **kwargs, validate=validate)
            if action == "install"
            else installer.update_plugin(plugin_id, **kwargs, validate=validate)
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
    if success and action in {"install", "update", "remove", "prepare", "restore", "recover", "purge"}:
        from row_bot.plugins import loader

        try:
            loader.refresh_plugin_runtime(f"plugin {action}", plugin_id=plugin_id)
        except Exception:
            message += " Files were published, but runtime refresh needs attention. Reload the affected package."
    validate()
    result = {
        "command_id": command["command_id"],
        "status": "completed" if success else "failed",
        "action": action,
        "plugin_id": plugin_id,
        "message": message,
    }
    return admissions.complete_command(owner_id, command["command_id"], result)


def _original_alive(owner_id: str, command_id: str) -> bool:
    import psutil
    saved = admissions.read_command_receipt(owner_id, command_id) or {}
    process = saved.get("_process", {})
    if process.get("pid") == os.getpid():
        return command_id in _ACTIVE
    try:
        return psutil.Process(process["pid"]).create_time() == process["birth"]
    except (KeyError, TypeError, psutil.Error):
        return False


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
        from row_bot.plugins import installer
        from row_bot.plugins.devtools import compute_plugin_checksum
        try:
            package = _package_revision(result.get("plugin_id", ""))
            destination = installer._owned_package(result["plugin_id"]) if result.get("plugin_id") else None
            proven = package.get("operation_id") == command_id and not package.get("pending")
            published = proven and ((package.get("removed") and destination is not None and not destination.exists())
                or (destination is not None and destination.exists() and compute_plugin_checksum(destination) == package.get("digest")))
        except (OSError, ValueError, KeyError, TypeError):
            # Missing or unreadable evidence cannot turn an interrupted write into success.
            published = False
        validate()
        if published:
            return {"command_id": command_id, "status": "completed", "action": result["action"],
                "plugin_id": result["plugin_id"], "message": "The original package publication is confirmed. Runtime setup may still need attention."}
        return {
            "command_id": command_id,
            "status": "uncertain",
            "action": result.get("action", "refresh"),
            "plugin_id": result.get("plugin_id", ""),
            "message": "The outcome is unconfirmed. Inspect installed plugins before another action.",
        }
    validate()
    return result


def reconcile_plugin_operation(*, owner_id: str, command_id: str, validate: Callable[[], None]) -> dict:
    """Explicitly settle bookkeeping; an uncertain package still needs recovery."""
    validate()
    with _LIFECYCLE_LOCK:
        result = read_plugin_lifecycle_receipt(command_id, owner_id=owner_id, validate=validate)
        if _original_alive(owner_id, command_id):
            raise ClientPlatformError("operation_pending")
        validate()
        admissions.complete_command(owner_id, command_id, result)
        return {"command_id": command_id, "settled": True, "message": result["message"]}
