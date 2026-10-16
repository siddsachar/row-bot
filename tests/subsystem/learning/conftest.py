from __future__ import annotations

import sys

import pytest

from tests.fixtures.learning import FakeClock, FakeThreads, save_learning_settings, set_learning_preview


@pytest.fixture(autouse=True)
def _learning_preview(monkeypatch):
    """These tests run a build started with the preview flag (O34) unless one says otherwise."""

    set_learning_preview(monkeypatch, True)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """An isolated, empty data folder that Learning resolves to."""

    from row_bot import learning

    root = tmp_path / "row-bot-data"
    root.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(root))
    learning.invalidate()
    yield root
    learning.invalidate()


@pytest.fixture
def learning_on(data_dir):
    """The data folder with Learning turned on (no store yet)."""

    save_learning_settings(data_dir, enabled=True)
    return data_dir


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def chats():
    """The conversations scope sees (a fake ``scope.ThreadLookup``)."""

    return FakeThreads()


@pytest.fixture
def writer(learning_on, clock, chats):
    """A capture that writes on the test's own thread (``drain``), with a fake clock."""

    from row_bot.learning.capture import Capture

    capture = Capture(clock=clock, start_thread=False, lookup=chats)
    yield capture
    capture.shutdown()


@pytest.fixture(autouse=True)
def _process_capture_stopped():
    """The app's own capture (if a test used it) writes nothing into the next test's folder."""

    yield
    capture = sys.modules.get("row_bot.learning.capture")
    if capture is not None:
        capture.shutdown(10.0)


@pytest.fixture(autouse=True)
def _no_real_graphics_card_or_download():
    """Never ask the real ``nvidia-smi`` or start a real download: the hardware check reads as
    "no NVIDIA card" unless a test sets its own, and the app's download is forgotten after each
    test (a test that downloads plugs in fakes on loopback)."""

    from row_bot.learning.training import hardware

    hardware.check(refresh=True, detector=lambda: hardware.HardwareCheck("no_gpu"))
    yield
    downloads = sys.modules.get("row_bot.learning.training.downloads")
    if downloads is not None and downloads.loaded() is not None:
        downloads.loaded().stop_quietly("app_closed")
        downloads._downloads = None  # noqa: SLF001 - a fresh app download for the next test
    engine = sys.modules.get("row_bot.learning.training.engine")
    if engine is not None:
        engine.forget_status()
    hardware.forget()


@pytest.fixture(autouse=True)
def _training_stopped():
    """The app's training runner (if a test started it) ends with its worker, and the next test
    gets a fresh one."""

    yield
    runner = sys.modules.get("row_bot.learning.runner")
    if runner is not None:
        runner.shutdown(10.0)
        runner._runner = None  # noqa: SLF001 - a fresh app runner for the next test
        runner._thread = None  # noqa: SLF001
    lease = sys.modules.get("row_bot.learning.lease")
    if lease is not None:
        lease.release_training()
