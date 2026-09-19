"""Authenticated conversation action review and recovery routes."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_protocol_application import (
    _client,
    _command,
    service,
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


def test_conversation_actions_review_replay_and_local_export(service):
    with _client(service) as client:
        _, headers = bootstrap(client)
        created = _command(
            client, headers, "conversation.create", {"title": "Action owner"}
        )
        assert created.status_code == 200, created.text
        conversation = created.json()["conversation_id"]
        root = f"/api/v1/conversations/{conversation}/actions"

        snapshot = client.get(root, headers=headers)
        assert snapshot.status_code == 200, snapshot.text
        initial = snapshot.json()
        assert initial["capabilities"]["archive"] == {
            "available": False,
            "code": "conversation_archive_unavailable",
        }

        review = client.post(
            root + "/review",
            headers=headers,
            json={
                "type": "conversation.rename",
                "expected_revision": initial["revision"],
                "payload": {"title": "Reviewed action owner"},
            },
        )
        assert review.status_code == 200, review.text
        reviewed = review.json()
        command_id = str(uuid4())
        command = {
            "command_id": command_id,
            "client_session_id": headers["X-Client-Session"],
            "type": "conversation.rename",
            "expected_revision": reviewed["revision"],
            "payload": {
                "title": "Reviewed action owner",
                "checkpoint_revision": reviewed["checkpoint_revision"],
                "action_digest": reviewed["action_digest"],
                "review_id": reviewed["review_id"],
            },
        }
        command_headers = {**headers, "Idempotency-Key": command_id}
        renamed = client.post(root + "/commands", headers=command_headers, json=command)
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["conversation"] == {
            "conversation_id": conversation,
            "revision": "1",
            "title": "Reviewed action owner",
            "pinned": False,
        }
        assert client.post(
            root + "/commands", headers=command_headers, json=command
        ).json() == renamed.json()
        receipt = client.get(root + f"/commands/{command_id}", headers=headers)
        assert receipt.status_code == 200, receipt.text
        assert receipt.json() == renamed.json()

        export_review = client.post(
            root + "/review",
            headers=headers,
            json={
                "type": "conversation.export",
                "expected_revision": "1",
                "payload": {},
            },
        )
        assert export_review.status_code == 200, export_review.text
        exported_review = export_review.json()
        export_id = str(uuid4())
        exported = client.post(
            root + "/commands",
            headers={**headers, "Idempotency-Key": export_id},
            json={
                "command_id": export_id,
                "client_session_id": headers["X-Client-Session"],
                "type": "conversation.export",
                "expected_revision": exported_review["revision"],
                "payload": {
                    "export_title": exported_review["fields"]["title"],
                    "checkpoint_revision": exported_review["checkpoint_revision"],
                    "action_digest": exported_review["action_digest"],
                    "review_id": exported_review["review_id"],
                },
            },
        )
        assert exported.status_code == 200, exported.text
        descriptor = exported.json()["export"]
        assert descriptor["file_name"] == "conversation-export.md"
        assert set(descriptor) == {
            "attachment_ref",
            "file_name",
            "size_bytes",
            "checkpoint_revision",
        }
        content = client.get(
            f"/api/v1/attachments/{descriptor['attachment_ref']}", headers=headers
        )
        assert content.status_code == 200, content.text
        assert content.content.startswith(b"# Reviewed action owner\n")
