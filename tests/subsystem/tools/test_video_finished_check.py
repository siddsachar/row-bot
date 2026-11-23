"""The finished check (C8): what Row-Bot reads in every video it makes before it says the video is done.

The render's plan is real (``build_plan``), the footage is tiny real clips (PyAV), and ffmpeg's one read is its
real words (as ffmpeg 7.1 prints them) unless ffmpeg is on this computer; the vision model is a fake.
"""

from __future__ import annotations

import shutil

import pytest

from row_bot import video_check
from row_bot.video_check import Footage, Measures, Render
from row_bot.video_editing import InputVideo, SceneCut, Step, VideoInfo, build_plan
from tests.helpers.media_files import video

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

WIDE = VideoInfo(duration=3.0, width=640, height=360, fps=10.0, has_audio=False)
MINUTE = VideoInfo(duration=60.0, width=1280, height=720, fps=30.0, has_audio=True)
FINE = Measures(loudness=-16.0, peak=-1.6)


def _ebur128(integrated: str, peak: str) -> str:
    """ffmpeg's ebur128 summary, as it prints it."""
    return (f"[Parsed_ebur128_0 @ 000001c630400600] Summary:\n\n  Integrated loudness:\n    I:         {integrated} LUFS\n"
            "    Threshold: -31.8 LUFS\n\n  Loudness range:\n    LRA:         0.0 LU\n    Threshold: -41.8 LUFS\n\n"
            f"  True peak:\n    Peak:      {peak} dBFS\n")


@pytest.fixture
def clips(tmp_path):
    av = pytest.importorskip("av")
    try:
        av.codec.Codec("libx264", "w")
    except Exception:
        pytest.skip("No H.264 encoder in this copy")

    def made(name: str, **options) -> InputVideo:
        video(tmp_path / name, sound=False, **options)
        width, height = options.get("size", (320, 180))
        return InputVideo(path=name, info=VideoInfo(duration=3.0, width=width, height=height, fps=10.0,
                                                    has_audio=False))

    return made


def _render(inputs, scenes, *, aspect="", style="", footage=(), folder=None) -> Render:
    step = Step(op="scenes", scenes=scenes, aspect=aspect, style=style)
    plan = build_plan(inputs, [step], "output.mp4")
    return Render(plan=plan, inputs=inputs, step=step, footage=list(footage), folder=folder)


def test_one_read_measures_loudness_and_true_peak_black_still_and_jumping_stretches():
    args = " ".join(video_check.measure_arguments("output.mp4", has_audio=True, narrated=True))
    assert "ebur128=peak=true" in args and "volumedetect" not in args, "loudness in LUFS, not a mean volume"
    assert all(name in args for name in ("blackdetect", "freezedetect", "scdet", "silencedetect"))
    assert "scale=480" in args, "the picture is read shrunk: the check stays quick next to the render"
    assert "-af" not in video_check.measure_arguments("output.mp4", has_audio=False)
    assert "silencedetect" not in " ".join(video_check.measure_arguments("output.mp4", has_audio=True))

    read = video_check.read_measures(
        "[scdet @ 000001c62f4dd980] lavfi.scd.score: 36.256, lavfi.scd.time: 3\n" + _ebur128("-21.8", "-17.6")
        + "black_start:2 black_end:4 black_duration:2\n", MINUTE)
    assert (read.loudness, read.peak, read.black, read.jumps) == (-21.8, -17.6, [(2.0, 4.0)], [(3.0, 36.256)])


def test_a_quiet_mix_a_loud_one_and_peaks_that_clip_need_fixing():
    def sound(integrated: str, peak: str) -> list[str]:
        result = video_check.checks(MINUTE, video_check.read_measures(_ebur128(integrated, peak), MINUTE), [])
        return [line for line in result.lines if line.startswith("Sound")] + [str(result.passed)]

    assert sound("-16.0", "-1.6") == ["Sound: -16.0 LUFS, peaks -1.6 dBTP.", "True"]
    assert sound("-28.4", "-12.0") == ["Sound: too quiet (-28.4 LUFS; keep it between -23 and -9 LUFS, -14 suits "
                                       "most videos). Turn the voice or music up.", "False"]
    assert sound("-6.5", "-0.4")[0].startswith("Sound: too loud (-6.5 LUFS") and sound("-6.5", "-0.4")[1] == "False"
    assert sound("-14.0", "-0.3")[0].startswith("Sound: its peaks reach -0.3 dBTP, enough to crackle on phones")
    assert sound("-70.0", "-inf")[0].startswith("Sound: too quiet (silent;")
    silent = video_check.checks(WIDE, video_check.read_measures("", WIDE), [])
    assert not silent.passed and silent.lines[1].startswith("Sound: none.")


def test_a_frozen_or_black_stretch_and_a_narrations_quiet_gaps_need_fixing():
    """The Release kit's demo said 3 s of each 18 s scene and held the picture for 14 s: "Sound: present" passed it."""
    loud = _ebur128("-16.0", "-2.0")
    held = video_check.checks(MINUTE, video_check.read_measures(
        loud + "lavfi.freezedetect.freeze_start: 4.4\nlavfi.freezedetect.freeze_duration: 14.2\n", MINUTE), [])
    assert not held.passed and "Still: 0:04.4 for 14 s: too long to watch. Cut those parts shorter or speed them " \
                                "up." in held.lines
    short = video_check.checks(MINUTE, video_check.read_measures(
        loud + "lavfi.freezedetect.freeze_start: 12.2\nlavfi.freezedetect.freeze_duration: 4.1\n"
        "black_start:0 black_end:0.6 black_duration:0.6\n", MINUTE), [])
    assert short.passed and "Black: none." in short.lines, "a fade-in at the start isn't a problem"
    assert "Still: 0:12.2 for 4 s (fine if nothing should move there)." in short.lines
    # Under the voice a viewer reads along: a narrated picture may hold up to 20 s, not longer.
    spoken = loud + "lavfi.freezedetect.freeze_start: 4.4\n"
    assert video_check.checks(MINUTE, video_check.read_measures(spoken + "lavfi.freezedetect.freeze_duration: 14.2\n",
                                                                 MINUTE, narrated=True), [], narrated=True).passed
    gappy = video_check.checks(MINUTE, video_check.read_measures(
        loud + "silence_start: 2.87\nsilence_end: 18.04 | silence_duration: 15.17\nsilence_start: 57.5\n", MINUTE,
        narrated=True), [], narrated=True)
    assert "Quiet: 0:02.9–0:18 with no voice. Give those parts a line, or cut them shorter." in gappy.lines
    black = video_check.checks(MINUTE, video_check.read_measures(loud + "black_start:20 black_end:23\n", MINUTE), [])
    assert "Black: 0:20–0:23. Cut it, or put a picture there." in black.lines and not black.passed


def test_a_clip_shown_far_larger_than_it_was_made_is_soft_and_a_little_larger_is_worth_a_look(clips, tmp_path):
    """Past 2× (a small clip at full size, a 720p clip zoomed in) a scene needs fixing; from 1.5× to 2× (a camera's
    zoom on a recording, a wide clip in a tall export) it's worth a look but doesn't hold the video back."""
    sharp, small = clips("sharp.mp4", size=(640, 360)), clips("small.mp4", size=(264, 148))
    render = _render([sharp, small], [SceneCut(input=0, end=3), SceneCut(input=1, end=3),
                                      SceneCut(input=0, end=3, zoom=(0.2, 0.2, 0.625, 0.625))], folder=tmp_path)

    found = [(item.line, item.fix) for item in video_check.findings(render, FINE)]
    assert found == [
        ("Scene 2, 0:03–0:06: soft. Its 264×148 footage is shown 2.4× larger. Use a sharper or larger copy.", True),
        ("Check scene 3, 0:06–0:09: a little soft. Its 640×360 footage is shown 1.6× larger, zoomed in 1.6×. Use a "
         "sharper or larger copy, or zoom in less.", False)], "scene 1 shows its footage as it was made"
    assert not video_check.checks(WIDE, FINE, video_check.findings(render, FINE)).passed, "2.4× needs fixing"
    gentle = _render([sharp], [SceneCut(input=0, end=3, zoom=(0.2, 0.2, 0.7, 0.7))], folder=tmp_path)
    assert video_check.findings(gentle, FINE) == [], "1.4× larger is still sharp enough"

    # The same wide footage exported tall: cropped to fill 9:16, its middle is shown 1.8× larger.
    tall = Step(op="scenes", scenes=[SceneCut(input=0, end=3)], aspect="9:16")
    drawn = build_plan([InputVideo("scenes.mp4", VideoInfo(3.0, 360, 640, 10.0, False))], [tall], "output.mp4")
    export = video_check.findings(Render(plan=drawn, inputs=[sharp], step=tall, folder=tmp_path), FINE)
    assert [item.line for item in export] == [
        "Check scene 1, 0:00–0:03: a little soft. Its 640×360 footage is shown 1.8× larger. Use a sharper or larger "
        "copy, or show its whole picture (fit)."]
    assert video_check.checks(WIDE, FINE, export).passed, "a tall export of wide footage is ready to share"


def test_a_caption_over_what_is_being_clicked_needs_fixing_and_one_clear_of_it_doesnt():
    recording = [InputVideo("input-1.mp4", VideoInfo(6.0, 640, 360, 30.0, False))]
    scenes = [SceneCut(input=0, end=6, line="Now send the note to every customer.", voice_seconds=5.0)]
    at_the_bottom = Footage(actions=((1.0, 2.0, "click", 0.45, 0.88, 0.1, 0.06, 'button "Send"'),))
    at_the_top = Footage(actions=((1.0, 2.0, "click", 0.45, 0.10, 0.1, 0.06, 'button "Send"'),
                                  (3.0, 4.0, "open", 0.0, 0.0, 1.0, 1.0, "example.com")))

    covered = video_check.findings(_render(recording, scenes, style="caption", footage=[at_the_bottom]), FINE)
    assert [item.line for item in covered] == [
        "Scene 1, 0:01: the caption covers what's being done (button \"Send\"). Frame the scene so it shows higher "
        "(its zoom), or move or shorten the words."]
    assert video_check.findings(_render(recording, scenes, style="caption", footage=[at_the_top]), FINE) == []
    assert video_check.findings(_render(recording, scenes, footage=[at_the_bottom]), FINE) == [], "no captions"


def test_words_at_the_edge_of_a_tall_export_reach_its_safe_area_and_captions_stay_clear_of_it():
    tall = [InputVideo("scenes.mp4", VideoInfo(6.0, 1080, 1920, 30.0, False))]
    # A web address as a scene's title: one word, too long for the frame's width.
    scenes = [SceneCut(input=0, end=6, card="shop.example.com/collections/summer-ceramics", line="Done.",
                       voice_seconds=2.0)]
    found = video_check.findings(_render(tall, scenes, aspect="9:16", style="caption"), FINE)
    assert [item.line for item in found] == [
        "Scene 1, 0:00.3: the scene's title reaches into the 9:16 video's right edge, where apps lay their own "
        "buttons and captions. Shorten it."], "the long title only: the caption is laid clear of the app's buttons"
    short = [SceneCut(input=0, end=6, card="Shipped", line="Done.", voice_seconds=2.0)]
    assert video_check.findings(_render(tall, short, aspect="9:16", style="caption"), FINE) == []
    wide = [InputVideo("scenes.mp4", VideoInfo(6.0, 1920, 1080, 30.0, False))]
    assert video_check.findings(_render(wide, scenes, style="caption"), FINE) == [], "a wide video has no safe area"


def test_a_jump_inside_a_scene_is_worth_a_look_but_a_cut_between_scenes_or_a_recordings_page_isnt():
    footage = [InputVideo("input-1.mp4", VideoInfo(6.0, 640, 360, 30.0, False))]
    render = _render(footage, [SceneCut(input=0, end=3), SceneCut(input=0, start=3, end=6)])
    jumps = Measures(loudness=-16.0, peak=-2.0, jumps=[(1.5, 30.0), (3.0, 60.0), (4.5, 6.0)])

    found = video_check.findings(render, jumps)
    assert [(item.line, item.fix) for item in found] == [
        ("Check scene 1, 0:01.5: the picture jumps there, with no cut meant (a jump cut). If it isn't wanted, start "
         "or end the scene away from it.", False)], "the cut at 0:03 is meant; a small change isn't a jump"
    recorded = Render(plan=render.plan, inputs=footage, step=render.step,
                      footage=[Footage(actions=((1.0, 1.4, "click", 0.1, 0.1, 0.1, 0.1, "link"),)), Footage()])
    assert video_check.findings(recorded, jumps) == [], "a recording's page changes at once"
    checks = video_check.checks(WIDE, jumps, found)
    assert checks.passed, "a heuristic is worth a look, not a reason to hold the video back"


def test_ai_shots_of_one_character_that_drift_apart_are_worth_a_look(clips, tmp_path):
    first, same = clips("maya-1.mp4", colour=(200, 160, 120)), clips("maya-2.mp4", colour=(200, 160, 120))
    changed = clips("maya-3.mp4", colour=(20, 40, 160))
    maya = Footage(look="character: maya")
    # (Before each clip's right half turns red.)
    render = _render([first, same, changed], [SceneCut(input=0, end=0.9), SceneCut(input=1, end=0.9),
                                              SceneCut(input=2, end=0.9)], footage=[maya, maya, maya], folder=tmp_path)

    found = video_check.findings(render, FINE)
    assert [(item.line, item.fix) for item in found] == [
        ("Check scene 3, 0:01.8: it may not look like scene 1 any more (character: maya): its colours have drifted. If "
         "the character or style changed, make that shot again from the same references.", False)]
    apart = _render([first, changed], [SceneCut(input=0, end=0.9), SceneCut(input=1, end=0.9)], folder=tmp_path)
    assert video_check.findings(apart, FINE) == [], "shots that share no character or style may differ"


def test_words_over_a_face_in_footage_need_fixing(clips, tmp_path, monkeypatch):
    clip = clips("talk.mp4", size=(640, 360))
    monkeypatch.setattr(video_check, "faces", lambda picture: [(0.42, 0.78, 0.16, 0.16)])  # A face low in the frame.
    scenes = [SceneCut(input=0, end=3, line="Welcome to my little shop on the corner", voice_seconds=2.5)]

    found = video_check.findings(_render([clip], scenes, style="caption", folder=tmp_path), FINE)
    assert [item.line for item in found] == [
        "Scene 1, 0:01: the caption covers a face. Frame the scene so the face shows higher (its zoom), or move or "
        "shorten the words."]


class _Vision:
    def __init__(self, answer: str, problem: str = "") -> None:
        self.answer, self.problem, self.asked = answer, problem, []

    def cannot_see(self, chat_model=None) -> str:
        return self.problem

    def analyze(self, image_bytes: bytes, question: str, **_options) -> str:
        self.asked.append((image_bytes, question))
        return self.answer


@pytest.fixture
def made(tmp_path, monkeypatch, reload_for_data_dir):
    """A project's video just rendered: ffmpeg's read and its review sheet are given, the vision model is a fake."""
    reload_for_data_dir(tmp_path / "data", "row_bot.threads")
    from row_bot import video_edits

    monkeypatch.setattr(video_edits, "measure_file", lambda *_args, **_options: (WIDE, FINE))
    monkeypatch.setattr(video_edits, "_review_sheet", lambda *_args: b"review sheet")
    footage = [InputVideo("input-1.mp4", WIDE)]
    step = Step(op="scenes", scenes=[SceneCut(input=0, end=1.5, line="Open the shop."),
                                     SceneCut(input=0, start=1.5, end=3, card="Ship it")])
    plan = build_plan(footage, [step], "output.mp4")

    def check(vision: _Vision):
        monkeypatch.setattr("row_bot.vision_runtime.get_vision_service", lambda: vision)
        edit = video_edits.VideoEdit(edit_id="edit-0000c8c8", conversation_id="conversation-a", kind="video",
                                     summary="", output_name="Shop v1.mp4", started_at=1.0, status="editing")
        video_edits._check_video(edit, None, tmp_path, plan,
                                 video_edits.Request(conversation_id="conversation-a", kind="video", sources=[],
                                                     output_name="Shop v1.mp4", check=True),
                                 (footage, step))
        return edit

    return check


def test_the_check_looks_at_the_review_sheet_once_with_a_fixed_rubric_and_lists_what_it_saw(made):
    vision = _Vision("Here's what I noticed:\n1. scene 2 · 0:02: the hand on the box looks warped\n"
                     "- 0:00.5: the caption runs off the right edge\nEverything else reads well.")
    edit = made(vision)

    [(sheet, question)] = vision.asked
    assert sheet == b"review sheet", "one call, on the review sheet"
    assert "scene 1: Open the shop." in question and "scene 2: Ship it" in question, "what each scene says"
    assert "warped hands or faces" in question and "cut off at an edge" in question and "reply OK" in question
    assert "Check scene 2 · 0:02: the hand on the box looks warped" in edit.checks
    assert "Check 0:00.5: the caption runs off the right edge" in edit.checks
    assert not any("reads well" in line for line in edit.checks), "only problems, not what's fine"
    assert edit.checks_passed is True, "a fresh look each make can always find something: it never holds a video"
    assert edit.detail == "", "the card no longer says it's checking"

    assert made(_Vision("OK")).checks_passed is True


def test_with_no_vision_model_the_look_is_skipped_and_said_so(made):
    vision = _Vision("", problem="No model is chosen yet, so Row-Bot can't look at images. Choose a model, then "
                                 "try again.")
    edit = made(vision)

    assert vision.asked == [], "nothing is sent"
    assert edit.checks_passed is True, "the rest of the check still decides"
    assert "Look: not done. No model is chosen yet, so Row-Bot can't look at images. Choose a model, then try " \
           "again." in edit.checks
    failed = made(_Vision("Vision analysis failed: the provider timed out"))
    assert failed.checks_passed is True
    assert "Look: the vision model couldn't look at the review sheet (Vision analysis failed: the provider timed " \
           "out). Tell the person the frames weren't looked at, so they can open the review sheet and check by " \
           "eye." in failed.checks


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_edit_videos_check_reads_any_video_and_looks_at_a_sheet_of_its_frames_once(tmp_path, monkeypatch,
                                                                                    reload_for_data_dir):
    (threads,) = reload_for_data_dir(tmp_path / "data", "row_bot.threads")
    from row_bot.application.attachments import list_conversation_files, register_attachment
    from row_bot.tools import video_editing_tool

    chat = threads.create_thread("Holiday", seed_default_skills=False)
    monkeypatch.setattr("row_bot.tool_outputs.current_thread_id", lambda: chat)
    register_attachment(chat, "beach.mp4", video(tmp_path / "beach.mp4", sound="silence").read_bytes())
    vision = _Vision("OK")
    monkeypatch.setattr("row_bot.vision_runtime.get_vision_service", lambda: vision)

    said = video_editing_tool._edit_video(action="check", inputs=["beach.mp4"])

    assert said.startswith("Checks for beach.mp4 NOT passed: fix these, then check it again:\n- Length 0:03, 320×180.")
    assert "- Sound: too quiet (silent;" in said
    assert "- Look: the vision model saw nothing wrong on the review sheet." in said and len(vision.asked) == 1
    assert said.endswith("Review sheet: beach review.jpg in this chat's files (the checks include one look at it).")
    assert "beach review.jpg" in [item["name"] for item in list_conversation_files(chat)], "the person can open it"


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_ffmpegs_one_read_of_a_real_video_finds_a_silent_mix_too_quiet(clips, tmp_path):
    from row_bot import ffmpeg_runtime, video_edits

    ffmpeg = ffmpeg_runtime.find()
    for sound, passed in (("silence", False), (True, True)):
        path = video(tmp_path / f"clip-{passed}.mp4", sound=sound)
        info, measures = video_edits.measure_file(ffmpeg, path, tmp_path)
        checks = video_check.checks(info, measures, [])
        assert checks.passed is passed, checks.lines
    assert measures.loudness is not None and -20 < measures.loudness < -9 and measures.peak < -1
