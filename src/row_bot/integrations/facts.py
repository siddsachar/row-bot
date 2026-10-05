"""Typed owner facts and status v2: one projection over skills, MCP servers and packages.

Owners stay authoritative and are read, never bypassed. Static facts are indexed
and reused until an owner's stored state changes; live runtime state, local
requirements and unfinished changes are applied on every read, and unfinished
changes are reconciled there (observed and settled, never repeated).
"""
from __future__ import annotations

from collections.abc import Callable
import copy
import hashlib
import os
import threading
from urllib.parse import urlsplit

from row_bot.integrations import apps
from row_bot.integrations.safe import public_url

# Blocking code -> (readiness, next action). Order is precedence.
BLOCKING = {
    "configuration_recovery": ("attention", "retry"),
    "change_unconfirmed": ("attention", "retry"),
    "change_in_progress": ("working", "none"),
    "auth_unsupported": ("needs_setup", "fix"),
    "sign_in_required": ("needs_sign_in", "sign_in"),
    "expired": ("needs_sign_in", "sign_in"),
    "key_required": ("needs_key", "add_key"),
    "inputs_required": ("needs_setup", "continue_setup"),
    "connection_failed": ("attention", "fix"),
    "cleanup_incomplete": ("attention", "retry"),
    "tools_changed": ("attention", "fix"),
    "source_removed": ("attention", "fix"),
    "source_blocked": ("attention", "fix"),
    "package_failed": ("attention", "fix"),
    "included_attention": ("attention", "fix"),
    "missing_runtime": ("needs_runtime", "install_runtime"),
    "package_preparation": ("needs_runtime", "continue_setup"),
    "app_required": ("needs_app", "open_app"),
    "tools_not_accepted": ("needs_setup", "continue_setup"),
    "package_setup": ("needs_setup", "continue_setup"),
    "included_needs_setup": ("needs_setup", "continue_setup"),
    "not_connected": ("needs_setup", "continue_setup"),
    "unsupported": (None, "none"),
}
LABELS = {"connect": "Connect", "add": "Add", "install": "Add", "continue_setup": "Continue setup", "sign_in": "Sign in",
          "add_key": "Add key", "install_runtime": "Continue setup", "open_app": "Check again", "turn_on": "Turn on",
          "fix": "Fix", "retry": "Retry", "try": "Try it", "delete_data": "Delete saved data", "turn_off": "Turn off",
          "remove": "Remove", "update": "Update", "none": ""}
_MESSAGES = {
    "configuration_recovery": "Finishing your last change. Retry if this does not clear.",
    "change_unconfirmed": "Your last change did not finish. Retry to check it again.",
    "change_in_progress": "Finishing your last change.",
    "auth_unsupported": "This authentication method is not supported. Review the publisher setup requirements.",
    "sign_in_required": "Connect the account for this connection before testing its tools.",
    "expired": "The saved sign-in no longer works. Sign in again.",
    "key_required": "Connect the account for this connection before testing its tools.",
    "inputs_required": "Add the settings this connection needs.",
    "connection_failed": "The connection failed or its cleanup needs attention.",
    "cleanup_incomplete": "The connection failed or its cleanup needs attention.",
    "missing_runtime": "Finish the required runtime setup.",
    "package_preparation": "Finish the required runtime setup.",
    "tools_not_accepted": "Test the connection and accept its tools.",
    "not_connected": "Connect using the accepted tools.",
    "package_setup": "Finish this package's setup and checks.",
    "package_failed": "This package failed to load or its checks failed.",
    "source_removed": "The publisher withdrew this package. Your data is kept.",
}
_FAILED = {"failed", "error", "sdk_missing", "cleanup_incomplete"}
_LOCK = threading.RLock()
_INDEX: dict = {}


def blocker(code: str, message: str = "", *, subject: str = "", severity: str = "") -> dict:
    return {"code": code, "severity": severity or ("blocking" if code in BLOCKING else "info"),
            "message": (message or _MESSAGES.get(code, ""))[:512], "subject": subject[:256]}


def status(kind: str, lifecycle: str, blockers: list[dict]) -> dict:
    """Status v2: lifecycle, readiness, every blocker, and exactly one next action."""
    blocking = sorted((b for b in blockers if b["severity"] == "blocking"), key=lambda b: list(BLOCKING).index(b["code"]))
    if blocking:
        readiness, action = BLOCKING[blocking[0]["code"]]
    elif lifecycle == "available":
        readiness, action = None, {"mcp": "connect", "skill": "add"}.get(kind, "install")
    elif lifecycle == "data_retained":
        readiness, action = None, "delete_data"
    else:
        readiness, action = "ready", "turn_on" if lifecycle == "off" else "try"
    label = "Sign in again" if blocking and blocking[0]["code"] == "expired" else LABELS[action]
    return {"lifecycle": lifecycle, "readiness": readiness, "blockers": blocking + [b for b in blockers if b not in blocking],
            "next_action": {"kind": action, "label": label}}


def entry(kind: str, owner_ref: str, name: str, **fields) -> dict:
    """The one internal record; typed responses are views of it."""
    row = {"id": kind + ":" + owner_ref, "kind": kind, "owner_ref": owner_ref, "name": str(name)[:256],
        "description": "", "parent_id": None, "source": "local", "publisher": "", "source_url": "", "version": "", "pin": "",
        "license": "", "compatibility": "supported", "platforms": [], "evidence": "Live service behavior has not been tested.",
        "installed": True, "enabled": False, "revision": "", "actions": [], "auth_status": "none", "account_label": "",
        "children": [], "target": None, "attributions": [], "evidence_stage": "listed", "tested_with_row_bot": False,
        "auth_requirement": "unknown", "canonical_identity": "", "app": None, "lifecycle": "installed", "blockers": [],
        # Catalog ranking and dedup: vendor verification, setup tier (0 best), freshness, source signals.
        "verified": False, "featured_rank": None, "setup_tier": 0, "updated_at": 0, "popularity": 0, "signals": None, "identities": [],
        "icon": apps.letter(name)}
    row.update(fields)
    return row


def finish(row: dict) -> dict:
    """Derive status v2 from lifecycle and blockers."""
    row.update(status(row["kind"], row["lifecycle"], row["blockers"]))
    if row["app"]:
        row["verified"] = row["verified"] or row["app"]["verified"]
        row["icon"] = row["app"]["icon"] if row["icon"].startswith("letter:") else row["icon"]
    return row


def mcp_setup(server: dict, cfg: dict) -> dict:
    """Setup requirements, never secret values or unreviewed executable recipes."""
    from row_bot.integrations import inputs
    from row_bot.mcp_client.auth import McpAuthError, validate_metadata
    auth, source = cfg.get("auth") or {}, cfg.get("source") or {}
    local = cfg.get("transport", "stdio") == "stdio"
    mode = auth.get("mode") if auth.get("mode") in {"oauth", "api_key"} else source.get("auth_mode", "unknown")
    if mode not in {"none", "oauth", "api_key", "unsupported"}:
        mode = "none" if auth.get("mode") == "none" and not auth.get("operation_id") and not source.get("requires_auth") else "unknown"
    # Disconnect records mode=none; it cannot erase a declared account requirement.
    try:
        declared = inputs.check(cfg.get("inputs"))
        # A declared secret is saved like any key: one keychain credential, filled into its templates at use.
        secrets = [{"kind": "input", "name": item["key"], "key": item["key"]} for item in declared if item["secret"]]
        bindings = validate_metadata({"mode": "api_key", "bindings": (auth.get("bindings") if auth.get("mode") == "api_key"
                                      else None) or [*(source.get("auth_bindings") or []), *secrets]})["bindings"]
        if any(binding["kind"] != "input" and (binding["kind"] == "env") != local for binding in bindings):
            raise McpAuthError("invalid_mcp_auth")
    except (McpAuthError, inputs.InputError):
        declared, bindings, mode = [], [], "unsupported"
    if any(item["secret"] for item in declared):
        # A declared secret is kept as a key, whatever the source called its sign-in (a plugin's mcp.json says
        # nothing). A sign-in's tokens would replace that key in the one credential: not supported together.
        mode = "unsupported" if mode == "oauth" else "api_key" if mode in {"unknown", "none"} else mode
    destination = "Local process"
    if not local:
        try:
            url = urlsplit(str(cfg.get("url", "")))
            destination = f"{url.scheme}://{url.hostname or ''}" + (f":{url.port}" if url.port else "") + url.path
        except ValueError:
            destination = "Invalid connection destination"
    return {"auth_mode": mode, "execution": "local" if local else "hosted", "destination": destination[:2048],
        "bindings": bindings, "credential_configured": bool(auth.get("credential_ref")), "inputs": declared,
        "inputs_missing": inputs.missing(cfg, bool(auth.get("credential_ref")))[1],
        "catalog_accepted": isinstance((cfg.get("tools") or {}).get("catalog"), dict),
        "requirements": list(server.get("requirements") or []),
        "runtime_status": str(server.get("runtime_status") or "not_connected")[:64],
        "package_prepared": bool(cfg.get("managed_launch") or cfg.get("plugin_prepared")),
        "package_required": local and _locked_package(cfg) and not cfg.get("managed_launch"),
        "account_requirements": str(source.get("account_requirements") or ("Account required; consult publisher requirements."
            if mode in {"oauth", "api_key"} else "Account requirements not supplied."))[:512],
        "cost": str(source.get("cost") or "Cost and subscription requirements not supplied. Check the publisher before connecting.")[:512],
        "evidence": str(source.get("evidence") or "Saved connection settings; live account access has not been verified.")[:512]}


def _locked_package(cfg: dict) -> bool:
    """Whether its launch needs a reviewed package: an npx recipe (as before), or any package a catalog
    recipe runs (its minimal environment is what makes the runtime use the reviewed folder). A connection
    the person wrote themselves that runs uvx or docker keeps running as they wrote it."""
    from row_bot.mcp_client.packages import kind
    found = kind(cfg)
    return found == "npm" or (found is not None and cfg.get("environment_mode") == "minimal")


def mcp_blockers(setup: dict, runtime: dict, *, enabled: bool) -> list[dict]:
    """What stands between a saved MCP connection and use, from typed facts only."""
    found = []
    state = runtime.get("status")
    if setup["auth_mode"] == "unsupported":
        found.append(blocker("auth_unsupported"))
    elif setup["auth_mode"] in {"oauth", "api_key"} and not setup["credential_configured"]:
        found.append(blocker("sign_in_required" if setup["auth_mode"] == "oauth" else "key_required"))
    elif setup["credential_configured"] and state in _FAILED | {"disconnected"} and runtime.get("sign_in_failed"):
        found.append(blocker("expired"))
    if setup.get("inputs_missing"):
        found.append(blocker("inputs_required"))
    if state in _FAILED and not runtime.get("sign_in_failed"):
        found.append(blocker("cleanup_incomplete" if state == "cleanup_incomplete" else "connection_failed"))
    if state == "dependency_missing" or any(not r["available"] for r in setup["requirements"]):
        found.append(blocker("missing_runtime"))
    if setup["package_required"]:
        found.append(blocker("package_preparation"))
    if not setup["catalog_accepted"]:
        found.append(blocker("tools_not_accepted"))
    elif enabled and state != "connected" and not found:
        found.append(blocker("not_connected"))
    return found


def _requirements(cfg: dict) -> list[dict]:
    from row_bot.application.capability_configuration_controls import requirement_summaries
    return list(requirement_summaries(cfg))


def _mcp_refs(cfg: dict) -> list[str]:
    source = cfg.get("source") or {}
    if source.get("marketplace") == "official":  # Imported from the Registry: judged by its namespace alone.
        return apps.registry_refs(str(source.get("registry_name") or ""))
    refs = apps.recipe_refs(cfg)
    if source.get("marketplace") == "curated" and source.get("id"):
        refs.insert(0, "curated:" + str(source["id"]).lower())
    return refs


def _fingerprint() -> tuple:
    """Changes whenever an owner's stored state changes; cheap to compute."""
    from row_bot import skills
    from row_bot.data_paths import get_row_bot_data_dir
    from row_bot.mcp_client import config
    from row_bot.application.plugin_commands import _failed_to_load
    from row_bot.skills_hub.provenance import lockfile_path
    root = get_row_bot_data_dir(create=False)

    def digest(path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""
    packages = []
    try:
        with os.scandir(root / "installed_plugins") as stream:
            packages = sorted((e.name, e.stat(follow_symlinks=False).st_mtime_ns) for e in stream)
    except OSError:
        pass
    return (str(root), skills._client_library_fingerprint(), digest(config.CONFIG_PATH), digest(root / "plugin_state.json"),
            digest(lockfile_path(create=False)), digest(root / "marketplace_cache.json"), tuple(packages),
            tuple(sorted(_failed_to_load())))


def _static(validate: Callable[[], None]) -> tuple[list[dict], list[dict]]:
    """Owner facts that change only when owners publish; reused between reads."""
    from row_bot import skills
    from row_bot.application import plugin_commands
    from row_bot.mcp_client import config
    from row_bot.skills_hub.provenance import load_records
    rows, errors = [], []
    try:
        snapshot = skills.read_client_skills()
        records = load_records()
        for item in snapshot["items"].values():
            skill = item["skill"]
            if skills.is_tool_guide(skill) or skill.source == "plugin":
                continue
            record = records.get(skill.name)
            blocked = str((record.metadata if record else {}).get("source_blocked") or "")[:512]
            available = snapshot["enabled"].get(skill.name, False)
            rows.append(entry("skill", skill.name, skill.display_name, description=skill.description[:2048],
                source=record.source if record else skill.source, version=str(getattr(skill, "version", "") or "")[:128],
                revision=item.get("revision", ""), enabled=available, lifecycle="installed" if available else "off",
                blockers=[blocker("source_blocked", blocked)] if blocked else [],
                actions=["configure", "edit"] if skill.source == "user" else ["configure"],
                evidence="Local instructions; use remains subject to tool approvals."))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        errors.append({"source": "skills", "status": "error", "message": "The skill inventory is unavailable; other integrations remain available.", "fetched_at": None})
    try:
        for plugin in plugin_commands.read_integration_packages(validate=validate):
            if plugin["installed"] or plugin.get("retained"):
                rows.append(_package(plugin, validate))
    except (OSError, ValueError, KeyError, TypeError):
        errors.append({"source": "plugins", "status": "error", "message": "The package inventory is unavailable; other integrations remain available.", "fetched_at": None})
    try:
        from row_bot.application.capability_configuration_controls import _revision, _server_id
        saved = config.read_saved_configuration()
        for name, cfg in saved.document.get("servers", {}).items():
            source = cfg.get("source") or {}
            rows.append(entry("mcp", _server_id(name), name, source=str(source.get("marketplace") or "custom")[:80],
                description="Tools from an external MCP server.", enabled=cfg.get("enabled") is True, revision=_revision(saved),
                target={"kind": "standalone"}, actions=["configure", "test", "connect", "remove"], source_url=public_url(source.get("url")),
                app=_app(_mcp_refs(cfg)), _mcp={"name": name, "cfg": cfg, "active": saved.document.get("enabled") is True}))
    except (OSError, ValueError, KeyError, TypeError):
        errors.append({"source": "mcp", "status": "error", "message": "The MCP inventory is unavailable; other integrations remain available.", "fetched_at": None})
    return rows, errors


def _app(refs: list[str]) -> dict | None:
    app = apps.match(refs)
    return app.ref(verified=apps.verified(app, refs)) if app else None


def _package(plugin: dict, validate: Callable[[], None]) -> dict:
    from row_bot.application.capability_configuration_controls import read_mcp_configuration
    from row_bot.mcp_client import config
    from row_bot.plugins.state import get_mcp_child_overrides
    identity = "plugin:" + plugin["plugin_id"]
    app = _app(apps.repository_refs(plugin.get("source_identity", "")))
    blockers = []
    for item in plugin.get("diagnostics", [])[:16]:
        code = "source_removed" if item.get("component") == "source" else "diagnostic"
        blockers.append(blocker(code, str(item.get("message", item.get("reason", "Unsupported component")))))
    if plugin.get("capabilities", {}).get("enable", {}).get("code") == "package_source_removed":
        blockers.append(blocker("source_removed"))
    if plugin.get("publication_pending"):
        blockers.append(blocker("change_unconfirmed", "Check the interrupted package operation before making another change."))
    if plugin.get("health") in {"failed", "error", "load_failed"}:
        blockers.append(blocker("package_failed"))
    elif plugin["installed"] and (not plugin.get("setup_complete") or plugin.get("health") != "passed"):
        blockers.append(blocker("package_setup"))
    children = []
    for child in plugin["children"]:
        row = entry(child["kind"], child["owner_ref"], child["name"], parent_id=identity, source="package", app=app,
            id=identity + ":" + child["kind"] + ":" + child["owner_ref"], enabled=plugin["enabled"],
            lifecycle="installed" if plugin["enabled"] else "off", required=child.get("optional") is not True)
        if child["kind"] == "mcp":
            target = {"kind": "plugin", "plugin_id": plugin["plugin_id"], "server_key": child["server_key"]}
            row.update(target=target, actions=["configure"])
            try:
                saved = config.read_saved_configuration(target)
                name, cfg = next(iter(saved.document["servers"].items()))
                revision = read_mcp_configuration(validate=validate, target=target).revision or ""
                excluded = get_mcp_child_overrides(plugin["plugin_id"], child["server_key"]).get("enabled") is False
                row.update(revision=revision, enabled=plugin["enabled"] and cfg.get("enabled") is True,
                    _mcp={"name": name, "cfg": cfg, "active": plugin["enabled"], "excluded": excluded})
            except (OSError, ValueError, KeyError, TypeError, StopIteration):
                row.update(blockers=[blocker("included_attention", "This included connection is unavailable. Review its setup.")])
        children.append(row)
    actions = [a for a, c in plugin["capabilities"].items() if c.get("available")]
    actions += ["restore"] if plugin.get("recoverable") else []
    actions += ["recover"] if plugin.get("publication_pending") else []
    return entry("plugin", plugin["plugin_id"], plugin["name"], description=plugin["description"][:2048],
        source="portable" if plugin["package_format"] != "row-bot-v2" else "native", publisher=plugin.get("publisher", "")[:160],
        license=plugin.get("license", "")[:256], source_url=public_url(plugin.get("source_url")), version=plugin["version"][:128],
        pin=plugin.get("pin", ""), enabled=plugin["enabled"], installed=plugin["installed"], revision=plugin.get("manifest_revision") or "",
        compatibility="partial" if plugin.get("diagnostics") else "supported", children=children, actions=actions, app=app,
        canonical_identity="plugin:" + str(plugin.get("source_identity", "")) + "@" + str(plugin.get("pin", "")),
        lifecycle="data_retained" if plugin.get("retained") else "installed" if plugin["enabled"] else "off", blockers=blockers)


def _index(validate: Callable[[], None]) -> tuple[dict, list[dict]]:
    key = _fingerprint()
    with _LOCK:
        if _INDEX.get("key") == key:
            return _INDEX["rows"], _INDEX["errors"]
    rows, errors = _static(validate)
    indexed = {row["id"]: row for row in rows}
    with _LOCK:
        _INDEX.update(key=key, rows=indexed, errors=errors)
    return indexed, errors


def _pending_targets(row: dict) -> list[str]:
    from row_bot.mcp_client import targets
    if row["kind"] == "skill":
        return ["settings:skill:" + row["owner_ref"]]
    if row["kind"] == "plugin":
        return ["settings:plugin:lifecycle:" + row["owner_ref"]]
    return [targets.admission_target(targets.normalize(row["target"])), "settings:mcp-runtime:" + row["owner_ref"]]


def reconcile_command(owner_id: str, command_id: str, kind: str, validate: Callable[[], None], *,
                      explicit: bool = False) -> dict:
    """Observe one unfinished owner command and settle it when its outcome is proven.
    Only an ``explicit`` check also settles a skill command whose outcome stays unknown."""
    from row_bot.runtime import admissions
    if kind == "plugin":
        from row_bot.application.client_plugin_lifecycle import reconcile_plugin_operation
        return reconcile_plugin_operation(owner_id=owner_id, command_id=command_id, validate=validate)
    if kind == "skill":
        from row_bot.application.client_skill_hub import reconcile_skill_hub_operation
        return reconcile_skill_hub_operation(owner_id=owner_id, command_id=command_id, validate=validate,
                                             proven_only=not explicit)
    metadata = admissions.read_command_metadata(owner_id, command_id) or {}
    if metadata.get("type") == "mcp.runtime.control":
        from row_bot.application.capability_runtime_controls import reconcile_mcp_runtime_operation
        return reconcile_mcp_runtime_operation(owner_id=owner_id, command_id=command_id, validate=validate)
    if str(metadata.get("type", "")).startswith("mcp.auth."):
        from row_bot.application.client_mcp_auth import _settle_recovered, auth_status
        result = auth_status(owner_id=owner_id, command_id=command_id, validate=validate)
        _settle_recovered(owner_id, command_id, result)
        return {"command_id": command_id, "settled": result["state"] in {"signed_in", "disconnected", "cancelled"},
                "message": result["message"]}
    if str(metadata.get("type", "")).startswith("integrations.plan"):
        from row_bot.integrations import plans
        plan = plans.read_plan(plans.Context(owner_id, owner_id, validate), command_id)
        return {"command_id": command_id, "settled": plan["state"] not in {"running", "uncertain"}, "message": ""}
    from row_bot.application.capability_configuration_controls import reconcile_mcp_configuration_operation
    return reconcile_mcp_configuration_operation(owner_id=owner_id, command_id=command_id, validate=validate)


def command_kind(command_type: str) -> str:
    """Which owner settles an admitted command."""
    return "plugin" if command_type.startswith("plugin.lifecycle.") else "skill" if command_type.startswith("skill.hub.") else "mcp"


def settle(row: dict, owner_id: str, mcp_owner_id: str, validate: Callable[[], None]) -> None:
    """Check one item's unfinished changes again because the person asked. Each settles only when its
    owner proves how it ended; nothing is sent again. Only the caller's own commands (and this
    instance's connection changes) are checked."""
    pending = _unfinished()
    for target in _pending_targets(row):
        for command in pending.get(target, []):
            kind = command_kind(command["type"])
            if command["owner_id"] != owner_id and not (kind == "mcp" and command["owner_id"] == mcp_owner_id):
                continue
            try:
                reconcile_command(command["owner_id"], command["command_id"], kind, validate, explicit=True)
            except Exception as error:  # Still running: it settles by itself.
                if getattr(error, "code", str(error)) not in {"operation_pending", "skill_install_pending"}:
                    raise
    invalidate()


def _unfinished() -> dict:
    """Unfinished owner commands by admission target, read once per listing."""
    from row_bot.runtime import admissions
    try:
        pending = admissions.read_unfinished_commands(prefixes=("settings:mcp", "settings:skill:", "settings:plugin:lifecycle:"))
    except admissions.AdmissionError:
        return {"overflow": True}
    by_target: dict = {"overflow": pending["overflow"], "settled": {}}
    for command in pending["items"]:
        by_target.setdefault(command["target"], []).append(command)
    return by_target


def _reconcile(row: dict, validate: Callable[[], None], pending: dict) -> list[dict]:
    """Unfinished changes on this item: settled when proven, otherwise one blocker."""
    if pending.get("overflow"):
        return [blocker("change_unconfirmed")]
    found = []
    for target in _pending_targets(row):
        for command in pending.get(target, []):
            kind = command_kind(command["type"])
            if kind == "plugin":
                # Settling package operations stays with the local owner's explicit recovery.
                found.append(blocker("change_unconfirmed"))
                continue
            settled = pending["settled"].get(command["command_id"])
            if command["command_id"] not in pending["settled"]:
                try:
                    settled = reconcile_command(command["owner_id"], command["command_id"], kind, validate)["settled"]
                except Exception as error:  # An owner still running reports itself busy.
                    settled = None if getattr(error, "code", str(error)) in {"operation_pending", "skill_install_pending"} else False
                pending["settled"][command["command_id"]] = settled
            if settled is None:
                found.append(blocker("change_in_progress"))
            elif not settled:
                found.append(blocker("configuration_recovery" if target == "settings:mcp" else "change_unconfirmed"))
    return found[:1]


def _live(row: dict, statuses: dict, validate: Callable[[], None], pending: dict) -> dict:
    """Apply live runtime state, requirements and unfinished changes to static facts."""
    row = copy.deepcopy(row)
    private = row.pop("_mcp", None)
    if private is not None:
        cfg, excluded = private["cfg"], private.get("excluded", False)
        runtime = statuses.get(private["name"], {})
        server = {"requirements": _requirements(cfg), "runtime_status": runtime.get("status")}
        setup = mcp_setup(server, cfg)
        enabled = cfg.get("enabled") is True and private["active"] and not excluded
        # A connection someone deliberately switched off is off, not unfinished.
        row["blockers"] = row["blockers"] + ([] if excluded else mcp_blockers(setup, runtime, enabled=enabled))
        row.update(setup=setup, account_label=str((cfg.get("auth") or {}).get("label", ""))[:128],
                   lifecycle="installed" if enabled else "off", server=private["name"])  # Where its chat tools come from.
    if row["parent_id"] is None:
        codes = {b["code"] for b in row["blockers"]}
        row["blockers"] = [b for b in _reconcile(row, validate, pending) if b["code"] not in codes] + row["blockers"]
    row["children"] = [_live(child, statuses, validate, pending) for child in row["children"]]
    for child in row["children"]:
        reasons = [b["message"] for b in child["blockers"] if b["message"]][:16]
        clear = not any(b["severity"] == "blocking" for b in child["blockers"])
        # A switched-off package's included items are off with it, not broken.
        if clear and (child["lifecycle"] == "installed" or (row["lifecycle"] == "off" and child["lifecycle"] == "off"
                                                             and (child["kind"] == "skill" or not reasons))):
            continue
        required = child.get("required", True)
        message = child["name"] + (": " if required else " (optional): ") + (" ".join(reasons) or "Turn on this included capability.")
        code = "included" if not required else "included_attention" if child["readiness"] == "attention" else "included_needs_setup"
        row["blockers"].append(blocker(code, message, subject=child["name"]))
    return finish(row)


def _statuses(rows: list[dict]) -> dict:
    import sys
    runtime = sys.modules.get("row_bot.mcp_client.runtime")
    names = [r["_mcp"]["name"] for row in rows for r in [row, *row["children"]] if r.get("_mcp")]
    found = {}
    for start in range(0, len(names) if runtime is not None else 0, 50):
        found.update(runtime.get_passive_server_statuses(tuple(names[start:start + 50])))
    return found


def inventory(validate: Callable[[], None] = lambda: None) -> tuple[list[dict], list[dict]]:
    """Every installed item with status v2; static facts come from the index."""
    validate()
    indexed, errors = _index(validate)
    rows = list(indexed.values())
    statuses, pending = _statuses(rows), _unfinished()
    result = [_live(row, statuses, validate, pending) for row in rows]
    validate()
    return result, copy.deepcopy(errors)


def read(item_id: str, validate: Callable[[], None] = lambda: None) -> dict | None:
    """One installed item (or an included child) by id, without rebuilding others."""
    validate()
    indexed, _ = _index(validate)
    parent = indexed.get(item_id) or next((row for row in indexed.values()
        if item_id.startswith(row["id"] + ":") and any(c["id"] == item_id for c in row["children"])), None)
    if parent is None:
        return None
    row = _live(parent, _statuses([parent]), validate, _unfinished())
    validate()
    return row if row["id"] == item_id else next(c for c in row["children"] if c["id"] == item_id)


def invalidate() -> None:
    """Forget the index; the next read rebuilds it from the owners."""
    with _LOCK:
        _INDEX.clear()
