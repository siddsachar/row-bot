from __future__ import annotations

import json

import pytest

from tests.fixtures.memory_stack import fresh_memory_stack


pytestmark = pytest.mark.subsystem


def test_extract_from_conversation_handles_response_shapes_and_invalid_payloads(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_extraction = stack["memory_extraction"]

    class ListResponse:
        content = [
            {"type": "text", "text": "```json\n"},
            {
                "type": "text",
                "text": json.dumps(
                    [
                        {"category": "person", "subject": "Ada", "content": "Ada likes tests."},
                        {"relation_type": "works_on", "source_subject": "Ada", "target_subject": "Row-Bot"},
                        {"category": "person", "subject": "No content"},
                        "ignored",
                    ]
                ),
            },
            {"type": "text", "text": "\n```"},
        ]

    class EmptyResponse:
        content = "No JSON here"

    class InvalidResponse:
        content = "[{bad-json]"

    class FakeLLM:
        def __init__(self, response):
            self.response = response

        def invoke(self, _messages):
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

    responses = iter([ListResponse(), EmptyResponse(), InvalidResponse(), RuntimeError("model down")])
    monkeypatch.setattr("row_bot.models.get_current_model", lambda: "fake")
    monkeypatch.setattr("row_bot.models.get_llm_for", lambda _model: FakeLLM(next(responses)))

    assert memory_extraction._extract_from_conversation("User: Ada likes tests.") == [
        {"category": "person", "subject": "Ada", "content": "Ada likes tests."},
        {"relation_type": "works_on", "source_subject": "Ada", "target_subject": "Row-Bot"},
    ]
    assert memory_extraction._extract_from_conversation("User: nothing") == []
    assert memory_extraction._extract_from_conversation("User: invalid") == []
    assert memory_extraction._extract_from_conversation("User: offline") == []


def test_entry_properties_preserve_source_context_evidence_confidence_and_method(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_extraction = stack["memory_extraction"]

    props = memory_extraction._entry_properties(
        {
            "category": "person",
            "properties": {"tags": ["existing"], "source_context": {"message_index": 1}},
            "evidence": "User said it",
            "evidence_role": "user",
            "confidence": 0.92,
            "memory_tier": "core",
        },
        source="document:notes.md",
        source_context={"thread_id": "thread-1", "thread_name": "Thread", "message_index": 3},
    )

    assert props["extraction_method"] == "document"
    assert props["source_thread_id"] == "thread-1"
    assert props["source_thread_name"] == "Thread"
    assert props["source_context"]["kind"] == "document"
    assert props["source_context"]["display_name"] == "notes.md"
    assert props["source_context"]["message_index"] == 3
    assert props["evidence"] == ["User said it"]
    assert props["evidence_role"] == "user"
    assert props["confidence"] == 0.92
    assert props["memory_tier"] == "resource"


def test_merge_properties_combines_aliases_tags_and_evidence_without_clobbering(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    memory_extraction = stack["memory_extraction"]

    merged = memory_extraction._merge_properties(
        {
            "entity_type": "person",
            "source": "live",
            "properties": {
                "tags": ["old"],
                "aliases": ["Al"],
                "evidence": ["old evidence"],
                "confidence": 0.95,
            },
        },
        {
            "tags": ["new"],
            "aliases": ["Ada"],
            "evidence": "new evidence",
            "confidence": 0.7,
            "source_context": {"thread_id": "thread-1"},
        },
    )

    assert set(merged["tags"]) >= {"old", "new"}
    assert set(merged["aliases"]) >= {"Al", "Ada"}
    assert "old evidence" in merged["evidence"]
    assert "new evidence" in merged["evidence"]
    assert merged["confidence"] >= 0.95
    assert merged["source_context"]["thread_id"] == "thread-1"


def test_dedup_save_creates_updates_and_links_with_provenance(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    kg = stack["kg"]
    memory_extraction = stack["memory_extraction"]

    monkeypatch.setattr(kg, "rebuild_index", lambda: None)
    monkeypatch.setattr(stack["memory_tool"], "_check_contradiction", lambda *_args, **_kwargs: None)

    created = memory_extraction._dedup_and_save(
        [
            {
                "category": "person",
                "subject": "Ada Dedup",
                "content": "Ada writes tests.",
                "aliases": ["A. Dedup"],
                "confidence": 0.9,
            },
            {"category": "project", "subject": "Row-Bot Dedup", "content": "Row-Bot has deterministic tests."},
            {
                "relation_type": "works on",
                "source_subject": "Ada Dedup",
                "target_subject": "Row-Bot Dedup",
                "confidence": 0.95,
                "evidence": "Ada writes tests for Row-Bot.",
            },
        ],
        source="extraction",
        source_context={"thread_id": "thread-1", "thread_name": "Test thread"},
    )

    ada = kg.find_by_subject(None, "A. Dedup")
    project = kg.find_by_subject(None, "Row-Bot Dedup")
    assert created == 3
    assert ada is not None
    assert project is not None
    assert json.loads(ada["properties"])["source_context"]["thread_id"] == "thread-1"
    relation = kg.get_relations(ada["id"], direction="outgoing")[0]
    assert relation["relation_type"] == "works_on"
    assert json.loads(relation["properties"])["evidence"] == "Ada writes tests for Row-Bot."

    updated = memory_extraction._dedup_and_save(
        [
            {
                "category": "person",
                "subject": "Ada Dedup",
                "content": "Ada writes tests and maintains coverage.",
                "aliases": "Coverage Ada",
            }
        ],
        source="extraction",
    )

    refreshed = kg.get_entity(ada["id"])
    assert updated == 1
    assert "maintains coverage" in refreshed["description"]
    assert "Coverage Ada" in refreshed["aliases"]
    assert kg._skip_reindex is False


def test_dedup_save_skips_invalid_low_confidence_vague_and_missing_endpoint_relations(tmp_path, monkeypatch) -> None:
    stack = fresh_memory_stack(tmp_path, monkeypatch)
    kg = stack["kg"]
    memory_extraction = stack["memory_extraction"]

    saved = memory_extraction._dedup_and_save(
        [
            {"category": "invalid", "subject": "Bad", "content": "Skip"},
            {"category": "person", "subject": "", "content": "Skip"},
            {"category": "person", "subject": "Solo Dedup", "content": "Solo exists."},
            {
                "relation_type": "related_to",
                "source_subject": "Solo Dedup",
                "target_subject": "Missing",
                "confidence": 0.99,
            },
            {
                "relation_type": "works_on",
                "source_subject": "Solo Dedup",
                "target_subject": "Missing",
                "confidence": 0.5,
            },
        ],
        source="extraction",
    )

    solo = kg.find_by_subject(None, "Solo Dedup")
    assert saved == 1
    assert solo is not None
    assert kg.get_relations(solo["id"]) == []
