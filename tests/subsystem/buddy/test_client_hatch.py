"""Strict Hatch media seams use fakes and existing execution-local tool owners."""
import base64
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4
import io
import json

import pytest

from row_bot.application.attachment_context import prepared_attachments
from row_bot.tools import image_gen_tool as image, video_gen_tool as video


@pytest.mark.parametrize("module", [image, video], ids=["image", "video"])
def test_captured_selection_is_nested_execution_local_and_does_not_reread_config(module, monkeypatch):
    monkeypatch.setattr(module.registry, "get_tool_config", lambda *args: "legacy/default")
    with module.strict_generation_output(selection="provider/outer", validate=lambda: None, sink=lambda data: "saved"):
        assert module._get_configured_selection() == "provider/outer"
        with module.strict_generation_output(selection="provider/inner", validate=lambda: None, sink=lambda data: "saved"):
            assert module._get_configured_selection() == "provider/inner"
        assert module._get_configured_selection() == "provider/outer"
    assert module._get_configured_selection() == "legacy/default"


def test_simultaneous_image_sinks_and_pending_slots_cannot_cross_conversations(monkeypatch):
    barrier = Barrier(2)
    monkeypatch.setattr(image, "_last_generated_image", "legacy")

    def execute(identity):
        saved = []
        with prepared_attachments(identity, []), image.strict_generation_output(
            selection=f"openai/{identity}", validate=lambda: None, sink=lambda data: saved.append(data) or identity):
            value = base64.b64encode(identity.encode()).decode()
            image._set_pending_image(value)
            barrier.wait(3)
            assert image._save_image_to_disk(value) == identity
            return saved, image.get_and_clear_last_image(), image._get_configured_selection()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, ["one", "two"]))
    assert results == [([b"one"], base64.b64encode(b"one").decode(), "openai/one"),
                       ([b"two"], base64.b64encode(b"two").decode(), "openai/two")]
    assert image._last_generated_image == "legacy"


@pytest.mark.parametrize("module", [image, video], ids=["image", "video"])
def test_strict_sink_revalidates_and_never_falls_back_to_legacy_file_save(module, monkeypatch):
    from row_bot.application import generated_media
    monkeypatch.setattr(generated_media, "save_generated_output", lambda *args, **kwargs: pytest.fail("legacy file save"))
    revoked = [False]
    saved = []
    sentinel = PermissionError("synthetic revoked")

    def validate():
        if revoked[0]:
            raise sentinel

    with prepared_attachments("synthetic", []), module.strict_generation_output(
            selection="provider/model", validate=validate, sink=lambda data: saved.append(data) or "owned"):
        revoked[0] = True
        with pytest.raises(PermissionError) as caught:
            if module is image:
                module._save_image_to_disk(base64.b64encode(b"image").decode())
            else:
                module._save_video_to_disk(b"video")
        assert caught.value is sentinel and not saved


def test_actual_image_generator_uses_captured_model_and_owned_sink_after_config_change(monkeypatch):
    calls = []
    saved = []

    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(b"image").decode())])

    def client():
        monkeypatch.setattr(image.registry, "get_tool_config", lambda *args: "foreign/changed")
        return SimpleNamespace(images=SimpleNamespace(generate=generate)), "OpenAI", "openai"

    monkeypatch.setattr(image, "_get_client", client)
    with prepared_attachments("synthetic", []), image.strict_generation_output(
            selection="openai/reviewed", validate=lambda: None, sink=lambda data: saved.append(data) or "owned"):
        result = image._generate_image("synthetic prompt")
    assert "successfully" in result and calls[0]["model"] == "reviewed" and saved == [b"image"]


def test_actual_image_generator_propagates_uncertain_provider_without_retry(monkeypatch):
    calls = []
    sentinel = TimeoutError("synthetic transport uncertainty")

    def generate(**kwargs):
        calls.append(kwargs)
        raise sentinel

    monkeypatch.setattr(image, "_get_client", lambda: (SimpleNamespace(images=SimpleNamespace(generate=generate)), "OpenAI", "openai"))
    with image.strict_generation_output(selection="openai/reviewed", validate=lambda: None,
                                        sink=lambda data: pytest.fail("unexpected output")):
        with pytest.raises(TimeoutError) as caught:
            image._generate_image("synthetic prompt")
    assert caught.value is sentinel and len(calls) == 1


def test_actual_provider_revalidation_after_factory_prevents_image_request(monkeypatch):
    revoked = [False]
    sentinel = PermissionError("revoked at factory")

    def validate():
        if revoked[0]:
            raise sentinel

    def client():
        revoked[0] = True
        return SimpleNamespace(images=SimpleNamespace(generate=lambda **kwargs: pytest.fail("provider request"))), "OpenAI", "openai"

    monkeypatch.setattr(image, "_get_client", client)
    with image.strict_generation_output(selection="openai/reviewed", validate=validate, sink=lambda data: "owned"):
        with pytest.raises(PermissionError) as caught:
            image._generate_image("synthetic prompt")
    assert caught.value is sentinel


def test_image_decoding_budget_rejects_before_sink():
    with image.strict_generation_output(selection="openai/reviewed", validate=lambda: None,
                                        sink=lambda data: pytest.fail("oversized output")):
        with pytest.raises(ValueError, match="generated_media_too_large"):
            image._save_image_to_disk("x" * (4 * ((16 * 1024 * 1024 + 2) // 3) + 1))


@pytest.fixture
def hatch_owner(tmp_path, monkeypatch):
    from PIL import Image
    from row_bot.buddy import assets, config, hatch, client_hatch
    monkeypatch.setattr(config, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "_BUDDY_CONFIG_PATH", tmp_path / "buddy_config.json")
    monkeypatch.setattr(hatch, "_DATA_DIR", tmp_path / "buddy_hatches")
    monkeypatch.setattr(assets, "_USER_PACKS_DIR", tmp_path / "buddy" / "packs")
    monkeypatch.setattr(assets, "_BUILTIN_PACKS_DIR", tmp_path / "builtins")
    with hatch._JOB_LOCK:
        hatch._CURRENT_JOB.clear()
    class ImmediateThread:
        def __init__(self, *, target, args, **kwargs):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(hatch.threading, "Thread", ImmediateThread)
    png = io.BytesIO()
    Image.new("RGBA", (24, 24), (10, 20, 30, 255)).save(png, format="PNG")
    calls, progress, proofs, draft_proofs = [], [], [], []
    def generate(*args, **kwargs):
        calls.append(("image", image._get_configured_selection()))
        image._save_image_to_disk(base64.b64encode(png.getvalue()).decode())
        return "synthetic success"
    def animate(*args, **kwargs):
        calls.append(("motion", video._get_configured_selection()))
        assert kwargs["image_source"] == "buddy-reference.png"
        assert image._resolve_image_source("buddy-reference.png").startswith(b"\x89PNG")
        video._save_video_to_disk(b"\x00\x00\x00\x18ftypmp42synthetic")
        return "synthetic success"
    monkeypatch.setattr(image, "_generate_image", generate)
    monkeypatch.setattr(video, "_animate_image", animate)
    def start(**kwargs):
        arguments = dict(command_id=str(uuid4()), prompt="A synthetic Buddy", mode="full",
            expected_config_revision=config.read_buddy_config_revision()[1], image_selection="openai/reviewed-image",
            video_selection="xai/reviewed-video", validate=lambda: None, validate_provider=lambda kind, selection: None,
            checkpoint=progress.append, checkpoint_config=proofs.append,
            checkpoint_draft=lambda stage, proof: draft_proofs.append((stage, proof)))
        arguments.update(kwargs)
        client_hatch.start_hatch(**arguments)
        return client_hatch.read_result(arguments["command_id"], validate=lambda: None)
    yield SimpleNamespace(start=start, calls=calls, progress=progress, proofs=proofs, config=config,
        assets=assets, hatch=hatch, client=client_hatch, root=tmp_path, draft_proofs=draft_proofs, png=png.getvalue())
    with hatch._JOB_LOCK:
        hatch._CURRENT_JOB.clear()


def test_full_hatch_stages_six_clips_then_selects_new_pack_once(hatch_owner):
    r = hatch_owner
    result = r.start()
    assert result.status == "completed" and result.selected and result.completed_clips == 6
    assert len(r.calls) == 7 and result.retained_copy
    assert r.config.get_buddy_config()["pack_id"] == result.pack_id
    assert r.assets.load_buddy_pack(result.pack_id, strict=True).status == "available"
    assert [event.stage for event in r.progress].index("pack_publication_prepared") < [event.stage for event in r.progress].index("pack_published")
    assert r.proofs and r.config.read_buddy_config_recovery(r.proofs[0]) == "applied"
    before = list(r.calls)
    repeated = r.start(command_id=result.command_id, expected_config_revision="missing")
    assert repeated == result and r.calls == before


def test_motion_failure_keeps_old_config_and_retained_still_without_provider_retry(hatch_owner, monkeypatch):
    r = hatch_owner
    r.config.save_buddy_config({"pack_id": "glyph", "private": "retained"})
    before = r.config._BUDDY_CONFIG_PATH.read_bytes()
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(1)
        raise TimeoutError("private provider body")
    monkeypatch.setattr(video, "_animate_image", fail)
    result = r.start()
    assert result.status == "uncertain" and result.code == "hatch_generation_uncertain"
    assert result.retained_copy and not result.selected and len(attempts) == 1
    assert r.config._BUDDY_CONFIG_PATH.read_bytes() == before
    assert "private provider body" not in json.dumps(result.__dict__)
    assert (r.hatch._DATA_DIR / result.command_id / "candidate" / "preview.png").exists()


def test_changed_config_after_generation_preserves_new_pack_without_overwriting_selection(hatch_owner):
    r = hatch_owner
    def checkpoint(event):
        r.progress.append(event)
        if event.stage == "pack_published":
            r.config.save_buddy_config({"pack_id": "different", "private": "latest"})
    result = r.start(checkpoint=checkpoint)
    assert result.status == "partial" and not result.selected and result.pack_id
    assert result.code == "buddy_revision_conflict"
    assert r.config.get_buddy_config()["pack_id"] == "different"
    assert r.assets.load_buddy_pack(result.pack_id, strict=True).status == "available"


def test_cancellation_before_first_provider_keeps_no_generation_effect(hatch_owner):
    r = hatch_owner
    def checkpoint(event):
        if event.stage == "starting":
            r.hatch.cancel_client_hatch(event.job_id, validate=lambda: None)
    result = r.start(checkpoint=checkpoint)
    assert result.status == "cancelled" and not r.calls and not result.selected
    assert not r.config._BUDDY_CONFIG_PATH.exists()


def test_draft_status_after_process_loss_is_uncertain_never_auto_restarted(hatch_owner):
    r = hatch_owner
    result = r.start()
    path = r.hatch._DATA_DIR / result.command_id / "manifest.json"
    saved = json.loads(path.read_text())
    saved.update(status="running", stage="motion_provider_started")
    path.write_text(json.dumps(saved))
    with r.hatch._JOB_LOCK:
        r.hatch._CURRENT_JOB.clear()
    count = len(r.calls)
    recovered = r.start(command_id=result.command_id)
    assert recovered.status == "uncertain" and recovered.code == "hatch_worker_unavailable"
    assert len(r.calls) == count


def test_explicit_still_recovery_uses_retained_image_without_repeating_provider(hatch_owner, monkeypatch):
    r = hatch_owner
    monkeypatch.setattr(video, "_animate_image", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))
    first = r.start()
    assert first.has_still and first.status == "uncertain"
    count = len(r.calls)
    recovered = r.start(mode="still", source_command_id=first.command_id)
    assert recovered.status == "completed" and recovered.selected and recovered.has_still
    assert recovered.command_id != first.command_id and len(r.calls) == count
    assert (r.hatch._DATA_DIR / first.command_id / "candidate" / "preview.png").read_bytes() == r.png
    assert r.assets.load_buddy_pack(recovered.pack_id, strict=True).runtime == "generated_still"


def test_motion_retry_creates_distinct_pack_preserving_original_active_bytes(hatch_owner):
    r = hatch_owner
    first = r.start()
    original = r.assets._USER_PACKS_DIR / first.pack_id
    before = {path.name: path.read_bytes() for path in original.iterdir()}
    descriptor = r.client.client_service._pack(first.pack_id)[0]
    second = r.start(mode="motion", source_pack_id=first.pack_id, source_pack_revision=descriptor.revision)
    assert second.status == "completed" and second.pack_id != first.pack_id
    assert [kind for kind, _ in r.calls].count("image") == 1 and len(r.calls) == 13
    assert {path.name: path.read_bytes() for path in original.iterdir()} == before


def test_draft_checkpoint_failure_prevents_provider_and_retains_exact_private_candidate(hatch_owner):
    r = hatch_owner
    proofs = []
    command = str(uuid4())
    def fail(stage, proof):
        proofs.append(proof)
        raise OSError("synthetic admission failure")
    r.client.start_hatch(command_id=command, prompt="synthetic", mode="full",
        expected_config_revision="missing", image_selection="openai/image", video_selection="xai/video",
        validate=lambda: None, validate_provider=lambda *args: None, checkpoint=lambda event: None,
        checkpoint_config=lambda proof: None, checkpoint_draft=fail)
    assert not r.calls and proofs
    assert r.client.read_draft_recovery(command, proofs[0], validate=lambda: None) == "not_applied"
    with pytest.raises((ValueError, OSError)):
        r.start(command_id=command)
    assert not r.calls


def test_cancel_during_provider_keeps_late_bytes_out_of_candidate_and_config(hatch_owner, monkeypatch):
    r = hatch_owner
    def cancelled_provider(*args, **kwargs):
        r.hatch.cancel_client_hatch(r.hatch.get_hatch_generation_status()["id"], validate=lambda: None)
        image._save_image_to_disk(base64.b64encode(r.png).decode())
    monkeypatch.setattr(image, "_generate_image", cancelled_provider)
    result = r.start()
    assert result.status == "cancelled" and not result.has_still and not result.selected
    assert not r.config._BUDDY_CONFIG_PATH.exists()


def test_unknown_candidate_bytes_block_publication_and_are_preserved(hatch_owner):
    r = hatch_owner
    unknown = []
    def checkpoint(event):
        if event.stage == "motion_retained" and event.completed_clips == 6:
            path = r.hatch._DATA_DIR / event.command_id / "candidate" / "user-note.txt"
            path.write_text("preserve me")
            unknown.append(path)
    result = r.start(checkpoint=checkpoint)
    assert result.status == "partial" and not result.pack_id and not result.selected
    assert unknown[0].read_text() == "preserve me"


def test_extreme_image_dimension_is_rejected_before_large_canvas_allocation():
    from PIL import Image
    from row_bot.buddy.client_hatch import _png
    output = io.BytesIO()
    Image.new("RGB", (4097, 1)).save(output, format="PNG")
    with pytest.raises(ValueError, match="generated_media_too_large"):
        _png(output.getvalue(), motion=True)


def test_retirement_preserves_unknown_bytes_and_exact_private_recovery(hatch_owner):
    r = hatch_owner
    result = r.start()
    # Keep selection elsewhere; retirement must not rewrite unrelated config.
    r.config.save_buddy_config({"pack_id": "glyph"})
    path = r.assets._USER_PACKS_DIR / result.pack_id
    (path / "user-note.txt").write_text("preserve this file")
    descriptor = r.client.client_service._pack(result.pack_id)[0]
    proofs = []
    outcome = r.client.retire_pack(result.pack_id, command_id=str(uuid4()),
        expected_pack_revision=descriptor.revision, expected_config_revision=r.config.read_buddy_config_revision()[1],
        validate=lambda: None, checkpoint=r.progress.append, checkpoint_config=r.proofs.append,
        checkpoint_retirement=proofs.append)
    assert outcome == {"status": "removed", "pack_id": result.pack_id, "retained_copy": True, "config_changed": False}
    assert not path.exists()
    assert (r.hatch._DATA_DIR / "retired-packs" / proofs[0].command_id / "user-note.txt").read_text() == "preserve this file"
    assert r.client.read_retirement_recovery(proofs[0], validate=lambda: None) == "applied"
    path.mkdir()
    assert r.client.read_retirement_recovery(proofs[0], validate=lambda: None) == "conflict"


def test_retirement_rechecks_revocation_after_checkpoint_preserving_pack(hatch_owner):
    r = hatch_owner
    result = r.start()
    r.config.save_buddy_config({"pack_id": "glyph"})
    descriptor = r.client.client_service._pack(result.pack_id)[0]
    revoked = [False]
    proofs = []
    def validate():
        if revoked[0]:
            raise PermissionError("synthetic authority rejection")
    def checkpoint(proof):
        proofs.append(proof)
        revoked[0] = True
    with pytest.raises(PermissionError):
        r.client.retire_pack(result.pack_id, command_id=str(uuid4()), expected_pack_revision=descriptor.revision,
            expected_config_revision=r.config.read_buddy_config_revision()[1], validate=validate,
            checkpoint=r.progress.append, checkpoint_config=r.proofs.append, checkpoint_retirement=checkpoint)
    assert (r.assets._USER_PACKS_DIR / result.pack_id).exists()
    assert r.client.read_retirement_recovery(proofs[0], validate=lambda: None) == "not_applied"


def test_selected_pack_retirement_switches_to_valid_default_with_config_proof(hatch_owner):
    r = hatch_owner
    default = r.assets._BUILTIN_PACKS_DIR / "glyph"
    default.mkdir(parents=True)
    (default / "manifest.json").write_text(json.dumps({"id": "glyph", "name": "Default", "runtime": "generated_still", "preview": "preview.png"}))
    (default / "preview.png").write_bytes(r.png)
    result = r.start()
    proofs = []
    r.client.retire_pack(result.pack_id, command_id=str(uuid4()),
        expected_pack_revision=r.client.client_service._pack(result.pack_id)[0].revision,
        expected_config_revision=r.config.read_buddy_config_revision()[1], validate=lambda: None,
        checkpoint=r.progress.append, checkpoint_config=proofs.append, checkpoint_retirement=lambda proof: None)
    assert r.config.get_buddy_config()["pack_id"] == "glyph"
    assert r.config.read_buddy_config_recovery(proofs[0]) == "applied"


def test_pack_change_at_removal_checkpoint_is_retained_and_not_retired(hatch_owner):
    r = hatch_owner
    result = r.start()
    r.config.save_buddy_config({"pack_id": "glyph"})
    path = r.assets._USER_PACKS_DIR / result.pack_id
    descriptor = r.client.client_service._pack(result.pack_id)[0]
    def change(proof):
        (path / "preview.png").write_bytes(r.png + b"changed")
    with pytest.raises(ValueError, match="hatch_storage_changed"):
        r.client.retire_pack(result.pack_id, command_id=str(uuid4()), expected_pack_revision=descriptor.revision,
            expected_config_revision=r.config.read_buddy_config_revision()[1], validate=lambda: None,
            checkpoint=r.progress.append, checkpoint_config=r.proofs.append, checkpoint_retirement=change)
    assert (path / "preview.png").read_bytes().endswith(b"changed")


def test_duplicate_admitted_command_before_worker_start_reads_identity_without_starting_again(hatch_owner, monkeypatch):
    r = hatch_owner
    starts = []
    class DeferredThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            starts.append(1)
    monkeypatch.setattr(r.hatch.threading, "Thread", DeferredThread)
    command = str(uuid4())
    first = r.start(command_id=command)
    second = r.start(command_id=command)
    assert first == second and first.job_id == "buddy-hatch-" + command
    assert starts == [1] and not r.calls


def test_candidate_change_after_publication_checkpoint_prevents_selection(hatch_owner):
    r = hatch_owner
    changed = []
    def checkpoint(event):
        if event.stage == "pack_publication_prepared":
            path = r.hatch._DATA_DIR / event.command_id / "candidate" / "preview.png"
            path.write_bytes(r.png + b"external change")
            changed.append(path)
    result = r.start(checkpoint=checkpoint)
    assert result.status == "uncertain" and not result.selected and not result.pack_id
    assert changed[0].read_bytes().endswith(b"external change")
    assert not r.config._BUDDY_CONFIG_PATH.exists()


@pytest.mark.parametrize("field,value", [("stage", []), ("status", {}), ("completed_clips", True), ("pack_id", "../../foreign")])
def test_saved_result_rejects_corrupt_public_descriptor(hatch_owner, field, value):
    r = hatch_owner
    result = r.start()
    path = r.hatch._DATA_DIR / result.command_id / "manifest.json"
    saved = json.loads(path.read_text())
    saved[field] = value
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="hatch_result_unavailable"):
        r.client.read_result(result.command_id, validate=lambda: None)
