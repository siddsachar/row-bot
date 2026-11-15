"""Training on Hugging Face through the Learning API (C1): the snapshot's ``hugging_face`` part, sign-in, the
model, the cap, Start with the price the person saw, Cancel, and the training card's remote facts. A fake Hub
and sign-in stand in for Hugging Face; no token ever reaches the snapshot."""

from __future__ import annotations

import json

import pytest

from row_bot import learning, threads  # noqa: F401 - bind threads to the session's folder first
from row_bot.learning import runner as runner_module
from row_bot.learning.remote import hub as hub_module
from row_bot.learning.remote import training as remote_training
from row_bot.learning.remote.training import RemoteTrainer
from row_bot.learning.store import LearningStore
from row_bot.learning.training import jobs as jobs_module
from row_bot.learning.training import recipes
from tests.fixtures.hugging_face import FakeFetcher, FakeHub, FakeSignIn
from tests.fixtures.learning import FakeDataset, save_learning_settings
from tests.subsystem.client_protocol.test_settings_snapshot_api import api
from tests.subsystem.learning.test_learning_surfaces_api import _command, _ok, _snapshot, _turn_on, no_channels

# ruff: noqa: F401, F811 -- the imported pytest fixtures are requested by name.

pytestmark = [pytest.mark.subsystem]


@pytest.fixture
def hf(api, no_channels):
    client, headers, data, _ = api
    _turn_on(client, headers)
    LearningStore(data).create()
    save_learning_settings(data, train_where="hugging_face")
    hub, sign_in = FakeHub(), FakeSignIn(signed_in=False)
    runner = runner_module.get()
    trainer = RemoteTrainer(data, jobs=runner.supervisor.jobs, hub=hub, sign_in=sign_in, fetcher=FakeFetcher(hub),
                            card_mib=lambda: 32_607, start_threads=False,
                            flavors=lambda: [hub_module.Flavor("a100-large", "1x Nvidia A100", 80, 2.5 / 60), hub_module.Flavor("l40sx1", "1x Nvidia L40S", 48, 0.03)])
    saved = runner.remote
    runner.remote = trainer
    remote_training.configure(trainer)
    yield client, headers, data, hub, sign_in, trainer
    runner.remote = saved
    remote_training.configure(None)


def _error(response, code: str, status: int = 409) -> None:
    assert response.status_code == status, response.text
    assert response.json()["code"] == code


def _plan(data, trainer):
    recipe = recipes.load("hf-27b")
    steps = recipe.steps_for(90)
    job = trainer.jobs.plan(steps_total=steps, checkpoint_every=10, recipe=recipe.spec(None), backend="hugging_face")
    folder = jobs_module.run_dir(job.id, data) / "data"
    folder.mkdir(parents=True)
    FakeDataset(examples=90).prepare(job, folder)
    return job


def test_the_snapshot_guides_someone_new_before_anything_is_set_up(hf):
    client, headers, _data, hub, _sign_in, _trainer = hf

    hugging = _snapshot(client, headers)["personal_model"]["hugging_face"]

    assert hugging["sign_in"]["state"] == "signed_out" and hugging["available"] in (True, False)
    assert [model["id"] for model in hugging["models"]] == ["qwen3.8-27b", "qwen3.5-9b"]
    assert hugging["models"][0]["recommended"] and hugging["model"] == "qwen3.8-27b"
    quote = hugging["quote"]  # a typical first run, priced before signing in
    assert quote["examples"] == 90 and quote["cost_usd"] > 0 and quote["flavor"] == "a100-large"
    assert hugging["cap_usd"] == 0 and not hugging["runs_confirmed"] and hub.calls == []


def test_sign_in_shows_the_code_and_page_and_never_the_token(hf):
    client, headers, _data, _hub, sign_in, _trainer = hf

    receipt = _ok(_command(client, headers, "hf_sign_in"))

    state = receipt["snapshot"]["personal_model"]["hugging_face"]["sign_in"]
    assert (state["state"], state["code"]) == ("signing_in", "ABCD-EFGH")
    assert state["url"].startswith("https://huggingface.co/oauth/device")
    sign_in.authorize()
    snapshot = client.get("/api/v1/learning", headers=headers).text
    assert json.loads(snapshot)["personal_model"]["hugging_face"]["sign_in"] == {
        "state": "signed_in", "user": "ada", "code": None, "url": None, "failure": None}
    assert FakeSignIn.TOKEN not in snapshot


def test_the_model_and_the_cap_are_chosen_from_what_is_offered(hf):
    client, headers, data, _hub, sign_in, _trainer = hf
    sign_in.authorize()

    chosen = _ok(_command(client, headers, "hf_choose_model", model="qwen3.5-9b"))
    _error(_command(client, headers, "hf_choose_model", model="meta/llama-70b"), "learning_training_changed")
    _error(_command(client, headers, "hf_set_cap", cap_usd=0), "invalid_command", 422)
    capped = _ok(_command(client, headers, "hf_set_cap", cap_usd=50))

    assert chosen["snapshot"]["personal_model"]["hugging_face"]["model"] == "qwen3.5-9b"
    assert capped["snapshot"]["personal_model"]["hugging_face"]["cap_usd"] == 50
    assert learning.read_settings(data).hf_monthly_cap_usd == 50


def test_start_needs_the_price_the_person_saw_and_then_uploads_and_submits(hf):
    client, headers, data, hub, sign_in, trainer = hf
    sign_in.authorize()
    job = _plan(data, trainer)
    quote = _snapshot(client, headers)["personal_model"]["hugging_face"]["quote"]
    cents = round(quote["cost_usd"] * 100)

    _error(_command(client, headers, "hf_start", job_id=job.id, cap_usd=20, quote_cost_cents=cents + 1),
           "learning_quote_changed")
    assert not hub.repos and "run_job" not in hub.calls  # nothing uploaded on a price they didn't see

    receipt = _ok(_command(client, headers, "hf_start", job_id=job.id, cap_usd=20, quote_cost_cents=cents))

    assert receipt["summary"].startswith("Uploading 90 examples to your private Hugging Face dataset")
    training = receipt["snapshot"]["training"]
    assert (training["state"], training["where"]) == ("running", "hugging_face")
    assert training["remote"]["phase"] == "running" and training["remote"]["lost"] is False
    settings = learning.read_settings(data)
    assert settings.hf_runs_confirmed and settings.hf_monthly_cap_usd == 20  # later runs: quiet hours, in the cap
    assert hub.calls.count("run_job") == 1 and FakeSignIn.TOKEN not in json.dumps(receipt)


def test_cancel_stops_billing_and_deletes_the_uploads_and_sign_out_waits_for_a_run(hf):
    client, headers, data, hub, sign_in, trainer = hf
    sign_in.authorize()
    job = _plan(data, trainer)
    cents = round(_snapshot(client, headers)["personal_model"]["hugging_face"]["quote"]["cost_usd"] * 100)
    _ok(_command(client, headers, "hf_start", job_id=job.id, cap_usd=20, quote_cost_cents=cents))

    _error(_command(client, headers, "hf_sign_out"), "learning_hugging_face_busy")
    cancelled = _ok(_command(client, headers, "hf_cancel", job_id=job.id))

    assert cancelled["summary"].startswith("Cancelled on Hugging Face. Billing has stopped")
    assert not hub.repos and list(hub.jobs.values())[0].stage == "CANCELED"
    assert _ok(_command(client, headers, "hf_sign_out"))["summary"].startswith("Signed out of Hugging Face")
    assert not sign_in.signed_in


def test_lost_contact_reads_as_checking_never_failed(hf):
    client, headers, data, hub, sign_in, trainer = hf
    sign_in.authorize()
    job = _plan(data, trainer)
    cents = round(_snapshot(client, headers)["personal_model"]["hugging_face"]["quote"]["cost_usd"] * 100)
    _ok(_command(client, headers, "hf_start", job_id=job.id, cap_usd=20, quote_cost_cents=cents))
    hub.fail["job"] = hub_module.HubError("network")
    trainer.tick()

    training = _snapshot(client, headers)["training"]

    assert training.get("remote"), training
    assert (training["state"], training["remote"]["lost"], training["failure"]) == ("unknown_remote", True, None)
    checked = _ok(_command(client, headers, "hf_check_now", job_id=job.id))
    assert checked["snapshot"]["training"]["state"] == "running"


def test_changing_where_to_train_sets_aside_a_run_planned_for_the_other_place(hf):
    from row_bot.application.settings_commands import write_learning_settings

    client, headers, data, _hub, _sign_in, trainer = hf
    write_learning_settings({"train_where": "this_computer"})
    local = trainer.jobs.plan(steps_total=12, checkpoint_every=4)

    write_learning_settings({"train_where": "hugging_face"})

    assert trainer.jobs.get(local.id).state == "stopped"  # its rows are planned again for Hugging Face
    remote = _plan(data, trainer)
    write_learning_settings({"train_where": "this_computer"})
    assert trainer.jobs.get(remote.id).state == "stopped"  # never uploaded: nothing on Hugging Face to undo


def test_the_card_is_chosen_from_the_recipe_s_and_the_quote_shows_each_with_its_price(hf):
    client, headers, data, _hub, sign_in, _trainer = hf
    sign_in.authorize()

    chosen = _ok(_command(client, headers, "hf_choose_flavor", flavor="l40sx1"))
    _error(_command(client, headers, "hf_choose_flavor", flavor="h200"), "learning_training_changed")
    _error(_command(client, headers, "hf_choose_flavor", flavor="Not A Card"), "invalid_command", 422)

    assert chosen["summary"] == "Training on Hugging Face uses 1x Nvidia L40S."
    assert learning.read_settings(data).hf_flavor == "l40sx1"
    quote = chosen["snapshot"]["personal_model"]["hugging_face"]["quote"]
    assert quote["flavor"] == "l40sx1" and quote["price_per_hour"] == 1.8
    assert [item["flavor"] for item in quote["choices"]] == ["a100-large", "l40sx1"]
