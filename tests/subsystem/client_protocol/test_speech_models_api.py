"""Settings › Voice's streaming speech models through the client API: the pick, Download, Stop, Remove.

The computer is a fake (``hardware.detector``) and the network a fake opener that records every
request: nothing is asked for before the person presses Download, and nothing reaches the network.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from contextlib import contextmanager
from dataclasses import replace

import pytest

from row_bot.application import client_speech_models
from row_bot.local_runtime import builds, hardware
from row_bot.local_runtime.fetch import Fetcher
from row_bot.local_runtime.hardware import GIB, NVIDIA, Gpu, classify
from row_bot.voice import speech_models
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

FILES = {"encoder_model.ort": b"e" * 3000, "decoder_model_merged.ort": b"d" * 4000, "tokens.txt": b"t 1\n"}


def _archive(top: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as bundle:
        for name, data in FILES.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture
def computer(tmp_path, monkeypatch):
    """A Windows computer with an NVIDIA card, a scratch data folder, a small pinned model in place
    of the real archives and a recording network."""

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(hardware, "detector", lambda: classify(
        builds.WINDOWS_X64, 32 * GIB, [Gpu(NVIDIA, "NVIDIA GeForce RTX 4070", 12 * GIB, "581.57")]))
    hardware.forget()
    archive = _archive("small")
    small = replace(speech_models.MOONSHINE_BASE_EN, archive="small.tar.bz2", archive_size=len(archive),
                    archive_sha256=hashlib.sha256(archive).hexdigest(), top="small",
                    files=tuple((name, len(data)) for name, data in FILES.items()))
    # The speech runtime (sherpa-onnx, in the voice extra) is installed and processor-only, whatever this
    # checkout has: whether it imports here must not decide the result.
    monkeypatch.setattr(speech_models, "runtime_available", lambda: True)
    monkeypatch.setattr(speech_models, "cuda_runtime", lambda: False)
    monkeypatch.setattr(speech_models, "CATALOG", (small, speech_models.NEMOTRON_STREAMING_EN))
    monkeypatch.setattr(speech_models, "_BY_ID", {model.id: model for model in speech_models.CATALOG})
    monkeypatch.setattr(client_speech_models, "_failure", None)
    monkeypatch.setattr(client_speech_models, "_installers", {})
    asked: list[str] = []

    @contextmanager
    def network(url, headers):
        asked.append(url)

        class Response:
            status_code = 200
            headers = {}

            def iter_bytes(self, chunk_size=None):
                yield archive

        yield Response()

    monkeypatch.setattr(speech_models, "Fetcher", lambda: Fetcher(opener=network, retry_wait=0))
    yield {"asked": asked, "small": small, "archive": archive, "root": tmp_path}
    client_speech_models.installer().stop()
    hardware.forget()


def _talk_model(root) -> str:
    path = root / "voice_runtime_settings.json"
    return json.loads(path.read_text(encoding="utf-8")).get("talk_model") if path.exists() else "local-whisper"


def test_the_state_names_the_pick_for_this_computer_and_asks_for_nothing(computer):
    view = client_speech_models.snapshot()

    assert view["state"] == "not_installed"
    # The locked sherpa-onnx build is processor-only, so an NVIDIA card still gets Moonshine.
    assert (view["recommended"], view["reason"]) == ("moonshine-base-en", "no_gpu_runtime")
    pick = next(row for row in view["models"] if row["id"] == "moonshine-base-en")
    assert pick["download_bytes"] == len(computer["archive"]) and not pick["installed"]
    assert view["talk_engine"] == "local-whisper" and view["download"] is None
    assert computer["asked"] == [] and not speech_models.models_root().exists()


def test_without_the_speech_runtime_the_state_says_so_and_nothing_downloads(computer, monkeypatch):
    monkeypatch.setattr(speech_models, "runtime_available", lambda: False)
    small = computer["small"]

    view = client_speech_models.snapshot()
    assert view["state"] == "unavailable"
    # The pick and sizes still show; only Download is refused, with a reason the card puts in words.
    assert view["recommended"] == small.id and view["download"] is None
    refused = client_speech_models.install(small.id, len(computer["archive"]))
    assert refused["state"] == "unavailable" and refused["failure"] == {"reason": "unavailable"}
    assert computer["asked"] == [] and not speech_models.models_root().exists()


def test_download_remove_and_talk_follow_the_person_through_the_api(computer):
    small = computer["small"]
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        view = client.get("/api/v1/voice/speech-models", headers=headers)
        assert view.status_code == 200, view.text
        refused = client.post("/api/v1/voice/speech-models/install", headers=headers,
                              json={"model_id": small.id, "download_bytes": 1})
        assert refused.status_code == 200
        assert refused.json()["failure"] == {"reason": "size_changed", "download_bytes": len(computer["archive"])}
        assert computer["asked"] == []
        unknown = client.post("/api/v1/voice/speech-models/install", headers=headers,
                              json={"model_id": "whisper-large", "download_bytes": 1})
        assert unknown.status_code >= 400

        started = client.post("/api/v1/voice/speech-models/install", headers=headers,
                              json={"model_id": small.id, "download_bytes": len(computer["archive"])})
        assert started.status_code == 200 and started.json()["failure"] is None
        client_speech_models.installer().wait(10)
        done = client.get("/api/v1/voice/speech-models", headers=headers).json()
        assert done["state"] == "installed" and done["download"]["phase"] == "finished"
        # The Download button said it listens with the model: Talk now does.
        assert done["talk_engine"] == small.engine_id == _talk_model(computer["root"])
        assert computer["asked"] == [small.pinned.url]

        removed = client.post("/api/v1/voice/speech-models/remove", headers=headers,
                              json={"model_id": small.id})
        assert removed.status_code == 200
        body = removed.json()
        assert body["freed_bytes"] >= sum(len(data) for data in FILES.values())
        assert body["state"] == "not_installed" and body["talk_engine"] == "local-whisper"
        assert _talk_model(computer["root"]) == "local-whisper"


def test_a_finished_download_leaves_realtime_talk_alone(computer):
    root = computer["root"]
    (root / "voice_runtime_settings.json").write_text(json.dumps(
        {"talk_provider": "openai_realtime", "talk_model": "gpt-realtime-2"}), encoding="utf-8")
    small = computer["small"]

    client_speech_models.install(small.id, len(computer["archive"]))
    client_speech_models.installer().wait(10)

    assert speech_models.installed(small) and _talk_model(root) == "gpt-realtime-2"


def test_talk_listens_with_a_downloaded_model_and_reports_one_that_is_gone(computer, monkeypatch):
    from row_bot.voice import providers
    from row_bot.voice.engine.sherpa_speech import LocalStreamingSpeechProvider
    from row_bot.voice.runtime import VoiceRuntimeSettings

    monkeypatch.setattr(speech_models, "_BY_ENGINE", {model.engine_id: model for model in speech_models.CATALOG})
    small = computer["small"]
    settings = VoiceRuntimeSettings(talk_model=small.engine_id)
    listen = providers.speech_to_text_provider("talk", voice_service=object(), settings=settings)

    assert isinstance(listen, LocalStreamingSpeechProvider)
    status = listen.status()
    assert not status.ready and status.unavailable_code == "speech_model_missing"
    client_speech_models.install(small.id, len(computer["archive"]))
    client_speech_models.installer().wait(10)
    assert listen.status().ready
