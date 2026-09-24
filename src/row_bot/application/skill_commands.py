"""Path-free client reads and reviewed commands for the canonical skill library.

The renderer receives stable skill identifiers and bounded text only.  File
locations, identities, retained copies, and publication proofs remain private.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import threading
from uuid import UUID

import yaml

from row_bot import skills
from row_bot.application.client_platform import ClientPlatformError
from row_bot.file_publication import publish_bytes, read_recovery
from row_bot.runtime import admissions

_LOCK = threading.RLock()
_ACTIONS = {
    "skill.preference",
    "skill.create",
    "skill.import",
    "skill.edit",
    "skill.duplicate",
    "skill.delete",
    "skill.proposal.apply",
    "skill.proposal.reject",
}
_NAME = re.compile(r"[a-z][a-z0-9_]{1,63}")
_FIELDS = {
    "display_name",
    "icon",
    "description",
    "instructions",
    "tags",
    "activation",
    "version",
}
_MAX_SKILL_BYTES = 64 * 1024
_MAX_PUBLIC_BYTES = 256 * 1024


def _error(code: str = "skills_unavailable") -> ClientPlatformError:
    return ClientPlatformError(code)


def _uuid(value: object) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
        return value
    except (TypeError, ValueError, AttributeError):
        raise _error("invalid_skill_command") from None


def _digest(value: object) -> str:
    try:
        raw = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):
        raise _error("invalid_skill_command") from None
    return hashlib.sha256(raw.encode()).hexdigest()


def _scope(owner_id: str, authority_id: str, *, read_only: bool = False) -> str:
    if not isinstance(authority_id, str) or not 1 <= len(authority_id) <= 256:
        raise _error("action_denied")
    return admissions.keyed_digest(
        {"skill_owner": owner_id, "authority": authority_id}, read_only=read_only
    )


def _name(value: object) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise _error("invalid_skill_target")
    return value


def _text(value: object, maximum: int, *, required: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or "\0" in value
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise _error("invalid_skill_fields")
    result = value.strip()
    if required and not result:
        raise _error("invalid_skill_fields")
    return result


def _string_list(value: object, *, count: int, length: int) -> list[str]:
    if not isinstance(value, list) or len(value) > count:
        raise _error("invalid_skill_fields")
    result = [_text(item, length, required=True) for item in value]
    if len(set(result)) != len(result):
        raise _error("invalid_skill_fields")
    return result


def _activation(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict) or not set(value).issubset(
        {"phrases", "keywords", "negative_phrases", "examples"}
    ):
        raise _error("invalid_skill_fields")
    bounds = {"phrases": 8, "keywords": 12, "negative_phrases": 5, "examples": 3}
    return {
        key: _string_list(items, count=bounds[key], length=256)
        for key, items in value.items()
    }


def _fields(value: object, *, partial: bool = False) -> dict:
    if (
        not isinstance(value, dict)
        or (not partial and set(value) != _FIELDS)
        or not set(value).issubset(_FIELDS)
    ):
        raise _error("invalid_skill_fields")
    if partial and not value:
        raise _error("invalid_skill_fields")
    result: dict[str, object] = {}
    if "display_name" in value:
        result["display_name"] = _text(value["display_name"], 128, required=True)
    if "icon" in value:
        result["icon"] = _text(value["icon"], 32, required=True)
    if "description" in value:
        result["description"] = _text(value["description"], 1024)
    if "instructions" in value:
        result["instructions"] = _text(value["instructions"], 48 * 1024, required=True)
    if "tags" in value:
        result["tags"] = _string_list(value["tags"], count=32, length=128)
    if "activation" in value:
        result["activation"] = _activation(value["activation"])
    if "version" in value:
        result["version"] = _text(value["version"], 32, required=True)
    return result


def _public(
    item: dict,
    enabled: dict,
    pinned: list[str],
    *,
    detail: bool = False,
    public: bool = False,
) -> dict:
    skill = item["skill"]
    value = {
        "id": skill.name,
        "display_name": skill.display_name,
        "icon": skill.icon,
        "description": skill.description,
        "source": skill.source,
        "public": public,
        "version": skill.version,
        "tags": list(skill.tags),
        "activation": deepcopy(skill.activation),
        "available": bool(enabled.get(skill.name, False)),
        "pinned": skill.name in pinned,
        "editable": skill.source == "user",
        "tool_guide": skills.is_tool_guide(skill),
        "revision": item["revision"],
        "instructions_preview": skill.instructions[:320],
        "truncated": len(skill.instructions) > 320,
    }
    if detail:
        value["instructions"] = skill.instructions
    return value


def _snapshot() -> dict:
    try:
        return skills.read_client_skills()
    except (OSError, ValueError, UnicodeError, yaml.YAMLError):
        raise _error() from None


def read_skill_library(
    *,
    query: str = "",
    source: str | None = None,
    filter: str = "all",
    sort: str = "name",
    cursor: str | None = None,
    limit: int = 50,
    validate: Callable[[], None],
) -> dict:
    """Read one stable, bounded page without loading or migrating skills."""
    validate()
    if (
        not isinstance(query, str)
        or len(query) > 256
        or source not in {None, "user", "bundled", "public"}
        or filter not in {"all", "pinned", "available", "custom", "public"}
        or sort not in {"name", "recent", "tokens", "source"}
        or type(limit) is not int
        or not 1 <= limit <= 50
    ):
        raise _error("invalid_skill_query")
    snapshot = _snapshot()
    revision = snapshot["revision"]
    from row_bot.skills_hub.provenance import load_records

    public_names = set(load_records())
    telemetry = {}
    if sort == "recent":
        from row_bot.skills_activation import get_skill_telemetry

        telemetry = get_skill_telemetry()
    needle = " ".join(query.lower().split())
    rows = []
    for item in snapshot["items"].values():
        skill = item["skill"]
        if skills.is_tool_guide(skill):
            continue
        is_public = skill.name in public_names
        if source and (not is_public if source == "public" else skill.source != source):
            continue
        if filter == "public" and not is_public:
            continue
        if filter == "custom" and skill.source != "user":
            continue
        if filter == "pinned" and skill.name not in snapshot["pinned"]:
            continue
        if filter == "available" and not snapshot["enabled"].get(skill.name, False):
            continue
        haystack = " ".join(
            [
                skill.name,
                skill.display_name,
                skill.description,
                *skill.tags,
                "Public"
                if is_public
                else "Bundled"
                if skill.source == "bundled"
                else "Custom",
            ]
        ).lower()
        if needle and needle not in haystack:
            continue
        row = _public(item, snapshot["enabled"], snapshot["pinned"], public=is_public)
        row["_tokens"] = skills.estimate_text_tokens(skill.instructions)
        rows.append(row)

    def sort_key(item: dict) -> tuple:
        name = item["display_name"].casefold()
        if sort == "recent":
            return (
                str(telemetry.get(item["id"], {}).get("last_used") or ""),
                name,
                item["id"],
            )
        if sort == "tokens":
            return (item["_tokens"], name, item["id"])
        if sort == "source":
            label = (
                "Public"
                if item["public"]
                else "Bundled"
                if item["source"] == "bundled"
                else "Custom"
            )
            return (label, name, item["id"])
        return (name, item["id"])

    rows.sort(key=sort_key, reverse=sort == "recent")
    proof = _digest(
        [revision, query, source, filter, sort, [row["id"] for row in rows]]
    )
    offset = 0
    if cursor:
        try:
            cursor_proof, raw_offset = cursor.split(":", 1)
            offset = int(raw_offset)
            if cursor_proof != proof or offset <= 0:
                raise ValueError
        except (AttributeError, ValueError):
            raise _error("cursor_expired") from None
    if cursor and offset >= len(rows):
        raise _error("cursor_expired")
    items = rows[offset : offset + limit]
    for item in items:
        item.pop("_tokens")
    result = {
        "schema_version": 1,
        "revision": revision,
        "availability": "available",
        "items": items,
        "total": len(rows),
        "next_cursor": f"{proof}:{offset + limit}"
        if offset + limit < len(rows)
        else None,
    }
    if len(json.dumps(result, ensure_ascii=True).encode()) > _MAX_PUBLIC_BYTES:
        raise _error("skills_response_too_large")
    validate()
    return result


def read_skill_detail(skill_id: str, *, validate: Callable[[], None]) -> dict:
    validate()
    snapshot = _snapshot()
    item = snapshot["items"].get(_name(skill_id))
    if item is None or skills.is_tool_guide(item["skill"]):
        raise _error("skill_missing")
    result = {
        "schema_version": 1,
        "library_revision": snapshot["revision"],
        "skill": _public(item, snapshot["enabled"], snapshot["pinned"], detail=True),
    }
    if len(json.dumps(result, ensure_ascii=True).encode()) > _MAX_PUBLIC_BYTES:
        raise _error("skills_response_too_large")
    validate()
    return result


def _proposal_store() -> tuple[list[dict], str]:
    from row_bot.data_paths import get_row_bot_data_dir
    from row_bot.evolution import redact_text

    def public_preview(value: object, depth: int = 0) -> object:
        if depth > 4:
            return "[truncated]"
        if isinstance(value, str):
            return redact_text(value, max_chars=4096)
        if type(value) in {bool, int, float} or value is None:
            return value
        if isinstance(value, list):
            return [public_preview(item, depth + 1) for item in value[:50]]
        if isinstance(value, dict):
            return {
                str(key)[:128]: public_preview(item, depth + 1)
                for key, item in list(value.items())[:50]
            }
        return "[unavailable]"

    path = get_row_bot_data_dir(create=False) / "controlled_evolution.json"
    try:
        raw = skills._client_file(path, maximum=1024 * 1024)
        if raw[0] is None:
            return [], "missing"
        store = json.loads(raw[0])
        values = store.get("proposals")
        if (
            not isinstance(store, dict)
            or not isinstance(values, list)
            or len(values) > 4096
        ):
            raise ValueError
        result = []
        for value in values:
            if not isinstance(value, dict) or value.get("proposal_type") not in {
                "create_skill",
                "patch_skill",
                "consolidate_skills",
            }:
                continue
            public = {
                "id": _text(value.get("id"), 128, required=True),
                "type": value["proposal_type"],
                "title": redact_text(_text(value.get("title", ""), 256), max_chars=256),
                "rationale": redact_text(
                    _text(value.get("rationale", ""), 4096), max_chars=4096
                ),
                "risk": value.get("risk")
                if value.get("risk") in {"low", "medium", "high"}
                else "medium",
                "status": value.get("status")
                if value.get("status")
                in {
                    "draft",
                    "ready",
                    "approved",
                    "applied",
                    "verified",
                    "rejected",
                    "failed",
                }
                else "draft",
                "preview": public_preview(value.get("preview"))
                if isinstance(value.get("preview"), dict)
                else {},
            }
            if len(json.dumps(public, ensure_ascii=True).encode()) > 32 * 1024:
                raise ValueError
            result.append(public)
        return result, raw[1]
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _error("skill_proposals_unavailable") from None


def read_skill_proposals(*, validate: Callable[[], None]) -> dict:
    validate()
    values, revision = _proposal_store()
    values.sort(key=lambda item: item["id"], reverse=True)
    result = {
        "schema_version": 1,
        "revision": revision,
        "items": values[:100],
        "truncated": len(values) > 100,
    }
    validate()
    return result


def _new_skill_content(name: str, fields: dict) -> bytes:
    metadata: dict[str, object] = {
        "name": name,
        "display_name": fields["display_name"],
        "icon": fields["icon"],
        "description": fields["description"],
        "enabled_by_default": True,
        "version": fields["version"],
        "author": "User",
    }
    if fields["tags"]:
        metadata["tags"] = fields["tags"]
    if fields["activation"]:
        metadata["activation"] = fields["activation"]
    content = f"---\n{skills._build_ordered_frontmatter(metadata)}---\n\n{fields['instructions']}\n"
    value = content.encode()
    parsed = skills._parse_skill_text(content, Path(name) / "SKILL.md", "user")
    if (
        len(value) > _MAX_SKILL_BYTES
        or parsed is None
        or parsed.name != name
        or parsed.display_name != fields["display_name"]
        or parsed.icon != fields["icon"]
        or parsed.description != fields["description"]
        or parsed.instructions != fields["instructions"]
        or parsed.tags != fields["tags"]
        or parsed.activation != fields["activation"]
        or parsed.version != fields["version"]
        or parsed.tools
    ):
        raise _error("invalid_skill_fields")
    return value


def _import_content(value: object) -> tuple[str, dict, bytes]:
    content = _text(value, _MAX_SKILL_BYTES, required=True)
    parsed = skills._parse_skill_text(content, Path("import") / "SKILL.md", "user")
    if parsed is None or not _NAME.fullmatch(parsed.name) or parsed.tools:
        raise _error("invalid_skill_import")
    fields = _fields(
        {
            "display_name": parsed.display_name,
            "icon": parsed.icon,
            "description": parsed.description,
            "instructions": parsed.instructions,
            "tags": list(parsed.tags),
            "activation": deepcopy(parsed.activation),
            "version": parsed.version,
        }
    )
    return parsed.name, fields, _new_skill_content(parsed.name, fields)


def _review(action: str, payload: dict) -> tuple[dict, dict]:
    if action not in _ACTIONS or not isinstance(payload, dict):
        raise _error("invalid_skill_command")
    snapshot = _snapshot()
    if payload.get("revision") != snapshot["revision"]:
        raise _error("skill_revision_conflict")
    normalized: dict = {"revision": payload["revision"]}
    target: dict | None = None
    after: dict | None = None
    if action == "skill.preference":
        if (
            set(payload) != {"revision", "name", "preference", "value"}
            or payload.get("preference") not in {"availability", "pin_defaults"}
            or type(payload.get("value")) is not bool
        ):
            raise _error("invalid_skill_command")
        name = _name(payload["name"])
        target = snapshot["items"].get(name)
        if target is None or skills.is_tool_guide(target["skill"]):
            raise _error("invalid_skill_target")
        normalized.update(
            name=name, preference=payload["preference"], value=payload["value"]
        )
    elif action in {"skill.create", "skill.import"}:
        if action == "skill.create":
            if set(payload) != {"revision", "name", "fields"}:
                raise _error("invalid_skill_command")
            name, fields = _name(payload["name"]), _fields(payload["fields"])
            content = _new_skill_content(name, fields)
        else:
            if set(payload) != {"revision", "content"}:
                raise _error("invalid_skill_command")
            name, fields, content = _import_content(payload["content"])
        if name in snapshot["items"]:
            raise _error("skill_exists")
        normalized.update(
            name=name, fields=fields, content_digest=hashlib.sha256(content).hexdigest()
        )
        after = fields
    elif action == "skill.edit":
        if set(payload) != {"revision", "name", "skill_revision", "fields"}:
            raise _error("invalid_skill_command")
        name, fields = _name(payload["name"]), _fields(payload["fields"], partial=True)
        target = snapshot["items"].get(name)
        if target is None:
            raise _error("skill_missing")
        if (
            target["skill"].source != "user"
            or target["revision"] != payload["skill_revision"]
        ):
            raise _error("skill_revision_conflict")
        current = target["skill"]
        merged = {
            "display_name": current.display_name,
            "icon": current.icon,
            "description": current.description,
            "instructions": current.instructions,
            "tags": list(current.tags),
            "activation": deepcopy(current.activation),
            "version": current.version,
            **fields,
        }
        _new_skill_content(name, merged)
        normalized.update(name=name, skill_revision=target["revision"], fields=fields)
        after = merged
    elif action == "skill.duplicate":
        if set(payload) != {"revision", "name", "new_name"}:
            raise _error("invalid_skill_command")
        name, new_name = _name(payload["name"]), _name(payload["new_name"])
        target = snapshot["items"].get(name)
        if target is None:
            raise _error("skill_missing")
        if new_name in snapshot["items"]:
            raise _error("skill_exists")
        current = target["skill"]
        after = {
            "display_name": f"{current.display_name} (Custom)",
            "icon": current.icon,
            "description": current.description,
            "instructions": current.instructions,
            "tags": list(current.tags),
            "activation": deepcopy(current.activation),
            "version": current.version,
        }
        _new_skill_content(new_name, after)
        normalized.update(
            name=name, new_name=new_name, skill_revision=target["revision"]
        )
    elif action == "skill.delete":
        if set(payload) != {"revision", "name", "skill_revision"}:
            raise _error("invalid_skill_command")
        name = _name(payload["name"])
        target = snapshot["items"].get(name)
        if target is None:
            raise _error("skill_missing")
        if (
            target["skill"].source != "user"
            or target["revision"] != payload["skill_revision"]
        ):
            raise _error("skill_revision_conflict")
        normalized.update(name=name, skill_revision=target["revision"])
    else:
        expected = {"revision", "proposal_id", "reason"}
        if set(payload) != expected:
            raise _error("invalid_skill_command")
        proposal_id = _text(payload["proposal_id"], 128, required=True)
        reason = _text(payload["reason"], 1024)
        proposals, proposal_revision = _proposal_store()
        proposal = next((item for item in proposals if item["id"] == proposal_id), None)
        if proposal is None or proposal["status"] in {
            "applied",
            "verified",
            "rejected",
        }:
            raise _error("skill_proposal_changed")
        normalized.update(
            proposal_id=proposal_id, reason=reason, proposal_revision=proposal_revision
        )
        after = proposal
    review = {
        "schema_version": 1,
        "action": action,
        "revision": snapshot["revision"],
        "target": normalized.get("name") or normalized.get("proposal_id"),
        "before_revision": target["revision"] if target else None,
        "after": after,
        "action_digest": _digest(normalized),
    }
    return review, normalized


def review_skill_command(
    action: str, payload: dict, *, validate: Callable[[], None]
) -> dict:
    validate()
    review, _ = _review(action, deepcopy(payload))
    validate()
    return review


def _proof(saved: dict) -> object | None:
    from row_bot.developer.edits import FileEditRecovery

    value = saved.get("_skill", {}).get("recovery")
    try:
        return FileEditRecovery(**value) if isinstance(value, dict) else None
    except TypeError:
        raise _error("skill_operation_unavailable") from None


def _public_receipt(saved: dict) -> dict:
    status = saved.get("status")
    if status not in {"completed", "partial"}:
        raise _error("skill_operation_unavailable")
    result = saved.get("result")
    if not isinstance(result, dict) or set(result) != {
        "command_id",
        "status",
        "action",
        "skill_id",
        "revision",
        "code",
    }:
        raise _error("skill_operation_unavailable")
    return deepcopy(result)


def read_skill_command(
    *, owner_id: str, authority_id: str, command_id: str, validate: Callable[[], None]
) -> dict:
    validate()
    command_id = _uuid(command_id)
    metadata = admissions.read_command_metadata(owner_id, command_id)
    saved = admissions.read_command_receipt(owner_id, command_id)
    private = saved.get("_skill") if isinstance(saved, dict) else None
    if (
        not metadata
        or metadata["target"] != "skills"
        or metadata["type"] not in _ACTIONS
        or not isinstance(private, dict)
        or private.get("scope") != _scope(owner_id, authority_id, read_only=True)
    ):
        raise _error("skill_operation_unavailable")
    result = _public_receipt(saved)
    if metadata["status"] == "completed":
        result["status"] = "completed"
    elif _proof(saved) is not None and private.get("root") in {"config", "skill"}:
        root = (
            skills.CONFIG_PATH.parent
            if private["root"] == "config"
            else skills.USER_SKILLS_DIR / private["skill_id"]
        )
        filename = (
            skills.CONFIG_PATH.name if private["root"] == "config" else "SKILL.md"
        )
        state = read_recovery(
            root,
            filename,
            _proof(saved),
            max_bytes=1024 * 1024,
            unavailable_code="skill_unavailable",
        )
        result.update(
            status="completed" if state == "applied" else "partial",
            code=None if state == "applied" else "skill_outcome_uncertain",
        )
    else:
        result.update(status="partial", code="skill_outcome_uncertain")
    validate()
    return result


def execute_skill_command(
    command: dict,
    *,
    owner_id: str,
    authority_id: str,
    key: str,
    validate: Callable[[], None],
    validate_action: Callable[[str], None],
    validate_review: Callable[[dict, dict], None],
) -> dict:
    """Apply exactly one reviewed command; retries recover the original receipt."""
    validate()
    command = deepcopy(command)
    command_id = _uuid(command.get("command_id"))
    action, raw = command.get("type"), command.get("payload")
    if action not in _ACTIONS or not isinstance(raw, dict) or "review_id" not in raw:
        raise _error("invalid_skill_command")
    payload = {name: value for name, value in raw.items() if name != "review_id"}
    with _LOCK:
        if admissions.read_command_metadata(owner_id, command_id) is not None:
            try:
                admissions.claim_command(owner_id, key, command, "skills")
            except admissions.AdmissionError as error:
                if str(error) != "operation_uncertain":
                    raise _error(str(error)) from None
            return read_skill_command(
                owner_id=owner_id,
                authority_id=authority_id,
                command_id=command_id,
                validate=validate,
            )
        review, normalized = _review(action, payload)

        def authority() -> None:
            validate()
            validate_action(action)
            validate_review(command, review)

        authority()
        result = {
            "command_id": command_id,
            "status": "partial",
            "action": action,
            "skill_id": normalized.get("new_name") or normalized.get("name"),
            "revision": None,
            "code": "skill_outcome_uncertain",
        }
        private = {
            "scope": _scope(owner_id, authority_id),
            "recovery": None,
            "root": None,
            "skill_id": result["skill_id"],
        }
        progress = {
            "command_id": command_id,
            "status": "partial",
            "result": result,
            "_skill": private,
        }
        try:
            prior = admissions.claim_command(
                owner_id, key, command, "skills", initial_result=progress
            )
        except admissions.AdmissionError as error:
            if str(error) != "operation_uncertain":
                raise _error(str(error)) from None
            return read_skill_command(
                owner_id=owner_id,
                authority_id=authority_id,
                command_id=command_id,
                validate=validate,
            )
        if prior is not None:
            return read_skill_command(
                owner_id=owner_id,
                authority_id=authority_id,
                command_id=command_id,
                validate=validate,
            )

        def checkpoint(proof: object) -> None:
            authority()
            private["recovery"] = asdict(proof)
            admissions.command_progress(owner_id, key, progress)

        try:
            if action == "skill.preference":
                private.update(root="config", skill_id=normalized["name"])
                revision = skills.update_client_skill_preference(
                    normalized["name"],
                    normalized["preference"],
                    normalized["value"],
                    expected_revision=normalized["revision"],
                    command_id=command_id,
                    validate=authority,
                    checkpoint=checkpoint,
                )
            elif action in {"skill.create", "skill.import", "skill.duplicate"}:
                name = normalized.get("new_name") or normalized["name"]
                fields = review["after"]
                skills.USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
                folder = skills.USER_SKILLS_DIR / name
                folder.mkdir()
                private.update(root="skill", skill_id=name)
                revision = publish_bytes(
                    folder.absolute(),
                    "SKILL.md",
                    _new_skill_content(name, fields),
                    expected_revision="missing",
                    command_id=command_id,
                    validate=authority,
                    checkpoint=checkpoint,
                    max_bytes=_MAX_SKILL_BYTES,
                    unavailable_code="skill_unavailable",
                )
            elif action == "skill.edit":
                folder = skills.USER_SKILLS_DIR / normalized["name"]
                private.update(root="skill", skill_id=normalized["name"])
                revision = publish_bytes(
                    folder.absolute(),
                    "SKILL.md",
                    _new_skill_content(normalized["name"], review["after"]),
                    expected_revision=normalized["skill_revision"],
                    command_id=command_id,
                    validate=authority,
                    checkpoint=checkpoint,
                    max_bytes=_MAX_SKILL_BYTES,
                    unavailable_code="skill_unavailable",
                )
            elif action == "skill.delete":
                folder = skills.USER_SKILLS_DIR / normalized["name"]
                private.update(root="skill", skill_id=normalized["name"])
                revision = publish_bytes(
                    folder.absolute(),
                    "SKILL.md",
                    None,
                    expected_revision=normalized["skill_revision"],
                    command_id=command_id,
                    validate=authority,
                    checkpoint=checkpoint,
                    max_bytes=_MAX_SKILL_BYTES,
                    unavailable_code="skill_unavailable",
                )
            else:
                proposals, current = _proposal_store()
                if current != normalized["proposal_revision"]:
                    raise _error("skill_proposal_changed")
                from row_bot import evolution

                if action == "skill.proposal.apply":
                    outcome = evolution.apply_proposal(
                        normalized["proposal_id"],
                        require_approval=False,
                        approved_by_user=True,
                    )
                else:
                    outcome = evolution.reject_proposal(
                        normalized["proposal_id"], normalized["reason"]
                    )
                    outcome = {"ok": outcome.get("status") == "rejected"}
                if not outcome.get("ok"):
                    raise _error("skill_proposal_changed")
                revision = _proposal_store()[1]
                result["skill_id"] = None
            authority()
            result.update(status="completed", revision=revision, code=None)
            progress["status"] = "completed"
            admissions.complete_command(owner_id, key, progress)
        except Exception:
            if private["recovery"] is not None:
                return read_skill_command(
                    owner_id=owner_id,
                    authority_id=authority_id,
                    command_id=command_id,
                    validate=validate,
                )
            raise
        return deepcopy(result)
