"""The model server's files (L2x, O75): pinned for every computer the engine trains on, downloaded with
it (checked against their SHA-256, resumable), unpacked and checked to start before they count as
installed, and removed with the engine. A package index on loopback stands in for GitHub."""

from __future__ import annotations

import dataclasses
import io
import json
import re
import zipfile

import pytest

from row_bot.learning.training import engine, fetch, lockfile, server_files
from tests.fixtures.learning_engine import FakeIndex, fake_llama_server, loopback_opener, server_version_runner

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def index(monkeypatch):
    with FakeIndex() as found:
        server_files.forget_status()
        yield found
        server_files.forget_status()


def _installer(root, index, *, runner=None, **options) -> server_files.ServerInstaller:
    return server_files.ServerInstaller("cu130", root=root, fetcher=fetch.Fetcher(opener=loopback_opener(index),
                                                                                  retry_wait=0),
                                        runner=runner or server_version_runner(), **options)


@pytest.mark.parametrize(("platform", "variant", "cuda"), [
    ("win_amd64", "cu130", "13.4"), ("win_amd64", "cu128", "12.4"),
    ("manylinux_2_28_x86_64", "cu130", "13.4"), ("manylinux_2_28_x86_64", "cu128", "12.8"),
])
def test_the_server_is_pinned_for_every_computer_the_engine_trains_on(platform, variant, cuda):
    build, runtime = server_files.plan(variant, [platform])

    for item in (build, runtime):
        assert item.url.startswith(f"https://github.com/ggml-org/llama.cpp/releases/download/{lockfile.LLAMA_CPP_RELEASE}/")
        assert re.fullmatch(r"[0-9a-f]{64}", item.sha256) and item.known_size and item.known_size > 10_000_000
        assert cuda in item.filename and item.filename == item.url.rsplit("/", 1)[-1]
    assert build.filename.startswith(f"llama-{lockfile.LLAMA_CPP_RELEASE}-bin-") and "cudart" in runtime.filename


def test_it_shows_its_size_about_0_6_gb_on_windows():
    assert server_files.download_bytes("cu130", ["win_amd64"]) == 154_582_227 + 423_535_356


@pytest.mark.parametrize("platform", ["macosx_14_0_arm64", "manylinux_2_28_aarch64", "win_arm64"])
def test_no_build_means_the_server_is_unavailable_there(tmp_path, platform):
    assert server_files.plan("cu130", [platform]) == ()
    assert server_files.status(tmp_path, variant="cu130", platforms=[platform]).state == "unavailable"
    assert server_files.plan(None) == ()  # a computer that can't train


def test_an_install_downloads_checks_it_starts_and_publishes_it_last(tmp_path, index, monkeypatch):
    fake_llama_server(index, monkeypatch)
    runner = server_version_runner()
    seen: list[tuple[int, int]] = []

    found = _installer(tmp_path, index, runner=runner, on_progress=lambda done, total: seen.append((done, total))
                       ).install()

    assert found.state == "installed" and found.executable is not None and found.executable.is_file()
    assert found.executable.name in ("llama-server.exe", "llama-server")
    assert (found.executable.parent / "cudart64_13.dll").is_file()  # the CUDA runtime beside the server
    assert runner.calls and runner.calls[0][1:] == ["--version"]
    assert not server_files.downloads_dir(tmp_path).exists()  # the archives go once unpacked
    installed = json.loads((server_files.server_dir(tmp_path) / "installed.json").read_text(encoding="utf-8"))
    assert installed["variant"] == "cu130" and installed["release"] == lockfile.LLAMA_CPP_RELEASE
    total = sum(item.known_size for item in server_files.plan("cu130"))
    assert seen[-1] == (total, total) and all(done <= total for done, _ in seen)
    assert server_files.status(tmp_path, variant="cu130", refresh=True).installed


def test_a_stop_part_way_keeps_what_arrived_and_the_next_attempt_downloads_only_the_rest(tmp_path, index,
                                                                                         monkeypatch):
    build, runtime = fake_llama_server(index, monkeypatch)
    calls = {"count": 0}

    def stop_after_first_file() -> None:
        if (server_files.downloads_dir(tmp_path) / build.filename).is_file():
            calls["count"] += 1
            raise fetch.Stopped()

    with pytest.raises(fetch.Stopped):
        _installer(tmp_path, index).install(stop_after_first_file)

    assert server_files.status(tmp_path, variant="cu130", refresh=True).state == "not_installed"
    assert _installer(tmp_path, index).downloaded() == build.size
    index.requests.clear()
    assert _installer(tmp_path, index).install().installed
    assert [item["path"].rsplit("/", 1)[-1] for item in index.requests] == [runtime.filename]


def test_a_file_that_doesnt_match_its_pin_is_refused_and_nothing_is_installed(tmp_path, index, monkeypatch):
    build, _runtime = fake_llama_server(index, monkeypatch)
    index.files[build.url.removeprefix("https://github.com")] = b"x" * build.size  # same size, other bytes

    with pytest.raises(fetch.FetchError) as raised:
        _installer(tmp_path, index).install()

    assert raised.value.code == "mismatch"
    assert server_files.status(tmp_path, variant="cu130", refresh=True).state == "not_installed"


def test_a_server_that_doesnt_start_here_isnt_installed(tmp_path, index, monkeypatch):
    fake_llama_server(index, monkeypatch)

    with pytest.raises(engine.EngineError) as raised:
        _installer(tmp_path, index, runner=server_version_runner(code=1, text="")).install()

    assert raised.value.code == "check_failed"
    base = server_files.server_dir(tmp_path)
    assert not (base / "installed.json").exists() and not list(base.glob(".staging-*"))


def test_missing_files_read_as_damaged_and_another_release_as_outdated(tmp_path, index, monkeypatch):
    fake_llama_server(index, monkeypatch)
    found = _installer(tmp_path, index).install()

    for name in server_files.EXECUTABLES:
        (found.executable.parent / name).unlink(missing_ok=True)
    assert server_files.status(tmp_path, variant="cu130", refresh=True).state == "damaged"
    found.executable.write_bytes(b"server")
    # An app update pins another build (the same names, new fingerprints): it downloads again.
    monkeypatch.setattr(lockfile, "LLAMA_CPP_SERVER", {
        key: (dataclasses.replace(build, sha256="0" * 64), runtime)
        for key, (build, runtime) in lockfile.LLAMA_CPP_SERVER.items()})
    assert server_files.status(tmp_path, variant="cu130", refresh=True).state == "outdated"


def test_remove_deletes_it_and_says_how_much_it_freed(tmp_path, index, monkeypatch):
    fake_llama_server(index, monkeypatch)
    found = _installer(tmp_path, index).install()

    freed = server_files.remove(tmp_path)

    assert freed >= found.bytes > 0
    assert not server_files.server_dir(tmp_path).exists()
    assert server_files.status(tmp_path, variant="cu130", refresh=True).state == "not_installed"


def test_an_archive_cant_write_outside_the_servers_folder(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../outside.dll", b"no")
        archive.writestr("C:/Windows/evil.dll", b"no")
        archive.writestr("llama-server.exe", b"server")
    path = tmp_path / "build.zip"
    path.write_bytes(buffer.getvalue())

    server_files.unpack([path], tmp_path / "server" / "bin")

    assert (tmp_path / "server" / "bin" / "llama-server.exe").is_file()
    assert not (tmp_path / "server" / "outside.dll").exists() and not (tmp_path / "outside.dll").exists()
