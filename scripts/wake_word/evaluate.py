"""Measure a "Hey Row-Bot" model the way Row-Bot runs it (``row_bot.voice.wake``), on the CPU.

Runs in Row-Bot's environment with a scratch ``ROW_BOT_DATA_DIR``:

    python scripts/wake_word/evaluate.py <model.onnx> <sources> <clips>

- false accepts per hour on held-out real speech (LibriSpeech test-clean and test-other, about
  10.8 h), clean and with CC0 noise at 10 dB, and on the held-out voices' near misses;
- recall on the held-out voices saying the phrase, clean and at 10 dB, each after a second of
  real speech;
- detection latency: from the end of the phrase to the detection, in audio time;
- CPU: time per 80 ms step on one thread, and the process's memory with the model loaded.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

SR = 16000
STEP = 1280


def _rss_mb() -> float:
    import psutil

    return psutil.Process(os.getpid()).memory_info().rss / 2**20


def _speech(sources: Path) -> np.ndarray:
    import soundfile as sf

    parts = [sf.read(path, dtype="float32")[0]
             for name in ("test-clean", "test-other") for path in sorted((sources / name).rglob("*.flac"))]
    return np.concatenate(parts)


def _noise(sources: Path) -> np.ndarray:
    import soundfile as sf
    from scipy.signal import resample_poly

    parts = []
    for path in sorted((sources / "ms-snsd" / "noise_test").glob("*.wav")):
        x, rate = sf.read(path, dtype="float32", always_2d=True)
        x = x.mean(1)
        parts.append(resample_poly(x, SR, rate).astype(np.float32) if rate != SR else x)
    return np.concatenate(parts)


def _mix(signal: np.ndarray, noise: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    start = int(rng.integers(0, max(1, len(noise) - len(signal))))
    piece = np.resize(noise[start:], len(signal))
    gain = np.sqrt((signal**2).mean() / ((piece**2).mean() + 1e-12) / 10 ** (snr_db / 10))
    return signal + gain * piece


def _pcm(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def _clips(folder: Path, category: str) -> list[tuple[np.ndarray, int]]:
    """The held-out voices' clips (Kokoro's, and Piper's when made)."""
    out = []
    for path in (folder / f"eval_{category}.npz", folder / f"piper_eval_{category}.npz"):
        if path.exists():
            data = np.load(path)
            out += [(data["audio"][o : o + n].astype(np.float32) / 32767, int(e))
                    for o, n, e in zip(data["offsets"], data["lengths"], data["ends"])]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("model", type=Path)
    parser.add_argument("sources", type=Path)
    parser.add_argument("clips", type=Path)
    parser.add_argument("--threshold", type=float, nargs="*", default=[0.3, 0.5, 0.7])
    args = parser.parse_args()
    from row_bot.voice.wake import WakeDetector, WakeModel

    class Clock:
        """Audio time: the refractory period counts samples heard, not wall time."""

        now = 0.0

        def __call__(self) -> float:
            return self.now

    before = _rss_mb()
    model = WakeModel.load(args.model)
    loaded = _rss_mb()
    rng = np.random.default_rng(7)
    speech, noise = _speech(args.sources), _noise(args.sources)
    positives = _clips(args.clips, "positive")
    near = _clips(args.clips, "adversarial")
    report: dict = {"model": str(args.model), "memory_mb": {"before": round(before), "loaded": round(loaded)}}

    # CPU: one step at a time, as the host runs it.
    state = model.warm
    chunk = np.zeros((1, STEP), np.float32)
    timings = []
    for _ in range(500):
        started = time.perf_counter()
        _, state = model.step(chunk, state)
        timings.append(time.perf_counter() - started)
    step_ms = float(np.median(timings) * 1000)
    report["cpu"] = {"step_ms_median": round(step_ms, 3), "one_core_percent": round(step_ms / 80 * 100, 2)}

    def run(threshold: float, audio: np.ndarray) -> list[int]:
        clock = Clock()
        detector = WakeDetector(model, clock=clock, threshold=threshold)
        out, data = [], _pcm(audio)
        for index in range(0, len(data), 640):
            clock.now = index / 2 / SR
            if detector.hear(data[index : index + 640]):
                out.append((index + 640) // 2)
        return out

    hours = len(speech) / SR / 3600
    noisy = _mix(speech, noise, 10.0, rng)
    near_audio = np.concatenate([np.concatenate([c, np.zeros(SR // 2, np.float32)]) for c, _ in near])
    near_hours = len(near_audio) / SR / 3600
    report["threshold"] = {}
    for threshold in args.threshold:
        row: dict = {
            "false_accepts_per_hour": {
                "speech_clean": round(len(run(threshold, speech)) / hours, 3),
                "speech_noise_10db": round(len(run(threshold, noisy)) / hours, 3),
                "near_misses": len(run(threshold, near_audio)),
                "near_miss_clips": len(near),
            },
        }
        for label, snr in (("clean", None), ("noise_10db", 10.0)):
            hits, delays = 0, []
            for clip, end in positives:
                lead = speech[int(rng.integers(0, len(speech) - SR)):][:SR]
                audio = np.concatenate([lead, clip, np.zeros(SR // 2, np.float32)])
                if snr is not None:
                    audio = _mix(audio, noise, snr, rng)
                found = [d for d in run(threshold, audio) if d >= SR]
                if found:
                    hits += 1
                    delays.append((found[0] - (SR + end)) / SR * 1000)
            row[f"recall_{label}"] = round(hits / len(positives), 3)
            if delays:
                row[f"latency_ms_{label}"] = {"p50": round(float(np.median(delays))),
                                              "p90": round(float(np.percentile(delays, 90)))}
        report["threshold"][str(threshold)] = row
    report["hours"] = {"speech": round(hours, 2), "near_misses": round(near_hours, 3)}
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
