"""The wake word, "Hey Row-Bot": opt-in, desktop only, heard on this computer.

The host never opens a microphone. While the person has armed the wake word, the
desktop window captures with Row-Bot Voice's worklet and streams 16 kHz frames to
``WS /api/v1/voice/stream`` in its wake mode, over loopback. Each 80 ms step runs
one small streaming ONNX model (LiveKit WakeWord's format: a ``chunk`` of 1280
samples and the model's state tensors in, its score and next state out). Audio is
kept only until its step is full, never written anywhere and never sent on. A
detection is only an event: the window then starts an ordinary Row-Bot Voice
session.

Three guards keep it from waking by mistake: a score threshold, a refractory
period after each detection, and silence while any voice session or dictation
holds the microphone's lease.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from pathlib import Path
import platform
import threading
import time
from typing import Any

import numpy as np

from row_bot.voice.client_transport import DictationError, DictationHandle, DictationOwner
from row_bot.voice.tickets import RedeemedTicket, VoiceTicket, VoiceTickets

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).with_name("hey_row_bot.onnx")
SAMPLE_RATE = 16000
STEP_SAMPLES = 1280  # 80 ms
# The model's state starts from 2 s of silence, as in training.
WARMUP_STEPS = 25
THRESHOLD = 0.5
REFRACTORY_SECONDS = 2.0
# The window sends 20 ms frames; anything larger than a second is refused.
MAX_FRAME_BYTES = SAMPLE_RATE * 2
# ONNX Runtime 1.27 on arm64 runs batch-1 convolutions through KleidiAI kernels
# that return wrong results; older versions accept the entry and ignore it.
_DISABLE_KLEIDIAI = "mlas.disable_kleidiai"


class WakeModel:
    """One loaded wake word model; each listener streams through it with its own state."""

    def __init__(self, session: Any) -> None:
        inputs = session.get_inputs()
        if "chunk" not in {item.name for item in inputs}:
            raise ValueError("wake_model_invalid")
        self._session = session
        self._lock = threading.Lock()
        self._outputs = [item.name.removesuffix("_out") for item in session.get_outputs()[1:]]
        state = {item.name: np.zeros(item.shape, np.int64 if "int64" in item.type else np.float32)
                 for item in inputs if item.name != "chunk"}
        silence = np.zeros((1, STEP_SAMPLES), np.float32)
        for _ in range(WARMUP_STEPS):
            state = self.step(silence, state)[1]
        self.warm: dict[str, np.ndarray] = state

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "WakeModel":
        from row_bot import onnxruntime_telemetry

        onnxruntime_telemetry.switch_off()
        from onnxruntime import InferenceSession, SessionOptions

        options = SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        if platform.machine().lower() in {"arm64", "aarch64"}:
            options.add_session_config_entry(_DISABLE_KLEIDIAI, "1")
        session = InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
        return cls(session)

    def step(self, chunk: np.ndarray, state: dict[str, np.ndarray]) -> tuple[float, dict[str, np.ndarray]]:
        with self._lock:
            out = self._session.run(None, {"chunk": chunk, **state})
        return float(np.asarray(out[0]).reshape(-1)[0]), dict(zip(self._outputs, out[1:]))


class WakeDetector:
    """One armed window's listener: 16-bit PCM in, True when it hears the wake word."""

    def __init__(self, model: WakeModel, *, clock: Callable[[], float] = time.monotonic,
                 threshold: float = THRESHOLD, refractory: float = REFRACTORY_SECONDS) -> None:
        self._model = model
        self._clock = clock
        self._threshold = threshold
        self._refractory = refractory
        self._state = model.warm
        self._pending = np.zeros(0, np.float32)
        self._quiet_until = float("-inf")

    def reset(self) -> None:
        """Forget everything heard so far."""
        self._state = self._model.warm
        self._pending = np.zeros(0, np.float32)

    def hear(self, pcm16: bytes) -> bool:
        if self._clock() < self._quiet_until:
            return False
        samples = np.frombuffer(pcm16[: len(pcm16) // 2 * 2], np.int16).astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, samples])
        while len(self._pending) >= STEP_SAMPLES:
            chunk = np.ascontiguousarray(self._pending[None, :STEP_SAMPLES])
            self._pending = self._pending[STEP_SAMPLES:]
            score, self._state = self._model.step(chunk, self._state)
            if score >= self._threshold:
                self.reset()
                self._quiet_until = self._clock() + self._refractory
                return True
        return False


@dataclass(frozen=True)
class WakeOptions:
    """What decides whether the wake word may listen now (read when asked, never cached)."""

    enabled: Callable[[], bool]
    # The shipped model unless a test names another.
    model_path: Path | None = None

    @property
    def model(self) -> Path:
        return self.model_path or MODEL_PATH


def wake_word_enabled() -> bool:
    from row_bot.voice.runtime import load_voice_runtime_settings

    return load_voice_runtime_settings().wake_word


class WakeListening:
    """Tickets for the wake mode of the voice socket, and its shared model."""

    def __init__(self, *, voice_busy: Callable[[], bool], clock: Callable[[], float] = time.monotonic,
                 options: WakeOptions | None = None) -> None:
        self.voice_busy = voice_busy
        self.clock = clock
        self.options = options or WakeOptions(enabled=wake_word_enabled)
        self._tickets = VoiceTickets(clock)
        self._model: WakeModel | None = None
        self._lock = threading.Lock()
        self.tick_seconds = 5.0

    def reason(self) -> str:
        """``available``, or why the wake word can't listen on this computer."""
        return "available" if self.options.model.is_file() else "wake_model_missing"

    def ticket(self, owner: DictationOwner, csrf: str) -> VoiceTicket:
        if not self.options.enabled():
            raise DictationError("wake_word_off")
        if self.reason() != "available":
            raise DictationError("wake_model_missing")
        # Wake tickets carry no conversation or voice lease: each opens one wake socket, once.
        handle = DictationHandle("wake", 0, "", owner.server_epoch)
        return self._tickets.issue(owner, handle, csrf)

    def redeem(self, ticket: object) -> RedeemedTicket:
        return self._tickets.redeem(ticket)

    def model(self) -> WakeModel:
        """The loaded model (once); called off the event loop."""
        with self._lock:
            if self._model is None:
                self._model = WakeModel.load(self.options.model)
            return self._model


async def run_wake_socket(listening: WakeListening, *, send_event: Callable[[dict], Awaitable[None]],
                          receive: Callable[[], Awaitable[Any]], revalidate: Callable[[], Awaitable[None]],
                          threshold: float = THRESHOLD) -> None:
    """Drive one armed window's socket until it ends, the person turns the wake word off, or access ends."""
    detector = WakeDetector(await asyncio.to_thread(listening.model), clock=listening.clock,
                            threshold=threshold)
    ended = asyncio.Event()

    async def watch() -> None:
        while not ended.is_set():
            try:
                await asyncio.wait_for(ended.wait(), timeout=listening.tick_seconds)
                return
            except TimeoutError:
                pass
            try:
                await revalidate()
                if not await asyncio.to_thread(listening.options.enabled):
                    ended.set()
            except Exception:
                ended.set()

    async def pump() -> None:
        busy = False
        while not ended.is_set():
            message = await receive()
            if message is None:
                ended.set()
                return
            if isinstance(message, bytes):
                if len(message) > MAX_FRAME_BYTES:
                    raise ValueError("wake_frame_too_large")
                if listening.voice_busy():
                    # Voice or dictation has the microphone: nothing is heard, and
                    # nothing half-heard carries over to afterwards.
                    if not busy:
                        detector.reset()
                    busy = True
                    continue
                busy = False
                if await asyncio.to_thread(detector.hear, message):
                    await send_event({"type": "wake"})
            elif message.get("type") == "end":
                ended.set()
                return
            else:
                raise ValueError("invalid_command")

    workers: list[asyncio.Task] = []
    try:
        await send_event({"type": "ready", "input_sample_rate": SAMPLE_RATE})
        workers = [asyncio.create_task(watch(), name="row-bot-wake-watch"),
                   asyncio.create_task(pump(), name="row-bot-wake-pump")]
        done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                raise task.exception()
    finally:
        ended.set()
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
