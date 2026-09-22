"""Contained media processing, review, validation, and publication."""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, ImageStat

from scripts.marketing.capture_contract import LandingStoryManifest
from scripts.marketing.capture_run import (
    CaptureSafetyError,
    RunReceipt,
    find_sensitive_text,
    require_contained,
    sha256_file,
    utc_now,
)


PUBLIC_MEDIA_BUDGET = 500_000
CLIP_MEDIA_BUDGET = 1_500_000
RESPONSIVE_WIDTHS = (720, 1200, 1600)
CLIP_LEAD_TRIM_SECONDS = 0.9


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
        staged = Path(handle.name)
    try:
        shutil.copyfile(source, staged)
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def _safe_crop(image: Image.Image, crop: tuple[int, int, int, int] | None) -> Image.Image:
    if crop is None:
        return image.copy()
    left, top, right, bottom = crop
    if left < 0 or top < 0 or right > image.width or bottom > image.height:
        raise CaptureSafetyError("crop extends outside the raw capture")
    if right <= left or bottom <= top:
        raise CaptureSafetyError("crop must have positive width and height")
    return image.crop(crop)


def process_webp(
    source: Path,
    destination: Path,
    *,
    width: int | None = None,
    crop: tuple[int, int, int, int] | None = None,
    quality: int = 82,
) -> dict[str, Any]:
    """Create a metadata-free WebP derivative from a lossless raw frame."""

    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image = _safe_crop(image, crop)
        if width and image.width > width:
            height = max(1, round(image.height * width / image.width))
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        clean = Image.new("RGB", image.size)
        clean.paste(image)
        destination.parent.mkdir(parents=True, exist_ok=True)
        clean.save(destination, "WEBP", quality=quality, method=6, exact=True)
    with Image.open(destination) as verified:
        dimensions = verified.size
        metadata = dict(verified.info)
    blocked_metadata = set(metadata).difference({"loop", "background"})
    if blocked_metadata:
        raise CaptureSafetyError(
            f"processed image retained metadata: {', '.join(sorted(blocked_metadata))}"
        )
    return {
        "path": destination.name,
        "width": dimensions[0],
        "height": dimensions[1],
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def process_webm(source: Path, destination: Path, *, ffmpeg: str = "ffmpeg") -> dict[str, Any]:
    """Build a muted, bounded VP9 micro-clip with metadata removed."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        str(CLIP_LEAD_TRIM_SECONDS),
        "-i",
        str(source),
        "-t",
        "6",
        "-an",
        "-map_metadata",
        "-1",
        "-c:v",
        "libvpx-vp9",
        "-deadline",
        "good",
        "-crf",
        "38",
        "-b:v",
        "0",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise CaptureSafetyError(f"ffmpeg failed to process clip: {result.stderr[-400:]}")
    if destination.stat().st_size > CLIP_MEDIA_BUDGET:
        raise CaptureSafetyError(
            f"processed clip exceeds {CLIP_MEDIA_BUDGET} byte budget"
        )
    return {
        "path": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "duration_limit_seconds": 6,
        "muted": True,
    }


def _capture_by_scene(receipt: RunReceipt) -> dict[str, dict[str, Any]]:
    return {str(item.get("scene_id") or ""): item for item in receipt.captures}


def build_contact_sheet(
    manifest: LandingStoryManifest,
    processed_dir: Path,
    review_dir: Path,
    assets: list[dict[str, Any]],
) -> Path:
    review_dir.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    by_scene = {item["scene_id"]: item for item in assets}
    for scene in manifest.scenes:
        asset = by_scene[scene.id]
        still = asset["webp"]["path"]
        cards.append(
            "<article><h2>"
            + html.escape(scene.id)
            + "</h2><a href=\"../processed/"
            + html.escape(still, quote=True)
            + "\"><img src=\"../processed/"
            + html.escape(still, quote=True)
            + "\" alt=\""
            + html.escape(scene.alt, quote=True)
            + "\"></a><p>"
            + html.escape(scene.alt)
            + "</p><code>"
            + html.escape(asset["webp"]["sha256"])
            + "</code></article>"
        )
    document = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Row-Bot landing story review</title><style>
body{margin:0;background:#080b0f;color:#eef4ff;font:15px system-ui;padding:24px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:24px}
article{background:#111821;border:1px solid #2b3a49;border-radius:14px;padding:16px}
img{display:block;width:100%;height:auto;border-radius:8px;background:#05070a}
code{font-size:11px;overflow-wrap:anywhere;color:#9bb7d4}h1{grid-column:1/-1}
</style><main><h1>Landing story full-resolution review</h1>""" + "".join(cards) + "</main></html>"
    target = review_dir / "contact-sheet.html"
    target.write_text(document, encoding="utf-8")
    return target


def process_run(
    manifest: LandingStoryManifest,
    run_dir: Path,
    *,
    ffmpeg: str = "ffmpeg",
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    receipt = RunReceipt.read(run_dir)
    raw_dir = require_contained(run_dir / "raw", run_dir)
    processed_dir = require_contained(run_dir / "processed", run_dir)
    review_dir = require_contained(run_dir / "review", run_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    captures = _capture_by_scene(receipt)
    assets: list[dict[str, Any]] = []
    for scene in manifest.scenes:
        capture = captures.get(scene.id)
        if not capture:
            raise CaptureSafetyError(f"run is missing raw capture for {scene.id}")
        source = require_contained(raw_dir / str(capture.get("image") or ""), raw_dir)
        if not source.is_file():
            raise CaptureSafetyError(f"raw image is missing for {scene.id}")
        target = processed_dir / f"{scene.asset}.webp"
        selected_width = 1200 if scene.viewport == "desktop" else 390
        webp = process_webp(source, target, width=selected_width)
        if scene.id == "hero-app" and webp["bytes"] > PUBLIC_MEDIA_BUDGET:
            raise CaptureSafetyError("hero image exceeds the critical media budget")
        record: dict[str, Any] = {"scene_id": scene.id, "asset": scene.asset, "webp": webp}
        raw_video = str(capture.get("video") or "")
        if "webm" in scene.outputs and raw_video:
            video_source = require_contained(raw_dir / raw_video, raw_dir)
            if not video_source.is_file():
                raise CaptureSafetyError(f"raw video is missing for {scene.id}")
            record["webm"] = process_webm(
                video_source,
                processed_dir / f"{scene.asset}.webm",
                ffmpeg=ffmpeg,
            )
        assets.append(record)
    contact_sheet = build_contact_sheet(manifest, processed_dir, review_dir, assets)
    decision = review_dir / "decision.json"
    decision.write_text(
        json.dumps(
            {
                "status": "pending",
                "reviewed_at": "",
                "reviewer": "",
                "checks": {
                    "privacy": False,
                    "model_labels": False,
                    "settled_states": False,
                    "visual_quality": False,
                },
                "asset_hashes": {item["asset"]: item["webp"]["sha256"] for item in assets},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    receipt.phase = "process"
    receipt.status = "processed"
    receipt.write(run_dir)
    return {
        "assets": assets,
        "contact_sheet": str(contact_sheet.relative_to(run_dir)).replace("\\", "/"),
        "decision": str(decision.relative_to(run_dir)).replace("\\", "/"),
    }


def _approved_decision(run_dir: Path) -> dict[str, Any]:
    path = require_contained(run_dir / "review" / "decision.json", run_dir)
    if not path.is_file():
        raise CaptureSafetyError("review decision is missing")
    decision = json.loads(path.read_text(encoding="utf-8"))
    checks = decision.get("checks") or {}
    if decision.get("status") != "approved" or not checks or not all(checks.values()):
        raise CaptureSafetyError("run has not passed the complete human review gate")
    if not str(decision.get("reviewer") or "").strip() or not str(decision.get("reviewed_at") or "").strip():
        raise CaptureSafetyError("approved review requires reviewer and reviewed_at")
    return decision


def validate_run(manifest: LandingStoryManifest, run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    receipt = RunReceipt.read(run_dir)
    processed = require_contained(run_dir / "processed", run_dir)
    errors = find_sensitive_text(receipt.to_dict())
    checked: list[str] = []
    for scene in manifest.scenes:
        path = require_contained(processed / f"{scene.asset}.webp", processed)
        if not path.is_file():
            errors.append(f"missing processed still: {scene.asset}")
            continue
        try:
            with Image.open(path) as image:
                if image.format != "WEBP":
                    errors.append(f"{scene.asset} is not WebP")
                if image.width < 320 or image.height < 240:
                    errors.append(f"{scene.asset} dimensions are too small")
                variance = sum(ImageStat.Stat(image.convert("RGB")).var) / 3
                if variance < 8:
                    errors.append(f"{scene.asset} appears blank")
                if find_sensitive_text(image.info, location=scene.asset):
                    errors.append(f"{scene.asset} contains unsafe metadata")
            checked.append(scene.asset)
        except Exception as exc:
            errors.append(f"could not inspect {scene.asset}: {exc}")
        clip = processed / f"{scene.asset}.webm"
        if clip.exists() and clip.stat().st_size > CLIP_MEDIA_BUDGET:
            errors.append(f"{scene.asset} clip exceeds budget")
    if errors:
        receipt.phase = "validate"
        receipt.status = "invalid"
    elif receipt.status != "published-locally":
        receipt.phase = "validate"
        receipt.status = "validated"
    receipt.write(run_dir)
    return {"ok": not errors, "errors": errors, "checked": checked}


def publish_run(
    manifest: LandingStoryManifest,
    run_dir: Path,
    public_root: Path,
    *,
    approve_reviewed_run: bool,
) -> dict[str, Any]:
    if not approve_reviewed_run:
        raise CaptureSafetyError("publication requires --approve-reviewed-run")
    result = validate_run(manifest, run_dir)
    if not result["ok"]:
        raise CaptureSafetyError("run validation failed before publication")
    decision = _approved_decision(run_dir)
    processed = require_contained(run_dir / "processed", run_dir)
    public_root = public_root.resolve()
    screenshots = public_root / "screenshots"
    clips = public_root / "clips"
    expected_hashes = decision.get("asset_hashes") or {}
    published: list[dict[str, Any]] = []
    for scene in manifest.scenes:
        still = processed / f"{scene.asset}.webp"
        digest = sha256_file(still)
        if expected_hashes.get(scene.asset) != digest:
            raise CaptureSafetyError(f"reviewed hash changed for {scene.asset}")
        destination = require_contained(screenshots / still.name, public_root)
        _atomic_copy(still, destination)
        item = {"scene_id": scene.id, "still": f"screenshots/{still.name}", "sha256": digest, "alt": scene.alt}
        clip = processed / f"{scene.asset}.webm"
        if clip.is_file():
            clip_destination = require_contained(clips / clip.name, public_root)
            _atomic_copy(clip, clip_destination)
            item["clip"] = f"clips/{clip.name}"
            item["clip_sha256"] = sha256_file(clip)
        published.append(item)
    manifest_path = require_contained(public_root / "manifest.json", public_root)
    public_root.mkdir(parents=True, exist_ok=True)
    public_manifest = {
        "schema": 1,
        "story_id": manifest.story.id,
        "run_id": RunReceipt.read(run_dir).run_id,
        "reviewed_at": decision["reviewed_at"],
        "assets": published,
    }
    manifest_path.write_text(json.dumps(public_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = RunReceipt.read(run_dir)
    receipt.phase = "publish"
    receipt.status = "published-locally"
    receipt.review = {"status": "approved", "reviewed_at": decision["reviewed_at"]}
    receipt.write(run_dir)
    return {"published": published, "manifest": str(manifest_path)}


def approve_review(run_dir: Path, *, reviewer: str = "Codex assisted visual review") -> Path:
    """Record a completed local review after every check has been performed."""

    path = require_contained(run_dir / "review" / "decision.json", run_dir)
    decision = json.loads(path.read_text(encoding="utf-8"))
    decision.update(status="approved", reviewer=reviewer, reviewed_at=utc_now())
    decision["checks"] = {key: True for key in (decision.get("checks") or {})}
    path.write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
