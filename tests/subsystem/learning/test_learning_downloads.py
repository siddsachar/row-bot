"""The personal model's download (L2b, O36, UX §1, §9): the engine, then the starting model;
Ready to download, downloading with a real denominator, paused, not enough disk, failed with a
reason, installed. Fakes on loopback stand in for PyPI, PyTorch, Hugging Face and uv."""

from __future__ import annotations

import json
import threading

import pytest

from row_bot.learning.training import downloads, engine, hardware, lockfile, weights
from tests.fixtures.learning import save_learning_settings
from tests.fixtures.learning_engine import download_world

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

@pytest.fixture
def world(learning_on, tmp_path, monkeypatch):
    with download_world(learning_on, tmp_path, monkeypatch) as found:
        yield found


def _until_model_under_way(task, world):
    """Wait until the engine is in and the model's first file has arrived (its big one is held)."""

    for _ in range(500):
        view = task.view()
        if view.phase == "model" and weights.downloaded_bytes(world.model, world.root) > 0:
            return view
        threading.Event().wait(0.02)
    raise AssertionError("the model's download didn't start")


def test_download_installs_the_engine_then_the_starting_model(world):
    task = world.downloads(start_threads=False)
    assert task.view().state == "none"  # nothing asked, nothing downloads

    finished = task.start()

    assert finished.state == "none"
    assert finished.engine_state == "installed" and finished.engine_bytes > 0
    assert finished.model_state == "downloaded" and finished.model_bytes >= world.model.download_bytes
    assert not task.state_path.exists()


def test_while_it_downloads_progress_is_bytes_of_the_whole_download(world):
    gate = threading.Event()
    world.index.hold[world.weights_path] = gate
    task = world.downloads()

    started = task.start()
    assert started.state == "downloading"
    total = started.total_bytes
    # Every wheel, llama.cpp's tools and every model file (uv is given here, so it isn't downloaded).
    plan = lockfile.engine_plan("cu130")
    assert total == sum(item.size for item in world.model.pinned()) + sum(
        wheel.known_size for wheel in plan.wheels) + plan.tools_bytes()
    view = _until_model_under_way(task, world)
    assert view.state == "downloading" and view.phase == "model"
    assert 0 < view.done_bytes < view.total_bytes == total
    gate.set()
    done = task.wait(30)
    assert done.state == "none" and done.model_state == "downloaded"


def test_stop_pauses_with_what_arrived_and_resume_finishes_it(world):
    gate = threading.Event()
    world.index.hold[world.weights_path] = gate
    task = world.downloads()
    task.start()
    _until_model_under_way(task, world)

    stopping = threading.Thread(target=task.stop)
    stopping.start()
    gate.set()
    stopping.join(30)

    paused = task.view()
    assert paused.state == "paused"
    assert paused.engine_state == "installed"  # what finished stays finished
    assert 0 < paused.done_bytes < paused.total_bytes == world.model.download_bytes
    # Row-Bot restarts: still paused, nothing resumes by itself.
    again = world.downloads(start_threads=False)
    assert again.view().state == "paused"

    world.index.hold.clear()
    assert again.start().state == "none"
    assert again.view().model_state == "downloaded"


def test_a_download_running_when_row_bot_closed_reads_as_paused(world):
    task = world.downloads(start_threads=False)
    task.state_path.parent.mkdir(parents=True, exist_ok=True)
    task.state_path.write_text(json.dumps({"state": "downloading"}), encoding="utf-8")

    assert task.view().state == "paused"


def test_cancel_deletes_what_arrived(world):
    gate = threading.Event()
    world.index.hold[world.weights_path] = gate
    task = world.downloads()
    task.start()
    _until_model_under_way(task, world)
    canceller = threading.Thread(target=task.cancel)
    canceller.start()
    gate.set()
    canceller.join(30)

    view = task.view()
    assert view.state == "none" and view.done_bytes == 0
    assert not weights.partial_dir(world.model, world.root).exists()
    assert not task.state_path.exists()


def test_not_enough_disk_says_what_it_needs_and_what_is_free(world):
    world.free = 500_000_000
    task = world.downloads(start_threads=False)

    with pytest.raises(downloads.DownloadError) as full:
        task.start()

    assert full.value.code == "not_enough_disk"
    view = task.view()
    assert view.state == "not_enough_disk"
    assert view.needs_bytes > view.free_bytes == 500_000_000
    assert not engine.downloads_dir(world.root).exists()  # nothing was downloaded


def test_a_failed_download_says_why_and_try_again_finishes(world):
    original = world.index.files[world.weights_path]
    world.index.files[world.weights_path] = b"not the pinned weights" * 1000
    task = world.downloads(start_threads=False)

    task.start()

    failed = task.view()
    assert failed.state == "failed" and failed.failure == "mismatch"
    world.index.files[world.weights_path] = original
    assert task.start().state == "none"


def test_an_earlier_download_answer_reads_as_ready_and_waits_for_a_tap(world):
    # O36: never a multi-GB download unprompted from an old answer.
    save_learning_settings(world.root, model_download="waiting")
    task = world.downloads(start_threads=False)

    assert task.view().state == "ready"
    assert world.index.requests == []


@pytest.mark.parametrize("settings, code", [
    ({"enabled": False}, "learning_off"),
    ({"paused": True}, "learning_paused"),
    ({"train_where": "hugging_face"}, "unavailable"),
])
def test_nothing_downloads_while_learning_is_off_paused_or_training_elsewhere(world, settings, code):
    save_learning_settings(world.root, **settings)
    task = world.downloads(start_threads=False)

    with pytest.raises(downloads.DownloadError) as refused:
        task.start()

    assert refused.value.code == code
    assert world.index.requests == []
    assert not engine.learning_runtimes(world.root).exists()


def test_nothing_downloads_without_the_preview_flag(world, monkeypatch):
    from tests.fixtures.learning import set_learning_preview

    set_learning_preview(monkeypatch, False)
    with pytest.raises(downloads.DownloadError):
        world.downloads(start_threads=False).start()
    assert world.index.requests == []


@pytest.mark.parametrize("found", [
    hardware.HardwareCheck("no_gpu"),
    hardware.classify([hardware.Gpu("NVIDIA GeForce RTX 4080", 16_376, "560.94")]),
])
def test_a_computer_that_cant_train_downloads_no_engine(world, found):
    assert found.state in {"no_gpu", "old_driver"}
    task = world.downloads(start_threads=False, check_hardware=lambda: found, peek_hardware=lambda: found)

    with pytest.raises(downloads.DownloadError) as refused:
        task.start()

    assert refused.value.code == "unavailable"


def test_pausing_learning_stops_a_running_download(world):
    from row_bot import learning

    gate = threading.Event()
    world.index.hold[world.weights_path] = gate
    task = world.downloads()
    task.start()
    _until_model_under_way(task, world)

    save_learning_settings(world.root, paused=True)
    learning.invalidate()
    gate.set()
    task.wait(30)

    assert task.view().state == "paused"


def test_a_damaged_engine_is_built_again_by_the_next_download(world):
    task = world.downloads(start_threads=False)
    task.start()
    found = engine.status(world.root, refresh=True)
    next(found.site.glob("torch-*.dist-info")).joinpath("METADATA").unlink()
    engine.forget_status()

    repaired = task.view()
    assert repaired.engine_state == "damaged"
    assert task.start().engine_state == "installed"


# ── Row-Bot's model server (L2x, O75) ─────────────────────────────────────────


def test_the_model_server_downloads_with_the_engine_and_counts_in_its_size(world, monkeypatch):
    from row_bot.learning.training import server_files
    from tests.fixtures.learning_engine import fake_llama_server, server_version_runner

    fake_llama_server(world.index, monkeypatch)
    server_files.forget_status()
    task = world.downloads(start_threads=False, server_options={"runner": server_version_runner()})
    server_bytes = sum(item.known_size for item in server_files.plan("cu130"))

    asked = task.view()
    finished = task.start()

    assert asked.engine_download_bytes == lockfile.engine_plan("cu130").download_bytes() + server_bytes
    assert finished.state == "none" and finished.engine_state == "installed"
    assert server_files.status(world.root, variant="cu130", refresh=True).installed
    # The training engine, as the person sees it, includes the server (Settings' size, Remove).
    assert finished.engine_bytes > engine.status(world.root, refresh=True).bytes


def test_an_engine_from_before_the_model_server_offers_the_server_alone_with_one_tap(world, monkeypatch):
    from row_bot.learning.training import server_files
    from tests.fixtures.learning_engine import fake_llama_server, server_version_runner

    world.downloads(start_threads=False).start()  # an engine and starting model from before L2x
    fake_llama_server(world.index, monkeypatch)
    server_files.forget_status()
    task = world.downloads(start_threads=False, server_options={"runner": server_version_runner()})
    server_bytes = sum(item.known_size for item in server_files.plan("cu130"))

    ready = task.view()
    assert ready.state == "ready" and ready.total_bytes - ready.done_bytes == server_bytes  # never by itself (O36)
    finished = task.start()

    assert finished.state == "none" and server_files.status(world.root, variant="cu130", refresh=True).installed
