"""Run on this computer (E1): downloading, checking, publishing and removing the build and a model.

Every file comes from a loopback fake of the pinned hosts (``tests/fixtures/local_runtime.py``); the
build's ``llama-server`` is a Python stand-in run through this interpreter. Nothing reaches the
network or the real data folder.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from row_bot.local_runtime import fetch, install
from row_bot.local_runtime.fetch import Fetcher, FetchError, Pinned
from tests.fixtures.local_runtime import (FakeFiles, fake_build, fake_model, opener, python_command, sha256,
                                          tar_bytes, zip_bytes)

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

PLENTY = 10**15


def _installer(root: Path, files: FakeFiles, **extra) -> install.Installer:
    return install.Installer(root, fetcher=Fetcher(opener=opener(files), retry_wait=0.01),
                             command=python_command, free_bytes=lambda _path: PLENTY, **extra)


def _finish(worker: install.Installer) -> install.Progress:
    worker.wait(30)
    assert worker.progress is not None
    return worker.progress


def test_a_build_and_a_model_are_checked_and_published_with_their_records_written_last(tmp_path):
    ready = []
    with FakeFiles() as files:
        build, model = fake_build(files), fake_model(files)
        worker = _installer(tmp_path, files, on_ready=ready.append)
        assert worker.plan(build, model) == build.size + model.size

        worker.start(build, model, expected_bytes=build.size + model.size)
        progress = _finish(worker)

    assert progress.phase == "finished" and progress.done == progress.total
    found = install.installed_build(tmp_path)
    assert found is not None and found.current and found.executable.name == "llama-server.exe"
    assert (found.folder / "LICENSE").read_bytes() == b"MIT"
    assert install.model_installed(model, tmp_path)
    assert ready == [model]
    # The archives went once unpacked; nothing is left in staging.
    assert not any(install.downloads_dir(tmp_path).iterdir())
    assert not any(path.name.startswith(".staging") for path in install.runtime_dir(tmp_path).iterdir())
    assert install.remaining_bytes(build, model, tmp_path) == 0


def test_a_linux_tarball_and_its_cuda_runtime_unpack_into_one_folder(tmp_path):
    runtime = tar_bytes({"cudart-llama-fake/libcudart.so.13": b"cudart", "cudart-llama-fake/libcublas.so.13": b"blas"})
    with FakeFiles() as files:
        build, model = fake_build(files, tarball=True, runtime=runtime), fake_model(files)
        worker = _installer(tmp_path, files)
        worker.start(build, model)
        progress = _finish(worker)

    assert progress.phase == "finished", progress
    found = install.installed_build(tmp_path)
    assert found is not None and found.acceleration == "cuda-13"
    assert {path.name for path in found.folder.iterdir()} >= {"llama-server", "libggml.so", "libcudart.so.13",
                                                              "libcublas.so.13", "installed.json"}


def test_a_checksum_mismatch_is_refused_deleted_and_never_installed(tmp_path):
    with FakeFiles() as files:
        build = fake_build(files)
        model = fake_model(files, served=b"GGUF" + b"\x01" * 4096)  # same size, other bytes
        worker = _installer(tmp_path, files)
        worker.start(build, model)
        progress = _finish(worker)

    assert progress.phase == "failed" and progress.reason == "mismatch"
    assert not install.model_installed(model, tmp_path)
    folder = install.model_dir(model, tmp_path)
    assert not (folder / model.file).exists() and not (folder / f"{model.file}.part").exists()


def test_a_build_with_a_wrong_checksum_is_never_unpacked(tmp_path):
    with FakeFiles() as files:
        build, model = fake_build(files), fake_model(files)
        name = build.assets[0].name
        served = bytearray(files.files[f"/{name}"])
        served[len(served) // 2] ^= 0xFF  # same size, one byte changed in transit
        files.files[f"/{name}"] = bytes(served)
        worker = _installer(tmp_path, files)
        worker.start(build, model)
        progress = _finish(worker)

    assert progress.reason == "mismatch"
    assert install.installed_build(tmp_path) is None
    assert [request["path"] for request in files.requests] == [f"/{name}"]  # the model was never fetched


def test_a_build_whose_program_doesnt_run_here_is_never_published(tmp_path):
    with FakeFiles() as files:
        build, model = fake_build(files, version_exit=3), fake_model(files)
        worker = _installer(tmp_path, files)
        worker.start(build, model)
        progress = _finish(worker)

    assert progress.phase == "failed" and progress.reason == "blocked"
    assert install.installed_build(tmp_path) is None
    assert not install.build_dir(build, tmp_path).exists()
    assert not any(path.name.startswith(".staging") for path in install.runtime_dir(tmp_path).iterdir())


def test_a_partial_download_never_counts_as_installed(tmp_path):
    with FakeFiles() as files:
        build, model = fake_build(files), fake_model(files)
    folder = install.model_dir(model, tmp_path)
    folder.mkdir(parents=True)
    (folder / f"{model.file}.part").write_bytes(b"GGUF")
    assert not install.model_installed(model, tmp_path)
    (folder / model.file).write_bytes(b"GGUF" + b"\x00" * 4096)  # the whole file, but no record
    assert not install.model_installed(model, tmp_path)
    # A build folder without installed.json (a crash mid-publish) is not a build.
    loose = install.build_dir(build, tmp_path)
    loose.mkdir(parents=True)
    (loose / "llama-server.exe").write_bytes(b"x")
    assert install.installed_build(tmp_path) is None


def test_a_stopped_download_keeps_what_arrived_and_resumes_with_a_range_request(tmp_path):
    data = bytes(range(256)) * 16384  # 4 MB: it arrives in many blocks
    with FakeFiles() as files:
        build, model = fake_build(files), fake_model(files, data)
        gate = threading.Event()
        files.hold[f"/{model.file}"] = gate
        worker = _installer(tmp_path, files)
        worker.start(build, model)
        part = install.model_dir(model, tmp_path) / f"{model.file}.part"
        for _ in range(1000):
            if worker.progress.done > build.size:  # the model's first byte arrived
                break
            threading.Event().wait(0.01)
        stopping = threading.Thread(target=worker.stop)
        stopping.start()
        gate.set()
        stopping.join(30)
        assert worker.progress.phase == "stopped"
        assert part.is_file() and 0 < part.stat().st_size and not install.model_installed(model, tmp_path)

        files.hold.clear()
        worker.start(build, model)
        progress = _finish(worker)

    assert progress.phase == "finished"
    assert install.model_installed(model, tmp_path)
    assert files.asked(model.file)[-1] is not None and files.asked(model.file)[-1].startswith("bytes=")
    assert (install.model_dir(model, tmp_path) / model.file).read_bytes() == data


def test_a_dropped_connection_continues_where_it_stopped(tmp_path):
    data = b"GGUF" + bytes(range(256)) * 32
    with FakeFiles() as files:
        model = fake_model(files, data)
        files.cut_after[f"/{model.file}"] = 1000
        target = Fetcher(opener=opener(files), retry_wait=0.01).fetch(
            Pinned(model.url, model.file, model.size, model.sha256), tmp_path)

    assert target.read_bytes() == data
    assert files.asked(model.file) == [None, "bytes=1000-"]


def test_a_download_starts_only_with_the_size_the_person_agreed_to_and_enough_disk(tmp_path):
    with FakeFiles() as files:
        build, model = fake_build(files), fake_model(files)
        worker = _installer(tmp_path, files)
        with pytest.raises(install.InstallError) as changed:
            worker.start(build, model, expected_bytes=1)
        assert changed.value.code == "size_changed"
        assert changed.value.facts["download_bytes"] == build.size + model.size

        tight = install.Installer(tmp_path, fetcher=Fetcher(opener=opener(files)), command=python_command,
                                  free_bytes=lambda _path: 10)
        with pytest.raises(install.InstallError) as full:
            tight.start(build, model)
        assert full.value.code == "out_of_disk" and full.value.facts["free_bytes"] == 10

    assert files.requests == []  # nothing was downloaded
    assert not install.runtime_dir(tmp_path).exists() and not install.models_dir(tmp_path).exists()


def test_unpacking_refuses_an_entry_that_would_land_outside_the_folder(tmp_path):
    archive = tmp_path / "evil.zip"
    archive.write_bytes(zip_bytes({"../outside.txt": b"x"}))

    with pytest.raises(install.InstallError) as refused:
        install.unpack(archive, tmp_path / "into")

    assert refused.value.code == "unpack" and not (tmp_path / "outside.txt").exists()
    tarball = tmp_path / "evil.tar.gz"
    tarball.write_bytes(tar_bytes({"../../outside.txt": b"x"}))
    with pytest.raises(install.InstallError):
        install.unpack(tarball, tmp_path / "into2")
    assert not (tmp_path / "outside.txt").exists()


def test_remove_frees_one_model_or_everything(tmp_path):
    with FakeFiles() as files:
        build, model = fake_build(files), fake_model(files)
        worker = _installer(tmp_path, files)
        worker.start(build, model)
        _finish(worker)

    freed_model = install.remove(tmp_path, model_id=model.id)
    assert freed_model >= model.size and not install.model_installed(model, tmp_path)
    assert install.installed_build(tmp_path) is not None

    freed = install.remove(tmp_path)
    assert freed > 0
    assert not install.runtime_dir(tmp_path).exists() and not install.models_dir(tmp_path).exists()
    assert install.disk_bytes(tmp_path) == 0


def test_backups_leave_out_the_build_and_the_model_files():
    from row_bot.profile_restore import kept_in_place

    assert kept_in_place("runtimes") and kept_in_place("local_models")


def test_the_product_opener_refuses_a_host_that_isnt_reviewed():
    with pytest.raises(ValueError, match="download_source_refused"):
        with fetch.safe_opener()("https://example.com/llama-server.zip", {}):
            pass
    with pytest.raises(ValueError, match="download_source_refused"):
        with fetch.safe_opener()("http://github.com/ggml-org/llama.cpp/releases/download/b12359/x.zip", {}):
            pass


def test_a_fetch_that_was_refused_reports_it_without_retrying(tmp_path):
    asked = []

    def refusing(url, headers):
        asked.append(url)
        raise ValueError("download_source_refused")

    item = Pinned("https://example.com/model.gguf", "model.gguf", 4, sha256(b"GGUF"))
    with pytest.raises(FetchError) as refused:
        Fetcher(opener=refusing, retry_wait=0).fetch(item, tmp_path)

    assert refused.value.code == "refused" and asked == [item.url]
