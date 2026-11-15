"""The memory review service on a memory store in the test's own folder, with a fake default chat model."""
from __future__ import annotations

import pytest

from tests.helpers.memory_review import memory_review_stack


@pytest.fixture
def stack(tmp_path, monkeypatch, reload_for_data_dir):
    with memory_review_stack(tmp_path, monkeypatch, reload_for_data_dir) as built:
        yield built
