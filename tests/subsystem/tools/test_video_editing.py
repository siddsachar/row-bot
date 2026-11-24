"""The ffmpeg arguments each video edit operation builds (F3), with no ffmpeg at all.

Every operation turns into filters of one graph and one H.264 MP4 encode. Inputs are opened
only as local video files; text is drawn into pictures, never written into the graph.
"""

from __future__ import annotations

import io
import re

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
    plan = _plan([{"op": "trim", "start": 1, "end": 4}, {"op": "text", "text": "Hi"}])
    assert plan.args[plan.args.index("-i") - 8:plan.args.index("-i") - 4] == ["-ss", "1", "-t", "3"],         "only the trim is read, found by seeking: nothing before it is decoded"
    assert "trim=" not in plan.graph
    assert (plan.duration, plan.summary) == (3, "Trimmed and added a badge")
    joined = _plan([{"op": "trim", "start": 1, "end": 4}], [InputVideo("input-1.mp4", HD)] * 2)
    assert "-ss" not in joined.args, "a trim of joined videos cuts the joined film"
    assert "trim=start=1:end=4,setpts=PTS-STARTPTS" in joined.graph
    assert "atrim=start=1:end=4,asetpts=PTS-STARTPTS" in joined.graph


def test_an_hours_video_trimmed_to_ten_seconds_is_read_from_a_seek_to_the_trim():
    hour = VideoInfo(duration=3600.0, width=3840, height=2160, fps=30.0, has_audio=True)
    plan = _plan([{"op": "trim", "start": 1800, "end": 1810}], [InputVideo("C:/footage/day.mov", hour)])
    before_input = plan.args[:plan.args.index("-i")]
    assert before_input[before_input.index("-ss") + 1] == "1800" and before_input[before_input.index("-t") + 1] == "10"
    assert plan.duration == 10


def test_join_fills_every_video_to_the_first_and_fills_missing_sound_with_silence():
    plan = _plan([], [InputVideo("input-1.mp4", HD), InputVideo("C:/work/clips/b.mp4", SILENT)])
    # A 4:3 clip joined to a 16:9 one fills its frame, cropped: one film, never black bars.
    assert "[1:v]scale=640:360:force_original_aspect_ratio=increase:out_color_matrix=bt709:out_range=tv,crop=640:360"         in plan.graph, "and in HD colour, as every input is"
    assert "pad=" not in plan.graph
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
    [data] = plan.overlays
    label = _picture(data)
    assert label.mode == "RGBA" and label.width < 640 / 2
    assert f"overlay=x={640 - label.width - 16}:y=16" in plan.graph  # Top right, inside the margin.
    assert "file:overlays.png" in plan.args
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
    badge, title = (_picture(data) for data in plan.overlays)
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


def test_voice_lines_and_ducked_music_are_mixed_on_the_made_picture_at_one_loudness():
    """C6: a video given sound is its picture (with the footage's own sound as cut), then its mix laid beside it:
    voices at their times, music under them, the video's own sound, brought to one loudness."""
    from row_bot.video_editing import final_arguments
    from row_bot.video_sound import Track

    steps = parse_steps([
        {"op": "trim", "start": 1},
        {"op": "sound", "source": "scene-1.wav", "start": 0.5},
        {"op": "sound", "source": "scene-2.wav", "start": 3},
        {"op": "sound", "source": "music-calm.wav", "role": "music"},
        {"op": "sound", "role": "original", "volume": 0.2},
    ])
    plan = build_plan([InputVideo(path="input-1.mp4", info=HD)], steps, "output.mp4",
                      sounds={"scene-1.wav": "sound-1.wav", "scene-2.wav": "sound-2.wav",
                              "music-calm.wav": "C:/music/calm.wav"}, loudness=-16)

    assert plan.args[-1] == "file:picture.mkv" and plan.args.count("-i") == 1, "the picture's run reads the video only"
    assert plan.args[plan.args.index("-c:a") + 1] == "pcm_s16le", "its own sound kept as cut, for the mix"
    assert set(plan.mix.tracks) == {Track("sound-1.wav", 0.5, 1.0, "voice"), Track("sound-2.wav", 3.0, 1.0, "voice"),
                                    Track("C:/music/calm.wav", 0.0, 0.3, "music"),
                                    Track("picture.mkv", 0.0, 0.2, "original")}
    assert (plan.mix.seconds, plan.mix.duck, plan.mix.loudness) == (5.0, True, -16), "the music dips under the voice"
    assert plan.has_audio and describe(steps) == "Trimmed, added a voice, added music and set the sound level"
    final = final_arguments("C:/kept/picture.mkv", "mix.wav", "output.mp4")
    assert final[final.index("-c:v") + 1] == "copy", "the picture is used as it was made"
    assert "file:C:/kept/picture.mkv" in final and "wav" in final[final.index("file:mix.wav") - 2]
    assert final[-1] == "file:output.mp4" and "aac" in final


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
    assert len(plan.overlays) == 2, "the card and the first scene's caption; a sped-up scene has no badge (U26)"
    assert plan.voices == [(2.0, "sound-1.wav")] and plan.mix.tracks[0].path == "narration.wav", \
        "its line starts with it, in the narration track the mix lays"
    assert (plan.duration, plan.marks) == (4.0, [0.0, 2.0, 4.0])
    assert plan.summary == "Joined 2 videos and cut 2 scenes"
    with pytest.raises(EditError, match="Scene 1: .* outside its video"):
        build_plan([InputVideo("input-1.mp4", HD)],
                   [Step(op="scenes", scenes=[SceneCut(input=0, start=8)])], "output.mp4")


def test_each_scene_lasts_until_its_voice_line_ends_so_no_line_runs_into_the_next():
    """5.3.2: a line longer than its scene made two voices talk at once. Now a card grows, a fast-forward slows,
    footage runs on into its recording where no other scene uses it, and what's still short holds its last frame."""
    from row_bot.video_editing import LINE_PAD, SceneCut, Step

    long = 12.0
    scenes = Step(op="scenes", scenes=[
        SceneCut(input=None, end=2, card="Your morning", voice="a.wav", voice_seconds=3.4),
        SceneCut(input=0, start=0, end=4, speed=4, voice="b.wav", voice_seconds=1.9),
        SceneCut(input=0, start=6, end=7, voice="c.wav", voice_seconds=2.4),
        SceneCut(input=0, start=9, end=10, voice="d.wav", voice_seconds=2.4),
        SceneCut(input=0, start=10, end=11, card="Done"),
    ])
    plan = build_plan([InputVideo("input-1.mp4", VideoInfo(duration=long, width=640, height=360, fps=30.0,
                                                           has_audio=True))], [scenes], "output.mp4",
                      sounds={name: f"sound-{name}" for name in ("a.wav", "b.wav", "c.wav", "d.wav")})

    lengths = [round(b - a, 2) for a, b in zip(plan.marks, plan.marks[1:])]
    assert lengths[0] == round(3.4 + LINE_PAD, 2), "the card grows to its line"
    assert "setpts=PTS/1.6" in plan.graph and lengths[1] == 2.5, "4× slows to fit, badge and all"
    assert "trim=start=6:end=9" in plan.graph and lengths[2] == 3.0, "runs on to where the next scene starts"
    assert "tpad=stop_mode=clone:stop_duration=2" in plan.graph and lengths[3] == 3.0,         "the next second is another scene's: its last frame holds instead"
    assert "apad=pad_dur=2" in plan.graph, "its sound stays in step"
    assert lengths[4] == 1.0, "a scene without a voice keeps its length"
    voiced = [(plan.marks[index], seconds) for index, seconds in enumerate((3.4, 1.9, 2.4, 2.4))]
    for (begin, seconds), next_begin in zip(voiced, plan.marks[1:]):
        assert begin + seconds + LINE_PAD <= next_begin + 0.01, "each line ends before the next scene starts"
    assert _every_stream_is_used(plan)


def test_a_sped_up_scene_carries_no_badge_and_a_gif_is_made_small():
    from row_bot.video_editing import SceneCut, Step, gif_arguments

    scenes = Step(op="scenes", scenes=[SceneCut(input=0, end=2), SceneCut(input=0, start=2, end=6, speed=2)])
    plan = build_plan([InputVideo("input-1.mp4", HD)], [scenes], "output.mp4")

    assert (plan.width, plan.height, plan.marks) == (640, 360, [0.0, 2.0, 4.0])
    assert plan.overlays == [], "no speed badge on a sped-up scene (U26)"
    assert _every_stream_is_used(plan)

    gif = gif_arguments("input-1.mp4", "output.gif")
    assert gif[gif.index("-filter_complex") + 1].startswith("[0:v]fps=12,scale=480:-2")
    assert "palettegen" in " ".join(gif) and gif[-1] == "file:output.gif" and "file:input-1.mp4" in gif


def test_a_caption_is_sized_by_the_frames_height_fits_its_width_and_wraps_onto_two_lines_at_most():
    """C7: captions read on a phone and a TV alike: 4.5% of the height (49 px at 1080p), as a tall short's are,
    never wider than the frame, and a long one on two lines at most."""
    from row_bot.video_editing import caption_size, render_text

    assert caption_size("box", 1920, 1080) == 49 and caption_size("box", 1280, 720) == 32
    short = "Who's coming back for another cup?"
    wide, tall = render_text(short, "caption", 1280, 720), render_text(short, "caption", 720, 1280)
    assert tall[2] > 1.4 * wide[2], "a tall frame's caption is larger, as its height is"
    line = ("First, let's see who's coming back for another cup. We ask Row-Bot to check the October "
            "orders file and show customers who ordered more than once.")
    for width, height in ((1280, 720), (720, 1280), (1080, 1080)):
        _png, shown_width, shown_height = render_text(line, "caption", width, height)
        size = caption_size("box", width, height)
        assert shown_width <= width * 0.86 + 1 and shown_height < 3.5 * size, (width, height)


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
    assert plan.voices == [(2.0, "sound-1.wav")], "its line starts with the scene, after the intro"
    pictures = [_picture(png) for png in plan.overlays]
    backdrops = [picture for picture in pictures if picture.size == (640, 360)]
    card, caption, intro, outro, end_logo, mark = (picture for picture in pictures if picture.size != (640, 360))
    assert len(backdrops) == 2, "the intro and outro each on a backdrop of the brand's colour"
    lit, right = card.getpixel((card.width // 3, card.height // 2)), card.getpixel((card.width - 12, card.height // 2))
    assert all(abs(a - b) < 12 for a, b in zip(right[:3], (17, 34, 51))) and sum(lit[:3]) > sum(right[:3]), \
        "the scene's title is a card of the brand's colour, lit from the left"
    assert card.getpixel((0, 0))[3] < 60, "its edge is a soft shadow, not a hard box"
    assert caption.getpixel((0, 0))[3] == 0, "a bold caption has no box"
    assert intro.getpixel((0, 0))[3] == 0 and outro.getpixel((0, 0))[3] == 0, "plain titles on the coloured cards"
    assert (mark.width, mark.height) == (72, 36), "the logo, a tenth of the picture's height"
    assert "overlay=x=552:y=308:eof_action=repeat:enable='between(t,0,5)'" in plan.graph, \
        "in the bottom-right corner, 16 px in, until the end card"
    assert end_logo.height > 1.5 * mark.height and \
        f"overlay=x={(640 - end_logo.width) // 2}:" in plan.graph, "C5: the end card has the logo, larger, centred"
    # The scene's card sits in the lower third on the left, not over the middle of the picture where the action is
    # (31 Oct: the OpenStreetMap how-to's step cards covered the directions they named), and slides in and out.
    [(x, y, begin, end)] = re.findall(r"overlay=x='(\d+)-[^']*':y=(\d+):eof_action=repeat:enable='between\(t,([\d.]+),"
                                      r"([\d.]+)\)'", plan.graph)
    assert abs(int(y) + card.height / 2 - 360 * 0.68) <= 2 and int(x) < 16
    assert (float(begin), float(end)) == (2.3, 5.0), "in a moment after its scene starts, gone when it ends"


def test_a_scenes_title_is_as_large_on_a_tall_video_as_on_a_wide_one():
    """5.3.2: on a phone video (1080x1920) the title came out a third smaller than on 1920x1080."""
    from row_bot.video_editing import lower_third

    wide = lower_third("Ask, check, done", (31, 41, 55), 1920, 1080)
    tall = lower_third("Ask, check, done", (31, 41, 55), 1080, 1920)
    assert abs(wide.height - tall.height) <= 2 and tall.x + tall.width <= 1080


def test_a_caption_shows_the_line_a_phrase_at_a_time_while_it_is_said():
    """5.3.2: a whole line in a box, cut off after three lines, read as a paragraph under the picture."""
    from row_bot.video_editing import SceneCut, Step, phrases

    line = ("Row-Bot opens the shop's orders, finds the three that haven't shipped, and drafts a note to each "
            "customer. You check them, and send.")
    # A caption ends at the pause, not on its comma (U27); a full stop stays.
    assert phrases(line) == ["Row-Bot opens the shop's orders", "finds the three that haven't shipped",
                             "and drafts a note to each customer.", "You check them, and send."]
    assert phrases("Search for your mountain; then read its height: 8,848.86 m.") == [
        "Search for your mountain", "then read its height", "8,848.86 m."]
    scenes = Step(op="scenes", style="caption", scenes=[
        SceneCut(input=0, end=6, line=line, voice="line-1.wav", voice_seconds=5.0)])
    plan = build_plan([InputVideo("input-1.mp4", HD)], [scenes], "output.mp4", sounds={"line-1.wav": "sound-1.wav"})
    shown = [(float(a), float(b)) for a, b in re.findall(r"between\(t,([\d.]+),([\d.]+)\)", plan.graph)]
    assert len(shown) == 4 and shown[0][0] == 0 and shown[-1][1] == 5.0, "the phrases over the line's own time"
    assert all(a[1] == pytest.approx(b[0], abs=1e-3) for a, b in zip(shown, shown[1:])), "one after another"


def test_a_video_with_footage_that_is_mostly_title_cards_needs_attention():
    from row_bot.video_editing import SceneCut, Step

    scenes = Step(op="scenes", scenes=[SceneCut(input=None, end=9, card="Welcome to the shop"),
                                       SceneCut(input=0, end=4), SceneCut(input=None, end=3, card="Thanks")])
    review = build_plan([InputVideo("input-1.mp4", HD)], [scenes], "output.mp4").review
    assert [line.split(":")[0] for line in review] == ["Cards", "Card at 0"], "75% cards, and one held 9 s"
    cards_only = Step(op="scenes", scenes=[SceneCut(input=None, end=3, card="Row-Bot 5.3")])
    assert build_plan([], [cards_only], "output.mp4").review == [], "a video of cards, asked for, is fine"


def test_a_review_sheets_stills_come_from_one_short_command_however_many_there_are():
    """5.3.2: twenty stills, one input each, passed the process runner's 256 arguments and the sheet never came."""
    from row_bot.video_editing import stills_arguments

    args = stills_arguments("output.mp4", [1.0 + index for index in range(24)], "review-%02d.jpg", max_width=960)
    assert len(args) < 30 and args.count("-i") == 1
    assert "gte(t,1)*lt(prev_t,1)+gte(t,2)*lt(prev_t,2)" in args[args.index("-vf") + 1]
    assert args[-1] == "file:review-%02d.jpg"


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
    pictures = [_picture(png) for png in alone.overlays]
    backdrops = [picture for picture in pictures if picture.size == (1080, 1080)]
    titles = [picture for picture in pictures if picture.size != (1080, 1080)]
    assert len(backdrops) == 2 and len(titles) == 2 and alone.args.count("-i") == 1 and "-an" in alone.args, \
        "each card on its backdrop with its title: the picture reads only the pictures it lays"
    assert [track.path for track in alone.mix.tracks] == ["narration.wav"], "and the voice is all its sound"
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
    from row_bot.video_sound import Track

    muted = _plan([{"op": "sound", "role": "original", "volume": 0}])
    assert muted.mix.tracks == (Track("picture.mkv", volume=0.0, bus="original"),) and _every_stream_is_used(muted)

    voiced = build_plan([InputVideo(path="input-1.mp4", info=HD)],
                        parse_steps([{"op": "sound", "role": "original", "volume": 0},
                                     {"op": "sound", "source": "line.wav"}]), "output.mp4",
                        sounds={"line.wav": "sound-1.wav"})
    assert voiced.has_audio and Track("sound-1.wav") in voiced.mix.tracks and _every_stream_is_used(voiced)


def test_a_silent_video_gets_its_narration_and_sounds_are_checked():
    plan = build_plan([InputVideo(path="input-1.mp4", info=SILENT)],
                      parse_steps([{"op": "sound", "source": "line.wav"}]), "output.mp4",
                      sounds={"line.wav": "sound-1.wav"})
    assert plan.has_audio and "-an" in plan.args, "its picture has no sound of its own: the mix is all of it"
    assert [track.bus for track in plan.mix.tracks] == ["voice"] and not plan.mix.duck

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


def test_a_long_narrated_captioned_project_is_one_short_command_with_each_picture_cut_from_one_sheet():
    """5.3.2 demo run 10: a two-minute video's captions, lower-thirds and voice lines were an input each, passed
    the process runner's 256 arguments, and every render failed with "no details"."""
    from row_bot.video_editing import OVERLAYS, SceneCut, Step, overlay_sheet

    scenes = Step(op="scenes", style="caption", scenes=[
        SceneCut(input=0, start=index * 0.5, end=index * 0.5 + 0.5, card=f"Step {index}",
                 line=f"Scene {index} opens the page, finds the row, and saves it.", voice=f"line-{index}.wav",
                 voice_seconds=0.4)
        for index in range(60)])
    plan = build_plan([InputVideo("input-1.mp4", VideoInfo(duration=30.0, width=640, height=360, fps=30.0,
                                                           has_audio=True))], [scenes], "output.mp4",
                      sounds={f"line-{index}.wav": f"C:/voices/line-{index}.wav" for index in range(60)})

    assert len(plan.overlays) > 120 and len(plan.voices) == 60
    assert len(plan.args) < 60 and plan.args.count("-i") == 2, "the video and the picture sheet"
    assert [track.path for track in plan.mix.tracks] == ["narration.wav", "picture.mkv"], "its lines as one track"
    assert plan.voices[1] == (pytest.approx(plan.marks[1]), "C:/voices/line-1.wav")
    assert f"file:{OVERLAYS}" in plan.args and _every_stream_is_used(plan)
    sheet = _picture(plan.overlay_sheet)
    _png, boxes = overlay_sheet(plan.overlays)
    for png, (x, y, width, height) in zip(plan.overlays, boxes):
        assert f"crop={width}:{height}:{x}:{y}" in plan.graph
        assert sheet.crop((x, y, x + width, y + height)).tobytes() == _picture(png).convert("RGBA").tobytes()


def test_a_long_caption_shows_phrase_by_phrase_over_its_own_time_never_cut_short():
    """D16: a long SRT cue was cut short with "…" (a walking-directions demo dropped its captions for it). It now
    shows phrase by phrase, each for its share of the cue's time, as a project's lines do; a short cue is one."""
    long = ("Row-Bot opens the map, finds the walking route from the station to the museum, and reads out each "
            "turn while you follow along on the screen.")
    srt = f"1\n00:00:00,500 --> 00:00:05,500\n{long}\n\n2\n00:00:05,500 --> 00:00:06,000\nDone."
    plan = _plan([{"op": "captions", "text": srt}])
    shown = [(float(a), float(b)) for a, b in re.findall(r"between\(t,([\d.]+),([\d.]+)\)", plan.graph)]
    assert len(shown) >= 4, "the long cue in phrases, then the short one"
    assert shown[0][0] == 0.5 and shown[-2][1] == pytest.approx(5.5, abs=1e-3), "within the cue's own time"
    assert all(a[1] == pytest.approx(b[0], abs=1e-3) for a, b in zip(shown, shown[1:])), "one after another"
    assert shown[-1] == (5.5, 6.0)
