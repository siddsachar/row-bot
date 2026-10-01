from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def wiki_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload_for_data_dir
) -> Iterator[dict[str, Any]]:
    data_dir = tmp_path / "row-bot-data"
    # Reloaded for this test's folder and given back their own state after it:
    # a vault left enabled here was read by a later test's delete (B215).
    kg, memory, memory_evolution, memory_tool, wiki_vault = reload_for_data_dir(
        data_dir,
        "row_bot.knowledge_graph",
        "row_bot.memory",
        "row_bot.memory_evolution",
        "row_bot.tools.memory_tool",
        "row_bot.wiki_vault",
    )
    kg._skip_reindex = True
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])

    wiki_vault._DATA_DIR = data_dir
    wiki_vault._CONFIG_PATH = data_dir / "wiki_config.json"
    wiki_vault.set_vault_path(str(tmp_path / "vault"))

    yield {
        "kg": kg,
        "memory": memory,
        "memory_evolution": memory_evolution,
        "memory_tool": memory_tool,
        "wiki_vault": wiki_vault,
        "data_dir": data_dir,
        "vault": tmp_path / "vault",
    }
