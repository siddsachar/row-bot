from __future__ import annotations

import pytest

from tests.fixtures.learning import FakeClock, save_learning_settings


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
