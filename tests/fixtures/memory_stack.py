from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


def fresh_memory_stack(tmp_path: Path, monkeypatch) -> dict[str, Any]:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))

    import row_bot.knowledge_graph as kg
    import row_bot.memory as memory
    import row_bot.memory_evolution as memory_evolution
    import row_bot.memory_extraction as memory_extraction
    import row_bot.tools.memory_tool as memory_tool
    import row_bot.wiki_vault as wiki_vault

    kg = importlib.reload(kg)
    kg._skip_reindex = True
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])

    memory = importlib.reload(memory)
    memory_evolution = importlib.reload(memory_evolution)
    memory_tool = importlib.reload(memory_tool)
    memory_extraction = importlib.reload(memory_extraction)
    wiki_vault = importlib.reload(wiki_vault)

    wiki_vault._DATA_DIR = tmp_path / "row-bot-data"
    wiki_vault._CONFIG_PATH = wiki_vault._DATA_DIR / "wiki_config.json"

    return {
        "kg": kg,
        "memory": memory,
        "memory_evolution": memory_evolution,
        "memory_extraction": memory_extraction,
        "memory_tool": memory_tool,
        "wiki_vault": wiki_vault,
    }
