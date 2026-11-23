"""More voices for "Hey Row-Bot": Piper's 904-speaker LibriTTS-R generator, and blends of its speakers.

Runs in the throwaway training environment (``piper-sample-generator==3.2.0``; it brings Piper and
its espeak-ng, used here as a tool and never shipped), on the CPU (``CUDA_VISIBLE_DEVICES=``):

    python scripts/wake_word/piper_voices.py <sources> <clips>

The same phrases as ``speech.py``; speakers 0-799 for training and 800-903 (with their blends)
held out. Writes ``piper_train_*.npz`` and ``piper_eval_*.npz`` beside Kokoro's, in the same
``ClipSet`` layout, which ``train.py`` and ``evaluate.py`` add to Kokoro's.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from speech import ADVERSARIAL, POSITIVE, SENTENCES, _pack  # noqa: E402

SR = 16000
SEED = 20261124
HELD_OUT = 800


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sources", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--positive", type=int, default=3000)
    parser.add_argument("--adversarial", type=int, default=1500)
    parser.add_argument("--speech", type=int, default=600)
    parser.add_argument("--eval", type=int, default=600)
    args = parser.parse_args()

    import torch
    from piper_sample_generator.__main__ import generate_audio, get_phonemes
    from scipy.signal import resample_poly

    model_path = args.sources / "en_US-libritts_r-medium.pt"
    config = json.loads(Path(f"{model_path}.json").read_text(encoding="utf-8"))
    model = torch.load(model_path, weights_only=False, map_location="cpu").eval()
    rate = config["audio"]["sample_rate"]
    voice = config["espeak"]["voice"]
    rng = np.random.default_rng(SEED)

    def speak(text: str, speakers: range) -> np.ndarray:
        first, second = (int(rng.choice(speakers)) for _ in range(2))
        ids = get_phonemes(voice, config, text)
        with torch.no_grad():
            audio, lengths = generate_audio(model, torch.LongTensor([first]), torch.LongTensor([second]), [ids],
                                            float(rng.uniform(0.0, 1.0)), float(rng.uniform(0.6, 0.9)),
                                            float(rng.uniform(0.7, 0.9)), float(rng.uniform(0.8, 1.25)), None)
        x = audio[0, 0].cpu().numpy()[: int(lengths[0].flatten().sum().item()) + 1]
        x = x / max(0.01, float(np.abs(x).max())) * float(rng.uniform(0.3, 0.9))
        return resample_poly(x, SR, rate).astype(np.float32)

    train, held = range(0, HELD_OUT), range(HELD_OUT, config["num_speakers"])
    plan = (("piper_train_positive", POSITIVE, train, args.positive),
            ("piper_train_adversarial", ADVERSARIAL, train, args.adversarial),
            ("piper_train_speech", SENTENCES, train, args.speech),
            ("piper_eval_positive", POSITIVE, held, args.eval),
            ("piper_eval_adversarial", ADVERSARIAL, held, args.eval))
    args.out.mkdir(parents=True, exist_ok=True)
    for name, texts, speakers, count in plan:
        path = args.out / f"{name}.npz"
        if path.exists():
            continue
        print(f"{name}: {count} clips", flush=True)
        clips = []
        for index in range(count):
            clips.append(speak(texts[index % len(texts)], speakers))
            if (index + 1) % 250 == 0:
                print(f"    {index + 1}/{count}", flush=True)
        np.savez(path, **_pack(clips))
    return 0


if __name__ == "__main__":
    sys.exit(main())
