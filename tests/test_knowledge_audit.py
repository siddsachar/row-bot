from __future__ import annotations

import importlib
import json


def test_audit_summary_normalizes_status_source_tier_and_evidence():
    from row_bot.ui import knowledge_audit as audit

    mem = {
        "id": "m1",
        "subject": "Atlas",
        "entity_type": "concept",
        "source": "document:atlas.pdf",
        "properties": json.dumps({
            "status": "needs_review",
            "memory_tier": "resource",
            "confidence": 0.875,
            "review_reason": "Conflicting source",
            "evidence": [{"quote": "Atlas appears in the uploaded plan."}],
            "source_context": {
                "kind": "document",
                "display_name": "atlas.pdf",
                "window_count": 2,
            },
        }),
    }

    summary = audit.audit_summary(mem)

    assert summary["status"] == "needs_review"
    assert summary["status_label"] == "Needs review"
    assert summary["source_bucket"] == "Document"
    assert summary["tier"] == "resource"
    assert summary["confidence_label"] == "88%"
    assert summary["evidence_count"] == 1
    assert "Atlas appears" in summary["evidence"][0]
    assert "display name: atlas.pdf" in summary["source_context_lines"]


def test_filter_memories_uses_audit_fields_and_query():
    from row_bot.ui import knowledge_audit as audit

    memories = [
        {
            "id": "a",
            "subject": "Current city",
            "description": "User lives in London.",
            "entity_type": "fact",
            "source": "live",
            "properties": {"status": "active", "memory_tier": "core"},
        },
        {
            "id": "b",
            "subject": "Atlas",
            "description": "Atlas document body.",
            "entity_type": "concept",
            "source": "document:atlas.pdf",
            "properties": {"status": "needs_review", "memory_tier": "resource"},
        },
    ]

    assert [m["id"] for m in audit.filter_memories(memories, status="Active")] == ["a"]
    assert [m["id"] for m in audit.filter_memories(memories, source="Document")] == ["b"]
    assert [m["id"] for m in audit.filter_memories(memories, tier="Resource")] == ["b"]
    assert [m["id"] for m in audit.filter_memories(memories, query="London")] == ["a"]
    assert audit.status_counts(memories)["needs_review"] == 1


def test_trace_and_journal_loaders_are_read_only(tmp_path, monkeypatch):
    import row_bot.memory_policy as memory_policy

    policy = importlib.reload(memory_policy)
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps([
            {"ts": "1", "allowed": False, "reason": "guardrail"},
            {"ts": "2", "allowed": True, "reason": "selected", "selected_count": 1},
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(policy, "_RECALL_TRACE_FILE", trace_path)

    from row_bot.ui import knowledge_audit as audit

    traces = audit.load_recent_recall_traces(limit=1)

    assert traces == [{"ts": "2", "allowed": True, "reason": "selected", "selected_count": 1}]


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


def test_phase5_ui_sources_are_wired_to_audit_paths():
    settings_src = open("src/row_bot/ui/settings.py", "r", encoding="utf-8").read()
    editor_src = open("src/row_bot/ui/entity_editor.py", "r", encoding="utf-8").read()
    graph_src = open("src/row_bot/ui/graph_panel.py", "r", encoding="utf-8").read()

    assert "knowledge_audit" in settings_src
    assert "Needs Review" in settings_src
    assert "Recent recall decisions" in settings_src
    assert "Memory change log" in settings_src
    assert "STATUS_OPTIONS" in settings_src
    assert "SOURCE_OPTIONS" in settings_src
    assert "TIER_OPTIONS" in settings_src

    assert "memory_evolution" in editor_src
    assert "Audit and Provenance" in editor_src
    assert "mark_user_modified" in editor_src
    assert "mark_superseded" in editor_src
    assert "set_status" in editor_src

    assert "graph-edit-trigger" in graph_src
    assert "open_entity_editor" in graph_src
    assert "_status" in graph_src and "_tier" in graph_src and "_review_reason" in graph_src
