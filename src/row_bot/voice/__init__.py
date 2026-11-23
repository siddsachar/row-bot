"""Local speech-to-text: the Whisper and SenseVoice models that browser voice and transcripts use.

Nothing here opens a microphone or a speaker: the browser records, and the
server transcribes the bytes it is sent.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np

from row_bot.data_paths import get_row_bot_data_dir

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
SAMPLE_RATE = 16_000        # 16 kHz — required by Whisper

# Whisper
_DEFAULT_WHISPER_SIZE = "small"
_WHISPER_SIZES = frozenset({"tiny", "base", "small", "medium"})

# Data directory
_DATA_DIR = get_row_bot_data_dir()
_VOICE_SETTINGS_FILE = _DATA_DIR / "voice_settings.json"
_WHISPER_CACHE_DIR = Path(
    os.environ.get("ROW_BOT_WHISPER_CACHE_DIR")
    or (_DATA_DIR / "cache" / "whisper")
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_voice_settings() -> dict:
    import json
    if _VOICE_SETTINGS_FILE.exists():
        try:
            return json.loads(_VOICE_SETTINGS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_voice_settings(settings: dict) -> None:
    import json
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _VOICE_SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


# ── Voice Service ────────────────────────────────────────────────────────────

class VoiceService:
    """The local speech-to-text models: the chosen size, explicit installs, and transcription of PCM bytes."""

    def __init__(self) -> None:
        # Whisper (lazy loaded)
        self._whisper_model = None
        self._funasr_provider = None
        self._models_lock = threading.Lock()

        # Settings
        settings = _load_voice_settings()
        self._whisper_size: str = settings.get("whisper_model", _DEFAULT_WHISPER_SIZE)
        self._sensevoice_model_path: str = str(
            settings.get("sensevoice_model_path") or ""
        )

    # ── Settings ─────────────────────────────────────────────────────────

    @property
    def whisper_size(self) -> str:
        return self._whisper_size

    @whisper_size.setter
    def whisper_size(self, value: str) -> None:
        self._whisper_size = value
        self._whisper_model = None
        s = _load_voice_settings(); s["whisper_model"] = value; _save_voice_settings(s)

    @property
    def sensevoice_model_path(self) -> str:
        return self._sensevoice_model_path

    # ── Model loading ────────────────────────────────────────────────────

    def _ensure_whisper(self, *, allow_download: bool = True):
        if self._whisper_model is not None:
            return self._whisper_model
        from row_bot import onnxruntime_telemetry

        # Before faster-whisper's voice filter (Silero VAD) opens an ONNX Runtime session.
        onnxruntime_telemetry.switch_off()
        from faster_whisper import WhisperModel

        _WHISPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._whisper_model = WhisperModel(
            self._whisper_size,
            device="cpu",
            compute_type="int8",
            download_root=str(_WHISPER_CACHE_DIR),
            local_files_only=not allow_download,
        )
        logger.info("Loaded Whisper model: %s (cpu/int8)", self._whisper_size)
        return self._whisper_model

    def whisper_model_available(self) -> bool:
        """Return whether the configured model is already cached locally."""
        if self._whisper_model is not None:
            return True
        model_name = f"models--Systran--faster-whisper-{self._whisper_size}"
        model_dir = _WHISPER_CACHE_DIR / model_name
        return model_dir.is_dir() and any(model_dir.iterdir())

    def install_whisper_model(self) -> None:
        """Explicitly download and load the selected Whisper model."""
        self._ensure_whisper(allow_download=True)

    def sensevoice_status(self):
        from row_bot.voice.local_provider import LocalFunASRProvider

        return LocalFunASRProvider(
            model_path=self._sensevoice_model_path,
        ).runtime_status()

    def install_sensevoice_model(self) -> Path:
        """Explicitly download SenseVoice and persist its verified local path."""
        from row_bot.voice.local_provider import install_sensevoice_model

        model_path = install_sensevoice_model()
        settings = _load_voice_settings()
        settings["sensevoice_model_path"] = str(model_path)
        _save_voice_settings(settings)
        self._sensevoice_model_path = str(model_path)
        self._funasr_provider = None
        return model_path

    def sync_saved_models(self) -> None:
        """Adopt the Whisper size and SenseVoice path saved in Voice settings.

        Settings writes ``voice_settings.json`` directly and installs SenseVoice
        with its own service, so browser voice re-reads the saved choice before
        each utterance instead of keeping the values this service started with.
        """
        settings = _load_voice_settings()
        size = str(settings.get("whisper_model") or _DEFAULT_WHISPER_SIZE)
        path = str(settings.get("sensevoice_model_path") or "")
        with self._models_lock:
            if size in _WHISPER_SIZES and size != self._whisper_size:
                self._whisper_size = size
                self._whisper_model = None
            if path != self._sensevoice_model_path:
                self._sensevoice_model_path = path
                self._funasr_provider = None

    def sensevoice_provider(self, *, language: str | None):
        """The SenseVoice provider for the saved model path, in *language* (None detects it)."""
        with self._models_lock:
            provider = self._get_funasr_provider()
            provider.language = language
            return provider

    def _get_funasr_provider(self):
        if self._funasr_provider is None:
            from row_bot.voice.local_provider import LocalFunASRProvider

            self._funasr_provider = LocalFunASRProvider(
                model_path=self._sensevoice_model_path,
            )
        return self._funasr_provider

    def transcribe_pcm16(
        self,
        audio_bytes: bytes,
        *,
        allow_download: bool = False,
        language: str | None,
    ) -> str:
        """Transcribe 16 kHz mono signed-16 PCM in *language*; None detects it."""
        return self.transcribe_pcm16_heard(audio_bytes, allow_download=allow_download, language=language)[0]

    def transcribe_pcm16_heard(
        self,
        audio_bytes: bytes,
        *,
        allow_download: bool = False,
        language: str | None,
    ) -> tuple[str, str | None]:
        """The words in 16 kHz mono signed-16 PCM and the language they are in: *language*, or with None
        the one Whisper detects. When detection can't tell (under half sure, or under 1.5 s of audio) it
        keeps the last language it heard clearly (else the interface's), on the same model."""
        from row_bot.voice import languages

        # Keep this call's model: a saved size change may drop the shared one.
        model = self._ensure_whisper(allow_download=allow_download)
        f32 = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        sure = False
        if language is None and languages.too_short(audio_bytes):
            language = languages.kept_language()
        segs, info = model.transcribe(f32, beam_size=5, language=language, vad_filter=True)
        if language is None:
            # Detection happens before any decoding: an unsure guess is dropped and the audio read again.
            heard = str(getattr(info, "language", "") or "")
            sure = bool(heard) and float(getattr(info, "language_probability", 0.0)) >= languages.SURE_PROBABILITY
            language = heard if sure else languages.kept_language()
            if not sure:
                segs, info = model.transcribe(f32, beam_size=5, language=language, vad_filter=True)
        text = " ".join(s.text.strip() for s in segs).strip()
        if sure and text:
            languages.note_heard(language)
        return text, language


    def transcribe_words(self, audio_bytes: bytes) -> tuple[list, str]:
        """16 kHz mono signed-16 PCM as stretches of speech with each word's time, and the language heard
        (a recording or a clip for video work, 5.3.1 B). The model must be installed: nothing downloads here."""
        from row_bot.sound import Segment

        model = self._ensure_whisper(allow_download=False)
        f32 = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        segments, info = model.transcribe(f32, beam_size=5, vad_filter=True, word_timestamps=True)
        found = [Segment(float(s.start), float(s.end), s.text.strip(),
                         tuple((float(w.start), float(w.end), w.word.strip()) for w in (s.words or ())))
                 for s in segments if s.text.strip()]
        return found, str(getattr(info, "language", "") or "")


# ── Singleton ────────────────────────────────────────────────────────────────
_instance: VoiceService | None = None
_instance_lock = threading.Lock()


def get_voice_service() -> VoiceService:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = VoiceService()
        return _instance

