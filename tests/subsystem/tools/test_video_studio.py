"""The studio pass of a video project (5.3.2): a camera that follows what was done, and the framed look."""

from __future__ import annotations

import pytest
from PIL import Image

from row_bot import video_studio
from row_bot.video_studio import Frame, camera, compose, crop_box, view

pytestmark = pytest.mark.subsystem

# Clicking a small "Save" button at the right, then typing into a field near it, then opening a new page.
ACTIONS = [
    [2.0, 2.6, "click", 0.80, 0.10, 0.06, 0.04, "button \"Save\""],
    [3.2, 5.0, "type", 0.62, 0.20, 0.20, 0.05, "textbox \"Name\""],
    [8.0, 8.9, "open", 0.0, 0.0, 1.0, 1.0, "example.com"],
    [9.5, 10.0, "click", 0.10, 0.80, 0.05, 0.04, "link \"Help\""],
]


def test_the_camera_eases_in_on_what_is_done_follows_the_next_action_and_eases_out_before_a_new_page():
    keys = camera(ACTIONS, start=1.0, end=9.0)

    assert view(keys, 0.0) == (0.5, 0.5, 1.0), "the whole picture before anything is done"
    x, y, zoom = view(keys, 2.6 - 1.0)
    assert (round(x, 2), round(y, 2)) == (0.83, 0.12) and zoom == pytest.approx(video_studio.MAX_ZOOM), \
        "in on the button, as far as the camera goes"
    x, y, zoom = view(keys, 5.0 - 1.0)
    assert (x, y) == pytest.approx((0.72, 0.225), abs=0.01) and zoom > 1.5, "panned to the field, still in"
    assert view(keys, 8.0 - 1.0)[2] == 1.0, "back out before the new page opens"
    assert camera(ACTIONS, start=1.0, end=9.0, speed=4) == (), "a fast-forward stays still"
    assert camera([[1.0, 2.0, "click", 0.1, 0.1, 0.8, 0.5, "the page"]], 0.0, 4.0) == (), \
        "a control most of the page wide isn't worth zooming to"


def test_a_scenes_own_zoom_eases_in_and_holds():
    keys = video_studio.zoom_keys((0.5, 0.5, 0.5, 0.5))
    assert view(keys, 0.0) == (0.5, 0.5, 1.0) and view(keys, 5.0) == (0.75, 0.75, 2.0)
    assert 1.0 < view(keys, video_studio.EASE_IN / 2)[2] < 2.0, "eased, not a jump"


def test_the_view_never_leaves_the_picture_and_crops_between_pixels():
    assert crop_box(1.0, 1.0, 2.0, 1920, 1080) == (960.0, 540.0, 1920.0, 1080.0)
    left, top, right, bottom = crop_box(0.333, 0.5, 1.7, 1920, 1080)
    assert left != int(left) and right - left == pytest.approx(1920 / 1.7), "sub-pixel: a slow zoom doesn't shimmer"


def test_framed_footage_sits_on_the_brand_backdrop_with_rounded_corners_and_full_fills_the_frame():
    picture = Image.new("RGB", (640, 360), (250, 250, 250))
    frame = Frame((640, 360), "framed", (20, 60, 140))
    framed = compose(picture, frame)

    assert framed.size == (640, 360)
    corner, middle = framed.getpixel((4, 4)), framed.getpixel((320, 180))
    assert corner[2] > corner[0] and corner != (250, 250, 250), "the backdrop shows around the picture"
    assert middle == (250, 250, 250), "the picture in the middle"
    left = (640 - round(640 * video_studio.FRAME_FILL)) // 2
    top = (360 - round(360 * video_studio.FRAME_FILL)) // 2
    assert framed.getpixel((left, top)) != (250, 250, 250), "its corner is rounded"
    full = compose(Image.new("RGB", (390, 844), (250, 250, 250)), Frame((640, 360), "full"))
    assert full.size == (640, 360) and full.getpixel((2, 2)) == (250, 250, 250), "edge to edge"


@pytest.fixture
def clip(tmp_path):
    av = pytest.importorskip("av")
    try:
        av.codec.Codec("libx264", "w")
    except Exception:
        pytest.skip("No H.264 encoder in this copy")
    path = tmp_path / "rec.mp4"
    with av.open(str(path), "w") as writer:
        stream = writer.add_stream("libx264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
        for index in range(30):  # Three seconds; the right half turns red from the second second.
            picture = Image.new("RGB", (320, 180), (240, 240, 240))
            if index >= 10:
                picture.paste((220, 20, 20), (160, 0, 320, 180))
            frame = av.VideoFrame.from_image(picture)
            frame.pts = index
            for packet in stream.encode(frame):
                writer.mux(packet)
        for packet in stream.encode(None):
            writer.mux(packet)
    return path


def test_a_scene_is_drawn_through_its_camera_into_a_clip_of_its_length(clip, tmp_path):
    import av

    keys = ((0.0, 0.5, 0.5, 1.0), (0.3, 0.9, 0.5, 2.0))
    seconds, sound = video_studio.render_scene(clip, 1.0, 2.5, keys, Frame((320, 180), "full"),
                                               tmp_path / "scene.mp4", fps=10)

    assert (seconds, sound) == (1.5, False)
    with av.open(str(tmp_path / "scene.mp4")) as reader:
        frames = [frame.to_image() for frame in reader.decode(video=0)]
    assert len(frames) == 15 and frames[0].size == (320, 180)
    assert frames[0].getpixel((240, 90))[1] < 80 < frames[0].getpixel((40, 90))[1], \
        "from one second in: the red half is there, the whole picture in view"
    assert frames[-1].getpixel((40, 90))[1] < 80, "zoomed in on the right: red fills the frame"
    with pytest.raises(Exception, match="Stopped"):
        video_studio.render_scene(clip, 0.0, 3.0, (), Frame((320, 180), "full"), tmp_path / "stop.mp4",
                                  fps=10, stopped=lambda: True)


def test_a_projects_recorded_scenes_reach_the_edit_as_drawn_clips_and_title_cards_stay_cards(clip, tmp_path):
    from row_bot.video_editing import InputVideo, Look, SceneCut, Step, VideoInfo, build_plan

    source = InputVideo(path=str(clip), info=VideoInfo(duration=3.0, width=320, height=180, fps=10.0,
                                                       has_audio=False))
    step = Step(op="scenes", aspect="16:9", look=Look(framing="framed"), scenes=[
        SceneCut(input=None, end=2, card="Hello"),
        SceneCut(input=0, start=0.5, end=2.0, camera=((0.0, 0.5, 0.5, 1.0), (0.3, 0.9, 0.5, 2.0))),
    ])
    inputs, drawn = video_studio.prepare([source], step, tmp_path)

    assert [item.path for item in inputs] == ["scene-01.mp4"], "the recording itself isn't read again"
    assert (inputs[0].info.width, inputs[0].info.height, inputs[0].info.duration) == (1920, 1080, 1.5)
    card, scene = drawn.scenes
    assert card.input is None and (scene.input, scene.start, scene.end, scene.camera) == (0, 0.0, 1.5, ())
    assert build_plan(inputs, [drawn], "output.mp4").duration == 3.5


def test_a_phones_picture_is_set_in_a_device_on_the_backdrop():
    picture = Image.new("RGB", (390, 844), (250, 250, 250))
    framed = compose(picture, Frame((1920, 1080), "framed", (20, 60, 140)))

    middle_x = 960
    screen_top = (1080 - round(844 * 1080 * video_studio.PHONE_FILL / 844)) // 2
    assert framed.getpixel((middle_x, 540)) == (250, 250, 250), "the screen in the middle"
    rim = framed.getpixel((middle_x, screen_top - 4))
    assert max(rim) < 60, "a dark rim around it, like the phone it was used on"
    assert framed.getpixel((200, 540))[2] > framed.getpixel((200, 540))[0], "the brand's backdrop either side"
