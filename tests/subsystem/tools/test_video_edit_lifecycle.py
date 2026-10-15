"""Video edits with real processes (F3): a fake ffmpeg program through the real process manager, and
one real tiny encode when ffmpeg is on this computer (skipped otherwise).

Slow: these start real processes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.platform, pytest.mark.slow]

FAKE_FFMPEG = r'''
import json, sys, time
from pathlib import Path

settings = json.loads((Path(__file__).with_suffix(".json")).read_text())
args = sys.argv[1:]
if "-version" in args:
    print("ffmpeg version 7.1 Copyright\nconfiguration: --enable-gpl --enable-version3")
    sys.exit(0)
outputs = [arg[5:] for index, arg in enumerate(args) if arg.startswith("file:") and args[index - 1] != "-i"]
if not outputs:
    length = "00:00:03.00" if args[-1].endswith("output.mp4") else "00:00:06.00"
    sys.stderr.write(f"  Duration: {length}, start: 0.000000\n"
                     "  Stream #0:0(und): Video: h264 (High), yuv420p, 640x360, 30 fps, 30 tbr\n"
                     "  Stream #0:1(und): Audio: aac (LC), 48000 Hz, stereo\n"
                     "At least one output file must be specified\n")
    sys.exit(1)
steps = int(settings["seconds"] / 0.1)
for step in range(steps):
    print(f"out_time_us={int(step * 100000)}\nprogress=continue", flush=True)
    time.sleep(0.1)
for name in outputs:
    Path(name).write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200)
print("progress=end", flush=True)
'''


@pytest.fixture
def studio(tmp_path, monkeypatch, reload_for_data_dir):
    (threads,) = reload_for_data_dir(tmp_path / "data", "row_bot.threads")
    from row_bot import background_processes, ffmpeg_runtime, video_edits
    from row_bot.application.attachments import register_attachment

    chat = threads.create_thread("Demo", seed_default_skills=False)
    monkeypatch.setattr("row_bot.tool_outputs.current_thread_id", lambda: chat)
    program = tmp_path / "bin" / "fake_ffmpeg.py"
    program.parent.mkdir()
    program.write_text(FAKE_FFMPEG, encoding="utf-8", newline="\n")
    settings = program.with_suffix(".json")
    settings.write_text(json.dumps({"seconds": 0.3}), encoding="utf-8")
    fake = ffmpeg_runtime.Ffmpeg(executable=str(program), source="row_bot", version="7.1",
                                 licence="GPL version 3 or later", prefix=(sys.executable, "-I"))
    yield {"chat": chat, "fake": fake, "settings": settings, "register": register_attachment, "monkeypatch": monkeypatch,
           "edits": video_edits}
    video_edits.stop_all(timeout=5)
    background_processes.stop_all(timeout=5)


def test_an_edit_runs_through_the_process_manager_and_stop_ends_it(studio):
    from row_bot import background_processes, ffmpeg_runtime
    from row_bot.tools import video_editing_tool as video_tool

    studio["monkeypatch"].setattr(ffmpeg_runtime, "find", lambda: studio["fake"])
    studio["register"](studio["chat"], "demo.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)

    done = video_tool._edit_video(inputs=["demo.mp4"], steps=[{"op": "trim", "start": 1, "end": 4}])
    assert ": saved 0:03 of video" in done, done

    studio["settings"].write_text(json.dumps({"seconds": 30}), encoding="utf-8")
    studio["monkeypatch"].setattr(video_tool, "INLINE_SECONDS", 0.5)
    running = video_tool._edit_video(inputs=["demo.mp4"], steps=[{"op": "speed", "factor": 2}])
    edit = studio["edits"].get(running.split(":", 1)[0].removeprefix("Video edit "))
    assert "carries on in the background" in running
    for _ in range(100):
        if (studio["edits"].view(edit)["progress"] or 0) > 0:
            break
        threading.Event().wait(0.1)
    assert studio["edits"].view(edit)["progress"] > 0
    process = background_processes.get(edit.process_id)
    assert process.running and process.name == "Video edit"

    studio["edits"].stop(edit.edit_id, by="you")
    assert studio["edits"].wait(edit, 20)
    assert edit.status == "stopped" and not process.running


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_a_real_tiny_encode(studio, tmp_path):
    from row_bot import ffmpeg_runtime
    from row_bot.tools import video_editing_tool as video_tool
    from row_bot.video_editing import parse_probe, probe_command

    real = ffmpeg_runtime.find()
    clip = tmp_path / "clip.mp4"
    subprocess.run([real.executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=15:duration=4", "-f", "lavfi", "-i", "sine=duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True)
    studio["register"](studio["chat"], "clip.mp4", clip.read_bytes())

    result = video_tool._edit_video(inputs=["clip.mp4"], name="tiny", steps=[
        {"op": "trim", "start": 0.5, "end": 3.5},
        {"op": "speed", "factor": 2, "start": 1, "end": 3},
        {"op": "text", "text": "Demo"},
        {"op": "captions", "text": "1\n00:00:00,200 --> 00:00:01,000\nHello\n"},
        {"op": "zoom", "region": [0.25, 0.25, 0.5, 0.5], "start": 0, "end": 1},
    ])

    assert ": saved 0:02 of video" in result, result
    from row_bot.application.attachments import read_attachment

    edit = studio["edits"].get(result.split(":", 1)[0].removeprefix("Video edit "))
    output = tmp_path / "out.mp4"
    output.write_bytes(read_attachment(edit.media_ref)[1])
    probe = subprocess.run([real.executable, *probe_command(output)], capture_output=True, text=True)
    info = parse_probe(probe.stderr)
    assert (info.width, info.height, info.has_audio) == (320, 180, True)
    assert abs(info.duration - 2.0) < 0.2
    assert Path(output).stat().st_size > 1000
