"""Original, royalty-free background music, made on this computer (5.3.1 B): no samples, no network.

Warm pads, an electric-piano arpeggio, a round bass and soft drums with a light reverb, on a four-chord loop. The
layers follow the video's beats: each beat (seconds into the video) starts the next section, from pads alone to
the full groove, so the music builds where the video does. A mood sets the tempo, the chords and how far it
builds. numpy only: filters and the reverb run in the frequency domain.
"""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Literal

import numpy as np

SR = 44100
MAX_SECONDS = 20 * 60
Mood = Literal["calm", "upbeat", "focused", "warm"]
Layer = Literal["pad", "keys", "bass", "drums"]

# MIDI notes per chord: pad voicing, arpeggio tones, bass root.
_FMAJ7_LOOP = (
    {"pad": (53, 57, 60, 64), "arp": (65, 69, 72, 76, 72, 69), "bass": 41},  # Fmaj7
    {"pad": (55, 59, 62, 64), "arp": (67, 71, 74, 76, 74, 71), "bass": 43},  # G6
    {"pad": (52, 55, 59, 62), "arp": (64, 67, 71, 74, 71, 67), "bass": 40},  # Em7
    {"pad": (57, 60, 64, 67), "arp": (69, 72, 76, 79, 76, 72), "bass": 45},  # Am7
)
_CMAJ_LOOP = (
    {"pad": (48, 55, 60, 64), "arp": (60, 64, 67, 72, 67, 64), "bass": 36},  # C
    {"pad": (45, 52, 57, 60), "arp": (57, 60, 64, 69, 64, 60), "bass": 33},  # Am
    {"pad": (53, 57, 60, 65), "arp": (65, 69, 72, 77, 72, 69), "bass": 41},  # F
    {"pad": (55, 59, 62, 67), "arp": (67, 71, 74, 79, 74, 71), "bass": 43},  # G
)
_DMIN_LOOP = (
    {"pad": (50, 53, 57, 60), "arp": (62, 65, 69, 72, 69, 65), "bass": 38},  # Dm7
    {"pad": (46, 50, 53, 57), "arp": (58, 62, 65, 69, 65, 62), "bass": 34},  # Bbmaj7
    {"pad": (48, 52, 55, 58), "arp": (60, 64, 67, 70, 67, 64), "bass": 36},  # C7
    {"pad": (45, 48, 52, 55), "arp": (57, 60, 64, 67, 64, 60), "bass": 33},  # Am7
)


@dataclass(frozen=True)
class Style:
    bpm: float
    chords: tuple[dict, ...]
    # The layers of each section, in the order the beats reach them; the last repeats.
    sections: tuple[frozenset[Layer], ...]


STYLES: dict[Mood, Style] = {
    "calm": Style(90, _FMAJ7_LOOP, (frozenset({"pad"}), frozenset({"pad", "keys"}),
                                   frozenset({"pad", "keys", "bass", "drums"}))),
    "upbeat": Style(112, _CMAJ_LOOP, (frozenset({"pad", "keys"}), frozenset({"pad", "keys", "bass", "drums"}))),
    "focused": Style(80, _DMIN_LOOP, (frozenset({"pad"}), frozenset({"pad", "keys"}),
                                     frozenset({"pad", "keys", "bass"}))),
    "warm": Style(84, _FMAJ7_LOOP, (frozenset({"pad"}), frozenset({"pad", "keys", "bass"}),
                                   frozenset({"pad", "keys", "bass", "drums"}))),
}


def hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def _envelope(n: int, attack: float, decay: float, sustain: float, release: float) -> np.ndarray:
    t = np.arange(n) / SR
    total = n / SR
    shape = np.full(n, sustain)
    shape = np.where(t < attack, t / max(attack, 1e-4), shape)
    decaying = (t >= attack) & (t < attack + decay)
    shape = np.where(decaying, 1 - (1 - sustain) * (t - attack) / max(decay, 1e-4), shape)
    releasing = t > total - release
    return np.where(releasing, shape * np.clip((total - t) / max(release, 1e-4), 0, 1), shape)


def _filter(x: np.ndarray, *, low: float = 0.0, high: float = 0.0, order: int = 2) -> np.ndarray:
    """A Butterworth-shaped high-pass (``low``) and/or low-pass (``high``), zero phase, in the frequency domain."""
    spectrum = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    gain = np.ones_like(f)
    if high:
        gain /= np.sqrt(1 + (f / high) ** (2 * order))
    if low:
        with np.errstate(divide="ignore"):
            gain /= np.sqrt(1 + np.where(f > 0, (low / np.maximum(f, 1e-9)) ** (2 * order), np.inf))
    return np.fft.irfft(spectrum * gain, len(x))


def _convolve(a: np.ndarray, b: np.ndarray, length: int) -> np.ndarray:
    size = 1 << int(np.ceil(np.log2(len(a) + len(b) - 1)))
    return np.fft.irfft(np.fft.rfft(a, size) * np.fft.rfft(b, size), size)[:length]


class _Mix:
    def __init__(self, seconds: float) -> None:
        self.n = int(seconds * SR) + SR * 4
        self.buses = {name: np.zeros((self.n, 2)) for name in ("pad", "keys", "bass", "drums")}

    def add(self, bus: str, start: float, signal: np.ndarray, pan: float = 0.0) -> None:
        i = int(start * SR)
        if i >= self.n:
            return
        signal = signal[: self.n - i]
        left, right = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
        self.buses[bus][i:i + len(signal), 0] += signal * left * 1.414
        self.buses[bus][i:i + len(signal), 1] += signal * right * 1.414


def _pad(note: int, seconds: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    n = int(seconds * SR)
    t = np.arange(n) / SR
    sides = []
    for detune in (-0.06, 0.06):  # A slow chorus between the sides.
        vibrato = 1 + 0.0015 * np.sin(2 * np.pi * (0.2 + rng.random() * 0.1) * t)
        phase = 2 * np.pi * hz(note + detune) * np.cumsum(vibrato) / SR
        tone = np.sin(phase) + 0.28 * np.sin(2 * phase) + 0.12 * np.sin(3 * phase) + 0.05 * np.sin(4 * phase)
        sides.append(tone * _envelope(n, 0.9, 0.6, 0.85, 1.4))
    return sides[0], sides[1]


def _epiano(note: int, velocity: float) -> np.ndarray:
    n = int(1.6 * SR)
    t = np.arange(n) / SR
    f = hz(note)
    body = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.55)
    bell = (0.5 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / 0.3)
            + 0.18 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t / 0.15))
    tine = 0.12 * np.sin(2 * np.pi * 7.0 * f * t) * np.exp(-t / 0.04)
    return (body + bell + tine) * np.clip(t / 0.004, 0, 1) * velocity


def _bass(note: int, seconds: float) -> np.ndarray:
    n = int(seconds * SR)
    t = np.arange(n) / SR
    f = hz(note)
    tone = np.sin(2 * np.pi * f * t) + 0.18 * np.sin(2 * np.pi * 2 * f * t)
    return _filter(tone * _envelope(n, 0.012, 0.25, 0.65, 0.12), high=400)


def _kick() -> np.ndarray:
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    phase = 2 * np.pi * np.cumsum(46 + 70 * np.exp(-t / 0.035)) / SR
    return np.sin(phase) * np.exp(-t / 0.16) * np.clip(t / 0.002, 0, 1)


def _hat(rng: np.random.Generator) -> np.ndarray:
    n = int(0.08 * SR)
    return _filter(rng.standard_normal(n), low=7500) * np.exp(-np.arange(n) / SR / 0.018)


def _snap(rng: np.random.Generator) -> np.ndarray:
    n = int(0.25 * SR)
    return _filter(rng.standard_normal(n), low=900, high=3200) * np.exp(-np.arange(n) / SR / 0.07)


def _reverb(seconds: float, rng: np.random.Generator) -> np.ndarray:
    n = int(seconds * SR)
    t = np.arange(n) / SR
    response = np.stack([rng.standard_normal(n), rng.standard_normal(n)], axis=1) * np.exp(-t / (seconds / 5))[:, None]
    response[:, 0] = _filter(response[:, 0], high=5000)
    response[:, 1] = _filter(response[:, 1], high=5000)
    return response / np.sqrt((response ** 2).sum(axis=0))


def compose(seconds: float, *, mood: Mood = "calm", beats: list[float] | None = None, seed: int = 7) -> np.ndarray:
    """A stereo track (float, -1..1) of *seconds*, whose sections start at *beats* (seconds into it)."""
    if not 1 <= seconds <= MAX_SECONDS:
        raise ValueError(f"music is between 1 second and {MAX_SECONDS // 60} minutes long")
    style = STYLES[mood]
    beat = 60 / style.bpm
    bar = 4 * beat
    rng = np.random.default_rng(seed)
    mix = _Mix(seconds)
    bars = int(np.ceil(seconds / bar)) + 1
    starts = sorted(b for b in (beats or []) if 0 < b < seconds)
    if not starts:  # Without beats it builds over the first quarter, then stays.
        starts = [seconds * 0.08, seconds * 0.2]
    section_bars = [0] + [int(round(b / bar)) for b in starts]
    kick, hats, snap = _kick(), [_hat(rng) for _ in range(4)], _snap(rng)
    # Each note is made once and reused bar after bar: a five-minute track stays quick.
    pads: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    keys: dict[int, np.ndarray] = {}
    basses: dict[tuple[int, float], np.ndarray] = {}

    def pad(note: int) -> tuple[np.ndarray, np.ndarray]:
        if note not in pads:
            pads[note] = _pad(note, bar + 1.4, rng)
        return pads[note]

    def key(note: int) -> np.ndarray:
        if note not in keys:
            keys[note] = _epiano(note, 1.0)
        return keys[note]

    def bass(note: int, length: float) -> np.ndarray:
        if (note, length) not in basses:
            basses[(note, length)] = _bass(note, length)
        return basses[(note, length)]

    def layers_at(index: int) -> frozenset[Layer]:
        section = max(i for i, first in enumerate(section_bars) if index >= first)
        return style.sections[min(section, len(style.sections) - 1)]

    for index in range(bars):
        t0 = index * bar
        if t0 > seconds + 2:
            break
        chord = style.chords[index % len(style.chords)]
        on = layers_at(index)
        if "pad" in on:
            for j, note in enumerate(chord["pad"]):
                left, right = pad(note)
                mix.add("pad", t0, left * 0.05, pan=-0.6 + 0.4 * j / 3)
                mix.add("pad", t0, right * 0.05, pan=0.6 - 0.4 * j / 3)
        if "keys" in on:
            for step in range(8):
                note = chord["arp"][step % len(chord["arp"])]
                swing = 0.035 if step % 2 else 0.0
                velocity = 0.34 * (1.0 if step % 4 == 0 else 0.72) * (0.9 + 0.2 * rng.random())
                mix.add("keys", t0 + step * beat / 2 + swing, key(note) * velocity, pan=-0.25 if step % 2 else 0.25)
        if "bass" in on:
            root = chord["bass"]
            mix.add("bass", t0, bass(root, beat * 1.6) * 0.42)
            mix.add("bass", t0 + 2 * beat, bass(root + 7 if index % 2 else root, beat * 1.2) * 0.34)
            mix.add("bass", t0 + 3.5 * beat, bass(root + 12, beat * 0.45) * 0.22)
        if "drums" in on:
            for count in (0, 2):
                mix.add("drums", t0 + count * beat, kick * 0.55)
            mix.add("drums", t0 + 2.5 * beat + 0.02, kick * 0.25)
            for count in (1, 3):
                mix.add("drums", t0 + count * beat + 0.01, snap * 0.10, pan=0.1)
            for step in range(8):
                swing = 0.035 if step % 2 else 0.0
                mix.add("drums", t0 + step * beat / 2 + swing, hats[step % 4] * (0.09 if step % 2 else 0.06), pan=0.35)

    reverb = _reverb(2.4, rng)
    out = np.zeros((mix.n, 2))
    sends = {"pad": 0.35, "keys": 0.30, "bass": 0.0, "drums": 0.08}
    for name, bus in mix.buses.items():
        if not bus.any():
            continue
        for side in (0, 1):
            # The pads stay out of the low-mids the bass and keys need.
            bus[:, side] = _filter(bus[:, side], low=220 if name == "pad" else 0.0, high=15000)
        out += bus
        if sends[name]:
            out += np.stack([_convolve(bus[:, side], reverb[:, side], mix.n) for side in (0, 1)], axis=1) * sends[name]
    out = out[: int(seconds * SR)]
    fade_in, fade_out = min(int(1.5 * SR), len(out) // 4), min(int(4.0 * SR), len(out) // 3)
    out[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    out[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None] ** 1.5
    return out / (np.abs(out).max() + 1e-9) * 0.89


def wav_bytes(track: np.ndarray) -> bytes:
    """16-bit stereo WAV at 44.1 kHz."""
    pcm = (np.clip(track, -1, 1) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SR)
        out.writeframes(pcm.tobytes())
    return buffer.getvalue()
