from __future__ import annotations

from dataclasses import asdict

import pytest

from row_bot.application import artifact_lifecycle_commands as lifecycle
from row_bot.designer import storage
from row_bot.designer.client_service import ArtifactError
from row_bot.designer.state import DESIGNER_MODES
from tests.subsystem.designer.test_client_exports import (
    isolated as _isolated,
    project as _project,
)

isolated, project = _isolated, _project
pytestmark = pytest.mark.subsystem


@pytest.fixture(autouse=True)
def readiness(monkeypatch):
    monkeypatch.setattr(lifecycle, "find_spec", lambda _module: object())
    monkeypatch.setattr(lifecycle, "_remote_access_available", lambda: True)
    monkeypatch.setattr(lifecycle, "_running_channel_available", lambda: True)
    monkeypatch.setattr(lifecycle, "_x_post_available", lambda: True)


@pytest.mark.parametrize("mode", DESIGNER_MODES)
def test_lifecycle_is_a_passive_bounded_view_for_every_designer_mode(
    project, monkeypatch, mode
):
    project.mode = mode
    storage.save_project(project)
    monkeypatch.setattr(
        storage, "save_project", lambda *_args: pytest.fail("passive read saved")
    )

    state = lifecycle.read_artifact_lifecycle(project.id)

    assert state.resource_id == project.id
    assert state.resource_revision == project.updated_at
    assert state.mode == mode
    assert state.page_count == 2
    assert len(state.capabilities) == 10
    assert {item.id for item in state.capabilities} == {
        "presentation",
        "thumbnails",
        "export.html",
        "export.pdf",
        "export.png",
        "export.pptx",
        "publish.local",
        "publish.remote",
        "share.channel",
        "share.x",
    }
    assert all(
        item.state in {"ready", "check_on_use", "unavailable"}
        for item in state.capabilities
    )
    assert all(len(item.detail) <= 160 for item in state.capabilities)
    assert not any(
        "\\" in item.detail or ":/" in item.detail for item in state.capabilities
    )


def test_lifecycle_truthfully_reports_missing_local_dependencies_and_destinations(
    project, monkeypatch
):
    monkeypatch.setattr(lifecycle, "_dependency", lambda _module: False)
    monkeypatch.setattr(lifecycle, "_remote_access_available", lambda: False)
    monkeypatch.setattr(lifecycle, "_running_channel_available", lambda: False)
    monkeypatch.setattr(lifecycle, "_x_post_available", lambda: False)

    state = lifecycle.read_artifact_lifecycle(project.id)
    values = {item.id: item for item in state.capabilities}

    assert values["export.html"].state == "ready"
    assert values["export.pdf"].state == "unavailable"
    assert values["export.png"].state == "unavailable"
    assert values["export.pptx"].state == "unavailable"
    assert values["publish.local"].state == "ready"
    assert values["publish.remote"].state == "unavailable"
    assert values["share.channel"].state == "unavailable"
    assert values["share.x"].state == "unavailable"
    assert all(
        values[name].review_required
        for name in (
            "publish.local",
            "publish.remote",
            "share.channel",
            "share.x",
        )
    )
    assert not any(
        values[name].review_required
        for name in (
            "presentation",
            "thumbnails",
            "export.html",
            "export.pdf",
            "export.png",
            "export.pptx",
        )
    )


def test_lifecycle_does_not_claim_renderer_success_before_an_export(project):
    values = {
        item.id: item
        for item in lifecycle.read_artifact_lifecycle(project.id).capabilities
    }
    assert values["export.pdf"].state == "check_on_use"
    assert values["export.png"].state == "check_on_use"
    assert values["export.pptx"].state == "check_on_use"
    assert values["publish.remote"].state == "check_on_use"
    assert all(
        "checked" in values[name].detail.lower()
        for name in (
            "export.pdf",
            "export.png",
            "publish.remote",
        )
    )


def test_lifecycle_refuses_unsupported_or_oversized_saved_artifacts(project):
    project.mode = "future-mode"
    storage.save_project(project)
    with pytest.raises(ArtifactError, match="artifact_type_unavailable"):
        lifecycle.read_artifact_lifecycle(project.id)

    project.mode = "deck"
    project.pages = project.pages * 101
    storage.save_project(project)
    with pytest.raises(ArtifactError, match="resource_state_invalid"):
        lifecycle.read_artifact_lifecycle(project.id)


def test_lifecycle_wire_shape_contains_no_paths_or_credentials(project):
    value = asdict(lifecycle.read_artifact_lifecycle(project.id))
    text = repr(value).lower()
    assert "api_key" not in text
    assert "token" not in text
    assert "password" not in text
    assert str(storage.DESIGNER_DIR).lower() not in text


def test_lifecycle_rejects_stale_admission_before_probing_capabilities(
    project, monkeypatch
):
    monkeypatch.setattr(
        lifecycle,
        "_dependency",
        lambda *_args: pytest.fail("stale resource probed capabilities"),
    )
    with pytest.raises(ArtifactError, match="resource_revision_conflict") as raised:
        lifecycle.read_artifact_lifecycle(project.id, expected_revision="older")
    assert raised.value.current_revision == project.updated_at


def test_lifecycle_rejects_a_save_during_passive_read(project, monkeypatch):
    def save_during_probe(_module):
        current = lifecycle.read_artifact(project.id)
        current.name = "Saved during readiness read"
        storage.save_project(current)
        return True

    monkeypatch.setattr(lifecycle, "_dependency", save_during_probe)
    with pytest.raises(ArtifactError, match="resource_revision_conflict") as raised:
        lifecycle.read_artifact_lifecycle(
            project.id,
            expected_revision=project.updated_at,
        )
    assert raised.value.current_revision != project.updated_at


def test_dependency_probe_fails_closed(monkeypatch):
    monkeypatch.setattr(
        lifecycle,
        "find_spec",
        lambda _module: (_ for _ in ()).throw(ModuleNotFoundError()),
    )
    assert lifecycle._dependency("missing.child") is False
