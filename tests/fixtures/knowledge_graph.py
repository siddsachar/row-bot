from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


def fresh_knowledge_graph(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))
    import row_bot.knowledge_graph as kg

    kg = importlib.reload(kg)
    kg._skip_reindex = True
    monkeypatch.setattr(kg, "semantic_search", lambda *_args, **_kwargs: [])
    return kg


def entity_payload(subject: str, entity_type: str = "person", **properties: Any) -> dict[str, Any]:
    return {
        "subject": subject,
        "entity_type": entity_type,
        "description": properties.pop("description", f"{subject} fixture entity"),
        "source": properties.pop("source", "test"),
        "properties": properties,
    }
