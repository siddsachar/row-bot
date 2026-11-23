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
    assert "\nSaved 0:03 of video" in done, done

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

    assert result.splitlines()[0].endswith(': Output "tiny" v1 (working) · tiny.mp4'), result
    assert "\nSaved 0:02 of video" in result, result
    from row_bot.application.attachments import read_attachment

    edit = studio["edits"].get(result.split(":", 1)[0].removeprefix("Video edit "))
    output = tmp_path / "out.mp4"
    output.write_bytes(read_attachment(edit.media_ref)[1])
    probe = subprocess.run([real.executable, *probe_command(output)], capture_output=True, text=True)
    info = parse_probe(probe.stderr)
    assert (info.width, info.height, info.has_audio) == (320, 180, True)
    assert abs(info.duration - 2.0) < 0.2
    assert Path(output).stat().st_size > 1000


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_a_real_video_given_music_is_its_picture_then_its_mix_at_minus_14(studio, tmp_path):
    """C6: the picture's run, the mix, then the sound laid beside the picture copied as it was made."""
    from row_bot import ffmpeg_runtime, music
    from row_bot.application.attachments import read_attachment
    from row_bot.tools import video_editing_tool as video_tool

    real = ffmpeg_runtime.find()
    clip = tmp_path / "clip.mp4"
    subprocess.run([real.executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=15:duration=4", "-f", "lavfi", "-i", "sine=duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True)
    studio["register"](studio["chat"], "clip.mp4", clip.read_bytes())
    studio["register"](studio["chat"], "music.wav", music.wav_bytes(music.compose(4.0, mood="upbeat")))

    result = video_tool._edit_video(inputs=["clip.mp4"], name="scored", steps=[
        {"op": "trim", "start": 0.5, "end": 3.5}, {"op": "sound", "source": "music.wav", "role": "music"}])

    import re

    assert re.search(r"\nSaved 0:03(\.\d)? of video", result), result  # As measured: 0:03.1 is three seconds too.
    edit = studio["edits"].get(result.split(":", 1)[0].removeprefix("Video edit "))
    output = tmp_path / "out.mp4"
    output.write_bytes(read_attachment(edit.media_ref)[1])
    info, measures = studio["edits"].measure_file(real, output, tmp_path)
    assert info.has_audio and abs(info.duration - 3.0) < 0.2
    assert -15.5 < measures.loudness < -12.5 and measures.peak < -1.0, (measures.loudness, measures.peak)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_a_real_project_is_its_picture_then_its_mix_and_a_sound_only_remake_reuses_the_picture(studio, tmp_path):
    """C6 for real: a project's scenes drawn, its picture made (picture.mkv, kept with the project), its mix with
    music made to fit and an effect laid beside it with -c:v copy; then a change to the sound alone mixes it again
    and copies the same picture."""
    from row_bot import ffmpeg_runtime
    from row_bot.application.attachments import read_attachment
    from row_bot.media_sources import resolve
    from row_bot.video_editing import SCORE, SceneCut, Step

    real = ffmpeg_runtime.find()
    edits = studio["edits"]
    clip = tmp_path / "clip.mp4"
    subprocess.run([real.executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30:duration=4", "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True)
    studio["register"](studio["chat"], "clip.mp4", clip.read_bytes())
    sources = resolve("clip.mp4", studio["chat"])
    runs: list[tuple[str, str]] = []
    sound_runs: list[list[str]] = []
    run = edits._run_process

    def recorded(edit, ffmpeg, args, folder, name):  # Only records: checked once the edit has ended.
        runs.append((name, args[-1].removeprefix("file:")))
        if name == "Video sound":
            sound_runs.append(list(args))
        return run(edit, ffmpeg, args, folder, name)

    def picture_read(args: list[str]) -> Path:
        """The picture the sound run copies: what it reads with -i that is a Matroska file."""
        return next(Path(args[index + 1].removeprefix("file:")) for index, arg in enumerate(args)
                    if arg == "-i" and args[index + 1].endswith(".mkv"))

    studio["monkeypatch"].setattr(edits, "_run_process", recorded)
    cache = tmp_path / "scenes"
    scenes = Step(op="scenes", aspect="16:9", scenes=[
        SceneCut(input=0, start=0.5, end=2.5, cue="quiet"),
        SceneCut(input=None, end=1.5, card="The end", effects=("whoosh",), cue="lift")])

    def make(volume: float):
        edit = edits.start(edits.Request(
            conversation_id=studio["chat"], kind="video", sources=sources, output_name="Real.mp4", role="draft",
            steps=[scenes, Step(op="sound", source=SCORE, role="music", volume=volume)], score="calm",
            scene_cache=cache))
        assert edits.wait(edit, 300) and edit.status == "saved", edit.detail
        output = tmp_path / f"real-{volume}.mp4"
        output.write_bytes(read_attachment(edit.media_ref)[1])
        info, measures = edits.measure_file(real, output, tmp_path)
        assert info.has_audio and abs(info.duration - 3.5) < 0.25, info
        assert -15.5 < measures.loudness < -12.5 and measures.peak < -1.0, (measures.loudness, measures.peak)
        return edit

    make(0.3)
    assert runs == [("Video edit", "picture.mkv"), ("Video sound", "output.mp4")]
    kept = list(cache.glob("picture-*.mkv"))
    assert len(kept) == 1, "the picture is kept with the project"
    first = sound_runs[0]
    assert first[first.index("-c:v") + 1] == "copy", "the picture is copied as it was made"
    assert picture_read(first).resolve() == kept[0].resolve(), "made, then read from where the project keeps it"
    runs.clear()
    again = make(0.6)
    assert runs == [("Video sound", "output.mp4")], "a change to the sound alone copies the same picture"
    remake = sound_runs[1]
    assert remake[remake.index("-c:v") + 1] == "copy"
    assert picture_read(remake).resolve() == kept[0].resolve(), "the kept picture, not a new one"
    assert again.scene_makes == ["reused", ""], "and draws no scene again"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_a_real_project_matched_graded_letterboxed_and_crossfaded(studio, tmp_path):
    """C5 for real: a warm clip and a cool one matched to one look, graded cinematic with grain, a 2.39 letterbox,
    and a crossfade between them, in ffmpeg's one run."""
    import av

    from row_bot import ffmpeg_runtime
    from row_bot.application.attachments import read_attachment
    from row_bot.media_sources import resolve
    from row_bot.video_editing import CROSSFADE, Finish, SceneCut, Step

    real = ffmpeg_runtime.find()
    edits = studio["edits"]
    for name, colour in (("warm.mp4", "0xc89a6e"), ("cool.mp4", "0x6e96d2")):
        clip = tmp_path / name
        subprocess.run([real.executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c={colour}:size=640x360:rate=30:duration=3", "-f", "lavfi", "-i",
                        "sine=frequency=300:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                        "-shortest", str(clip)], check=True)
        studio["register"](studio["chat"], name, clip.read_bytes())
    sources = resolve("warm.mp4", studio["chat"]) + resolve("cool.mp4", studio["chat"])
    scenes = Step(op="scenes", aspect="16:9", finish=Finish(grade="cinematic", grain=True, letterbox=True,
                                                            transition="crossfade"),
                  scenes=[SceneCut(input=0, end=2.0, matched=True), SceneCut(input=1, end=2.0, matched=True)])
    edit = edits.start(edits.Request(conversation_id=studio["chat"], kind="video", sources=sources, steps=[scenes],
                                     output_name="Look.mp4", role="draft", scene_cache=tmp_path / "scenes"))
    assert edits.wait(edit, 300) and edit.status == "saved", edit.detail
    assert edit.marks == pytest.approx([0.0, 2.0 - CROSSFADE, 4.0 - CROSSFADE], abs=0.05)
    output = tmp_path / "look.mp4"
    output.write_bytes(read_attachment(edit.media_ref)[1])
    with av.open(str(output)) as reader:
        frames = {round(frame.time, 1): frame.to_image() for frame in reader.decode(video=0)}
    from PIL import ImageStat

    first, last = frames[0.5], frames[3.0]
    assert first.size == (1920, 1080) and max(first.getpixel((960, 20))) < 24, "black bars above the picture"
    middle = (860, 480, 1060, 600)  # Averaged, so the grain doesn't count.
    distance = sum(abs(a - b) for a, b in zip(ImageStat.Stat(first.crop(middle)).mean,
                                              ImageStat.Stat(last.crop(middle)).mean))
    assert distance < 0.6 * sum(abs(a - b) for a, b in zip((0xc8, 0x9a, 0x6e), (0x6e, 0x96, 0xd2))), \
        "the two clips' colours meet"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_a_real_short_is_captioned_word_by_word_with_the_word_being_said_lit(studio, tmp_path):
    """C7 for real: a tall short's big captions, one moment per word, the word being said in yellow."""
    import av

    from row_bot import ffmpeg_runtime
    from row_bot.application.attachments import read_attachment
    from row_bot.media_sources import resolve
    from row_bot.video_editing import Look, SceneCut, Step

    real = ffmpeg_runtime.find()
    edits = studio["edits"]
    clip = tmp_path / "tall.mp4"
    subprocess.run([real.executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x334455:size=360x640:rate=30:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(clip)], check=True)
    studio["register"](studio["chat"], "tall.mp4", clip.read_bytes())
    words = ((0.2, 0.9, "Pack"), (0.9, 1.6, "light."), (1.8, 2.6, "Go."))
    scenes = Step(op="scenes", aspect="9:16", style="caption", look=Look(captions="word"),
                  scenes=[SceneCut(input=0, end=3.0, line="Pack light. Go.", words=words)])
    edit = edits.start(edits.Request(conversation_id=studio["chat"], kind="video",
                                     sources=resolve("tall.mp4", studio["chat"]), steps=[scenes],
                                     output_name="Short.mp4", role="draft", scene_cache=tmp_path / "scenes"))
    assert edits.wait(edit, 300) and edit.status == "saved", edit.detail
    output = tmp_path / "short.mp4"
    output.write_bytes(read_attachment(edit.media_ref)[1])
    with av.open(str(output)) as reader:
        frames = {round(frame.time, 1): frame.to_image() for frame in reader.decode(video=0)}
    lit = [sum(1 for red, green, blue in frames[moment].getdata() if red > 200 and green > 160 and blue < 90)
           for moment in (0.5, 1.2, 2.2)]
    assert frames[0.5].size == (1080, 1920) and all(count > 200 for count in lit), "a word lit at each moment"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_a_real_caption_is_as_large_as_its_frame_is_tall(studio, tmp_path):
    """C7 for real: a boxed caption is sized by the frame's height, so a tall short's reads on a phone, and it stays
    clear of the bottom of a phone app's screen."""
    import av

    from row_bot import ffmpeg_runtime
    from row_bot.application.attachments import read_attachment
    from row_bot.media_sources import resolve
    from row_bot.video_editing import Look, SceneCut, Step

    real = ffmpeg_runtime.find()
    edits = studio["edits"]

    def caption_rows(shape: str, size: str) -> tuple[int, int]:
        """How tall the caption's dark box is down the made video's middle column (its top to its bottom, so
        white letters crossing the column don't count against it), and its last row."""
        name = f"plain-{shape.replace(':', 'x')}.mp4"
        clip = tmp_path / name
        subprocess.run([real.executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=0xdddddd:size={size}:rate=30:duration=2", "-c:v", "libx264", "-pix_fmt",
                        "yuv420p", str(clip)], check=True)
        studio["register"](studio["chat"], name, clip.read_bytes())
        step = Step(op="scenes", aspect=shape, style="caption", look=Look(captions="box"),
                    scenes=[SceneCut(input=0, end=2.0, line="Who's coming back?",
                                     words=((0.0, 0.6, "Who's"), (0.6, 1.2, "coming"), (1.2, 1.9, "back?")))])
        edit = edits.start(edits.Request(conversation_id=studio["chat"], kind="video", sources=resolve(name, studio[
            "chat"]), steps=[step], output_name=name, role="draft", scene_cache=tmp_path / f"scenes-{size}"))
        assert edits.wait(edit, 300) and edit.status == "saved", edit.detail
        made = tmp_path / f"made-{name}"
        made.write_bytes(read_attachment(edit.media_ref)[1])
        with av.open(str(made)) as reader:
            frame = next(frame.to_image() for frame in reader.decode(video=0) if frame.time >= 1.0)
        dark = [y for y in range(frame.height) if max(frame.getpixel((frame.width // 2, y))) < 90]
        return max(dark) - min(dark) + 1, max(dark)

    wide, (tall, bottom) = caption_rows("16:9", "640x360")[0], caption_rows("9:16", "360x640")
    assert tall > 1.4 * wide, (wide, tall)
    assert bottom < 1920 * 0.8, "above a phone app's own buttons and caption"


SPEECH = ((0.8, 1.0, "So,"), (1.1, 1.4, "um,"), (1.5, 1.9, "today"), (1.9, 2.1, "we"), (2.1, 2.4, "open"),
          (2.4, 2.5, "the"), (2.5, 2.9, "box."), (4.0, 4.3, "And"), (4.3, 4.6, "here"), (4.6, 4.8, "it"),
          (4.8, 5.2, "is."))


def _hears_speech(monkeypatch):
    """The local speech model, as a fake that hears SPEECH in any clip (CI has no Whisper model)."""
    from types import SimpleNamespace

    from row_bot import sound, voice

    monkeypatch.setattr(sound, "transcribe", lambda *_args: ([sound.Segment(0.8, 5.2, "", SPEECH)], "en"))
    monkeypatch.setattr(voice, "get_voice_service", lambda: SimpleNamespace(
        whisper_size="small", whisper_model_available=lambda: True))


def _decoded(path: Path):
    """A made video's pictures by their time (tenths), and its sound's left channel with its rate."""
    import av
    import numpy

    with av.open(str(path)) as reader:
        frames = {round(frame.time, 1): frame.to_image() for frame in reader.decode(video=0)}
    with av.open(str(path)) as reader:
        resample = av.AudioResampler(format="flt", layout="mono", rate=48000)
        sound = numpy.concatenate([piece.to_ndarray()[0] for frame in reader.decode(audio=0)
                                   for piece in resample.resample(frame)])
    return frames, sound


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_a_real_talking_clip_tidied_and_given_b_roll_at_its_words(studio, tmp_path):
    """C4 for real: the pauses and the um cut in one pass, the picture and sound still together, and a picture
    laid over the picture where "open the box" is said."""
    from PIL import Image

    from row_bot import ffmpeg_runtime
    from row_bot.application.attachments import read_attachment
    from row_bot.tools import video_editing_tool as video_tool

    _hears_speech(studio["monkeypatch"])
    real = ffmpeg_runtime.find()
    clip = tmp_path / "talk.mp4"
    subprocess.run([real.executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30:duration=6", "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True)
    studio["register"](studio["chat"], "talk.mp4", clip.read_bytes())
    picture = tmp_path / "box.png"
    Image.new("RGB", (320, 180), (230, 0, 230)).save(picture)
    studio["register"](studio["chat"], "box.png", picture.read_bytes())

    result = video_tool._edit_video(inputs=["talk.mp4"], name="tidy", steps=[
        {"op": "tidy"}, {"op": "cover", "source": "box.png", "text": "open the box"}])

    assert "Cut the pauses and fillers and added b-roll." in result, result
    edit = studio["edits"].get(result.split(":", 1)[0].removeprefix("Video edit "))
    output = tmp_path / "tidy.mp4"
    output.write_bytes(read_attachment(edit.media_ref)[1])
    frames, sound = _decoded(output)
    assert abs(max(frames) - 3.5) < 0.15, "0.4 + 1.65 + 1.45 s kept of 6"
    assert abs(sound.size / 48000 - 3.5) < 0.15, "and the sound with them"
    shown = frames[1.8].getpixel((320, 180))
    assert shown[0] > 180 and shown[1] < 60 and shown[2] > 180, "the picture over the picture while it's said"
    assert frames[0.5].getpixel((320, 180)) != shown


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_a_real_noisy_voice_is_cleaned(studio, tmp_path):
    """C4 for real: a tone in steady hiss, then hiss alone; cleaned, the hiss between is far lower against it."""
    import numpy

    from row_bot import ffmpeg_runtime
    from row_bot.application.attachments import read_attachment
    from row_bot.tools import video_editing_tool as video_tool

    real = ffmpeg_runtime.find()
    clip = tmp_path / "noisy.mp4"
    subprocess.run([real.executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x445566:size=320x180:rate=30:duration=4", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=4", "-f", "lavfi", "-i",
                    "anoisesrc=color=white:amplitude=0.02:duration=4", "-filter_complex",
                    "[1:a]volume='lt(t,2)':eval=frame[tone];[tone][2:a]amix=inputs=2:normalize=0[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "192k", "-shortest", str(clip)], check=True)
    studio["register"](studio["chat"], "noisy.mp4", clip.read_bytes())

    result = video_tool._edit_video(inputs=["noisy.mp4"], name="clean", steps=[
        {"op": "sound", "role": "original", "clean": True}])

    assert "Cleaned the voice." in result, result
    edit = studio["edits"].get(result.split(":", 1)[0].removeprefix("Video edit "))
    output = tmp_path / "clean.mp4"
    output.write_bytes(read_attachment(edit.media_ref)[1])

    def gap(path: Path) -> float:
        """How far the tone stands above the hiss alone, in dB."""
        _frames, sound = _decoded(path)
        rms = [float(numpy.sqrt(numpy.mean(sound[round(a * 48000):round(b * 48000)] ** 2)))
               for a, b in ((0.5, 1.8), (2.4, 3.8))]
        return 20 * numpy.log10(rms[0] / max(rms[1], 1e-9))

    assert gap(output) > gap(clip) + 6, (gap(clip), gap(output))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_a_real_clips_moments_start_where_its_shot_changes(studio, tmp_path):
    """C4 for real: ffmpeg's scene score finds the cut between two shots, and a frame of each moment is saved."""
    from row_bot import ffmpeg_runtime
    from row_bot.tools import video_editing_tool as video_tool

    _hears_speech(studio["monkeypatch"])
    real = ffmpeg_runtime.find()
    clip = tmp_path / "two shots.mp4"
    subprocess.run([real.executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0xc83c28:size=320x180:rate=30:duration=3", "-f", "lavfi", "-i",
                    "color=c=0x285ac8:size=320x180:rate=30:duration=3", "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(clip)], check=True)
    studio["register"](studio["chat"], "two shots.mp4", clip.read_bytes())

    result = video_tool._edit_video(action="moments", inputs=["two shots.mp4"])
    if "carries on in the background" in result:
        result = video_tool._edit_video(action="status", edit_id=result.split(":", 1)[0].removeprefix("Video edit "))

    assert "found 2 moments in two shots.mp4" in result and "(no speech was heard)" in result, result
    assert "\n1. 0:00–0:03\n2. 0:03–0:06\n" in result, "a silent clip: its shots"
    edit = studio["edits"].get(result.split(":", 1)[0].removeprefix("Video edit "))
    assert len(edit.frame_refs) == 2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="No ffmpeg on this computer")
def test_a_real_project_scene_tidied_with_b_roll_over_it(studio, tmp_path):
    """C4 for real: a project's scene drawn from only the parts it keeps, with a picture over part of it, read as
    an input after the drawn scenes."""
    from PIL import Image

    from row_bot import ffmpeg_runtime
    from row_bot.application.attachments import read_attachment
    from row_bot.media_sources import resolve
    from row_bot.video_editing import SceneCut, Step

    real = ffmpeg_runtime.find()
    edits = studio["edits"]
    clip = tmp_path / "talk.mp4"
    subprocess.run([real.executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x33aa55:size=640x360:rate=30:duration=4", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(clip)], check=True)
    studio["register"](studio["chat"], "talk.mp4", clip.read_bytes())
    picture = tmp_path / "box.png"
    Image.new("RGB", (320, 180), (230, 0, 230)).save(picture)
    studio["register"](studio["chat"], "box.png", picture.read_bytes())
    scenes = Step(op="scenes", aspect="16:9", scenes=[SceneCut(input=0, start=0.0, end=4.0, keeps=(
        (0.0, 1.0), (2.0, 3.0)), covers=(("box.png", 0.5, 1.5),))])

    edit = edits.start(edits.Request(
        conversation_id=studio["chat"], kind="video", sources=resolve("talk.mp4", studio["chat"]), steps=[scenes],
        output_name="Tidy.mp4", role="draft", scene_cache=tmp_path / "scenes",
        covers={"box.png": resolve("box.png", studio["chat"])[0]}))
    assert edits.wait(edit, 300) and edit.status == "saved", edit.detail

    output = tmp_path / "project.mp4"
    output.write_bytes(read_attachment(edit.media_ref)[1])
    frames, sound = _decoded(output)
    assert abs(max(frames) - 2.0) < 0.15 and abs(sound.size / 48000 - 2.0) < 0.15, "two seconds kept of four"
    assert frames[1.0].getpixel((960, 540))[1] < 60, "the picture over it"
    assert frames[1.8].getpixel((960, 540))[1] > 120, "the scene again after it"