"""Starting-model weights (L2b, D13): pinned files from Hugging Face, downloaded only when asked,
resumable, verified, published with their licence, and deleted on request. A loopback server
stands in for Hugging Face (its ``resolve`` address redirects to a CDN, as the real one does)."""

from __future__ import annotations

import hashlib
import json

import pytest

from row_bot.learning.training import fetch, weights
from tests.fixtures.learning_engine import FakeIndex, loopback_opener

pytestmark = pytest.mark.subsystem

LICENCE = b"Apache License\nVersion 2.0, January 2004\n"
WEIGHTS = bytes(range(256)) * 8192  # 2 MiB
CONFIG = b'{"architectures": ["Qwen3_5ForConditionalGeneration"]}'


def _model() -> weights.PinnedModel:
    files = tuple(weights.WeightFile(name, len(data), hashlib.sha256(data).hexdigest())
                  for name, data in (("LICENSE", LICENCE), ("config.json", CONFIG),
                                     ("model.safetensors", WEIGHTS)))
    return weights.PinnedModel("qwen3.5-2b", "Qwen3.5 2B", "Qwen/Qwen3.5-2B", "15852e8c" + "0" * 32, "Apache 2.0",
                               "apache-2.0", files)


@pytest.fixture
def hub():
    model = _model()
    with FakeIndex() as index:
        for item, data in zip(model.files, (LICENCE, CONFIG, WEIGHTS), strict=True):
            cdn = f"/cdn/{item.sha256}"
            index.add(cdn, data)
            index.redirects[f"/{model.repo}/resolve/{model.revision}/{item.name}"] = cdn
        yield index, model


def _fetcher(index) -> fetch.Fetcher:
    return fetch.Fetcher(opener=loopback_opener(index), retry_wait=0)


def test_the_pinned_starting_model_is_every_file_of_one_revision():
    model = weights.QWEN35_2B

    assert model.repo == "Qwen/Qwen3.5-2B" and model.revision == "15852e8c16360a2fea060d615a32b45270f8a8fc"
    assert model.licence == "Apache 2.0" and model.download_bytes == 4_571_272_453
    names = {item.name for item in model.files}
    assert {"LICENSE", "config.json", "tokenizer.json", "chat_template.jinja",
            "model.safetensors-00001-of-00001.safetensors"} <= names
    for item in model.pinned():
        assert item.url == f"https://huggingface.co/Qwen/Qwen3.5-2B/resolve/{model.revision}/{item.name}"
        assert item.size and len(item.sha256) == 64


def test_a_download_is_published_with_its_licence_only_when_every_file_matches(tmp_path, hub):
    index, model = hub
    seen: list[int] = []

    folder = weights.download(model, root=tmp_path, fetcher=_fetcher(index), progress=seen.append)

    assert folder == weights.model_dir(model, tmp_path)
    assert (folder / "LICENSE").read_bytes() == LICENCE
    manifest = json.loads((folder / "model.json").read_text(encoding="utf-8"))
    assert manifest["licence"] == "Apache 2.0" and manifest["licence_file"] == "LICENSE"
    assert manifest["source"] == "https://huggingface.co/Qwen/Qwen3.5-2B" and manifest["revision"] == model.revision
    assert weights.status(model, tmp_path).state == "downloaded"
    assert seen[-1] == model.download_bytes
    assert not weights.partial_dir(model, tmp_path).exists()


def test_an_interrupted_download_continues_from_what_arrived(tmp_path, hub):
    index, model = hub
    big = model.files[2]
    index.cut_after[f"/cdn/{big.sha256}"] = 700_000

    weights.download(model, root=tmp_path, fetcher=_fetcher(index))

    assert index.ranged(f"/cdn/{big.sha256}")[-1] == "bytes=700000-"


def test_a_download_stopped_part_way_reads_as_partial_and_resumes(tmp_path, hub):
    index, model = hub

    def stop() -> None:
        if fetch.on_disk(weights.partial_dir(model, tmp_path), model.pinned()[2]) > 0:
            raise fetch.Stopped()

    with pytest.raises(fetch.Stopped):
        weights.download(model, root=tmp_path, fetcher=_fetcher(index), check=stop)

    partial = weights.status(model, tmp_path)
    assert partial.state == "partial" and 0 < partial.bytes < model.download_bytes
    assert weights.downloaded_bytes(model, tmp_path) == partial.bytes

    weights.download(model, root=tmp_path, fetcher=_fetcher(index))
    assert weights.status(model, tmp_path).state == "downloaded"


def test_a_file_that_doesnt_match_its_pin_is_refused_and_nothing_is_published(tmp_path, hub):
    index, model = hub
    index.files[f"/cdn/{model.files[1].sha256}"] = b'{"architectures": ["SomethingElse"]}'

    with pytest.raises(fetch.FetchError) as refused:
        weights.download(model, root=tmp_path, fetcher=_fetcher(index))

    assert refused.value.code == "mismatch"
    assert not weights.model_dir(model, tmp_path).exists()
    assert weights.status(model, tmp_path).state != "downloaded"


def test_a_published_model_missing_a_file_reads_as_damaged(tmp_path, hub):
    index, model = hub
    folder = weights.download(model, root=tmp_path, fetcher=_fetcher(index))
    (folder / "config.json").unlink()

    assert weights.status(model, tmp_path).state == "damaged"


def test_delete_removes_the_model_and_a_partial_download_and_says_what_it_freed(tmp_path, hub):
    index, model = hub
    weights.download(model, root=tmp_path, fetcher=_fetcher(index))
    weights.partial_dir(model, tmp_path).mkdir(parents=True)
    (weights.partial_dir(model, tmp_path) / "config.json.part").write_bytes(b"x" * 10)

    freed = weights.delete(model, tmp_path)

    assert freed >= model.download_bytes + 10
    assert weights.status(model, tmp_path).state == "not_downloaded"
    assert not (tmp_path / "runtimes" / "learning").exists()


def test_nothing_is_downloaded_at_import_or_by_reading_the_status(tmp_path):
    assert weights.status(weights.QWEN35_2B, tmp_path).state == "not_downloaded"
    assert list(tmp_path.iterdir()) == []
