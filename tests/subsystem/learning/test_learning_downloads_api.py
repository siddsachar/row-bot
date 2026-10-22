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


def test_the_hardware_check_says_what_training_needs_of_the_computers_memory(world, monkeypatch):
    # O105: each tier's measured need, the computer's memory, and the tiers it can't hold.
    client, headers, found = world
    models = {"8gb": hardware.QWEN35_2B, "16gb": hardware.QWEN35_9B, "24gb": hardware.QWEN38_27B,
              "32gb": hardware.QWEN38_27B}
    table = tuple(hardware.Tier(tier.id, tier.label, tier.min_memory_mib, tier.id != "32gb", tier.summary,
                                (models[tier.id],), {"8gb": 15_360, "16gb": 30_720, "24gb": 41_984}.get(tier.id))
                  for tier in hardware.TIERS)
    monkeypatch.setattr(hardware, "TIERS", table)
    small = hardware.classify([hardware.Gpu("NVIDIA GeForce RTX 5090", 32_607, "610.60")],
                              hardware.SystemMemory(limit_mib=36_864, free_mib=20_480))
    hardware.check(refresh=True, detector=lambda: small)
    found.gpu = small

    response = client.get("/api/v1/learning/hardware", headers=headers)

    assert response.status_code == 200
    view = LearningHardware.model_validate_json(response.text).model_dump()
    assert view["state"] == "gpu" and view["tier"]["id"] == "16gb" and view["tier"]["system_memory_gb"] == 30
    assert [(tier["id"], tier["system_memory_gb"]) for tier in view["memory_short"]] == [("24gb", 41)]
    assert view["memory"]["limit_gb"] == 36 and view["memory"]["free_gb"] == 20
    assert view["memory"]["system"] in ("windows", "linux", "other")


def test_after_two_nights_without_room_on_the_card_the_view_offers_a_smaller_model(world):
    # O112: no silent forever-wait. A 16 GB card that also runs the display never has the 9B's 15.6 GB free;
    # after two quiet-hours nights the Personal model view says so and offers the 2B, which one tap (after the
    # client's ask) makes the starting model and downloads.
    import json

    from row_bot.learning import runner as runner_module
    from row_bot.learning.training import jobs as jobs_module
    from tests.fixtures.learning import save_learning_settings

    client, headers, found = world
    card = hardware.classify([hardware.Gpu("NVIDIA GeForce RTX 4080", 16_376, "596.08", 600)],
                             hardware.SystemMemory(limit_mib=131_072, free_mib=90_000))
    hardware.check(refresh=True, detector=lambda: card)
    found.gpu = card
    root = runner_module.get().supervisor.jobs.root
    save_learning_settings(root, enabled=True, starting_model="qwen3.5-9b")
    from row_bot.learning.training import recipes

    nine = next(recipe for recipe in recipes.certified_for(card)
                if recipe.starting_model == "qwen3.5-9b")
    path = jobs_module.models_dir(root) / runner_module.ROOM_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"nights": ["2026-10-21"], "free_mib": 9_216, "need_mib": nine.memory_mib,
                                "model": "qwen3.5-9b"}), encoding="utf-8")
    assert _snapshot(client, headers)["personal_model"]["room"] is None  # one night: not yet

    path.write_text(json.dumps({"nights": ["2026-10-21", "2026-10-22"], "free_mib": 9_216,
                                "need_mib": nine.memory_mib, "model": "qwen3.5-9b"}), encoding="utf-8")
    room = _snapshot(client, headers)["personal_model"]["room"]
    assert (room["nights"], room["free_gb"], room["smaller"]["id"]) == (2, 9.0, "qwen3.5-2b")
    assert room["need_gb"] == round(nine.memory_mib / 1024, 1)
    _error(_command(client, headers, "use_smaller_model", model="qwen3.8-27b"), "learning_training_changed")

    receipt = _ok(_command(client, headers, "use_smaller_model", model="qwen3.5-2b"))

    assert receipt["summary"].startswith("Switched to Qwen3.5 2B.")
    model = receipt["snapshot"]["personal_model"]
    assert model["starting_model"]["id"] == "qwen3.5-2b" and model["room"] is None
    assert runner_module.card_room(root) is None


def test_without_the_preview_flag_there_is_no_download(api, monkeypatch):
    client, headers, _data, _ = api
    set_learning_preview(monkeypatch, False)

    response = client.get("/api/v1/learning/download", headers=headers)

    assert response.status_code == 404 and response.json()["code"] == "learning_unavailable"
