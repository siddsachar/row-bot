"""Row-Bot Voice's streaming speech models (VD3): the pinned catalog, the pick for this computer, and
the consented download with a fake network. No real model, no network, no real profile."""

from __future__ import annotations

import hashlib
import io
import random
import tarfile
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import pytest

from row_bot.local_runtime import builds
from row_bot.local_runtime.fetch import HOSTS, Fetcher
from row_bot.local_runtime.hardware import APPLE, GIB, NVIDIA, Gpu, classify
from row_bot.voice import speech_models
from row_bot.voice.speech_models import CATALOG, MOONSHINE_BASE_EN, NEMOTRON_STREAMING_EN, Installer

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


# ── The catalog ─────────────────────────────────────────────────────────────


def test_every_model_is_pinned_to_an_official_archive_with_its_size_and_fingerprint():
    assert len({model.id for model in CATALOG}) == len(CATALOG)
    assert len({model.engine_id for model in CATALOG}) == len(CATALOG)
    for model in CATALOG:
        pinned = model.pinned
        host = urlparse(pinned.url).hostname
        assert urlparse(pinned.url).scheme == "https" and host == "github.com" and host in HOSTS
        assert pinned.url.startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/")
        assert pinned.size == model.archive_size > 1_000_000 and len(pinned.sha256) == 64
        assert all(size > 0 for _name, size in model.files) and model.installed_bytes > model.archive_size / 2
        assert model.licence and urlparse(model.licence_url).scheme == "https"
        assert urlparse(model.source).scheme == "https"
        # The recognizer finds its files by role.
        if model.family == "transducer":
            assert {model.file(role) for role in ("encoder", "decoder", "joiner", "tokens.txt")}
        else:
            assert {model.file(role) for role in ("encoder", "decoder", "tokens.txt")}
    assert speech_models.by_engine(MOONSHINE_BASE_EN.engine_id) is MOONSHINE_BASE_EN
    assert speech_models.by_engine(speech_models.WHISPER_ENGINE) is None


# ── The pick for this computer ──────────────────────────────────────────────


RTX = Gpu(NVIDIA, "NVIDIA GeForce RTX 4070", 12 * GIB, "581.57")


@pytest.mark.parametrize(("hardware", "gpu_runtime", "pick", "reason"), [
    (classify(builds.WINDOWS_X64, 32 * GIB, [RTX]), True, NEMOTRON_STREAMING_EN, "gpu"),
    (classify(builds.WINDOWS_X64, 32 * GIB, [RTX]), False, MOONSHINE_BASE_EN, "no_gpu_runtime"),
    # A driver too old for CUDA: the card can't run it, whatever the runtime.
    (classify(builds.WINDOWS_X64, 32 * GIB, [replace(RTX, driver="470.10")]), True, MOONSHINE_BASE_EN, "cpu"),
    (classify(builds.WINDOWS_X64, 16 * GIB), True, MOONSHINE_BASE_EN, "cpu"),
    (classify(builds.MACOS_ARM64, 16 * GIB, [Gpu(APPLE, "Apple M3", 16 * GIB)], apple_silicon=True), True,
     MOONSHINE_BASE_EN, "mac"),
    (classify(None, 8 * GIB), False, MOONSHINE_BASE_EN, "cpu"),
])
def test_the_pick_follows_the_computer(hardware, gpu_runtime, pick, reason):
    assert speech_models.recommend(hardware, gpu_runtime=gpu_runtime) == (pick, reason)


# ── The download ────────────────────────────────────────────────────────────


def _archive(top: str, files: dict[str, bytes], extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as bundle:
        for name, data in {**{f"{top}/{key}": value for key, value in files.items()}, **(extra or {})}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _model(archive: bytes, files: dict[str, bytes]) -> speech_models.SpeechModel:
    return replace(MOONSHINE_BASE_EN, id="moonshine-test", archive="moonshine-test.tar.bz2",
                   archive_size=len(archive), archive_sha256=hashlib.sha256(archive).hexdigest(),
                   top="moonshine-test", files=tuple((name, len(data)) for name, data in files.items()))


FILES = {"encoder_model.ort": b"e" * 3000, "decoder_model_merged.ort": b"d" * 5000, "tokens.txt": b"t 1\n"}


class Network:
    """A fake opener serving one archive (or wrong bytes), recording every request."""

    def __init__(self, body: bytes, *, gate: threading.Event | None = None) -> None:
        self.body = body
        self.asked: list[tuple[str, dict]] = []
        self.gate = gate
        self.reached = threading.Event()

    @contextmanager
    def __call__(self, url, headers):
        self.asked.append((url, dict(headers)))
        start = int(headers.get("Range", "bytes=0-")[6:].rstrip("-") or 0)
        body, gate, reached = self.body, self.gate, self.reached

        class Response:
            status_code = 206 if start else 200
            headers = {"content-range": f"bytes {start}-{len(body) - 1}/{len(body)}"} if start else {}

            def iter_bytes(self, chunk_size=None):
                for offset in range(start, len(body), 1024):
                    if gate is not None and offset - start >= 2048:
                        reached.set()
                        gate.wait(0.3)  # a slow network: Stop lands while this block is on its way
                    yield body[offset:offset + 1024]

        yield Response()


def _installer(root: Path, network: Network, **kwargs) -> Installer:
    return Installer(root, fetcher=Fetcher(opener=network, retry_wait=0), free_bytes=lambda _path: 10 * GIB, **kwargs)


def test_nothing_downloads_without_the_size_the_person_agreed_to(tmp_path):
    archive = _archive("moonshine-test", FILES)
    model = _model(archive, FILES)
    network = Network(archive)
    worker = _installer(tmp_path, network)

    with pytest.raises(speech_models.InstallError) as refused:
        worker.start(model, expected_bytes=1)
    assert refused.value.code == "size_changed" and refused.value.facts == {"download_bytes": len(archive)}
    assert network.asked == [] and not speech_models.models_root(tmp_path).exists()


def test_a_download_is_checked_unpacked_and_recorded_then_chosen(tmp_path):
    archive = _archive("moonshine-test", FILES, extra={"moonshine-test/test_wavs/0.wav": b"x" * 10,
                                                        "../escaped.txt": b"never"})
    model = _model(archive, FILES)
    chosen = []
    worker = _installer(tmp_path, Network(archive), on_ready=chosen.append)

    worker.start(model, expected_bytes=speech_models.remaining_bytes(model, tmp_path))
    worker.wait(10)

    assert worker.progress.phase == "finished" and worker.progress.done == len(archive)
    assert speech_models.installed(model, tmp_path) and chosen == [model]
    folder = speech_models.model_dir(model, tmp_path)
    assert sorted(path.name for path in folder.iterdir()) == sorted([*FILES, "model.json"])
    assert not (tmp_path / "escaped.txt").exists() and not list(tmp_path.rglob("escaped.txt"))
    # The archive is gone once unpacked; the model takes only its own files.
    assert not any(speech_models.downloads_dir(tmp_path).glob("*"))
    assert speech_models.remaining_bytes(model, tmp_path) == 0


def test_an_archive_that_does_not_match_its_fingerprint_is_deleted_and_never_used(tmp_path):
    archive = _archive("moonshine-test", FILES)
    model = _model(archive, FILES)
    tampered = bytearray(archive)
    tampered[-10] ^= 0xFF
    worker = _installer(tmp_path, Network(bytes(tampered)))

    worker.start(model, expected_bytes=len(archive))
    worker.wait(10)

    assert (worker.progress.phase, worker.progress.reason) == ("failed", "mismatch")
    assert not speech_models.installed(model, tmp_path)
    assert not speech_models.model_dir(model, tmp_path).exists()
    assert not any(speech_models.downloads_dir(tmp_path).glob("*"))


def test_an_archive_missing_a_model_file_installs_nothing(tmp_path):
    partial = {key: value for key, value in FILES.items() if key != "tokens.txt"}
    archive = _archive("moonshine-test", partial)
    model = replace(_model(archive, partial), files=tuple((name, len(data)) for name, data in FILES.items()))
    worker = _installer(tmp_path, Network(archive))

    worker.start(model, expected_bytes=len(archive))
    worker.wait(10)

    assert (worker.progress.phase, worker.progress.reason) == ("failed", "unpack")
    assert not speech_models.model_dir(model, tmp_path).exists()
    assert not list(speech_models.models_root(tmp_path).glob(".staging-*"))


def test_stop_keeps_what_arrived_and_the_next_download_resumes_it(tmp_path):
    files = {**FILES, "decoder_model_merged.ort": random.Random(7).randbytes(20_000)}  # does not compress
    archive = _archive("moonshine-test", files)
    model = _model(archive, files)
    gate = threading.Event()
    network = Network(archive, gate=gate)
    worker = _installer(tmp_path, network)

    worker.start(model, expected_bytes=len(archive))
    assert network.reached.wait(5)
    assert worker.stop()  # the Stop button, pressed while the next block is still on its way
    assert worker.progress.phase == "stopped"
    left = speech_models.remaining_bytes(model, tmp_path)
    assert 0 < left < len(archive)
    assert not speech_models.installed(model, tmp_path)

    network.gate = None
    worker.start(model, expected_bytes=left)
    worker.wait(10)
    assert worker.progress.phase == "finished" and speech_models.installed(model, tmp_path)
    assert network.asked[-1][1].get("Range", "").startswith("bytes=")


def test_not_enough_disk_space_is_refused_before_anything_downloads(tmp_path):
    archive = _archive("moonshine-test", FILES)
    model = _model(archive, FILES)
    network = Network(archive)
    worker = Installer(tmp_path, fetcher=Fetcher(opener=network, retry_wait=0), free_bytes=lambda _path: 1000)

    with pytest.raises(speech_models.InstallError) as refused:
        worker.start(model, expected_bytes=len(archive))
    assert refused.value.code == "out_of_disk" and network.asked == []


def test_remove_deletes_the_model_and_its_partial_download(tmp_path):
    archive = _archive("moonshine-test", FILES)
    model = _model(archive, FILES)
    worker = _installer(tmp_path, Network(archive))
    worker.start(model, expected_bytes=len(archive))
    worker.wait(10)
    downloads = speech_models.downloads_dir(tmp_path)
    (downloads / f"{model.archive}.part").write_bytes(b"x" * 100)

    freed = speech_models.remove(model, tmp_path)

    assert freed >= sum(len(data) for data in FILES.values()) + 100
    assert not speech_models.model_dir(model, tmp_path).exists()
    assert not speech_models.installed(model, tmp_path)
    assert speech_models.remaining_bytes(model, tmp_path) == len(archive)
