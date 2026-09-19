"""Bound artifact lifecycle readiness through the authenticated client API."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

import pytest

from tests.subsystem.client_protocol.test_artifact_modes_setup import (
    _create,
    artifact_service,
)
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_platform.test_workspace_setup_integrity import _completed

pytestmark = pytest.mark.subsystem


def test_lifecycle_read_is_bound_bounded_and_revision_admitted(artifact_service):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, "deck"))
        path = (
            f"/api/v1/conversations/{created['conversation_id']}"
            f"/artifacts/{created['binding_id']}/lifecycle"
        )
        editing = client.get(
            path.removesuffix("/lifecycle") + "/editing",
            headers=headers,
        )
        assert editing.status_code == 200, editing.text
        revision = editing.json()["resource_revision"]

        response = client.get(
            path,
            headers=headers,
            params={"expected_revision": revision},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["resource_id"] == created["resource_id"]
        assert body["resource_revision"] == revision
        assert body["mode"] == "deck"
        assert len(body["capabilities"]) == 10
        assert {item["state"] for item in body["capabilities"]} <= {
            "ready",
            "check_on_use",
            "unavailable",
        }
        assert not any(
            key in repr(body).lower() for key in ("api_key", "password", "token")
        )

        stale = client.get(
            path,
            headers=headers,
            params={"expected_revision": "stale"},
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "resource_revision_conflict"


def test_lifecycle_read_requires_current_binding(artifact_service):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, "deck"))
        response = client.get(
            f"/api/v1/conversations/{created['conversation_id']}"
            "/artifacts/missing-binding/lifecycle",
            headers=headers,
            params={"expected_revision": "revision"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "resource_binding_revoked"
