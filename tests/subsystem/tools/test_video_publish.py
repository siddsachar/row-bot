"""Ready to publish (C10): a made video's publish kit, made only for the platforms the person names (G9): each
platform's shape (4:5 too, with its safe area), a thumbnail in the brand's title style, the post's title and
description with a YouTube video's chapters, and SRT and WebVTT subtitles from the words its captions are timed by.

Real attachment storage in an isolated data folder; the voice, ffmpeg and the edit job are fakes, so nothing is
spoken, encoded or downloaded, and no picture is made by a provider.
"""
from __future__ import annotations

import io
import threading

import pytest
from PIL import Image

from row_bot.video_editing import InputVideo, Look, SceneCut, Step, VideoInfo, build_plan, estimated_words
from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401
from tests.subsystem.tools.test_video_projects import MP4, _wait, studio  # noqa: F401

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

HD = VideoInfo(duration=10.0, width=640, height=360, fps=30.0, has_audio=True)


def test_subtitles_are_srt_and_webvtt_phrases_timed_as_the_words_are_said():
    from row_bot.video_publish import SUBTITLE_LINE, srt, subtitle_cues, vtt

    line = ("First, open the shop. Then pick the three orders that never shipped and write a short note to each "
            "customer today.")
    words = [(start + 1.0, end + 1.0, word) for start, end, word in estimated_words(line, 9.0)]
    cues = subtitle_cues(words)
    assert " ".join(text.replace("\n", " ") for _start, _end, text in cues) == line, "every word, in order"
    assert cues[0][0] == 1.0 and cues[-1][1] >= 10.0
    assert all(start < end for start, end, _text in cues)
    assert all(a[1] <= b[0] for a, b in zip(cues, cues[1:])), "one subtitle at a time"
    assert all(len(text.split("\n")) <= 2 and all(len(part) <= SUBTITLE_LINE + 8 for part in text.split("\n"))
               for _start, _end, text in cues), "at most two lines of about a line's length"

    sub = srt(cues)
    assert sub.startswith("1\n00:00:01,000 --> 00:00:") and f"\n{len(cues)}\n" in sub
    web = vtt(cues)
    assert web.startswith("WEBVTT\n\n00:00:01.000 --> 00:00:") and "," not in web.split("\n")[2]
    assert "01:02:05,500 --> 01:02:06,250" in srt([(3725.5, 3726.25, "Later.")]), "hours, minutes, milliseconds"
    assert subtitle_cues([]) == []


def test_a_made_videos_words_in_time_are_what_its_lines_voices_and_narration_say():
    """The plan keeps what's said on the finished video: a voiced line's words over its voice, after an intro card;
    a line nobody says isn't there; a clip's own heard speech is; one narration joins them in time."""
    scenes = [SceneCut(input=0, end=2.0, line="Pack light.", voice="a.wav", voice_seconds=1.5),
              SceneCut(input=0, start=2.0, end=4.0, line="Never said."),
              SceneCut(input=0, start=4.0, end=6.0, words=((0.2, 0.6, "Hello"),))]
    plan = build_plan([InputVideo("input-1.mp4", HD)], [Step(op="scenes", scenes=scenes, look=Look(intro="Hi"))],
                      "output.mp4", sounds={"a.wav": "voice-1.wav"})
    said = [word for _start, _end, word in plan.spoken]
    assert said == ["Pack", "light.", "Hello"], "a line without a voice isn't said"
    assert plan.spoken[0][0] == pytest.approx(plan.marks[0]) and plan.marks[0] == pytest.approx(2.0), \
        "after the intro card"
    assert plan.spoken[1][1] <= plan.marks[0] + 1.5 + 1e-6, "within its voice"
    assert plan.spoken[2][0] == pytest.approx(plan.marks[2] + 0.2)

    narrated = build_plan([InputVideo("input-1.mp4", HD)],
                          [Step(op="scenes", scenes=scenes[2:], words=((0.0, 0.1, "Welcome"), (9.0, 9.5, "late")))],
                          "output.mp4")
    assert [word for _s, _e, word in narrated.spoken] == ["Welcome", "Hello"], \
        "a narration's words, in time with the clip's own, none after the video ends"


def test_each_platform_has_its_shape_and_a_kit_needs_a_platform_named():
    from row_bot.video_projects import ProjectError
    from row_bot.video_publish import shapes_for

    assert shapes_for(["youtube", "x", "reels", "tiktok", "instagram", "linkedin"]) == ["16:9", "9:16", "4:5", "1:1"]
    for unnamed in ([], ["myspace"]):
        with pytest.raises(ProjectError, match="Name where it's going.*ask them"):
            shapes_for(unnamed)


def test_a_portrait_post_is_made_at_4_5_with_its_words_clear_of_the_feeds_edges():
    from row_bot.video_editing import caption_size, margins

    left, top, right, bottom = margins(1080, 1350)
    assert (left, top, right) == (70, 84, 70) and bottom >= round(1350 * 0.08), "5% at the sides, 8% at the foot"
    step = Step(op="scenes", aspect="4:5", style="caption", scenes=[SceneCut(
        input=None, end=3.0, card="Spring sale",
        line="Every plant in the shop is half price this weekend, so bring a friend and a big bag.")])
    plan = build_plan([], [step], "output.mp4")
    assert (plan.width, plan.height) == (1080, 1350)
    captions = [box for box in plan.texts if box.kind == "caption"]
    assert captions and caption_size("box", 1080, 1350) == round(1350 * 0.045), "sized by the frame's height"
    for box in captions:
        assert box.x >= left and box.x + box.width <= 1080 - right and box.width <= 1080 * 0.86
        assert box.y + box.height <= 1350 - bottom


def _brand_box(data: bytes, colour: tuple[int, int, int]) -> tuple[tuple[int, int], tuple[int, int, int, int] | None]:
    """A thumbnail's size, and where the brand's colour is on it (None: nowhere)."""
    from PIL import ImageChops

    shown = Image.open(io.BytesIO(data))
    assert shown.format == "JPEG"
    red, green, blue = ImageChops.difference(shown.convert("RGB"), Image.new("RGB", shown.size, colour)).split()
    far = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return shown.size, far.point(lambda value: 255 if value < 40 else 0).getbbox()


def test_a_thumbnail_is_its_platforms_shape_titled_in_the_brand_style_inside_its_safe_area():
    from row_bot.video_editing import margins
    from row_bot.video_publish import THUMBNAIL_BYTES, THUMBNAIL_SIZES, thumbnail

    red = (200, 30, 30)
    still = Image.new("RGB", (1920, 1080), (40, 120, 60))
    for shape, size in THUMBNAIL_SIZES.items():
        for titles in ("studio", "bold"):
            data = thumbnail(still, "How I built a desk from one sheet of plywood", shape, Look(colour=red,
                                                                                                titles=titles))
            shown, box = _brand_box(data, red)
            assert shown == size and len(data) <= THUMBNAIL_BYTES
            left, top, right, bottom = margins(*size)
            assert box is not None, f"{shape} {titles}: the title is on the brand's colour"
            assert box[0] >= left - 1 and box[2] <= size[0] - right + 1 and box[1] >= top - 1 \
                and box[3] <= size[1] - bottom + 1, f"{shape} {titles}: inside the safe area"
    assert _brand_box(thumbnail(still, "", "16:9", Look(colour=red)), red)[1] is None, "no words: the still alone"
    card = Image.open(io.BytesIO(thumbnail(None, "Desk", "9:16", Look(colour=(30, 60, 200)))))
    assert card.size == (1080, 1920) and card.convert("RGB").getpixel((540, 1700))[2] > 120, \
        "no still: the brand's title card"


def _made_project(studio, title="Plywood desk"):  # noqa: F811
    """A YouTube video of three scenes, made: the fake edit is saved with its words, its marks and its shape."""
    from row_bot import video_edits, video_projects
    from row_bot.application.attachments import register_attachment

    project = video_projects.create("conversation-a", title, [
        {"source": "signup.mp4", "end": 12, "line": "First, sign up."},
        {"source": "pricing.mp4", "card": "Cutting", "line": "Then cut."},
        {"source": "signup.mp4", "card": "Finish", "line": "Then oil it."}], look={"recipe": "youtube"})
    video_projects.make(project, 1)
    made = video_edits.get(_wait(project.project_id, 1).edit_id)
    saved = register_attachment("conversation-a", f"{title}.mp4", MP4)
    video_edits._set(made, status="saved", media_ref=saved["attachment_ref"], mime_type="video/mp4",
                     marks=[0.0, 12.0, 30.0, 45.0], expected_seconds=45.0, shape="16:9",
                     words=[(0.5, 1.0, "First,"), (1.0, 1.4, "sign"), (1.4, 2.0, "up."), (12.5, 13.0, "Then"),
                            (13.0, 13.4, "cut.")])
    return video_projects.get(project.project_id)


def _names() -> list[str]:
    from row_bot.application.attachments import list_conversation_files

    return [item["name"] for item in list_conversation_files("conversation-a")]


def test_the_publish_kit_is_made_only_when_the_person_names_where_it_is_going(studio, monkeypatch):  # noqa: F811
    from row_bot import ffmpeg_runtime, video_projects
    from row_bot.application.attachments import read_attachment
    from row_bot.tools import video_project_tool

    monkeypatch.setattr(video_project_tool, "_conversation", lambda: "conversation-a")
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: object())
    project = _made_project(studio)
    status = video_project_tool._video(action="status", project=project.project_id)
    assert "Publish kit" not in status and not any(name.endswith((".jpg", ".srt", ".vtt")) for name in _names()), \
        "a made video has no kit until a platform is named"
    refused = video_project_tool._video(action="publish", project=project.project_id)
    assert "Name where it's going" in refused and not video_projects.get(project.project_id).latest.publish

    result = video_project_tool._video(action="publish", project=project.project_id, platforms=["youtube", "x"],
                                       title="I built a <desk>", description="One sheet,\nno table saw.")
    assert len(studio["started"]) == 1, "YouTube and X take the 16:9 video as it was made: nothing is made again"
    assert "Publish kit for YouTube, X:" in result and "- 16:9 video for YouTube, X: made: Plywood desk.mp4" in result
    assert "- Title: I built a desk" in result, "YouTube refuses angle brackets"
    assert "- Description with its chapters: One sheet, / no table saw. / 0:00 Plywood desk / 0:12 Cutting / " \
           "0:30 Finish" in result
    assert "Thumbnails (the title card, titled): Plywood desk thumbnail 16x9.jpg" in result, \
        "footage that can't be read here: the title card"
    assert "- Subtitles: Plywood desk.srt, Plywood desk.vtt" in result
    kit = video_projects.view(video_projects.get(project.project_id))["publish"]
    assert kit["platforms"] == ["youtube", "x"] and [item["shape"] for item in kit["thumbnails"]] == ["16:9"]
    subtitles = {item["format"]: read_attachment(item["ref"])[1].decode() for item in kit["subtitles"]}
    assert subtitles["srt"] == ("1\n00:00:00,500 --> 00:00:02,400\nFirst, sign up.\n\n"
                                "2\n00:00:12,500 --> 00:00:13,800\nThen cut.\n")
    assert subtitles["vtt"].startswith("WEBVTT\n\n00:00:00.500 --> 00:00:02.400\nFirst, sign up.\n")
    thumb = Image.open(io.BytesIO(read_attachment(kit["thumbnails"][0]["ref"])[1]))
    assert thumb.size == (1280, 720)


def test_each_other_shape_is_exported_once_and_a_picture_given_is_the_thumbnail(studio):  # noqa: F811
    from row_bot import video_projects, video_publish
    from row_bot.application.attachments import register_attachment
    from row_bot.video_projects import ProjectError

    project = _made_project(studio, "Desk")
    cover = io.BytesIO()
    Image.new("RGB", (1536, 864), (90, 40, 160)).save(cover, format="PNG")
    register_attachment("conversation-a", "cover.png", cover.getvalue())

    version = video_publish.make(project, ["reels", "tiktok", "instagram"], picture="cover.png")
    for _ in range(300):
        exports = video_projects.get(project.project_id).latest.exports
        if {"9:16", "4:5"} <= set(exports):
            break
        threading.Event().wait(0.01)
    shapes = sorted(request.steps[0].aspect for request in studio["started"][1:])
    assert shapes == ["4:5", "9:16"], "Reels and TikTok share one tall video; Instagram's is 4:5"
    assert "Desk 4x5.mp4" in [request.output_name for request in studio["started"]]
    assert [item["shape"] for item in version.publish["thumbnails"]] == ["9:16", "4:5"]
    assert version.publish["thumbnail_from"] == "picture" and not version.publish["chapters"], \
        "no chapters off YouTube"

    video_publish.make(project, ["reels"], picture="cover.png")
    assert len(studio["started"]) == 3, "an export being made isn't made again"
    with pytest.raises(ProjectError, match="isn't a picture"):
        video_publish.make(project, ["reels"], picture="signup.mp4")
    summary = video_projects.summary(video_projects.get(project.project_id))
    assert "Publish kit for Instagram Reels:" in summary and "- 9:16 video for Instagram Reels: being made" in summary


def test_a_thumbnails_still_is_the_videos_longest_scene_read_from_its_own_footage(studio):  # noqa: F811
    from row_bot import video_projects
    from row_bot.application.attachments import register_attachment
    from row_bot.video_publish import still

    photo = io.BytesIO()
    Image.new("RGB", (800, 600), (250, 200, 0)).save(photo, format="PNG")
    register_attachment("conversation-a", "photo.png", photo.getvalue())
    project = video_projects.create("conversation-a", "Garden", [
        {"source": "", "card": "Garden", "end": 3},
        {"source": "photo.png", "end": 6},
        {"source": "signup.mp4", "end": 2}])
    picture, number = still(project, project.latest)
    assert number == 2 and picture.getpixel((10, 10)) == (250, 200, 0), "the longest scene, without its words"
    cards = video_projects.create("conversation-a", "Cards", [{"source": "", "card": "Only words", "end": 3}])
    assert still(cards, cards.latest) == (None, 0)
