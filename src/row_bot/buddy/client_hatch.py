"""Explicit Hatch adapter using the canonical job, draft, pack and tool owners.

A full Hatch paints the character on its family's pose guide, then paints six
expression edits of that painting; ``living_hatch`` turns them into a living
pack (cut-out art, rig, expression patches). "motion" (Bring to life) paints
only the expressions for an existing still; "still" makes a pack from a
retained still with no provider call.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import threading
from itertools import islice
from typing import Any
from uuid import UUID, uuid4

from . import assets, config, hatch, client_service, living_hatch
from row_bot.developer.client_workspace import _directory_identity, _empty_parent_guard
from row_bot.developer.edits import _rename_edit_no_replace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HatchProgress:
    stage: str
    command_id: str
    job_id: str
    pack_id: str
    completed_clips: int
    total_clips: int
    code: str = ""


@dataclass(frozen=True)
class HatchResult:
    schema_version: int
    command_id: str
    job_id: str
    status: str
    stage: str
    pack_id: str | None
    selected: bool
    completed_clips: int
    total_clips: int
    code: str
    retained_copy: bool
    has_still: bool = False


@dataclass(frozen=True)
class HatchPackRetirement:
    command_id: str
    pack_id: str
    pack_revision: str
    parent_identity: str
    pack_identity: str


def read_draft_recovery(command_id: str, proof: Any, *, validate: Callable[[], None]) -> str:
    """Inspect an application-held draft proof; do not resume generation."""
    validate()
    result = config._read_json_recovery(hatch._DATA_DIR / _command(command_id), "manifest.json", proof)
    validate()
    return result


def read_retirement_recovery(proof: HatchPackRetirement, *, validate: Callable[[], None]) -> str:
    """Classify exact directory retirement without deleting or recreating it."""
    validate()
    if not isinstance(proof, HatchPackRetirement):
        return "conflict"
    command_id = _command(proof.command_id)
    if (not isinstance(proof.pack_id, str) or not 1 <= len(proof.pack_id) <= 256
            or any(not (character.isascii() and (character.isalnum() or character in "_-")) for character in proof.pack_id)):
        return "conflict"
    source = assets._USER_PACKS_DIR / proof.pack_id
    retained = hatch._DATA_DIR / "retired-packs" / command_id
    try:
        with _empty_parent_guard(assets._USER_PACKS_DIR, proof.parent_identity):
            if retained.exists():
                with _empty_parent_guard(retained, proof.pack_identity):
                    result = "applied" if not source.exists() else "conflict"
            elif _directory_identity(source, parent=True) == proof.pack_identity:
                result = "not_applied" if client_service._pack(proof.pack_id)[0].revision == proof.pack_revision else "conflict"
            else:
                result = "conflict"
    except (OSError, ValueError):
        result = "conflict"
    validate()
    return result


def retire_pack(pack_id: str, *, command_id: str, expected_pack_revision: str,
                expected_config_revision: str, validate: Callable[[], None],
                checkpoint: Callable[[HatchProgress], None], checkpoint_config: Callable[[Any], None],
                checkpoint_retirement: Callable[[HatchPackRetirement], None]) -> dict:
    """Remove a generated look from discovery by retaining its entire directory.

    The application must obtain the ordinary destructive-action approval first.
    Unknown files are retained with the pack, never recursively deleted.
    """
    command_id = _command(command_id)
    validate()
    descriptor, _, _ = client_service._pack(pack_id)
    if not descriptor.generated or descriptor.revision != expected_pack_revision:
        raise ValueError("buddy_revision_conflict")
    job_id = "buddy-hatch-" + command_id
    with hatch._JOB_LOCK:
        validate()
        if hatch._CURRENT_JOB.get("status") in hatch._RUNNING_JOB_STATES:
            raise ValueError("hatch_job_busy")
        checkpoint(HatchProgress("removal_admitted", command_id, job_id, pack_id, 0, 0))
        hatch._CURRENT_JOB.clear()
        hatch._CURRENT_JOB.update(id=job_id, status="running", phase="removing", pack_id=pack_id,
                                  message="Removing generated Buddy look")
    config_changed = False
    proof = None
    try:
        with config._lock:
            validate()
            saved, revision = config.read_buddy_config_revision()
            if revision != expected_config_revision:
                raise ValueError("buddy_revision_conflict")
            if saved.get("pack_id") == pack_id:
                client_service.update_buddy({"pack_id": "glyph"}, expected_revision=revision,
                    command_id=command_id, validate=validate, checkpoint=checkpoint_config)
                config_changed = True
            descriptor, _, _ = client_service._pack(pack_id)
            if descriptor.revision != expected_pack_revision:
                raise ValueError("buddy_revision_conflict")
            source = assets._USER_PACKS_DIR / pack_id
            retained = hatch._DATA_DIR / "retired-packs" / command_id
            identity = _directory_identity(source, parent=True)
            parent_identity = _directory_identity(assets._USER_PACKS_DIR, parent=True)
            proof = HatchPackRetirement(command_id, pack_id, expected_pack_revision, parent_identity, identity)
            with _directory(retained.parent) as destination, _empty_parent_guard(assets._USER_PACKS_DIR, parent_identity) as parent:
                checkpoint_retirement(proof)
                validate()
                if (_directory_identity(source, parent=True) != identity
                        or _directory_identity(assets._USER_PACKS_DIR, parent=True) != parent_identity
                        or client_service._pack(pack_id)[0].revision != expected_pack_revision):
                    raise ValueError("hatch_storage_changed")
                _rename_edit_no_replace(pack_id if parent is not None else source,
                    command_id if destination is not None else retained,
                    src_dir_fd=parent, dst_dir_fd=destination)
                if _directory_identity(retained, parent=True) != identity:
                    raise ValueError("hatch_storage_changed")
        checkpoint(HatchProgress("pack_retired", command_id, job_id, pack_id, 0, 0))
        hatch._update_hatch_job(job_id, status="completed", phase="removed", message="Generated look removed; copy retained")
        return {"status": "removed", "pack_id": pack_id, "retained_copy": True, "config_changed": config_changed}
    except Exception:
        hatch._update_hatch_job(job_id, status="uncertain" if proof is not None else "partial" if config_changed else "failed",
            phase="needs_attention", message="Buddy removal needs review", error="hatch_removal_incomplete")
        # Private callbacks already recorded any admitted publication. Root may
        # classify the exact recovery proof; never repeat a directory retirement.
        raise


def _command(value: str) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise client_service.BuddyClientError("invalid_hatch_command")
    return value


@contextmanager
def _directory(path: Path, *, exclusive: bool = False):
    """Create only this owner-selected directory; retain ancestor protection."""
    with ExitStack() as stack:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("hatch_storage_unavailable")
        missing = []
        parent = path
        while not parent.exists():
            missing.append(parent.name)
            parent = parent.parent
        descriptor = stack.enter_context(_empty_parent_guard(parent, _directory_identity(parent, parent=True)))
        if exclusive and not missing:
            raise FileExistsError
        for child in reversed(missing):
            if descriptor is None:
                (parent / child).mkdir()
                parent = parent / child
                descriptor = stack.enter_context(_empty_parent_guard(parent, _directory_identity(parent, parent=True)))
            else:
                os.mkdir(child, mode=0o700, dir_fd=descriptor)
                descriptor = os.open(child, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                stack.callback(os.close, descriptor)
                parent = parent / child
        yield descriptor


def _write_new(root: Path, filename: str, data: bytes, validate: Callable[[], None], *, root_identity: str) -> None:
    if not isinstance(data, bytes) or not data or len(data) > 64 * 1024 * 1024:
        raise ValueError("generated_media_too_large")
    if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("hatch_storage_unavailable")
    validate()
    with _empty_parent_guard(root, root_identity) as parent:
        fd = os.open(filename if parent is not None else root / filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if assets.read_buddy_asset(root / filename, root=root) != data:
            raise ValueError("hatch_storage_changed")


def _png(data: bytes) -> bytes:
    from PIL import Image
    if not isinstance(data, bytes) or not 0 < len(data) <= 16 * 1024 * 1024:
        raise ValueError("generated_media_too_large")
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 16_777_216 or min(source.size) < 1 or max(source.size) > 4096:
            raise ValueError("generated_media_too_large")
        image = source.convert("RGBA")
        output = io.BytesIO()
        image.save(output, format="PNG")
        value = output.getvalue()
    if len(value) > 16 * 1024 * 1024:
        raise ValueError("generated_media_too_large")
    return value


def _edit_source(data: bytes) -> bytes:
    """The still as an opaque square on the key colour: providers treat transparency as "edit here"."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as source:
        image = source.convert("RGBA")
    if image.getchannel("A").getextrema()[0] < 250:
        side = max(image.size)
        canvas = Image.new("RGBA", (side, side), (*living_hatch.KEY_COLOUR, 255))
        canvas.alpha_composite(image, ((side - image.width) // 2, (side - image.height) // 2))
        image = canvas
    image = ImageOps.fit(image.convert("RGB"), (1024, 1024), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _total(mode: str) -> int:
    return {"full": living_hatch.FULL_STEPS, "motion": len(living_hatch.EXPRESSIONS)}.get(mode, 0)


def _public(saved: dict) -> HatchResult:
    # completed_clips/total_clips are the wire names; they count painted images.
    return HatchResult(1, saved["command_id"], saved["job_id"], saved["status"], saved["stage"],
        saved.get("pack_id") or None, bool(saved.get("selected")), int(saved.get("completed_clips", 0)),
        int(saved.get("total_clips", living_hatch.FULL_STEPS)), saved.get("code", ""),
        bool(saved.get("retained_copy")))


# Hatches from before 5.3.4 recorded motion stages; they stay readable for recovery.
_STAGES = frozenset({"starting", "image_provider_started", "image_retained", "source_retained",
    "expression_provider_started", "expression_retained", "rig_prepared", "motion_provider_started",
    "motion_retained", "pack_publication_prepared", "pack_published", "completed", "stopped", "needs_attention"})


def _saved(command_id: str) -> dict:
    root = hatch._DATA_DIR / command_id
    saved = json.loads(assets.read_buddy_asset(root / "manifest.json", root=root, limit=65536))
    if (not isinstance(saved, dict) or saved.get("command_id") != command_id or type(saved.get("schema_version")) is not int or saved["schema_version"] != 1
            or saved.get("job_id") != "buddy-hatch-" + command_id
            or any(not isinstance(saved.get(key), str) for key in ("pack_id", "status", "stage", "code"))
            or saved.get("pack_id") not in {"", "hatch-" + UUID(command_id).hex}
            or saved.get("status") not in {"queued", "running", "cancelling", "completed", "partial", "failed", "uncertain", "cancelled"}
            or saved.get("stage") not in _STAGES
            or type(saved.get("completed_clips")) is not int or not 0 <= saved["completed_clips"] <= living_hatch.FULL_STEPS
            or type(saved.get("selected")) is not bool or type(saved.get("retained_copy")) is not bool
            or saved.get("code") not in {"", "hatch_cancelled", "hatch_job_changed", "buddy_revision_conflict",
                "file_revision_conflict", "hatch_storage_changed", "generated_media_too_large", "hatch_generation_uncertain", "hatch_incomplete"}):
        raise ValueError("hatch_result_unavailable")
    return saved


def _retained_still(command_id: str, *, validate: Callable[[], None]) -> bytes:
    validate()
    saved = _saved(_command(command_id))
    root = assets._USER_PACKS_DIR / saved["pack_id"] if saved.get("pack_id") else hatch._DATA_DIR / command_id / "candidate"
    data = assets.read_buddy_asset(root / "preview.png", root=root, limit=16 * 1024 * 1024)
    if not isinstance(saved.get("preview_digest"), str) or hashlib.sha256(data).hexdigest() != saved["preview_digest"]:
        raise ValueError("hatch_source_changed")
    validate()
    return data


def read_result(command_id: str, *, validate: Callable[[], None]) -> HatchResult:
    validate()
    command_id = _command(command_id)
    active = hatch.get_hatch_generation_status()
    if (not (hatch._DATA_DIR / command_id / "manifest.json").exists()
            and active.get("id") == "buddy-hatch-" + command_id and active.get("status") in hatch._RUNNING_JOB_STATES):
        validate()
        return HatchResult(1, command_id, active["id"], active["status"], "starting", None, False, 0,
                           int(active.get("total_clips", living_hatch.FULL_STEPS)), "", False)
    saved = _saved(command_id)
    if saved.get("status") in {"queued", "running", "cancelling"} and (active.get("id") != saved.get("job_id")
            or active.get("status") not in hatch._RUNNING_JOB_STATES):
        saved = {**saved, "status": "uncertain", "code": "hatch_worker_unavailable"}
    elif active.get("id") == saved.get("job_id") and active.get("status") == "cancelling":
        saved = {**saved, "status": "cancelling"}
    validate()
    result = _public(saved)
    try:
        _retained_still(command_id, validate=lambda: None)
        result = HatchResult(**{**asdict(result), "has_still": True})
    except (ValueError, OSError):
        pass
    validate()
    return result


def start_hatch(*, command_id: str, prompt: str, mode: str, expected_config_revision: str,
                image_selection: str, validate: Callable[[], None],
                validate_provider: Callable[[str, str], None], checkpoint: Callable[[HatchProgress], None],
                checkpoint_config: Callable[[Any], None], checkpoint_draft: Callable[[str, Any], None], source_pack_id: str = "",
                source_pack_revision: str = "", source_command_id: str = "", expected_source_digest: str | None = None,
                family: str = "auto") -> HatchResult:
    command_id = _command(command_id)
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000 or mode not in {"full", "motion", "still"}:
        raise ValueError("invalid_hatch_request")
    if mode in {"full", "motion"} and (not isinstance(image_selection, str) or not 1 <= len(image_selection) <= 256
                                       or "/" not in image_selection):
        raise ValueError("invalid_media_selection")
    if family not in {"auto", *living_hatch.FAMILIES}:
        raise ValueError("invalid_hatch_request")
    validate()
    # Canonical command receipts own payload idempotency. Existing durable work
    # is recovery only and must not fail merely because its own selection saved.
    current_job = hatch.get_hatch_generation_status()
    if ((hatch._DATA_DIR / command_id).exists()
            or current_job.get("id") == "buddy-hatch-" + command_id):
        return read_result(command_id, validate=validate)
    _, current_revision = config.read_buddy_config_revision()
    if current_revision != expected_config_revision:
        raise ValueError("buddy_revision_conflict")
    source = b""
    if mode in {"motion", "still"}:
        if source_command_id:
            source = _retained_still(source_command_id, validate=validate)
        else:
            source, _ = client_service.read_buddy_media(source_pack_id, "preview",
                expected_revision=source_pack_revision, validate=validate)
        if expected_source_digest is not None and hashlib.sha256(source).hexdigest() != expected_source_digest:
            raise ValueError("hatch_source_changed")
    # Never repeat a provider-started command merely because its response was lost.
    pack_id = "hatch-" + UUID(command_id).hex

    def admitted(job_id: str) -> None:
        validate()
        checkpoint(HatchProgress("job_admitted", command_id, job_id, pack_id, 0, _total(mode)))

    def run(job_id: str, cancellation: threading.Event) -> None:
        _run(command_id=command_id, job_id=job_id, pack_id=pack_id, prompt=prompt.strip(), mode=mode,
            source=source, expected_config_revision=expected_config_revision, image_selection=image_selection,
            family=living_hatch.choose_family(prompt, None if family == "auto" else family),
            validate=validate, validate_provider=validate_provider,
            checkpoint=checkpoint, checkpoint_config=checkpoint_config, checkpoint_draft=checkpoint_draft,
            cancellation=cancellation)

    started = hatch.start_hatch_generation_job(prompt, mode=mode, total_steps=_total(mode),
        _client_runner=run, _command_id=command_id, _validate=validate, _checkpoint=admitted)
    return HatchResult(1, command_id, started["id"], "queued", "starting", None, False, 0,
                       _total(mode), "", False)


def _run(*, command_id, job_id, pack_id, prompt, mode, source, expected_config_revision,
         image_selection, family, validate, validate_provider, checkpoint, checkpoint_config,
         checkpoint_draft, cancellation):
    from row_bot.application.attachment_context import prepared_attachments, current_caches
    from row_bot.tools import image_gen_tool
    from row_bot.providers.media_auth import validate_auth
    draft = hatch._DATA_DIR / command_id
    candidate = draft / "candidate"
    total = _total(mode)
    saved = {"schema_version": 1, "command_id": command_id, "job_id": job_id, "prompt": prompt,
        "status": "running", "stage": "starting", "pack_id": "", "selected": False,
        "completed_clips": 0, "total_clips": total, "code": "", "retained_copy": False}
    revision = "missing"
    provider_pending = False
    proof_received = False
    candidate_identity = ""
    draft_identity = ""
    painted = 0
    edits: dict[str, bytes] = {}
    preview = b""
    pack_files: dict[str, bytes] = {}

    def authority():
        validate()
        if cancellation.is_set():
            raise ValueError("hatch_cancelled")
        with hatch._JOB_LOCK:
            if hatch._CURRENT_JOB.get("id") != job_id:
                raise ValueError("hatch_job_changed")

    def record(stage, *, status="running", code=""):
        nonlocal revision
        saved.update(stage=stage, status=status, code=code, completed_clips=painted)
        encoded = json.dumps(saved, sort_keys=True, ensure_ascii=False).encode("utf-8")
        # Private draft publication is a canonical owner update, not authority
        # to continue provider or selected-config effects after cancellation.
        def storage_current():
            if _directory_identity(draft, parent=True) != draft_identity:
                raise ValueError("hatch_storage_changed")
        revision = config._publish_json_revision(draft, "manifest.json", encoded, expected_revision=revision,
            command_id=str(uuid4()), validate=storage_current, checkpoint=lambda proof: checkpoint_draft(stage, proof))
        hatch._update_hatch_job(job_id, status=status, phase=stage, pack_id=saved["pack_id"] or pack_id,
            completed_clips=painted, total_clips=total, message=stage.replace("_", " "), error=code)
        checkpoint(HatchProgress(stage, command_id, job_id, saved["pack_id"] or pack_id, painted, total, code))

    def paint(stage: str, prompt_text: str, image_source: str, sink: Callable[[bytes], str]) -> None:
        """One image edit on the person's image model, written only through the owned sink.

        Credentials are checked before the stage says a provider started; the call stays
        pending (an uncertain outcome) until the caller has checked its result."""
        nonlocal provider_pending
        authority()
        auth = validate_provider("image", image_selection)
        if auth is not None:
            validate_auth(auth, image_selection)
        record(stage)
        provider_pending = True
        with image_gen_tool.strict_generation_output(selection=image_selection,
                validate=lambda: (authority(), validate_provider("image", image_selection)), sink=sink,
                **({"auth": auth} if auth is not None else {})):
            image_gen_tool._edit_image(prompt_text, image_source=image_source, size="1024x1024")

    def publish_pack():
        nonlocal proof_received
        authority()
        if _directory_identity(candidate, parent=True) != candidate_identity:
            raise ValueError("hatch_storage_changed")
        manifest = {"schema": 2, "id": pack_id, "name": hatch._hatch_pack_name(prompt, pack_id)[:256],
            "runtime": assets.LIVING_RUNTIME, "version": "1.0.0", "art": "art.png", "rig": "rig.json",
            "preview": "art.png", "prompt": prompt, **({"patches": "patches.png"} if "patches.png" in pack_files else {})}
        _write_new(candidate, "manifest.json", json.dumps(manifest, ensure_ascii=False).encode(), authority,
                   root_identity=candidate_identity)
        entries = list(islice(candidate.iterdir(), 9))
        expected_names = {"manifest.json", "preview.png", *pack_files}
        if {entry.name for entry in entries} != expected_names:
            raise ValueError("hatch_storage_changed")
        fingerprints = {path.name: hashlib.sha256(assets.read_buddy_asset(path, root=candidate)).hexdigest()
                        for path in entries}
        saved["candidate_digest"] = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
        saved["candidate_identity"] = candidate_identity
        record("pack_publication_prepared")
        proof_received = True
        with _directory(assets._USER_PACKS_DIR) as target_parent, _empty_parent_guard(draft, _directory_identity(draft, parent=True)) as parent:
            authority()
            if (_directory_identity(candidate, parent=True) != candidate_identity
                    or {entry.name for entry in islice(candidate.iterdir(), 9)} != expected_names):
                raise ValueError("hatch_storage_changed")
            for name, digest in fingerprints.items():
                if hashlib.sha256(assets.read_buddy_asset(candidate / name, root=candidate)).hexdigest() != digest:
                    raise ValueError("hatch_storage_changed")
            _rename_edit_no_replace("candidate" if parent is not None else candidate,
                pack_id if target_parent is not None else assets._USER_PACKS_DIR / pack_id,
                src_dir_fd=parent, dst_dir_fd=target_parent)
        published = assets._USER_PACKS_DIR / pack_id
        if (_directory_identity(published, parent=True) != candidate_identity
                or {entry.name for entry in islice(published.iterdir(), 9)} != expected_names):
            raise ValueError("hatch_storage_changed")
        for name, digest in fingerprints.items():
            if hashlib.sha256(assets.read_buddy_asset(published / name, root=published)).hexdigest() != digest:
                raise ValueError("hatch_storage_changed")
        saved.update(pack_id=pack_id, retained_copy=True)
        record("pack_published")
        proof_received = False
        authority()
        client_service.update_buddy({"pack_id": pack_id}, expected_revision=expected_config_revision,
            command_id=command_id, validate=authority, checkpoint=checkpoint_config)
        saved["selected"] = True

    try:
        authority()
        with _directory(draft, exclusive=True):
            draft_identity = _directory_identity(draft, parent=True)
            with _directory(candidate, exclusive=True):
                pass
            candidate_identity = _directory_identity(candidate, parent=True)
            record("starting")
            with prepared_attachments("buddy-hatch-" + command_id, []):
                if mode == "full":
                    def image_sink(data):
                        nonlocal preview, painted
                        authority()
                        preview = _png(data)
                        _write_new(candidate, "preview.png", preview, authority, root_identity=candidate_identity)
                        saved["preview_digest"] = hashlib.sha256(preview).hexdigest()
                        saved["retained_copy"] = True
                        painted += 1
                        return str(candidate / "preview.png")
                    current_caches().images["buddy-guide.png"] = living_hatch.pose_guide(family)
                    paint("image_provider_started", living_hatch.paint_prompt(prompt, family), "buddy-guide.png",
                          image_sink)
                    if not preview:
                        raise ValueError("hatch_generation_uncertain")
                    provider_pending = False
                    record("image_retained")
                else:
                    preview = _png(source)
                    _write_new(candidate, "preview.png", preview, authority, root_identity=candidate_identity)
                    saved["preview_digest"] = hashlib.sha256(preview).hexdigest()
                    saved["retained_copy"] = True
                    record("source_retained")
                if mode != "still":
                    current_caches().images["buddy-base.png"] = _edit_source(preview)
                    for kind in living_hatch.EXPRESSIONS:
                        def edit_sink(data, kind=kind):
                            nonlocal painted
                            authority()
                            edits[kind] = _png(data)
                            painted += 1
                            return f"{kind}.png"
                        paint("expression_provider_started", living_hatch.EXPRESSION_PROMPTS[kind], "buddy-base.png",
                              edit_sink)
                        provider_pending = False
                        if kind not in edits:
                            # The provider declined or failed: hatch with the expressions so far
                            # rather than spend more calls on a failing model.
                            logger.warning("Buddy Hatch: no %s expression came back; continuing without it", kind)
                            break
                        record("expression_retained")
                authority()
                built = living_hatch.build_pack(preview, edits, family, prompt)
                logger.info("Buddy Hatch %s: %s", job_id, json.dumps(built.report, sort_keys=True))
                pack_files = {"art.png": built.art, "rig.json": built.rig,
                              **({"patches.png": built.patches} if built.patches else {})}
                for name, data in pack_files.items():
                    _write_new(candidate, name, data, authority, root_identity=candidate_identity)
                record("rig_prepared")
                publish_pack()
                record("completed", status="completed")
    except Exception as error:
        known = str(getattr(error, "code", "") or error)
        code = known if known in {"hatch_cancelled", "hatch_job_changed", "buddy_revision_conflict",
            "file_revision_conflict", "hatch_storage_changed", "generated_media_too_large"} else "hatch_generation_uncertain" if provider_pending else "hatch_incomplete"
        status = "cancelled" if code == "hatch_cancelled" else "uncertain" if provider_pending or proof_received else "partial" if saved["retained_copy"] else "failed"
        try:
            if draft.exists():
                record("stopped" if status == "cancelled" else "needs_attention", status=status, code=code)
            else:
                hatch._update_hatch_job(job_id, status=status, phase="needs_attention", message="Buddy generation needs attention", error=code)
        except Exception:
            hatch._update_hatch_job(job_id, status="uncertain", phase="needs_attention", message="Buddy recovery needs review", error="hatch_receipt_unconfirmed")
