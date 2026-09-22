import hashlib
import json
import re
import subprocess
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "docs" / "landing-story.css").read_text(encoding="utf-8")
JS = (ROOT / "docs" / "landing-story.js").read_text(encoding="utf-8")
MEDIA = ROOT / "docs" / "media" / "landing-story"


def test_statement_led_pinned_story_uses_the_real_app_as_hero() -> None:
    assert "Think it. Build it. Run it. <em>Keep it yours.</em>" in HTML
    assert 'class="product-journey"' in HTML
    assert 'class="journey-sticky"' in HTML
    assert 'class="product-stage"' in HTML
    assert [beat for beat in re.findall(r'data-story-trigger="([^"]+)"', HTML)] == [
        "research",
        "create",
        "automate",
        "ship",
    ]
    assert HTML.count('data-story-panel="') == 4
    assert HTML.count('data-story-copy="') == 4
    assert "model:ollama:qwen3.8:27b" in HTML
    assert "model:codex:gpt-5.6-sol" in HTML
    assert "Real UI · Normal-speed recordings" in HTML
    assert "capability-section" not in HTML
    assert "data-capability=" not in HTML


def test_four_real_clips_have_posters_fallbacks_and_bounded_receipt() -> None:
    receipt = json.loads((MEDIA / "recording-receipt.json").read_text(encoding="utf-8"))
    assert receipt["editing"].startswith("normal-speed editorial cuts")
    assert set(receipt["clips"]) == {"research", "create", "automate", "ship"}
    for beat, record in receipt["clips"].items():
        assert 8 <= record["duration_seconds"] <= 13
        assert record["dimensions"] == [1440, 810]
        assert record["fps"] == 30
        assert record["webm_bytes"] <= 1_500_000
        assert record["mp4_bytes"] <= 1_500_000
        for key in ("webm", "mp4", "poster"):
            path = ROOT / "docs" / record[key]
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == record[f"{key}_sha256"]
        assert f'data-story-video="{beat}"' in HTML
        assert f'clips/{beat}.webm' in HTML
        assert f'clips/{beat}.mp4' in HTML
        assert f'screenshots/{beat}.webp' in HTML
    assert " loop " not in HTML


def test_reviewed_manifest_hashes_every_public_story_asset() -> None:
    manifest = json.loads((MEDIA / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["review_status"] == "approved"
    assert manifest["reviewed_at"]
    for asset in manifest["assets"]:
        still = MEDIA / asset["still"]
        assert hashlib.sha256(still.read_bytes()).hexdigest() == asset["sha256"]
        if clip := asset.get("clip"):
            video = MEDIA / clip
            assert hashlib.sha256(video.read_bytes()).hexdigest() == asset["clip_sha256"]
        if fallback := asset.get("clip_fallback"):
            video = MEDIA / fallback
            assert hashlib.sha256(video.read_bytes()).hexdigest() == asset["clip_fallback_sha256"]


def test_buddy_has_transparent_states_and_automatic_scene_choreography() -> None:
    for state in ("idle", "thinking", "working", "approval", "success", "error"):
        path = MEDIA / "buddy" / f"{state}.webp"
        assert path.is_file()
        with Image.open(path) as image:
            assert image.mode == "RGBA"
            assert image.getchannel("A").getextrema() == (0, 255)
    assert "${state}.webp" in JS
    assert HTML.count("data-buddy-image") == 2
    assert 'src="media/landing-story/buddy/thinking.webp"' in HTML
    assert "<noscript>" in HTML
    assert "transition: opacity 360ms ease" in CSS
    assert "BEAT_STATES" in JS
    assert "controller.dataset.scene = beat" in JS
    assert "setBuddyState(BEAT_STATES[beat], false)" in JS
    assert "video.dataset.storyVideo === 'ship'" in JS
    assert "event.key === 'Enter' || event.key === ' '" in JS


def test_scene_controller_handles_scroll_keyboard_and_media_preferences() -> None:
    assert "prefers-reduced-motion: reduce" in CSS
    assert "prefers-reduced-motion: reduce" in JS
    assert "navigator.connection?.saveData" in JS
    assert "document.hidden" in JS
    assert "IntersectionObserver" in JS
    assert "requestAnimationFrame(updateStoryFromScroll)" in JS
    assert "video.pause()" in JS
    assert "ArrowRight" in JS and "ArrowLeft" in JS
    assert "ArrowDown" in JS and "ArrowUp" in JS
    assert "window.RowBotLandingStory" in JS
    assert "landing_scene" in JS and "motion" in JS
    assert ".landing-overhaul::before { display: none; }" in CSS


def test_sovereignty_reveal_combines_core_visual_and_local_first_proofs() -> None:
    assert "Your AI.<br>Your machine.<br><em>Your rules.</em>" in HTML
    assert HTML.count('class="sovereignty-core') == 2
    assert HTML.count("<li><strong>") == 4
    assert "Your system of record" in HTML
    assert "No Row-Bot account" in HTML
    assert "Math.min(52" in JS
    assert "seed = 1977" in JS
    assert "requestAnimationFrame(drawSovereigntyField)" in JS


def test_three_privacy_enhanced_demo_facades_remain() -> None:
    assert HTML.count("data-youtube=") == 3
    assert "youtube-nocookie.com/embed" in (ROOT / "docs" / "site.js").read_text(encoding="utf-8")
    assert all(video_id in HTML for video_id in ("hRLuOEqbsds", "GA2Tnlt4jNk", "Vuk2xz-vPcA"))


def test_scrollable_linux_command_is_keyboard_focusable() -> None:
    assert 'class="linux-command" data-linux-command tabindex="0"' in HTML


def test_story_controller_runtime_contract() -> None:
    runtime_test = ROOT / "tests" / "docs" / "landing_story_runtime_test.cjs"
    subprocess.run(["node", str(runtime_test)], check=True, cwd=ROOT)
