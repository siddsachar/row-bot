"""Pinned downloads that resume (L2b): the engine's wheels and the starting model's files come
through the same downloader. A loopback server stands in for PyPI, PyTorch and Hugging Face."""

from __future__ import annotations

import errno
import hashlib

import pytest

from row_bot.integrations import safe
from row_bot.learning.training import fetch
from tests.fixtures.learning_engine import FakeIndex, loopback_opener

pytestmark = pytest.mark.subsystem

DATA = bytes(range(256)) * 4096 * 4  # 4 MiB
SHA = hashlib.sha256(DATA).hexdigest()
URL = "https://files.pythonhosted.org/packages/big-1.0-py3-none-any.whl"
PATH = "/packages/big-1.0-py3-none-any.whl"


def _item(size: int | None = len(DATA), sha: str = SHA) -> fetch.Pinned:
    return fetch.Pinned(URL, "big-1.0-py3-none-any.whl", sha, size)


def _fetcher(index: FakeIndex) -> fetch.Fetcher:
    return fetch.Fetcher(opener=loopback_opener(index), retry_wait=0)


def test_a_whole_download_is_verified_then_put_in_place(tmp_path):
    with FakeIndex() as index:
        index.add(PATH, DATA)
        seen: list[int] = []

        path = _fetcher(index).fetch(_item(), tmp_path, progress=seen.append)

    assert path.read_bytes() == DATA and path.name == "big-1.0-py3-none-any.whl"
    assert not fetch.part_path(tmp_path, _item()).exists()
    assert seen[-1] == len(DATA) and seen == sorted(seen)


def test_a_dropped_connection_resumes_where_it_stopped(tmp_path):
    with FakeIndex() as index:
        index.add(PATH, DATA)
        index.cut_after[PATH] = 300_000

        path = _fetcher(index).fetch(_item(), tmp_path)
        ranges = index.ranged(PATH)

    assert path.read_bytes() == DATA
    # The second request asked only for the rest.
    assert ranges[0] is None and ranges[1] == "bytes=300000-"


def test_a_download_left_from_before_a_restart_resumes_from_its_part_file(tmp_path):
    fetch.part_path(tmp_path, _item()).write_bytes(DATA[:700_000])
    with FakeIndex() as index:
        index.add(PATH, DATA)
        seen: list[int] = []

        _fetcher(index).fetch(_item(), tmp_path, progress=seen.append)
        ranges = index.ranged(PATH)

    assert ranges == ["bytes=700000-"]
    assert seen[0] >= 700_000  # progress counts what was already there


def test_a_server_that_ignores_the_range_starts_the_file_again_cleanly(tmp_path):
    fetch.part_path(tmp_path, _item()).write_bytes(b"x" * 1000)  # not even the right bytes
    with FakeIndex() as index:
        index.add(PATH, DATA)
        index.ignore_range.add(PATH)

        path = _fetcher(index).fetch(_item(), tmp_path)

    assert path.read_bytes() == DATA


def test_a_file_that_doesnt_match_its_fingerprint_is_refused_and_deleted(tmp_path):
    with FakeIndex() as index:
        index.add(PATH, DATA)

        with pytest.raises(fetch.FetchError) as refused:
            _fetcher(index).fetch(_item(sha="0" * 64), tmp_path)

    assert refused.value.code == "mismatch"
    assert list(tmp_path.iterdir()) == []


def test_a_file_larger_than_its_pin_is_cut_off_and_refused(tmp_path):
    with FakeIndex() as index:
        index.add(PATH, DATA + b"more")

        with pytest.raises(fetch.FetchError) as refused:
            _fetcher(index).fetch(_item(), tmp_path)

    assert refused.value.code == "mismatch"
    assert not (tmp_path / _item().name).exists()


def test_stop_keeps_what_arrived_for_next_time(tmp_path):
    calls = {"count": 0}

    def check() -> None:
        calls["count"] += 1
        if calls["count"] > 1:
            raise fetch.Stopped()

    with FakeIndex() as index:
        index.add(PATH, DATA)
        with pytest.raises(fetch.Stopped):
            _fetcher(index).fetch(_item(), tmp_path, check=check)

    part = fetch.part_path(tmp_path, _item())
    assert part.is_file() and 0 < part.stat().st_size < len(DATA)
    assert fetch.on_disk(tmp_path, _item()) == part.stat().st_size


def test_redirects_like_hugging_face_to_its_cdn_are_followed(tmp_path):
    with FakeIndex() as index:
        index.add("/cdn/blob", DATA)
        index.redirects["/Qwen/Model/resolve/abc/model.safetensors"] = "/cdn/blob"
        item = fetch.Pinned("https://huggingface.co/Qwen/Model/resolve/abc/model.safetensors", "model.safetensors",
                            SHA, len(DATA))

        path = _fetcher(index).fetch(item, tmp_path)

    assert path.read_bytes() == DATA


def test_a_size_that_isnt_pinned_is_asked_for_with_one_byte(tmp_path):
    with FakeIndex() as index:
        index.add(PATH, DATA)

        size = _fetcher(index).size(_item(size=None))
        ranges = index.ranged(PATH)

    assert size == len(DATA) and ranges == ["bytes=0-0"]


def test_a_full_disk_is_reported_as_such(tmp_path, monkeypatch):
    real_open = open

    def full(path, mode="r", *args, **kwargs):
        if str(path).endswith(".part") and "b" in mode and ("a" in mode or "w" in mode):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", full)
    with FakeIndex() as index:
        index.add(PATH, DATA)
        with pytest.raises(fetch.FetchError) as full_disk:
            _fetcher(index).fetch(_item(), tmp_path)

    assert full_disk.value.code == "out_of_disk"


@pytest.mark.parametrize("url", [
    "http://files.pythonhosted.org/packages/x.whl",  # not https
    "https://example.com/packages/x.whl",  # not a reviewed host
    "https://user:secret@files.pythonhosted.org/x.whl",  # credentials in the address
    "https://localhost/x.whl",  # never this computer through the reviewed path
])
def test_the_safe_path_refuses_before_connecting(url):
    with pytest.raises(ValueError):
        with safe.open_stream(url, hosts=fetch.HOSTS | {"localhost"}):
            pass


def test_reviewed_hosts_include_the_cdn_subdomains_only():
    assert safe._reviewed("cas-bridge.xethub.hf.co", frozenset(fetch.HOSTS))  # noqa: SLF001
    assert safe._reviewed("huggingface.co", frozenset(fetch.HOSTS))  # noqa: SLF001
    assert not safe._reviewed("evilhf.co", frozenset(fetch.HOSTS))  # noqa: SLF001
    assert not safe._reviewed("huggingface.co.example.net", frozenset(fetch.HOSTS))  # noqa: SLF001
