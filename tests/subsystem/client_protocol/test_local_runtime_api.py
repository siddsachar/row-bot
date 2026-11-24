"""Run on this computer (E1) through the client API: the state, Download, Stop, Remove, Repair.

The computer is a fake (``hardware.detector``) and every download request is recorded by a fake opener:
nothing reaches the network, and nothing is asked for before the person presses Download.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager

import pytest

from row_bot.application import client_local_runtime
from row_bot.local_runtime import builds, hardware, install, models
from row_bot.local_runtime.fetch import Fetcher
from row_bot.local_runtime.hardware import GIB, NVIDIA, Gpu, classify
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def computer(tmp_path, monkeypatch):
    """A Windows computer with a 12 GB NVIDIA card, a scratch data folder and a recording opener."""

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    state = {"hardware": classify(builds.WINDOWS_X64, 32 * GIB,
                                  [Gpu(NVIDIA, "NVIDIA GeForce RTX 4070", 12 * GIB, "581.57")]),
             "asked": []}
    monkeypatch.setattr(hardware, "detector", lambda: state["hardware"])
    hardware.forget()
    monkeypatch.setattr(client_local_runtime, "_failure", None)
    monkeypatch.setattr(client_local_runtime, "_installers", {})

    @contextmanager
    def unreachable(url, headers):
        state["asked"].append(url)
        raise OSError("offline")

    monkeypatch.setattr(install, "Fetcher", lambda: Fetcher(opener=unreachable, retry_wait=0))
    yield state
    client_local_runtime.installer().stop()
    hardware.forget()


def test_the_state_recommends_a_model_with_its_size_and_asks_for_nothing(computer):
    view = client_local_runtime.snapshot()

    assert view["state"] == "not_installed"
    assert view["recommended"] == "qwen3.8-27b-q2" and view["acceleration"] == "cuda-13"
    assert view["computer"] == "NVIDIA GeForce RTX 4070 (12 GB)"
    build = builds.select_build(builds.WINDOWS_X64, builds.CUDA_13)
    pick = next(row for row in view["models"] if row["id"] == "qwen3.8-27b-q2")
    assert pick["download_bytes"] == build.size + models.get("qwen3.8-27b-q2").size
    assert pick["model_ref"] == "model:custom_openai_llamacpp:qwen3.8-27b-q2"
    assert (pick["quantization"], pick["older"]) == ("2-bit", False)
    nine = next(row for row in view["models"] if row["id"] == "qwen3.5-9b")
    assert nine["older"] is True
    # What each reads on this 12 GB card: the 2-bit 27B its 32K, the 9B (5.7 GB) 128K with room to spare.
    assert (pick["context_tokens"], nine["context_tokens"]) == (32_768, 131_072)
    assert computer["asked"] == []
    assert not install.runtime_dir().exists() and not install.models_dir().exists()


def test_download_starts_only_with_the_size_the_person_saw(computer):
    model = models.get("qwen3.8-27b-q2")
    build = builds.select_build(builds.WINDOWS_X64, builds.CUDA_13)

    view = client_local_runtime.install_model(model.id, 123)

    assert view["failure"] == {"reason": "size_changed", "download_bytes": build.size + model.size}
    assert view["state"] == "failed" and computer["asked"] == []

    client_local_runtime.install_model(model.id, build.size + model.size)
    client_local_runtime.installer().wait(30)

    # Only the pinned release asset was asked for (the fake network is offline).
    assert computer["asked"][0] == build.assets[0].url
    view = client_local_runtime.snapshot()
    assert view["state"] == "failed" and view["failure"]["reason"] == "network"


def test_a_computer_no_build_runs_on_says_so_and_downloads_nothing(computer):
    computer["hardware"] = classify(None, 64 * GIB)
    hardware.forget()

    view = client_local_runtime.snapshot()
    assert view["state"] == "unavailable" and view["recommended"] is None
    after = client_local_runtime.install_model("qwen3.5-4b", 0)
    assert after["failure"] == {"reason": "unavailable"} and computer["asked"] == []


def test_the_api_serves_the_state_and_keeps_downloads_to_row_bots_own_computer(computer):
    client, _, _ = client_app()
    with client:
        _, headers = bootstrap(client)
        view = client.get("/api/v1/local-runtime", headers=headers)
        assert view.status_code == 200, view.text
        assert view.json()["on_this_computer"] is True and view.json()["state"] == "not_installed"
        refused = client.post("/api/v1/local-runtime/install", headers=headers,
                              json={"model_id": "qwen3.5-9b", "download_bytes": 1})
        assert refused.status_code == 200 and refused.json()["failure"]["reason"] == "size_changed"
        unknown = client.post("/api/v1/local-runtime/install", headers=headers,
                              json={"model_id": "llama-70b", "download_bytes": 1})
        assert unknown.status_code >= 400
        repaired = client.post("/api/v1/local-runtime/repair", headers=headers)
        assert repaired.status_code == 200 and repaired.json()["failure"] is None

    remote, _, _ = client_app(remote=True)
    with remote:
        _, headers = bootstrap(remote)
        phone = remote.get("/api/v1/local-runtime", headers=headers)
        assert phone.status_code == 200 and phone.json()["on_this_computer"] is False
        refused = remote.post("/api/v1/local-runtime/install", headers=headers,
                              json={"model_id": "qwen3.5-9b", "download_bytes": 1})
        assert refused.status_code == 409 and refused.json()["code"] == "desktop_only"
        assert remote.post("/api/v1/local-runtime/remove", headers=headers, json={}).status_code == 409
    assert computer["asked"] == []


def test_repair_reads_a_new_card_and_the_endpoint_and_snapshot_take_its_window(computer, tmp_path, monkeypatch):
    import row_bot.providers.config as provider_config
    from row_bot import local_runtime
    from row_bot.local_runtime import server
    from row_bot.providers import custom

    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(install, "installed_models", lambda root=None: [models.get("qwen3.8-27b-q2")])
    stopped = []
    monkeypatch.setattr(server, "stop", lambda reason="you": stopped.append(reason) or True)
    server.save_endpoint()
    assert custom.get_custom_endpoint(local_runtime.PROVIDER_ID)["models"][0]["context_window"] == 32_768

    computer["hardware"] = classify(builds.WINDOWS_X64, 32 * GIB,
                                    [Gpu(NVIDIA, "NVIDIA GeForce RTX 4090", 24 * GIB, "581.57")])
    view = client_local_runtime.repair()

    assert stopped == ["repair"]  # the running server serves the old window until it starts again
    row = next(row for row in view["models"] if row["id"] == "qwen3.8-27b-q2")
    assert row["context_tokens"] == 131_072
    assert custom.get_custom_endpoint(local_runtime.PROVIDER_ID)["models"][0]["context_window"] == 131_072


def test_remove_reports_the_space_it_freed(computer):
    model = models.get("qwen3.5-4b")
    folder = install.model_dir(model)
    folder.mkdir(parents=True)
    (folder / f"{model.file}.part").write_bytes(b"x" * 1000)

    view = client_local_runtime.remove()

    assert view["freed_bytes"] == 1000 and view["disk_bytes"] == 0
    assert not install.models_dir().exists()


def test_reading_the_state_loads_nothing_of_learning(computer):
    before = {name for name in sys.modules if name.startswith("row_bot.learning")}

    client_local_runtime.snapshot()

    assert {name for name in sys.modules if name.startswith("row_bot.learning")} == before


def test_setup_can_choose_a_downloaded_model_and_never_one_that_isnt(computer, tmp_path, monkeypatch):
    import json

    from row_bot import models as runtime_models
    from row_bot.application import client_first_run
    from row_bot.application.client_platform import ClientPlatformError

    monkeypatch.setattr(runtime_models, "_current_model", "")
    monkeypatch.setattr("row_bot.application.client_diagnosis.recheck_default_model", lambda: None)
    with pytest.raises(ClientPlatformError, match="model_configuration_unavailable"):
        client_first_run.choose_model("model:custom_openai_llamacpp:qwen3.5-9b")

    nine = models.get("qwen3.5-9b")
    monkeypatch.setattr(install, "installed_models", lambda root=None: [nine])
    monkeypatch.setattr(install, "installed_build", lambda root=None: install.InstalledBuild(
        tmp_path, builds.RELEASE, builds.CUDA_13, tmp_path / "llama-server.exe"))
    chosen = client_first_run.choose_model("model:custom_openai_llamacpp:qwen3.5-9b")

    assert chosen == "model:custom_openai_llamacpp:qwen3.5-9b"
    saved = json.loads((tmp_path / "model_settings.json").read_text(encoding="utf-8"))
    assert saved["model"] == chosen
    with pytest.raises(ClientPlatformError, match="model_configuration_unavailable"):
        client_first_run.choose_model("model:custom_openai_llamacpp:qwen3.5-27b")  # not downloaded
