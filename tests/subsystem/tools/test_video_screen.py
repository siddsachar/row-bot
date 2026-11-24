"""Screen and app footage (C9): what a recording logged being done is ringed, spotlit or zoomed in on, its
recordings are captured twice over so a zoom stays sharp, and a recording with no beats is cut by what was done."""

from __future__ import annotations

import pytest
from PIL import Image

from row_bot import video_screen, video_studio
from row_bot.video_studio import Frame, camera
from tests.helpers.media_files import video

pytestmark = pytest.mark.subsystem

# Clicking a small "Save" button at the right, typing into a field near it, then opening a new page and clicking a
# wide panel there that's too big to zoom to.
ACTIONS = [
    [2.0, 2.6, "click", 0.80, 0.10, 0.06, 0.04, 'button "Save"'],
    [3.2, 5.0, "type", 0.62, 0.20, 0.20, 0.05, 'textbox "Name"'],
    [8.0, 8.9, "open", 0.0, 0.0, 1.0, 1.0, "example.com"],
    [9.5, 10.0, "click", 0.10, 0.40, 0.36, 0.30, "region \"Orders\""],
]


def test_where_the_camera_zooms_in_the_control_is_ringed_and_a_control_too_big_to_zoom_to_is_spotlit():
    keys = camera(ACTIONS, start=0.0, end=12.0)
    marks = video_screen.marks(ACTIONS, 0.0, 12.0, camera="follow", keys=keys)

    rings = [mark for mark in marks if not mark[6]]
    assert [mark[2:6] for mark in rings] == [(0.80, 0.10, 0.06, 0.04), (0.62, 0.20, 0.20, 0.05)], \
        "the button, then the field the camera pans to"
    for begin, until, *_box in rings:
        assert video_studio.view(keys, begin + 0.01)[2] > 1.25 and video_studio.view(keys, until - 0.01)[2] > 1.25, \
            "ringed only while the camera holds in on it"
    assert rings[0][1] <= rings[1][0], "one ring at a time"
    assert rings[-1][1] <= 8.0, "gone before the new page opens"
    [spot] = [mark for mark in marks if mark[6]]
    assert spot[2:6] == (0.10, 0.40, 0.36, 0.30) and spot[0] < 9.5 < spot[1], \
        "the wide panel: the camera stays out, so the rest of the page dims around it"


def test_the_spotlight_camera_stays_wide_and_dims_around_each_control_and_still_marks_nothing():
    marks = video_screen.marks(ACTIONS, 0.0, 12.0, camera="spotlight")

    assert [mark[2:7] for mark in marks] == [(0.80, 0.10, 0.06, 0.04, True), (0.62, 0.20, 0.20, 0.05, True),
                                             (0.10, 0.40, 0.36, 0.30, True)]
    assert marks[0][1] <= marks[1][0] and marks[1][1] <= 8.0, "one at a time, and none over the next page"
    assert video_screen.marks(ACTIONS, 0.0, 12.0, camera="still") == (), "the person said no zooms or marks"
    assert video_screen.marks(ACTIONS, 0.0, 12.0, speed=4, camera="spotlight") == (), "a fast-forward isn't marked"
    assert video_screen.marks(ACTIONS, 6.0, 12.0, camera="spotlight")[0][2:6] == (0.10, 0.40, 0.36, 0.30), \
        "a scene cut later marks only what's done in it"


def test_a_mark_fades_in_and_out_and_its_ring_settles_onto_the_control():
    mark = (1.0, 3.0, 0.4, 0.4, 0.2, 0.1, True)

    assert video_screen.strength(mark, 0.9)[0] == 0.0 and video_screen.strength(mark, 3.0)[0] == 0.0
    rising, middle, falling = (video_screen.strength(mark, at) for at in (1.1, 2.0, 2.9))
    assert 0 < rising[0] < 1 and middle[0] == 1.0 and 0 < falling[0] < 1, "eased in, held, eased out"
    assert rising[1] > video_screen.STEPS and middle[1] == falling[1] == video_screen.STEPS, \
        "the ring grows in from larger, then stays on the control"


def _drawn(mark, size=(640, 360), shown=1.0):
    picture = Image.new("RGB", size, (240, 240, 240))
    width, height = size

    def place(box):
        x, y, box_width, box_height = box
        return x * width, y * height, (x + box_width) * width, (y + box_height) * height

    video_screen.draw(picture, ((mark, shown, video_screen.STEPS),), place)
    return picture


def test_a_spotlight_dims_the_picture_around_the_control_and_rings_it():
    marked = _drawn((0.0, 1.0, 0.40, 0.40, 0.20, 0.10, True))

    assert marked.getpixel((40, 40))[0] < 150, "away from the control, the picture is dimmed"
    assert marked.getpixel((320, 162)) == (240, 240, 240), "the control itself as it is"
    red, green, blue = marked.getpixel((round(0.4 * 640) - 2, 162))
    assert blue > red + 40, "a blue ring just outside it"
    half = _drawn((0.0, 1.0, 0.40, 0.40, 0.20, 0.10, True), shown=0.5)
    assert marked.getpixel((40, 40))[0] < half.getpixel((40, 40))[0] < 240, "fading: dimmed less"
    ring = _drawn((0.0, 1.0, 0.40, 0.40, 0.20, 0.10, False))
    assert ring.getpixel((40, 40)) == (240, 240, 240), "a ring alone leaves the rest as it is"
    point = _drawn((0.0, 1.0, 0.5, 0.5, 0.0, 0.0, False))
    assert min(point.getpixel((320, 180))) > 230 and point.getpixel((320, 173))[2] > 200, \
        "a click with no control's box: a round ring around the point"


@pytest.fixture
def clip(tmp_path):
    av = pytest.importorskip("av")
    try:
        av.codec.Codec("libx264", "w")
    except Exception:
        pytest.skip("No H.264 encoder in this copy")
    return video(tmp_path / "rec.mp4", sound=False)


def _frames(path):
    import av

    with av.open(str(path)) as reader:
        return [frame.to_image() for frame in reader.decode(video=0)]


def test_a_recorded_scene_is_drawn_with_its_marks_only_while_they_show(clip, tmp_path):
    """The studio pass lays a mark on the frames it shows on, in one pass: a still view (ffmpeg's scaler) and a
    moving one (the camera) alike."""
    spot = (0.8, 1.6, 0.05, 0.4, 0.3, 0.2, True)
    studio = video_studio.Studio(tmp_path / "scenes.mkv", (320, 180), fps=10)
    studio.add(clip, 0.0, 2.0, (), Frame((320, 180), "full"), marks=(spot,))
    studio.add(clip, 0.0, 2.0, ((0.0, 0.5, 0.5, 1.0), (0.3, 0.2, 0.5, 1.5)), Frame((320, 180), "full"),
               marks=((0.8, 1.6, 0.05, 0.4, 0.3, 0.2, False),))
    studio.close()

    frames = _frames(tmp_path / "scenes.mkv")
    assert len(frames) == 40
    assert frames[2].getpixel((300, 20))[0] > 200, "before its action: the picture as it was"
    assert frames[12].getpixel((300, 20))[0] < 160, "while it shows: the rest dimmed"
    assert min(frames[12].getpixel((50, 90))) > 200, "the control kept bright"
    assert frames[18].getpixel((300, 20))[0] > 200, "after it: as it was"
    ringed = frames[32]  # Zoomed in 1.5× on the left middle: the control's box is in view, ringed.
    assert any(pixel[2] > pixel[0] + 40 for pixel in (ringed.getpixel((x, y)) for x in range(0, 320, 2)
                                                       for y in range(0, 180, 2))), "a blue ring in the zoomed view"


def test_a_recording_captured_twice_over_still_makes_a_video_of_the_usual_size(tmp_path):
    from row_bot.video_editing import InputVideo, SceneCut, Step, VideoInfo

    pytest.importorskip("av")
    tall = video(tmp_path / "phone.mp4", sound=False, size=(1170, 2532), seconds=1)
    source = InputVideo(path=str(tall), info=VideoInfo(duration=1.0, width=1170, height=2532, fps=10.0,
                                                       has_audio=False))
    inputs, _step, _made = video_studio.prepare([source], Step(op="scenes", scenes=[SceneCut(input=0, end=0.2)]),
                                                tmp_path / "made")

    assert (inputs[0].info.width, inputs[0].info.height) == (886, 1920), "its own shape, at most 1920 tall"


def test_a_recording_with_no_beats_has_a_scene_for_each_burst_of_what_was_done():
    done = [[3.0, 3.5, "click", 0.1, 0.1, 0.1, 0.1, ""], [4.0, 6.0, "type", 0.1, 0.1, 0.1, 0.1, ""],
            [20.0, 21.0, "click", 0.5, 0.5, 0.1, 0.1, ""], [25.0, 26.0, "open", 0.0, 0.0, 1.0, 1.0, ""]]

    beats = video_screen.bursts(done, 40.0)

    assert [(at, kind) for at, kind, *_words in beats] == [
        (0.0, "live"), (7.5, "work"), (19.0, "live"), (24.0, "live"), (27.5, "cut")], \
        "a short wait joins the first scene, a long one is sped up, a quiet end left out"
    assert video_screen.bursts([], 40.0) == []
