"""The assistant creates designs and code folders with tools (decision 12, B113).

Real resource, designer, developer and admission owners on ``tmp_path``; the
model is a scripted stream, so nothing calls a provider.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage
import pytest

from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.developer.test_conversation_creation import creation  # noqa: F401

pytestmark = pytest.mark.subsystem


def _in(conversation):
    from row_bot.conversation_resources import execution_context

    return execution_context(conversation)


def _command(service, conversation, kind, payload):  # noqa: F811
    identity = str(uuid4())
    return service.execute(owner_id="fixture", idempotency_key=identity, command={
        "type": kind, "command_id": identity,
        "expected_revision": str(service._metadata(conversation)["client_revision"]),
        "payload": payload,
    }, target=conversation)


def test_code_folder_is_named_from_the_request_bound_and_not_git(creation):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer.storage import get_workspace
    from row_bot.tools.conversation_setup_tool import create_code_folder

    service, conversation, root = creation
    with _in(conversation):
        result = json.loads(create_code_folder("Tiny date app"))
    assert result["ok"] is True and result["kind"] == "resource_created"
    assert result["resource_kind"] == "code" and result["name"] == "Tiny date app"
    bindings = list_bindings(conversation).bindings
    assert [(item.kind, item.binding_id) for item in bindings] == [("workspace", result["binding_id"])]
    workspace = get_workspace(bindings[0].resource_id)
    assert Path(workspace.path) == root / "Drafts" / "Tiny date app"
    assert workspace.origin_conversation_id == conversation
    assert not (Path(workspace.path) / ".git").exists()


def test_a_taken_name_gets_a_number_and_a_bound_folder_is_reused(creation):  # noqa: F811
    from row_bot import threads
    from row_bot.tools.conversation_setup_tool import create_code_folder

    service, conversation, root = creation
    other = threads.create_thread("Other conversation")
    with _in(conversation):
        assert json.loads(create_code_folder("Tiny date app"))["ok"]
        again = json.loads(create_code_folder("Something else"))
    assert again["ok"] is False and again["kind"] == "existing"
    with _in(other):
        second = json.loads(create_code_folder("Tiny date app"))
    assert second["name"] == "Tiny date app 2"
    assert sorted(path.name for path in (root / "Drafts").iterdir()) == ["Tiny date app", "Tiny date app 2"]


def test_developer_off_asks_to_turn_it_on_instead_of_writing_loose_files(creation, monkeypatch):  # noqa: F811
    import langgraph.types
    from row_bot.conversation_resources import list_bindings
    from row_bot.tools import registry
    from row_bot.tools.conversation_setup_tool import create_code_folder

    service, conversation, root = creation
    enabled = {"designer"}
    monkeypatch.setattr(registry, "is_enabled", lambda name: name in enabled)
    monkeypatch.setattr(registry, "set_enabled", lambda name, on: enabled.add(name) if on else enabled.discard(name))
    requests = []
    answers = [False, True]
    monkeypatch.setattr(langgraph.types, "interrupt", lambda payload: (requests.append(payload), answers.pop(0))[1])
    with _in(conversation):
        declined = json.loads(create_code_folder("Tiny date app"))
    assert declined["ok"] is False and declined["kind"] == "setup_declined"
    assert "do not write files" in declined["error"]
    assert not (root / "Drafts").exists() or not any((root / "Drafts").iterdir())
    assert requests[0]["setup"] == {"kind": "tool", "label": "Developer tools"}
    assert requests[0]["args"] == {"setting": "tool_toggle", "value": "developer:on"}
    with _in(conversation):
        created = json.loads(create_code_folder("Tiny date app"))
    assert created["ok"] is True and "developer" in enabled
    assert len(list_bindings(conversation).bindings) == 1


def test_design_is_created_with_its_name_type_and_owner(creation):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.designer.storage import load_project
    from row_bot.tools.conversation_setup_tool import create_design

    service, conversation, _ = creation
    with _in(conversation):
        result = json.loads(create_design("deck", "Harbour cleanup deck", "Three slides about the harbour"))
    assert result["ok"] is True and result["resource_kind"] == "design"
    binding = list_bindings(conversation).bindings[0]
    project = load_project(binding.resource_id)
    assert project.name == "Harbour cleanup deck" and project.mode == "deck"
    assert project.thread_id == conversation and project.thread_ownership == "resume"


def test_undo_removes_what_the_conversation_created_and_keeps_the_conversation(creation):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.designer.storage import load_project
    from row_bot.developer.storage import get_workspace
    from row_bot.tools.conversation_setup_tool import create_code_folder, create_design

    service, conversation, root = creation
    with _in(conversation):
        folder = json.loads(create_code_folder("Tiny date app"))
        design = json.loads(create_design("deck", "Harbour cleanup deck"))
    (root / "Drafts" / "Tiny date app" / "index.html").write_text("<p>date</p>", encoding="utf-8")
    for created in (folder, design):
        receipt = _command(service, conversation, "resource.discard", {"binding_id": created["binding_id"]})
        assert receipt["status"] == "completed"
    assert list_bindings(conversation).bindings == ()
    assert get_workspace(folder["resource_id"]) is None
    assert not (root / "Drafts" / "Tiny date app").exists()
    assert load_project(design["resource_id"]) is None
    assert service._metadata(conversation)["thread_id"] == conversation, "the conversation stays"


def test_undo_never_touches_a_folder_or_design_it_did_not_create(creation, tmp_path):  # noqa: F811
    from row_bot import threads
    from row_bot.application.client_platform import ClientPlatformError
    from row_bot.conversation_resources import bind, list_bindings
    from row_bot.developer.state import DeveloperWorkspace
    from row_bot.developer.storage import save_workspace
    from row_bot.tools.conversation_setup_tool import create_design

    service, conversation, _ = creation
    existing = tmp_path / "my-project"
    existing.mkdir()
    (existing / "keep.txt").write_text("mine", encoding="utf-8")
    save_workspace(DeveloperWorkspace(id="existing-project", name="My project", path=str(existing)))
    bind(conversation, "workspace", "existing-project",
         expected_revision=int(service._metadata(conversation)["client_revision"]))
    binding = list_bindings(conversation).bindings[0]
    with pytest.raises(ClientPlatformError, match="resource_not_discardable"):
        _command(service, conversation, "resource.discard", {"binding_id": binding.binding_id})
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "mine"
    # A design this conversation created but another conversation also uses stays.
    with _in(conversation):
        design = json.loads(create_design("deck", "Shared deck"))
    other = threads.create_thread("Other conversation")
    bind(other, "artifact", design["resource_id"], expected_revision=int(service._metadata(other)["client_revision"]))
    with pytest.raises(ClientPlatformError, match="resource_not_discardable"):
        _command(service, conversation, "resource.discard", {"binding_id": design["binding_id"]})


def test_rename_changes_the_design_and_folder_names(creation):  # noqa: F811
    from row_bot.designer.storage import load_project
    from row_bot.developer.storage import get_workspace
    from row_bot.tools.conversation_setup_tool import create_code_folder, create_design

    service, conversation, root = creation
    with _in(conversation):
        folder = json.loads(create_code_folder("Tiny date app"))
        design = json.loads(create_design("deck", "Harbour cleanup deck"))
    _command(service, conversation, "resource.rename", {"binding_id": folder["binding_id"], "name": "Date picker"})
    _command(service, conversation, "resource.rename", {"binding_id": design["binding_id"], "name": "Tides deck"})
    assert get_workspace(folder["resource_id"]).name == "Date picker"
    assert (root / "Drafts" / "Tiny date app").is_dir(), "the folder on disk keeps its name"
    assert load_project(design["resource_id"]).name == "Tides deck"


def test_the_follow_up_turn_works_in_the_folder_the_reply_created(creation, monkeypatch):  # noqa: F811
    from row_bot import threads
    from row_bot.application import workspace_setup
    from row_bot.conversation_resources import current_execution_context, list_bindings
    from row_bot.tools.conversation_setup_tool import create_code_folder

    service, conversation, _ = creation
    monkeypatch.setattr(workspace_setup, "generation_readiness", lambda *_: True)
    seen: list = []

    def commit(text: str):
        identity = str(uuid4())
        threads.append_checkpoint_messages(conversation, [AIMessage(id=identity, content=text)])
        return ("output_binding", {"native_message_id": identity,
                                   "checkpoint_revision": threads.get_latest_checkpoint_revision(conversation)})

    def stream(text, _enabled, config, *, stop_event):
        if text.startswith("[Continue in the new code folder]"):
            context = current_execution_context()
            seen.append(("follow-up", context.resolve("workspace").resource_id,
                         config["configurable"].get("internal_goal_continuation")))
            yield commit("Built it.")
            yield "done", "Built it."
            return
        seen.append(("reply", json.loads(create_code_folder("Tiny date app"))))
        yield commit("Setting up a code folder.")
        yield "done", "Setting up a code folder."

    service.stream_factory = stream
    receipt = service._start(conversation, {
        "submission_id": str(uuid4()), "text": "Build a tiny date app", "attachment_refs": [],
        "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"},
    }, resume=False, command_id=str(uuid4()))
    first = service.registry.get(receipt["execution_id"])
    assert first.producer_done.wait(10)
    for _ in range(100):
        if len(seen) == 2 and not service.registry.active(conversation):
            break
        __import__("time").sleep(0.05)
    created = seen[0][1]
    binding = list_bindings(conversation).bindings[0]
    assert created["binding_id"] == binding.binding_id
    assert seen[1] == ("follow-up", binding.resource_id, True)
    notes = [row for row in service.snapshot(conversation)["rows"] if row.get("note") == "continuation"]
    assert [row["blocks"][0]["text"] for row in notes] == ["Continuing in Tiny date app"]


@pytest.mark.parametrize("prompt", [
    "Build me a landing page",
    "Make a 3-slide deck about tides",
    "Create a workflow that summarises my inbox and deliver results in the app only",
])
def test_wording_alone_never_creates_or_binds_anything(creation, monkeypatch, prompt):  # noqa: F811
    from row_bot.application import workspace_setup
    from row_bot.conversation_resources import list_bindings

    service, conversation, root = creation
    monkeypatch.setattr(workspace_setup, "generation_readiness", lambda *_: True)

    def stream(_text, _enabled, _config, *, stop_event):
        yield "done", "An answer without tools."

    service.stream_factory = stream
    receipt = service._start(conversation, {
        "submission_id": str(uuid4()), "text": prompt, "attachment_refs": [],
        "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"},
    }, resume=False, command_id=str(uuid4()))
    assert service.registry.get(receipt["execution_id"]).producer_done.wait(10)
    assert list_bindings(conversation).bindings == ()
    assert not (root / "Drafts").exists()


def test_the_model_is_told_when_to_create_and_when_not_to(monkeypatch):
    from row_bot import agent

    token = agent._current_enabled_tool_names_var.set(("conversation_setup", "row_bot_status"))
    try:
        context = agent._agent_runtime_system_context()
    finally:
        agent._current_enabled_tool_names_var.reset(token)
    assert "build, make or write an app" in context
    assert "deck, slides, a presentation" in context
    assert "in the chat or the app only" in context
    assert "Never write a project's files loosely" in context
    assert "request_connection" in context


def test_cards_and_setup_approvals_are_specialised():
    from row_bot.application.approval_projection import project_approval_context
    from row_bot.application.conversation_traces import specialize_tool_result
    from row_bot.tools.conversation_setup_tool import request_connection

    created = specialize_tool_result({"name": "create_code_folder", "content": json.dumps({
        "ok": True, "kind": "resource_created", "resource_kind": "code", "resource_id": "workspace-1",
        "binding_id": "binding-1", "name": "Tiny date app", "display_summary": "Created code folder"})})
    assert (created.kind, created.resource_kind, created.binding_id, created.display_name) == (
        "resource_created", "code", "binding-1", "Tiny date app")
    connect = specialize_tool_result({"name": "request_connection", "content": request_connection("google")})
    assert (connect.kind, connect.setup_target, connect.settings_page) == ("setup_needed", "google", "accounts")
    approval = project_approval_context({"tool": "row_bot_update_setting", "label": "Turn on Web Search",
                                         "description": "Row-Bot needs Web Search for this.",
                                         "args": {"setting": "tool_toggle", "value": "web_search:on"},
                                         "setup": {"kind": "tool", "label": "Web Search"}})
    assert approval["setup"] == {"kind": "tool", "label": "Web Search"}
    assert "setup" not in project_approval_context({"tool": "workspace_file_delete", "args": {}})
