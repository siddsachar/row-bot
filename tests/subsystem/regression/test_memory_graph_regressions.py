from __future__ import annotations

import json
import types
from typing import Any

import pytest

from tests.fixtures.memory_stack import fresh_memory_stack


pytestmark = pytest.mark.subsystem


def test_memory_entity_crud_relations_aliases_and_delete_cascade(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    kg = stack["kg"]
    memory = stack["memory"]

    alice = memory.save_memory("person", "Alice Regression", "Alice is a software engineer.", tags="test")
    alice_id = alice["id"]
    assert alice["entity_type"] == "person"
    assert memory.get_memory(alice_id)["subject"] == "Alice Regression"
    assert memory.find_by_subject(None, "alice regression")["id"] == alice_id

    updated = memory.update_memory(
        alice_id,
        "Alice is a senior software engineer at Acme.",
        aliases="Ally Regression, Al Regression",
    )
    assert updated is not None
    assert "senior" in updated["description"]
    assert memory.find_by_subject(None, "Ally Regression")["id"] == alice_id

    berlin = memory.save_memory("place", "Berlin Regression", "Capital of Germany.", tags="test")
    lives_in = kg.add_relation(alice_id, berlin["id"], "lives_in", source="test", confidence=0.9)
    born_in = kg.add_relation(alice_id, berlin["id"], "born_in", source="test", confidence=0.7)
    assert lives_in is not None
    assert born_in is not None

    relation_types = {
        relation["relation_type"]
        for relation in kg.get_relations(alice_id, direction="both")
        if relation["peer_id"] == berlin["id"]
    }
    assert {"lives_in", "born_in"} <= relation_types

    assert kg.delete_relation(born_in["id"]) is True
    relation_types_after_delete = {
        relation["relation_type"]
        for relation in kg.get_relations(alice_id, direction="both")
        if relation["peer_id"] == berlin["id"]
    }
    assert "born_in" not in relation_types_after_delete

    assert memory.delete_memory(alice_id) is True
    assert all(relation["peer_id"] != alice_id for relation in kg.get_relations(berlin["id"], direction="both"))

    with pytest.raises(ValueError):
        memory.save_memory("invalid_type", "Bad", "Should fail.")


def test_memory_edge_cases_and_data_integrity(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    kg = stack["kg"]
    memory = stack["memory"]

    with pytest.raises(ValueError):
        memory.save_memory("INVALID_CATEGORY", "bad", "bad content")

    assert kg.delete_entity("definitely-not-real") is False
    fake_relation = kg.add_relation("fake-source", "fake-target", "test_rel")
    assert fake_relation is None

    unicode_entity = memory.save_memory(
        "fact",
        "Unicode Regression",
        "User likes Japanese text and music.",
        tags="unicode",
    )
    assert "Japanese text" in memory.get_memory(unicode_entity["id"])["content"]

    long_content = "A" * 10_000
    long_entity = memory.save_memory("fact", "Long Regression", long_content, tags="long")
    assert len(memory.get_memory(long_entity["id"])["content"]) == 10_000

    source = memory.save_memory("person", "DupRelA", "test")
    target = memory.save_memory("place", "DupRelB", "test")
    rel1 = kg.add_relation(source["id"], target["id"], "test_rel", source="test")
    rel2 = kg.add_relation(source["id"], target["id"], "test_rel", source="test")
    test_relations = [
        relation for relation in kg.get_relations(source["id"])
        if relation["relation_type"] == "test_rel" and relation["peer_id"] == target["id"]
    ]
    assert rel1 is not None
    assert rel2 is None
    assert len(test_relations) == 1

    cross = memory.save_memory("person", "Cross Category Regression", "test cross-category")
    assert memory.find_by_subject("place", cross["subject"]) is None
    assert memory.find_by_subject(None, cross["subject"])["id"] == cross["id"]
    assert memory.find_by_subject("person", cross["subject"])["id"] == cross["id"]


def test_dedup_and_save_merges_entities_aliases_relations_and_resets_reindex(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    kg = stack["kg"]
    memory_extraction = stack["memory_extraction"]
    monkeypatch.setattr(kg, "rebuild_index", lambda: None)
    monkeypatch.setattr(stack["memory_tool"], "_check_contradiction", lambda *_args, **_kwargs: None)

    extracted = [
        {
            "category": "person",
            "subject": "Emma Regression",
            "content": "Emma Regression is a doctor at St Mary Hospital.",
            "aliases": ["Em", "Doctor Emma"],
            "confidence": 0.92,
        },
        {
            "category": "place",
            "subject": "Dublin Regression",
            "content": "Dublin Regression is where Emma lives.",
        },
        {
            "relation_type": "lives_in",
            "source_subject": "Emma Regression",
            "target_subject": "Dublin Regression",
            "confidence": 0.95,
        },
    ]

    saved = memory_extraction._dedup_and_save(
        extracted,
        source="test_pipeline",
        source_context={"thread_id": "thread-1", "thread_name": "Regression thread"},
    )
    saved_again = memory_extraction._dedup_and_save(extracted, source="test_pipeline")

    emma = kg.find_by_subject(None, "Doctor Emma")
    dublin = kg.find_by_subject(None, "Dublin Regression")
    assert saved >= 3
    assert saved_again == 0
    assert emma is not None
    assert dublin is not None
    assert "Em" in emma["aliases"]
    assert kg._skip_reindex is False

    relation_types = {
        relation["relation_type"]
        for relation in kg.get_relations(emma["id"])
        if relation["peer_id"] == dublin["id"]
    }
    assert "lives_in" in relation_types
    props = json.loads(emma["properties"])
    assert props["source_context"]["thread_id"] == "thread-1"


def test_full_extraction_flow_uses_thread_messages_filters_and_dedups(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    kg = stack["kg"]
    memory_extraction = stack["memory_extraction"]

    threads = [
        ("thread-1", "Family", "", "2099-01-01T00:00:00"),
        ("thread-2", "Pet", "", "2099-01-01T00:00:00"),
        ("thread-skip", "Active", "", "2099-01-01T00:00:00"),
        ("thread-bg", "⚡ Background", "", "2099-01-01T00:00:00"),
    ]
    monkeypatch.setattr("row_bot.threads._list_threads", lambda: threads)
    monkeypatch.setattr(
        memory_extraction,
        "_get_thread_messages",
        lambda thread_id: [
            {"role": "user", "content": f"facts from {thread_id}"},
            {"role": "assistant", "content": "assistant context should be truncated"},
        ],
    )

    def fake_extract(conversation: str) -> list[dict[str, Any]]:
        if "thread-1" in conversation:
            return [{"category": "person", "subject": "Maria Regression", "content": "Maria lives in Rome."}]
        if "thread-2" in conversation:
            return [{"category": "media", "subject": "Whiskers Regression", "content": "Whiskers is a cat."}]
        return []

    monkeypatch.setattr(memory_extraction, "_extract_from_conversation", fake_extract)

    statuses: list[str] = []
    saved = memory_extraction.run_extraction(on_status=statuses.append, exclude_thread_ids={"thread-skip"})

    assert saved == 2
    assert kg.find_by_subject(None, "Maria Regression") is not None
    assert kg.find_by_subject(None, "Whiskers Regression") is not None
    assert any("Scanning 2 conversation" in status for status in statuses)
    assert any("Extracting memories from: Family" == status for status in statuses)
    assert statuses[-1] == "Extracted 2 new memory(s)"
    status = memory_extraction.get_extraction_status()
    assert status["threads_scanned"] == 2
    assert status["entities_saved"] == 2


def test_extract_from_conversation_parses_json_filters_invalid_and_handles_failures(
    tmp_path,
    monkeypatch,
) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_extraction = stack["memory_extraction"]

    class FakeResponse:
        content = (
            "<think>ignore this</think>\n"
            "["
            "{\"category\":\"person\",\"subject\":\"Ada\",\"content\":\"Ada likes tests\"},"
            "{\"relation_type\":\"works_on\",\"source_subject\":\"Ada\",\"target_subject\":\"Row-Bot\"},"
            "{\"category\":\"person\",\"subject\":\"Missing content\"},"
            "\"not an object\""
            "]"
        )

    class FakeLLM:
        def invoke(self, _messages):
            return FakeResponse()

    monkeypatch.setattr("row_bot.models.get_current_model", lambda: "fake-model")
    monkeypatch.setattr("row_bot.models.get_llm_for", lambda _model: FakeLLM())

    extracted = memory_extraction._extract_from_conversation("User: Ada likes tests.")

    assert extracted == [
        {"category": "person", "subject": "Ada", "content": "Ada likes tests"},
        {"relation_type": "works_on", "source_subject": "Ada", "target_subject": "Row-Bot"},
    ]

    class BrokenLLM:
        def invoke(self, _messages):
            raise RuntimeError("offline")

    monkeypatch.setattr("row_bot.models.get_llm_for", lambda _model: BrokenLLM())
    assert memory_extraction._extract_from_conversation("User: no facts") == []


def test_memory_repair_maintenance_contracts(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    kg = stack["kg"]

    assert not callable(getattr(kg, "repair_graph_islands", None))

    source = kg.save_entity("fact", "Repair Source", "Source fact.", source="test")
    target = kg.save_entity("fact", "Repair Target", "Target fact.", source="test")
    assert kg.add_relation(source["id"], target["id"], "related_to", source="test") is None

    components = kg.get_connected_components()
    assert isinstance(components, list)
    assert any(source["id"] in component for component in components)


def test_json_block_parser_handles_nested_blocks_and_greedy_traps(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    kg = stack["kg"]

    assert kg.extract_json_block("[1, 2, 3]") == "[1, 2, 3]"
    assert kg.extract_json_block('Here is [{"a": [1, 2, 3]}] done') == '[{"a": [1, 2, 3]}]'
    assert kg.extract_json_block("No JSON here") is None
    assert kg.extract_json_block('prefix {"key": "value"} suffix', "{") == '{"key": "value"}'
    assert kg.extract_json_block("Here [1,2] and then [3,4] end") == "[1,2]"
