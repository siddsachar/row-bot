"""Attaching a code folder from a device with no folder picker (F19), through the real v1 API.

Any signed-in device (a phone, another computer) may list the workspace root's folders and
choose one; the choice registers through Add resource's reviewed ``resource.setup`` with the
picker's validation. Nothing outside the root is listed or accepted. A clone into the root
reaches the network, so it follows the conversation's approval level: Block refuses, Ask
needs the person's confirmation, Auto and Hands-off go ahead on the person's click.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

REPO = "https://example.test/team/demo.git"


@pytest.fixture
def root_api(service, tmp_path, monkeypatch):  # noqa: F811
    """A real service with an isolated workspace root, signed in from another device."""
    from row_bot.api.v1.routes import create_client_platform_app
    from row_bot.access.config import AccessConfig, DeploymentMode
    from row_bot.access.request_context import SessionIdentity
    from row_bot.developer import storage, workspace_folders

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    service.readiness_factory = lambda _: False
    root = (tmp_path / "Row-Bot").resolve()
    (root / "Projects" / "tide-app" / ".git").mkdir(parents=True)
    (root / "Projects" / "notes").mkdir()
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(workspace_folders, "workspace_root", lambda: root)
    app = create_client_platform_app(
        service, access_config=AccessConfig(deployment_mode=DeploymentMode.SERVER),
        session_authenticator=lambda scope, provenance: SessionIdentity("phone", "phone-session"),
        choices=lambda: {"models": [], "capabilities": []})
    # A phone on the network, not this computer's desktop window.
    with TestClient(app, base_url="http://localhost", client=("192.168.1.20", 50000)) as client:
        _, headers = bootstrap(client)
        yield SimpleNamespace(service=service, storage=storage, root=root, client=client, headers=headers,
                              outside=tmp_path / "outside")


def _command(api, payload, *, kind="resource.setup", target=None, revision="0"):
    identity = str(uuid4())
    path = "/api/v1/resources/commands" if target is None else f"/api/v1/conversations/{target}/commands"
    return api.client.post(path, headers={**api.headers, "Idempotency-Key": identity}, json={
        "command_id": identity, "client_session_id": api.headers["X-Client-Session"], "type": kind,
        "expected_revision": revision, "payload": payload})


def test_another_device_lists_the_roots_folders_without_host_paths(root_api):
    response = root_api.client.get("/api/v1/workspace-folders", headers=root_api.headers)
    assert response.status_code == 200, response.text
    assert [item["name"] for item in response.json()["folders"]] == ["Projects"]
    inner = root_api.client.get("/api/v1/workspace-folders", params={"path": "Projects"},
                                headers=root_api.headers)
    assert inner.status_code == 200, inner.text
    assert [item["path"] for item in inner.json()["folders"]] == ["Projects/notes", "Projects/tide-app"]
    assert str(root_api.root) not in inner.text


@pytest.mark.parametrize("path", ["..", "Projects/../..", "/", "C:/Windows", "Projects\\tide-app", ".git",
                                  "%2e%2e/%2e%2e", "Projects/tide-app/.git"])
def test_listing_outside_the_root_is_refused(root_api, path):
    response = root_api.client.get("/api/v1/workspace-folders", params={"path": path}, headers=root_api.headers)
    assert response.status_code in {403, 404}, response.text
    assert response.json()["code"] in {"workspace_folder_refused", "workspace_folder_unavailable"}
    assert str(root_api.outside) not in response.text


def test_listing_needs_a_signed_in_session(root_api):
    response = root_api.client.get("/api/v1/workspace-folders")
    assert response.status_code == 401


def test_a_chosen_folder_registers_and_binds_from_another_device(root_api):
    response = _command(root_api, {"kind": "workspace", "intent": "create", "root_folder": "Projects/tide-app"})
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["status"] == "completed"
    assert str(root_api.root) not in response.text
    saved = root_api.storage.get_workspace(receipt["resource_id"])
    assert Path(saved.path) == root_api.root / "Projects" / "tide-app"


@pytest.mark.parametrize("path", ["..", "Projects/..", "../outside", "Projects/tide-app/.git", "/tmp", "C:/"])
def test_a_choice_outside_the_root_is_refused_before_anything_runs(root_api, path):
    response = _command(root_api, {"kind": "workspace", "intent": "create", "root_folder": path})
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "workspace_folder_refused"
    assert root_api.storage.list_workspaces() == []


def test_the_root_itself_is_never_the_code_folder(root_api):
    response = _command(root_api, {"kind": "workspace", "intent": "create", "root_folder": ""})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "workspace_folder_root"
    assert root_api.storage.list_workspaces() == []


def test_a_root_folder_and_a_picker_grant_are_never_both_given(root_api):
    response = _command(root_api, {"kind": "workspace", "intent": "create", "root_folder": "Projects/notes",
                                   "folder_grant": "a" * 43})
    assert response.status_code == 422
    assert root_api.storage.list_workspaces() == []


@pytest.fixture
def fake_git(root_api, monkeypatch):
    from row_bot.developer import client_clone

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        if "clone" in args:
            Path(args[-1]).joinpath(".git").mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(stdout="")
        return SimpleNamespace(stdout=REPO + "\n")

    monkeypatch.setattr(client_clone.subprocess, "run", fake_run)
    return calls


def _conversation(api, level: str) -> str:
    from row_bot import threads

    conversation = str(uuid4())
    threads.create_thread("Clone here", thread_id=conversation)
    threads._set_thread_approval_mode(conversation, level)
    api.service._metadata(conversation)
    return conversation


def _clone(api, conversation, *, confirm: bool | None):
    payload = {"kind": "workspace", "intent": "create", "root_folder": "Projects",
               "clone_workspace": {"repo_url": REPO}}
    if confirm is not None:
        payload["confirm_network"] = confirm
    revision = str(api.service._metadata(conversation)["client_revision"])
    return _command(api, payload, target=conversation, revision=revision)


def test_clone_into_the_root_under_ask_needs_the_persons_confirmation(root_api, fake_git):
    conversation = _conversation(root_api, "approve")
    unconfirmed = _clone(root_api, conversation, confirm=None)
    assert unconfirmed.status_code == 409 and unconfirmed.json()["code"] == "clone_confirmation_required"
    assert fake_git == [] and not (root_api.root / "Projects" / "demo").exists()
    confirmed = _clone(root_api, conversation, confirm=True)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "completed"
    assert sum("clone" in args for args in fake_git) == 1
    assert (root_api.root / "Projects" / "demo" / ".git").is_dir()


@pytest.mark.parametrize("level", ["auto", "full"])
def test_clone_into_the_root_goes_ahead_on_the_persons_click_under_auto_and_hands_off(root_api, fake_git, level):
    from row_bot import full_autonomy

    if level == "full":
        full_autonomy.confirm()
    conversation = _conversation(root_api, level)
    response = _clone(root_api, conversation, confirm=None)
    assert response.status_code == 200, response.text
    assert sum("clone" in args for args in fake_git) == 1


def test_clone_is_refused_under_block_even_when_confirmed(root_api, fake_git):
    conversation = _conversation(root_api, "block")
    response = _clone(root_api, conversation, confirm=True)
    assert response.status_code == 403 and response.json()["code"] == "clone_blocked"
    assert fake_git == [] and not (root_api.root / "Projects" / "demo").exists()


def test_an_address_row_bot_never_clones_is_refused_first(root_api, fake_git):
    conversation = _conversation(root_api, "block")
    payload = {"kind": "workspace", "intent": "create", "root_folder": "Projects",
               "clone_workspace": {"repo_url": "file:///etc"}, "confirm_network": True}
    revision = str(root_api.service._metadata(conversation)["client_revision"])
    response = _command(root_api, payload, target=conversation, revision=revision)
    assert response.json()["code"] == "clone_source_invalid"
    assert fake_git == []


def test_add_resource_without_a_conversation_clones_at_the_default_level(root_api, fake_git):
    unconfirmed = _command(root_api, {"kind": "workspace", "intent": "create", "root_folder": "",
                                      "clone_workspace": {"repo_url": REPO}})
    assert unconfirmed.json()["code"] == "clone_confirmation_required"
    confirmed = _command(root_api, {"kind": "workspace", "intent": "create", "root_folder": "",
                                    "clone_workspace": {"repo_url": REPO}, "confirm_network": True})
    assert confirmed.status_code == 200, confirmed.text
    assert (root_api.root / "demo" / ".git").is_dir()
