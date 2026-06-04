from __future__ import annotations

import importlib
import json


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("THOTH_DATA_DIR", str(tmp_path / "data"))
    import row_bot.knowledge_graph as knowledge_graph
    import row_bot.memory as memory
    import row_bot.memory_evolution as memory_evolution

    kg = importlib.reload(knowledge_graph)
    importlib.reload(memory)
    evo = importlib.reload(memory_evolution)
    kg._skip_reindex = True
    monkeypatch.setattr(kg, "semantic_search", lambda *args, **kwargs: [])
    return kg, evo


def _props(entity: dict) -> dict:
    raw = entity.get("properties", "{}")
    return json.loads(raw or "{}") if isinstance(raw, str) else dict(raw or {})


def test_normalize_and_merge_properties_preserves_authority(tmp_path, monkeypatch):
    _kg, evo = _fresh_modules(tmp_path, monkeypatch)
    merged = evo.merge_properties(
        {
            "status": "needs_review",
            "last_user_modified_at": "2026-01-01T00:00:00",
            "evidence": ["old"],
        },
        {
            "status": "active",
            "confidence": 1.5,
            "evidence": ["old", "new"],
        },
        actor="extraction",
        source_context={"thread_id": "t1"},
        high_authority=False,
    )

    assert merged["status"] == "needs_review"
    assert merged["confidence"] == 1.0
    assert merged["evidence"] == ["old", "new"]
    assert merged["source_context"]["thread_id"] == "t1"
    assert merged["source_context"]["actor"] == "extraction"


def test_mark_needs_review_and_journal(tmp_path, monkeypatch):
    kg, evo = _fresh_modules(tmp_path, monkeypatch)
    entity = kg.save_entity("fact", "Current city", "User lives in London.", source="live")

    updated = evo.mark_needs_review(
        entity["id"],
        "Conflicts with new claim: User lives in Paris.",
        actor="extraction",
        incoming={"subject": "Current city", "content": "User lives in Paris."},
    )

    props = _props(updated)
    assert props["status"] == "needs_review"
    assert "Paris" in props["review_reason"]
    assert props["review_candidate"]["content"] == "User lives in Paris."
    journal = evo.get_journal()
    assert journal[-1]["action"] == "set_status"
    assert journal[-1]["actor"] == "extraction"


def test_mark_superseded_links_entities(tmp_path, monkeypatch):
    kg, evo = _fresh_modules(tmp_path, monkeypatch)
    old = kg.save_entity("fact", "Current city", "User lives in London.", source="live")
    new = kg.save_entity("fact", "Current city now", "User lives in Paris.", source="live")

    updated_old, updated_new = evo.mark_superseded(old["id"], new["id"], reason="manual correction", actor="manual")

    assert _props(updated_old)["status"] == "superseded"
    assert _props(updated_old)["superseded_by"] == new["id"]
    assert old["id"] in _props(updated_new)["supersedes"]


def test_mark_user_modified_restores_active_and_sets_actor(tmp_path, monkeypatch):
    kg, evo = _fresh_modules(tmp_path, monkeypatch)
    entity = kg.save_entity(
        "fact",
        "Current city",
        "User lives in London.",
        properties={"status": "needs_review", "review_reason": "conflict"},
        source="live",
    )

    updated = evo.mark_user_modified(entity["id"], actor="wiki", source_context={"vault_path": "wiki/fact/Current city.md"})

    props = _props(updated)
    assert props["status"] == "active"
    assert "review_reason" not in props
    assert props["last_user_modified_at"]
    assert props["source_context"]["actor"] == "wiki"
    assert props["source_context"]["vault_path"] == "wiki/fact/Current city.md"


def test_memory_tool_conflict_marks_existing_needs_review(tmp_path, monkeypatch):
    kg, _evo = _fresh_modules(tmp_path, monkeypatch)
    import row_bot.tools.memory_tool as memory_tool

    memory_tool = importlib.reload(memory_tool)
    existing = kg.save_entity("fact", "Current city", "User lives in London.", source="live")
    monkeypatch.setattr(memory_tool, "_check_contradiction", lambda old, new, subject: "City conflict")

    result = memory_tool._save_memory("fact", "Current city", "User lives in Paris.")

    props = _props(kg.get_entity(existing["id"]))
    assert "CONFLICT" in result
    assert props["status"] == "needs_review"
    assert props["review_reason"] == "City conflict"
    assert props["review_candidate"]["content"] == "User lives in Paris."


def test_memory_tool_update_marks_manual_authority_and_search_shows_status(tmp_path, monkeypatch):
    kg, _evo = _fresh_modules(tmp_path, monkeypatch)
    import row_bot.tools.memory_tool as memory_tool

    memory_tool = importlib.reload(memory_tool)
    entity = kg.save_entity(
        "fact",
        "Current city",
        "User lives in London.",
        properties={"status": "needs_review", "review_reason": "conflict"},
        source="live",
    )
    monkeypatch.setattr(kg, "retrieve_memory_candidates", lambda *args, **kwargs: [kg.get_entity(entity["id"])])

    update_result = memory_tool._update_memory(entity["id"], "User lives in Paris.")
    search_result = memory_tool._search_memory("Current city")
    props = _props(kg.get_entity(entity["id"]))

    assert "Memory updated successfully" in update_result
    assert props["status"] == "active"
    assert props["source_context"]["actor"] == "manual"
    rows = json.loads(search_result)
    assert rows[0]["status"] == "active"
    assert "confidence" in rows[0]


def test_extraction_document_source_sets_resource_provenance(tmp_path, monkeypatch):
    kg, _evo = _fresh_modules(tmp_path, monkeypatch)
    import row_bot.memory_extraction as memory_extraction

    extraction = importlib.reload(memory_extraction)
    saved = extraction._dedup_and_save(
        [{"category": "concept", "subject": "Atlas", "content": "Atlas appears in the document."}],
        source="document:atlas.pdf",
        source_context={"document_title": "Atlas", "display_name": "atlas.pdf", "window_count": 2},
    )

    entity = kg.find_by_subject(None, "Atlas")
    props = _props(entity)
    assert saved == 1
    assert entity["source"] == "document:atlas.pdf"
    assert props["memory_tier"] == "resource"
    assert props["source_context"]["kind"] == "document"
    assert props["source_context"]["display_name"] == "atlas.pdf"
    assert props["source_context"]["window_count"] == 2


def test_extraction_conflict_marks_review_without_overwrite(tmp_path, monkeypatch):
    kg, _evo = _fresh_modules(tmp_path, monkeypatch)
    import row_bot.memory_extraction as memory_extraction
    import row_bot.tools.memory_tool as memory_tool

    extraction = importlib.reload(memory_extraction)
    memory_tool = importlib.reload(memory_tool)
    existing = kg.save_entity(
        "fact",
        "Current city",
        "User lives in London.",
        properties={"last_user_modified_at": "2026-01-01T00:00:00"},
        source="live",
    )
    monkeypatch.setattr(memory_tool, "_check_contradiction", lambda old, new, subject: "City conflict")

    saved = extraction._dedup_and_save(
        [{"category": "fact", "subject": "Current city", "content": "User lives in Paris."}],
        source="extraction",
        source_context={"thread_id": "thread-1"},
    )

    updated = kg.get_entity(existing["id"])
    props = _props(updated)
    assert saved == 0
    assert updated["description"] == "User lives in London."
    assert props["status"] == "needs_review"
    assert props["review_reason"] == "City conflict"


def test_document_extraction_hub_and_entities_get_document_context(tmp_path, monkeypatch):
    kg, _evo = _fresh_modules(tmp_path, monkeypatch)
    import row_bot.document_extraction as document_extraction
    import row_bot.memory_extraction as memory_extraction

    doc = importlib.reload(document_extraction)
    importlib.reload(memory_extraction)
    file_path = tmp_path / "atlas.txt"
    file_path.write_text("Atlas document body " * 120, encoding="utf-8")
    monkeypatch.setattr("documents.load_document_text", lambda path: ("Atlas document body " * 120, ""))
    monkeypatch.setattr(doc, "_copy_to_vault_raw", lambda *args, **kwargs: None)
    monkeypatch.setattr(doc, "_map_summarize_window", lambda *args, **kwargs: "Atlas summary.")
    monkeypatch.setattr(
        doc,
        "_reduce_summaries",
        lambda title, summaries: "Atlas compiled article with enough detail for document extraction provenance testing.",
    )
    monkeypatch.setattr(doc, "_extract_from_summary", lambda title, article: [
        {"category": "concept", "subject": "Atlas Concept", "content": "Atlas Concept is described in the document."}
    ])
    monkeypatch.setattr(kg, "rebuild_index", lambda: None)

    result = doc.extract_from_document(str(file_path), "atlas.pdf")

    hub = kg.find_by_subject("media", "atlas")
    extracted = kg.find_by_subject(None, "Atlas Concept")
    hub_props = _props(hub)
    extracted_props = _props(extracted)
    assert result["status"] == "completed"
    assert hub_props["memory_tier"] == "resource"
    assert hub_props["source_context"]["display_name"] == "atlas.pdf"
    assert extracted["source"] == "document:atlas.pdf"
    assert extracted_props["memory_tier"] == "resource"
    assert extracted_props["source_context"]["document_title"] == "atlas"


def test_wiki_import_marks_high_authority_user_edit(tmp_path, monkeypatch):
    kg, _evo = _fresh_modules(tmp_path, monkeypatch)
    import row_bot.wiki_vault as wiki_vault

    wiki = importlib.reload(wiki_vault)
    vault = tmp_path / "vault"
    wiki.set_vault_path(str(vault))
    wiki.set_enabled(True)
    entity = kg.save_entity(
        "fact",
        "Current city",
        "User lives in London.",
        properties={"status": "needs_review", "review_reason": "conflict"},
        source="live",
    )
    path = wiki.export_entity(entity)
    text = path.read_text(encoding="utf-8")
    text = text.replace("User lives in London.", "User lives in Paris.")
    path.write_text(text, encoding="utf-8")

    assert wiki.import_from_vault(entity["id"], path) is True
    updated = kg.get_entity(entity["id"])
    props = _props(updated)
    assert updated["description"] == "User lives in Paris."
    assert props["status"] == "active"
    assert props["source_context"]["actor"] == "wiki"
    assert props["last_user_modified_at"]
    assert "review_reason" not in props


def test_wiki_export_does_not_create_false_sync(tmp_path, monkeypatch):
    kg, _evo = _fresh_modules(tmp_path, monkeypatch)
    import row_bot.wiki_vault as wiki_vault

    wiki = importlib.reload(wiki_vault)
    wiki.set_vault_path(str(tmp_path / "vault"))
    wiki.set_enabled(True)
    entity = kg.save_entity("fact", "Current city", "User lives in London.", source="live")
    wiki.export_entity(entity)

    assert wiki.check_vault_sync() == []


def test_policy_skips_needs_review_and_superseded_by_default(tmp_path, monkeypatch):
    kg, _evo = _fresh_modules(tmp_path, monkeypatch)
    import row_bot.memory_policy as memory_policy

    policy = importlib.reload(memory_policy)
    review = kg.save_entity(
        "fact",
        "Current city",
        "User lives in London.",
        properties={"status": "needs_review", "review_reason": "conflict"},
        source="live",
    )
    superseded = kg.save_entity(
        "fact",
        "Old city",
        "User used to live in Oxford.",
        properties={"status": "superseded", "superseded_by": "new-id"},
        source="live",
    )
    monkeypatch.setattr(kg, "retrieve_memory_candidates", lambda *args, **kwargs: [
        {**kg.get_entity(review["id"]), "score": 0.99, "lexical_score": 0.99, "decay_multiplier": 1.0, "via": "keyword", "relations": []},
        {**kg.get_entity(superseded["id"]), "score": 0.98, "lexical_score": 0.98, "decay_multiplier": 1.0, "via": "keyword", "relations": []},
    ])

    decision = policy.build_auto_recall("What is my current city?", [], runtime_surface="agent")
    history_decision = policy.build_auto_recall("What was my old city previously?", [], runtime_surface="agent")

    assert decision.allowed is False
    assert {r["reason"] for r in decision.trace["rejected"]} == {"status_needs_review", "stale_or_superseded"}
    assert history_decision.allowed is True
    assert history_decision.selected[0]["id"] == superseded["id"]
