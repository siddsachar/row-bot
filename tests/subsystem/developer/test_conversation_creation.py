"""Draft code folders and designs in a conversation, without Git or a provider."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401

pytestmark = pytest.mark.subsystem


@pytest.fixture
def creation(service, tmp_path, monkeypatch):
    from row_bot import threads
    from row_bot.developer import storage as workspaces
    from row_bot.designer import storage as designs
    from row_bot.tools import registry

    monkeypatch.setattr(workspaces, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(workspaces, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    monkeypatch.setattr(designs, "DESIGNER_DIR", tmp_path / "designer")
    monkeypatch.setattr(designs, "PROJECTS_DIR", tmp_path / "designer" / "projects")
    monkeypatch.setattr(designs, "ASSETS_DIR", tmp_path / "designer" / "assets")
    monkeypatch.setattr(designs, "REFERENCES_DIR", tmp_path / "designer" / "references")
    monkeypatch.setattr(registry, "is_enabled", lambda name: name in {"developer", "designer"})
    root = tmp_path / "configured-workspace"
    filesystem = type("Filesystem", (), {"get_config": lambda self, key, default: str(root)})()
    monkeypatch.setattr(registry, "get_tool", lambda name: filesystem if name == "filesystem" else None)
    conversation = threads.create_thread("New conversation")
    return service, conversation, root


def _draft(service, conversation):
    identity = str(uuid4())
    result = service.execute(owner_id="fixture", idempotency_key=identity, command={
        "type": "resource.setup", "command_id": identity,
        "expected_revision": str(service._metadata(conversation)["client_revision"]),
        "payload": {"kind": "workspace", "intent": "create", "draft_workspace": True},
    }, target=conversation)
    assert result["status"] == "completed"
    return result


def test_media_requests_expose_only_one_generation_family():
    from row_bot.application.conversation_creation import media_tool_selection

    enabled = ["designer", "image_gen", "video_gen", "developer"]
    assert media_tool_selection("Generate an image of a cat", enabled, has_design=True) == [
        "image_gen", "developer",
    ]
    assert media_tool_selection("Generate a video for this deck", enabled, has_design=True) == [
        "designer", "developer",
    ]
    assert media_tool_selection("Create images for the slides", enabled, has_design=True) == [
        "designer", "developer",
    ]
    assert media_tool_selection("Edit the cover page", enabled, has_design=True) == enabled


def test_second_workspace_is_rejected_before_creating_an_orphan(creation):
    from row_bot.application.conversation_creation import _draft_parent
    from row_bot.application.client_platform import ClientPlatformError

    service, conversation, root = creation
    _draft(service, conversation)
    before = tuple((root / "Drafts").iterdir())
    command_id = str(uuid4())
    with pytest.raises(ClientPlatformError, match="resource_ambiguous"):
        service.execute(owner_id="fixture", idempotency_key=command_id,
                        command={"type": "resource.setup", "command_id": command_id,
                                 "expected_revision": str(service._metadata(conversation)["client_revision"]),
                                 "payload": {"kind": "workspace", "intent": "create",
                                             "empty_workspace": {"folder_name": "extra"}}},
                        target=conversation, authorized_folder=_draft_parent())
    assert tuple((root / "Drafts").iterdir()) == before


def test_context_fallback_creates_default_draft_without_native_folder_grant(creation):
    from row_bot.api.v1.schemas import ResourceSetupPayload
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer.storage import get_workspace

    service, conversation, root = creation
    identity = str(uuid4())
    payload = ResourceSetupPayload.model_validate_json('''{"kind":"workspace","intent":"create","draft_workspace":true}''')
    command = {
        "type": "resource.setup", "command_id": identity,
        "expected_revision": str(service._metadata(conversation)["client_revision"]),
        "payload": payload.model_dump(mode="json"),
    }
    result = service.execute(owner_id="fixture", idempotency_key=identity,
                             command=command, target=conversation)
    assert result["status"] == "completed"
    workspace = get_workspace(list_bindings(conversation).bindings[0].resource_id)
    assert workspace is not None and Path(workspace.path).parent == root / "Drafts"


def test_two_chat_writers_queue_and_cancel_without_releasing_owner(creation):
    from row_bot.application.conversation_writer import writer_run
    from row_bot.agent_runs import get_agent_write_lock

    _, conversation, _ = creation
    owner_entered, owner_release, waiter_started, waiter_entered = (Event() for _ in range(4))
    workspace_id = "synthetic-shared-checkout"

    def owner():
        with writer_run(conversation, workspace_id, str(uuid4()), Event()) as run_id:
            owner_entered.set()
            assert owner_release.wait(5)
            assert get_agent_write_lock(f"developer:{workspace_id}")["run_id"] == run_id

    def waiter(cancel: Event):
        waiter_started.set()
        with writer_run(conversation, workspace_id, str(uuid4()), cancel):
            waiter_entered.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(owner)
        assert owner_entered.wait(5)
        cancel = Event()
        second = pool.submit(waiter, cancel)
        assert waiter_started.wait(5)
        assert not waiter_entered.is_set()
        cancel.set()
        with pytest.raises(RuntimeError, match="checkout_writer_cancelled"):
            second.result(timeout=5)
        assert get_agent_write_lock(f"developer:{workspace_id}") is not None
        owner_release.set()
        first.result(timeout=5)
    assert get_agent_write_lock(f"developer:{workspace_id}") is None


def test_writer_run_records_real_completion_state(creation):
    from row_bot.application.conversation_writer import writer_run, writer_status
    from row_bot.agent_runs import get_agent_run

    _, conversation, _ = creation
    with writer_run(conversation, "synthetic-workspace", str(uuid4()), Event(),
                    final_status=lambda: "waiting_approval") as run_id:
        assert writer_status(conversation) == "running"
    assert get_agent_run(run_id)["status"] == "waiting_approval"
    assert writer_status(conversation) == "waiting_approval"


def test_child_mutation_requires_its_own_writer_lease(creation, monkeypatch):
    from row_bot.application.conversation_writer import require_execution_writer
    from row_bot import agent

    monkeypatch.setattr(agent, "get_active_runtime_context",
                        lambda: {"agent_run_id": "synthetic-child"})
    require_execution_writer("synthetic-workspace", write=False)
    with pytest.raises(ValueError, match="checkout_writer_unavailable"):
        require_execution_writer("synthetic-workspace")


def test_full_replacement_requires_current_file_hash_inside_captured_run(creation, monkeypatch):
    from row_bot.application.conversation_writer import writer_run
    from row_bot.conversation_resources import execution_context, list_bindings
    from row_bot.developer.edits import write_file_to_workspace
    from row_bot.developer.storage import get_workspace
    from row_bot import agent
    import hashlib

    service, conversation, _ = creation
    _draft(service, conversation)
    workspace_id = list_bindings(conversation).bindings[0].resource_id
    path = Path(get_workspace(workspace_id).path) / "index.html"
    with writer_run(conversation, workspace_id, str(uuid4()), Event()) as run_id:
        monkeypatch.setattr(agent, "get_active_runtime_context", lambda: {"agent_run_id": run_id})
        with execution_context(conversation):
            write_file_to_workspace(workspace_id=workspace_id, thread_id=conversation,
                                    path="index.html", content="one", approval_mode="allow_all")
            with pytest.raises(ValueError, match="file_revision_conflict"):
                write_file_to_workspace(workspace_id=workspace_id, thread_id=conversation,
                                        path="index.html", content="two", approval_mode="allow_all")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            write_file_to_workspace(workspace_id=workspace_id, thread_id=conversation,
                                    path="index.html", content="two", approval_mode="allow_all",
                                    expected_sha256=digest)
    assert path.read_text(encoding="utf-8") == "two"


def test_generated_media_import_is_scoped_logged_and_reversible(creation, monkeypatch, tmp_path):
    from row_bot.application.attachments import register_attachment
    from row_bot.application.conversation_writer import writer_run
    from row_bot.conversation_resources import execution_context, list_bindings
    from row_bot.developer import change_ledger
    from row_bot.developer.edits import import_conversation_media, revert_change_set
    from row_bot.developer.storage import get_workspace
    from row_bot import agent

    service, conversation, _ = creation
    _draft(service, conversation)
    workspace_id = list_bindings(conversation).bindings[0].resource_id
    root = Path(get_workspace(workspace_id).path)
    monkeypatch.setattr(change_ledger, "DEVELOPER_DIR", tmp_path / "ledger")
    monkeypatch.setattr(change_ledger, "LEDGER_PATH", tmp_path / "ledger" / "changes.json")
    media = register_attachment(conversation, "synthetic.png", b"\x89PNG\r\n\x1a\nfixture")
    reference = media["attachment_ref"]
    with writer_run(conversation, workspace_id, str(uuid4()), Event()) as run_id:
        monkeypatch.setattr(agent, "get_active_runtime_context", lambda: {"agent_run_id": run_id})
        with execution_context(conversation):
            with pytest.raises(ValueError, match="media_scope_conflict"):
                import_conversation_media(workspace_id=workspace_id, thread_id=conversation,
                                          media_ref="other:" + reference, path="image.png",
                                          approval_mode="allow_all")
            change, _ = import_conversation_media(workspace_id=workspace_id,
                                                   thread_id=conversation, media_ref=reference,
                                                   path="image.png", approval_mode="allow_all")
            assert change and change.files[0].binary
            repeat, _ = import_conversation_media(workspace_id=workspace_id,
                                                   thread_id=conversation, media_ref=reference,
                                                   path="image.png", approval_mode="allow_all")
            assert repeat is None
            (root / "existing.png").write_bytes(b"different")
            with pytest.raises(ValueError, match="media_destination_conflict"):
                import_conversation_media(workspace_id=workspace_id, thread_id=conversation,
                                          media_ref=reference, path="existing.png", approval_mode="allow_all")
    assert (root / "image.png").read_bytes().startswith(b"\x89PNG")
    assert change is not None
    assert "Reverted" in revert_change_set(workspace_id, change.id)
    assert not (root / "image.png").exists()


def test_explicit_output_save_is_scoped_idempotent_and_outside_code_folder(creation):
    from row_bot.application.attachments import register_attachment, list_generated_outputs
    from row_bot.application.conversation_media_copy import save_output

    service, conversation, root = creation
    output = register_attachment(conversation, "generated-image.png", b"\x89PNG\r\n\x1a\nfixture")
    reference = output["attachment_ref"]
    assert list_generated_outputs(conversation) == [{"media_ref": reference, "mime_type": "image/png"}]
    with pytest.raises(ValueError, match="media_scope_conflict"):
        save_output(conversation, "other:" + reference)
    name = save_output(conversation, reference)
    saved = root / "Saved outputs" / name
    assert saved.read_bytes() == b"\x89PNG\r\n\x1a\nfixture"
    assert save_output(conversation, reference) == name
    # If the configured root itself is a code folder, it must use Developer.
    _draft(service, conversation)
    assert saved.exists()
    from row_bot.thread_cleanup import delete_thread
    assert delete_thread(conversation).deleted is True
    assert saved.exists()


def test_explicit_output_save_rejects_code_folder_without_creating_destination(creation):
    from row_bot.application.attachments import register_attachment
    from row_bot.application.conversation_media_copy import save_output
    from row_bot.developer.state import DeveloperWorkspace
    from row_bot.developer.storage import save_workspace

    _, conversation, root = creation
    output = register_attachment(conversation, "generated-image.png", b"\x89PNG\r\n\x1a\nfixture")
    save_workspace(DeveloperWorkspace(id="root", name="root", path=str(root)))
    with pytest.raises(ValueError, match="media_destination_is_code_folder"):
        save_output(conversation, output["attachment_ref"])
    assert not (root / "Saved outputs").exists()


def test_media_save_command_returns_a_replayable_receipt(creation):
    from row_bot.api.v1.schemas import MediaSavePayload
    from row_bot.application.attachments import register_attachment

    service, conversation, root = creation
    output = register_attachment(conversation, "generated-image.png", b"\x89PNG\r\n\x1a\nfixture")
    payload = MediaSavePayload.model_validate({"media_ref": output["attachment_ref"]})
    identity = str(uuid4())
    command = {"type": "media.save", "command_id": identity,
               "expected_revision": str(service._metadata(conversation)["client_revision"]),
               "payload": payload.model_dump(mode="json")}
    result = service.execute(owner_id="fixture", idempotency_key=identity,
                             command=command, target=conversation)
    assert result["status"] == "completed"
    assert (root / "Saved outputs" / result["saved_name"]).is_file()
    assert service.execute(owner_id="fixture", idempotency_key=identity,
                           command=command, target=conversation) == result
