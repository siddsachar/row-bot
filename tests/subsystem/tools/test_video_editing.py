"""The ffmpeg arguments each video edit operation builds (F3), with no ffmpeg at all.

Every operation turns into filters of one graph and one H.264 MP4 encode. Inputs are opened
only as local video files; text is drawn into pictures, never written into the graph.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from row_bot.video_editing import (
    EditError, InputVideo, VideoInfo, build_plan, clock, describe, frame_times, frames_arguments, graph_option_for,
    parse_probe, parse_srt, parse_steps, progress_seconds,
)

pytestmark = pytest.mark.subsystem

HD = VideoInfo(duration=6.0, width=640, height=360, fps=30.0, has_audio=True)
SILENT = VideoInfo(duration=4.0, width=320, height=240, fps=25.0, has_audio=False)

PROBE = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'file:input-1.mp4':
  Duration: 00:01:02.50, start: 0.000000, bitrate: 868 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1920x1080 [SAR 1:1 DAR 16:9], 788 kb/s, 29.97 fps, 29.97 tbr, 15360 tbn (default)
      Side data:
        displaymatrix: rotation of -90.00 degrees
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, mono, fltp, 69 kb/s (default)
At least one output file must be specified
"""


def _plan(steps, inputs=None):
    videos = inputs or [InputVideo(path="input-1.mp4", info=HD)]
    return build_plan(videos, parse_steps(steps), "output.mp4")


def _picture(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_probe_reads_length_size_rate_sound_and_turns_portrait_video_upright():
    info = parse_probe(PROBE)
    assert (info.duration, info.width, info.height, info.fps, info.has_audio) == (62.5, 1080, 1920, 29.97, True)
    with pytest.raises(EditError, match="no video"):
        parse_probe("Input #0\n  Duration: 00:00:03.00\n  Stream #0:0: Audio: aac\n")


def test_inputs_are_local_video_files_only_and_the_result_is_an_h264_mp4():
    plan = _plan([])
    args = plan.args
    assert args[args.index("-i") - 4:args.index("-i") + 2] == [
        "-protocol_whitelist", "file", "-format_whitelist",
        "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpegts,flv,gif,ogg,asf", "-i", "file:input-1.mp4"]
    assert args[args.index("-c:v"):args.index("-c:v") + 6] == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    for option in (["-pix_fmt", "yuv420p"], ["-movflags", "+faststart"], ["-c:a", "aac"], ["-progress", "pipe:1"]):
        assert args[args.index(option[0]):args.index(option[0]) + 2] == option
    assert args[-1] == "file:output.mp4" and "-nostdin" in args
    assert args[args.index("-/filter_complex") + 1] == "graph.txt"
    assert plan.graph.endswith("fps=30,format=yuv420p[v3]")
    assert (plan.duration, plan.width, plan.height, plan.summary) == (6.0, 640, 360, "Converted to MP4")


def test_trim_cuts_picture_and_sound_together():
    plan = _plan([{"op": "trim", "start": 1, "end": 4}])
    assert "trim=start=1:end=4,setpts=PTS-STARTPTS" in plan.graph
    assert "atrim=start=1:end=4,asetpts=PTS-STARTPTS" in plan.graph
    assert plan.duration == 3


def test_join_fits_every_video_to_the_first_and_fills_missing_sound_with_silence():
    plan = _plan([], [InputVideo("input-1.mp4", HD), InputVideo("C:/work/clips/b.mp4", SILENT)])
    assert "[1:v]scale=640:360:force_original_aspect_ratio=decrease,pad=640:360" in plan.graph
    assert "fps=30" in plan.graph
    assert "anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration=4" in plan.graph
    assert "concat=n=2:v=1:a=1" in plan.graph
    assert "file:C:/work/clips/b.mp4" in plan.args
    assert (plan.duration, plan.summary) == (10.0, "Joined 2 videos")


def test_speeding_up_part_of_a_video_labels_it_and_keeps_the_sound_in_step():
    plan = _plan([{"op": "speed", "factor": 4, "start": 1, "end": 5}])
    assert "split=3" in plan.graph and "asplit=3" in plan.graph
    assert "setpts=PTS/4" in plan.graph
    assert "atempo=2,atempo=2" in plan.graph  # atempo takes at most 2 per filter.
    assert "concat=n=3:v=1:a=1" in plan.graph
    assert plan.duration == pytest.approx(3.0)  # 1 s, then 4 s at 4×, then 1 s.
    [(name, data)] = plan.overlays
    label = _picture(data)
    assert name == "o001.png" and label.mode == "RGBA" and label.width < 640 / 2
    assert f"overlay=x={640 - label.width - 16}:y=16" in plan.graph  # Top right, inside the margin.
    assert "file:o001.png" in plan.args
    assert plan.summary == "Sped up 4×"


def test_a_speed_label_can_be_left_off_and_slowing_down_chains_atempo():
    plan = _plan([{"op": "speed", "factor": 0.25, "label": False}])
    assert plan.overlays == [] and "atempo=0.5,atempo=0.5" in plan.graph
    assert plan.duration == pytest.approx(24.0)
    assert plan.summary == "Slowed to 0.25×"


def test_text_is_drawn_into_a_picture_shown_between_its_times_and_never_put_in_the_graph():
    words = "New: ']; [0:v] drawtext=text=x"
    plan = _plan([{"op": "text", "text": words, "start": 1, "end": 3, "position": "bottom-left"},
                  {"op": "text", "text": "Row-Bot 5.3", "style": "title"}])
    assert words not in plan.graph and "drawtext" not in plan.graph
    assert not any(words in arg for arg in plan.args)
    badge, title = (_picture(data) for _name, data in plan.overlays)
    assert badge.height < title.height  # A title is larger than a badge.
    assert f"overlay=x=16:y={360 - badge.height - 16}:eof_action=repeat:enable='between(t,1,3)'" in plan.graph
    assert f"overlay=x={(640 - title.width) // 2}:y={(360 - title.height) // 2}:eof_action=repeat[" in plan.graph
    assert plan.summary == "Added a badge and added a title"


def test_zoom_fills_the_picture_with_part_of_it_for_a_while_keeping_its_shape():
    plan = _plan([{"op": "zoom", "region": [0.5, 0.5, 0.4, 0.4], "start": 2, "end": 4}])
    assert "crop=256:144:" in plan.graph  # 0.4 of 640×360, as wide as it is tall in the same proportion.
    assert ",scale=640:360:flags=lanczos" in plan.graph
    assert "concat=n=3:v=1:a=0" in plan.graph  # Only the picture is cut; the sound plays on.
    assert "asplit" not in plan.graph
    assert (plan.duration, plan.width, plan.height) == (6.0, 640, 360)


def test_crop_keeps_part_of_the_picture_at_its_own_size():
    plan = _plan([{"op": "crop", "region": [0.1, 0.1, 0.5, 0.5]}])
    assert "crop=320:180:64:36" in plan.graph
    assert (plan.width, plan.height) == (320, 180)


SRT = """1
00:00:00,500 --> 00:00:02,000
<i>Hello</i> there

2
00:00:02.500 --> 00:00:04.000
Second caption

3
00:00:09,000 --> 00:00:10,000
After the end
"""


def test_captions_show_each_cue_at_its_times_and_skip_those_after_the_end():
    plan = _plan([{"op": "captions", "text": SRT}])
    assert len(plan.overlays) == 2
    assert "enable='between(t,0.5,2)'" in plan.graph and "enable='between(t,2.5,4)'" in plan.graph
    assert "Hello" not in plan.graph
    assert [cue.text for cue in parse_srt(SRT)] == ["Hello there", "Second caption", "After the end"]
    with pytest.raises(EditError, match="No captions were found"):
        parse_srt("just some words")


@pytest.mark.parametrize(("steps", "words"), [
    ([{"op": "fade"}], "fade isn't an operation"),
    ([{"op": "trim", "start": 4, "end": 2}], "end must be after start"),
    ([{"op": "speed", "factor": 1}], "speed needs a factor"),
    ([{"op": "speed", "factor": 40}], "speed needs a factor"),
    ([{"op": "zoom", "region": [0.2, 0.2]}], "region is \\[x, y, width, height\\]"),
    ([{"op": "zoom"}], "zoom needs a region"),
    ([{"op": "zoom", "region": [0.2, 0.2, 2, 0.5]}], "fractions of the picture"),
    ([{"op": "crop", "region": [0, 0, 0.5, 0.5], "start": 1}], "use zoom for part of it"),
    ([{"op": "text"}], "text needs the words"),
    ([{"op": "text", "text": "x", "position": "middle"}], "position must be one of"),
])
def test_steps_that_cannot_be_made_say_why(steps, words):
    with pytest.raises(EditError, match=words):
        parse_steps(steps)


def test_times_outside_the_video_are_refused_plainly():
    with pytest.raises(EditError, match="outside the video, which is 6 s long"):
        _plan([{"op": "trim", "start": 7, "end": 9}])


def test_steps_apply_in_order_on_the_video_as_it_is_then():
    plan = _plan([{"op": "trim", "start": 0, "end": 4}, {"op": "speed", "factor": 2}, {"op": "captions", "text": SRT}])
    assert plan.duration == pytest.approx(2.0)
    assert len(plan.overlays) == 2  # The label, and the one caption that starts within 2 s.
    assert describe(parse_steps([{"op": "trim", "end": 2}, {"op": "speed", "factor": 2}, {"op": "captions",
                                 "text": SRT}]), 3) == "Joined 3 videos, trimmed, sped up 2× and captioned"


def test_frames_are_taken_by_one_run_with_a_seek_per_still():
    times = frame_times(10.0, 4)
    assert times == [1.25, 3.75, 6.25, 8.75]
    args = frames_arguments("input-1.mp4", times, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"], max_width=640)
    assert args.count("-ss") == 4 and args.count("file:input-1.mp4") == 4
    assert args[args.index("-map"):args.index("-map") + 4] == ["-map", "0:v:0", "-frames:v", "1"]
    assert args[-1] == "file:d.jpg" and "scale='min(640,iw)':-2" in args
    assert frame_times(10.0, 3, times=[12, 2, 2]) == [2.0, 9.95]  # Inside the video, once each.


def test_progress_ffmpeg_version_options_and_clock_words():
    assert progress_seconds("frame=10\nout_time_us=1500000\nprogress=continue\nout_time_ms=2500000\n") == 2.5
    assert progress_seconds("nothing yet") is None
    assert graph_option_for("7.1-essentials_build-www.gyan.dev") == "-/filter_complex"
    assert graph_option_for("6.1.1") == graph_option_for("N-112345-gabc") == "-filter_complex_script"
    assert [clock(value) for value in (0, 42, 1.5, 62.25, 3730)] == ["0:00", "0:42", "0:01.5", "1:02.2", "1:02:10"]


def test_voice_lines_and_ducked_music_are_laid_on_the_finished_video_at_one_loudness():
    """5.3.1 B: a narrated video's sound in one pass: voices at their times, music under them."""
    steps = parse_steps([
        {"op": "trim", "start": 1},
        {"op": "sound", "source": "scene-1.wav", "start": 0.5},
        {"op": "sound", "source": "scene-2.wav", "start": 3},
        {"op": "sound", "source": "music-calm.wav", "role": "music"},
        {"op": "sound", "role": "original", "volume": 0.2},
    ])
    plan = build_plan([InputVideo(path="input-1.mp4", info=HD)], steps, "output.mp4",
                      sounds={"scene-1.wav": "sound-1.wav", "scene-2.wav": "sound-2.wav",
                              "music-calm.wav": "C:/music/calm.wav"})

    sound_inputs = [plan.args[i + 1] for i, a in enumerate(plan.args) if a == "-i" and "sound" in plan.args[i + 1]]
    assert sound_inputs == ["file:sound-1.wav", "file:sound-2.wav"] and "file:C:/music/calm.wav" in plan.args
    assert "wav" in plan.args[plan.args.index("file:sound-1.wav") - 2], "plain audio files open as audio"
    assert "[1:a]" in plan.graph and "adelay=500|500" in plan.graph and "adelay=3000|3000" in plan.graph
    assert "[3:a]" in plan.graph and "volume=0.300" in plan.graph and "afade=t=out" in plan.graph
    assert "sidechaincompress" in plan.graph, "the music dips while a voice speaks"
    assert "volume=0.200" in plan.graph, "the video's own sound, turned down"
    assert plan.graph.count("loudnorm=I=-16") == 1 and "atrim=0:5" in plan.graph, "one loudness, the video's length"
    assert plan.has_audio and describe(steps) == "Trimmed, added a voice, added music and set the sound level"


def test_a_projects_scenes_are_cut_in_order_with_speed_zoom_card_caption_and_their_voice_at_each_start():
    """5.3.1 B3: a video project renders in one run; each scene is a piece of one of the inputs."""
    from row_bot.video_editing import SceneCut, Step

    scenes = Step(op="scenes", style="caption", scenes=[
        SceneCut(input=1, start=1, end=3, card="Sign up", line="First, sign up."),
        SceneCut(input=0, start=0, end=4, speed=2, zoom=(0.5, 0.5, 0.5, 0.5), voice="line-2.wav"),
    ])
    plan = build_plan([InputVideo("input-1.mp4", HD), InputVideo("input-2.mp4", SILENT)], [scenes], "output.mp4",
                      sounds={"line-2.wav": "sound-1.wav"})

    # The second input starts 6 s into the joined inputs: its 1–3 s is 7–9 s there, and comes first.
    assert plan.graph.index("trim=start=7:end=9") < plan.graph.index("trim=start=0:end=4")
    assert "setpts=PTS/2" in plan.graph and "atempo=2" in plan.graph
    assert "crop=" in plan.graph, "the second scene punches in"
    assert len(plan.overlays) == 3, "the card, the first scene's caption and the 2× badge"
    assert "file:sound-1.wav" in plan.args and "adelay=2000|2000" in plan.graph, "its line starts with it"
    assert (plan.duration, plan.marks) == (4.0, [0.0, 2.0, 4.0])
    assert plan.summary == "Joined 2 videos and cut 2 scenes"
    with pytest.raises(EditError, match="Scene 1: .* outside its video"):
        build_plan([InputVideo("input-1.mp4", HD)],
                   [Step(op="scenes", scenes=[SceneCut(input=0, start=8)])], "output.mp4")


def test_a_project_in_another_shape_reframes_each_scene_on_its_zoom_and_keeps_the_speed_badge_in_frame():
    from row_bot.video_editing import SceneCut, Step, gif_arguments

    scenes = Step(op="scenes", aspect="9:16", scenes=[
        SceneCut(input=0, end=2, zoom=(0.75, 0.0, 0.25, 0.5)), SceneCut(input=0, start=2, end=6, speed=2)])
    plan = build_plan([InputVideo("input-1.mp4", HD)], [scenes], "output.mp4")

    # A tall cut is 1080×1920 whatever the source. The zoomed scene is cropped on its zoom (640×360 tall is
    # 202×360, at the right edge); the wide scene without one is shown whole over a blurred copy of itself,
    # never cropped to its middle third with its words cut in half (31 Oct: the teaser's phone cut was 606×1080).
    assert "crop=202:360:438:0,scale=1080:1920" in plan.graph and "crop=202:360:219:0" not in plan.graph
    assert "boxblur" in plan.graph and "scale=1080:-2" in plan.graph and "overlay=(W-w)/2:(H-h)/2" in plan.graph
    assert (plan.width, plan.height, plan.marks) == (1080, 1920, [0.0, 2.0, 4.0])
    assert _every_stream_is_used(plan)
    assert len(plan.overlays) == 1, "the 2× badge, laid on the tall picture"
    same = build_plan([InputVideo("input-1.mp4", HD)], [Step(op="scenes", aspect="16:9", scenes=[SceneCut(input=0)])],
                      "output.mp4")
    assert "crop=" not in same.graph and (same.width, same.height) == (640, 360), "its own shape: as it is"
    with pytest.raises(EditError, match="16:9, 1:1 or 9:16"):
        build_plan([InputVideo("input-1.mp4", HD)], [Step(op="scenes", aspect="4:3", scenes=[SceneCut(input=0)])],
                   "output.mp4")

    gif = gif_arguments("input-1.mp4", "output.gif")
    assert gif[gif.index("-filter_complex") + 1].startswith("[0:v]fps=12,scale=480:-2")
    assert "palettegen" in " ".join(gif) and gif[-1] == "file:output.gif" and "file:input-1.mp4" in gif


def test_a_caption_reads_the_same_in_a_square_or_tall_cut_as_in_a_wide_one():
    from row_bot.video_editing import render_text

    line = ("First, let's see who's coming back for another cup. We ask Row-Bot to check the October "
            "orders file and show customers who ordered more than once.")
    wide = render_text(line, "caption", 1280, 720)
    # The same lines at the same size, nothing cut short, whatever the shape of a picture this wide.
    assert render_text(line, "caption", 1280, 1280)[1:] == wide[1:]
    assert render_text(line, "caption", 1280, 2276)[1:] == wide[1:]
    assert render_text(line, "caption", 720, 1280)[1] <= 720


def test_a_square_cut_of_a_scene_without_a_zoom_is_centred_where_its_pointer_was():
    from row_bot.video_editing import SceneCut, Step

    scenes = Step(op="scenes", aspect="1:1", scenes=[
        SceneCut(input=0, end=2, focus=(0.9, 0.2)), SceneCut(input=0, start=2, end=4)])
    plan = build_plan([InputVideo("input-1.mp4", HD)], [scenes], "output.mp4")

    # 640×360 square is 360×360: the first scene at the right edge, where the pointer was; the second in the middle;
    # each at the standard 1080×1080.
    assert "crop=360:360:280:0,scale=1080:1080" in plan.graph and "crop=360:360:140:0,scale=1080:1080" in plan.graph
    assert (plan.width, plan.height) == (1080, 1080)


def test_the_brand_kit_puts_an_intro_and_outro_on_its_colour_and_its_logo_in_a_corner():
    from row_bot.video_editing import Look, SceneCut, Step

    logo = io.BytesIO()
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(logo, format="PNG")
    look = Look(colour=(17, 34, 51), captions="bold", logo=logo.getvalue(), logo_corner="bottom-right",
                intro="Row-Bot", outro="row-bot.ai")
    scenes = Step(op="scenes", style="caption", look=look, scenes=[
        SceneCut(input=0, end=3, card="Sign up", line="First, sign up.", voice="line-1.wav")])
    plan = build_plan([InputVideo("input-1.mp4", HD)], [scenes], "output.mp4", sounds={"line-1.wav": "sound-1.wav"})

    assert plan.graph.count("color=c=0x112233:s=640x360") == 2, "an intro and an outro card on its colour"
    assert plan.graph.count("anullsrc") == 2, "each with silence, so the sound stays in step"
    assert (plan.duration, plan.marks) == (7.0, [2.0, 5.0]), "the scene starts after the intro"
    assert "adelay=2000|2000" in plan.graph, "its line starts with the scene, after the intro"
    pictures = [_picture(png) for _name, png in plan.overlays]
    backdrops = [picture for picture in pictures if picture.size == (640, 360)]
    card, caption, intro, outro, mark = (picture for picture in pictures if picture.size != (640, 360))
    assert len(backdrops) == 2, "the intro and outro each on a backdrop of the brand's colour"
    assert card.getpixel((2, card.height // 2))[:3] == (17, 34, 51), "the title card is the brand's colour"
    assert caption.getpixel((0, 0))[3] == 0, "a bold caption has no box"
    assert intro.getpixel((0, 0))[3] == 0 and outro.getpixel((0, 0))[3] == 0, "plain titles on the coloured cards"
    assert (mark.width, mark.height) == (72, 36), "the logo, a tenth of the picture's height"
    assert "overlay=x=552:y=308" in plan.graph, "in the bottom-right corner, 16 px in"
    # The scene's card sits in the lower third on the left, not over the middle of the picture where the action is
    # (31 Oct: the OpenStreetMap how-to's step cards covered the directions they named).
    assert f"overlay=x=16:y={round(360 * 0.68) - card.height // 2}:" in plan.graph


def _every_stream_is_used(plan) -> bool:
    """ffmpeg refuses a graph with a made stream nothing reads ("Error binding filtergraph inputs/outputs")."""
    import re

    text = plan.graph + " " + " ".join(plan.args)
    return all(text.count(label) >= 2 for label in set(re.findall(r"\[[av]\d+\]", plan.graph)))


def test_a_title_card_scene_needs_no_video_and_shows_its_card_on_the_brand_colour_for_its_length():
    """5.3.1 beta F17: a title, an end card or a video of cards alone, without a made-up background video."""
    from row_bot.video_editing import Look, SceneCut, Step

    look = Look(colour=(17, 34, 51))
    cards = Step(op="scenes", aspect="1:1", look=look, scenes=[
        SceneCut(input=None, end=3, card="Row-Bot 5.3", voice="line-1.wav"),
        SceneCut(input=None, end=2, card="row-bot.ai", speed=2)])
    alone = build_plan([], [cards], "output.mp4", sounds={"line-1.wav": "sound-1.wav"})

    assert alone.graph.count("color=c=0x112233:s=1080x1080") == 2, "each card on the brand colour, square"
    assert (alone.duration, alone.marks) == (5.0, [0.0, 3.0, 5.0]), "a card keeps its length: no speed"
    pictures = [_picture(png) for _name, png in alone.overlays]
    backdrops = [picture for picture in pictures if picture.size == (1080, 1080)]
    titles = [picture for picture in pictures if picture.size != (1080, 1080)]
    assert len(backdrops) == 2 and len(titles) == 2 and "[0:a]" in alone.graph, \
        "each card on its backdrop with its title, and the voice is the only input"
    assert titles[0].getpixel((0, 0))[3] == 0, "a plain title: the card's backdrop is its box"
    # The backdrop is the brand's colour, lit from the top left and darker at the edges: a slide, not a flat fill.
    top_left, centre, bottom_right = (backdrops[0].getpixel(at)[:3] for at in ((40, 40), (540, 540), (1040, 1040)))
    assert sum(top_left) > sum(centre) > sum(bottom_right)
    assert all(abs(value - brand) < 30 for value, brand in zip(centre, (17, 34, 51), strict=True))
    # Its title has a soft shadow, not a hard black outline.
    shadowed = [titles[0].getpixel((x, y)) for x in range(titles[0].width) for y in range(titles[0].height)]
    assert any(0 < alpha < 120 for *_rgb, alpha in shadowed), "a soft edge"
    assert _every_stream_is_used(alone)

    mixed = build_plan([InputVideo("input-1.mp4", HD)], [Step(op="scenes", look=look, scenes=[
        SceneCut(input=None, end=2, card="Sign up"), SceneCut(input=0, start=1, end=4)])], "output.mp4")
    assert "color=c=0x112233:s=640x360" in mixed.graph and "anullsrc" in mixed.graph, "silence keeps sound in step"
    assert (mixed.duration, mixed.marks) == (5.0, [0.0, 2.0, 5.0]) and _every_stream_is_used(mixed)
    with pytest.raises(EditError, match="Give at least one video"):
        build_plan([], [Step(op="scenes", scenes=[SceneCut(input=0)])], "output.mp4")


def test_turning_the_videos_own_sound_off_keeps_only_the_voice_or_leaves_no_sound():
    """A video's own sound at volume 0 once broke the edit, alone or under a voice."""
    muted = _plan([{"op": "sound", "role": "original", "volume": 0}])
    assert "volume=0.000" in muted.graph and "amix=inputs=0" not in muted.graph and _every_stream_is_used(muted)

    voiced = build_plan([InputVideo(path="input-1.mp4", info=HD)],
                        parse_steps([{"op": "sound", "role": "original", "volume": 0},
                                     {"op": "sound", "source": "line.wav"}]), "output.mp4",
                        sounds={"line.wav": "sound-1.wav"})
    assert voiced.has_audio and "[1:a]" in voiced.graph and _every_stream_is_used(voiced)


def test_a_narrated_video_with_long_quiet_gaps_or_a_long_held_picture_needs_attention():
    """The Release kit's demo said 3 s of each 18 s scene and held the picture for 14 s: "Sound: present" passed it."""
    from row_bot.video_editing import check_arguments, read_checks

    minute = VideoInfo(duration=60.0, width=1280, height=720, fps=30.0, has_audio=True)
    assert "silencedetect" in " ".join(check_arguments("output.mp4", has_audio=True, narrated=True))
    assert "silencedetect" not in " ".join(check_arguments("output.mp4", has_audio=True))
    heard = ("mean_volume: -25.0 dB\nmax_volume: -3.9 dB\n"
             "silence_start: 2.87\nsilence_end: 18.04 | silence_duration: 15.17\n"
             "silence_start: 20.87\nsilence_end: 36.04 | silence_duration: 15.17\n"
             "silence_start: 57.5\n")
    gappy = read_checks(heard, minute, narrated=True)
    assert not gappy.passed
    assert gappy.lines[3] == "Quiet: 0:02.9–0:18, 0:20.9–0:36 with no voice. Give those parts a line, or cut them shorter."
    assert read_checks(heard, minute).passed, "a recording's own quiet isn't a gap in a narration"

    held = read_checks("mean_volume: -19.0 dB\nmax_volume: -2.0 dB\n"
                       "lavfi.freezedetect.freeze_start: 4.4\nlavfi.freezedetect.freeze_duration: 14.2\n", minute)
    assert not held.passed and held.lines[3].startswith("Still: 0:04.4 for 14 s: too long to watch.")
    # Under the voice a viewer reads along: a narrated picture may hold up to 20 s (demo run 2, 30 Oct, re-cut its
    # video twelve times for 11 s holds), not longer.
    spoken = "mean_volume: -19.0 dB\nmax_volume: -2.0 dB\nlavfi.freezedetect.freeze_start: 4.4\n"
    assert read_checks(spoken + "lavfi.freezedetect.freeze_duration: 14.2\n", minute, narrated=True).passed
    assert not read_checks(spoken + "lavfi.freezedetect.freeze_duration: 22.0\n", minute, narrated=True).passed

    voiced = build_plan([InputVideo(path="input-1.mp4", info=HD)], parse_steps([{"op": "sound", "source": "a.wav"}]),
                        "output.mp4", sounds={"a.wav": "sound-1.wav"})
    assert voiced.narrated and not _plan([{"op": "trim", "start": 1}]).narrated


def test_a_silent_video_gets_its_narration_and_sounds_are_checked():
    plan = build_plan([InputVideo(path="input-1.mp4", info=SILENT)],
                      parse_steps([{"op": "sound", "source": "line.wav"}]), "output.mp4",
                      sounds={"line.wav": "sound-1.wav"})
    assert plan.has_audio and "-an" not in plan.args and "sidechaincompress" not in plan.graph

    with pytest.raises(EditError, match="isn't a sound file Row-Bot found"):
        build_plan([InputVideo(path="input-1.mp4", info=SILENT)],
                   parse_steps([{"op": "sound", "source": "missing.wav"}]), "output.mp4", sounds={})
    with pytest.raises(EditError, match="after the video ends"):
        build_plan([InputVideo(path="input-1.mp4", info=SILENT)],
                   parse_steps([{"op": "sound", "source": "line.wav", "start": 9}]), "output.mp4",
                   sounds={"line.wav": "sound-1.wav"})
    for bad, words in (({"op": "sound"}, "needs its source"), ({"op": "sound", "source": "a.wav", "role": "song"},
                       "role is voice, music or original"), ({"op": "sound", "source": "a.wav", "volume": 9},
                       "volume is from 0")):
        with pytest.raises(EditError, match=words):
            parse_steps([bad])


def test_checks_pass_a_video_with_sound_and_list_what_needs_attention():
    from row_bot.video_editing import check_arguments, read_checks

    args = check_arguments("output.mp4", has_audio=True)
    assert "volumedetect" in args and any("blackdetect" in a and "freezedetect" in a for a in args)
    assert "volumedetect" not in check_arguments("output.mp4", has_audio=False)

    good = read_checks("[Parsed_volumedetect_0] mean_volume: -18.2 dB\n[Parsed_volumedetect_0] max_volume: -1.0 dB\n"
                       "black_start:0 black_end:0.6 black_duration:0.6\n"
                       "lavfi.freezedetect.freeze_start: 12.2\nlavfi.freezedetect.freeze_duration: 4.1\n", HD)
    assert good.passed and good.lines[1] == "Sound: present (average -18.2 dB, peak -1.0 dB)."
    assert good.lines[2] == "Black: none.", "a fade-in at the start isn't a problem"
    assert good.lines[3].startswith("Still: 0:12.2 for 4 s")

    quiet = read_checks("mean_volume: -48.0 dB\nmax_volume: -30.0 dB\nblack_start:2 black_end:4 black_duration:2\n", HD)
    assert not quiet.passed and "too quiet" in quiet.lines[1] and quiet.lines[2] == "Black: 0:02–0:04."
    assert read_checks("", SILENT).lines[1].startswith("Sound: none.")
