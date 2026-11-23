"""A video's look (C5): its grade, footage from several sources matched to one look, film grain, a 2.39 letterbox,
crossfades between scenes, and the brand kit's typeface and title styles.

ffmpeg's own filters run here through PyAV's copy of them (the media extra; skipped without it), on tiny pictures.
No ffmpeg program: the nightly's real-ffmpeg make covers the whole command (``test_video_edit_lifecycle``).
"""
from __future__ import annotations

import io
from fractions import Fraction

import pytest
from PIL import Image

from row_bot import video_studio
from row_bot.video_editing import (
    CROSSFADE, Finish, InputVideo, Look, SceneCut, Step, VideoInfo, build_plan, letterbox_filters, look_filters,
    lower_third, on_the_beat,
)

pytestmark = pytest.mark.subsystem

HD = VideoInfo(duration=10.0, width=640, height=360, fps=30.0, has_audio=True)


def _chain(text: str) -> list[tuple[str, str]]:
    """A filter chain as ffmpeg reads it ("a=1,b='x,y'"), split at the commas outside quotes."""
    filters, current, quoted = [], "", False
    for character in text:
        if character == "'":
            quoted = not quoted
        if character == "," and not quoted:
            filters.append(current)
            current = ""
        else:
            current += character
    filters.append(current)
    return [(name, arguments) for name, _, arguments in (item.partition("=") for item in filters)]


def _through(chain: str, picture: Image.Image) -> Image.Image:
    """A picture through these filters, as the video's BT.709 frames go through them."""
    av = pytest.importorskip("av")
    filters = pytest.importorskip("av.filter")

    frame = av.VideoFrame.from_image(picture).reformat(format="yuv420p", dst_colorspace="ITU709",
                                                       dst_color_range="MPEG")
    frame.pts = 0
    graph = filters.Graph()
    node = graph.add_buffer(width=picture.width, height=picture.height, format="yuv420p", time_base=Fraction(1, 30))
    for name, arguments in [*_chain(chain), ("buffersink", "")]:
        following = graph.add(name, arguments) if arguments else graph.add(name)
        node.link_to(following)
        node = following
    graph.configure()
    graph.push(frame)
    return graph.pull().reformat(format="rgb24", src_colorspace="ITU709", src_color_range="MPEG").to_image()


def _swatches() -> Image.Image:
    """A dark blue-grey, a mid skin tone and a bright sky, side by side."""
    picture = Image.new("RGB", (96, 32))
    for index, colour in enumerate(((40, 48, 60), (190, 140, 110), (150, 190, 235))):
        picture.paste(colour, (index * 32, 0, index * 32 + 32, 32))
    return picture


def _at(picture: Image.Image, index: int) -> tuple[int, int, int]:
    return picture.getpixel((index * 32 + 16, 16))


def test_each_grade_changes_the_footage_as_its_name_says_and_natural_changes_nothing():
    assert look_filters("natural") == "", "a natural grade, with nothing to match, draws nothing"
    swatches = _swatches()
    shown = {grade: _through(look_filters(grade), swatches) for grade in ("warm", "cinematic", "vivid", "bw")}

    def warmth(picture, index):
        red, _green, blue = _at(picture, index)
        return red - blue

    def colourful(picture, index):
        return max(_at(picture, index)) - min(_at(picture, index))

    assert all(warmth(shown["warm"], index) > warmth(swatches, index) + 5 for index in range(3)), "warmer"
    assert all(colourful(shown["vivid"], index) > colourful(swatches, index) * 1.15 for index in range(3)), \
        "more colour"
    assert all(colourful(shown["bw"], index) <= 3 for index in range(3)), "black and white"
    assert warmth(shown["cinematic"], 0) < warmth(swatches, 0) and warmth(shown["cinematic"], 1) > warmth(
        swatches, 1), "film's teal shadows and warm skin"


def _tone(colour: tuple[int, int, int], spread: float, pixels: int = 1000) -> list[float]:
    """A clip's colour as the studio pass notes it: pixels, each channel's sum and its squares."""
    return [pixels, *(value * pixels for value in colour), *((value ** 2 + spread ** 2) * pixels for value in colour)]


def test_footage_from_two_sources_is_matched_to_one_look_and_footage_from_one_is_left_alone():
    phone = _tone((150, 120, 95), 40.0, pixels=3000)  # Warm, and most of the video.
    ai = _tone((95, 120, 160), 55.0)  # Cool and contrasty.
    matched = video_studio.colour_match({"phone.mov": phone, "shot.mp4": ai})
    assert video_studio.colour_match({"phone.mov": phone}) == {}, "one source: nothing to match it to"

    def mean(tone, correction):
        gains, offsets = correction[:3], correction[3:]
        return [tone[1 + channel] / tone[0] * gains[channel] + offsets[channel] for channel in range(3)]

    before = [abs(a - b) for a, b in zip((150, 120, 95), (95, 120, 160))]
    after = [abs(a - b) for a, b in zip(mean(phone, matched["phone.mov"]), mean(ai, matched["shot.mp4"]))]
    assert after[0] < before[0] / 2 and after[2] < before[2] / 2, "their colours meet"
    assert abs(mean(phone, matched["phone.mov"])[0] - 150) < abs(mean(ai, matched["shot.mp4"])[0] - 95), \
        "the source most of the video is moves least"
    picture = _through(look_filters("natural", matched["shot.mp4"]), Image.new("RGB", (32, 32), (95, 120, 160)))
    assert picture.getpixel((16, 16))[0] > 105 and picture.getpixel((16, 16))[2] < 150, \
        "and ffmpeg draws the AI shot warmer, as found"

    night = video_studio.colour_match({"day.mov": _tone((170, 170, 170), 50.0), "night.mov": _tone((30, 30, 40), 20.0)})
    lifted = mean(_tone((30, 30, 40), 20.0), night["night.mov"])
    assert max(lifted) <= 30 + 40 + video_studio.MATCH_SHIFT, "a night scene stays darker than the day"


def test_a_letterbox_bars_a_wide_picture_to_2_39_and_leaves_a_tall_one_as_it_is():
    picture = _through(letterbox_filters(640, 360), Image.new("RGB", (640, 360), (230, 230, 230)))
    bar = (360 - 640 / 2.39) / 2
    assert picture.getpixel((320, round(bar) - 3)) == (0, 0, 0) and picture.getpixel((320, 362 - round(bar))) == (
        0, 0, 0), "black above and below"
    assert picture.getpixel((320, round(bar) + 3))[0] > 200 and picture.getpixel((320, 180))[0] > 200
    assert letterbox_filters(360, 640) == "", "a tall video gets no bars"


def test_crossfades_start_each_scene_while_the_one_before_fades_out_and_cuts_dont():
    scenes = [SceneCut(input=0, start=0, end=3, voice="a.wav", voice_seconds=1.0),
              SceneCut(input=None, end=2, card="Later"),
              SceneCut(input=0, start=5, end=8, voice="b.wav", voice_seconds=1.0)]
    sounds = {"a.wav": "a.wav", "b.wav": "b.wav"}

    def plan(transition):
        step = Step(op="scenes", scenes=scenes, finish=Finish(transition=transition))
        return build_plan([InputVideo("input-1.mp4", HD)], [step], "output.mp4", sounds=sounds)

    cut, faded = plan("cut"), plan("crossfade")
    assert (cut.duration, cut.marks) == (8.0, [0.0, 3.0, 5.0, 8.0])
    assert faded.duration == pytest.approx(8.0 - 2 * CROSSFADE)
    assert faded.marks == pytest.approx([0.0, 3.0 - CROSSFADE, 5.0 - 2 * CROSSFADE, 8.0 - 2 * CROSSFADE]), \
        "each scene starts as it fades in"
    assert [at for at, _name in faded.voices] == pytest.approx([0.0, 5.0 - 2 * CROSSFADE]), "lines start with them"
    assert faded.graph.count("xfade=") == 2 and faded.graph.count("acrossfade=") == 2


def test_on_the_beat_with_crossfades_each_scene_starts_fading_in_on_a_beat():
    beats = [index * 0.8 for index in range(40)]
    scenes = [SceneCut(input=0, start=0, end=2.0), SceneCut(input=0, start=3, end=5.1), SceneCut(input=0, start=6)]
    cut = on_the_beat(scenes, [10.0], [False], beats, overlap=CROSSFADE)
    step = Step(op="scenes", scenes=cut, finish=Finish(transition="crossfade"))
    plan = build_plan([InputVideo("input-1.mp4", HD)], [step], "output.mp4")
    assert all(min(abs(mark - beat) for beat in beats) < 1e-3 for mark in plan.marks[:-1]), plan.marks


def test_grain_and_the_letterbox_cover_the_whole_picture_once_and_the_grade_only_the_footage():
    scenes = [SceneCut(input=0, start=0, end=3), SceneCut(input=None, end=2, card="The end")]
    step = Step(op="scenes", scenes=scenes, finish=Finish(grade="bw", grain=True, letterbox=True))
    plan = build_plan([InputVideo("input-1.mp4", HD)], [step], "output.mp4")
    assert plan.graph.count("colorchannelmixer=") == 1, "one scene of footage is graded; the card keeps its colour"
    assert plan.graph.count("noise=") == 1 and plan.graph.count("drawbox=") == 2


def test_the_brand_kits_title_styles_and_typefaces_draw_differently():
    colour = (200, 40, 60)

    def card(overlay):
        return Image.open(io.BytesIO(overlay.png)).convert("RGBA")

    studio, minimal, bold = (card(lower_third("Day one", colour, 1920, 1080, style=style))
                             for style in ("studio", "minimal", "bold"))
    middle = (studio.width // 2, studio.height // 2)
    assert studio.getpixel(middle)[3] > 200, "studio: words on a card"
    assert minimal.getpixel((minimal.width - 4, minimal.height // 3))[3] < 40, "minimal: no card, just words"
    assert bold.height > studio.height and bold.getpixel((bold.width - 30, bold.height // 2))[:3] == colour, \
        "bold: larger, on the brand's solid colour"
    widths = {family: card(lower_third("Day one", colour, 1920, 1080, family=family)).width
              for family in ("sans", "serif", "display")}
    assert len(set(widths.values())) == 3, "each typeface is its own"

    scenes = [SceneCut(input=None, end=2, card="Chapter one")]
    plain, large = (build_plan([], [Step(op="scenes", aspect="16:9", scenes=scenes,
                                         look=Look(colour=colour, titles=titles))], "output.mp4")
                    for titles in ("studio", "bold"))
    sizes = [max(Image.open(io.BytesIO(png)).height for png in plan.overlays if Image.open(io.BytesIO(png)).width
                 < 1920) for plan in (plain, large)]
    assert sizes[1] > sizes[0], "a bold title card's words are larger"
    minimal_card = build_plan([], [Step(op="scenes", aspect="16:9", scenes=scenes,
                                        look=Look(colour=colour, titles="minimal"))], "output.mp4")
    assert "color=c=0x0c0c0e" in minimal_card.graph, "minimal title cards are black"


@pytest.fixture
def clips(tmp_path):
    av = pytest.importorskip("av")
    try:
        av.codec.Codec("libx264", "w")
    except Exception:
        pytest.skip("No H.264 encoder in this copy")
    from tests.helpers.media_files import video

    return (video(tmp_path / "warm.mp4", sound=False, colour=(200, 150, 110)),
            video(tmp_path / "cool.mp4", sound=False, colour=(110, 150, 210)))


def test_the_studio_pass_matches_clips_from_two_sources_but_never_a_screen_recording(clips, tmp_path):
    warm, cool = clips
    info = VideoInfo(duration=3.0, width=320, height=180, fps=10.0, has_audio=False)
    inputs = [InputVideo(path=str(warm), info=info, identity="warm"), InputVideo(path=str(cool), info=info,
                                                                                identity="cool")]
    step = Step(op="scenes", aspect="16:9", scenes=[
        SceneCut(input=0, end=1.0, matched=True), SceneCut(input=1, end=1.0, matched=True),
        SceneCut(input=1, start=1.0, end=2.0, matched=False)])
    cache = tmp_path / "cache"
    for folder in ("make", "again"):
        (tmp_path / folder).mkdir()
    _inputs, drawn, made = video_studio.prepare(inputs, step, tmp_path / "make", cache=cache)
    tones = [scene.tone for scene in drawn.scenes]
    assert tones[0] and tones[1] and tones[2] == (), "the clips are matched; the screen recording isn't"
    assert tones[0][2] > 1 or tones[0][5] > 0, "the warm clip is drawn a little cooler"

    again, *_rest = video_studio.prepare(inputs, step, tmp_path / "again", cache=cache)[1:]
    assert [scene.tone for scene in again.scenes] == tones and _rest[0] == ["reused"] * 3, \
        "made again, the same match from the colours kept with the project"
