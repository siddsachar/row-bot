"""A made video's sound (C6): effects and ambience made here, the beat of music, cuts on it, and the mix.

numpy for the sound; PyAV reads the files the mix lays (skipped without the media extra). No ffmpeg program.
"""
from __future__ import annotations

import numpy as np
import pytest

from row_bot import video_sound
from row_bot.video_sound import RATE, Mix, Track

pytestmark = pytest.mark.subsystem


def _loudness(samples: np.ndarray) -> float:
    powers: list[float] = []
    for at in range(0, samples.shape[0], RATE):
        powers += video_sound._powers(samples[at:at + RATE])
    return video_sound.integrated(powers)


def _read(path) -> np.ndarray:
    import wave

    with wave.open(str(path), "rb") as audio:
        assert (audio.getnchannels(), audio.getframerate()) == (2, RATE)
        return np.frombuffer(audio.readframes(audio.getnframes()), "<i2").reshape(-1, 2) / 32767


def test_every_built_in_effect_is_made_here_the_same_each_time_and_never_clips():
    for name in video_sound.EFFECTS:
        sound = video_sound.effect(name, 2.0)
        assert np.array_equal(sound, video_sound.effect(name, 2.0)), f"{name}: the same every time it's made"
        assert sound.shape[1] == 2 and np.abs(sound).max() <= 0.95 and np.abs(sound).max() > 0.2, name
        if name in video_sound.AMBIENCE:
            assert sound.shape[0] == 2 * RATE, f"{name} runs under its whole scene"
            edge = int(0.05 * RATE)
            assert np.abs(sound[:edge]).max() < np.abs(sound[RATE // 2:RATE]).max() / 4, f"{name} fades in"
        else:
            assert sound.shape[0] < 1.5 * RATE, f"{name} is a one-shot"
    assert video_sound.effect_words("Rain, then a whoosh as we cut") == ["whoosh", "rain"]
    with pytest.raises(video_sound.SoundError, match="no built-in effect called thunder"):
        video_sound.effect("thunder", 2.0)


def test_the_beat_of_music_is_found_by_its_tempo_and_phase():
    seconds, bpm, first = 12.0, 100.0, 0.25
    rng = np.random.default_rng(1)
    music = rng.standard_normal(int(22050 * seconds)) * 0.01
    for k in range(int((seconds - first) * bpm / 60)):  # A drum on every beat.
        at = int((first + k * 60 / bpm) * 22050)
        music[at:at + 600] += rng.standard_normal(600) * np.exp(-np.arange(600) / 120)
    beats = video_sound.beats(music)

    period = float(np.median(np.diff(beats)))
    assert period == pytest.approx(0.6, rel=0.01), "100 beats a minute"
    assert min(abs(beat - first) for beat in beats[:3]) < 0.03, "on the drums, not between them"
    assert video_sound.grid(120, 2.0) == [0.0, 0.5, 1.0, 1.5, 2.0]
    assert video_sound.beats(np.zeros(1000)) == [], "too short to have a beat"


def test_a_montages_cuts_land_on_the_beat_never_past_the_footage_or_over_a_line():
    from row_bot.video_editing import LINE_PAD, SceneCut, on_the_beat

    beats = [0.5 * k for k in range(60)]
    scenes = [SceneCut(input=0, start=1.0, end=2.3),  # 1.3 s of a 10 s clip.
              SceneCut(input=None, end=1.7, card="Day two"),  # A card: any length.
              SceneCut(input=1, start=0.0, end=1.0, speed=2.0),  # 0.5 s at 2×, from a clip 1.1 s long.
              SceneCut(input=2, end=2.2, voice="line.wav", voice_seconds=2.0),  # A picture under a line.
              SceneCut(input=0, start=5.0, end=6.1)]
    cut = on_the_beat(scenes, [10.0, 1.1, 0.0], [False, False, True], beats)

    lengths = [(scene.end - scene.start) / (scene.speed if scene.input in (0, 1) else 1.0) for scene in cut]
    ends = np.cumsum(lengths)
    assert all(min(abs(end - beat) for beat in beats) < 1e-6 for end in ends), "every cut on a beat"
    assert cut[0].start == 1.0 and cut[0].end == pytest.approx(2.5), "1.3 s ends on the nearest beat, 1.5"
    assert cut[2].end <= 1.1, "footage never runs past its video's end"
    assert lengths[3] >= 2.0 + LINE_PAD, "a scene with a line lasts the line and a breath"
    after_intro = on_the_beat(scenes[:1], [10.0], [False], beats, offset=2.0)
    assert after_intro[0].end - after_intro[0].start == pytest.approx(1.5), "cut on the beat after the intro"
    assert on_the_beat(scenes, [10.0, 1.1, 0.0], [False, False, True], []) == scenes, "no music: as it was"


def test_loudness_is_measured_as_bs1770_says_and_silence_is_gated_out():
    tone = 0.1 * np.sin(2 * np.pi * 1000 * np.arange(5 * RATE) / RATE)
    stereo = np.stack([tone, tone], axis=1)
    assert _loudness(stereo) == pytest.approx(-20.0, abs=0.2), "a 1 kHz tone at -20 dBFS on both sides"
    gapped = np.concatenate([stereo, np.zeros((5 * RATE, 2))])
    assert _loudness(gapped) == pytest.approx(_loudness(stereo), abs=0.2), "silence doesn't pull it down"
    assert video_sound.integrated([]) == float("-inf")


def _wav(path, samples: np.ndarray):
    video_sound.write_wav(path, samples)
    return path.name


def test_the_mix_brings_any_video_to_its_loudness_and_dips_the_music_under_every_voice(tmp_path):
    pytest.importorskip("av")
    seconds = 8
    t = np.arange(seconds * RATE) / RATE
    rng = np.random.default_rng(3)
    # A voice line from 2 to 4 s; speech in the footage's own sound from 5.5 to 7 s; music all through.
    voice = np.zeros((seconds * RATE, 2))
    voice[2 * RATE:4 * RATE] = 0.3 * np.sin(2 * np.pi * 220 * t[:2 * RATE])[:, None]
    footage = np.zeros((seconds * RATE, 2))
    footage[int(5.5 * RATE):7 * RATE] = 0.3 * np.sin(2 * np.pi * 600 * t[:int(1.5 * RATE)])[:, None]
    music = 0.2 * video_sound._band(rng.standard_normal((seconds * RATE, 2)), 100, 4000)
    names = [_wav(tmp_path / name, data) for name, data in (("voice.wav", voice), ("own.wav", footage),
                                                            ("music.wav", music))]
    whoosh = _wav(tmp_path / "whoosh.wav", video_sound.effect("whoosh", 0))
    mix = Mix(tracks=(Track(names[0]), Track(names[1], bus="original"), Track(names[2], volume=0.3, bus="music"),
                      Track(whoosh, at=1.0, volume=0.6, bus="effects")), seconds=seconds, duck=True, loudness=-14)

    before = video_sound.mix_down(mix, tmp_path, tmp_path / "mix.wav")
    mixed = _read(tmp_path / "mix.wav")

    assert mixed.shape[0] == seconds * RATE and abs(before + 14) > 1, "it wasn't at its target before"
    assert _loudness(mixed) == pytest.approx(-14.0, abs=0.5), "two passes: measured, then set to its target"
    assert np.abs(mixed).max() <= 10 ** (video_sound.CEILING / 20) + 1e-3, "its peaks held under the ceiling"
    gain = 10 ** ((-14 - before) / 20)

    def music_at(start: float, speech: np.ndarray) -> float:
        """How loud the music is in the mix there: the mix less the speech laid at that moment (RMS)."""
        window = slice(int(start * RATE), int((start + 0.4) * RATE))
        return float(np.sqrt(((mixed[window] - speech[window] * gain) ** 2).mean()))

    between = music_at(0.5, voice)
    for start, speech in ((3.0, voice), (6.2, footage)):  # Under a line, and under speech in the footage.
        assert music_at(start, speech) < between * 0.4, f"the music dips under the speech at {start} s"
    assert music_at(5.0, voice) > between * 0.7, "and comes back between lines"
    assert music_at(7.5, voice) < between * 0.5, "it fades out as the video ends"


MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200


@pytest.mark.slow  # Four makes, each drawing two full-size title cards.
def test_a_change_to_the_sound_alone_mixes_it_again_and_uses_the_picture_as_it_was_made(tmp_path, monkeypatch,
                                                                                        reload_for_data_dir):
    """C6 and C1: a project's picture is kept with it; changing its music, effects or loudness mixes only the sound
    again, and its music made to fit and its effects are made once. ffmpeg's runs are recorded, not run."""
    pytest.importorskip("av")
    (threads,) = reload_for_data_dir(tmp_path / "data", "row_bot.threads")
    from dataclasses import replace

    from row_bot import ffmpeg_runtime, music, video_edits
    from row_bot.video_editing import SCORE, SceneCut, Step, VideoInfo

    chat = threads.create_thread("Film", seed_default_skills=False)
    runs: list[tuple[str, str]] = []
    mixes: list[np.ndarray] = []

    def run(edit, ffmpeg, args, folder, name):
        made = args[-1].removeprefix("file:")
        runs.append((name, made))
        if name == "Video sound":
            mixes.append(_read(folder / "mix.wav"))
        (folder / made).write_bytes(MP4)
        return ""

    composed: list[float] = []
    compose = music.compose
    monkeypatch.setattr(music, "compose", lambda seconds, **options: composed.append(seconds) or compose(
        seconds, **options))
    monkeypatch.setattr(video_edits, "_run_process", run)
    monkeypatch.setattr(video_edits, "probe_video", lambda *_args: VideoInfo(2.0, 1920, 1080, 30.0, True))
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: ffmpeg_runtime.Ffmpeg(
        executable="ffmpeg", source="row_bot", version="7.1", licence="GPL"))
    cards = Step(op="scenes", aspect="16:9", scenes=[
        SceneCut(input=None, end=1, card="Night", effects=("rain",), cue="quiet"),
        SceneCut(input=None, end=1, card="The storm", effects=("whoosh",), cue="build")])
    cache = tmp_path / "data" / "scenes"

    def make(scenes: Step, volume: float = 0.3, loudness: float | None = None, on_beat: bool = False):
        edit = video_edits.start(video_edits.Request(
            conversation_id=chat, kind="video", sources=[], output_name="Night.mp4", role="draft",
            steps=[scenes, Step(op="sound", source=SCORE, role="music", volume=volume)], score="calm",
            loudness=loudness, scene_cache=cache, on_beat=on_beat))
        assert video_edits.wait(edit, 60) and edit.status == "saved", edit.detail
        return edit

    make(cards)
    assert runs == [("Video edit", "picture.mkv"), ("Video sound", "output.mp4")]
    assert _loudness(mixes[0]) == pytest.approx(-14.0, abs=0.6), "the mix at -14 LUFS by default"
    assert composed == [2.0], "music made to the video's length"

    runs.clear()
    make(cards, volume=0.5, loudness=-16)
    assert runs == [("Video sound", "output.mp4")], "only the sound is made again"
    assert _loudness(mixes[1]) == pytest.approx(-16.0, abs=0.6) and composed == [2.0], "the same music, kept"
    runs.clear()
    make(replace(cards, scenes=[replace(cards.scenes[0], effects=("wind",)), cards.scenes[1]]))
    assert runs == [("Video sound", "output.mp4")], "new effects are sound too"
    runs.clear()
    make(replace(cards, scenes=[replace(cards.scenes[0], card="Midnight"), cards.scenes[1]]))
    assert runs == [("Video edit", "picture.mkv"), ("Video sound", "output.mp4")], "a new picture is made again"
    kept = sorted(path.name.split("-")[0] for path in cache.iterdir())
    assert kept.count("picture") == 2 and kept.count("score") == 1 and kept.count("effect") == 3
    beat = 60 / music.STYLES["calm"].bpm
    assert make(cards, on_beat=True).marks == pytest.approx([0.0, 2 * beat, 3 * beat], abs=1e-3), \
        "on the beat: each card ends on the music's beat (2 at calm's 90 a minute, then 1 more)"


def test_an_hour_of_sound_is_mixed_a_block_at_a_time(tmp_path, monkeypatch):
    """Only blocks are held: a track is read as the mix reaches it, never whole."""
    pytest.importorskip("av")
    _wav(tmp_path / "line.wav", 0.2 * np.ones((RATE // 2, 2)))
    taken: list[int] = []
    take = video_sound._Reader.take

    def counted(self, count):
        taken.append(count)
        return take(self, count)

    monkeypatch.setattr(video_sound._Reader, "take", counted)
    mix = Mix(tracks=(Track("line.wav", at=2.5),), seconds=5.0)
    video_sound.mix_down(mix, tmp_path, tmp_path / "mix.wav")
    assert max(taken) <= RATE and _read(tmp_path / "mix.wav").shape[0] == 5 * RATE
    assert np.abs(_read(tmp_path / "mix.wav")[:2 * RATE]).max() == 0, "the line starts where it's laid"
