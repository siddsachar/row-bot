"""The studio pass of a video project (5.3.2): a camera that follows what was done, and the framed look."""

from __future__ import annotations

import pytest

from PIL import Image

from row_bot import video_studio
from tests.helpers.media_files import video
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
    return video(tmp_path / "rec.mp4", sound=False)


def test_scenes_are_drawn_one_after_another_into_one_clip_through_their_cameras(clip, tmp_path):
    import av

    studio = video_studio.Studio(tmp_path / "scenes.mkv", (320, 180), fps=10)
    zoomed = studio.add(clip, 1.0, 2.5, ((0.0, 0.5, 0.5, 1.0), (0.3, 0.9, 0.5, 2.0)), Frame((320, 180), "full"))
    plain = studio.add(clip, 0.0, 1.0, (), Frame((320, 180), "full"))
    studio.close()

    assert (zoomed, plain) == ((0.0, 1.5), (1.5, 1.0)), "where each starts in the clip, and how long"
    with av.open(str(tmp_path / "scenes.mkv")) as reader:
        frames = [frame.to_image() for frame in reader.decode(video=0)]
    assert len(frames) == 25 and frames[0].size == (320, 180)
    assert frames[0].getpixel((240, 90))[1] < 80 < frames[0].getpixel((40, 90))[1], \
        "from one second in: the red half is there, the whole picture in view"
    assert frames[14].getpixel((40, 90))[1] < 80, "zoomed in on the right: red fills the frame"
    assert frames[15].getpixel((240, 90))[1] > 200, "the next scene from the start: before the red"
    stopping = video_studio.Studio(tmp_path / "stop.mkv", (320, 180), fps=10)
    with pytest.raises(Exception, match="Stopped"):
        stopping.add(clip, 0.0, 3.0, (), Frame((320, 180), "full"), stopped=lambda: True)
    stopping.close()


def test_a_scenes_sound_stays_with_its_pictures_and_a_silent_scene_is_silence(clip, tmp_path):
    import av
    import numpy

    noisy = video(tmp_path / "talk.mp4", sound=True)
    studio = video_studio.Studio(tmp_path / "scenes.mkv", (320, 180), fps=10, sound=True)
    studio.add(noisy, 0.5, 1.5, (), Frame((320, 180), "full"))
    studio.add(clip, 0.0, 1.0, (), Frame((320, 180), "full"))
    studio.close()

    with av.open(str(tmp_path / "scenes.mkv")) as reader:
        sound = _left(reader)
    assert abs(sound.size / 48000 - 2.0) < 0.1, "as long as the pictures"
    assert numpy.abs(sound[4800:43200]).mean() > 0.1, "the first scene's tone"
    assert numpy.abs(sound[52800:91200]).max() < 0.01, "silence under the scene that has none"


def test_a_projects_recorded_scenes_reach_the_edit_as_one_drawn_clip_however_many(clip, tmp_path):
    """5.3.2: a clip per scene passed the edit's ten inputs, and an 11-scene video failed to make."""
    from row_bot.video_editing import InputVideo, Look, SceneCut, Step, VideoInfo, build_plan

    source = InputVideo(path=str(clip), info=VideoInfo(duration=3.0, width=320, height=180, fps=10.0,
                                                       has_audio=False))
    step = Step(op="scenes", aspect="16:9", look=Look(framing="framed"), scenes=[
        SceneCut(input=None, end=2, card="Hello"),
        *[SceneCut(input=0, start=0.2 * index, end=0.2 * index + 0.2,
                   camera=((0.0, 0.5, 0.5, 1.0), (0.1, 0.9, 0.5, 2.0))) for index in range(12)],
    ])
    inputs, drawn, made = video_studio.prepare([source], step, tmp_path)

    assert [(item.path, item.concat) for item in inputs] == [("scenes.txt", True)], \
        "the recording itself isn't read again: its scenes' clips, one after another"
    assert made == ["", *["new"] * 12], "the card is the edit's; each scene was drawn"
    assert (inputs[0].info.width, inputs[0].info.height) == (1920, 1080)
    card, *scenes = drawn.scenes
    assert card.input is None and all(scene.input == 0 and scene.camera == () for scene in scenes)
    assert [scene.start for scene in scenes] == pytest.approx([5 / 24 * index for index in range(12)], abs=1e-3),         "one after another, each a whole number of frames at the video's rate (24 at least)"
    plan = build_plan(inputs, [drawn], "output.mp4")
    assert plan.duration == pytest.approx(2 + 12 * 5 / 24, abs=0.01)
    assert plan.args[plan.args.index("-f") + 1] == "concat" and plan.args.count("-i") == 2, \
        "ffmpeg reads the clips as one input, beside the cards' pictures"


@pytest.mark.slow
def test_making_a_video_again_draws_only_the_scenes_whose_inputs_changed(clip, tmp_path, monkeypatch):
    """C1: each scene's clip is kept under a hash of what it was drawn from. Making the video again draws only the
    scenes whose inputs changed; a scene's speed and line are the edit's, so changing them draws nothing; footage
    that changed on disk draws its scenes again; clips no make uses any more go."""
    from dataclasses import replace

    from row_bot.video_editing import InputVideo, SceneCut, Step, VideoInfo

    source = InputVideo(path=str(clip), info=VideoInfo(3.0, 320, 180, 10.0, False), identity="rec.mp4:1")
    scenes = [SceneCut(input=0, start=0.0, end=1.0), SceneCut(input=None, end=1, card="Hi"),
              SceneCut(input=0, start=1.0, end=2.0), SceneCut(input=0, start=2.0, end=3.0)]
    kept = tmp_path / "kept"

    def make(cuts, name, footage=source):
        (tmp_path / name).mkdir()
        return video_studio.prepare([footage], Step(op="scenes", aspect="16:9", scenes=cuts), tmp_path / name,
                                    cache=kept)[2]

    assert make(scenes, "first") == ["new", "", "new", "new"]
    assert len(list(kept.iterdir())) == 3, "one clip per scene of footage, kept with the project"
    edited = [scenes[0], scenes[1], replace(scenes[2], end=1.5), replace(scenes[3], speed=2.0, line="Faster.")]
    assert make(edited, "second") == ["reused", "", "new", "reused"], \
        "only the trimmed scene is drawn again: its speed and line are the edit's"
    with _scenes(tmp_path / "second") as reader:
        assert len(list(reader.decode(video=0))) == (1 + 0.5 + 1) * 24, "the kept clips and the new one, in order"

    monkeypatch.setattr(video_studio, "KEEP_SECONDS", -60.0)  # Every clip this make doesn't use is old.
    assert make(scenes, "third", replace(source, identity="rec.mp4:2")) == ["new", "", "new", "new"], \
        "the file changed: its scenes are drawn again"
    assert len(list(kept.iterdir())) == 3, "the clips no make used went"


def _left(reader):
    """A drawn clip's sound, its left channel, from -1 to 1 (plain 16-bit samples, side by side)."""
    import numpy

    return numpy.concatenate([frame.to_ndarray()[0][::2] for frame in reader.decode(audio=0)]) / 32768


def _scenes(folder):
    """The drawn scenes as the edit reads them: their clips, one after another."""
    import av

    return av.open(str(folder / "scenes.txt"), format="concat", options={"safe": "0"})


def _drawn(folder, sources, step):
    """The drawn clip of a project's scenes from these (path, size) videos: (its inputs, its frames)."""
    from row_bot.video_editing import InputVideo, VideoInfo

    folder.mkdir(exist_ok=True)
    given = [InputVideo(path=str(path), info=VideoInfo(duration=3.0, width=size[0], height=size[1], fps=10.0,
                                                      has_audio=False)) for path, size in sources]
    inputs, _step, _made = video_studio.prepare(given, step, folder)
    with _scenes(folder) as reader:
        return inputs, [frame.to_image() for frame in reader.decode(video=0)]


def _edges(picture):
    width, height = picture.size
    return [picture.getpixel(at) for at in ((2, height // 2), (width - 3, height // 2), (width // 2, 2),
                                            (width // 2, height - 3))]


def test_clips_of_other_shapes_fill_the_frame_by_default_and_are_inset_only_when_asked(clip, tmp_path):
    """M8 (22 Nov): a video's footage came out in a rounded inset on a backdrop although the person kept asking for
    full screen, and AI clips and screen footage of other sizes didn't play as one film."""
    from dataclasses import replace

    from row_bot.video_editing import Look, SceneCut, Step

    sources = [(video(tmp_path / "photo.mp4", sound=False, size=(320, 240)), (320, 240)),
               (video(tmp_path / "short.mp4", sound=False, size=(180, 320)), (180, 320))]
    scenes = [SceneCut(input=0, end=0.5), SceneCut(input=1, end=0.5)]

    inputs, frames = _drawn(tmp_path / "plain", sources, Step(op="scenes", aspect="16:9", scenes=scenes))
    assert [(item.info.width, item.info.height) for item in inputs] == [(1920, 1080)]
    assert frames[0].size == (1920, 1080) and len(frames) == 24, "at 24 frames a second at least: smooth motion"
    for picture in (frames[0], frames[-1]):  # A 4:3 clip, then a 9:16 one.
        assert all(min(pixel) > 200 for pixel in _edges(picture)), "the clip to every edge: no bars, no backdrop"

    _inputs, framed = _drawn(tmp_path / "framed", sources, Step(op="scenes", aspect="16:9", look=Look(
        framing="framed"), scenes=[scenes[0], replace(scenes[1], fit=False)]))
    assert all(max(pixel) < 150 for pixel in _edges(framed[0])) and min(framed[0].getpixel((960, 540))) > 200, \
        "the brand kit's framed look: the clip inset on the backdrop"
    assert all(min(pixel) > 200 for pixel in _edges(framed[-1])), "a scene's fit false fills the frame anyway"
    _inputs, fitted = _drawn(tmp_path / "fit", sources,
                             Step(op="scenes", aspect="16:9", scenes=[replace(scenes[0], fit=True), scenes[1]]))
    assert all(max(pixel) < 150 for pixel in _edges(fitted[0])), "a scene's fit: its whole picture, inset"
    assert all(min(pixel) > 200 for pixel in _edges(fitted[-1])), "the next scene fills the frame again"


def test_a_wide_recording_in_a_tall_video_fills_it_cropped_around_where_its_pointer_was(clip, tmp_path):
    from row_bot.video_editing import SceneCut, Step

    # From a second in, the recording's right half is red.
    scenes = [SceneCut(input=0, start=1.0, end=1.5, focus=(0.9, 0.5)), SceneCut(input=0, start=1.0, end=1.5)]
    inputs, frames = _drawn(tmp_path / "tall", [(clip, (320, 180))], Step(op="scenes", aspect="9:16", scenes=scenes))

    assert (inputs[0].info.width, inputs[0].info.height) == (1080, 1920) and frames[0].size == (1080, 1920)
    assert all(pixel[1] < 80 for pixel in _edges(frames[0])), "cropped on the right, where the pointer was"
    assert frames[-1].getpixel((2, 960))[1] > 200 and frames[-1].getpixel((1077, 960))[1] < 80, \
        "no pointer: its middle, the grey and the red"


def test_a_phones_picture_is_set_in_a_device_on_the_backdrop():
    picture = Image.new("RGB", (390, 844), (250, 250, 250))
    framed = compose(picture, Frame((1920, 1080), "framed", (20, 60, 140)))

    middle_x = 960
    screen_top = (1080 - round(844 * 1080 * video_studio.PHONE_FILL / 844)) // 2
    assert framed.getpixel((middle_x, 540)) == (250, 250, 250), "the screen in the middle"
    rim = framed.getpixel((middle_x, screen_top - 4))
    assert max(rim) < 60, "a dark rim around it, like the phone it was used on"
    assert framed.getpixel((200, 540))[2] > framed.getpixel((200, 540))[0], "the brand's backdrop either side"


def test_with_captions_the_picture_sits_above_them_instead_of_under_them():
    """5.3.2: the phone demo's captions covered the phone's message box, and the desktop demo's its composer."""
    from row_bot.video_editing import Step, phrase_limit, phrases, text_overlay

    line = "The written findings remain available while the deck takes shape, slide by slide."
    looks = {"boxed": ("caption", False), "bold": ("caption", True), "title": ("title", True)}

    def shown(look, text):
        style, plain = looks[look]
        return text_overlay(Step(op="text", text=text, style=style, position="bottom", plain=plain), 1920, 1080)

    for look in looks:
        longest = max(phrases(line, phrase_limit(look, 1920, 1080)), key=len)
        assert shown(look, longest).height == shown(look, "Hi").height, f"a {look} phrase is one line"
    caption = shown("bold", max(phrases(line, phrase_limit("bold", 1920, 1080)), key=len))
    for size in ((390, 844), (1600, 1000)):
        picture = Image.new("RGB", size, (250, 250, 250))
        plain = compose(picture, Frame((1920, 1080), "framed", (20, 60, 140)))
        captioned = compose(picture, Frame((1920, 1080), "framed", (20, 60, 140), captions=video_studio.CAPTION_ROOM))

        def screen_bottom(frame):
            return max(y for y in range(1080) if frame.getpixel((960, y)) == (250, 250, 250))

        assert screen_bottom(captioned) < caption.y < screen_bottom(plain), size


def test_a_videos_voice_lines_are_laid_at_their_times_into_one_track(tmp_path):
    import wave

    import numpy

    pytest.importorskip("av")
    for name, pitch in (("a.wav", 300), ("b.wav", 600)):
        with wave.open(str(tmp_path / name), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            tone = 0.5 * numpy.sin(numpy.arange(8000) * 2 * numpy.pi * pitch / 16000)
            writer.writeframes((tone * 32767).astype("<i2").tobytes())
    video_studio.narration([(1.0, tmp_path / "a.wav"), (2.5, tmp_path / "b.wav")], 4.0, tmp_path / "voice.wav")

    with wave.open(str(tmp_path / "voice.wav")) as reader:
        rate = reader.getframerate()
        sound = numpy.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2") / 32767
    assert sound.size == 4 * rate, "as long as the video"
    loud = [abs(sound[round(at * rate):round((at + 0.25) * rate)]).mean() > 0.2 for at in (0.5, 1.1, 2.0, 2.6, 3.5)]
    assert loud == [False, True, False, True, False], "each line at its time, quiet between them"


# ── Any footage (C2): pictures, any frame rate, phones, long clips, many scenes ───────────────────────────────


def _frames(path):
    import av

    with (_scenes(path.parent) if path.name == "scenes.txt" else av.open(str(path))) as reader:
        stream = reader.streams.video[0]
        frames = list(reader.decode(video=0))
        return stream.average_rate, frames


def test_a_picture_is_held_filling_the_frame_upright_and_pushed_in_slowly(clip, tmp_path):
    """A photo, a screenshot or generated art is a scene of its own: cropped to fill the video's shape, stood up
    by its EXIF orientation, and moving (Ken Burns), never a frozen slide."""
    upright = Image.new("RGB", (400, 300), (240, 240, 240))
    upright.paste((20, 60, 200), (0, 0, 400, 120))  # Its top 40% blue.
    exif = Image.Exif()
    exif[0x0112] = 6  # Saved on its side, as a phone saves a photo, with the orientation that stands it up.
    upright.rotate(90, expand=True).save(tmp_path / "photo.jpg", exif=exif, quality=95)
    info = video_studio.still_info(tmp_path / "photo.jpg")
    assert (info.width, info.height, info.still) == (400, 300, True)

    seconds = 4.0
    studio = video_studio.Studio(tmp_path / "scenes.mkv", (320, 180), fps=30)
    assert studio.still(tmp_path / "photo.jpg", seconds, video_studio.push_in(seconds), Frame((320, 180))) == \
        (0.0, 4.0)
    studio.close()

    rate, frames = _frames(tmp_path / "scenes.mkv")
    assert rate == 30 and len(frames) == 120 and (frames[0].width, frames[0].height) == (320, 180)
    first, last = frames[0].to_image(), frames[-1].to_image()

    def edge(picture):  # Where the blue top ends, down the middle.
        return next(y for y in range(180) if picture.getpixel((160, y))[0] > 150)

    assert first.getpixel((160, 4))[0] < 100 < first.getpixel((160, 175))[0], "upright: blue at the top"
    assert all(min(first.getpixel((x, 120))) > 200 for x in (2, 317)), "filling the frame: no bars either side"
    assert edge(last) <= edge(first) - 3, "pushed in over the scene: the picture grows towards its middle"
    assert video_studio.view(video_studio.push_in(seconds), seconds / 2)[2] < video_studio.PUSH_IN, "gently"


def test_a_pictures_scene_lasts_its_line_else_a_few_seconds_and_never_less_than_its_line():
    from row_bot.video_editing import LINE_PAD, SceneCut

    assert video_studio.still_seconds(SceneCut(input=0)) == video_studio.STILL_SECONDS
    said = SceneCut(input=0, line="Our first morning.", voice="line.wav", voice_seconds=5.0)
    assert video_studio.still_seconds(said) == 5.0 + LINE_PAD
    assert video_studio.still_seconds(SceneCut(input=0, end=8.0)) == 8.0, "as long as asked"
    assert video_studio.still_seconds(SceneCut(input=0, end=2.0, voice="line.wav", voice_seconds=5.0)) == \
        5.0 + LINE_PAD, "never cut off mid-line"


def test_footage_at_any_frame_rate_and_a_turned_phone_clip_come_out_at_one_rate_upright_in_hd_colour(tmp_path):
    """Phone clips (turned, 30 fps), AI shots (24 or 25) and 60 fps clips mix in one video: each comes out at the
    rate most of the footage has, upright, in BT.709 colour and TV range, with silence where a clip has no sound."""
    from row_bot.video_editing import InputVideo, SceneCut, Step, VideoInfo

    phone = video(tmp_path / "phone.mp4", sound=False, size=(180, 320), rotation=90, seconds=2)
    sixty = video(tmp_path / "sixty.mp4", sound=False, rate=60, seconds=2)
    pal = video(tmp_path / "pal.mp4", sound=True, rate=25, seconds=2)
    given = [InputVideo(path=str(phone), info=VideoInfo(2.0, 180, 320, 10.0, False)),
             InputVideo(path=str(sixty), info=VideoInfo(2.0, 320, 180, 60.0, False)),
             InputVideo(path=str(pal), info=VideoInfo(2.0, 320, 180, 25.0, True))]
    scenes = [SceneCut(input=0, start=1.0, end=2.0), SceneCut(input=1, start=0.5, end=2.0),
              SceneCut(input=2, start=1.0, end=1.5)]
    inputs, step, _made = video_studio.prepare(given, Step(op="scenes", scenes=scenes), tmp_path)

    assert [item.path for item in inputs] == ["scenes.txt"] and inputs[0].info.fps == 60.0, \
        "the rate most of the footage plays at (1.5 s of the 3 s at 60)"
    assert [(scene.start, scene.end) for scene in step.scenes] == [(0.0, 1.0), (1.0, 2.5), (2.5, 3.0)]
    rate, frames = _frames(tmp_path / "scenes.txt")
    assert rate == 60 and len(frames) == 180 and (frames[0].width, frames[0].height) == (180, 320)
    phone_frame = frames[30].to_image()
    assert phone_frame.getpixel((170, 160))[1] < 80 < phone_frame.getpixel((10, 160))[1], \
        "the phone clip upright: its right half red, as it was filmed"
    assert frames[89].to_image().getpixel((170, 160))[1] > 200 > frames[91].to_image().getpixel((170, 160))[1], \
        "the 60 fps clip from half a second in: red from its second second, on time"
    assert {(frame.colorspace, frame.color_range) for frame in frames} == {(1, 1)}, "BT.709, TV range"
    with _scenes(tmp_path) as reader:
        sound = sum(frame.samples for frame in reader.decode(audio=0))
    assert abs(sound / 48000 - 3.0) < 0.1, "sound all along: silence under the clips that have none"


def test_a_long_clip_is_read_only_around_the_part_used(tmp_path):
    """Real footage is long: a clip used for one second is read from a seek to the key frame before it."""
    long = video(tmp_path / "long.mp4", sound=True, seconds=30)
    studio = video_studio.Studio(tmp_path / "scenes.mkv", (320, 180), fps=10, sound=True)
    assert studio.add(long, 25.0, 26.0, (), Frame((320, 180))) == (0.0, 1.0)
    studio.close()

    assert studio.decoded <= 25, "a second or two of pictures decoded, not the 250 before it"
    _rate, frames = _frames(tmp_path / "scenes.mkv")
    assert len(frames) == 10 and frames[0].to_image().getpixel((300, 90))[1] < 80, "the part used, from 25 s"


def test_two_hundred_scenes_from_a_hundred_videos_and_pictures_make_one_short_ffmpeg_command(tmp_path, monkeypatch):
    """60 scenes and 10 sources were the most a video could have, and every source was one more input on
    ffmpeg's command. Now every scene is drawn into one clip, one source at a time, and the command stays short."""
    from row_bot.video_editing import InputVideo, Look, SceneCut, Step, VideoInfo, build_plan

    drawn: list[tuple] = []

    class Drawing:  # The drawing itself is tested above: here, what is drawn, in what order, how long.
        def __init__(self, output, size, *, fps, sound):
            self.fps, self.written, self.has_sound = fps, 0, sound
            output.write_bytes(b"")
            drawn.append(("clip", size, fps, sound))

        def add(self, source, start, end, keys, frame, *, stopped):
            return self._take(("video", source.name, start, end), end - start)

        def still(self, source, seconds, keys, frame):
            return self._take(("picture", source.name, seconds, keys[-1][3]), seconds)

        def _take(self, what, seconds):
            drawn.append(what)
            at = self.written / self.fps
            self.written += round(seconds * self.fps)
            return at, round(seconds * self.fps) / self.fps

        def close(self):
            drawn.append("closed")

    monkeypatch.setattr(video_studio, "Studio", Drawing)
    phone = VideoInfo(duration=600.0, width=2160, height=3840, fps=29.97, has_audio=True)
    picture = VideoInfo(duration=0.0, width=4032, height=3024, fps=30.0, has_audio=False, still=True)
    given = [InputVideo(path=f"D:/Footage/clip-{index:02d}.mov", info=phone) for index in range(80)]
    given += [InputVideo(path=f"D:/Footage/photo-{index:02d}.jpg", info=picture) for index in range(20)]
    scenes = []
    for number in range(200):
        source = number % 100
        line = f"Line {number}." if number % 2 else ""
        voice = {"voice": f"line-{number}.wav", "voice_seconds": 2.0} if line else {}
        start = 10.0 * (number // 100) + 5
        scenes.append(SceneCut(input=source, start=start if source < 80 else 0.0,
                               end=start + 3 if source < 80 else None, line=line, card=f"Day {number}"
                               if number % 10 == 0 else "", **voice))
    sounds = {scene.voice: f"C:/voices/{scene.voice}" for scene in scenes if scene.voice}
    step = Step(op="scenes", aspect="16:9", scenes=scenes, style="caption", look=Look())
    inputs, cut, _made = video_studio.prepare(given, step, tmp_path)

    clips = [what for what in drawn if what[0] == "clip"]
    assert len(clips) == 200 and set(clips) == {("clip", (1920, 1080), 30.0, True)} and drawn[-1] == "closed", \
        "a clip of its own per scene, all of one size, rate and sound"
    assert len(drawn) == 600 and drawn[1] == ("video", "clip-00.mov", 5.0, 8.0)
    held = [what for what in drawn if what[0] == "picture"]
    assert len(held) == 40 and {what[2] for what in held} == {video_studio.STILL_SECONDS, 2.6}, \
        "a picture lasts its line, else a few seconds"
    plan = build_plan(inputs, [cut], "output.mp4", sounds=sounds)
    assert len(plan.args) < 80 and plan.args.count("-i") == 3, "one clip, the voice track and the pictures laid"
    assert len(plan.marks) == 201 and len(plan.voices) == 100
