"""The personal model's training through the API (L2a): the snapshot's training run and the
runner's status, the polled training read, Train now, Stop, Continue, Discard and recommended
settings, the recovery prompt after a restart, Delete learning data stopping training, Buddy's
quiet line, and the preview gate. The training engine is the fake trainer; nothing reaches a
GPU or a download."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from row_bot import learning, threads  # noqa: F401 - bind threads to the session's folder first
from row_bot.api.v1.schemas import LearningReceipt, LearningSnapshot, LearningTraining
from row_bot.learning import runner as runner_module
from row_bot.learning.store import LearningStore
from row_bot.learning.training import jobs as training_jobs
from row_bot.learning.training import protocol
from tests.fixtures.learning import FakeDataset, FakeEngine, FakeLease, set_learning_preview
from tests.subsystem.client_protocol.test_settings_snapshot_api import api

# ruff: noqa: F401, F811 -- the imported pytest fixture is requested by name.

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

LEARNING = "/api/v1/learning"
SETUP = {
    "sources": {"my_chats": True, "workflows_and_goals": True, "past_chats": False},
    "apply_mode": "ask_first",
    "monthly_token_limit": 2_000_000,
    "train_in_quiet_hours": True,
    "train_where": "this_computer",
}


def _snapshot(client, headers) -> dict:
    response = client.get(LEARNING, headers=headers)
    assert response.status_code == 200, response.text
    LearningSnapshot.model_validate_json(response.text)
    return response.json()


def _training(client, headers) -> dict:
    response = client.get(LEARNING + "/training", headers=headers)
    assert response.status_code == 200, response.text
    LearningTraining.model_validate_json(response.text)
    return response.json()


def _command(client, headers, action: str, **fields):
    identity = str(uuid4())
    revision = _snapshot(client, headers)["revision"]
    return client.post(
        LEARNING + "/commands",
        headers={**headers, "Idempotency-Key": identity},
        json={"command_id": identity, "client_session_id": headers["X-Client-Session"], "revision": revision,
              "action": action, **fields},
    )


def _ok(response) -> dict:
    assert response.status_code == 200, response.text
    LearningReceipt.model_validate_json(response.text)
    receipt = response.json()
    assert receipt["status"] == "completed", receipt
    return receipt


def _refused(response, code: str) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["code"] == code


@pytest.fixture
def on(api):
    client, headers, data, _ = api
    _ok(_command(client, headers, "turn_on", setup=SETUP))
    return client, headers, data


@pytest.fixture
def engine(on):
    found = FakeEngine()
    runner_module.configure(engine=found, dataset=FakeDataset(), lease=FakeLease(since_activity=3600.0))
    return found


def _supervisor():
    return runner_module.get().supervisor


def test_with_no_training_engine_the_snapshot_says_nothing_can_train_yet(on):
    client, headers, _ = on
    training = _snapshot(client, headers)["training"]
    assert training["state"] == "none" and training["can_train_now"] is False
    assert training["runner"] == {"state": "nothing_to_do", "reason": "no_engine"}
    assert _training(client, headers) == training


def test_train_now_stop_continue_and_discard(on, engine):
    client, headers, data = on
    job = _supervisor().jobs.plan(steps_total=1000, checkpoint_every=10)
    planned = _training(client, headers)
    assert planned["state"] == "planned" and planned["can_train_now"] is True and planned["version"] == 1
    assert planned["runner"]["state"] == "scheduled"

    receipt = _ok(_command(client, headers, "train_now", job_id=job.id))
    assert receipt["summary"] == "Training started."
    assert receipt["snapshot"]["training"]["state"] in ("preparing", "running")
    _supervisor().wait_for(lambda job: job.state == "running")
    _supervisor().live_worker().advance(34)
    _supervisor().wait_for(lambda job: job.steps_done == 34)
    running = _training(client, headers)
    assert (running["state"], running["step"], running["steps_total"], running["checkpoint_step"]) == (
        "running", 34, 1000, 30)
    assert running["seconds_left"] == int(966 * 3.6)  # "about 58 min left"
    assert running["can_train_now"] is False and running["runner"]["state"] == "training"

    stopped = _ok(_command(client, headers, "stop_training", job_id=job.id))
    assert stopped["summary"] == "Training stopped. It can continue from step 30."
    training = stopped["snapshot"]["training"]
    assert (training["state"], training["pause_reason"], training["resumes"]) == ("paused", "you", "ask")
    assert training["can_train_now"] is True

    again = _ok(_command(client, headers, "continue_training", job_id=job.id))
    assert again["summary"] == "Training continues from step 30."
    _supervisor().wait_for(lambda job: job.state == "running")
    _ok(_command(client, headers, "stop_training", job_id=job.id))
    discarded = _ok(_command(client, headers, "discard_training", job_id=job.id))
    assert discarded["summary"] == "Training discarded."
    assert discarded["snapshot"]["training"]["state"] == "none"
    assert not training_jobs.run_dir(job.id, data).exists()


def test_training_actions_refuse_a_run_that_changed_or_nothing_to_train(on, engine):
    client, headers, _ = on
    _refused(_command(client, headers, "train_now"), "learning_nothing_to_train")
    job = _supervisor().jobs.plan(steps_total=10, checkpoint_every=5)
    _refused(_command(client, headers, "stop_training", job_id="job_someone_else"), "learning_training_changed")
    _refused(_command(client, headers, "use_recommended", job_id=job.id), "learning_training_changed")
    missing = _command(client, headers, "stop_training")
    assert missing.status_code == 422 and missing.json()["code"] == "invalid_learning_command"
    _ok(_command(client, headers, "pause"))
    _refused(_command(client, headers, "train_now", job_id=job.id), "learning_paused")
    assert engine.commands == []


def test_out_of_gpu_memory_shows_its_reason_and_recommended_settings_plan_it_again(on, engine):
    client, headers, _ = on
    engine.options["oom_at_step"] = 3
    job = _supervisor().jobs.plan(steps_total=10, checkpoint_every=5)
    _ok(_command(client, headers, "train_now", job_id=job.id))
    _supervisor().wait_for(lambda job: job.state == "running")
    _supervisor().live_worker().advance(5)
    _supervisor().wait_for(lambda job: job.state == "failed")
    failed = _training(client, headers)
    assert (failed["state"], failed["failure"]) == ("failed", "out_of_gpu_memory")
    assert failed["runner"] == {"state": "failed", "reason": "out_of_gpu_memory"}
    receipt = _ok(_command(client, headers, "use_recommended", job_id=job.id))
    assert receipt["summary"] == "Training will use the recommended settings next time."
    assert receipt["snapshot"]["training"]["state"] == "planned"
    assert receipt["snapshot"]["training"]["job_id"] != job.id


def test_a_run_left_running_when_row_bot_closed_asks_to_continue_from_its_checkpoint(on, engine):
    client, headers, data = on
    jobs = _supervisor().jobs
    job = jobs.plan(steps_total=1000, checkpoint_every=10)
    # Row-Bot crashed at step 605: the record still says running, with checkpoints at 590 and 600.
    jobs.move(job.id, "preparing", reason="quiet_hours")
    jobs.move(job.id, "running", reason="started")
    jobs.update(job.id, steps_done=605, checkpoint_step=600)
    run = training_jobs.run_dir(job.id, data)
    (run / "data").mkdir(parents=True)
    (run / "data" / "examples.jsonl").write_text('{"messages": []}\n', encoding="utf-8")
    (run / "lease.json").write_text('{"examples": 1}', encoding="utf-8")
    for step in (590, 600):
        folder = run / "checkpoints" / f"checkpoint-{step}"
        folder.mkdir(parents=True)
        (folder / "state.json").write_text(json.dumps({"step": step}), encoding="utf-8")
        protocol.write_checkpoint_manifest(folder, step)
    assert LearningStore(data).get("job", job.id).state == "running"
    shown = _training(client, headers)
    assert (shown["state"], shown["pause_reason"], shown["resumes"]) == ("paused", "app_closed", "ask")

    runner_module.recover()  # the next start
    recovered = _training(client, headers)
    assert (recovered["state"], recovered["pause_reason"], recovered["checkpoint_step"]) == (
        "paused", "app_closed", 600)
    receipt = _ok(_command(client, headers, "continue_training", job_id=job.id))
    assert receipt["summary"] == "Training continues from step 600."


def test_delete_learning_data_stops_training_and_removes_its_runs(on, engine):
    client, headers, data = on
    job = _supervisor().jobs.plan(steps_total=10, checkpoint_every=5)
    _ok(_command(client, headers, "train_now", job_id=job.id))
    _supervisor().wait_for(lambda job: job.state == "running")
    _supervisor().live_worker().advance(6)
    _supervisor().wait_for(lambda job: job.steps_done == 6)
    receipt = _ok(_command(client, headers, "delete_data"))
    assert receipt["summary"].startswith("Learning data deleted")
    assert _supervisor().training() is False
    assert not training_jobs.runs_dir(data).exists() or not any(training_jobs.runs_dir(data).iterdir())
    assert receipt["snapshot"]["training"]["state"] == "none"


def test_buddy_shows_a_quiet_line_while_a_personal_model_trains(on, engine, monkeypatch):
    import row_bot.agent_orchestrator as orchestrator
    import row_bot.buddy.brain as brain_mod
    import row_bot.tasks as tasks
    from row_bot.buddy.brain import BuddyBrain

    monkeypatch.setattr(tasks, "get_pending_approvals", lambda: [])
    monkeypatch.setattr(tasks, "get_running_tasks", lambda: {})
    monkeypatch.setattr(orchestrator, "get_thread_orchestration_activity", lambda: {})
    monkeypatch.setattr(brain_mod, "get_buddy_config", lambda: {"enabled": True, "mode": "sidebar", "pack_id": "glyph"})
    now = [1000.0]
    monkeypatch.setattr(brain_mod.time, "time", lambda: now[0])
    brain = BuddyBrain()
    client, headers, _ = on
    job = _supervisor().jobs.plan(steps_total=10, checkpoint_every=5)
    _ok(_command(client, headers, "train_now", job_id=job.id))
    _supervisor().wait_for(lambda job: job.state == "running")
    now[0] = 1003.0
    assert brain.resolve(None).message == "Learning: training your personal model"
    _ok(_command(client, headers, "stop_training", job_id=job.id))
    now[0] = 1010.0
    assert brain.resolve(None).message == "Idle"


def test_without_the_preview_flag_the_training_read_and_actions_are_unavailable(api, monkeypatch):
    client, headers, _, _ = api
    set_learning_preview(monkeypatch, False)
    response = client.get(LEARNING + "/training", headers=headers)
    assert response.status_code == 404 and response.json()["code"] == "learning_unavailable"
    identity = str(uuid4())
    refused = client.post(LEARNING + "/commands", headers={**headers, "Idempotency-Key": identity},
                          json={"command_id": identity, "client_session_id": headers["X-Client-Session"],
                                "revision": "0" * 64, "action": "train_now"})
    assert refused.status_code == 404 and refused.json()["code"] == "learning_unavailable"
    assert runner_module.loaded() is None or runner_module.training_now() is False
