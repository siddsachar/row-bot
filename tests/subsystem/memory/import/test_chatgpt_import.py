from __future__ import annotations

import json

import pytest

from row_bot.memory_import import scan_files
from tests.fixtures.memory_exports import chatgpt_conversation, chatgpt_simple, write_zip


pytestmark = pytest.mark.subsystem


def _texts(conversation):
    return [(message.role, message.text) for message in conversation.messages]


def test_chatgpt_reads_the_current_branch_only_and_skips_hidden_and_system_messages(tmp_path) -> None:
    export = write_zip(tmp_path / "chatgpt-export.zip", {
        "conversations.json": [chatgpt_conversation("conv-1", "Sourdough starter schedule", 1762934400.0), {}],
        "user.json": {"id": "user-x", "email": "someone@example.invalid", "phone_number": "+0000"},
        "chat.html": "<!DOCTYPE html><html><body>copy</body></html>",
        "file_00aa-photo.dat": b"\x89PNG....",
    })

    scan = scan_files([export])
    [conversation] = list(scan.iter_conversations())

    assert conversation.provider == "chatgpt"
    assert conversation.id == "conv-1"
    assert conversation.title == "Sourdough starter schedule"
    assert _texts(conversation) == [
        ("user", "How often should I feed a rye starter in the fridge?"),
        ("assistant", "Rye starters ferment faster so every 5 days."),
    ]
    assert conversation.created_at.tzinfo is not None
    assert conversation.created_at.isoformat().startswith("2025-11-12T08:00:00")
    assert scan.counts["chatgpt"].conversations == 1
    assert scan.counts["chatgpt"].messages == 2
    assert any(item.name.endswith("user.json") and "never imported" in item.reason for item in scan.skipped)
    assert all("someone@example.invalid" not in fact.text for fact in scan.facts)


def test_chatgpt_falls_back_to_the_newest_leaf_when_current_node_is_missing_or_dangling(tmp_path) -> None:
    export = write_zip(tmp_path / "export.zip", {"conversations.json": [
        chatgpt_conversation("missing", "No pointer", 1762934400.0, current_node=None),
        chatgpt_conversation("dangling", "Bad pointer", 1762934400.0, current_node="gone"),
    ]})

    scan = scan_files([export])
    conversations = {conversation.id: conversation for conversation in scan.iter_conversations()}

    for conversation in conversations.values():
        assert _texts(conversation)[-1] == ("assistant", "Rye starters ferment faster so every 5 days.")
        assert "Once a week is usually enough." not in [text for _, text in _texts(conversation)]
    assert any("newest reply" in warning for warning in scan.warnings)


def test_chatgpt_custom_instructions_become_deduplicated_instruction_facts_latest_wins(tmp_path) -> None:
    older = chatgpt_conversation("old", "Old", 1700000000.0, profile="I teach chemistry in Leeds.",
                                 instructions="Use imperial units.")
    newer = chatgpt_conversation("new", "New", 1762934400.0, profile="I teach chemistry in Leeds.",
                                 instructions="Use metric units. Keep answers short.")
    same_again = chatgpt_conversation("again", "Again", 1750000000.0, profile="I teach chemistry in Leeds.",
                                      instructions="Use imperial units.")
    export = write_zip(tmp_path / "export.zip", {"conversations.json": [newer, older, same_again]})

    scan = scan_files([export])

    facts = {fact.text: fact for fact in scan.facts}
    assert set(facts) == {"I teach chemistry in Leeds.", "Use metric units.", "Keep answers short."}
    assert facts["Use metric units."].category == "preference"
    assert "instruction" in facts["Use metric units."].tags
    assert "instruction" in facts["I teach chemistry in Leeds."].tags
    assert facts["Use metric units."].date == "2025-11-12"
    assert facts["Use metric units."].source == "chatgpt"
    assert facts["Use metric units."].source_label == "ChatGPT custom instructions"
    again = scan_files([export])
    assert [fact.source_ref for fact in again.facts] == [fact.source_ref for fact in scan.facts]


def test_chatgpt_shards_across_parts_merge_in_order_and_missing_shards_are_named(tmp_path) -> None:
    manifest = {"version": 1, "logical_files": {"conversations.json": {
        "files": ["conversations-000.json", "conversations-001.json", "conversations-002.json"],
        "sharded": True, "shard_count": 3}}}
    part_one = write_zip(tmp_path / "chatgpt-part-0001.zip", {
        "export_manifest.json": manifest,
        "conversations-001.json": [chatgpt_simple("b", 1762000100.0, "second shard")],
        "conversations-000.json": [chatgpt_simple("a", 1762000000.0, "first shard"),
                                   chatgpt_simple("b", 1762000100.0, "duplicate of b")],
    })
    part_two = write_zip(tmp_path / "chatgpt-part-0002.zip", {"file_ab12-scan.dat": b"%PDF-1.7"})

    scan = scan_files([part_one, part_two])
    conversations = list(scan.iter_conversations())

    assert [conversation.id for conversation in conversations] == ["a", "b"]
    assert conversations[1].messages[0].text == "duplicate of b"  # shard 000 is read before 001
    assert scan.counts["chatgpt"].conversations == 2
    assert any("conversations-002.json" in warning for warning in scan.warnings)


def test_chatgpt_shards_picked_as_loose_files_merge_too(tmp_path) -> None:
    first = tmp_path / "conversations-000.json"
    second = tmp_path / "conversations-001.json"
    first.write_text(json.dumps([chatgpt_simple("one", 1762000000.0, "loose one")]), encoding="utf-8")
    second.write_text(json.dumps([chatgpt_simple("two", 1762000100.0, "loose two")]), encoding="utf-8")

    scan = scan_files([second, first])

    assert [conversation.id for conversation in scan.iter_conversations()] == ["one", "two"]
    assert scan.conversation_count == 2


def test_chatgpt_image_parts_become_placeholders_and_nothing_is_fetched(tmp_path) -> None:
    conversation = chatgpt_simple("img", 1762000000.0, "What plant is this?")
    conversation["mapping"]["q"]["message"]["content"] = {
        "content_type": "multimodal_text",
        "parts": [{"content_type": "image_asset_pointer", "asset_pointer": "sediment://file_00ab"},
                  "What plant is this?"]}
    export = write_zip(tmp_path / "export.zip", {"conversations.json": [conversation]})

    [result] = list(scan_files([export]).iter_conversations())

    assert result.messages[0].text == "[Image not included in the export]\nWhat plant is this?"
