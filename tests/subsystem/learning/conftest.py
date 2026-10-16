from __future__ import annotations

import sys

import pytest

from tests.fixtures.learning import FakeClock, FakeThreads, save_learning_settings


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
