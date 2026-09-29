"""Settings › Tools › Custom tools: the whole library, add from a folder, approvals (Phase 12, parity rows 33/34)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from row_bot.application.client_platform import ClientPlatformError
from row_bot.developer import client_custom_tool_library as library
from row_bot.developer import tool_capsules as capsules
from row_bot.developer.runtime import CommandResult
from row_bot.developer.sandbox import ApprovalDecision

pytestmark = pytest.mark.subsystem


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(capsules, "CAPSULES_PATH", tmp_path / "capsules.json")
    monkeypatch.setattr(capsules, "CUSTOM_TOOL_DRAFTS_PATH", tmp_path / "drafts.json")
    records: dict[str, dict] = {}

    def metadata(_owner, command_id):
        row = records.get(command_id)
        return {"target": row["target"], "type": row["wire"]["type"]} if row else None

    def claim(_owner, key, wire, target, **_kwargs):
        row = records.get(key)
        if row:
            assert row["wire"] == wire and row["target"] == target
            return row["result"]
        records[key] = {"target": target, "wire": wire, "result": None}
        return None

    def complete(_owner, key, result):
        records[key]["result"] = result
        return result

    monkeypatch.setattr(library.admissions, "read_command_metadata", metadata)
    monkeypatch.setattr(library.admissions, "claim_command", claim)
    monkeypatch.setattr(library.admissions, "complete_command", complete)
    cache = []
    monkeypatch.setattr(library, "_clear_agent_cache", lambda: cache.append(1))
    folder = tmp_path / "weather-cli"
    folder.mkdir()
    (folder / "weather.py").write_text("print('sunny')\n", encoding="utf-8")

    def propose(path: str, *, source_url: str, use_ai: bool):
        return capsules.CapsuleManifestProposal(
            "Weather", "1.0.0", source_url, path,
            [{"name": "Today", "description": "Today's weather", "command": "python weather.py"},
             {"name": "Fetch", "description": "Download data", "command": "curl https://example.invalid/data"}],
        )

    monkeypatch.setattr(capsules, "propose_capsule_manifest", propose)
    return folder, records, cache


def run(snapshot, action, folder=None, **payload):
    return library.execute_custom_tool_library(
        {"command_id": str(uuid4()), "revision": snapshot["revision"], "action": action, "payload": payload},
        owner_id="owner", validate=lambda: None, folder=folder, approval_mode="ask")


def read():
    return library.read_custom_tool_library(validate=lambda: None)


def test_add_from_a_folder_then_create_lists_it_with_its_folder(store):
    folder, _records, _cache = store
    empty = read()
    assert empty["tools"] == [] and empty["drafts"] == []
    with pytest.raises(ClientPlatformError, match="invalid_custom_tool_command"):
        run(empty, "inspect")
    inspected = run(empty, "inspect", folder=folder)
    draft = inspected["snapshot"]["drafts"][0]
    assert draft["name"] == "Weather" and draft["folder"] == "weather-cli"
    created = run(inspected["snapshot"], "create", draft_id=draft["id"])
    tool = created["snapshot"]["tools"][0]
    assert tool["name"] == "Weather" and tool["folder"] == "weather-cli"
    assert tool["draft_id"] == draft["id"] and tool["enabled"] is False
    # A created tool leaves the drafts list: one row per tool.
    assert created["snapshot"]["drafts"] == []


def test_switching_on_off_and_into_chat_refreshes_the_agent(store):
    folder, _records, cache = store
    snapshot = run(read(), "inspect", folder=folder)["snapshot"]
    snapshot = run(snapshot, "create", draft_id=snapshot["drafts"][0]["id"])["snapshot"]
    tool_id = snapshot["tools"][0]["id"]
    on = run(snapshot, "enable", tool_id=tool_id, enabled=True)
    assert on["snapshot"]["tools"][0]["enabled"] is True and on["summary"] == "Weather is on."
    off = run(on["snapshot"], "enable", tool_id=tool_id, enabled=False)
    assert off["snapshot"]["tools"][0]["enabled"] is False
    chat = run(off["snapshot"], "promote", tool_id=tool_id)
    assert chat["snapshot"]["tools"][0]["available_in_chat"] is True
    assert len(cache) == 3


def test_a_command_that_needs_approval_runs_only_after_that_exact_approval(store, monkeypatch):
    folder, _records, _cache = store
    snapshot = run(read(), "inspect", folder=folder)["snapshot"]
    snapshot = run(snapshot, "create", draft_id=snapshot["drafts"][0]["id"])["snapshot"]
    tool_id = snapshot["tools"][0]["id"]
    ran = []

    def fake_run(tool, command, *, approved_once=False, approval_mode=None, **_kwargs):
        ran.append((tool, command, approved_once))
        return CommandResult(command=command, cwd=str(folder), returncode=0, stdout="data",
                             stderr="", decision=ApprovalDecision("allow", "ok"))

    monkeypatch.setattr(capsules, "run_custom_tool_test_command", fake_run)
    asked = run(snapshot, "test", tool_id=tool_id, command_name="Fetch")
    assert asked["status"] == "approval_required" and ran == []
    approval = asked["approval"]
    assert approval["command"] == "curl https://example.invalid/data"
    assert approval["reason"] == "It uses the network."
    # A forged or reused approval for another command is not an approval.
    refused = run(asked["snapshot"], "test", tool_id=tool_id, command_name="Fetch", approval_nonce="0" * 64)
    assert refused["status"] == "approval_required" and ran == []
    other = run(asked["snapshot"], "test", tool_id=tool_id, command_name="Today",
                approval_nonce=approval["nonce"])
    assert other["status"] == "completed" and ran[-1][2] is False  # a local command needs no approval
    approved = run(asked["snapshot"], "test", tool_id=tool_id, command_name="Fetch",
                   approval_nonce=approval["nonce"])
    assert approved["status"] == "completed" and approved["test"]["ok"] is True
    assert ran[-1] == (tool_id, "curl https://example.invalid/data", True)


def test_draft_tests_use_the_same_approval(store, monkeypatch):
    folder, _records, _cache = store
    snapshot = run(read(), "inspect", folder=folder)["snapshot"]
    draft_id = snapshot["drafts"][0]["id"]
    calls = []

    def fake_draft_test(draft, *, command_name, approval_mode, query, approved_once):
        calls.append((draft, command_name, approved_once))
        return CommandResult(command="curl", cwd=str(folder), returncode=0, stdout="",
                             stderr="", decision=ApprovalDecision("allow", "ok"))

    monkeypatch.setattr(capsules, "test_custom_tool_draft_command", fake_draft_test)
    asked = run(snapshot, "test", draft_id=draft_id, command_name="Fetch")
    assert asked["status"] == "approval_required" and calls == []
    done = run(asked["snapshot"], "test", draft_id=draft_id, command_name="Fetch",
               approval_nonce=asked["approval"]["nonce"])
    assert done["status"] == "completed" and calls == [(draft_id, "Fetch", True)]


def test_remove_forgets_the_tool_and_its_draft_but_keeps_the_files(store):
    folder, _records, cache = store
    snapshot = run(read(), "inspect", folder=folder)["snapshot"]
    snapshot = run(snapshot, "create", draft_id=snapshot["drafts"][0]["id"])["snapshot"]
    removed = run(snapshot, "remove", tool_id=snapshot["tools"][0]["id"])
    assert removed["snapshot"]["tools"] == [] and removed["snapshot"]["drafts"] == []
    assert removed["summary"] == "Removed Weather. Its files stay in the folder."
    assert (folder / "weather.py").is_file() and cache


def test_stale_lists_and_mixed_targets_are_refused(store):
    folder, _records, _cache = store
    before = read()
    run(before, "inspect", folder=folder)
    with pytest.raises(ClientPlatformError, match="custom_tool_revision_conflict"):
        run(before, "inspect", folder=folder)
    now = read()
    with pytest.raises(ClientPlatformError, match="invalid_custom_tool_command"):
        run(now, "remove", tool_id="t", draft_id="d")
    with pytest.raises(ClientPlatformError, match="custom_tool_unavailable"):
        run(now, "enable", tool_id="missing")


def test_a_store_refusal_is_a_plain_finished_failure(store, monkeypatch):
    folder, _records, _cache = store
    snapshot = run(read(), "inspect", folder=folder)["snapshot"]
    draft_id = snapshot["drafts"][0]["id"]
    # Not created yet: switching it on is refused by the store.
    refused = run(snapshot, "enable", draft_id=draft_id, enabled=True)
    assert refused["status"] == "failed"
    assert refused["summary"] == "That didn't work. Check the tool and try again."
    assert refused["snapshot"]["drafts"][0]["id"] == draft_id
