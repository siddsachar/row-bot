from __future__ import annotations

import json

import pytest

from row_bot.memory_import import scan_files
from tests.fixtures.memory_exports import (
    claude_conversation,
    claude_memories_new,
    claude_memories_old,
    claude_message,
    write_zip,
)


pytestmark = pytest.mark.subsystem


def test_claude_zip_gives_memory_facts_and_the_newest_branch_of_each_chat(tmp_path) -> None:
    export = write_zip(tmp_path / "data-2026-10-03-batch-0000.zip", {
        "conversations.json": [claude_conversation()],
        "memories.json": claude_memories_old(),
        "users.json": [{"uuid": "u1", "full_name": "Test Person", "email_address": "person@example.invalid"}],
        "projects.json": [{"uuid": "p1", "name": "Garden", "prompt_template": "Be brief", "docs": []}],
    })

    scan = scan_files([export])

    [conversation] = list(scan.iter_conversations())
    assert [(m.role, m.text) for m in conversation.messages] == [
        ("user", "What can I sow in October?"),
        ("assistant", "Broad beans and garlic."),
        ("user", "And in a cold frame?"),
        ("assistant", "Lettuce and spinach do well."),
    ]
    assert conversation.title == "Allotment planting plan"
    facts = {fact.text: fact for fact in scan.facts}
    assert {"The user is a nurse who works night shifts.", "They mentor two students.",
            "Lives in Porto with a partner", "Prefers metric units", "Planning an allotment",
            "Planning a community garden; budget 400 pounds."} <= set(facts)
    assert facts["Planning an allotment"].category == "project"
    assert facts["Planning a community garden; budget 400 pounds."].category == "project"
    assert all(fact.source == "claude" and fact.source_label == "Claude memory" for fact in scan.facts)
    assert scan.counts["claude"].facts == len(scan.facts)
    skipped = {item.name.rsplit(" › ", 1)[-1]: item.reason for item in scan.skipped}
    assert "never imported" in skipped["users.json"]
    assert "not memories" in skipped["projects.json"]
    assert all("Injected memory context" not in m.text for m in conversation.messages)


def test_claude_per_category_zips_with_memory_files_merge(tmp_path) -> None:
    conversations = write_zip(tmp_path / "conversations-000.zip", {"conversations.json": [claude_conversation()]})
    memories = write_zip(tmp_path / "memories-000.zip", {
        "memories/9f8e7d6c-0000-4000-8000-123456789abc.json": claude_memories_new(),
    })
    metadata = write_zip(tmp_path / "light_metadata-000.zip", {
        "users.json": [{"uuid": "u1", "email_address": "person@example.invalid"}],
        "login_history.json": [{"ip_address": "192.0.2.1", "created_at": "2026-10-01T00:00:00Z"}],
    })

    scan = scan_files([conversations, memories, metadata])

    facts = {fact.text: fact for fact in scan.facts}
    assert set(facts) == {"Keeps bees on the allotment", "Short answers first, detail on request", "British spelling"}
    assert facts["British spelling"].category == "preference"
    assert facts["British spelling"].date == "2026-10-20"
    assert scan.conversation_count == 1
    assert len([item for item in scan.skipped if "never imported" in item.reason]) == 2


def test_claude_manifest_lists_the_linked_zips_and_downloads_nothing(tmp_path) -> None:
    manifest = tmp_path / "manifest-9f8e-1760000000-ab12-20261001.json"
    manifest.write_text(json.dumps({
        "version": "1.0", "instructions": "Download each file", "created_at": "2026-10-01T00:00:00Z",
        "total_files": 2,
        "data_files": [
            {"batch_index": 0, "export_url": "https://downloads.example.invalid/c", "category": "conversations",
             "part": 0, "filename": "conversations-000.zip"},
            {"batch_index": 0, "export_url": "https://downloads.example.invalid/m", "category": "memories",
             "part": 0, "filename": "memories-000.zip"},
        ],
    }), encoding="utf-8")

    scan = scan_files([manifest])

    assert scan.facts == [] and scan.conversation_count == 0
    assert {item.name for item in scan.skipped} == {"conversations-000.zip", "memories-000.zip"}
    assert any("download" in item.reason.lower() for item in scan.skipped)
    assert any("email link" in warning for warning in scan.warnings)


def test_claude_messages_without_parents_follow_their_times(tmp_path) -> None:
    conversation = claude_conversation()
    conversation["chat_messages"] = [
        claude_message("b", "assistant", "Second", "2026-01-01T10:00:05Z", parent=None),
        claude_message("a", "human", "First", "2026-01-01T10:00:00Z", parent=None),
    ]
    export = write_zip(tmp_path / "export.zip", {"conversations.json": [conversation]})

    [result] = list(scan_files([export]).iter_conversations())

    assert [message.text for message in result.messages] == ["First", "Second"]
