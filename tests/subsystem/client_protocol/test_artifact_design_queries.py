"""Bound, passive design controls and script-free presentation through the API."""
# ruff: noqa: F811 -- isolated shared fixtures.
import pytest

from row_bot.designer import storage
from tests.subsystem.client_protocol.test_artifact_modes_setup import artifact_service, _create  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap
from tests.subsystem.client_platform.test_workspace_setup_integrity import _completed

pytestmark = pytest.mark.subsystem


@pytest.mark.parametrize("mode", ["deck", "document", "landing", "app_mockup", "storyboard"])
def test_closed_passive_design_queries_and_static_page(artifact_service, mode):
    with _client(artifact_service) as client:
        _, headers = bootstrap(client)
        created = _completed(_create(client, headers, mode))
        project = storage.load_project(created["resource_id"])
        project.pages[0].html = '<main><h1>Synthetic title</h1><p>Saved text</p></main>'
        project.pages[0].notes = 'Synthetic presenter notes'
        storage.save_project(project)
        before = storage.load_project(project.id).to_dict()
        base = f"/api/v1/conversations/{created['conversation_id']}/artifacts/{created['binding_id']}"
        for section in ("elements", "assets", "fonts", "presets", "interactions"):
            result = client.get(base + "/design-controls", params={"section": section}, headers=headers)
            assert result.status_code == 200, result.text
            assert result.json()["section"] == section
            assert result.json()["resource_id"] == project.id
            assert len(result.json()["items"]) <= 25
        report = client.get(base + "/design-review", headers=headers)
        assert report.status_code == 200, report.text
        assert report.json()["heuristic"] is True
        presentation = client.get(base + "/presentation", headers=headers)
        assert presentation.status_code == 200, presentation.text
        assert presentation.json()["notes"] == 'Synthetic presenter notes'
        static = client.get(base + "/preview", headers=headers,
            params={"page_id": project.pages[0].route_id, "static_page": "true"})
        assert static.status_code == 200, static.text
        assert static.json()["scripts_allowed"] is False
        assert 'Synthetic title' in static.json()["html"]
        assert storage.load_project(project.id).to_dict() == before
        assert client.get(base + "/design-controls", headers=headers, params={"limit": 51}).status_code == 422
        assert client.get(base + "/design-review", headers=headers, params={"scope": "arbitrary"}).status_code == 422
        assert client.get(base.replace(created['binding_id'], 'wrong') + "/presentation", headers=headers).status_code == 403
