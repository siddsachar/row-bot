import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from scripts.marketing.capture_contract import load_manifest
from scripts.marketing.capture_run import CaptureSafetyError, RunReceipt, sha256_file
from scripts.marketing.media_pipeline import approve_review, process_run, publish_run, validate_run


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = load_manifest(ROOT / "scripts" / "marketing" / "landing_story.yml")


def _create_run(run_dir: Path) -> None:
    raw = run_dir / "raw"
    raw.mkdir(parents=True)
    captures = []
    for index, scene in enumerate(MANIFEST.scenes):
        path = raw / f"{scene.id}.png"
        image = Image.new("RGB", (640, 480), (12 + index * 9, 28, 45))
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 50, 580, 430), outline=(95, 180, 210), width=8)
        draw.text((70, 80), scene.id, fill=(235, 242, 249))
        image.save(path)
        captures.append({"scene_id": scene.id, "image": path.name, "video": ""})
    receipt = RunReceipt(
        run_id="20260921T120000Z-a1b2c3",
        story_id=MANIFEST.story.id,
        prompt_version=MANIFEST.story.prompt_version,
        git_commit="46d88892",
        client="nicegui",
        models={"local": MANIFEST.models.local, "frontier": MANIFEST.models.frontier},
        max_generation_attempts=6,
        status="captured",
        captures=captures,
    )
    receipt.write(run_dir)


def test_processing_removes_metadata_and_requires_review_before_publish(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _create_run(run_dir)
    result = process_run(MANIFEST, run_dir)
    assert len(result["assets"]) == 9
    assert (run_dir / "review" / "contact-sheet.html").is_file()
    assert validate_run(MANIFEST, run_dir)["ok"] is True
    with pytest.raises(CaptureSafetyError, match="--approve-reviewed-run"):
        publish_run(MANIFEST, run_dir, tmp_path / "public", approve_reviewed_run=False)
    with pytest.raises(CaptureSafetyError, match="human review gate"):
        publish_run(MANIFEST, run_dir, tmp_path / "public", approve_reviewed_run=True)


def test_review_hash_gate_blocks_changed_asset(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    public = tmp_path / "public"
    _create_run(run_dir)
    process_run(MANIFEST, run_dir)
    approve_review(run_dir, reviewer="deterministic test")
    published = publish_run(MANIFEST, run_dir, public, approve_reviewed_run=True)
    assert len(published["published"]) == 9
    public_manifest = json.loads((public / "manifest.json").read_text(encoding="utf-8"))
    assert public_manifest["run_id"] == "20260921T120000Z-a1b2c3"

    changed = run_dir / "processed" / f"{MANIFEST.scenes[0].asset}.webp"
    changed.write_bytes(changed.read_bytes() + b"changed")
    assert sha256_file(changed) != json.loads(
        (run_dir / "review" / "decision.json").read_text(encoding="utf-8")
    )["asset_hashes"][MANIFEST.scenes[0].asset]
    with pytest.raises(CaptureSafetyError, match="reviewed hash changed"):
        publish_run(MANIFEST, run_dir, public, approve_reviewed_run=True)
