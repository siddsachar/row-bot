"""Text that reads well (C7): captions word by word as they're said (Row-Bot's voice, a clip's own speech or one
narration), in clean, boxed or a short's big active-word style, and a made video's chapters."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from row_bot.video_editing import (
    InputVideo, Look, SceneCut, Step, VideoInfo, aligned_words, build_plan, caption_pages, estimated_words,
)

pytestmark = pytest.mark.subsystem

HD = VideoInfo(duration=10.0, width=640, height=360, fps=30.0, has_audio=True)
TALL = VideoInfo(duration=10.0, width=360, height=640, fps=30.0, has_audio=True)


def test_a_lines_words_take_the_times_they_were_heard_and_keep_how_they_were_written():
    heard = [(0.0, 0.4, "Kwen"), (0.4, 0.8, "runs"), (0.9, 1.3, "eight"), (1.3, 1.8, "thousand"),
             (1.9, 2.4, "tokens.")]
    words = aligned_words("Qwen runs 8,000 tokens.", heard)
    assert [word for _start, _end, word in words] == ["Qwen", "runs", "8,000", "tokens."], "the written words"
    assert words[1][:2] == (0.4, 0.8) and words[3][:2] == (1.9, 2.4), "heard words keep their times"
    assert words[0] == (0.0, 0.4, "Qwen") and words[2][:2] == (0.8, 1.9), \
        "the rest fill the time between their neighbours"
    spread = estimated_words("One two three", 3.0)
    assert spread[0][0] == 0.0 and spread[-1][1] == pytest.approx(3.0) and \
        all(a[1] == pytest.approx(b[0]) for a, b in zip(spread, spread[1:])), "no speech model: spread over the voice"


def test_captions_show_a_phrase_at_a_pause_and_a_shorts_few_big_words_one_at_a_time():
    words = estimated_words("First, open the shop. Then pick the three orders that never shipped.", 6.0)
    phrases = caption_pages(words, 30)
    assert [" ".join(shown) for _start, _end, shown, _active in phrases] == [
        "First, open the shop.", "Then pick the three orders", "that never shipped."]
    assert all(start < end for start, end, _shown, _active in phrases)
    assert all(a[1] <= b[0] + 1e-9 for a, b in zip(phrases, phrases[1:])), "one caption at a time"

    big = caption_pages(words, 30, big=True)
    assert len(big) == len(words), "one moment per word"
    assert all(len(shown) <= 3 for _start, _end, shown, _active in big)
    assert [shown[active] for _start, _end, shown, active in big] == [word for _s, _e, word in words], \
        "the word being said is lit, in order"


def _caption_pictures(plan):
    return [Image.open(io.BytesIO(png)).convert("RGBA") for png in plan.overlays]


def test_a_scenes_caption_follows_its_words_in_each_style():
    words = ((0.0, 0.4, "Pack"), (0.4, 0.8, "light"), (0.8, 1.0, "for"), (1.0, 1.2, "the"), (1.2, 1.6, "trip."),
             (2.0, 2.4, "Go"), (2.4, 2.8, "far."))
    scene = SceneCut(input=0, end=3, line="Pack light for the trip. Go far.", words=words)

    def plan(style, info=HD):
        step = Step(op="scenes", style="caption", look=Look(captions=style), scenes=[scene])
        return build_plan([InputVideo("input-1.mp4", info)], [step], "output.mp4")

    boxed, clean = plan("box"), plan("clean")
    shown = [(box.start, box.end) for box in boxed.texts if box.kind == "caption"]
    assert shown[0][0] == 0.0 and shown[-1][0] == pytest.approx(2.0) and shown[-1][1] <= 3.0, "as they're said"
    boxed_picture, clean_picture = _caption_pictures(boxed)[0], _caption_pictures(clean)[0]
    assert boxed_picture.getpixel((boxed_picture.width // 2, 2))[3] > 100, "boxed: on a dark box"
    assert clean_picture.getpixel((2, 2))[3] < 30, "clean: no box"

    short = plan("word", TALL)
    moments = [box for box in short.texts if box.kind == "caption"]
    assert [round(box.start, 2) for box in moments] == [word[0] for word in words], "a short: one moment per word"
    assert all(box.y + box.height < 640 * 0.8 for box in moments), "clear of a phone app's buttons at the bottom"
    lit = _caption_pictures(short)[0]
    assert any(pixel[0] > 200 and pixel[1] > 170 and pixel[2] < 80 for pixel in lit.getdata()), \
        "the word being said is yellow"


def test_one_narration_over_the_whole_video_is_captioned_as_it_says_it_instead_of_each_line():
    narration = ((0.2, 0.8, "Welcome"), (0.8, 1.2, "to"), (1.2, 1.9, "Lisbon."), (4.0, 4.6, "Day"), (4.6, 5.0, "two."))
    step = Step(op="scenes", style="caption", look=Look(), words=narration, scenes=[
        SceneCut(input=0, end=3, line="A line that isn't said"), SceneCut(input=0, start=4, end=7)])
    plan = build_plan([InputVideo("input-1.mp4", HD)], [step], "output.mp4")
    shown = sorted((box.start, box.end) for box in plan.texts if box.kind == "caption")
    assert [round(start, 1) for start, _end in shown] == [0.2, 4.0], "the narration's two phrases, nothing else"
