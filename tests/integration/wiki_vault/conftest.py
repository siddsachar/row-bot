from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def wiki_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    data_dir = tmp_path / "row-bot-data"
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(data_dir))

    import row_bot.knowledge_graph as kg
    import row_bot.memory as memory
    import row_bot.memory_evolution as memory_evolution
    import row_bot.tools.memory_tool as memory_tool
    import row_bot.wiki_vault as wiki_vault

    kg = importlib.reload(kg)
    kg._skip_reindex = True
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])

    memory = importlib.reload(memory)
    memory_evolution = importlib.reload(memory_evolution)
    memory_tool = importlib.reload(memory_tool)
    wiki_vault = importlib.reload(wiki_vault)

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
