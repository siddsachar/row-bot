"""The sound of a made video (C6): its effects and ambience, the beat of its music, and its mix.

**Effects and ambience** are made here, from noise and tones shaped by filters and envelopes: no recorded
samples, so nothing of unknown licence. A one-shot (a whoosh, a hit, a click) plays as its scene starts; an
ambience (rain, wind, a crowd, waves, footsteps) runs under the whole scene, fading in and out. Each is the same
every time it's made for the same scene (a seed from its name and place).

**The beat** of a music file is found from its onsets (spectral flux), their tempo (autocorrelation) and their
phase: deterministic, numpy only. A montage's cuts snap to it (``video_editing.on_the_beat``).

**The mix** lays every sound of a video on its timeline: its voices (the project's lines, dialogue in each
character's voice, voice files laid by edit_video), the footage's own sound, music, ambience and effects. Music and
ambience dip while anyone speaks (a voice line, or speech in the footage's own sound) and come back between lines.
Loudness takes two passes: the first measures the whole mix (ITU-R BS.1770: K-weighted, gated), the second brings it
to its target (-14 LUFS for social video by default; the brand kit may set another) and holds its peaks under
``CEILING``. It reads each file a block at a time, so an hour's video costs a block of memory, not an hour's.
"""

from __future__ import annotations

import hashlib
import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

RATE = 48000
# One-shots play as their scene starts; an ambience runs under the whole scene.
ONE_SHOTS = ("whoosh", "hit", "click")
AMBIENCE = ("rain", "wind", "crowd", "waves", "footsteps")
EFFECTS = ONE_SHOTS + AMBIENCE
MAX_EFFECTS = 4
# Sound files an effect may be instead (one made elsewhere, or the person's own).
SOUND_SUFFIXES = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac")
# How loud each sits under the voice (the voice plays at 1).
AMBIENCE_VOLUME, EFFECT_VOLUME = 0.35, 0.6
# The finished mix: loudness by default (LUFS), and the sample peak it's held under (dBFS), a little under the true
# peak the finished check allows.
LOUDNESS = -14.0
CEILING = -1.5
# While anyone speaks, music and ambience dip this far (dB), easing down in DUCK_ATTACK and back in DUCK_RELEASE;
# speech is a 10 ms window louder than SPEAKING (RMS).
DUCK, DUCK_ATTACK, DUCK_RELEASE, SPEAKING = -12.0, 0.02, 0.45, 0.02
_WINDOW = RATE // 100
_BLOCK = RATE
_SEGMENT = RATE // 10


class SoundError(ValueError):
    """The video's sound can't be made as asked; the message says why."""


def effect_words(text: str) -> list[str]:
    """The built-in effects a scene's music cue names ("rain, then a whoosh"), in order."""
    words = {word.strip(".,;:!?()").casefold() for word in str(text or "").split()}
    return [name for name in EFFECTS if name in words or f"{name}s" in words][:MAX_EFFECTS]


# ── Effects and ambience ─────────────────────────────────────────────────────


def _seed(*parts: object) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.sha256(repr(parts).encode()).hexdigest()[:12], 16))


def _band(signal: np.ndarray, low: float = 0.0, high: float = 0.0, order: int = 2) -> np.ndarray:
    """A zero-phase Butterworth-shaped band (``low`` high-pass, ``high`` low-pass), in the frequency domain."""
    spectrum = np.fft.rfft(signal, axis=0)
    f = np.fft.rfftfreq(signal.shape[0], 1 / RATE)
    gain = np.ones_like(f)
    if high:
        gain /= np.sqrt(1 + (f / high) ** (2 * order))
    if low:
        gain /= np.sqrt(1 + (low / np.maximum(f, 1e-6)) ** (2 * order))
        gain[0] = 0.0
    shaped = spectrum * (gain[:, None] if signal.ndim == 2 else gain)
    return np.fft.irfft(shaped, signal.shape[0], axis=0)


def _pink(n: int, rng: np.random.Generator, slope: float = 0.5) -> np.ndarray:
    """Noise falling ``slope`` (0.5: pink, 1: brown) per octave, stereo, decorrelated sides."""
    white = rng.standard_normal((n, 2))
    spectrum = np.fft.rfft(white, axis=0)
    f = np.maximum(np.fft.rfftfreq(n, 1 / RATE), 20.0)
    return np.fft.irfft(spectrum / (f ** slope)[:, None], n, axis=0)


def _slow(n: int, rng: np.random.Generator, rate: float) -> np.ndarray:
    """A slow random movement 0–1 (about ``rate`` changes a second), smooth."""
    points = max(2, int(n / RATE * rate) + 2)
    values = rng.random(points)
    curve = np.interp(np.arange(n), np.linspace(0, n, points), values)
    return _band(curve, high=max(rate, 0.05), order=1).clip(0, None) / max(values.max(), 1e-6)


def _normal(signal: np.ndarray, peak: float = 0.8) -> np.ndarray:
    return signal / (np.abs(signal).max() + 1e-9) * peak


def _stereo(mono: np.ndarray, pan: np.ndarray | float = 0.0) -> np.ndarray:
    pan = np.asarray(pan, dtype=float)
    return np.stack([mono * np.cos((pan + 1) * np.pi / 4), mono * np.sin((pan + 1) * np.pi / 4)], axis=1) * 1.414


def _whoosh(rng: np.random.Generator) -> np.ndarray:
    n = int(0.9 * RATE)
    t = np.arange(n) / n
    noise = rng.standard_normal(n)
    dark, bright = _band(noise, 150, 900), _band(noise, 1200, 6000)
    rise = np.sin(np.pi * t) ** 2
    blend = np.sin(np.pi * np.clip(t * 1.3, 0, 1)) ** 2  # Brightest just after its middle, where it passes.
    return _normal(_stereo((dark * (1 - blend) + bright * blend) * rise, pan=-0.7 + 1.4 * t))


def _hit(rng: np.random.Generator) -> np.ndarray:
    n = int(1.4 * RATE)
    t = np.arange(n) / RATE
    phase = 2 * np.pi * np.cumsum(45 + 70 * np.exp(-t / 0.05)) / RATE
    body = np.sin(phase) * np.exp(-t / 0.35)
    crack = _band(rng.standard_normal(n), 200, 4000) * np.exp(-t / 0.05)
    return _normal(_stereo(body + 0.5 * crack * np.clip(t / 0.001, 0, 1)), 0.9)


def _click(rng: np.random.Generator) -> np.ndarray:
    n = int(0.08 * RATE)
    t = np.arange(n) / RATE
    tick = _band(rng.standard_normal(n), 1500) * np.exp(-t / 0.003) + 0.6 * np.sin(2 * np.pi * 2100 * t) * np.exp(
        -t / 0.01)
    return _normal(_stereo(tick), 0.7)


def _footsteps(n: int, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros(n)
    step = int(0.08 * RATE)
    t = np.arange(step) / RATE
    at, side = int(rng.random() * 0.2 * RATE), 1.0
    pan = np.zeros(n)
    while at < n:
        for offset, level in ((0, 1.0), (int(0.035 * RATE), 0.55)):  # Heel, then toe.
            begin = at + offset
            if begin >= n:
                break
            sound = _band(rng.standard_normal(step), 120, 1400) * np.exp(-t / 0.025) * level * (0.8 + 0.4 * rng.random())
            out[begin:begin + step] += sound[:n - begin]
        pan[at:at + int(0.52 * RATE)] = 0.15 * side
        side = -side
        at += int((0.52 + 0.06 * (rng.random() - 0.5)) * RATE)
    return _normal(_stereo(out, pan), 0.6)


def _rain(n: int, rng: np.random.Generator) -> np.ndarray:
    hiss = _band(_pink(n, rng), 400, 9000) * (0.85 + 0.15 * _slow(n, rng, 0.3))[:, None]
    drops = np.zeros((n, 2))
    count = int(n / RATE * 40)
    places = rng.integers(0, max(1, n - 400), count)
    blip = _band(rng.standard_normal(400), 2000, 7000) * np.exp(-np.arange(400) / RATE / 0.002)
    for place, side in zip(places, rng.integers(0, 2, count)):
        drops[place:place + 400, side] += blip * (0.3 + 0.7 * rng.random())
    return _normal(_normal(hiss) + 0.25 * _normal(drops), 0.5)


def _wind(n: int, rng: np.random.Generator) -> np.ndarray:
    gusts = 0.35 + 0.65 * _slow(n, rng, 0.25)
    low = _band(_pink(n, rng, 1.0), 60, 700) * gusts[:, None]
    whistle = _band(rng.standard_normal((n, 2)), 500, 1400, order=4) * (gusts ** 3)[:, None]
    return _normal(_normal(low) + 0.2 * _normal(whistle), 0.5)


def _crowd(n: int, rng: np.random.Generator) -> np.ndarray:
    out = np.zeros((n, 2))
    for _voice in range(14):  # Murmured syllables: speech-band noise in bursts, many voices apart.
        syllables = _slow(n, rng, 5.0 + 2.0 * rng.random()) ** 2
        speaker = _band(rng.standard_normal(n), 250 + 200 * rng.random(), 2200 + 800 * rng.random()) * syllables
        out += _stereo(speaker, pan=rng.random() * 1.6 - 0.8)
    return _normal(_band(out, 150, 3500), 0.5)


def _waves(n: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / RATE
    period = 7.0 + rng.random()
    cycle = (t / period + rng.random()) % 1.0
    swell = np.where(cycle < 0.7, (cycle / 0.7) ** 2, np.exp(-(cycle - 0.7) / 0.08))  # Builds, breaks, runs back.
    body = _band(_pink(n, rng), 80, 1500) * swell[:, None]
    foam = _band(rng.standard_normal((n, 2)), 2500, 9000) * (swell ** 4)[:, None]
    return _normal(_normal(body) + 0.3 * _normal(foam), 0.5)


_SHOTS: dict[str, Callable[[np.random.Generator], np.ndarray]] = {"whoosh": _whoosh, "hit": _hit, "click": _click}
_BEDS: dict[str, Callable[[int, np.random.Generator], np.ndarray]] = {
    "rain": _rain, "wind": _wind, "crowd": _crowd, "waves": _waves, "footsteps": _footsteps}
# A one-shot leads its scene a little: a whoosh passes as the cut happens.
LEAD = {"whoosh": 0.45}
FADE = 0.6


def effect(name: str, seconds: float, *, seed: object = 0) -> np.ndarray:
    """A built-in effect as stereo samples at ``RATE``: a one-shot at its own length, an ambience ``seconds`` long
    fading in and out."""
    rng = _seed(name, seed)
    if name in _SHOTS:
        return _SHOTS[name](rng)
    if name not in _BEDS:
        raise SoundError(f"There's no built-in effect called {name}. Effects: {', '.join(EFFECTS)}.")
    n = max(_WINDOW, int(round(seconds * RATE)))
    bed = _BEDS[name](n, rng)
    fade = min(int(FADE * RATE), n // 3)
    ramp = np.linspace(0, 1, fade)[:, None]
    bed[:fade] *= ramp
    bed[n - fade:] *= ramp[::-1]
    return bed


def write_wav(path: Path, samples: np.ndarray) -> None:
    """16-bit stereo WAV at ``RATE``."""
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


# ── The beat of a music file ─────────────────────────────────────────────────

_BEAT_RATE, _HOP, _FFT = 22050, 512, 1024


def beats(samples: np.ndarray, rate: int = _BEAT_RATE, *, low: float = 70.0, high: float = 180.0) -> list[float]:
    """The beats of music (mono samples at *rate*): seconds from its start, one tempo through it."""
    samples = np.asarray(samples, dtype=float).reshape(-1)
    if samples.size < _FFT * 8:
        return []
    frames = (samples.size - _FFT) // _HOP
    index = np.arange(_FFT)[None, :] + _HOP * np.arange(frames)[:, None]
    spectrum = np.log1p(100 * np.abs(np.fft.rfft(samples[index] * np.hanning(_FFT), axis=1)))
    onset = np.maximum(0.0, np.diff(spectrum, axis=0)).sum(axis=1)
    onset = np.maximum(0.0, onset - np.convolve(onset, np.ones(16) / 16, mode="same"))
    if not onset.any():
        return []
    step = _HOP / rate
    lags = np.arange(max(1, int(60 / high / step)), int(60 / low / step) + 2)
    centred = onset - onset.mean()
    correlation = np.array([np.dot(centred[:-lag], centred[lag:]) / (onset.size - lag) for lag in lags])
    # Tempos near 120 BPM are likelier (a log-normal prior an octave wide), so a half or double isn't picked.
    prior = np.exp(-0.5 * (np.log2(60 / (lags * step) / 120.0)) ** 2)
    best = int(np.argmax(correlation * prior))
    lag = float(lags[best])
    if 0 < best < len(lags) - 1:  # Between frames, where the peak really is.
        left, middle, right = correlation[best - 1:best + 2]
        bend = left - 2 * middle + right
        lag += 0.5 * (left - right) / bend if bend else 0.0
    # The tempo and phase together, finely: the comb of beats that lands on the most onset across the whole track.
    frames = np.arange(onset.size)
    best, chosen, phase = -1.0, lag, 0.0
    for trial in np.arange(max(1.0, lag - 1.0), lag + 1.0001, 0.02):
        for start in np.arange(0.0, trial, 0.25):
            places = start + trial * np.arange(int((onset.size - 1 - start) / trial) + 1)
            score = float(np.interp(places, frames, onset).mean())
            if score > best:
                best, chosen, phase = score, float(trial), float(start)
    lag = chosen
    count = int((onset.size - 1 - phase) / lag)
    # Each onset is the change into the next frame, which holds the beat at its middle: a frame's length later.
    times = [(phase + lag * k) * step + _FFT / rate for k in range(count + 1)]
    return [round(float(at), 4) for at in times if at < samples.size / rate]


def grid(bpm: float, seconds: float) -> list[float]:
    """The beats of music made here at *bpm*: one every beat from its start."""
    beat = 60.0 / bpm
    return [round(k * beat, 4) for k in range(int(seconds / beat) + 1)]


def file_beats(path: Path) -> list[float]:
    """The beats of a music file (its first 20 minutes), read with PyAV."""
    import av

    from row_bot.video_editing import SOUND_FORMATS

    pieces: list[np.ndarray] = []
    limit = _BEAT_RATE * 20 * 60
    resample = av.AudioResampler(format="flt", layout="mono", rate=_BEAT_RATE)
    try:
        with av.open(str(path), options={"protocol_whitelist": "file", "format_whitelist": SOUND_FORMATS}) as reader:
            for frame in reader.decode(audio=0):
                pieces += [piece.to_ndarray()[0] for piece in resample.resample(frame)]
                if sum(piece.size for piece in pieces) >= limit:
                    break
    except (av.error.FFmpegError, OSError) as error:
        raise SoundError(f"Row-Bot couldn't read the music to find its beat ({error}).") from None
    return beats(np.concatenate(pieces)[:limit]) if pieces else []


# ── The mix ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Track:
    """One sound laid on a video: its file (in the edit's folder, or a full path), where it starts on the video,
    how loud, and its bus: ``voice`` (it ducks the music), ``original`` (the footage's own sound: speech in it ducks
    the music too), ``music`` and ``bed`` (ambience; both duck), or ``effects`` (one-shots, never ducked). Music
    fades out over the video's last ``MUSIC_FADE`` seconds."""

    path: str
    at: float = 0.0
    volume: float = 1.0
    bus: str = "voice"


BUSES = ("voice", "original", "music", "bed", "effects")
MUSIC_FADE = 2.0


@dataclass(frozen=True)
class Mix:
    """A video's sound: its tracks, its length, whether music and ambience dip under speech, and its loudness."""

    tracks: tuple[Track, ...]
    seconds: float
    duck: bool = True
    loudness: float = LOUDNESS


class _Reader:
    """A sound file's samples, stereo at ``RATE``, taken a block at a time (silence after its end)."""

    def __init__(self, path: Path) -> None:
        import av

        from row_bot.video_editing import SOUND_FORMATS

        self._reader = av.open(str(path), options={"protocol_whitelist": "file", "format_whitelist": SOUND_FORMATS})
        self._frames = self._reader.decode(audio=0) if self._reader.streams.audio else iter(())
        self._resample = av.AudioResampler(format="fltp", layout="stereo", rate=RATE)
        self._kept = np.zeros((0, 2), dtype=np.float32)
        self._ended = False

    def take(self, count: int) -> np.ndarray:
        import av

        pieces = [self._kept]
        have = self._kept.shape[0]
        while have < count and not self._ended:
            try:
                frame = next(self._frames)
                resampled = self._resample.resample(frame)
            except StopIteration:
                self._ended = True
                resampled = self._resample.resample(None)
            except av.error.FFmpegError:
                self._ended = True
                resampled = []
            for piece in resampled:
                samples = piece.to_ndarray().T.astype(np.float32)
                pieces.append(samples)
                have += samples.shape[0]
        joined = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]
        self._kept = joined[count:]
        taken = joined[:count]
        if taken.shape[0] < count:
            taken = np.concatenate([taken, np.zeros((count - taken.shape[0], 2), dtype=np.float32)])
        return taken

    def close(self) -> None:
        self._reader.close()


def _k_weights(size: int) -> np.ndarray:
    """|H(f)|² of BS.1770's K-weighting (its head shelf, then its high-pass) at ``RATE``, for an rfft of *size*."""
    z = np.exp(-1j * 2 * np.pi * np.fft.rfftfreq(size, 1 / RATE) / RATE)
    shelf = (1.53512485958697 - 2.69169618940638 * z + 1.19839281085285 * z ** 2) / (
        1 - 1.69065929318241 * z + 0.73248077421585 * z ** 2)
    high = (1 - 2 * z + z ** 2) / (1 - 1.99004745483398 * z + 0.99007225036621 * z ** 2)
    return np.abs(shelf * high) ** 2


_WEIGHTS = _k_weights(_SEGMENT)


def _powers(block: np.ndarray) -> list[float]:
    """Each 100 ms of a block's K-weighted power, both sides summed (BS.1770's mean square, by Parseval)."""
    count = block.shape[0] // _SEGMENT
    if not count:
        return []
    segments = block[:count * _SEGMENT].reshape(count, _SEGMENT, 2)
    spectrum = np.abs(np.fft.rfft(segments, axis=1)) ** 2
    spectrum[:, 1:-1] *= 2  # Each bin but the first and last stands for its mirror too.
    return list((spectrum * _WEIGHTS[None, :, None]).sum(axis=(1, 2)) / _SEGMENT ** 2)


def integrated(powers: list[float]) -> float:
    """BS.1770 integrated loudness (LUFS) from its 100 ms powers: 400 ms blocks overlapping by 75%, gated at -70
    LUFS and then 10 LU under the mean of what's left. -inf for silence."""
    if len(powers) < 4:
        return float("-inf")
    values = np.asarray(powers, dtype=float)
    blocks = np.convolve(values, np.ones(4) / 4, mode="valid")
    with np.errstate(divide="ignore"):
        loud = -0.691 + 10 * np.log10(np.maximum(blocks, 1e-20))
    kept = blocks[loud > -70.0]
    if not kept.size:
        return float("-inf")
    relative = -0.691 + 10 * np.log10(kept.mean()) - 10.0
    gated = blocks[(loud > -70.0) & (loud > relative)]
    return float(-0.691 + 10 * np.log10(gated.mean())) if gated.size else float("-inf")


def _levels(block: np.ndarray) -> np.ndarray:
    """Each 10 ms window's RMS (both sides together)."""
    count = block.shape[0] // _WINDOW
    return np.sqrt((block[:count * _WINDOW] ** 2).reshape(count, -1).mean(axis=1))


def _per_sample(windows: np.ndarray, size: int) -> np.ndarray:
    centres = (np.arange(windows.size) + 0.5) * _WINDOW
    return np.interp(np.arange(size), centres, windows)[:, None]


class _Duck:
    """How far music and ambience are turned down, window by window, eased down and back up."""

    def __init__(self) -> None:
        self.level = 1.0
        self.down = 1 - math.exp(-0.01 / DUCK_ATTACK)
        self.up = 1 - math.exp(-0.01 / DUCK_RELEASE)
        self.floor = 10 ** (DUCK / 20)

    def gains(self, key: np.ndarray) -> np.ndarray:
        out = np.empty(key.size)
        for index, value in enumerate(key):
            target = self.floor if value > SPEAKING else 1.0
            self.level += (target - self.level) * (self.down if target < self.level else self.up)
            out[index] = self.level
        return out


class _Limiter:
    """Peaks held under ``CEILING``: the gain falls at once before a peak (one window ahead) and recovers slowly."""

    def __init__(self) -> None:
        self.level = 1.0
        self.ceiling = 10 ** (CEILING / 20)
        self.up = 1 - math.exp(-0.01 / 0.08)

    def __call__(self, block: np.ndarray) -> np.ndarray:
        count = -(-block.shape[0] // _WINDOW)
        padded = np.zeros((count * _WINDOW, 2), dtype=block.dtype)
        padded[:block.shape[0]] = block
        peaks = np.abs(padded).reshape(count, -1).max(axis=1)
        needed = np.minimum(1.0, self.ceiling / np.maximum(peaks, 1e-9))
        needed = np.minimum(needed, np.append(needed[1:], 1.0))  # Look one window ahead.
        out = np.empty(count)
        for index, value in enumerate(needed):
            self.level = value if value < self.level else self.level + (value - self.level) * self.up
            out[index] = self.level
        limited = block * _per_sample(out, block.shape[0])
        return np.clip(limited, -self.ceiling, self.ceiling)


def _speech(block: np.ndarray) -> np.ndarray:
    """The speech band of the footage's own sound (where a voice is, not a rumble or hiss): what ducks the music."""
    spectrum = np.fft.rfft(block, axis=0)
    f = np.fft.rfftfreq(block.shape[0], 1 / RATE)
    spectrum[(f < 250) | (f > 3800)] = 0
    return np.fft.irfft(spectrum, block.shape[0], axis=0)


def _blocks(mix: Mix, folder: Path, stopped: Callable[[], bool]) -> Iterator[np.ndarray]:
    """The mixed sound, a block at a time, before its loudness is set."""
    total = max(1, int(round(mix.seconds * RATE)))
    readers = [(track, _Reader(Path(track.path) if Path(track.path).is_absolute() else folder / track.path),
                int(round(track.at * RATE))) for track in mix.tracks]
    duck = _Duck()
    fade_from = total - int(MUSIC_FADE * RATE)
    try:
        for begin in range(0, total, _BLOCK):
            if stopped():
                raise SoundError("Stopped.")
            size = min(_BLOCK, total - begin)
            buses = {name: np.zeros((size, 2), dtype=np.float32) for name in BUSES}
            for track, reader, start in readers:
                if start >= begin + size:
                    continue
                local = max(0, start - begin)
                buses[track.bus][local:] += reader.take(size - local) * track.volume
            if mix.duck and (buses["music"].any() or buses["bed"].any()):
                key = buses["voice"] + (_speech(buses["original"]) if buses["original"].any() else 0.0)
                windows = _levels(np.pad(key, ((0, (-size) % _WINDOW), (0, 0))))
                under = _per_sample(duck.gains(windows), size)
            else:
                under = 1.0
            music = buses["music"]
            if music.any() and begin + size > fade_from:
                at = np.arange(begin, begin + size)
                music = music * np.clip((total - at) / (total - fade_from), 0, 1)[:, None] ** 1.5
            yield buses["voice"] + buses["original"] + (music + buses["bed"]) * under + buses["effects"]
    finally:
        for _track, reader, _start in readers:
            reader.close()


def _write(mix: Mix, folder: Path, output: Path, gain: float, stopped: Callable[[], bool]) -> float:
    """The mix at *gain*, its peaks held, written to *output*: its loudness as written."""
    limit = _Limiter()
    powers: list[float] = []
    with wave.open(str(output), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        for block in _blocks(mix, folder, stopped):
            held = limit(block * gain)
            powers += _powers(held)
            out.writeframes((held * 32767).astype("<i2").tobytes())
    return integrated(powers)


# Held peaks take a little loudness off a peaky mix (a hit, a whoosh): past this, it's set once more.
LOUDNESS_SLACK = 0.5


def mix_down(mix: Mix, folder: Path, output: Path, *, stopped: Callable[[], bool] = lambda: False) -> float:
    """Mix a video's sound into one WAV (16-bit stereo at ``RATE``) at its loudness: the first pass measures it, the
    second sets it and holds its peaks (and, when holding them took it short of its loudness, a third makes that
    up). The loudness it was before (LUFS; -inf for silence)."""
    powers: list[float] = []
    for block in _blocks(mix, folder, stopped):
        powers += _powers(block)
    before = integrated(powers)
    if not math.isfinite(before):
        _write(mix, folder, output, 1.0, stopped)
        return before
    lift = min(30.0, mix.loudness - before)
    made = _write(mix, folder, output, 10 ** (lift / 20), stopped)
    if math.isfinite(made) and mix.loudness - made > LOUDNESS_SLACK:
        _write(mix, folder, output, 10 ** (min(30.0, lift + mix.loudness - made) / 20), stopped)
    return before
