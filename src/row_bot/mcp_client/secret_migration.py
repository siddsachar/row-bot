"""Keys that Row-Bot 5.0.0 users typed into a server's headers or variables move into the system keychain.

5.0.0 saved them in ``mcp_servers.json`` as typed. At start, before any server launches, each value whose
name reads like a credential (``Authorization``, ``GITHUB_TOKEN``) moves into the keychain behind the
server's ``api_key`` binding, and the server launches with exactly the header or variable it had. A
keychain copy is read back before its plaintext is removed; if the keychain can't keep it, that server
keeps its plaintext and the next start tries again. Every start deletes the copies of the settings file that
saves keep for recovery once no unfinished change needs them, so no old copy keeps a key.

Only the keychain names a move will use, and later its publication proof, are recorded (never a value or
the settings document), before anything is written. A move a crash interrupted is settled at the next
start from what the settings file holds: a publication that began is finished, and the copies of one
that never landed are deleted. No outcome leaves MCP settings waiting for a recovery.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import asdict

from row_bot.developer.edits import FileEditRecovery
from row_bot.integrations import inputs
from row_bot.mcp_client import auth, config
from row_bot.runtime import admissions

logger = logging.getLogger(__name__)
_OWNER, _TYPE, _TARGET = "mcp:secret-migration", "mcp.configuration.secret_migration", "settings:mcp"
_BINDABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}")  # A name a binding can hold (auth.validate_metadata).
_NOT_A_KEY = re.compile(r"\d+|true|false|yes|no", re.IGNORECASE)  # MAX_TOKENS=4096 is a setting, not a key.


def _plaintext(name: str, cfg: dict) -> tuple[str, dict[str, str]]:
    """The binding kind and the credential values a 5.0.0 server keeps in plaintext ("", {} when none)."""
    source = cfg.get("source")
    if cfg.get("auth") or cfg.get("inputs") or (type(source) is dict and source.get("kind") == "plugin"):
        return "", {}
    stdio = config.normalize_server_config(name, cfg)["transport"] == "stdio"
    kind, field = ("env", "env") if stdio else ("header", "headers")
    values = cfg.get(field) if type(cfg.get(field)) is dict else {}
    found = {key: value for key, value in values.items()
             if inputs.secretish(key) and _BINDABLE.fullmatch(key) and type(value) is str and value
             and not _NOT_A_KEY.fullmatch(value) and len(value) <= 16384 and "\r" not in value and "\n" not in value}
    unique = len({inputs.key_of(key) for key in found}) == len(found)
    return (kind, found) if found and len(found) <= 16 and unique else ("", {})


def _forget(refs: list[str]) -> None:
    for ref in refs:
        try:
            auth.delete_credentials(ref)
        except Exception:
            logger.warning("A keychain copy from an unfinished key move couldn't be deleted yet")


def _recovery_root():
    """The settings folder's own kept-copies folder, never one a link or junction points elsewhere."""
    root = config.CONFIG_PATH.parent / ".row-bot-edit-recovery"
    try:
        real = root.is_dir() and not root.is_symlink() and not root.is_junction()
        return root if real and root.resolve() == config.CONFIG_PATH.parent.resolve() / root.name else None
    except OSError:
        return None


def _mcp_copy(path) -> bool:
    """A kept copy of the MCP settings file (a JSON object with its servers)."""
    if not path.is_file() or path.is_symlink() or path.stat().st_size > config.SAVED_CONFIG_BYTE_LIMIT:
        return False
    try:
        value = json.loads(path.read_bytes())
    except ValueError:
        return False
    return type(value) is dict and type(value.get("servers")) is dict


def _forget_old_copies(command_id: str = "") -> None:
    """Delete the kept copies of the MCP settings file that no unfinished change needs (``command_id``'s own
    included): saves keep the file they replaced, keys and all. Runs at every start, so one that couldn't be
    deleted is tried again; never fails a move."""
    root = _recovery_root()
    try:
        directories = sorted(root.iterdir()) if root is not None else []
    except OSError:
        directories = []
    for directory in directories:
        try:
            if (directory.is_symlink() or directory.is_junction() or not directory.is_dir()
                    or (directory.name != command_id and admissions.unfinished(directory.name))):
                continue
            if any(_mcp_copy(directory / name) for name in ("previous", "candidate")):
                shutil.rmtree(directory)
        except Exception:
            logger.warning("A kept copy of the MCP settings couldn't be checked or deleted; the next start tries again")


def _published(command_id: str) -> bool | None:
    """Whether the settings file holds this move (None: the file is missing)."""
    current = config.read_saved_configuration()
    if not current.exists:
        return None
    return (current.document.get("_client_publication") or {}).get("command_id") == command_id


def _settle() -> None:
    """A move a crash interrupted: finish a publication that began; delete the copies of one that didn't land."""
    for row in admissions.read_unfinished_target_commands(_TARGET)["items"]:
        if row["owner_id"] != _OWNER:
            continue
        command_id = row["command_id"]
        move = (admissions.read_command_receipt(_OWNER, command_id) or {}).get("_migration") or {}
        refs, proof = move.get("refs", []), move.get("publication")
        if proof:
            kept = config.CONFIG_PATH.parent / ".row-bot-edit-recovery" / command_id
            try:  # A conflict leaves the file as it is; what it holds decides below.
                if not _mcp_copy(kept / "candidate"):
                    raise ValueError("the move's settings copy is missing or unreadable")
                config.publish_saved_configuration(json.loads((kept / "candidate").read_bytes()),
                    expected_digest=proof["before_digest"], command_id=command_id,
                    persist_recovery=lambda _proof: None, recovery=FileEditRecovery(**proof))
            except Exception:
                # Never leave the settings file missing while the file it replaced is kept.
                with contextlib.suppress(OSError):
                    if not config.CONFIG_PATH.exists() and _mcp_copy(kept / "previous"):
                        os.link(kept / "previous", config.CONFIG_PATH)
                        config._config_cache = None
        published = _published(command_id)
        if published:
            _forget_old_copies(command_id)
            admissions.complete_command(_OWNER, row["key"], {"command_id": command_id, "status": "completed"})
            continue
        if published is False or not proof:  # The old settings are in use: these copies are not.
            _forget(refs)
        else:
            logger.warning("A key move found the MCP settings file missing; its kept copy stays for recovery")
        admissions.reject_command(_OWNER, row["key"], "mcp_secret_migration_interrupted")


def _move(servers: dict, name: str, kind: str, values: dict[str, str], ref: str) -> bool:
    """Copy one server's keys to the keychain, read them back, then bind them in place of the plaintext."""
    cfg = servers[name]
    data = {"binding": auth.binding(name, config.normalize_server_config(name, cfg)),
            "values": {inputs.key_of(key): value for key, value in values.items()}}
    try:
        auth.write_credentials(ref, data)
        if auth.read_credentials(ref) != data:
            raise ValueError("the keychain copy reads back differently")
        metadata = auth.validate_metadata({"mode": "api_key", "credential_ref": ref, "binding": data["binding"],
            "bindings": [{"kind": kind, "name": key, "key": inputs.key_of(key), "prefix": ""} for key in values]})
    except Exception:
        _forget([ref])
        return False
    field = "env" if kind == "env" else "headers"
    cfg[field] = {key: value for key, value in cfg[field].items() if key not in values}
    cfg["auth"] = metadata
    return True


def migrate() -> dict[str, int]:
    """Move saved servers' plaintext keys into the keychain: how many servers moved, and how many kept them."""
    with config.configuration_transaction():
        _settle()
        if config.configuration_recovery_required():
            return {"migrated": 0, "kept": 0}  # Another change awaits recovery: never publish over it.
        _forget_old_copies()
        saved = config.read_saved_configuration()
        document = json.loads(json.dumps(saved.document))
        servers = document.get("servers") if type(document.get("servers")) is dict else {}
        found = {name: _plaintext(name, cfg) for name, cfg in servers.items() if type(cfg) is dict}
        found = {name: value for name, value in found.items() if value[1]}
        if not found:
            return {"migrated": 0, "kept": 0}
        command_id, refs = str(uuid.uuid4()), {name: uuid.uuid4().hex for name in found}
        admissions.claim_command(_OWNER, command_id, {"command_id": command_id, "type": _TYPE,
            "expected_revision": saved.digest, "intent_digest": admissions.keyed_digest(sorted(refs.values()))}, _TARGET)
        progress = {"command_id": command_id, "status": "admitting", "_migration": {"refs": list(refs.values())}}
        try:
            admissions.command_progress(_OWNER, command_id, progress)
            moved = [name for name, (kind, values) in found.items() if _move(servers, name, kind, values, refs[name])]
            if not moved:
                raise ValueError("the keychain kept none of them")
            document["_client_publication"] = {"owner_id": _OWNER, "key": command_id, "command_id": command_id}

            def checkpoint(proof: FileEditRecovery) -> None:
                progress["_migration"]["publication"] = asdict(proof)
                admissions.command_progress(_OWNER, command_id, progress)

            config.publish_saved_configuration(document, expected_digest=saved.digest, command_id=command_id,
                                               persist_recovery=checkpoint)
        except Exception as error:
            logger.warning("Moving app keys into the keychain stopped (%s)", type(error).__name__)
            if "publication" in progress["_migration"]:
                _settle()  # It began: finish it now, or settle it from what the file holds.
                if not _published(command_id):
                    raise
                moved = [name for name in found if servers[name].get("auth")]
            else:  # Nothing was published: the plaintext stays in use, and so do the old settings.
                _forget(list(refs.values()))
                admissions.reject_command(_OWNER, command_id, "mcp_secret_migration_failed")
                return {"migrated": 0, "kept": len(found)}
        else:
            _forget_old_copies(command_id)
            admissions.complete_command(_OWNER, command_id, {"command_id": command_id, "status": "completed"})
    return {"migrated": len(moved), "kept": len(found) - len(moved)}
