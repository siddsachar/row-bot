"""Import memory end to end: files or a pasted answer, a scan, an optional look through past
conversations, a preview, Apply and Undo. Nothing is written to memory before Apply."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from row_bot.application.client_platform import ClientPlatformError
from tests.fixtures.memory_exports import chatgpt_conversation, chatgpt_simple, zip_bytes
from tests.helpers.memory_import import settle

pytestmark = pytest.mark.subsystem

SESSION = "session-a"
ANSWER = """```
category | date | fact
preference | 2026-10-01 | Recordings of the app are made in light theme
project | unknown | Is building a greenhouse from recycled windows
skill | unknown | Plays the cello in a community orchestra
identity | unknown | Lives in a canal boat near Leeds
```
This is the complete set."""


def _upload(service, batch_id: str, name: str, data: bytes) -> str:
    view = service.add_file(SESSION, batch_id, name=name, size_bytes=len(data))
    for offset in range(0, len(data), service.CHUNK_BYTES):
        view = service.write_chunk(SESSION, batch_id, view["file_id"], offset,
                                   data[offset:offset + service.CHUNK_BYTES])
    assert view["received_bytes"] == len(data)
    return view["file_id"]


def _scanned(service, *, text: str | None = ANSWER, files: dict[str, bytes] | None = None) -> str:
    batch_id = service.begin(SESSION)["batch_id"]
    for name, data in (files or {}).items():
        _upload(service, batch_id, name, data)
    if text:
        service.add_text(SESSION, batch_id, text)
    service.scan(SESSION, batch_id)
    settle()
    view = service.status(SESSION, batch_id)
    assert view["job"]["status"] == "completed", view["job"]
    return batch_id


def _previewed(service, batch_id: str, edits: dict[str, dict] | None = None) -> dict:
    items = [{"candidate_id": item["candidate_id"], **(edits or {}).get(item["text"], {})}
             for item in service.candidates(SESSION, batch_id)["items"]]
    service.preview(SESSION, batch_id, items)
    settle()
    view = service.status(SESSION, batch_id)
    assert view["job"]["status"] == "completed", view["job"]
    return service.read_preview(SESSION, batch_id)


def _memory(stack) -> list[dict]:
    return sorted(({"id": entity["id"], "subject": entity["subject"], "text": entity["description"],
                    "properties": entity.get("properties")} for entity in stack.kg.list_entities(limit=500)),
                  key=lambda row: row["id"])


def _status(entity: dict) -> str:
    return json.loads(entity.get("properties") or "{}").get("status", "active")


def test_a_pasted_answer_is_previewed_applied_and_undone_with_nothing_saved_before_apply(stack) -> None:
    kg, service = stack.kg, stack.service
    dark = kg.save_entity("preference", "App recordings theme", "Recordings of the app are made in dark theme.")
    stack.close[dark["id"]] = 0.85
    before, journal_before = _memory(stack), stack.evolution.get_journal(200)

    batch_id = _scanned(service)
    found = service.candidates(SESSION, batch_id)["items"]
    assert [item["category"] for item in found] == ["preference", "project", "skill", "fact"]
    assert {item["source_label"] for item in found} == {"Pasted answer"}
    preview = _previewed(service, batch_id, {
        "Plays the cello in a community orchestra": {"text": "Plays the viola in a community orchestra"},
    })
    actions = {item["text"]: item for item in preview["items"]}
    replaced = actions["Recordings of the app are made in light theme"]
    assert replaced["action"] == "supersede" and replaced["saved_subject"] == "App recordings theme"
    assert replaced["subject"] == "Recording theme"
    assert actions["Plays the viola in a community orchestra"]["action"] == "add"  # the edited text is used
    # Scanning and previewing wrote nothing to memory, and nothing to its journal.
    assert _memory(stack) == before and stack.evolution.get_journal(200) == journal_before

    receipt = service.apply(SESSION, batch_id, command_id="c0ffee00-0000-4000-8000-000000000001",
                            revision=preview["revision"],
                            exclude=[actions["Lives in a canal boat near Leeds"]["candidate_id"]])
    assert (receipt["added"], receipt["superseded"], receipt["updated"], receipt["skipped"]) == (2, 1, 0, 0)
    assert receipt["sources"] == ["a pasted answer"] and receipt["status"] == "applied"
    saved = {entity["description"]: entity for entity in kg.list_entities(limit=50)}
    assert "Lives in a canal boat near Leeds" not in saved  # unchecked in the preview
    light = saved["Recordings of the app are made in light theme"]
    assert light["source"] == "import:pasted" and light["subject"] == "Recording theme"
    assert _status(kg.get_entity(dark["id"])) == "superseded"  # kept, as history
    assert [row["action"] for row in stack.evolution.get_journal(5)][-2:] == ["supersede", "import"]
    # Its uploads and pasted text are gone; the receipt stays for Undo.
    assert sorted(path.name for path in (stack.root / batch_id).iterdir()) == ["receipt.json"]

    again = service.apply(SESSION, batch_id, command_id="c0ffee00-0000-4000-8000-000000000002",
                          revision=preview["revision"], exclude=[])
    assert again == receipt and len(kg.list_entities(limit=50)) == 4  # applied once

    undone = service.undo(batch_id)
    assert undone["status"] == "undone"
    assert [row["text"] for row in _memory(stack)] == [dark["description"]]
    assert _status(kg.get_entity(dark["id"])) == "active"
    assert service.undo(batch_id) == undone
    assert stack.evolution.get_journal(1)[0]["action"] == "import_undo"


def test_an_update_and_the_persons_own_record_are_restored_by_undo_never_deleted(stack) -> None:
    kg, service = stack.kg, stack.service
    user = kg.save_entity("person", "User", "The person Row-Bot works for.")
    greenhouse = kg.save_entity("project", "Greenhouse", "Is building a greenhouse")
    stack.close[greenhouse["id"]] = 0.85
    batch_id = _scanned(service, text="```\nidentity | unknown | Lives in a canal boat near Leeds\n"
                                      "project | unknown | Is building a greenhouse from recycled windows\n```")
    preview = _previewed(service, batch_id)
    by_text = {item["text"]: item for item in preview["items"]}
    # The fact adds to a saved memory: it is rewritten, and Undo puts back what it said.
    grown = by_text["Is building a greenhouse from recycled windows"]
    assert grown["action"] == "update" and grown["saved_subject"] == "Greenhouse"

    service.apply(SESSION, batch_id, command_id="c0ffee00-0000-4000-8000-000000000003",
                  revision=preview["revision"], exclude=[])
    assert kg.get_entity(greenhouse["id"])["description"] == "Is building a greenhouse from recycled windows"
    assert "canal boat" in kg.get_entity(user["id"])["description"]

    service.undo(batch_id)
    assert kg.get_entity(user["id"])["description"] == "The person Row-Bot works for."
    assert kg.get_entity(greenhouse["id"])["description"] == "Is building a greenhouse"
    assert len(kg.list_entities(limit=50)) == 2


def test_a_memory_changed_after_the_preview_asks_for_a_new_preview_and_nothing_is_written(stack) -> None:
    kg, service = stack.kg, stack.service
    dark = kg.save_entity("preference", "App recordings theme", "Recordings of the app are made in dark theme.")
    stack.close[dark["id"]] = 0.85
    batch_id = _scanned(service)
    preview = _previewed(service, batch_id)
    kg.update_entity(dark["id"], "Recordings of the app are made in dark theme, at 1080p.")

    with pytest.raises(ClientPlatformError) as refused:
        service.apply(SESSION, batch_id, command_id="c0ffee00-0000-4000-8000-000000000004",
                      revision=preview["revision"], exclude=[])
    assert refused.value.code == "import_preview_stale"
    assert len(kg.list_entities(limit=50)) == 1


def test_an_export_scan_counts_conversations_and_skips_account_files(stack) -> None:
    service = stack.service
    export = zip_bytes({
        "conversations.json": [chatgpt_conversation("conv-1", "Sourdough starter schedule", 1762934400.0,
                                                    profile="Bakes bread every Sunday",
                                                    instructions="Answer in metric units")],
        "user.json": {"id": "user-x", "email": "someone@example.invalid"},
    })
    batch_id = _scanned(service, text=None, files={"chatgpt-export.zip": export})
    view = service.status(SESSION, batch_id)
    assert view["sources"] == [{"source": "chatgpt", "label": "ChatGPT", "facts": view["sources"][0]["facts"],
                                "conversations": 1}]
    assert view["conversations"]["available"] == 1
    assert any("never imported" in item["reason"] for item in view["skipped"])
    texts = [item["text"] for item in service.candidates(SESSION, batch_id)["items"]]
    assert texts and all("someone@example.invalid" not in text for text in texts)


def test_finding_memories_in_conversations_reads_only_the_persons_newest_messages_within_bounds(stack,
                                                                                                monkeypatch):
    service = stack.service
    monkeypatch.setattr(service, "CONVERSATION_LIMIT", 3)
    chats = [chatgpt_simple(f"c{number}", 1762900000.0 + number * 100, f"question {number} about lentils")
             for number in range(5)]
    batch_id = _scanned(service, text=None, files={"export.zip": zip_bytes({"conversations.json": chats})})
    before = _memory(stack)
    assert service.status(SESSION, batch_id)["conversations"]["limit"] == 3

    service.find_in_conversations(SESSION, batch_id)
    settle()
    view = service.status(SESSION, batch_id)
    assert view["job"]["status"] == "completed"
    assert view["conversations"] | {"available": 5} == {"ran": True, "read": 3, "found": 3, "available": 5,
                                                       "characters": view["conversations"]["characters"],
                                                       "limit": 3, "character_limit": service.CHARACTER_LIMIT}
    sent = "\n".join(stack.model.extraction_prompts)
    assert "question 4" in sent and "question 2" in sent and "question 1" not in sent  # newest first
    assert "Answer to" not in sent and "Assistant:" not in sent  # only the person's own words
    found = [item for item in service.candidates(SESSION, batch_id)["items"] if item["origin"] == "conversation"]
    assert {item["source_label"] for item in found} == {"ChatGPT · Chat c4", "ChatGPT · Chat c3",
                                                        "ChatGPT · Chat c2"}
    assert _memory(stack) == before  # found, not saved


def test_finding_memories_stops_at_the_character_bound(stack, monkeypatch):
    service = stack.service
    monkeypatch.setattr(service, "CHARACTER_LIMIT", 9_000)
    chats = [chatgpt_simple(f"c{number}", 1762900000.0 + number * 100, f"long {number} " + "x" * 5_000)
             for number in range(3)]
    batch_id = _scanned(service, text=None, files={"export.zip": zip_bytes({"conversations.json": chats})})

    service.find_in_conversations(SESSION, batch_id)
    settle()
    conversations = service.status(SESSION, batch_id)["conversations"]
    assert conversations["characters"] == 9_000 and conversations["read"] == 2
    assert "long 0" not in "\n".join(stack.model.extraction_prompts)


def test_finding_memories_needs_a_model_and_says_when_it_does_not_answer(stack, monkeypatch):
    service = stack.service
    chats = [chatgpt_simple(f"c{number}", 1762900000.0 + number, f"question {number}") for number in range(3)]
    batch_id = _scanned(service, text=None, files={"export.zip": zip_bytes({"conversations.json": chats})})
    stack.model.fail = True
    service.find_in_conversations(SESSION, batch_id)
    settle()
    assert service.status(SESSION, batch_id)["job"] | {"done": 0} == {
        "kind": "conversations", "status": "failed", "code": "import_model_unavailable", "done": 0, "total": 3}

    monkeypatch.setattr(service, "_model", lambda: None)
    with pytest.raises(ClientPlatformError) as refused:
        service.find_in_conversations(SESSION, batch_id)
    assert refused.value.code == "model_selection_required"


def test_a_preview_the_model_can_not_answer_fails_with_nothing_written(stack) -> None:
    kg, service = stack.kg, stack.service
    dark = kg.save_entity("preference", "App recordings theme", "Recordings of the app are made in dark theme.")
    stack.close[dark["id"]] = 0.85
    batch_id = _scanned(service)
    stack.model.fail = True
    items = [{"candidate_id": item["candidate_id"]} for item in service.candidates(SESSION, batch_id)["items"]]
    service.preview(SESSION, batch_id, items)
    settle()
    assert service.status(SESSION, batch_id)["job"]["code"] == "import_model_unavailable"
    with pytest.raises(ClientPlatformError):
        service.read_preview(SESSION, batch_id)
    assert len(kg.list_entities(limit=50)) == 1


def test_uploads_are_in_order_bounded_and_belong_to_their_window(stack, monkeypatch) -> None:
    service = stack.service
    batch_id = service.begin(SESSION)["batch_id"]
    with pytest.raises(ClientPlatformError) as huge:
        service.add_file(SESSION, batch_id, name="export.zip", size_bytes=service.FILE_MAX_BYTES + 1)
    assert huge.value.code == "import_file_too_large"
    with monkeypatch.context() as patch:
        patch.setattr(service.shutil, "disk_usage", lambda folder: SimpleNamespace(free=10))
        with pytest.raises(ClientPlatformError) as full:
            service.add_file(SESSION, batch_id, name="export.zip", size_bytes=5)
    assert full.value.code == "import_storage_full"
    for name in ("../escape.zip", "a\\b.zip", ""):
        with pytest.raises(ClientPlatformError):
            service.add_file(SESSION, batch_id, name=name, size_bytes=5)

    data = b"x" * (service.CHUNK_BYTES + 10)
    file_id = service.add_file(SESSION, batch_id, name="export part 1.zip", size_bytes=len(data))["file_id"]
    with pytest.raises(ClientPlatformError):
        service.write_chunk(SESSION, batch_id, file_id, service.CHUNK_BYTES, data[service.CHUNK_BYTES:])
    first = data[:service.CHUNK_BYTES]
    service.write_chunk(SESSION, batch_id, file_id, 0, first)
    assert service.write_chunk(SESSION, batch_id, file_id, 0, first)["received_bytes"] == len(first)  # a retry
    with pytest.raises(ClientPlatformError) as other:
        service.file_status("session-b", batch_id, file_id)
    assert other.value.code == "not_found"
    with pytest.raises(ClientPlatformError) as early:
        service.scan(SESSION, batch_id)
    assert early.value.code == "upload_incomplete"
    service.write_chunk(SESSION, batch_id, file_id, service.CHUNK_BYTES, data[service.CHUNK_BYTES:])
    assert (stack.root / batch_id / "files" / file_id / "export part 1.zip").stat().st_size == len(data)


def test_discarded_abandoned_and_old_batches_leave_nothing_behind(stack, monkeypatch) -> None:
    service = stack.service
    clock = [1_000_000.0]
    monkeypatch.setattr(service, "_now", lambda: clock[0])

    discarded = _scanned(service)
    service.discard(SESSION, discarded)
    assert not (stack.root / discarded).exists()

    abandoned = _scanned(service)
    clock[0] += service.IDLE_SECONDS + 1
    service.sweep()
    assert not (stack.root / abandoned).exists()
    with pytest.raises(ClientPlatformError) as gone:
        service.status(SESSION, abandoned)
    assert gone.value.code == "import_expired"

    applied = _scanned(service, text="```\nskill | unknown | Juggles three clubs\n```")
    preview = _previewed(service, applied)
    service.apply(SESSION, applied, command_id="c0ffee00-0000-4000-8000-000000000005",
                  revision=preview["revision"], exclude=[])
    leftover = stack.root / ("0" * 32)
    (leftover / "files").mkdir(parents=True)
    (leftover / "files" / "from-before.zip").write_bytes(b"an earlier run")
    service.close_batches()  # the server stops: an applied import keeps its receipt
    assert [item["batch_id"] for item in service.recent()] == [applied]
    assert not leftover.exists()  # never applied, from an earlier run
    clock[0] += service.KEEP_SECONDS + 1
    assert service.recent() == []
    assert not (stack.root / applied).exists()
    with pytest.raises(ClientPlatformError):
        service.undo(applied)


def test_a_cancelled_step_lets_go_of_what_it_found(stack, monkeypatch) -> None:
    service = stack.service
    batch_id = service.begin(SESSION)["batch_id"]
    service.add_text(SESSION, batch_id, ANSWER)
    started = threading.Event()
    release = threading.Event()
    real = service._scans

    def slow(batch):
        started.set()
        release.wait(10)
        return real(batch)

    monkeypatch.setattr(service, "_scans", slow)
    service.scan(SESSION, batch_id)
    started.wait(10)
    assert service.status(SESSION, batch_id)["job"]["status"] == "running"
    assert service.cancel_job(SESSION, batch_id)["job"]["status"] == "cancelled"
    release.set()
    settle()
    view = service.status(SESSION, batch_id)
    assert view["job"]["status"] == "cancelled" and view["found"] == 0 and not view["scanned"]
