"""Train "Hey Row-Bot" with LiveKit WakeWord's trainer on local data only.

LiveKit's own recipe generates its speech with a cloud TTS service and streams negative speech,
noise and rooms from the internet. This driver feeds the same training loop local inputs instead:
the Kokoro clips ``speech.py`` made, LibriSpeech's dev sets as background speech (a held-out part
of them calibrates the threshold), MS-SNSD's CC0 noise plus generated coloured noise, and
generated rooms. Nothing is fetched while it runs.

It runs in a throwaway environment, never Row-Bot's (``docs/VOICE_ENGINE.md`` has the commands):

    LIVEKIT_WAKEWORD_ENCODERS=<sources>/encoders HF_HUB_OFFLINE=1 \\
        python scripts/wake_word/train.py <sources> <clips> <out>/hey_row_bot.onnx

On Windows the data loader runs in this process (``workers=0``): spawned workers would not see
the local data.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np

SR = 16000
PHRASE = "hey robot"  # how "Hey Row-Bot" is said; the phrase encoder reads letters


def _speech(sources: Path, sets: tuple[str, ...]) -> list[np.ndarray]:
    """Every utterance of these LibriSpeech sets, as 16 kHz int16."""
    import soundfile as sf

    out = []
    for name in sets:
        for path in sorted((sources / name).rglob("*.flac")):
            audio, rate = sf.read(path, dtype="int16")
            assert rate == SR, path
            out.append(audio)
    return out


def _noise(sources: Path, seconds: float = 600.0) -> np.ndarray:
    """MS-SNSD's CC0 clips and coloured noise, each at the same level, as one float16 array."""
    import soundfile as sf
    from scipy.signal import resample_poly

    rng = np.random.default_rng(1)
    parts = []
    for path in sorted((sources / "ms-snsd").rglob("*.wav")):
        x, rate = sf.read(path, dtype="float32", always_2d=True)
        x = x.mean(1)
        if rate != SR:
            x = resample_poly(x, SR, rate).astype(np.float32)
        parts.append(x)
    n = int(seconds * SR)
    white = rng.standard_normal(n).astype(np.float32)
    spectrum = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    freqs[0] = freqs[1]
    for power in (0.5, 1.0):  # pink, brown
        parts.append(np.fft.irfft(spectrum / freqs**power, n).astype(np.float32))
    parts.append(white)
    out = []
    for x in parts:
        rms = float(np.sqrt((x**2).mean() + 1e-12))
        if rms > 1e-5:
            out.append((x / rms * 0.1).astype(np.float16))
    return np.concatenate(out)


def _rooms(count: int = 600, length: int = SR) -> np.ndarray:
    """Generated room responses: a direct path at 0, a few early reflections and a decaying tail."""
    rng = np.random.default_rng(2)
    out = np.zeros((count, length), np.float32)
    t = np.arange(length) / SR
    for i in range(count):
        rt60 = rng.uniform(0.15, 0.9)
        tail = rng.standard_normal(length) * np.exp(-6.9 * t / rt60)
        tail[: int(rng.uniform(0.002, 0.012) * SR)] = 0
        h = tail * rng.uniform(0.2, 0.8)
        for _ in range(int(rng.integers(3, 9))):
            h[int(rng.uniform(0.001, 0.03) * SR)] += rng.uniform(-0.7, 0.7)
        h[0] = 1.0
        if rng.random() < 0.7:  # walls absorb high frequencies
            kernel = np.ones(int(rng.integers(2, 6))) / 1.0
            h[1:] = np.convolve(h[1:], kernel / kernel.sum(), mode="same")
        out[i] = h / (np.sqrt((h**2).sum()) + 1e-8)
    return out


class _LocalSpeech:
    """Stands in for the trainer's streaming pool: random crops of local speech."""

    pools: dict[str, np.ndarray] = {}

    def __init__(self, files, seed: int = 0, **_: object) -> None:
        self.audio = self.pools[files[0][1]]
        self.rng = np.random.default_rng(seed)

    def crop(self, rng: np.random.Generator, length: int, max_wait_s: float = 0.0) -> np.ndarray:
        start = int(rng.integers(0, max(1, len(self.audio) - length)))
        piece = self.audio[start : start + length]
        return np.pad(piece, (0, length - len(piece))).astype(np.int16)

    def stop(self, join: bool = False, timeout: float = 0.0) -> None:
        pass


def _clips(folder: Path):
    from livekit.wakeword_train.data import ClipSet

    sets = {}
    for category in ("positive", "adversarial", "speech"):
        data = np.load(folder / f"train_{category}.npz")
        sets[category] = ClipSet(data["audio"], *(data[k] for k in ("offsets", "lengths", "starts", "ends")))
    return sets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sources", type=Path)
    parser.add_argument("clips", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--steps", type=int, default=6000)
    parser.add_argument("--target-fpph", type=float, default=0.5)
    args = parser.parse_args()

    from dataclasses import replace

    import torch
    from livekit.wakeword_train import data as D
    from livekit.wakeword_train import loop as L
    from livekit.wakeword_train.encoders import load_pretrained
    from livekit.wakeword_train.export import calibrate, export_streaming
    from livekit.wakeword_train.pipeline import STANDARD_P_PARTY

    utterances = _speech(args.sources, ("dev-clean", "dev-other"))
    rng = np.random.default_rng(3)
    order = rng.permutation(len(utterances))
    cut = int(len(order) * 0.8)
    _LocalSpeech.pools = {
        "train": np.concatenate([utterances[i] for i in order[:cut]]),
        "calibration": np.concatenate([utterances[i] for i in order[cut:]]),
    }
    hours = {k: len(v) / SR / 3600 for k, v in _LocalSpeech.pools.items()}
    print(f"background speech: {hours['train']:.1f} h training, {hours['calibration']:.1f} h calibration")
    clips = _clips(args.clips)
    noise = _noise(args.sources)
    rooms = _rooms()

    D.load_clipsets = lambda dataset: clips
    D.negative_speech_files = lambda: [("local", "train")]
    D.StreamPool = _LocalSpeech
    D.noise_pool = lambda: noise
    D.rir_pool = lambda max_len=SR: rooms
    L.calibration_speech_files = lambda: [("local", "calibration")]

    device = L.default_device()
    print(f"training on {device}")
    start = load_pretrained("standard").bake(PHRASE)
    aug = L.default_aug(start)
    config = L.TrainConfig(steps=args.steps, aug=replace(aug, p_party=STANDARD_P_PARTY), workers=0)
    detector = L.train(start, "local", config, device=device)
    logit = L.calibration_logit(detector, target_fpph=args.target_fpph, device=device, aug=aug)
    print(f"calibration: score 0.5 = {args.target_fpph} false alarms an hour (logit {logit:.2f})")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    meta = {"wake_word": "Hey Row-Bot", "language": "en", "threshold": "0.5",
            "format": "livekit-wakeword-v2", "trained_by": "Row-Bot (scripts/wake_word)"}
    export_streaming(calibrate(detector.cpu(), logit), args.out, metadata=meta)
    torch.save(detector.state_dict(), args.out.with_suffix(".pt"))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
