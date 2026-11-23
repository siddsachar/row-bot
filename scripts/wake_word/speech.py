"""Synthetic speech for training "Hey Row-Bot": the phrase, near misses and everyday sentences.

Runs in Row-Bot's own environment (kokoro-onnx, numpy and scipy are already there), on the CPU,
with the Kokoro files ``sources.py`` fetched. Voices are Kokoro's own and blends of two of them,
at varied speed; a fifth of the voices (and every blend of them) is held out for evaluation.

    python scripts/wake_word/speech.py <sources folder> <out folder>

writes ``train_positive.npz``, ``train_adversarial.npz``, ``train_speech.npz`` and
``eval_positive.npz``, ``eval_adversarial.npz``: 16 kHz int16 clips packed as LiveKit
WakeWord's ``ClipSet`` (audio, offsets, lengths, starts, ends of the spoken part).

"Row-Bot" and "robot" sound the same, so "hey robot" is the phrase, never a near miss.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np

SR = 16000
SEED = 20261123

POSITIVE = ("Hey Row-Bot", "Hey robot", "Hey, Row-Bot!", "Hey Row-Bot.", "Hey robot!",
            "Hey Row Bot", "Hey, robot.", "Hey Row-Bot?", "Hey Robot,")
# Close-sounding phrases it must not wake on (and none of them says "hey robot").
ADVERSARIAL = (
    "Robot", "The robot", "A robot", "Robots", "Hey Rob", "Hey Robert", "Hey Robin", "Hey Rhoda",
    "Hey bot", "Hey row", "Hey Ro", "Row boat", "Rowboat", "Hey rowboat", "Hey road", "Hey Roberta",
    "Hey Roblox", "Hey Siri", "Hey Google", "OK Google", "Alexa", "Hey Jarvis", "Hey buddy",
    "Hey Bob", "Hey Rowan", "Hey Rory", "Hey robe", "Hay bales", "Hey there", "Hello", "Hi Robert",
    "They rowed a boat", "Pay the robot", "Say robot", "Hey Rose", "Hey rover", "Hey Toby",
    "Rob it", "Row by row", "Hey Roman", "The rowboat", "Hey baby", "Hey Ruby", "Hey Robbie",
    "Hey rob them", "Hey Ronaldo", "Hey Rodney", "Rowing boats", "Hey, row it", "Roll the dice",
    "Hey, Bert", "Hey Dobbie", "Hey Bobby", "Okay, robot", "Hey Rome", "Hey Rhodes",
)
SENTENCES = (
    "What time is it?", "Can you turn the lights off in the kitchen?", "I'll be there in five minutes.",
    "Did you see the game last night?", "Let's order pizza for dinner.", "The meeting moved to Thursday.",
    "Could you send me that file again?", "It's going to rain tomorrow afternoon.",
    "My robot vacuum got stuck under the sofa again.", "Row, row, row your boat, gently down the stream.",
    "We rowed across the lake before breakfast.", "The robots in that film were terrifying.",
    "Hey, did anyone feed the cat?", "Hey, can you pass the salt?", "Hey, where are my keys?",
    "Okay, let's get started.", "Thanks, that's really helpful.", "I don't think that's right.",
    "Remind me to call my mother.", "Play some music, something calm.", "How far is the station?",
    "She works on industrial robotics.", "Robert is coming over at six.", "Rob left his jacket here.",
    "The boat was rocking in the wind.", "Robin Hood stole from the rich.", "That's a great idea.",
    "Put the kettle on, would you?", "Let me think about it for a second.", "I'm not sure yet.",
    "We need milk, eggs and bread.", "The train is running ten minutes late.", "Turn it up a bit.",
    "Can you hear me now?", "That was the best concert I've ever been to.", "Open the window, please.",
    "The kids are building a robot for the science fair.", "He rowed for his university.",
    "Hey, hey, slow down!", "Hey everyone, dinner's ready.", "Hey, look at that.",
    "Where did I park the car?", "Write that down before you forget.", "I'll check my calendar.",
    "Is this seat taken?", "Our flight leaves at seven in the morning.", "What's the weather like?",
    "The printer is out of paper again.", "Good morning, how did you sleep?", "See you tomorrow.",
)


def _voices(kokoro) -> list[str]:
    return sorted(kokoro.get_voices())


def _speak(kokoro, text: str, voice, speed: float) -> np.ndarray:
    from scipy.signal import resample_poly

    audio, rate = kokoro.create(text, voice=voice, speed=speed, lang="en-us")
    audio = np.asarray(audio, np.float32)
    if rate != SR:
        audio = resample_poly(audio, SR, rate).astype(np.float32)
    return audio


def _span(audio: np.ndarray) -> tuple[int, int]:
    """The spoken part: first and last 10 ms frame within 30 dB of the loudest."""
    frames = audio[: len(audio) // 160 * 160].reshape(-1, 160)
    energy = np.sqrt((frames**2).mean(1) + 1e-12)
    loud = np.flatnonzero(energy > energy.max() * 0.0316)
    if not len(loud):
        return 0, len(audio)
    return int(loud[0] * 160), int(min(len(audio), (loud[-1] + 1) * 160))


def _pack(clips: list[np.ndarray]) -> dict[str, np.ndarray]:
    pcm = [(np.clip(c, -1, 1) * 32767).astype(np.int16) for c in clips]
    lengths = np.array([len(c) for c in pcm], np.int64)
    spans = np.array([_span(c) for c in clips], np.int64).reshape(-1, 2)
    return {"audio": np.concatenate(pcm), "offsets": np.concatenate([[0], np.cumsum(lengths)[:-1]]),
            "lengths": lengths, "starts": spans[:, 0], "ends": spans[:, 1]}


def _generate(kokoro, texts, voices, count: int, rng: np.random.Generator) -> list[np.ndarray]:
    styles = {name: kokoro.get_voice_style(name) for name in voices}
    out = []
    for index in range(count):
        text = texts[index % len(texts)]
        first = voices[int(rng.integers(len(voices)))]
        style = styles[first]
        if rng.random() < 0.5:  # a new voice between two
            second = voices[int(rng.integers(len(voices)))]
            weight = float(rng.uniform(0.2, 0.8))
            style = weight * styles[first] + (1 - weight) * styles[second]
        speed = float(rng.uniform(0.8, 1.25))
        out.append(_speak(kokoro, text, style, speed))
        if (index + 1) % 250 == 0:
            print(f"    {index + 1}/{count}", flush=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sources", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--positive", type=int, default=3000)
    parser.add_argument("--adversarial", type=int, default=1500)
    parser.add_argument("--speech", type=int, default=600)
    parser.add_argument("--eval", type=int, default=600)
    args = parser.parse_args()
    from kokoro_onnx import Kokoro

    kokoro = Kokoro(str(args.sources / "kokoro-v1.0.fp16.onnx"), str(args.sources / "voices-v1.0.bin"))
    rng = np.random.default_rng(SEED)
    voices = _voices(kokoro)
    order = rng.permutation(len(voices))
    held = sorted(voices[i] for i in order[: max(1, len(voices) // 5)])
    train = sorted(v for v in voices if v not in held)
    print(f"{len(train)} training voices, {len(held)} held out: {', '.join(held)}")
    args.out.mkdir(parents=True, exist_ok=True)
    plan = (("train_positive", POSITIVE, train, args.positive),
            ("train_adversarial", ADVERSARIAL, train, args.adversarial),
            ("train_speech", SENTENCES, train, args.speech),
            ("eval_positive", POSITIVE, held, args.eval),
            ("eval_adversarial", ADVERSARIAL, held, args.eval))
    for name, texts, pool, count in plan:
        path = args.out / f"{name}.npz"
        if path.exists():
            continue
        print(f"{name}: {count} clips", flush=True)
        np.savez(path, **_pack(_generate(kokoro, texts, pool, count, rng)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
