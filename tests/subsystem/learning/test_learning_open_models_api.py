"""Any open model through the Learning API (C1 Part 4): Hugging Face's search, a check started from the picker
(the licence agreed where it has terms; gated, unsupported or too big for this card never checked), Stop and
Forget, and the checked models in the snapshot. A fake Hugging Face and fake check steps; no network, no card."""

from __future__ import annotations

import pytest

from row_bot import threads  # noqa: F401 - bind threads to the session's folder first
from row_bot.learning.store import LearningStore
from row_bot.learning.training import open_models
from tests.fixtures.open_models import REV, FakeSteps
from tests.fixtures.open_models import FakeHubPages as FakeHub
from tests.fixtures.open_models import card as _card
from tests.fixtures.open_models import hub as _hub
from tests.subsystem.client_protocol.test_settings_snapshot_api import api
from tests.subsystem.learning.test_learning_surfaces_api import _command, _ok, _turn_on, no_channels

# ruff: noqa: F401, F811 -- the imported pytest fixtures are requested by name.

pytestmark = [pytest.mark.subsystem]


@pytest.fixture
def models(api, no_channels, monkeypatch, tmp_path):
    client, headers, data, _ = api
    _turn_on(client, headers)
    LearningStore(data).create()
    hub = _hub()
    hub.pages["/api/models?"] = [_card(), _card("microsoft/Phi-4-mini-instruct",
                                                config={"architectures": ["Phi3ForCausalLM"]})]
    llama = _card("unsloth/Llama-3.1-8B-Instruct", cardData={"license": "llama3.1"},
                  config={"architectures": ["LlamaForCausalLM"]}, safetensors={"total": 8_030_261_248})
    hub.add(llama).add(_card("meta-llama/Llama-3.1-8B-Instruct", gated="manual", cardData={"license": "llama3.1"}))
    monkeypatch.setattr(open_models, "safe_reader", hub)
    monkeypatch.setattr(open_models.search, "__kwdefaults__", {"reader": hub})
    monkeypatch.setattr(open_models.inspect, "__kwdefaults__", {"reader": hub})
    from row_bot.application import learning_open_models

    monkeypatch.setattr(learning_open_models, "_card_mib", lambda: 32_607)  # a 32 GB card
    monkeypatch.setattr(learning_open_models, "_engine_installed", lambda: True)  # the training engine is here
    checker = open_models.Checker(data, steps=FakeSteps(tmp_path), start_threads=False)
    open_models.reset_checker(checker)
    yield client, headers, data, hub, checker
    open_models.reset_checker(None)


def test_search_says_what_each_model_is_and_whether_row_bot_can_try_it(models):
    client, headers, _data, hub, _checker = models

    found = client.get("/api/v1/learning/models", params={"query": "qwen3"}, headers=headers)

    assert found.status_code == 200, found.text
    body = found.json()
    assert body["query"] == "qwen3" and body["failure"] is None
    qwen, phi = body["models"]
    assert (qwen["id"], qwen["licence_kind"], qwen["unsupported"], qwen["runs_here"]) == (
        "hf:Qwen/Qwen3-14B", "permissive", None, True)
    assert phi["unsupported"].endswith("in a way Row-Bot’s training doesn’t adapt yet.")


def test_search_while_hugging_face_cant_be_reached_says_so(models, monkeypatch):
    client, headers, *_rest = models
    monkeypatch.setattr(open_models.search, "__kwdefaults__", {"reader": FakeHub({
        "/api/models?": open_models.HubError("unreachable")})})

    body = client.get("/api/v1/learning/models", params={"query": "qwen"}, headers=headers).json()

    assert (body["models"], body["failure"]) == ([], "unreachable")


def test_a_check_runs_from_the_picker_and_the_snapshot_shows_what_was_checked(models):
    client, headers, data, _hub, checker = models

    started = _ok(_command(client, headers, "check_model", model="hf:Qwen/Qwen3-14B"))
    checker.run("Qwen/Qwen3-14B")  # the check's thread, here in the test
    snapshot = client.get("/api/v1/learning", headers=headers).json()

    assert started["summary"].startswith("Checking Qwen3-14B on this computer. It downloads first (")
    checked = snapshot["personal_model"]["other_models"][0]
    assert (checked["id"], checked["state"], checked["licence_agreed"]) == ("hf:Qwen/Qwen3-14B", "passed", True)
    assert [item["state"] for item in checked["items"]] == ["passed"] * 4
    assert [item["passed"] for item in checked["sample"]] == [True, True, False]
    assert checked["trains_here"] is True


def test_a_licence_with_terms_is_agreed_in_the_same_ask_and_gated_or_unsupported_models_are_never_checked(models):
    client, headers, _data, _hub, checker = models

    needs = _command(client, headers, "check_model", model="hf:unsloth/Llama-3.1-8B-Instruct")
    assert needs.status_code == 409 and needs.json()["code"] == "learning_licence_needed"
    assert checker.running is None
    agreed = _ok(_command(client, headers, "check_model", model="hf:unsloth/Llama-3.1-8B-Instruct", licence_ok=True))
    assert agreed["summary"].startswith("Checking Llama-3.1-8B-Instruct")
    checker.run("unsloth/Llama-3.1-8B-Instruct")

    for model in ("hf:meta-llama/Llama-3.1-8B-Instruct", "hf:microsoft/Phi-4-mini-instruct"):
        refused = _command(client, headers, "check_model", model=model)
        assert refused.status_code == 409 and refused.json()["code"] == "learning_model_unavailable"


def test_a_check_needs_the_training_engine_first_and_downloads_nothing_without_it(models, monkeypatch):
    from row_bot.application import learning_open_models

    client, headers, _data, hub, checker = models
    monkeypatch.setattr(learning_open_models, "_engine_installed", lambda: False)

    refused = _command(client, headers, "check_model", model="hf:Qwen/Qwen3-14B")

    assert refused.status_code == 409 and refused.json()["code"] == "learning_engine_needed"
    assert checker.running is None and open_models.records(_data) == []
    assert not any("/api/models/" in url for url in hub.asked)  # nothing read or downloaded for it


def test_stop_and_forget_follow_the_check(models):
    client, headers, _data, _hub, checker = models

    _ok(_command(client, headers, "check_model", model="hf:Qwen/Qwen3-14B"))
    busy = _command(client, headers, "check_model", model="hf:unsloth/Llama-3.1-8B-Instruct", licence_ok=True)
    assert busy.status_code == 409 and busy.json()["code"] == "learning_model_check_busy"
    assert _ok(_command(client, headers, "stop_model_check", model="hf:Qwen/Qwen3-14B"))["summary"].startswith(
        "Stopping the check.")
    assert checker.run("Qwen/Qwen3-14B").state == "stopped"

    _ok(_command(client, headers, "forget_model", model="hf:Qwen/Qwen3-14B"))
    snapshot = client.get("/api/v1/learning", headers=headers).json()
    assert snapshot["personal_model"]["other_models"] == []


def test_a_model_that_passed_its_check_becomes_the_starting_model_for_local_training(models, monkeypatch):
    from row_bot import learning
    from row_bot.learning.training import hardware, weights

    client, headers, data, _hub, checker = models
    card = hardware.HardwareCheck(state="gpu", gpu=hardware.Gpu(name="NVIDIA GeForce RTX 5090", memory_mib=32_607,
                                                                    driver="580.00"))
    monkeypatch.setattr(hardware, "peek", lambda: card)
    refused = _command(client, headers, "change_starting_model", model="hf:Qwen/Qwen3-14B")
    assert refused.status_code == 409 and refused.json()["code"] == "learning_training_changed"  # not checked yet

    _ok(_command(client, headers, "check_model", model="hf:Qwen/Qwen3-14B"))
    checker.run("Qwen/Qwen3-14B")
    switched = _ok(_command(client, headers, "change_starting_model", model="hf:Qwen/Qwen3-14B"))

    assert switched["summary"].startswith("Switched to Qwen3-14B.")
    assert learning.read_settings(data).starting_model == "hf:Qwen/Qwen3-14B"
    starting = switched["snapshot"]["personal_model"]["starting_model"]
    assert (starting["id"], starting["name"], starting["licence"]) == ("hf:Qwen/Qwen3-14B", "Qwen3-14B", "Apache 2.0")
    pinned = weights.model_for("hf:Qwen/Qwen3-14B", data)
    assert (pinned.repo, pinned.revision) == ("Qwen/Qwen3-14B", REV) and len(pinned.files) == 2
    assert weights.model_for("hf:Some/Unchecked", data, weights.QWEN35_9B) is weights.QWEN35_9B


def test_a_passed_model_trains_on_hugging_face_and_stays_while_training_uses_it(models):
    from row_bot.learning import runner as runner_module
    from row_bot.learning.remote import hub as hub_module
    from row_bot.learning.remote import training as remote_training
    from row_bot.learning.remote.training import RemoteTrainer
    from tests.fixtures.hugging_face import FakeFetcher, FakeSignIn
    from tests.fixtures.hugging_face import FakeHub as FakeHfHub
    from tests.fixtures.learning import save_learning_settings

    client, headers, data, _hub, checker = models
    save_learning_settings(data, train_where="hugging_face")
    runner = runner_module.get()
    trainer = RemoteTrainer(data, jobs=runner.supervisor.jobs, hub=FakeHfHub(), sign_in=FakeSignIn(),
                            fetcher=FakeFetcher(FakeHfHub()), card_mib=lambda: 32_607, start_threads=False,
                            flavors=lambda: [hub_module.Flavor("l40sx1", "1x Nvidia L40S", 48, 0.03)])
    saved, runner.remote = runner.remote, trainer
    remote_training.configure(trainer)
    try:
        _ok(_command(client, headers, "check_model", model="hf:Qwen/Qwen3-14B"))
        checker.run("Qwen/Qwen3-14B")
        hugging = client.get("/api/v1/learning", headers=headers).json()["personal_model"]["hugging_face"]
        offered = {model["id"]: model for model in hugging["models"]}
        assert offered["hf:Qwen/Qwen3-14B"]["certified"] is False and not offered["hf:Qwen/Qwen3-14B"]["recommended"]
        assert offered["qwen3.8-27b"]["recommended"] and hugging["model"] == "qwen3.8-27b"

        chosen = _ok(_command(client, headers, "hf_choose_model", model="hf:Qwen/Qwen3-14B"))

        assert chosen["summary"] == ("Qwen3-14B will train on Hugging Face. It isn’t certified: Row-Bot checked it "
                                     "on this computer, and its time and price are estimates.")
        hugging = chosen["snapshot"]["personal_model"]["hugging_face"]
        assert hugging["model"] == "hf:Qwen/Qwen3-14B" and hugging["quote"]["measured"] is False
        for action in ("forget_model", "check_model"):  # training uses it: it stays as checked
            refused = _command(client, headers, action, model="hf:Qwen/Qwen3-14B")
            assert refused.status_code == 409 and refused.json()["code"] == "learning_model_in_use"
        _ok(_command(client, headers, "hf_choose_model", model="qwen3.5-9b"))
        _ok(_command(client, headers, "forget_model", model="hf:Qwen/Qwen3-14B"))
    finally:
        runner.remote = saved
        remote_training.configure(None)
