from __future__ import annotations

import importlib


def test_graph_vis_json_includes_audit_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    import row_bot.knowledge_graph as knowledge_graph

    kg = importlib.reload(knowledge_graph)
    kg._skip_reindex = True
    entity = kg.save_entity(
        "fact",
        "Phase Five Memory",
        "A test memory.",
        source="live",
        properties={
            "status": "needs_review",
            "memory_tier": "core",
            "confidence": 0.9,
            "review_reason": "test review",
            "recalled_at": "2026-05-26T12:00:00",
        },
    )

    data = kg.graph_to_vis_json(max_nodes=20)
    node = next(n for n in data["nodes"] if n["id"] == entity["id"])

    assert node["_status"] == "needs_review"
    assert node["_tier"] == "core"
    assert node["_confidence"] == 0.9
    assert node["_review_reason"] == "test review"
    assert node["_recalled_at"].startswith("2026-05-26")
