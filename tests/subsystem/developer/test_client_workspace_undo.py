"""Undo uses exact retained imports in disposable workspaces, never display text."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from tests.subsystem.developer.test_client_workspace_imports import imports as imports
from tests.subsystem.developer.test_client_workspace_edits import domain as domain

pytestmark = pytest.mark.subsystem


@pytest.fixture
def undo(imports, monkeypatch):
    from row_bot.developer import client_undo
    from row_bot import models
    # Retained tool registration reads availability at import; never discover
    # installed CLI accounts or invoke a provider while testing Undo dispatch.
    monkeypatch.setattr(models, "is_cloud_available", lambda: False)
    d = imports
    d.undo = client_undo
    d.undo_receipts = []
    def apply(review, **kwargs):
        return client_undo.undo_workspace_change(review, command_id=kwargs.pop("command_id", str(uuid.uuid4())),
            confirmed=kwargs.pop("confirmed", True), persist_recovery=lambda value: d.undo_receipts.append(copy.deepcopy(value)), **kwargs)
    d.restore = apply
    def imported(before, after):
        pending = d.pending(before, after)
        result = d.apply(d.imports.review_workspace_import(d.workspace.id, "chat", pending.id))
        assert result.status == "imported", result
        return result.change_set_id
    d.imported = imported
    d.review = lambda identity: client_undo.review_workspace_undo(d.workspace.id, "chat", identity)
    return d


@pytest.mark.parametrize("before,after", [(b"old\r\n", b"new\r\n"), (b"old", b"new"),
    (b"", None), (None, b""), (None, b"new\n"), (b"old\n", None)])
def test_exact_original_bytes_and_metadata_are_restored(undo, before, after):
    d = undo
    identity = d.imported({"file.txt": before.decode() if before is not None else None},
        {"file.txt": after.decode() if after is not None else None})
    saved, _ = d.ledger.read_change_set(identity)
    source = d.edits.FileEditRecovery(**saved.guarded_import["files"]["file.txt"])
    review = d.review(identity)
    result = d.restore(review)
    assert result.status == "undone" and result.reverted and result.ledger_saved, result
    assert result.files_restored == ("file.txt",)
    path = d.root / "file.txt"
    assert path.read_bytes() == before if before is not None else not path.exists()
    if before is not None:
        assert d.edits._edit_identity(path.stat()) == source.original_identity
        assert d.edits.file_edit_metadata_digest(path) == source.metadata_digest
    assert d.ledger.read_change_set(identity)[0].reverted


def test_invalid_utf8_untouched_source_restores_exact_bytes(undo):
    d = undo
    before = b"first\n" + b"context\n" * 10 + b"retained \xff\n"
    after = before.replace(b"first", b"changed", 1)
    pending = d.sandbox._record_pending_change(d.workspace, "chat", "synthetic", {"legacy.txt": before.decode(errors="replace")},
        {"legacy.txt": after.decode(errors="replace")})
    (d.root / "legacy.txt").write_bytes(before)
    imported = d.apply(d.imports.review_workspace_import(d.workspace.id, "chat", pending.id))
    assert imported.imported
    restored = d.restore(d.review(imported.change_set_id))
    assert restored.status == "undone", restored
    assert (d.root / "legacy.txt").read_bytes() == before


@pytest.mark.parametrize("stage", ["review", "execute"])
@pytest.mark.parametrize("edit", ["content", "same_bytes_inode", "parent"])
def test_user_changes_are_never_overwritten(undo, stage, edit):
    d = undo
    identity = d.imported({"nested/file.txt": "old\n"}, {"nested/file.txt": "new\n"})
    review = d.review(identity)
    target = d.root / "nested/file.txt"
    if edit == "content":
        target.write_bytes(b"user\n")
    elif edit == "same_bytes_inode":
        target.rename(target.with_name("saved"))
        target.write_bytes(b"new\n")
    else:
        target.parent.rename(d.root / "retained")
        target.parent.mkdir()
        target.write_bytes(b"new\n")
    original = target.read_bytes()
    if stage == "review":
        with pytest.raises(ValueError, match="file_revision_conflict"):
            d.review(identity)
    else:
        result = d.restore(review)
        assert result.status == "conflict" and result.code == "file_revision_conflict", result
    assert target.read_bytes() == original
    assert not d.ledger.read_change_set(identity)[0].reverted


def test_new_directories_remain_and_unrelated_children_survive(undo):
    d = undo
    pending = d.sandbox._record_pending_change(d.workspace, "chat", "synthetic", {}, {"new/nested/file.txt": "created\n"})
    imported = d.apply(d.imports.review_workspace_import(d.workspace.id, "chat", pending.id))
    assert imported.imported
    (d.root / "new/nested/user.txt").write_bytes(b"retain")
    review = d.review(imported.change_set_id)
    assert review.directories_retained == ("new", "new/nested")
    assert d.restore(review).reverted
    assert (d.root / "new/nested/user.txt").read_bytes() == b"retain"
    assert not (d.root / "new/nested/file.txt").exists()


def test_mixed_empty_creation_and_text_update_are_separate_git_changes(undo):
    d = undo
    identity = d.imported({"old.txt": "before\n"}, {"old.txt": "after\n", "new/nested/empty.txt": ""})
    assert (d.root / "new/nested/empty.txt").read_bytes() == b""
    assert (d.root / "old.txt").read_bytes() == b"after\n"
    assert d.restore(d.review(identity)).reverted
    assert (d.root / "old.txt").read_bytes() == b"before\n"


def test_partial_restore_retries_original_proof_without_overwriting_later_edits(undo, monkeypatch):
    d = undo
    identity = d.imported({"a.txt": "old a\n", "b.txt": "old b\n"}, {"a.txt": "new a\n", "b.txt": "new b\n"})
    review, command = d.review(identity), str(uuid.uuid4())
    publish = d.edits.publish_text_revision
    def fail_second(root, path, *args, **kwargs):
        if path == "b.txt":
            raise OSError("synthetic interruption")
        return publish(root, path, *args, **kwargs)
    monkeypatch.setattr(d.edits, "publish_text_revision", fail_second)
    first = d.restore(review, command_id=command)
    assert first.status == "partial" and first.files_restored == ("a.txt",), first
    assert not first.reverted
    proof = d.undo_receipts[-1]
    monkeypatch.setattr(d.edits, "publish_text_revision", publish)
    assert d.restore(review, command_id=command, recovery=proof).status == "undone"
    assert (d.root / "a.txt").read_bytes() == b"old a\n"
    assert (d.root / "b.txt").read_bytes() == b"old b\n"


@pytest.mark.parametrize("failure", ["ledger", "release", "finish"])
def test_original_command_recovers_finalization_without_duplicate_restore(undo, monkeypatch, failure):
    from row_bot import agent_runs
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    review, command = d.review(identity), str(uuid.uuid4())
    owner, name = (d.ledger, "mark_reverted") if failure == "ledger" else (agent_runs, "release_agent_write_lock" if failure == "release" else "finish_agent_run")
    original = getattr(owner, name)
    monkeypatch.setattr(owner, name, lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic failure")))
    first = d.restore(review, command_id=command)
    assert first.status == "partial", first
    inode = (d.root / "a.txt").stat().st_ino
    monkeypatch.setattr(owner, name, original)
    if failure != "ledger":
        monkeypatch.setattr(d.edits, "publish_text_revision", lambda *_a, **_kw: pytest.fail("completed Undo republished a file"))
    retried = d.restore(review, command_id=command, recovery=d.undo_receipts[-1])
    assert retried.status == "undone", retried
    assert (d.root / "a.txt").stat().st_ino == inode
    assert agent_runs.get_agent_write_lock("developer:" + d.workspace.id) is None


def test_missing_or_changed_private_proof_never_uses_display_text(undo):
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    data = json.loads(d.ledger.LEDGER_PATH.read_text())
    data["change_sets"][0]["guarded_import"]["files"] = {}
    d.ledger.LEDGER_PATH.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="workspace_undo_proof_unavailable"):
        d.review(identity)
    with pytest.raises(ValueError, match="workspace_undo_review_required"):
        d.edits.revert_change_set(d.workspace.id, identity)
    assert (d.root / "a.txt").read_bytes() == b"new\n"


def test_removed_strict_proof_marker_cannot_fall_back_to_legacy_undo(undo):
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    data = json.loads(d.ledger.LEDGER_PATH.read_text())
    data["change_sets"][0].pop("guarded_import")
    d.ledger.LEDGER_PATH.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="workspace_undo_review_required"):
        d.edits.revert_change_set(d.workspace.id, identity)
    assert (d.root / "a.txt").read_bytes() == b"new\n"


def test_revocation_before_first_publication_has_no_effect(undo):
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    review = d.review(identity)
    result = d.restore(review, validate=lambda: (_ for _ in ()).throw(ValueError("capability_revoked")))
    assert result.status == "denied" and result.code == "capability_revoked"
    assert (d.root / "a.txt").read_bytes() == b"new\n"


def test_reviewed_action_tampering_is_rejected(undo):
    from dataclasses import replace
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    result = d.restore(replace(d.review(identity), files=("different.txt",)))
    assert result.status == "conflict" and result.code == "edit_recovery_conflict"


def test_retained_adapter_recovers_original_review_after_lost_marker(undo, monkeypatch):
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    def validate():
        pass
    review, command = d.undo.prepare_retained_undo(d.workspace.id, "chat", identity, validate=validate)
    mark = d.ledger.mark_reverted
    monkeypatch.setattr(d.ledger, "mark_reverted", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic marker loss")))
    first = d.undo.execute_retained_undo(review, command, confirmed=True, validate=validate)
    assert first.status == "partial", first
    recovered_review, original = d.undo.prepare_retained_undo(d.workspace.id, "chat", identity, validate=validate)
    assert recovered_review == review and original == command
    monkeypatch.setattr(d.ledger, "mark_reverted", mark)
    result = d.undo.execute_retained_undo(recovered_review, original, confirmed=True, validate=validate)
    assert result.status == "undone", result
    assert (d.root / "a.txt").read_bytes() == b"old\n"


def test_tool_review_is_reserved_before_interrupt_and_not_replaced_by_changed_files(undo):
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    def validate():
        pass
    review, command = d.undo.prepare_retained_undo(d.workspace.id, "chat", identity, validate=validate)
    d.undo.reserve_retained_undo_review(review, command, validate=validate)
    (d.root / "a.txt").write_bytes(b"later user edit\n")
    repeated, repeated_command = d.undo.prepare_retained_undo(d.workspace.id, "chat", identity, validate=validate)
    assert repeated == review and repeated_command == command
    result = d.undo.execute_retained_undo(repeated, command, confirmed=True, validate=validate)
    assert result.status == "conflict", result
    assert (d.root / "a.txt").read_bytes() == b"later user edit\n"


@pytest.mark.parametrize("borrowed", ["own", "foreign", "stopped", "missing_context"])
def test_tool_borrows_only_its_current_live_exact_writer_and_never_releases_it(undo, monkeypatch, borrowed):
    from row_bot import agent, conversation_resources
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    review = d.review(identity)
    run = "existing-parent-run"
    d.runs.create_agent_run(run_id=run, kind="workflow", status="running", thread_id="chat",
        workspace_id=d.workspace.id, workspace_path=d.workspace.path, write_lock_key="developer:" + d.workspace.id)
    assert d.runs.acquire_agent_write_lock("developer:" + d.workspace.id, run, thread_id="chat", workspace_id=d.workspace.id)
    monkeypatch.setattr(agent, "get_active_runtime_context", lambda: {"agent_run_id": "another-run" if borrowed == "foreign" else run})
    if borrowed == "stopped":
        monkeypatch.setattr(d.runs, "get_agent_run", lambda _id: {"thread_id": "chat", "workspace_id": d.workspace.id, "stop_requested": True})
    with conversation_resources.execution_context("chat"):
        if borrowed == "missing_context":
            monkeypatch.setattr(conversation_resources, "current_execution_context", lambda: None)
        result = d.restore(review, borrowed_run_id=run)
    assert result.status == ("undone" if borrowed == "own" else "denied"), result
    assert d.runs.get_agent_write_lock("developer:" + d.workspace.id)["run_id"] == run
    assert (d.root / "a.txt").read_bytes() == (b"old\n" if borrowed == "own" else b"new\n")


@pytest.mark.parametrize("tamper", ["content", "metadata", "inode"])
def test_retained_source_tampering_never_restores_unproven_content(undo, tamper):
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    review = d.review(identity)
    original = next(d.root.glob(".row-bot-edit-recovery/*/previous"))
    if tamper == "content":
        original.write_bytes(b"changed")
    elif tamper == "inode":
        original.rename(original.with_name("retained-other"))
        original.write_bytes(b"old\n")
    else:
        import stat
        original.chmod(stat.S_IREAD)
    result = d.restore(review)
    assert result.status in {"denied", "conflict"} and not result.reverted, result
    assert (d.root / "a.txt").read_bytes() == b"new\n"


def test_later_edit_after_partial_undo_is_not_overwritten_on_retry(undo, monkeypatch):
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    review, command = d.review(identity), str(uuid.uuid4())
    mark = d.ledger.mark_reverted
    monkeypatch.setattr(d.ledger, "mark_reverted", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic marker loss")))
    assert d.restore(review, command_id=command).status == "partial"
    proof = d.undo_receipts[-1]
    (d.root / "a.txt").write_bytes(b"user after restore\n")
    monkeypatch.setattr(d.ledger, "mark_reverted", mark)
    result = d.restore(review, command_id=command, recovery=proof)
    assert result.status == "partial" and not result.reverted, result
    assert (d.root / "a.txt").read_bytes() == b"user after restore\n"


def test_legacy_tool_routes_strict_undo_through_original_review(undo, monkeypatch):
    from row_bot import agent, conversation_resources
    from row_bot.tools import developer_tool
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    monkeypatch.setattr(developer_tool, "get_thread_id", lambda: "chat")
    monkeypatch.setattr(developer_tool, "get_workspace_id", lambda: d.workspace.id)
    monkeypatch.setattr(agent, "get_active_runtime_context", lambda: {})
    approvals = []
    monkeypatch.setattr(developer_tool, "interrupt", lambda request: approvals.append(request) or True)
    with conversation_resources.execution_context("chat"):
        message = developer_tool._revert_change_set(identity)
    assert "Reverted 1 file(s)" in message
    assert len(approvals) == 1 and approvals[0]["args"]["change_set_id"] == identity
    assert len(approvals[0]["args"]["action_digest"]) == 64
    assert (d.root / "a.txt").read_bytes() == b"old\n"


def test_legacy_tool_approval_cannot_adopt_a_later_source_revision(undo, monkeypatch):
    from row_bot import conversation_resources
    from row_bot.tools import developer_tool
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    monkeypatch.setattr(developer_tool, "get_thread_id", lambda: "chat")
    monkeypatch.setattr(developer_tool, "get_workspace_id", lambda: d.workspace.id)
    def interrupt(_request):
        (d.root / "a.txt").write_bytes(b"user during approval\n")
        return True
    monkeypatch.setattr(developer_tool, "interrupt", interrupt)
    with conversation_resources.execution_context("chat"):
        message = developer_tool._revert_change_set(identity)
    assert "conflict" in message
    assert (d.root / "a.txt").read_bytes() == b"user during approval\n"


def test_cancelled_tool_review_allows_a_new_explicit_review_without_resurrecting_approval(undo):
    from row_bot.runtime import admissions
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    review, first = d.undo.prepare_retained_undo(d.workspace.id, "chat", identity, validate=lambda: None)
    d.undo.reserve_retained_undo_review(review, first, validate=lambda: None)
    admissions.reject_command("developer-undo:chat", first, "action_denied")
    second_review, second = d.undo.prepare_retained_undo(d.workspace.id, "chat", identity, validate=lambda: None)
    assert first != second and second_review == review
    assert d.undo.execute_retained_undo(second_review, second, confirmed=True, validate=lambda: None).reverted


@pytest.mark.parametrize("confirm,revoked", [(False, False), (True, False), (True, True)])
def test_actual_nicegui_revert_callback_requires_confirmation_and_live_owner(undo, monkeypatch, confirm, revoked):
    import ast
    import asyncio
    from functools import partial
    from pathlib import Path
    from types import SimpleNamespace
    from row_bot.ui import access_context
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    module = ast.parse(Path("src/row_bot/developer/ui.py").read_text())
    callback = next(node for node in ast.walk(module) if isinstance(node, ast.AsyncFunctionDef) and node.name == "_revert")
    class Element:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def classes(self, *_args): return self
        def style(self, *_args): return self
        def props(self, *_args): return self
        def submit(self, value): self.value = value
        def __await__(self):
            async def answer():
                if revoked:
                    client.has_socket_connection = False
                return confirm
            return answer().__await__()
    class Client:
        instances = {}
        id = "synthetic-client"
        has_socket_connection = True
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(row_bot_access_service=None)))
    client = Client()
    Client.instances[client.id] = client
    access = SimpleNamespace(is_local_owner=True)
    monkeypatch.setattr(access_context, "access_context_from_client", lambda _client: access)
    monkeypatch.setattr(access_context, "require_ui_owner", lambda selected: selected)
    notices, audits = [], []
    ui = SimpleNamespace(context=SimpleNamespace(client=client), dialog=Element, card=Element, row=Element,
        label=lambda *_args: Element(), button=lambda *_args, **_kwargs: Element(), notify=lambda message, **_kwargs: notices.append(message))
    async def io_bound(function, *args, **kwargs):
        return function(*args, **kwargs)
    state = SimpleNamespace(thread_id="chat", active_developer_workspace_id=d.workspace.id, messages=[])
    scope = {"ui": ui, "run": SimpleNamespace(io_bound=io_bound), "partial": partial, "workspace_now": d.workspace,
        "next_snapshot": SimpleNamespace(thread_id="chat"), "state": state, "add_chat_message": audits.append,
        "on_refresh": lambda: None, "reverting": set(), "revert_change_set": lambda *_a: pytest.fail("strict Undo used legacy text reconstruction")}
    exec(compile(ast.Module(body=[callback], type_ignores=[]), "<actual-nicegui-undo-callback>", "exec"), scope)
    asyncio.run(scope["_revert"](identity))
    assert (d.root / "a.txt").read_bytes() == (b"old\n" if confirm and not revoked else b"new\n")
    assert len(audits) == (1 if confirm and not revoked else 0)
    assert not scope["reverting"]
    if revoked:
        assert notices and "resource_binding_revoked" in notices[-1]


def test_fresh_inspector_snapshot_keeps_original_reverted_undo_until_finalization(undo, monkeypatch):
    from row_bot.developer import inspector_snapshot, runtime, todos
    d = undo
    identity = d.imported({"a.txt": "old\n"}, {"a.txt": "new\n"})
    monkeypatch.setattr(d.storage, "detect_git_summary", lambda _path: {"is_git": False})
    monkeypatch.setattr(runtime, "detect_project_commands", lambda _path: [])
    monkeypatch.setattr(todos, "list_todos", lambda _thread: [])
    review, command = d.undo.prepare_retained_undo(d.workspace.id, "chat", identity, validate=lambda: None)
    release = d.runs.release_agent_write_lock
    monkeypatch.setattr(d.runs, "release_agent_write_lock", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("synthetic release loss")))
    partial = d.undo.execute_retained_undo(review, command, confirmed=True, validate=lambda: None)
    assert partial.status == "partial" and partial.reverted
    inspector_snapshot.clear_thread_snapshots("chat")
    remounted = inspector_snapshot.refresh_snapshot_for_tests(d.workspace.id, "chat")
    assert [(row.id, row.reverted) for row in remounted.agent_changes] == [(identity, True)]
    retained, original = d.undo.prepare_retained_undo(d.workspace.id, "chat", remounted.agent_changes[0].id, validate=lambda: None)
    assert retained == review and original == command
    monkeypatch.setattr(d.runs, "release_agent_write_lock", release)
    assert d.undo.execute_retained_undo(retained, original, confirmed=True, validate=lambda: None).status == "undone"
    inspector_snapshot.clear_thread_snapshots("chat")
    finished = inspector_snapshot.refresh_snapshot_for_tests(d.workspace.id, "chat")
    assert not finished.agent_changes
    assert (d.root / "a.txt").read_bytes() == b"old\n"
