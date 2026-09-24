"""The React builder uses the existing draft owner and bound workspace."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.application.client_platform import ClientPlatformError
from row_bot.developer import client_custom_tools as client
from row_bot.developer import tool_capsules as capsules


@pytest.fixture
def local_builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(capsules, "CAPSULES_PATH", tmp_path / "capsules.json")
    monkeypatch.setattr(capsules, "CUSTOM_TOOL_DRAFTS_PATH", tmp_path / "drafts.json")
    monkeypatch.setattr(
        client,
        "_scope",
        lambda resource, conversation, validate: (
            SimpleNamespace(path=str(workspace), repo_url=""),
            SimpleNamespace(binding_id="binding"),
            "revision",
            "ask",
        ),
    )
    records: dict[str, dict] = {}

    def metadata(_owner: str, command_id: str):
        row = records.get(command_id)
        return {"target": row["target"], "type": row["wire"]["type"]} if row else None

    def claim(_owner: str, key: str, wire: dict, target: str, **_kwargs):
        row = records.get(key)
        if row:
            if row["wire"] != wire or row["target"] != target:
                raise ValueError("idempotency_mismatch")
            return row["result"]
        records[key] = {"target": target, "wire": wire, "result": None}
        return None

    def complete(_owner: str, key: str, result: dict):
        records[key]["result"] = result
        return result

    monkeypatch.setattr(client.admissions, "read_command_metadata", metadata)
    monkeypatch.setattr(client.admissions, "claim_command", claim)
    monkeypatch.setattr(client.admissions, "complete_command", complete)
    return workspace, records


def _command(snapshot: dict, action: str, **payload):
    return {
        "command_id": str(uuid4()),
        "revision": snapshot["revision"],
        "action": action,
        "payload": payload,
    }


def test_inspect_is_click_only_and_replay_does_not_call_model(local_builder, monkeypatch):
    workspace, _records = local_builder
    calls = []

    def propose(path: str, *, source_url: str, use_ai: bool):
        calls.append((path, source_url, use_ai))
        return capsules.CapsuleManifestProposal(
            "Example", "1.0.0", source_url, path,
            [{"name": "Hello", "description": "Greeting", "command": "python hello.py"}],
        )

    monkeypatch.setattr(capsules, "propose_capsule_manifest", propose)
    before = client.read_custom_tools("resource", "conversation", validate=lambda: None)
    assert before["drafts"] == []
    assert calls == []
    command = _command(before, "inspect")
    receipt = client.execute_custom_tool(
        "resource", "conversation", command, owner_id="owner", validate=lambda: None
    )
    assert receipt["status"] == "completed"
    assert receipt["snapshot"]["drafts"][0]["name"] == "Example"
    assert calls == [(str(workspace), str(workspace), True)]
    assert client.execute_custom_tool(
        "resource", "conversation", command, owner_id="owner", validate=lambda: None
    ) == receipt
    assert len(calls) == 1

    created = client.execute_custom_tool(
        "resource", "conversation",
        _command(receipt["snapshot"], "create", draft_id=receipt["snapshot"]["drafts"][0]["id"]),
        owner_id="owner", validate=lambda: None,
    )
    assert created["status"] == "completed"
    assert created["snapshot"]["tools"][0]["name"] == "Example"
    assert created["snapshot"]["tools"][0]["enabled"] is False


def test_failed_inspect_without_saved_draft_allows_explicit_retry(local_builder, monkeypatch):
    workspace, _records = local_builder
    calls = []

    def propose(path: str, *, source_url: str, use_ai: bool):
        calls.append((path, use_ai))
        if len(calls) == 1:
            raise RuntimeError("fake model unavailable")
        return capsules.CapsuleManifestProposal(
            "Example", "1.0.0", source_url, path,
            [{"name": "Hello", "description": "Greeting", "command": "python hello.py"}],
        )

    monkeypatch.setattr(capsules, "propose_capsule_manifest", propose)
    before = client.read_custom_tools("resource", "conversation", validate=lambda: None)
    failed = client.execute_custom_tool(
        "resource", "conversation", _command(before, "inspect"),
        owner_id="owner", validate=lambda: None,
    )
    assert failed["status"] == "failed"
    assert failed["snapshot"]["drafts"] == []
    assert "fake model unavailable" not in failed["summary"]
    assert client.read_custom_tools("resource", "conversation", validate=lambda: None) == before
    retried = client.execute_custom_tool(
        "resource", "conversation", _command(before, "inspect"),
        owner_id="owner", validate=lambda: None,
    )
    assert retried["status"] == "completed"
    assert retried["snapshot"]["drafts"][0]["name"] == "Example"
    assert calls == [(str(workspace), True), (str(workspace), True)]


def test_inspect_with_ambiguous_saved_draft_keeps_original_uncertain(local_builder, monkeypatch):
    workspace, records = local_builder

    def save_then_fail(path: str, *, source_url: str, use_ai: bool):
        capsules._save_draft(capsules.CustomToolDraft(
            id="draft-ambiguous", source_url=source_url, installed_path=path,
            name="Partial proposal",
        ))
        raise RuntimeError("fake acknowledgement lost")

    monkeypatch.setattr(capsules, "propose_capsule_manifest", save_then_fail)
    before = client.read_custom_tools("resource", "conversation", validate=lambda: None)
    command = _command(before, "inspect")
    with pytest.raises(RuntimeError, match="fake acknowledgement lost"):
        client.execute_custom_tool(
            "resource", "conversation", command,
            owner_id="owner", validate=lambda: None,
        )
    assert records[command["command_id"]]["result"] is None
    assert client.read_custom_tools("resource", "conversation", validate=lambda: None)["drafts"][0]["name"] == "Partial proposal"


def test_draft_scope_and_stale_revision_are_enforced(local_builder, monkeypatch):
    workspace, _records = local_builder
    other = workspace.parent / "other"
    other.mkdir()
    foreign = capsules.CustomToolDraft(
        id="foreign", source_url="", installed_path=str(other), name="Other"
    )
    capsules._save_draft(foreign)
    snapshot = client.read_custom_tools("resource", "conversation", validate=lambda: None)
    assert snapshot["drafts"] == []
    with pytest.raises(ClientPlatformError, match="custom_tool_draft_unavailable"):
        client.execute_custom_tool(
            "resource", "conversation", _command(snapshot, "remove", draft_id="foreign"),
            owner_id="owner", validate=lambda: None,
        )
    with pytest.raises(ClientPlatformError, match="custom_tool_revision_conflict"):
        client.execute_custom_tool(
            "resource", "conversation", {**_command(snapshot, "inspect"), "revision": "0" * 64},
            owner_id="owner", validate=lambda: None,
        )
