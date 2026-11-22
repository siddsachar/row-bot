"""tts.py – Text-to-Speech service using Kokoro TTS (local, offline).

Kokoro is a fast, high-quality neural text-to-speech model that runs
entirely via ONNX Runtime.  This module manages model downloads,
preprocesses agent responses (stripping markdown, truncating long text),
and returns WAV bytes for the browser to play: it never uses a host speaker.

Everything runs locally — no audio is sent to the cloud.
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any, Callable

import numpy as np
from row_bot.data_paths import get_row_bot_data_dir
from row_bot.voice.speech_policy import clean_for_speech

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────
_ROW_BOT_DIR = get_row_bot_data_dir()
_KOKORO_DIR = _ROW_BOT_DIR / "kokoro"
_SETTINGS_PATH = _ROW_BOT_DIR / "tts_settings.json"

# ── Model download URLs ─────────────────────────────────────────────────────
_KOKORO_RELEASE_BASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)
_MODEL_FILENAME = "kokoro-v1.0.fp16.onnx"
_VOICES_FILENAME = "voices-v1.0.bin"

_MODEL_PATH = _KOKORO_DIR / _MODEL_FILENAME
_VOICES_PATH = _KOKORO_DIR / _VOICES_FILENAME

_MODEL_URL = f"{_KOKORO_RELEASE_BASE}/{_MODEL_FILENAME}"
_VOICES_URL = f"{_KOKORO_RELEASE_BASE}/{_VOICES_FILENAME}"
# Their sizes and SHA-256, as that release serves them: a download is kept only when both match (5.3.1).
_MODEL_BYTES, _MODEL_SHA256 = 177_464_787, "c1610a859f3bdea01107e73e50100685af38fff88f5cd8e5c56df109ec880204"
_VOICES_BYTES, _VOICES_SHA256 = 28_214_398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
VOICE_DOWNLOAD_BYTES = _MODEL_BYTES + _VOICES_BYTES
VOICE_DOWNLOAD_HOST = "github.com (thewh1teagle/kokoro-onnx)"


def _prepare_configured_runtime_tmp() -> None:
    """Create an explicitly configured data-local temp directory.

    The Kokoro phonemizer loads a temporary copy of ``libespeak``. Hardened
    server containers keep shared ``/tmp`` mounted ``noexec``, so the Docker
    runtime points ``TMPDIR`` at an isolated directory under Row-Bot data.
    Ignore arbitrary external temp paths; only the app-owned data tree is
    eligible for this bootstrap.
    """
    configured = str(os.environ.get("TMPDIR") or "").strip()
    if not configured:
        return
    candidate = Path(configured).resolve()
    data_root = _ROW_BOT_DIR.resolve()
    try:
        candidate.relative_to(data_root)
    except ValueError:
        return
    candidate.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(candidate)


_prepare_configured_runtime_tmp()

# ── Voice catalog (id → display name) ───────────────────────────────────────
# Curated set of the highest-rated Kokoro v1.0 voices.
VOICE_CATALOG: dict[str, str] = {
    "af_heart":   "Heart (American Female) ❤️",
    "af_bella":   "Bella (American Female)",
    "af_nicole":  "Nicole (American Female)",
    "af_sarah":   "Sarah (American Female)",
    "af_nova":    "Nova (American Female)",
    "am_michael": "Michael (American Male)",
    "am_fenrir":  "Fenrir (American Male)",
    "am_puck":    "Puck (American Male)",
    "bf_emma":    "Emma (British Female)",
    "bm_george":  "George (British Male)",
}
_DEFAULT_VOICE = "af_heart"


# ═════════════════════════════════════════════════════════════════════════════
# TTSService
# ═════════════════════════════════════════════════════════════════════════════

class _SerialKokoro:
    """The Kokoro model, one voice at a time: its phonemizer (espeak) is not thread-safe, and voices made together
    traded lines ("number of lines in input and output must be equal")."""

    def __init__(self, model: Any) -> None:
        self._model = model
        self._lock = threading.Lock()

    def create(self, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return self._model.create(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)


# One Kokoro model (~169 MB) for every TTSService in the process.
_SHARED_KOKORO: list[_SerialKokoro] = []
_KOKORO_LOAD_LOCK = threading.Lock()


class TTSService:
    """Text-to-speech using Kokoro TTS via ONNX Runtime (local, offline): WAV bytes, never host playback."""

    def __init__(self) -> None:
        # Lazy-loaded Kokoro instance (heavy — loads ~169 MB model)
        self._kokoro = None

        # Persisted settings
        self._voice: str = _DEFAULT_VOICE
        self._speed: float = 1.0
        self._load_settings()

    # ── Kokoro model (lazy-loaded) ───────────────────────────────────────

    def _get_kokoro(self):
        """Return the Kokoro instance, loading the model on first call (one model for every service)."""
        if self._kokoro is not None:
            return self._kokoro

        with _KOKORO_LOAD_LOCK:
            if _SHARED_KOKORO:
                self._kokoro = _SHARED_KOKORO[0]
                return self._kokoro

            if not self.is_installed():
                return None

            try:
                from kokoro_onnx import Kokoro
                logger.info("Loading Kokoro TTS model from %s", _MODEL_PATH)
                _SHARED_KOKORO.append(_SerialKokoro(Kokoro(str(_MODEL_PATH), str(_VOICES_PATH))))
                self._kokoro = _SHARED_KOKORO[0]
                logger.info("Kokoro TTS model loaded successfully")
            except Exception:
                logger.warning("Failed to load Kokoro TTS model", exc_info=True)
                return None

        return self._kokoro

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def speed(self) -> float:
        return self._speed

    # ── Status checks ────────────────────────────────────────────────────

    def is_installed(self) -> bool:
        """True if the Kokoro model and voices file are both present."""
        return _MODEL_PATH.exists() and _VOICES_PATH.exists()

    # ── Synthesis ────────────────────────────────────────────────────────

    def synthesize_wav_bytes(self, text: str, *, voice: str | None = None, speed: float | None = None) -> bytes:
        """Generate local Kokoro audio without using a physical device: every sentence, without markdown.

        *voice* and *speed* override the saved ones for this call.
        """
        clean = clean_for_speech(text)
        if not clean.strip():
            return b""
        samples, sample_rate = self._synthesize_samples(clean, voice=voice, speed=speed)
        pcm = (
            np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
            * 32767.0
        ).astype("<i2")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1 if pcm.ndim == 1 else int(pcm.shape[1]))
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate))
            wav.writeframes(pcm.tobytes())
        return output.getvalue()

    # ── Download helpers ─────────────────────────────────────────────────

    def download_model(
        self, progress: Callable[[float], None] | None = None
    ) -> None:
        """Download the Kokoro ONNX model and voices file."""
        _KOKORO_DIR.mkdir(parents=True, exist_ok=True)

        logger.info("Downloading Kokoro TTS model from %s", _MODEL_URL)
        _download_file(_MODEL_URL, _MODEL_PATH, progress, size=_MODEL_BYTES, sha256=_MODEL_SHA256)

        logger.info("Downloading Kokoro voices from %s", _VOICES_URL)
        _download_file(_VOICES_URL, _VOICES_PATH, None, size=_VOICES_BYTES, sha256=_VOICES_SHA256)

    # ── Internal ─────────────────────────────────────────────────────────

    def _synthesize_samples(self, text: str, *, voice: str | None = None,
                            speed: float | None = None) -> tuple[np.ndarray, int]:
        kokoro = self._get_kokoro()
        if kokoro is None:
            raise RuntimeError("Kokoro model is not installed")
        chosen = voice if voice in VOICE_CATALOG else self._voice
        samples, sample_rate = kokoro.create(
            text,
            voice=chosen,
            speed=float(speed) if speed else self._speed,
            lang=_voice_lang(chosen),
        )
        return np.asarray(samples), int(sample_rate)

    def reload_settings(self) -> None:
        """Adopt the voice and speed saved in Voice settings.

        Settings writes ``tts_settings.json`` directly, so a long-lived service
        re-reads it rather than keeping the values it started with.
        """
        self._load_settings()

    def _load_settings(self) -> None:
        try:
            if _SETTINGS_PATH.exists():
                data = json.loads(_SETTINGS_PATH.read_text())
                saved_voice = data.get("voice", _DEFAULT_VOICE)
                # Migrate from Piper voice IDs (e.g. "en_US-lessac-medium")
                # to Kokoro voice IDs — fall back to default if not in catalog
                if saved_voice in VOICE_CATALOG:
                    self._voice = saved_voice
                else:
                    self._voice = _DEFAULT_VOICE
                self._speed = data.get("speed", 1.0)
        except Exception:
            logger.warning("Failed to load TTS settings from %s", _SETTINGS_PATH, exc_info=True)


# ═════════════════════════════════════════════════════════════════════════════
# Module-level helpers
# ═════════════════════════════════════════════════════════════════════════════

def _voice_lang(voice_id: str) -> str:
    """Infer the ``lang`` parameter from a Kokoro voice ID prefix.

    Kokoro voice IDs use a two-letter prefix:
      a* = American English, b* = British English,
      j* = Japanese, z* = Mandarin Chinese, etc.
    """
    prefix = voice_id[:1] if voice_id else "a"
    return {
        "a": "en-us",
        "b": "en-gb",
        "j": "ja",
        "z": "cmn",
        "e": "es",
        "f": "fr-fr",
        "h": "hi",
        "i": "it",
        "p": "pt-br",
    }.get(prefix, "en-us")


def _download_file(
    url: str,
    dest: Path,
    progress: Callable[[float], None] | None = None,
    *,
    size: int | None = None,
    sha256: str | None = None,
) -> None:
    """Download a file with optional progress callback (0.0 → 1.0); kept only when its size and SHA-256 match."""
    import hashlib

    import requests

    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0)) or int(size or 0)
    downloaded = 0
    digest = hashlib.sha256()
    partial = dest.with_name(dest.name + ".part")
    try:
        with open(partial, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65_536):
                f.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                if progress and total:
                    progress(min(downloaded / total, 1.0))
        if (size is not None and downloaded != size) or (sha256 and digest.hexdigest() != sha256):
            raise RuntimeError(f"{dest.name} didn't match the fingerprint saved in Row-Bot, so it wasn't kept.")
        os.replace(partial, dest)
    finally:
        partial.unlink(missing_ok=True)


def get_voice_catalog() -> dict[str, str]:
    """Return ``{voice_id: display_name}`` for all available voices."""
    return dict(VOICE_CATALOG)
