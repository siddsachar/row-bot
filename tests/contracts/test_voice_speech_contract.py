"""The streaming speech seams Row-Bot Voice talks to (``voice/streaming_base.py``).

Any speech-to-text engine is a ``StreamingSpeechToText`` (push audio, partial
and final transcripts, a reserved language field); any voice is a
``SentenceSpeech`` (one sentence in, mono PCM out). Today's local engines are
adapted to them; these checks hold every adapter to the same contract.
"""
from __future__ import annotations

import io
import wave

import pytest

from row_bot.voice.engine.speech import (
    MAX_UTTERANCE_BYTES,
    PARTIAL_STEP_BYTES,
    ProviderSentenceSpeech,
    SegmentedSpeechToText,
)
from row_bot.voice.provider_base import SynthesizedSpeech, VoiceProviderError, VoiceProviderStatus
from row_bot.voice.streaming_base import SpeechAudio, TranscriptUpdate

pytestmark = pytest.mark.contract


class WholeUtteranceEngine:
    """A whole-utterance engine like faster-whisper: it says how much audio it heard."""

    provider_id = "fake_whole"
    display_name = "Fake"

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.calls: list[int] = []

    def status(self) -> VoiceProviderStatus:
        return VoiceProviderStatus(self.provider_id, self.display_name, self.ready,
                                   unavailable_code="whisper_model_missing")

    def transcribe_bytes(self, audio: bytes) -> str:
        self.calls.append(len(audio))
        return f" heard {len(audio)} bytes "


def _wav(pcm: bytes, rate: int = 24_000, channels: int = 1) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return out.getvalue()


class KokoroLikeVoice:
    provider_id = "fake_voice"
    display_name = "Fake voice"

    def __init__(self, ready: bool = True, channels: int = 1) -> None:
        self.ready = ready
        self.channels = channels
        self.said: list[str] = []

    def status(self) -> VoiceProviderStatus:
        return VoiceProviderStatus(self.provider_id, self.display_name, self.ready,
                                   unavailable_code="kokoro_model_missing")

    def synthesize(self, text: str, *, language: str | None = None) -> SynthesizedSpeech:
        self.said.append(text)
        return SynthesizedSpeech(_wav(b"\x01\x00" * self.channels * 480, channels=self.channels), "audio/wav")


def test_speech_to_text_streams_partials_then_one_final_transcript():
    engine = WholeUtteranceEngine()
    listen = SegmentedSpeechToText(engine)
    listen.warm()
    stream = listen.open(language=None)
    stream.push(b"\0\0" * 100)
    assert stream.partial() is None  # Too little new audio to be worth a decode.
    stream.push(b"\0" * PARTIAL_STEP_BYTES)
    partial = stream.partial()
    assert isinstance(partial, TranscriptUpdate) and not partial.final and partial.language is None
    assert stream.partial() is None
    final = stream.finish()
    assert final.final and final.text == f"heard {200 + PARTIAL_STEP_BYTES} bytes"
    # A finished stream keeps no audio and takes no more.
    stream.push(b"\0\0" * 100)
    stream.close()
    assert stream.partial() is None


def test_speech_to_text_bounds_an_utterance_and_refuses_to_warm_without_its_model():
    stream = SegmentedSpeechToText(WholeUtteranceEngine()).open()
    stream.push(b"\0" * (MAX_UTTERANCE_BYTES + 10))
    assert stream.finish().text == f"heard {MAX_UTTERANCE_BYTES} bytes"
    with pytest.raises(VoiceProviderError) as missing:
        SegmentedSpeechToText(WholeUtteranceEngine(ready=False)).warm()
    assert missing.value.code == "whisper_model_missing"


@pytest.mark.parametrize("channels", [1, 2])
def test_sentence_speech_gives_mono_pcm_with_its_rate_through_the_selected_voice(channels):
    voice = KokoroLikeVoice(channels=channels)
    speak = ProviderSentenceSpeech(voice)
    speak.warm()
    audio = speak.synthesize("It is noon.")
    assert isinstance(audio, SpeechAudio) and audio.sample_rate == 24_000
    assert audio.pcm16 == b"\x01\x00" * 480
    assert voice.said == ["Hi.", "It is noon."]
    assert speak.synthesize("   ").pcm16 == b""
    with pytest.raises(VoiceProviderError) as missing:
        ProviderSentenceSpeech(KokoroLikeVoice(ready=False)).warm()
    assert missing.value.code == "kokoro_model_missing"
