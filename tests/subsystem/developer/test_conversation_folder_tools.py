"""The assistant uses an existing code folder or clones a repository in the chat (B277).

The model never chooses a path: a registered folder is bound by name, and
anything else is a card on which the person picks the folder through the same
picker and reviewed ``resource.setup`` path as Add resource. The card's action
is simulated here with that setup command, run as the route runs it once the
picker's grant is consumed; no Git or network is used.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.developer.test_conversation_creation import creation  # noqa: F401

pytestmark = pytest.mark.subsystem

REPO = "https://github.com/example/demo.git"


def _in(conversation):
    from row_bot.conversation_resources import execution_context

    return execution_context(conversation)


def _registered(tmp_path: Path, identity: str, name: str, *, repo_url: str = "") -> Path:
    from row_bot.developer.state import DeveloperWorkspace
    from row_bot.developer.storage import save_workspace

    folder = tmp_path / "folders" / identity
    folder.mkdir(parents=True)
    save_workspace(DeveloperWorkspace(id=identity, name=name, path=str(folder), repo_url=repo_url))
    return folder


def _cards(monkeypatch, answer):
    """Record each card the tool shows; ``answer`` plays the person's part."""
    import langgraph.types

    shown: list[dict] = []

    def interrupt(payload):
        shown.append(payload)
        return answer(payload)

    monkeypatch.setattr(langgraph.types, "interrupt", interrupt)
    return shown


def _no_cards(monkeypatch):
    import langgraph.types

    monkeypatch.setattr(langgraph.types, "interrupt", lambda payload: pytest.fail(f"a card was shown: {payload}"))


def _card_setup(service, conversation, payload, folder=None):
    """What the card's Choose folder / Choose where sends after the pick."""
    from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder

    identity = str(uuid4())
    return service.execute(owner_id="card", idempotency_key=identity, command={
        "type": "resource.setup", "command_id": identity,
        "expected_revision": str(service._metadata(conversation)["client_revision"]),
        "payload": payload,
    }, target=conversation, authorized_folder=(
        AuthorizedWorkspaceFolder(folder, folder, "picker-grant") if folder is not None else None))


def test_a_unique_registered_name_binds_without_a_card(creation, monkeypatch, tmp_path):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer.storage import list_workspaces
    from row_bot.tools.conversation_setup_tool import use_code_folder

    service, conversation, _ = creation
    folder = _registered(tmp_path, "my-project", "My project")
    (folder / "keep.txt").write_text("mine", encoding="utf-8")
    _registered(tmp_path, "other", "Other project")
    _no_cards(monkeypatch)
    with _in(conversation):
        result = json.loads(use_code_folder("my PROJECT"))
    assert result["ok"] is True and result["kind"] == "resource_bound"
    assert (result["resource_kind"], result["resource_id"], result["name"]) == ("code", "my-project", "My project")
    bindings = list_bindings(conversation).bindings
    assert [(item.kind, item.resource_id, item.binding_id) for item in bindings] == [
        ("workspace", "my-project", result["binding_id"])]
    # Bound mid-turn, so the work continues in a follow-up turn.
    assert "next reply" in result["next"] or "next step" in result["next"]
    assert (folder / "keep.txt").read_text(encoding="utf-8") == "mine"
    assert {item.id for item in list_workspaces()} == {"my-project", "other"}


def test_the_follow_up_turn_works_in_the_folder_bound_by_name(creation, monkeypatch, tmp_path):  # noqa: F811
    from langchain_core.messages import AIMessage
    from row_bot import threads
    from row_bot.application import workspace_setup
    from row_bot.conversation_resources import current_execution_context
    from row_bot.tools.conversation_setup_tool import use_code_folder

    service, conversation, _ = creation
    _registered(tmp_path, "my-project", "My project")
    monkeypatch.setattr(workspace_setup, "generation_readiness", lambda *_: True)
    seen: list = []

    def commit(text: str):
        identity = str(uuid4())
        threads.append_checkpoint_messages(conversation, [AIMessage(id=identity, content=text)])
        return ("output_binding", {"native_message_id": identity,
                                   "checkpoint_revision": threads.get_latest_checkpoint_revision(conversation)})

    def stream(text, _enabled, _config, *, stop_event):
        if text.startswith("[Continue in the code folder]"):
            seen.append(("follow-up", current_execution_context().resolve("workspace").resource_id))
            yield commit("Working in it.")
            yield "done", "Working in it."
            return
        seen.append(("reply", json.loads(use_code_folder("My project"))))
        yield commit("Using your folder.")
        yield "done", "Using your folder."

    service.stream_factory = stream
    receipt = service._start(conversation, {
        "submission_id": str(uuid4()), "text": "Work on my existing My project folder", "attachment_refs": [],
        "model_selection": {"provider_id": "fixture", "model_ref": "fixture::model"},
    }, resume=False, command_id=str(uuid4()))
    assert service.registry.get(receipt["execution_id"]).producer_done.wait(10)
    for _ in range(100):
        if len(seen) == 2 and not service.registry.active(conversation):
            break
        __import__("time").sleep(0.05)
    assert seen[0][1]["kind"] == "resource_bound"
    assert seen[1] == ("follow-up", "my-project")


def test_several_matching_names_ask_which_on_a_card(creation, monkeypatch, tmp_path):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.tools.conversation_setup_tool import use_code_folder

    service, conversation, _ = creation
    app = _registered(tmp_path, "web-app", "Web app")
    _registered(tmp_path, "web-site", "Web site")
    _registered(tmp_path, "notes", "Notes")
    shown = _cards(monkeypatch, lambda _payload: False)
    with _in(conversation):
        declined = json.loads(use_code_folder("web"))
    assert len(shown) == 1
    setup = shown[0]["setup"]
    assert setup["kind"] == "folder"
    assert sorted((item["resource_id"], item["name"]) for item in setup["folders"]) == [
        ("web-app", "Web app"), ("web-site", "Web site")]
    assert all(set(item) == {"resource_id", "name", "revision"} for item in setup["folders"])
    assert str(app.parent) not in json.dumps(shown), "the card never carries a folder path"
    assert declined["ok"] is False and declined["kind"] == "setup_declined"
    assert list_bindings(conversation).bindings == ()
    # The person picks one on the card: the same binding the reuse list makes.
    picked = setup["folders"][0]

    def choose(payload):
        _card_setup(service, conversation, {"kind": "workspace", "intent": "add",
                                            "resource_id": picked["resource_id"],
                                            "expected_resource_revision": picked["revision"]})
        return True

    _cards(monkeypatch, choose)
    with _in(conversation):
        bound = json.loads(use_code_folder("web"))
    assert bound["ok"] is True and bound["resource_id"] == picked["resource_id"]
    # The folder the person picked is the one they meant, whatever its name.
    assert "one code folder" not in bound["next"]
    assert [item.resource_id for item in list_bindings(conversation).bindings] == [picked["resource_id"]]


def test_two_folders_with_the_same_name_are_never_guessed(creation, monkeypatch, tmp_path):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.tools.conversation_setup_tool import use_code_folder

    service, conversation, _ = creation
    _registered(tmp_path, "site-a", "Site")
    _registered(tmp_path, "site-b", "Site")
    shown = _cards(monkeypatch, lambda _payload: False)
    with _in(conversation):
        json.loads(use_code_folder("Site"))
    assert sorted(item["resource_id"] for item in shown[0]["setup"]["folders"]) == ["site-a", "site-b"]
    assert list_bindings(conversation).bindings == ()


def test_no_match_offers_choose_folder_and_the_picked_folder_is_bound(creation, monkeypatch, tmp_path):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer.storage import get_workspace
    from row_bot.tools.conversation_setup_tool import use_code_folder

    service, conversation, _ = creation
    _registered(tmp_path, "notes", "Notes")
    picked = tmp_path / "picked" / "tide-app"
    picked.mkdir(parents=True)
    (picked / "index.html").write_text("<p>tides</p>", encoding="utf-8")

    def choose(payload):
        receipt = _card_setup(service, conversation, {"kind": "workspace", "intent": "create"}, picked)
        assert receipt["status"] == "completed"
        return True

    shown = _cards(monkeypatch, choose)
    with _in(conversation):
        result = json.loads(use_code_folder("tide app"))
    assert shown[0]["setup"]["kind"] == "folder" and shown[0]["setup"]["folders"] == []
    assert "tide app" in shown[0]["description"]
    assert result["ok"] is True and result["kind"] == "resource_bound" and result["name"] == "tide-app"
    assert "one code folder" not in result["next"], "the pick answers the request, it isn't a second folder"
    binding = list_bindings(conversation).bindings[0]
    assert binding.binding_id == result["binding_id"]
    assert Path(get_workspace(binding.resource_id).path) == picked
    assert (picked / "index.html").read_text(encoding="utf-8") == "<p>tides</p>", "files untouched"
    assert not (picked / ".git").exists()


@pytest.mark.parametrize("path_like", ["C:\\Users\\someone\\secret", "/home/someone/secret", "~/secret",
                                       "../secret"])
def test_a_path_from_the_model_is_never_used(creation, monkeypatch, tmp_path, path_like):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer.storage import list_workspaces
    from row_bot.tools.conversation_setup_tool import ConversationSetupTool

    service, conversation, _ = creation
    shown = _cards(monkeypatch, lambda _payload: False)
    tool = next(item for item in ConversationSetupTool().as_langchain_tools() if item.name == "use_code_folder")
    secret = tmp_path / "secret"
    secret.mkdir()
    with _in(conversation):
        tool.invoke({"name": path_like, "path": str(secret)})
    assert shown[0]["setup"] == {"kind": "folder", "label": "Use an existing folder", "folders": []}
    assert path_like not in json.dumps(shown) and str(secret) not in json.dumps(shown)
    assert list_workspaces() == [] and list_bindings(conversation).bindings == ()


def test_a_conversation_with_a_code_folder_keeps_working_in_it(creation, monkeypatch, tmp_path):  # noqa: F811
    from row_bot.conversation_resources import bind, list_bindings
    from row_bot.tools.conversation_setup_tool import clone_repository, use_code_folder

    service, conversation, _ = creation
    _registered(tmp_path, "demo", "demo", repo_url=REPO)
    _registered(tmp_path, "notes", "Notes")
    bind(conversation, "workspace", "demo", expected_revision=int(service._metadata(conversation)["client_revision"]))
    _no_cards(monkeypatch)
    with _in(conversation):
        again = json.loads(use_code_folder("Notes"))
        same_clone = json.loads(clone_repository(REPO))
        other_clone = json.loads(clone_repository("https://github.com/example/other.git"))
    assert again["ok"] is True and again["resource_id"] == "demo"
    assert "one code folder" in again["next"]
    # Bound before this turn started, so the turn goes on in it now.
    assert "now" in again["next"]
    assert same_clone["ok"] is True and same_clone["resource_id"] == "demo"
    assert other_clone["ok"] is False and other_clone["kind"] == "existing"
    assert [item.resource_id for item in list_bindings(conversation).bindings] == ["demo"]


@pytest.mark.parametrize("url", [
    "https://someone:token@github.com/example/demo.git",
    "https://token@github.com/example/demo.git",
    "file:///C:/private/repo",
    "ext::sh -c touch% /tmp/x",
    "C:\\private\\repo",
    "/home/someone/repo",
    "https://github.com/example/demo.git?ref=main",
    "ftp://example.com/demo.git",
    "",
])
def test_a_bad_repository_url_is_refused_before_any_card(creation, monkeypatch, url):  # noqa: F811
    from row_bot.developer import client_clone
    from row_bot.tools.conversation_setup_tool import clone_repository

    service, conversation, _ = creation
    _no_cards(monkeypatch)
    monkeypatch.setattr(client_clone.subprocess, "run", lambda *a, **k: pytest.fail("git ran"))
    with _in(conversation):
        result = json.loads(clone_repository(url))
    assert result["ok"] is False and result["kind"] == "invalid_repository"
    # The refusal never echoes what was passed (it may hold a token).
    assert not url or url not in json.dumps(result)


def test_the_clone_card_shows_the_url_and_clones_on_the_reviewed_path_once(creation, monkeypatch, tmp_path):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer import client_clone
    from row_bot.developer.storage import get_workspace
    from row_bot.tools.conversation_setup_tool import clone_repository

    service, conversation, _ = creation
    parent = tmp_path / "chosen-parent"
    parent.mkdir()
    clones: list = []

    def fake_git(args, **_kwargs):
        if "clone" in args:
            clones.append(args)
            (parent / "demo" / ".git").mkdir()
            return SimpleNamespace(stdout="")
        return SimpleNamespace(stdout=REPO + "\n")

    monkeypatch.setattr(client_clone.subprocess, "run", fake_git)

    def choose_where(payload):
        assert clones == [], "nothing is cloned before the person chooses where"
        receipt = _card_setup(service, conversation, {"kind": "workspace", "intent": "create",
                                                      "clone_workspace": {"repo_url": payload["setup"]["repo_url"]}},
                              parent)
        assert receipt["status"] == "completed"
        return True

    shown = _cards(monkeypatch, choose_where)
    with _in(conversation):
        result = json.loads(clone_repository("  " + REPO + " "))
    assert shown[0]["setup"] == {"kind": "clone", "label": "demo", "repo_url": REPO}
    assert result["ok"] is True and result["kind"] == "resource_bound" and result["name"] == "demo"
    assert len(clones) == 1
    workspace = get_workspace(list_bindings(conversation).bindings[0].resource_id)
    assert Path(workspace.path) == parent / "demo" and workspace.repo_url == REPO


def test_an_uncertain_clone_is_kept_never_repeated_and_nothing_is_bound(creation, monkeypatch, tmp_path):  # noqa: F811
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer import client_clone
    from row_bot.developer.client_workspace import AuthorizedWorkspaceFolder
    from row_bot.tools.conversation_setup_tool import clone_repository

    service, conversation, _ = creation
    parent = tmp_path / "chosen-parent"
    parent.mkdir()
    clones: list = []

    def failing_git(args, **_kwargs):
        if "clone" in args:
            clones.append(args)
            (parent / "demo" / "partial.txt").write_text("retained")
            raise subprocess.CalledProcessError(128, args)
        return SimpleNamespace(stdout=REPO + "\n")

    monkeypatch.setattr(client_clone.subprocess, "run", failing_git)

    def choose_then_check(payload):
        first = _card_setup(service, conversation, {"kind": "workspace", "intent": "create",
                                                    "clone_workspace": {"repo_url": REPO}}, parent)
        assert first["status"] == "partial" and first["code"] == "workspace_clone_unconfirmed"
        # Check clone status: the same parent picked again, continuation only inspects.
        identity = str(uuid4())
        again = service.execute(owner_id="card", idempotency_key=identity, command={
            "type": "resource.continue", "command_id": identity,
            "expected_revision": str(service._metadata(conversation)["client_revision"]),
            "payload": {"setup_command_id": first["command_id"]},
        }, target=conversation, authorized_folder=AuthorizedWorkspaceFolder(parent, parent, "picker-grant-2"))
        assert again["status"] == "partial" and again["code"] == "workspace_clone_unconfirmed"
        return True

    _cards(monkeypatch, choose_then_check)
    with _in(conversation):
        result = json.loads(clone_repository(REPO))
    assert len(clones) == 1
    assert (parent / "demo" / "partial.txt").read_text() == "retained"
    assert list_bindings(conversation).bindings == ()
    assert result["ok"] is False and result["kind"] == "setup_declined"


def test_developer_tools_off_asks_first_and_not_now_shows_no_folder_card(creation, monkeypatch):  # noqa: F811
    from row_bot.tools import registry
    from row_bot.tools.conversation_setup_tool import use_code_folder

    service, conversation, _ = creation
    monkeypatch.setattr(registry, "is_enabled", lambda name: False)
    shown = _cards(monkeypatch, lambda _payload: False)
    with _in(conversation):
        result = json.loads(use_code_folder())
    assert [item["setup"]["kind"] for item in shown] == ["tool"]
    assert result["ok"] is False and result["kind"] == "setup_declined"
    assert "chose Not now" in result["error"]


def test_the_model_is_told_to_use_these_instead_of_the_shell_or_a_path():
    from row_bot import agent

    token = agent._current_enabled_tool_names_var.set(("conversation_setup",))
    try:
        context = agent._agent_runtime_system_context()
    finally:
        agent._current_enabled_tool_names_var.reset(token)
    assert "call use_code_folder" in context and "call clone_repository" in context
    assert "Never run git clone in the shell and never ask for a folder path" in context


def test_folder_cards_are_specialised_and_projected_bounded():
    from row_bot.application.approval_projection import project_approval_context
    from row_bot.application.conversation_traces import specialize_tool_result

    bound = specialize_tool_result({"name": "clone_repository", "content": json.dumps({
        "ok": True, "kind": "resource_bound", "resource_kind": "code", "resource_id": "workspace-1",
        "binding_id": "binding-1", "name": "demo", "display_summary": "Using code folder “demo”"})})
    assert (bound.kind, bound.resource_kind, bound.binding_id, bound.display_name) == (
        "resource_bound", "code", "binding-1", "demo")
    many = [{"resource_id": f"folder-{index}", "name": f"Folder {index}", "revision": "r", "path": "C:\\x"}
            for index in range(12)]
    folder = project_approval_context({"tool": "use_code_folder", "description": "Pick one.",
                                       "setup": {"kind": "folder", "label": "Use an existing folder",
                                                 "folders": many}})
    assert folder["setup"]["kind"] == "folder" and len(folder["setup"]["folders"]) == 8
    assert all(set(item) == {"resource_id", "name", "revision"} for item in folder["setup"]["folders"])
    clone = project_approval_context({"tool": "clone_repository",
                                      "setup": {"kind": "clone", "label": "demo", "repo_url": REPO}})
    assert clone["setup"] == {"kind": "clone", "label": "demo", "repo_url": REPO}
    assert "setup" not in project_approval_context({"tool": "clone_repository",
                                                    "setup": {"kind": "clone", "label": "demo"}})
