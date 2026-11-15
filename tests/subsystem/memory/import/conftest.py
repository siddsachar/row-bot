"""The memory import service on a memory store in the test's own folder, with a fake default chat model."""
from __future__ import annotations

import pytest

from tests.helpers.memory_import import memory_import_stack


@pytest.fixture
def stack(tmp_path, monkeypatch, reload_for_data_dir):
    with memory_import_stack(tmp_path, monkeypatch, reload_for_data_dir) as built:
        yield built
