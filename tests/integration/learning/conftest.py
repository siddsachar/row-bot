from __future__ import annotations

import pytest

from tests.fixtures.learning import save_learning_settings, set_learning_preview


@pytest.fixture(autouse=True)
def _learning_preview(monkeypatch):
    """A build started with the preview flag (O34)."""

    set_learning_preview(monkeypatch, True)


@pytest.fixture(autouse=True)
def _no_real_graphics_card():
    """Never ask the real ``nvidia-smi``: the hardware check reads as "no NVIDIA card"."""

    from row_bot.learning.training import hardware

    hardware.check(refresh=True, detector=lambda: hardware.HardwareCheck("no_gpu"))
    yield
    hardware.forget()


@pytest.fixture
def learning_dir(tmp_path, monkeypatch):
    """Learning on in an isolated data folder, with the process's capture writing there."""

    from row_bot import learning
    from row_bot.learning import capture

    root = tmp_path / "learning-data"
    root.mkdir()
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(root))
    learning.invalidate()
    save_learning_settings(root, enabled=True)
    monkeypatch.setattr(capture, "_default", capture.Capture(idle_seconds=60))
    yield root
    capture.shutdown(10)
    learning.invalidate()
