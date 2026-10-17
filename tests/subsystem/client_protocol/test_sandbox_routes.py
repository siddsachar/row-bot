"""The client's Prepare sandbox and sandbox image (F18), through the real v1 API with a fake Docker.

A conversation's code folder shows its sandbox: Docker, the image (with an offer to download it
when it isn't there), what Prepare would install and the latest job. The person's own Prepare is
confirmed in the app and refused under Block; it runs with the network on only for the install,
and Stop needs no approval. Docker is a fake: nothing is pulled or installed.
"""
from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, _remote_client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.developer.test_sandbox_prepare import IMAGE, FakeDocker, _project

pytestmark = pytest.mark.subsystem


@pytest.fixture
def chat(service, tmp_path, monkeypatch):  # noqa: F811
    """A conversation whose code folder runs in the Docker sandbox, with a fake Docker."""
    from row_bot import conversation_resources, threads
    from row_bot.developer import sandbox_prepare, sandbox_runtime, storage

    monkeypatch.setattr(storage, "DEVELOPER_DIR", tmp_path / "developer")
    monkeypatch.setattr(storage, "WORKSPACES_PATH", tmp_path / "developer" / "workspaces.json")
    monkeypatch.setattr(sandbox_runtime, "SANDBOX_ROOT", tmp_path / "developer" / "sandboxes")
    root = _project(tmp_path, {"requirements.txt": "httpx\n", "tests/test_one.py": "def test_one():\n    pass\n"})
    workspace = storage.add_or_update_local_workspace(str(root))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker", sandbox_network="off",
                                             sandbox_image=IMAGE)
    conversation = threads.create_thread("Tide tests", seed_default_skills=False)
    conversation_resources.bind(conversation, "workspace", workspace.id, expected_revision="0")
    docker = FakeDocker()
    docker.images.clear()
    docker.install(monkeypatch, sandbox_prepare)
    yield conversation, docker, storage, workspace
    docker.hold.set()
    sandbox_prepare.stop_all(timeout=2)


def _start(client, headers, conversation, kind="prepare", body=None):
    return client.post(f"/api/v1/conversations/{conversation}/sandbox/{kind}", headers=headers,
                       json={"confirmed": True} if body is None else body)


def test_the_sandbox_says_the_image_is_missing_and_what_prepare_would_install(service, chat):  # noqa: F811
    conversation, _docker, _storage, _workspace = chat
    with _client(service) as client:
        _, headers = bootstrap(client)
        response = client.get(f"/api/v1/conversations/{conversation}/sandbox", headers=headers)
        assert response.status_code == 200, response.text
        status = response.json()
    assert status["execution_mode"] == "docker" and status["network"] == "off"
    assert (status["runtime"], status["image_present"], status["image_download"]) == ("ready", False, "about 570 MB")
    assert status["prepare"] == {"summary": "pytest and the packages in requirements.txt",
                                 "sources": ["requirements.txt"],
                                 "commands": ["python -m pip install --user -r requirements.txt pytest"]}
    assert status["job"] is None


def test_the_image_is_downloaded_on_the_persons_click(service, chat):  # noqa: F811
    conversation, docker, _storage, _workspace = chat
    from row_bot.developer import sandbox_prepare

    with _client(service) as client:
        _, headers = bootstrap(client)
        started = _start(client, headers, conversation, "pull")
        assert started.status_code == 200, started.text
        job = sandbox_prepare.get(started.json()["job_id"])
        assert sandbox_prepare.wait(job, 5)
        status = client.get(f"/api/v1/conversations/{conversation}/sandbox", headers=headers).json()
    assert status["image_present"] is True and status["job"]["status"] == "ready"
    assert status["job"]["kind"] == "pull" and docker.runs == []


def test_the_persons_prepare_runs_with_the_network_on_only_for_the_install(service, chat):  # noqa: F811
    conversation, docker, storage, workspace = chat
    from row_bot.developer import sandbox_prepare

    docker.images.add(IMAGE)
    docker.hold.clear()
    with _client(service) as client:
        _, headers = bootstrap(client)
        started = _start(client, headers, conversation)
        assert started.status_code == 200, started.text
        view = started.json()
        assert view["by"] == "you" and view["kind"] == "prepare"
        assert docker.started.wait(5)
        live = client.get(f"/api/v1/sandbox-jobs/{view['job_id']}", headers=headers).json()
        assert (live["status"], live["network"]) == ("installing", "on")
        docker.hold.set()
        assert sandbox_prepare.wait(sandbox_prepare.get(view["job_id"]), 5)
        done = client.get(f"/api/v1/sandbox-jobs/{view['job_id']}", headers=headers).json()
        page = client.get("/api/v1/sandbox-jobs", params={"conversation_id": conversation}, headers=headers).json()
    assert (done["status"], done["network"], done["network_off_confirmed"]) == ("ready", "off", True)
    assert done["network_on_at"] and done["network_off_at"]
    assert [item["job_id"] for item in page["items"]] == [view["job_id"]] and page["running"] == 0
    assert storage.get_workspace(workspace.id).sandbox_network == "off"
    assert not any(name.endswith(view["job_id"]) for name in docker.containers)


def test_stop_needs_no_approval_and_turns_the_network_off(service, chat):  # noqa: F811
    conversation, docker, _storage, _workspace = chat
    from row_bot.developer import sandbox_prepare

    docker.images.add(IMAGE)
    docker.hold.clear()
    with _client(service) as client:
        _, headers = bootstrap(client)
        job_id = _start(client, headers, conversation).json()["job_id"]
        assert docker.started.wait(5)
        stopped = client.post(f"/api/v1/sandbox-jobs/{job_id}/stop", headers=headers)
        assert stopped.status_code == 200, stopped.text
        assert sandbox_prepare.wait(sandbox_prepare.get(job_id), 5)
        done = client.get(f"/api/v1/sandbox-jobs/{job_id}", headers=headers).json()
        assert client.post("/api/v1/sandbox-jobs/prep-00000000/stop", headers=headers).status_code == 404
        assert client.get("/api/v1/sandbox-jobs/not-a-job", headers=headers).status_code == 404
    assert (done["status"], done["stopped_by"], done["network"]) == ("stopped", "you", "off")
    assert done["network_off_confirmed"]


@pytest.mark.parametrize("level, allowed", [("block", False), ("approve", True), ("auto", True), ("full", True)])
def test_the_persons_prepare_at_each_level(service, chat, level, allowed):  # noqa: F811
    from row_bot import full_autonomy, threads

    conversation, docker, _storage, _workspace = chat
    docker.images.add(IMAGE)
    if level == "full":
        full_autonomy.confirm()
    threads._set_thread_approval_mode(conversation, level)
    with _client(service) as client:
        _, headers = bootstrap(client)
        response = _start(client, headers, conversation)
    if allowed:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 403 and response.json()["code"] == "sandbox_prepare_blocked"
        assert docker.runs == [] and not [call for call in docker.calls if call[:1] == ["pull"]]


def test_a_start_must_be_confirmed_by_the_person(service, chat):  # noqa: F811
    conversation, docker, _storage, _workspace = chat
    with _client(service) as client:
        _, headers = bootstrap(client)
        for body in ({}, {"confirmed": False}, {"confirmed": "yes"}):
            response = _start(client, headers, conversation, body=body)
            assert response.status_code == 422, response.text
    assert docker.calls == []


def test_another_device_can_prepare_too(service, chat):  # noqa: F811
    conversation, docker, _storage, _workspace = chat
    docker.images.add(IMAGE)
    client, _active = _remote_client(service)
    with client:
        _, headers = bootstrap(client)
        assert _start(client, headers, conversation).status_code == 200


def test_a_conversation_without_a_docker_code_folder_has_nothing_to_prepare(service, chat):  # noqa: F811
    from row_bot import threads

    conversation, docker, storage, workspace = chat
    plain = threads.create_thread("No folder", seed_default_skills=False)
    with _client(service) as client:
        _, headers = bootstrap(client)
        missing = client.get(f"/api/v1/conversations/{plain}/sandbox", headers=headers)
        assert missing.status_code == 404 and missing.json()["code"] == "sandbox_unavailable"
        storage.set_workspace_execution_settings(workspace.id, execution_mode="local")
        local = _start(client, headers, conversation)
        assert local.status_code == 409 and local.json()["code"] == "sandbox_docker_required"
        status = client.get(f"/api/v1/conversations/{conversation}/sandbox", headers=headers).json()
    assert status["execution_mode"] == "local" and status["runtime"] == "not_checked"
    assert docker.calls == []
