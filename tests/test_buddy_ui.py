from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_FILES = {"app.py", "launcher.py", "notifications.py", "tasks.py"}
_RUNTIME_PREFIXES = (
    "buddy/",
    "designer/",
)


def _read(relative: str) -> str:
    if relative in _RUNTIME_FILES or relative.startswith(_RUNTIME_PREFIXES):
        relative = f"src/row_bot/{relative}"
    return (ROOT / relative).read_text(encoding="utf-8")


def test_buddy_events_are_emitted_from_runtime_sources():
    tasks_src = _read("src/row_bot/tasks.py")
    notifications_src = _read("src/row_bot/notifications.py")
    brain_src = _read("buddy/brain.py")

    for marker in ["APPROVAL_NEEDED", "APPROVAL_APPROVED", "APPROVAL_DENIED"]:
        assert marker in tasks_src
    assert "WORKFLOW_STARTED" in tasks_src
    assert "WORKFLOW_STEP" in tasks_src
    assert "WORKFLOW_DONE" in tasks_src
    assert "APPROVAL_TIMED_OUT" in tasks_src
    assert 'BuddyEventType.APPROVAL_DENIED: ("approval", "workflow")' in brain_src
    assert "BuddyEventType.NOTIFICATION" in notifications_src


def test_buddy_hatch_prompts_request_keyable_motion_assets():
    hatch_src = _read("buddy/hatch.py")

    assert "Create exactly one animated-app companion character" in hatch_src
    assert "single centered avatar portrait" in hatch_src
    assert "Do not create a sprite sheet" in hatch_src
    assert "contact sheet" in hatch_src
    assert "multiple poses" in hatch_src
    assert "Create one video clip only" in hatch_src
    assert "flat solid" in hatch_src
    assert "keyable background" in hatch_src
    assert "no transparent background" in hatch_src
    assert "no alpha checkerboard" in hatch_src
    assert "motion_source.png" in hatch_src
    assert "_prepare_motion_source_image" in hatch_src
    assert "18 percent empty margin" in hatch_src
    assert "frame edge" in hatch_src
    assert "rim light or outline" in hatch_src
    assert "for attempt in range(2)" in hatch_src
    assert "reused existing clip" in hatch_src
    assert "reuse_existing" in hatch_src
    assert "_is_rate_limited_generation_result" in hatch_src
    assert "ROW_BOT_BUDDY_GOOGLE_VIDEO_SPACING_SECONDS" in hatch_src
    assert "The user concept is the source of truth" in hatch_src
    assert "do not force ancient, mystical, ink, gold, teal, glyph, or Row-Bot-like motifs" in hatch_src
    assert "forced Row-Bot motifs" not in hatch_src
    assert "teal-gold magical glow" not in hatch_src


def test_the_nicegui_desktop_overlay_and_its_runtime_are_retired():
    """The torn-off Buddy is the React overlay (``/app-v2/buddy-overlay``)."""
    import row_bot.buddy.overlay as overlay

    assert not (ROOT / "static" / "buddy" / "runtime" / "buddy.js").exists()
    for retired in ("build_thread_snapshot", "project_approval", "ForegroundAppTracker", "NativeBuddyLifecycle"):
        assert not hasattr(overlay, retired), retired


def test_builtin_glyph_pack_ships_a_still_and_motion_clips():
    manifest_src = _read("static/buddy/builtins/glyph/manifest.json")

    assert '"runtime": "generated_motion_pack"' in manifest_src
    assert '"preview": "preview.png"' in manifest_src
    assert '"path": "motions/idle.mp4"' in manifest_src


def test_installers_ship_the_runtime_package():
    assert "..\\src\\row_bot\\*" in _read("installer/row_bot_setup.iss")
    assert "src/row_bot" in _read("installer/build_mac_app.sh")
    assert "src/row_bot" in _read("installer/build_linux_app.sh")
