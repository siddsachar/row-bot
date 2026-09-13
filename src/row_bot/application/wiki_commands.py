"""Reviewed wiki controls over canonical config, manifest, graph and admissions.

The server supplies an authorized vault scope. Paths and publication proofs stay
private; opening and checking saved articles never drain graph projections.
"""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from row_bot import knowledge_views, wiki_vault as wiki
from row_bot.application import knowledge_commands as common
from row_bot.data_paths import get_memory_db_path
from row_bot.file_ownership import directory_identity
from row_bot.runtime import admissions

_ACTIONS = {"wiki.configure", "wiki.publish", "wiki.rebuild", "wiki.import", "wiki.sync"}
_FILE_BYTES = 2 * 1024 * 1024
_REVIEW_BYTES = 192 * 1024


@dataclass(frozen=True)
class WikiScope:
    """Server-only folder grant/resource binding; never a renderer path input."""

    id: str
    vault: Path
    identity: str


def _guard(scope: WikiScope) -> None:
    if not isinstance(scope.id, str) or not 1 <= len(scope.id) <= 256 or not scope.vault.is_absolute():
        raise common._error("wiki_scope_unavailable")
    try:
        for path in (*reversed(scope.vault.parents), scope.vault):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError
        if directory_identity(scope.vault, parent=True) != scope.identity:
            raise ValueError
    except (OSError, ValueError):
        raise common._error("wiki_scope_unavailable") from None


def _config(scope: WikiScope, *, selecting: bool = False) -> tuple[dict, str]:
    _guard(scope)
    try:
        cfg, revision = wiki.read_control_config()
        if not selecting and Path(cfg["vault_path"]).absolute() != scope.vault:
            raise ValueError
        return cfg, revision
    except (OSError, ValueError):
        raise common._error("wiki_unavailable") from None


def _bytes(path: Path) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise common._error("wiki_unavailable")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        value = handle.read(_FILE_BYTES + 1)
        after = os.fstat(handle.fileno())
    final = path.lstat()
    identities = [(item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) for item in (info, opened, after, final)]
    if len(value) > _FILE_BYTES or len(set(identities)) != 1:
        raise common._error("wiki_unavailable")
    return value


def _inventory(scope: WikiScope) -> tuple[list[dict], str]:
    """Complete bounded enumeration; failures never become an empty sync list."""
    _config(scope)
    root = scope.vault / "wiki"
    rows: list[dict] = []
    if not root.exists():
        return rows, common._digest([])
    manifest = wiki._read_manifest()
    deadline, used = time.monotonic() + 2, 0
    pending = [root]
    while pending:
        directory = pending.pop()
        wiki._wiki_path(directory.relative_to(root).as_posix() + "/_probe")
        with os.scandir(directory) as entries:
            for item in entries:
                if time.monotonic() > deadline or len(rows) >= 100_000:
                    raise common._error("wiki_enumeration_incomplete")
                if item.name.startswith(".row-bot-"):
                    continue
                path = Path(item.path)
                if item.is_symlink() or getattr(item.stat(follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                    raise common._error("wiki_enumeration_incomplete")
                if item.is_dir(follow_symlinks=False):
                    if len(path.relative_to(root).parts) > 32:
                        raise common._error("wiki_enumeration_incomplete")
                    pending.append(path)
                elif path.suffix.lower() == ".md":
                    relative = path.relative_to(root).as_posix()
                    content = _bytes(wiki._wiki_path(relative))
                    used += len(content)
                    if used > 64 * 1024 * 1024:
                        raise common._error("wiki_enumeration_incomplete")
                    baseline = manifest["files"].get(relative)
                    parsed = wiki._parse_entity_text(content.decode("utf-8"))
                    entity_id = str(parsed["id"]) if parsed else None
                    saved = common._entity(entity_id) if entity_id else None
                    digest = hashlib.sha256(content).hexdigest()
                    status = "unmanaged"
                    if baseline and baseline.get("entity_id"):
                        if entity_id != baseline["entity_id"]:
                            status = "conflict"
                        elif digest == baseline["hash"]:
                            status = "unchanged"
                        elif saved and wiki._source_revision(saved) == baseline.get("source_revision"):
                            status = "edited"
                        else:
                            status = "conflict"
                    elif entity_id and saved:
                        status = "legacy_review"
                    rows.append({"relative": relative, "entity_id": entity_id,
                        "title": str((parsed or {}).get("subject", path.stem))[:256], "status": status,
                        "vault_hash": digest, "db_revision": wiki._source_revision(saved) if saved else None})
    # Missing managed files must remain visible even though scandir cannot find them.
    present = {row["relative"] for row in rows}
    for relative, entry in manifest["files"].items():
        if relative not in present and entry.get("entity_id"):
            rows.append({"relative": relative, "entity_id": entry["entity_id"], "title": str(entry.get("subject", ""))[:256],
                         "status": "missing", "vault_hash": None, "db_revision": None})
    rows.sort(key=lambda row: row["relative"])
    return rows, common._digest({"files": rows, "manifest": manifest})


def _article_id(scope: WikiScope, relative: str) -> str:
    return admissions.keyed_digest({"wiki_scope": scope.id, "relative": relative}, read_only=True)


def read_wiki_status(*, scope: WikiScope | None, validate: Callable[[], None]) -> dict:
    validate()
    try:
        cfg, revision = wiki.read_control_config()
        result = {"schema_version": 1, "revision": revision, "enabled": cfg["enabled"],
                  "availability": "scope_required", "scope_id": None, "articles": None,
                  "edited": None, "conflicts": None}
        if scope is not None:
            _guard(scope)
            result.update(availability="available", scope_id=scope.id)
            # A newly selected folder is a valid configure target even when it
            # is not the currently configured vault. Only inspect managed wiki
            # content after the canonical configuration points at this scope.
            if Path(cfg["vault_path"]).absolute() == scope.vault:
                rows, _ = _inventory(scope)
                result.update(articles=len(rows), edited=sum(row["status"] == "edited" for row in rows),
                              conflicts=sum(row["status"] in {"conflict", "legacy_review", "missing"} for row in rows))
    except (OSError, ValueError, common.ClientPlatformError):
        result = {"schema_version": 1, "revision": common._digest("unavailable"), "enabled": False,
                  "availability": "unavailable", "scope_id": None, "articles": None, "edited": None, "conflicts": None}
    validate()
    return result


def read_wiki_articles(*, scope: WikiScope, cursor: str | None = None, limit: int = 50,
                       validate: Callable[[], None]) -> dict:
    validate()
    if type(limit) is not int or not 1 <= limit <= 50:
        raise common._error("invalid_wiki_query")
    rows, revision = _inventory(scope)
    offset = 0
    if cursor:
        try:
            saved_revision, number = cursor.split(":")
            offset = int(number)
            if saved_revision != revision or not 0 < offset < len(rows):
                raise ValueError
        except (ValueError, AttributeError):
            raise common._error("cursor_expired") from None
    items = [{"article_id": _article_id(scope, row["relative"]),
              **{key: row[key] for key in ("entity_id", "title", "status", "vault_hash", "db_revision")}}
             for row in rows[offset:offset + limit]]
    validate()
    return {"schema_version": 1, "revision": revision, "scope_id": scope.id, "items": items, "total": len(rows),
            "next_cursor": f"{revision}:{offset + limit}" if offset + limit < len(rows) else None}


def _article(scope: WikiScope, article_id: str) -> dict:
    rows, _ = _inventory(scope)
    for row in rows:
        if _article_id(scope, row["relative"]) == article_id:
            return row
    raise common._error("wiki_article_unavailable")


def read_wiki_article(*, scope: WikiScope, article_id: str, validate: Callable[[], None]) -> dict:
    validate()
    row = _article(scope, article_id)
    if row["status"] == "missing":
        raise common._error("wiki_article_unavailable")
    text = _bytes(wiki._wiki_path(row["relative"])).decode("utf-8")
    if hashlib.sha256(text.encode()).hexdigest() != row["vault_hash"]:
        raise common._error("wiki_changed")
    result = {"article_id": article_id, "entity_id": row["entity_id"], "title": row["title"],
              "vault_text": text, "vault_hash": row["vault_hash"], "database_text": None, "db_revision": row["db_revision"]}
    if row["entity_id"]:
        entity = common._entity(row["entity_id"])
        if entity is not None:
            if wiki._source_revision(entity) != row["db_revision"]:
                raise common._error("wiki_changed")
            # Render only fields imported by the existing parser, excluding runtime provenance/paths.
            result["database_text"] = json.dumps({key: entity[key] for key in
                ("id", "entity_type", "subject", "description", "aliases", "tags", "properties", "source")},
                ensure_ascii=False, indent=2)
    if len(json.dumps(result, ensure_ascii=True).encode()) > _REVIEW_BYTES:
        raise common._error("wiki_review_too_large")
    validate()
    return result


def _graph_revision() -> str:
    revisions = []
    for table, columns in (("entities", "id description properties"), ("relations", "id")):
        def build(row: object) -> common._Row:
            value = dict(row)
            value.pop("matched")
            return common._Row(common._digest(value))
        page = knowledge_views._read(get_memory_db_path(create_parent=False), {table: columns},
            f"SELECT *,1 matched FROM {table} ORDER BY id", (), build, common._Page, "wiki-source", 0, None, 1)
        if page.availability != "available":
            raise common._error("wiki_source_unavailable")
        revisions.append(page.revision)
    return common._digest(revisions)


def review_wiki_command(action: str, payload: dict, *, scope: WikiScope,
                        validate: Callable[[], None]) -> dict:
    validate()
    if action not in _ACTIONS or type(payload) is not dict:
        raise common._error("invalid_wiki_command")
    expected = {"revision", "enabled"} if action == "wiki.configure" else {"revision", "entity_id"} if action == "wiki.publish" else {"revision", "article_ids"} if action in {"wiki.import", "wiki.sync"} else {"revision"}
    if payload.keys() != expected or not isinstance(payload.get("revision"), str):
        raise common._error("invalid_wiki_command")
    cfg, revision = _config(scope, selecting=action == "wiki.configure")
    if payload["revision"] != revision:
        raise common._error("wiki_changed")
    review = {"schema_version": 1, "action": action, "scope_id": scope.id, "revision": revision,
              "source_revision": None, "vault_revision": None, "articles": [], "enabled": None}
    if action == "wiki.configure":
        if type(payload["enabled"]) is not bool:
            raise common._error("invalid_wiki_command")
        review["enabled"] = payload["enabled"]
    else:
        if not cfg["enabled"]:
            raise common._error("wiki_disabled")
        rows, review["vault_revision"] = _inventory(scope)
        if action in {"wiki.publish", "wiki.rebuild"}:
            review["source_revision"] = _graph_revision()
            if action == "wiki.publish":
                entity = common._entity(payload["entity_id"])
                if entity is None:
                    raise common._error("wiki_source_unavailable")
                review["entity_id"] = entity["id"]
                review["entity_revision"] = wiki._source_revision(entity)
        else:
            ids = payload["article_ids"]
            if (type(ids) is not list or not 1 <= len(ids) <= (1 if action == "wiki.import" else 50)
                    or any(type(value) is not str or len(value) != 64 for value in ids) or len(set(ids)) != len(ids)):
                raise common._error("invalid_wiki_command")
            for identifier in ids:
                row = next((row for row in rows if _article_id(scope, row["relative"]) == identifier), None)
                if row is None or row["entity_id"] is None or row["status"] in {"missing", "unchanged", "unmanaged"}:
                    raise common._error("wiki_article_unavailable")
                if action == "wiki.sync" and row["status"] != "edited":
                    raise common._error("wiki_conflict_review_required")
                article = read_wiki_article(scope=scope, article_id=identifier, validate=validate)
                review["articles"].append(article)
    review["action_digest"] = admissions.keyed_digest({"review": review, "payload": payload, "identity": scope.identity})
    if len(json.dumps(review, ensure_ascii=True).encode()) > _REVIEW_BYTES:
        raise common._error("wiki_review_too_large")
    validate()
    return review


def _receipt_scope(owner_id: str, authority_id: str, scope: WikiScope, *, read_only: bool = False) -> str:
    return admissions.keyed_digest({"wiki_owner": owner_id, "authority": authority_id,
                                   "scope": scope.id, "identity": scope.identity}, read_only=read_only)


def read_wiki_receipt(*, owner_id: str, authority_id: str, command_id: str, scope: WikiScope,
                      validate: Callable[[], None]) -> dict:
    validate()
    _guard(scope)
    metadata = admissions.read_command_metadata(owner_id, common._uuid(command_id))
    saved = admissions.read_command_receipt(owner_id, command_id)
    if (not metadata or metadata["target"] != "wiki" or metadata["type"] not in _ACTIONS or not saved
            or saved.get("_wiki", {}).get("scope") != _receipt_scope(owner_id, authority_id, scope, read_only=True)):
        raise common._error("wiki_operation_unavailable")
    if saved.get("status") != "completed":
        cfg, _ = wiki.read_control_config()
        if metadata["type"] == "wiki.configure" and cfg.get("wiki_command") == {
                "owner_id": owner_id, "key": metadata["key"], "command_id": command_id}:
            saved = {**saved, "status": "completed", "code": None}
        elif Path(cfg["vault_path"]).absolute() == scope.vault:
            publication = wiki._read_manifest().get("wiki_command", {})
            candidate = publication.get("result", {})
            if (publication.get("owner_id") == owner_id and publication.get("key") == metadata["key"]
                    and candidate.get("command_id") == command_id and candidate.get("_wiki") == saved["_wiki"]
                    and candidate.get("action") == metadata["type"] and candidate.get("status") == "completed"):
                saved = candidate
    if (saved.get("status") not in {"completed", "partial"} or saved.get("action") != metadata["type"]
            or any(type(saved.get(name)) is not int or not 0 <= saved[name] <= 100_000 for name in ("count", "conflicts"))
            or saved.get("code") not in {None, "wiki_conflict", "wiki_outcome_uncertain"}):
        raise common._error("wiki_operation_unavailable")
    result = {key: saved[key] for key in ("command_id", "status", "action", "count", "conflicts", "code")}
    validate()
    return result


def execute_wiki_command(command: dict, *, owner_id: str, authority_id: str, key: str, scope: WikiScope,
                         validate: Callable[[], None], validate_action: Callable[[str], None],
                         validate_review: Callable[[dict, dict], None],
                         cancelled: Callable[[], bool] | None = None) -> dict:
    command = deepcopy(command)
    validate()
    command_id = common._uuid(command.get("command_id"))
    action, raw = command.get("type"), command.get("payload")
    if action not in _ACTIONS or type(raw) is not dict or "review_id" not in raw:
        raise common._error("invalid_wiki_command")
    payload = {name: value for name, value in raw.items() if name != "review_id"}
    def receipt() -> dict:
        return read_wiki_receipt(owner_id=owner_id, authority_id=authority_id, command_id=command_id,
                                 scope=scope, validate=validate)
    if admissions.read_command_metadata(owner_id, command_id):
        try:
            admissions.claim_command(owner_id, key, command, "wiki")
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise common._error(str(error)) from None
        return receipt()
    review = review_wiki_command(action, payload, scope=scope, validate=validate)
    error: Exception | None = None
    def authority() -> None:
        nonlocal error
        try:
            validate()
            validate_action(action)
            validate_review(command, review)
            _guard(scope)
            if wiki.read_control_config()[1] != review["revision"]:
                raise common._error("wiki_changed")
            if cancelled is not None and cancelled():
                raise common._error("wiki_cancelled")
        except Exception as caught:
            error = caught
            raise
    authority()
    proof = {"scope": _receipt_scope(owner_id, authority_id, scope)}
    result = {"command_id": command_id, "status": "partial", "action": action, "count": 0,
              "conflicts": 0, "code": "wiki_outcome_uncertain", "_wiki": proof}
    try:
        prior = admissions.claim_command(owner_id, key, command, "wiki", initial_result=result)
    except admissions.AdmissionError as caught:
        if str(caught) != "operation_uncertain":
            raise common._error(str(caught)) from None
        return receipt()
    if prior is not None:
        return receipt()
    from row_bot.providers.config import provider_config_transaction
    try:
        # Shared config admission prevents legacy/new clients changing the vault mid-effect.
        with wiki._export_lock, provider_config_transaction(wiki._CONFIG_PATH):
            authority()
            current = review_wiki_command(action, payload, scope=scope, validate=validate)
            if current != review:
                raise common._error("wiki_changed")
            if action == "wiki.configure":
                wiki.configure_vault(enabled=payload["enabled"], vault_path=scope.vault,
                    expected_revision=review["revision"], validate=authority,
                    proof={"owner_id": owner_id, "key": key, "command_id": command_id})
            elif action == "wiki.rebuild":
                from row_bot import knowledge_graph as kg
                conn = kg._get_conn()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    authority()
                    if _graph_revision() != review["source_revision"]:
                        raise common._error("wiki_changed")
                    outcome = wiki.rebuild_vault(validate=authority)
                finally:
                    conn.close()
                result["count"] = outcome.get("exported", 0)
                result["conflicts"] = len(outcome.get("conflicts", []))
                if not outcome.get("complete"):
                    result["code"] = "wiki_conflict"
            else:
                from row_bot import knowledge_graph as kg
                with kg.projection_batch(drain_on_exit=False):
                    if action == "wiki.publish":
                        entity = common._entity(payload["entity_id"])
                        outcome = wiki.export_entities_projection([entity], validate=authority)[0]
                        result["count"] = int(outcome.complete)
                        result["conflicts"] = int(not outcome.complete)
                    else:
                        for article in review["articles"]:
                            authority()
                            row = _article(scope, article["article_id"])
                            ok = wiki.import_from_vault(row["entity_id"], wiki._wiki_path(row["relative"]),
                                expected_db_revision=article["db_revision"], expected_vault_hash=article["vault_hash"],
                                validate=authority)
                            result["count"] += int(ok)
                            result["conflicts"] += int(not ok)
            result.update(status="completed", code="wiki_conflict" if result["conflicts"] else None)
            if action != "wiki.configure":
                authority()
                manifest = wiki._read_manifest()
                manifest["wiki_command"] = {"owner_id": owner_id, "key": key, "result": result}
                wiki._write_manifest(manifest, validate=authority)
        admissions.complete_command(owner_id, key, result)
    except Exception:
        if error is not None:
            raise error
        return receipt()
    return receipt()
