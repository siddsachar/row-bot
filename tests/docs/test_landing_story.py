import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "docs" / "landing-story.css").read_text(encoding="utf-8")
JS = (ROOT / "docs" / "landing-story.js").read_text(encoding="utf-8")


def test_statement_story_and_real_media_contract() -> None:
    assert "Think it.<br>Build it.<br>Run it.<br><em>Keep it yours.</em>" in HTML
    assert [beat for beat in re.findall(r'data-story-trigger="([^"]+)"', HTML)] == [
        "research",
        "create",
        "automate",
        "control",
    ]
    assert HTML.count('data-story-panel="') == 4
    assert "model:ollama:qwen3.8:27b" in HTML
    assert "model:codex:gpt-5.6-sol" in HTML
    assert "Real Designer output, not a mockup." in HTML
    assert "Nothing was delivered." in HTML


def test_orbit_buddy_has_all_states_and_progressive_fallbacks() -> None:
    for state in ("idle", "thinking", "working", "approval", "success", "error"):
        assert (ROOT / "docs" / "media" / "landing-story" / "buddy" / f"{state}.mp4").is_file()
    assert "${state}.mp4" in JS
    assert 'poster="media/landing-story/buddy/poster.png"' in HTML
    assert "<noscript>" in HTML
    assert "transition: opacity 420ms ease" in CSS
    assert "event.key === 'Enter' || event.key === ' '" in JS


def test_scene_controller_pauses_for_user_and_system_preferences() -> None:
    assert "prefers-reduced-motion: reduce" in CSS
    assert "prefers-reduced-motion: reduce" in JS
    assert "navigator.connection?.saveData" in JS
    assert "document.hidden" in JS
    assert JS.count("IntersectionObserver") >= 2
    assert "video.pause()" in JS
    assert "mediaFailed = true" in JS
    assert "ArrowRight" in JS and "ArrowLeft" in JS
    assert "window.RowBotLandingStory" in JS


def test_capability_constellation_and_sovereignty_field_are_bounded() -> None:
    assert HTML.count("data-capability=") == 9
    assert "Your machine is the system of record." in HTML
    assert "You choose" in HTML
    assert "Math.min(46" in JS
    assert "seed = 1977" in JS
    assert "requestAnimationFrame(drawSovereigntyField)" in JS


def test_three_privacy_enhanced_demo_facades_remain() -> None:
    assert HTML.count("data-youtube=") == 3
    assert "youtube-nocookie.com/embed" in (ROOT / "docs" / "site.js").read_text(encoding="utf-8")
    assert all(video_id in HTML for video_id in ("hRLuOEqbsds", "GA2Tnlt4jNk", "Vuk2xz-vPcA"))
