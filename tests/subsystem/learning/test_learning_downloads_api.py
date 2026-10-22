"""The personal model's download through the Learning API (L2b): the snapshot's engine and
starting-model rows, the polled download read, Download (the ask), Resume / Try again, Stop,
Cancel, Remove engine and Delete starting model. Fakes on loopback; no network, no GPU."""

from __future__ import annotations

import pytest

from row_bot.api.v1.schemas import LearningDownload, LearningHardware
from row_bot.learning.training import downloads, hardware
from tests.fixtures.learning import set_learning_preview
from tests.fixtures.learning_engine import download_world
from tests.subsystem.client_protocol.test_settings_snapshot_api import api
from tests.subsystem.learning.test_learning_surfaces_api import _command, _ok, _snapshot, _turn_on, no_channels

# ruff: noqa: F401, F811 -- the imported pytest fixtures are requested by name.

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def world(api, no_channels, tmp_path, monkeypatch):
    client, headers, data, _ = api
    _turn_on(client, headers)
    with download_world(data, tmp_path, monkeypatch) as found:
        hardware.check(refresh=True, detector=lambda: found.gpu)
        downloads._downloads = found.downloads(start_threads=False)  # noqa: SLF001 - the app's, faked
        yield client, headers, found


def _error(response, code: str, status: int = 409) -> None:
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def test_the_snapshot_shows_the_engine_and_starting_model_and_nothing_downloads_by_reading(world):
    client, headers, found = world

    model = _snapshot(client, headers)["personal_model"]
    polled = client.get("/api/v1/learning/download", headers=headers)

    assert model["engine"] == "not_downloaded" and model["starting_model_state"] == "not_downloaded"
    assert model["download_state"]["state"] == "none"
    assert polled.status_code == 200
    LearningDownload.model_validate_json(polled.text)
    assert found.index.requests == []


def test_download_from_the_ask_downloads_the_engine_and_the_starting_model(world):
    client, headers, found = world

    receipt = _ok(_command(client, headers, "answer_download", download="download"))

    assert receipt["summary"] == "Downloading the training engine and Qwen3.5 2B."
    model = receipt["snapshot"]["personal_model"]
    assert model["download"] == "waiting"
    assert model["engine"] == "installed" and model["engine_bytes"] > 0
    assert model["starting_model_state"] == "downloaded"
    assert model["starting_model_bytes"] >= found.model.download_bytes
    assert model["download_state"]["state"] == "none"


def test_remove_engine_and_delete_starting_model_say_what_they_freed(world):
    client, headers, _found = world
    _ok(_command(client, headers, "start_download"))

    removed = _ok(_command(client, headers, "remove_engine"))
    deleted = _ok(_command(client, headers, "delete_starting_model"))

    assert removed["summary"].startswith("Training engine removed. ") and removed["summary"].endswith(" freed.")
    assert deleted["summary"].startswith("Qwen3.5 2B deleted. ")
    model = deleted["snapshot"]["personal_model"]
    assert model["engine"] == "not_downloaded" and model["starting_model_state"] == "not_downloaded"
    # Removing asks nothing to download again by itself.
    assert model["download_state"]["state"] == "none"


def test_not_enough_disk_is_refused_and_shown(world):
    client, headers, found = world
    found.free = 100_000_000

    refused = _command(client, headers, "start_download")

    _error(refused, "learning_not_enough_disk")
    state = _snapshot(client, headers)["personal_model"]["download_state"]
    assert state["state"] == "not_enough_disk" and state["needs_bytes"] > state["free_bytes"] == 100_000_000


def test_stop_with_nothing_downloading_is_refused(world):
    client, headers, _found = world

    _error(_command(client, headers, "stop_download"), "learning_download_changed")


def test_remove_waits_for_a_running_download(world, monkeypatch):
    client, headers, _found = world
    _ok(_command(client, headers, "start_download"))
    monkeypatch.setattr(downloads._downloads, "running", lambda: True)  # noqa: SLF001

    _error(_command(client, headers, "remove_engine"), "learning_download_busy")


def test_cancel_forgets_the_answer_and_what_arrived(world):
    client, headers, _found = world
    _ok(_command(client, headers, "answer_download", download="download"))
    _ok(_command(client, headers, "remove_engine"))

    receipt = _ok(_command(client, headers, "cancel_download"))

    assert receipt["summary"] == "Download cancelled. What it had downloaded is deleted."
    assert receipt["snapshot"]["personal_model"]["download"] == "not_now"


def test_an_old_driver_is_reported_by_the_hardware_check(world):
    client, headers, found = world
    old = hardware.classify([hardware.Gpu("NVIDIA GeForce RTX 3080", 10240, "560.94")])
    hardware.check(refresh=True, detector=lambda: old)
    found.gpu = old

    response = client.get("/api/v1/learning/hardware", headers=headers)

    assert response.status_code == 200
    LearningHardware.model_validate_json(response.text)
    assert response.json()["state"] == "old_driver"
    _error(_command(client, headers, "start_download"), "learning_download_unavailable")


def test_without_the_preview_flag_there_is_no_download(api, monkeypatch):
    client, headers, _data, _ = api
    set_learning_preview(monkeypatch, False)

    response = client.get("/api/v1/learning/download", headers=headers)

    assert response.status_code == 404 and response.json()["code"] == "learning_unavailable"
