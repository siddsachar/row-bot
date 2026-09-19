"""Canonical Buddy admission with synthetic config and fake generation only."""
from uuid import uuid4
import json

import pytest

from row_bot.application import buddy_commands as adapter
from row_bot.buddy import config, client_service, client_hatch, hatch
from row_bot.runtime import admissions


@pytest.fixture
def env(tmp_path, monkeypatch):
    from row_bot import tasks
    monkeypatch.setattr(tasks, "_DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    monkeypatch.setattr(config, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_BUDDY_CONFIG_PATH", tmp_path / "buddy_config.json")
    monkeypatch.setattr(hatch, "_DATA_DIR", tmp_path / "hatch")
    return dict(owner_id="synthetic-installation", authority_id="synthetic-auth", validate=lambda: None)


def policy(action):
    return {"image_model": "fixture/image", "video_model": "fixture/video", "approval_revision": "synthetic-policy"}


def command(kind="buddy.update", payload=None):
    return {"command_id": str(uuid4()), "type": kind, "expected_revision": "0", "payload": payload or {
        "changes": {"visible": False}, "config_revision": "missing"}}


def execute(env, value, **options):
    return adapter.execute_buddy_command(value, key=value["command_id"], **{**env, "capture_provider_policy": policy,
        "validate_action": lambda action: None, "validate_provider": lambda *args: None, **options})


def reviewed(env, **options):
    request = {"action": "full", "prompt": "Synthetic private prompt", "config_revision": "missing"}
    review = adapter.review_buddy_action(**env, request=request, capture_provider_policy=options.get("capture_provider_policy", policy))
    return command("buddy.hatch", {"request": request, "review_id": review["review_id"]})


def test_settings_actual_publication_replay_and_private_proof(env):
    value = command()
    result = execute(env, value)
    assert result["status"] == "completed"
    assert not config.read_buddy_config_revision()[0]["visible"]
    assert execute(env, value) == result
    saved = admissions.receipt(env["owner_id"], value["command_id"])
    assert saved["_buddy"]["config"]["command_id"] == value["command_id"]
    assert "_buddy" not in result and "candidate_identity" not in json.dumps(result)
    with pytest.raises(adapter.ClientPlatformError, match="idempotency_mismatch"):
        execute(env, {**value, "payload": {**value["payload"], "changes": {"visible": True}}})


def test_lost_settings_ack_recovers_exact_proof_without_resave(env, monkeypatch):
    value = command()
    original = adapter._progress
    def progress(owner, key, changes, **kwargs):
        if kwargs.get("complete"):
            raise OSError("synthetic lost acknowledgement")
        return original(owner, key, changes, **kwargs)
    monkeypatch.setattr(adapter, "_progress", progress)
    with pytest.raises(OSError):
        execute(env, value)
    before = config._BUDDY_CONFIG_PATH.read_bytes()
    monkeypatch.setattr(client_service, "update_buddy", lambda *a, **k: pytest.fail("resave"))
    result = execute(env, value)
    assert result["status"] == "completed"
    assert config._BUDDY_CONFIG_PATH.read_bytes() == before
    config._BUDDY_CONFIG_PATH.write_text('{"visible":true}')
    result = execute(env, value)
    assert result["status"] == "partial" and "buddy_revision" not in result


@pytest.mark.parametrize("change", ["prompt", "policy", "session", "action"])
def test_review_exact_prompt_policy_action_session_no_provider(env, monkeypatch, change):
    value = reviewed(env)
    current_policy = policy
    if change == "prompt":
        value["payload"]["request"]["prompt"] = "changed"
    elif change == "policy":
        def current_policy(action):
            return {**policy(action), "approval_revision": "changed"}
    elif change == "session":
        env = {**env, "authority_id": "other-auth"}
    else:
        value["type"] = "buddy.remove"
    monkeypatch.setattr(client_hatch, "start_hatch", lambda **kwargs: pytest.fail("provider start"))
    with pytest.raises(adapter.ClientPlatformError):
        execute(env, value, capture_provider_policy=current_policy)
    assert not config._BUDDY_CONFIG_PATH.exists()


def test_hatch_async_progress_fast_completion_and_lost_start_ack(env, monkeypatch):
    value = reviewed(env)
    result = client_hatch.HatchResult(1, value["command_id"], "buddy-hatch-" + value["command_id"],
        "completed", "completed", "hatch-synthetic", True, 6, 6, "", True)
    monkeypatch.setattr(client_hatch, "read_result", lambda *args, **kwargs: result)
    calls = []
    def start(**kwargs):
        calls.append(kwargs)
        kwargs["validate"]()
        kwargs["validate_provider"]("image", "fixture/image")
        kwargs["checkpoint"](client_hatch.HatchProgress("completed", value["command_id"], result.job_id, result.pack_id, 6, 6))
        raise OSError("lost acknowledgement after completion")
    monkeypatch.setattr(client_hatch, "start_hatch", start)
    with pytest.raises(OSError):
        execute(env, value)
    recovered = execute(env, value)
    assert recovered["status"] == "completed" and recovered["hatch"]["completed_clips"] == 6
    assert len(calls) == 1
    assert "Synthetic private prompt" not in json.dumps(admissions.receipt(env["owner_id"], value["command_id"]))


def test_lost_start_before_manifest_never_blindly_restarts(env, monkeypatch):
    value = reviewed(env)
    calls = []
    def start(**kwargs):
        calls.append(True)
        raise OSError("process stopped before worker manifest")
    monkeypatch.setattr(client_hatch, "start_hatch", start)
    with pytest.raises(OSError):
        execute(env, value)
    assert execute(env, value)["status"] == "partial"
    assert calls == [True]


def test_captured_policy_revalidated_before_each_media_effect(env, monkeypatch):
    current = [policy("full")]
    def capture(action):
        return current[0]
    value = reviewed(env, capture_provider_policy=capture)
    called = []
    def start(**kwargs):
        kwargs["validate_provider"]("image", "fixture/image")
        current[0] = {**current[0], "approval_revision": "revoked"}
        kwargs["validate_provider"]("motion", "fixture/video")
    monkeypatch.setattr(client_hatch, "start_hatch", start)
    with pytest.raises(adapter.ClientPlatformError, match="buddy_policy_changed"):
        execute(env, value, capture_provider_policy=capture, validate_provider=lambda *args: called.append(args))
    assert len(called) == 1


def test_stop_exact_owned_source_and_no_raw_canonical_job_on_wire(env, monkeypatch):
    source = reviewed(env)
    execute(env, command())  # independent real settings receipt cannot become a Hatch source
    admissions.claim_command(env["owner_id"], source["command_id"], source, "buddy")
    admissions.command_progress(env["owner_id"], source["command_id"], {"command_id": source["command_id"],
        "_buddy": {"scope": adapter._scope(env["owner_id"], env["authority_id"])}})
    value = command("buddy.cancel", {"source_command_id": source["command_id"], "job_id": "buddy-hatch-" + source["command_id"]})
    calls = []
    monkeypatch.setattr(hatch, "cancel_client_hatch", lambda job_id, **kw: calls.append(job_id) or {"prompt": "private", "preview_path": "private"})
    result = execute(env, value)
    assert result == {"command_id": value["command_id"], "status": "completed", "cancel_requested": True}
    assert len(calls) == 1
    with pytest.raises(adapter.ClientPlatformError, match="buddy_command_unavailable"):
        adapter.read_buddy_command(**{**env, "authority_id": "other"}, command_id=source["command_id"])
    with pytest.raises(adapter.ClientPlatformError, match="buddy_command_unavailable"):
        execute({**env, "authority_id": "other"}, command("buddy.cancel", value["payload"]))
    assert len(calls) == 1


def test_authority_rejection_before_save_preserves_original_exception(env):
    sentinel = PermissionError("synthetic authority revoked")
    def reject(action):
        raise sentinel
    with pytest.raises(PermissionError) as caught:
        execute(env, command(), validate_action=reject)
    assert caught.value is sentinel and not config._BUDDY_CONFIG_PATH.exists()


def test_retirement_partial_exact_proof_recovery_no_repeat(env, monkeypatch):
    from row_bot.buddy.client_service import BuddyPack
    descriptor = BuddyPack("hatch-synthetic", "Synthetic", "pack-revision", "generated_still", True, True, (), {})
    monkeypatch.setattr(client_service, "_pack", lambda value: (descriptor, None, None))
    request = {"action": "remove", "prompt": "Synthetic", "config_revision": "missing",
               "source_pack_id": descriptor.id, "source_pack_revision": descriptor.revision}
    review = adapter.review_buddy_action(**env, request=request, capture_provider_policy=policy)
    value = command("buddy.remove", {"request": request, "review_id": review["review_id"]})
    calls = []
    proof = client_hatch.HatchPackRetirement(value["command_id"], descriptor.id, descriptor.revision, "private-parent", "private-leaf")
    def retire(*args, **kwargs):
        calls.append(True)
        kwargs["checkpoint_retirement"](proof)
        raise OSError("lost retirement acknowledgement")
    monkeypatch.setattr(client_hatch, "retire_pack", retire)
    monkeypatch.setattr(client_hatch, "read_retirement_recovery", lambda *a, **k: "applied")
    with pytest.raises(OSError):
        execute(env, value)
    result = execute(env, value)
    assert result["status"] == "completed" and result["removal"]["retained_copy"]
    assert calls == [True] and "private" not in json.dumps(result)
    monkeypatch.setattr(client_hatch, "read_retirement_recovery", lambda *a, **k: "conflict")
    assert execute(env, value)["status"] == "partial"


def test_rejected_late_session_cannot_read_completed_result(env):
    value = command()
    execute(env, value)
    sentinel = PermissionError("revoked")
    def reject():
        raise sentinel
    with pytest.raises(PermissionError) as caught:
        adapter.read_buddy_command(**{**env, "validate": reject}, command_id=value["command_id"])
    assert caught.value is sentinel


def test_one_time_authority_rejection_during_recovery_is_not_swallowed(env, monkeypatch):
    value = reviewed(env)
    monkeypatch.setattr(client_hatch, "start_hatch", lambda **kwargs: (_ for _ in ()).throw(OSError("lost")))
    with pytest.raises(OSError):
        execute(env, value)
    sentinel = ValueError("synthetic one-time rejection")
    calls = []
    def validate():
        calls.append(True)
        if len(calls) == 2:
            raise sentinel
    monkeypatch.setattr(client_hatch, "read_result", lambda *args, **kwargs: kwargs["validate"]())
    with pytest.raises(ValueError) as caught:
        adapter.read_buddy_command(**{**env, "validate": validate}, command_id=value["command_id"])
    assert caught.value is sentinel


def test_review_binds_retained_source_bytes_without_wire_digest(env, monkeypatch):
    from row_bot.buddy.client_service import BuddyPack
    descriptor = BuddyPack("hatch-synthetic", "Synthetic", "pack-revision", "generated_still", True, True, (), {})
    monkeypatch.setattr(client_service, "_pack", lambda value: (descriptor, None, None))
    data = [b"reviewed synthetic source"]
    monkeypatch.setattr(client_service, "read_buddy_media", lambda *a, **k: (data[0], "image/png"))
    request = {"action": "still", "prompt": "Synthetic", "config_revision": "missing",
               "source_pack_id": descriptor.id, "source_pack_revision": descriptor.revision}
    review = adapter.review_buddy_action(**env, request=request, capture_provider_policy=policy)
    assert "source_digest" not in review
    data[0] = b"changed after review"
    monkeypatch.setattr(client_hatch, "start_hatch", lambda **kwargs: pytest.fail("source substitution"))
    with pytest.raises(adapter.ClientPlatformError, match="buddy_review_changed"):
        execute(env, command("buddy.hatch", {"request": request, "review_id": review["review_id"]}))


def test_actual_start_checks_same_captured_source_buffer_before_job_admission(env, monkeypatch):
    import hashlib
    monkeypatch.setattr(hatch, "get_hatch_generation_status", lambda: {})
    monkeypatch.setattr(client_service, "read_buddy_media", lambda *a, **k: (b"changed while source was read", "image/png"))
    monkeypatch.setattr(hatch, "start_hatch_generation_job", lambda *a, **k: pytest.fail("must not admit worker"))
    with pytest.raises(ValueError, match="hatch_source_changed"):
        client_hatch.start_hatch(command_id=str(uuid4()), prompt="Synthetic", mode="still", expected_config_revision="missing",
            image_selection="", video_selection="", validate=lambda: None, validate_provider=lambda *args: None,
            checkpoint=lambda value: None, checkpoint_config=lambda value: None, checkpoint_draft=lambda *args: None,
            source_pack_id="hatch-synthetic", source_pack_revision="synthetic",
            expected_source_digest=hashlib.sha256(b"reviewed bytes").hexdigest())


@pytest.mark.parametrize("media,selection", [("image", "foreign/image"), ("motion", "foreign/video"), ("unknown", "fixture/video")])
def test_provider_callback_cannot_change_reviewed_kind_or_model(env, monkeypatch, media, selection):
    value = reviewed(env)
    monkeypatch.setattr(client_hatch, "start_hatch", lambda **kwargs: kwargs["validate_provider"](media, selection))
    with pytest.raises(adapter.ClientPlatformError, match="buddy_policy_changed"):
        execute(env, value, validate_provider=lambda *a: pytest.fail("unreviewed provider"))
