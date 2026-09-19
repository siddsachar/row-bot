"""Buddy effects use canonical admissions; recovery never repeats generation.

The route supplies current session authority and the canonical provider/tool
approval callbacks. Private publication proofs stay in the existing receipt.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from row_bot.buddy.client_hatch import HatchPackRetirement, HatchProgress
    from row_bot.developer.edits import FileEditRecovery
    from row_bot.providers.media_auth import CapturedMediaAuth

from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict
import hmac
import hashlib
import json
from typing import Any
from uuid import UUID

from row_bot.application.client_platform import ClientPlatformError
from row_bot.runtime import admissions

_TYPES = {"buddy.update", "buddy.hatch", "buddy.remove", "buddy.cancel"}
_PUBLIC = {"command_id", "status", "code", "buddy_revision", "hatch", "removal", "cancel_requested"}


def public_receipt(value: dict) -> dict:
    """Also required at generic receipt and command replay endpoints."""
    return {name: deepcopy(item) for name, item in value.items() if name in _PUBLIC}


def _uuid(value: Any) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError):
        raise ClientPlatformError("invalid_buddy_command") from None


def _request(value: dict) -> dict:
    allowed = {"action", "prompt", "config_revision", "source_pack_id", "source_pack_revision", "source_command_id"}
    if (not isinstance(value, dict) or not value.keys() <= allowed
            or value.get("action") not in {"full", "motion", "still", "remove"}
            or not isinstance(value.get("prompt"), str) or not 1 <= len(value["prompt"].strip()) <= 4000
            or not isinstance(value.get("config_revision"), str) or not 1 <= len(value["config_revision"]) <= 128):
        raise ClientPlatformError("invalid_hatch_request")
    for key in ("source_pack_id", "source_pack_revision", "source_command_id"):
        if key in value and (not isinstance(value[key], str) or not 1 <= len(value[key]) <= 128):
            raise ClientPlatformError("invalid_hatch_request")
    source = bool(value.get("source_command_id"))
    pack = bool(value.get("source_pack_id"))
    if (source and pack or pack != bool(value.get("source_pack_revision"))
            or value["action"] == "full" and (source or pack)
            or value["action"] != "full" and not (source or pack)
            or value["action"] == "remove" and not pack):
        raise ClientPlatformError("invalid_hatch_request")
    if source:
        _uuid(value["source_command_id"])
    return deepcopy(value)


def _scope(owner_id: str, authority_id: str, *, read_only: bool = False) -> str:
    if not owner_id or not isinstance(authority_id, str) or not 1 <= len(authority_id) <= 256:
        raise ClientPlatformError("action_denied")
    return admissions.keyed_digest({"buddy_authority": authority_id, "owner": owner_id}, read_only=read_only)


def _owned(owner_id: str, command_id: str, scope: str, *, kinds: set[str] | None = None) -> tuple[dict, dict]:
    metadata = admissions.read_command_metadata(owner_id, _uuid(command_id))
    saved = admissions.read_command_receipt(owner_id, command_id)
    if (not metadata or metadata["target"] != "buddy" or metadata["type"] not in (kinds or _TYPES)
            or not saved or saved.get("_buddy", {}).get("scope") != scope):
        raise ClientPlatformError("buddy_command_unavailable")
    return metadata, saved


def _policy(action: str, capture: Callable[[str], dict]) -> dict:
    policy = deepcopy(capture(action))
    if not isinstance(policy, dict) or len(json.dumps(policy).encode()) > 16384:
        raise ClientPlatformError("buddy_policy_unavailable")
    for name, required in (("image_model", action == "full"), ("video_model", action in {"full", "motion"})):
        value = policy.get(name)
        if required and (not isinstance(value, str) or not 1 <= len(value) <= 256 or "/" not in value):
            raise ClientPlatformError("invalid_media_selection")
        if not required:
            policy[name] = None
    return policy


def _source_digest(request: dict, validate: Callable[[], None]) -> str | None:
    from row_bot.buddy import client_hatch, client_service
    if request["action"] not in {"motion", "still"}:
        return None
    if request.get("source_command_id"):
        data = client_hatch._retained_still(request["source_command_id"], validate=validate)
    else:
        data, _ = client_service.read_buddy_media(request["source_pack_id"], "preview",
            expected_revision=request["source_pack_revision"], validate=validate)
    return hashlib.sha256(data).hexdigest()


def review_buddy_action(*, owner_id: str, authority_id: str, request: dict,
        validate: Callable[[], None], capture_provider_policy: Callable[[str], dict]) -> dict:
    """Passive review; capture must inspect policy without creating providers."""
    from row_bot.buddy import config, client_service, client_hatch
    validate()
    request = _request(request)
    scope = _scope(owner_id, authority_id)
    if config.read_buddy_config_revision()[1] != request["config_revision"]:
        raise ClientPlatformError("buddy_revision_conflict")
    if request.get("source_command_id"):
        _owned(owner_id, request["source_command_id"], scope, kinds={"buddy.hatch"})
        if not client_hatch.read_result(request["source_command_id"], validate=validate).has_still:
            raise ClientPlatformError("hatch_source_changed")
    if request.get("source_pack_id"):
        descriptor = client_service._pack(request["source_pack_id"])[0]
        if (not descriptor.available or descriptor.revision != request["source_pack_revision"]
                or request["action"] == "remove" and not descriptor.generated):
            raise ClientPlatformError("buddy_revision_conflict")
    policy = _policy(request["action"], capture_provider_policy)
    digest = admissions.keyed_digest({"scope": scope, "request": request, "policy": policy,
                                      "source_digest": _source_digest(request, validate)})
    validate()
    return {"review_id": digest, "action": request["action"], "config_revision": request["config_revision"],
            "image_model": policy["image_model"], "video_model": policy["video_model"],
            "provider_calls": 7 if request["action"] == "full" else 6 if request["action"] == "motion" else 0}


def _progress(owner: str, key: str, changes: dict, *, private: dict | None = None, complete=False) -> dict:
    # Merge under the existing SQLite writer admission: a fast worker must not
    # lose its newer proof when the start response or a read finishes later.
    with admissions.transaction() as conn:
        row = conn.execute("SELECT result_json,status FROM client_commands WHERE owner_id=? AND key=?", (owner, key)).fetchone()
        if not row or row["status"] != "admitting":
            raise admissions.AdmissionError("operation_uncertain")
        saved = json.loads(row["result_json"])
        saved.update(deepcopy(changes))
        if private:
            saved.setdefault("_buddy", {}).update(deepcopy(private))
        data = json.dumps(saved, separators=(",", ":"))
        if len(data.encode()) > 65536:
            raise ClientPlatformError("buddy_receipt_unavailable")
        conn.execute("UPDATE client_commands SET result_json=?,status=? WHERE owner_id=? AND key=?",
                     (data, "completed" if complete else "admitting", owner, key))
        return saved


def read_buddy_command(*, owner_id: str, authority_id: str, command_id: str,
                       validate: Callable[[], None]) -> dict:
    """Read exact owner progress/proofs. Never re-save, resume or re-generate."""
    from row_bot.buddy import config, client_hatch
    from row_bot.developer.edits import FileEditRecovery
    validate()
    metadata, saved = _owned(owner_id, command_id, _scope(owner_id, authority_id, read_only=True))
    if saved.get("status") == "completed":
        validate()
        return public_receipt(saved)
    private = saved["_buddy"]
    result = {**saved, "status": "partial", "code": "buddy_outcome_uncertain"}
    authority_error = None
    def authority() -> None:
        nonlocal authority_error
        try:
            validate()
        except Exception as exc:
            authority_error = exc
            raise
    try:
        if metadata["type"] == "buddy.hatch":
            result["hatch"] = asdict(client_hatch.read_result(command_id, validate=authority))
            result["status"] = "accepted" if result["hatch"]["status"] in {"queued", "running", "cancelling"} else "partial"
        elif metadata["type"] == "buddy.update" and private.get("config"):
            proof = FileEditRecovery(**private["config"])
            if proof.command_id == command_id and config.read_buddy_config_recovery(proof) == "applied":
                result.update(status="completed", code="", buddy_revision=proof.after_digest)
        elif metadata["type"] == "buddy.remove" and private.get("retirement"):
            proof = client_hatch.HatchPackRetirement(**private["retirement"])
            config_proof = private.get("config")
            config_ok = not config_proof or config.read_buddy_config_recovery(FileEditRecovery(**config_proof)) == "applied"
            if proof.command_id == command_id and config_ok and client_hatch.read_retirement_recovery(proof, validate=authority) == "applied":
                result.update(status="completed", code="", removal={"status": "removed", "pack_id": proof.pack_id,
                    "retained_copy": True, "config_changed": bool(config_proof)})
    except (OSError, ValueError, TypeError):
        # Unknown or modified recovery bytes stay untouched. Authority failures
        # are rechecked below, never converted into a public saved result.
        if authority_error is not None:
            raise authority_error
    validate()
    return public_receipt(result)


def execute_buddy_command(command: dict, *, owner_id: str, authority_id: str, key: str,
        validate: Callable[[], None], capture_provider_policy: Callable[[str], dict],
        validate_action: Callable[[str], None], validate_provider: Callable[[str, str, dict], None]) -> dict:
    """Callbacks enforce current authenticated authority and canonical approvals.

    ``capture_provider_policy(action)`` returns image_model/video_model plus the
    current private provider/account/approval identity. ``validate_provider``
    must revalidate that captured selection and canonical tool approval before
    each provider call. ``validate_action`` covers ordinary edits/removal too.
    """
    from row_bot.buddy import client_service, client_hatch, hatch
    validate()
    command = deepcopy(command)
    kind, command_id = command.get("type"), _uuid(command.get("command_id"))
    if kind not in _TYPES or not isinstance(command.get("payload"), dict):
        raise ClientPlatformError("invalid_buddy_command")
    if len(json.dumps(command).encode()) > 65536:
        raise ClientPlatformError("invalid_buddy_command")
    scope = _scope(owner_id, authority_id)
    payload = command["payload"]
    expected_fields = ({"changes", "config_revision"} if kind == "buddy.update" else
                       {"source_command_id", "job_id"} if kind == "buddy.cancel" else {"request", "review_id"})
    if payload.keys() != expected_fields:
        raise ClientPlatformError("invalid_buddy_command")
    # Claim first only after route nonce validation. Replay bypasses changed
    # current config/policy checks and can only inspect the original effect.
    try:
        prior = admissions.claim_command(owner_id, key, command, "buddy")
    except admissions.AdmissionError as exc:
        if str(exc) != "operation_uncertain":
            raise ClientPlatformError(str(exc)) from exc
        return read_buddy_command(owner_id=owner_id, authority_id=authority_id, command_id=command_id, validate=validate)
    if prior is not None:
        _owned(owner_id, command_id, scope)
        validate()
        return public_receipt(prior)
    admissions.command_progress(owner_id, key, {"command_id": command_id, "status": "admitting", "_buddy": {"scope": scope}})
    action = "update" if kind == "buddy.update" else "cancel" if kind == "buddy.cancel" else ""
    policy = None

    def authority() -> None:
        validate()
        validate_action(action)
        if policy is not None and not hmac.compare_digest(admissions.keyed_digest(_policy(action, capture_provider_policy)), policy_digest):
            raise ClientPlatformError("buddy_policy_changed")

    def proof(name: str, value: FileEditRecovery | HatchPackRetirement) -> None:
        _progress(owner_id, key, {}, private={name: asdict(value)})

    def provider_authority(media: str, selection: str) -> CapturedMediaAuth:
        authority()
        name = {"image": "image_model", "motion": "video_model"}.get(media)
        if not name or policy is None or policy[name] != selection or not selection:
            raise ClientPlatformError("buddy_policy_changed")
        return validate_provider(media, selection, deepcopy(policy))

    def checkpoint(value: HatchProgress) -> None:
        if value.command_id != command_id:
            raise ClientPlatformError("buddy_receipt_unavailable")
        _progress(owner_id, key, {}, private={"stage": value.stage})
        if kind == "buddy.hatch" and value.stage in {"completed", "stopped", "needs_attention"}:
            outcome = asdict(client_hatch.read_result(command_id, validate=lambda: None))
            _progress(owner_id, key, {"status": "completed" if outcome["status"] == "completed" else "partial", "hatch": outcome}, complete=True)

    try:
        if kind in {"buddy.hatch", "buddy.remove"}:
            request = _request(payload.get("request"))
            action = request["action"]
            if (kind == "buddy.remove") != (action == "remove"):
                raise ClientPlatformError("invalid_hatch_request")
            policy = _policy(action, capture_provider_policy)
            policy_digest = admissions.keyed_digest(policy)
            if request.get("source_command_id"):
                _owned(owner_id, request["source_command_id"], scope, kinds={"buddy.hatch"})
            source_digest = _source_digest(request, validate)
            expected = admissions.keyed_digest({"scope": scope, "request": request, "policy": policy, "source_digest": source_digest})
            if not isinstance(payload.get("review_id"), str) or not hmac.compare_digest(payload["review_id"], expected):
                raise ClientPlatformError("buddy_review_changed")
            if request.get("source_command_id"):
                _owned(owner_id, request["source_command_id"], scope, kinds={"buddy.hatch"})
        authority()
        _progress(owner_id, key, {}, private={"stage": "effect_started"})
        if kind == "buddy.update":
            revision = client_service.update_buddy(payload["changes"], expected_revision=payload["config_revision"],
                command_id=command_id, validate=authority, checkpoint=lambda value: proof("config", value))
            outcome = _progress(owner_id, key, {"status": "completed", "buddy_revision": revision}, complete=True)
        elif kind == "buddy.cancel":
            source = _uuid(payload.get("source_command_id"))
            _owned(owner_id, source, scope, kinds={"buddy.hatch"})
            if payload.get("job_id") != "buddy-hatch-" + source:
                raise ClientPlatformError("hatch_job_unavailable")
            hatch.cancel_client_hatch(payload["job_id"], validate=authority)
            outcome = _progress(owner_id, key, {"status": "completed", "cancel_requested": True}, complete=True)
        elif kind == "buddy.remove":
            result = client_hatch.retire_pack(request["source_pack_id"], command_id=command_id,
                expected_pack_revision=request["source_pack_revision"], expected_config_revision=request["config_revision"],
                validate=authority, checkpoint=checkpoint, checkpoint_config=lambda value: proof("config", value),
                checkpoint_retirement=lambda value: proof("retirement", value))
            outcome = _progress(owner_id, key, {"status": "completed", "removal": result}, complete=True)
        else:
            client_hatch.start_hatch(command_id=command_id, prompt=request["prompt"], mode=action,
                expected_config_revision=request["config_revision"], image_selection=policy["image_model"] or "",
                video_selection=policy["video_model"] or "", validate=authority,
                validate_provider=provider_authority,
                checkpoint=checkpoint, checkpoint_config=lambda value: proof("config", value),
                checkpoint_draft=lambda stage, value: _progress(owner_id, key, {}, private={"draft_stage": stage, "draft": asdict(value)}),
                expected_source_digest=source_digest,
                **{name: request[name] for name in ("source_pack_id", "source_pack_revision", "source_command_id") if name in request})
            return read_buddy_command(owner_id=owner_id, authority_id=authority_id, command_id=command_id, validate=validate)
        validate()
        return public_receipt(outcome)
    except Exception:
        # Before effect admission there are no uncertain bytes. After admission
        # preserve private proof and require read-only recovery, even for a lost
        # response from publication or provider startup.
        saved = admissions.receipt(owner_id, command_id) or {}
        if saved.get("_buddy", {}).get("stage") is None:
            admissions.reject_command(owner_id, key, "buddy_action_rejected")
        raise
