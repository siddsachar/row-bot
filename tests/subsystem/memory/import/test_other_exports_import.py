from __future__ import annotations

import io
import json
import tarfile

import pytest

from row_bot.memory_import import scan_files
from tests.fixtures.memory_exports import (
    deepseek_conversations,
    gemini_record,
    gzip_lines,
    grok_backend,
    letta_agent_file,
    write_zip,
)


pytestmark = pytest.mark.subsystem


def _pairs(conversation):
    return [(message.role, message.text) for message in conversation.messages]


def test_gemini_takeout_groups_prompts_by_chat_link_and_turns_html_answers_into_text(tmp_path) -> None:
    records = [  # Takeout lists the newest first
        gemini_record("And for day 2?", "<p>Day 2: Belém.</p>", "2026-10-28T18:10:00.000Z", "0a1b2c3d4e5f6a7b"),
        gemini_record("Eine Frage", "<p>Eine Antwort &amp; mehr</p>", "2026-10-27T09:00:00.000Z", "ffff0000ffff0000",
                      header="Gemini-Apps", title_prefix="Eingegebener Prompt: "),
        gemini_record("Suggest a 3-day itinerary for Lisbon",
                      "<p>Day 1: Alfama and the castle.</p><ul><li>Tram 28</li><li>Miradouro</li></ul>"
                      "<script>ignored()</script>", "2026-10-28T18:04:11.532Z", "0a1b2c3d4e5f6a7b"),
        {"header": "Search", "title": "Searched for weather", "time": "2026-10-28T10:00:00Z",
         "products": ["Search"], "activityControls": ["Web & App Activity"]},
        {"header": "Gemini Apps", "title": "Gave feedback: thumbs up", "time": "2026-10-28T18:05:00Z",
         "products": ["Gemini Apps"], "activityControls": ["Gemini Apps Activity"]},
    ]
    export = write_zip(tmp_path / "takeout-20261101T101500Z-001.zip", {
        "Takeout/My Activity/Gemini Apps/MyActivity.json": records,
        "Takeout/My Activity/Gemini Apps/upload.png": b"\x89PNG",
    })

    scan = scan_files([export])
    chats = {conversation.id: conversation for conversation in scan.iter_conversations()}

    lisbon = chats["0a1b2c3d4e5f6a7b"]
    assert _pairs(lisbon) == [
        ("user", "Suggest a 3-day itinerary for Lisbon"),
        ("assistant", "Day 1: Alfama and the castle.\n\n- Tram 28\n- Miradouro"),
        ("user", "And for day 2?"),
        ("assistant", "Day 2: Belém."),
    ]
    assert lisbon.title == "Suggest a 3-day itinerary for Lisbon"
    assert _pairs(chats["ffff0000ffff0000"]) == [("user", "Eine Frage"), ("assistant", "Eine Antwort & mehr")]
    assert scan.counts["gemini"].conversations == 2
    assert any("not Gemini" in item.reason for item in scan.skipped)


def test_gemini_takeout_as_tgz_is_streamed(tmp_path) -> None:
    payload = json.dumps([gemini_record("Name a hardy herb", "<p>Rosemary.</p>", "2026-10-01T08:00:00Z",
                                        "1234abcd1234abcd")]).encode("utf-8")
    archive_path = tmp_path / "takeout-20261101T101500Z-001.tgz"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("Takeout/Meine Aktivitäten/Gemini-Apps/MyActivity.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    [conversation] = list(scan_files([archive_path]).iter_conversations())

    assert _pairs(conversation) == [("user", "Name a hardy herb"), ("assistant", "Rosemary.")]


def test_gemini_html_activity_asks_for_a_json_export(tmp_path) -> None:
    page = tmp_path / "MyActivity.html"
    page.write_text("<!DOCTYPE html><html><body><div>Prompted hello</div></body></html>", encoding="utf-8")

    scan = scan_files([page])

    assert scan.facts == [] and scan.conversation_count == 0
    assert any("JSON" in warning for warning in scan.warnings)


def test_copilot_csv_is_reversed_not_sorted_and_keeps_multiline_messages(tmp_path) -> None:
    rows = [
        "Conversation,Time,Author,Message",
        '"Fixing a squeaky door",2026-07-26T23:21:30,AI,"Then wipe off the extra."',
        '"Fixing a squeaky door",2026-07-26T23:21:30,Human,"how much oil?"',
        '"Fixing a squeaky door",2026-07-26T23:20:56,AI,"**Short answer:** a drop of oil on the hinge pin.\nRepeat '
        'if needed."',
        '"Fixing a squeaky door",2026-07-26T23:20:56,Human,"my door squeaks, what do I do?"',
        '"Packing list",2026-07-20T08:00:00,Human,"what should I pack for a weekend?"',
    ]
    csv_file = tmp_path / "copilot-activity-history.csv"
    csv_file.write_bytes(b"\xef\xbb\xbf" + "\r\n".join(rows).encode("utf-8") + b"\r\n")

    scan = scan_files([csv_file])
    chats = {conversation.title: conversation for conversation in scan.iter_conversations()}

    assert _pairs(chats["Fixing a squeaky door"]) == [
        ("user", "my door squeaks, what do I do?"),
        ("assistant", "**Short answer:** a drop of oil on the hinge pin.\nRepeat if needed."),
        ("user", "how much oil?"),
        ("assistant", "Then wipe off the extra."),
    ]
    assert chats["Fixing a squeaky door"].messages[0].timestamp.tzinfo is not None
    assert scan.counts["copilot"].conversations == 2
    assert any("time zone" in warning for warning in scan.warnings)
    again = {conversation.title: conversation.id for conversation in scan_files([csv_file]).iter_conversations()}
    assert again == {title: conversation.id for title, conversation in chats.items()}


def test_perplexity_export_strips_citation_markers_and_skips_the_account_sheet(tmp_path) -> None:
    export = write_zip(tmp_path / "user_data_export_20260901_ab12.zip", {
        "conversations-20260901_101500-3fa9c2.json": {"conversations": [{
            "context_uuid": "5e0c0000", "context_title": "what is the boiling point of water at 2000 m altitude",
            "created_at": "2026-08-30T12:00:01Z", "updated_at": "2026-08-30T12:03:40Z", "mode": "CONCISE",
            "entries": [{"entry_uuid": "b71e", "query": "what is the boiling point of water at 2000 m altitude",
                         "answer": "About 93 °C [1][2] at that height.", "created_at": "2026-08-30T12:00:01Z",
                         "query_status": "COMPLETED"}]}]},
        "user-data-20260901.xlsx": b"PK\x03\x04 not really a spreadsheet",
    })

    scan = scan_files([export])
    [conversation] = list(scan.iter_conversations())

    assert conversation.id == "5e0c0000"
    assert _pairs(conversation)[1] == ("assistant", "About 93 °C at that height.")
    assert any(item.name.endswith(".xlsx") and "never imported" in item.reason for item in scan.skipped)


def test_grok_uses_sender_for_roles_puts_the_person_first_on_ties_and_skips_account_files(tmp_path) -> None:
    export = write_zip(tmp_path / "grok-export.zip", {
        "ttl/30d/export_data/u-1/prod-grok-backend.json": grok_backend(),
        "ttl/30d/export_data/u-1/prod-mc-auth-mgmt-api.json": {"email": "person@example.invalid"},
        "ttl/30d/export_data/u-1/prod-mc-billing.json": {"plan": "free"},
    })

    scan = scan_files([export])
    [conversation] = list(scan.iter_conversations())

    assert _pairs(conversation) == [
        ("user", "My chain slips under load. Why?"),
        ("assistant", "Usually a worn chain."),
    ]
    assert conversation.messages[0].timestamp.year == 2025
    assert conversation.created_at.isoformat().startswith("2026-09-10T07:31:02")
    assert any("Imagine" in item.reason for item in scan.skipped)
    assert len([item for item in scan.skipped if "never imported" in item.reason]) == 2


def test_deepseek_tree_uses_the_newest_leaf_and_fragment_roles(tmp_path) -> None:
    export = write_zip(tmp_path / "deepseek_data.zip", {
        "conversations.json": deepseek_conversations(),
        "user.json": {"id": "x", "email": "person@example.invalid"},
    })

    [conversation] = list(scan_files([export]).iter_conversations())

    assert conversation.provider == "deepseek"
    assert _pairs(conversation) == [
        ("user", "Why are my tomato leaves brown?"),
        ("assistant", "It looks like late blight; remove the leaves."),
    ]


def test_mem0_list_and_results_wrapper_skip_deleted_items(tmp_path) -> None:
    items = [
        {"id": "f4cb-1", "memory": "Is planning a trip to Lisbon from 1 to 10 March", "hash": "9b2c",
         "user_id": "alex", "categories": ["travel"], "created_at": "2026-02-01T12:00:00-08:00"},
        {"id": "f4cb-2", "memory": "Prefers window seats", "hash": "1a2b", "user_id": "alex",
         "categories": ["user_preferences"], "created_at": "2026-02-02T12:00:00Z"},
        {"id": "f4cb-3", "memory": "An old fact that was removed", "hash": "3c4d", "user_id": "alex",
         "state": "deleted"},
    ]
    bare = tmp_path / "mem0-all.json"
    bare.write_text(json.dumps(items), encoding="utf-8")
    wrapped = tmp_path / "mem0-results.json"
    wrapped.write_text(json.dumps({"results": [
        {"id": "f4cb-4", "memory": "Has a cat called Pixel", "user_id": "alex", "categories": ["family"]},
    ]}), encoding="utf-8")

    scan = scan_files([bare, wrapped])

    facts = {fact.text: fact for fact in scan.facts}
    assert set(facts) == {"Is planning a trip to Lisbon from 1 to 10 March", "Prefers window seats",
                          "Has a cat called Pixel"}
    assert facts["Is planning a trip to Lisbon from 1 to 10 March"].category == "event"
    assert facts["Is planning a trip to Lisbon from 1 to 10 March"].date == "2026-02-01"
    assert facts["Prefers window seats"].category == "preference"
    assert facts["Has a cat called Pixel"].category == "person"
    assert facts["Prefers window seats"].source_ref.startswith("mem0:f4cb-2")
    assert any("1 deleted" in item.reason for item in scan.skipped)


def test_openmemory_backup_reads_memories_json_once_and_tags_inactive_states(tmp_path) -> None:
    memories = {
        "user": {"id": "u1", "name": "Test Person", "email": "person@example.invalid"},
        "apps": [{"id": "app1", "name": "cursor"}],
        "categories": [{"id": "cat1", "name": "food"}],
        "memories": [
            {"id": "m1", "content": "Bakes sourdough on Sundays", "state": "active", "category_ids": ["cat1"],
             "created_at": "2026-05-01T00:00:00Z"},
            {"id": "m2", "content": "Used to commute by train", "state": "archived", "category_ids": []},
            {"id": "m3", "content": "Removed memory", "state": "deleted", "deleted_at": "2026-05-02T00:00:00Z"},
        ],
        "access_controls": [],
        "export_meta": {"version": "1", "generated_at": "2026-06-01T00:00:00Z"},
    }
    lines = gzip_lines([{"id": "m1", "content": "Bakes sourdough on Sundays", "state": "active"}])
    export = write_zip(tmp_path / "memories_export_u1.zip", {"memories.json": memories, "memories.jsonl.gz": lines})

    scan = scan_files([export])

    facts = {fact.text: fact for fact in scan.facts}
    assert set(facts) == {"Bakes sourdough on Sundays", "Used to commute by train"}
    assert facts["Bakes sourdough on Sundays"].category == "preference"
    assert facts["Used to commute by train"].tags == ("archived",)
    assert all("Test Person" not in fact.text for fact in scan.facts)
    assert any("same memories" in item.reason for item in scan.skipped)


def test_openmemory_lines_file_on_its_own(tmp_path) -> None:
    lines = tmp_path / "memories.jsonl.gz"
    lines.write_bytes(gzip_lines([
        {"id": "m1", "content": "Plays the cello", "state": "active", "categories": ["hobbies"]},
        {"id": "m2", "content": "Gone", "state": "deleted"},
    ]))

    scan = scan_files([lines])

    assert [(fact.text, fact.category) for fact in scan.facts] == [("Plays the cello", "preference")]


def test_letta_agent_file_imports_human_blocks_split_into_sentences_not_persona(tmp_path) -> None:
    agent_file = tmp_path / "helper.af"
    agent_file.write_text(json.dumps(letta_agent_file()), encoding="utf-8")

    scan = scan_files([agent_file])

    assert [fact.text for fact in scan.facts] == ["Name: Sam.", "Vegetarian.", "Lives in Bristol."]
    assert all(fact.source == "letta" and fact.subject_hint == "User" for fact in scan.facts)
    assert any("persona" in item.reason for item in scan.skipped)
    [conversation] = list(scan.iter_conversations())
    assert _pairs(conversation) == [("user", "Remember that I'm vegetarian."),
                                    ("assistant", "Noted, no meat in any recipe.")]
