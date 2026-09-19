"""Bound artifact edits through real command admission and isolated stores."""
from uuid import uuid4

import pytest

from row_bot.designer import client_service as artifacts, storage
from tests.subsystem.client_protocol.test_artifact_modes_setup import artifact_service, _create  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _client, _command, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_platform.test_workspace_setup_integrity import _completed

pytestmark = pytest.mark.subsystem


def editing(client, headers, created):
    url = f"/api/v1/conversations/{created['conversation_id']}/artifacts/{created['binding_id']}/editing"
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def target(client, headers, created, revision):
    response = client.get(f"/api/v1/conversations/{created['conversation_id']}/workspace", headers=headers)
    assert response.status_code == 200, response.text
    workspace = response.json()
    bound = next(item for item in workspace["resources"] if item["binding"]["binding_id"] == created["binding_id"])
    binding = bound["binding"]
    return {"kind": "artifact", "binding_id": binding["binding_id"], "binding_revision": binding["revision"],
            "resource_id": created["resource_id"], "resource_revision": revision}


@pytest.mark.parametrize("mode", ["deck", "document", "landing", "app_mockup", "storyboard"])
def test_real_edit_and_response_loss_reuse_exact_command_without_changing_chat(artifact_service, mode):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, mode))
        state = editing(client, headers, created)
        payload = {"target": target(client, headers, created, state["resource_revision"]),
                   "operation": "project_properties", "name": "Edited synthetic design"}
        identity, key = str(uuid4()), str(uuid4())
        kwargs = {"target": created["conversation_id"], "revision": created["revision"], "command_id": identity, "key": key}
        result = _command(client, headers, "artifact.edit", payload, **kwargs)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "completed"
        assert result.json()["conversation_id"] == created["conversation_id"]
        assert _command(client, headers, "artifact.edit", payload, **kwargs).json() == result.json()
        current = editing(client, headers, created)
        assert current["name"] == "Edited synthetic design" and current["history_count"] == 1
        assert len(artifact_service.list_conversations()["items"]) == 1
        stale = _command(client, headers, "artifact.edit", payload,
                         target=created["conversation_id"], revision=created["revision"])
        assert stale.status_code == 409 and stale.json()["code"] == "resource_revision_conflict"


def test_wrong_binding_never_edits_another_resource(artifact_service):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        first = _completed(_create(client, headers, "deck"))
        second = _completed(_create(client, headers, "document"))
        state = editing(client, headers, first)
        selected = target(client, headers, first, state["resource_revision"])
        selected["resource_id"] = second["resource_id"]
        before = {path.name: path.read_bytes() for path in storage.PROJECTS_DIR.glob("*.json")}
        response = _command(client, headers, "artifact.edit", {"target": selected, "operation": "project_properties", "name": "Wrong"},
                            target=first["conversation_id"], revision=first["revision"])
        assert response.status_code == 403 and response.json()["code"] == "resource_binding_revoked"
        assert {path.name: path.read_bytes() for path in storage.PROJECTS_DIR.glob("*.json")} == before
        assert artifacts.read_artifact(second["resource_id"]).name != "Wrong"
