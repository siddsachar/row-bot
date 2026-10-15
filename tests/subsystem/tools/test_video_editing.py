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
