"""Keys that Row-Bot 5.0.0 users typed into a server's headers or variables move into the system keychain.

5.0.0 saved them in ``mcp_servers.json`` as typed. At start, before any server launches, each value whose
name reads like a credential (``Authorization``, ``GITHUB_TOKEN``) moves into the keychain behind the
server's ``api_key`` binding, and the server launches with exactly the header or variable it had. A
keychain copy is read back before its plaintext is removed; if the keychain can't keep it, that server
keeps its plaintext and the next start tries again. The keychain names a move will use are recorded
before anything is written, so a move a crash interrupted is settled at the next start from what the
settings file holds: a publication that began is finished, and copies of one that never landed are deleted.
"""
from __future__ import annotations

import contextlib
import copy
import logging
import uuid
from dataclasses import asdict

from row_bot.developer.edits import FileEditRecovery
from row_bot.integrations import inputs
from row_bot.mcp_client import auth, config
from row_bot.runtime import admissions

logger = logging.getLogger(__name__)
_OWNER, _TYPE, _TARGET = "mcp:secret-migration", "mcp.configuration.secret_migration", "settings:mcp"


def _plaintext(name: str, cfg: dict) -> tuple[str, dict[str, str]]:
    """The binding kind and the credential values a 5.0.0 server keeps in plaintext ("", {} when none)."""
    source = cfg.get("source")
    if cfg.get("auth") or cfg.get("inputs") or (type(source) is dict and source.get("kind") == "plugin"):
        return "", {}
    stdio = config.normalize_server_config(name, cfg)["transport"] == "stdio"
    kind, field = ("env", "env") if stdio else ("header", "headers")
    values = cfg.get(field) if type(cfg.get(field)) is dict else {}
    found = {key: value for key, value in values.items() if inputs.secretish(key)
             and type(value) is str and value and len(value) <= 16384 and "\r" not in value and "\n" not in value}
    unique = len({inputs.key_of(key) for key in found}) == len(found)
    return (kind, found) if found and len(found) <= 16 and unique else ("", {})


def _forget(refs: list[str]) -> None:
    for ref in refs:
        try:
            auth.delete_credentials(ref)
        except Exception:
            logger.warning("A keychain copy from an unfinished key move couldn't be deleted yet")


def _published(command_id: str) -> bool | None:
    """Whether the settings file holds this move (None: the file is missing, so nothing is guessed)."""
    current = config.read_saved_configuration()
    if not current.exists:
        return None
    return (current.document.get("_client_publication") or {}).get("command_id") == command_id


def _settle() -> None:
    """A move a crash interrupted: finish a publication that began; delete the copies of one that didn't land."""
    for row in admissions.read_unfinished_target_commands(_TARGET)["items"]:
        if row["owner_id"] != _OWNER:
            continue
        move = (admissions.read_command_receipt(_OWNER, row["command_id"]) or {}).get("_migration") or {}
        if move.get("publication"):
            proof = FileEditRecovery(**move["publication"])
            with contextlib.suppress(Exception):  # A conflict leaves the file as it is; what it holds decides.
                config.publish_saved_configuration(move["document"], expected_digest=proof.before_digest,
                    command_id=row["command_id"], persist_recovery=lambda _proof: None, recovery=proof)
        published = _published(row["command_id"])
        if published:
            admissions.complete_command(_OWNER, row["key"], {"command_id": row["command_id"], "status": "completed"})
        elif published is False:
            _forget(move.get("refs", []))
            admissions.reject_command(_OWNER, row["key"], "mcp_secret_migration_interrupted")


def migrate() -> dict[str, int]:
    """Move saved servers' plaintext keys into the keychain: how many servers moved, and how many kept them."""
    with config.configuration_transaction():
        _settle()
        if config.configuration_recovery_required():
            return {"migrated": 0, "kept": 0}  # Another change awaits recovery: never publish over it.
        saved = config.read_saved_configuration()
        document = copy.deepcopy(saved.document)
        servers = document.get("servers") if type(document.get("servers")) is dict else {}
        found = {name: _plaintext(name, cfg) for name, cfg in servers.items() if type(cfg) is dict}
        found = {name: value for name, value in found.items() if value[1]}
        if not found:
            return {"migrated": 0, "kept": 0}
        command_id, refs = str(uuid.uuid4()), {name: uuid.uuid4().hex for name in found}
        admissions.claim_command(_OWNER, command_id, {"command_id": command_id, "type": _TYPE,
            "expected_revision": saved.digest, "intent_digest": admissions.keyed_digest(sorted(refs.values()))}, _TARGET)
        progress = {"command_id": command_id, "status": "admitting", "_migration": {"refs": list(refs.values())}}
        admissions.command_progress(_OWNER, command_id, progress)
        moved = []
        for name, (kind, values) in found.items():
            cfg = servers[name]
            data = {"binding": auth.binding(name, config.normalize_server_config(name, cfg)),
                    "values": {inputs.key_of(key): value for key, value in values.items()}}
            try:
                auth.write_credentials(refs[name], data)
                if auth.read_credentials(refs[name]) != data:
                    raise ValueError("the keychain copy reads back differently")
            except Exception:
                _forget([refs[name]])
                continue
            field = "env" if kind == "env" else "headers"
            cfg[field] = {key: value for key, value in cfg[field].items() if key not in values}
            cfg["auth"] = auth.validate_metadata({"mode": "api_key", "credential_ref": refs[name],
                "binding": data["binding"],
                "bindings": [{"kind": kind, "name": key, "key": inputs.key_of(key), "prefix": ""} for key in values]})
            moved.append(name)
        if not moved:
            admissions.reject_command(_OWNER, command_id, "mcp_keychain_unavailable")
            return {"migrated": 0, "kept": len(found)}
        document["_client_publication"] = {"owner_id": _OWNER, "key": command_id, "command_id": command_id}
        progress["_migration"]["document"] = document

        def checkpoint(proof: FileEditRecovery) -> None:
            progress["_migration"]["publication"] = asdict(proof)
            admissions.command_progress(_OWNER, command_id, progress)

        try:
            config.publish_saved_configuration(document, expected_digest=saved.digest, command_id=command_id,
                                               persist_recovery=checkpoint)
        except Exception:
            if "publication" not in progress["_migration"]:  # Nothing was published: the plaintext stays in use.
                _forget(list(refs.values()))
                admissions.reject_command(_OWNER, command_id, "mcp_secret_migration_failed")
                raise
            _settle()  # It began: finish it now, or settle it from what the file holds.
            if not _published(command_id):
                raise
        else:
            admissions.complete_command(_OWNER, command_id, {"command_id": command_id, "status": "completed"})
    return {"migrated": len(moved), "kept": len(found) - len(moved)}
