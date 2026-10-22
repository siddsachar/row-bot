"""Using the personal model through the API (L2c2): the versions table and its comparison in the
snapshot, Needs you, Use v1 for <kind> (a trial: the person asked, D24), Keep, Not now, Undo,
Export…, Delete version, Advanced and Reset, Retrain without it, and Experiment details (counts
only, file names only there). Undo, Export and Delete work while Learning is off; using a new
version needs Learning on."""

from __future__ import annotations

import sys
from uuid import uuid4

import pytest

from row_bot import learning, threads  # noqa: F401 - bind threads to the session's folder first
from row_bot.api.v1.schemas import LearningReceipt, LearningSnapshot, LearningVersionDetails
from row_bot.learning import model_compare, use
from row_bot.learning import runner as runner_module
from row_bot.learning.training import releases, serving
from tests.fixtures.learning import FakeDataset, FakeEngine, FakeLease
from tests.subsystem.client_protocol.test_settings_snapshot_api import api

# ruff: noqa: F401, F811 -- the imported pytest fixture is requested by name.

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

LEARNING = "/api/v1/learning"
SETUP = {
    "sources": {"my_chats": True, "workflows_and_goals": True, "past_chats": False},
    "apply_mode": "automatic", "monthly_token_limit": 2_000_000, "train_in_quiet_hours": True,
    "train_where": "this_computer",
}
PERSONAL = "model:ollama:row-bot-personal-v1:abcd1234"


def _snapshot(client, headers) -> dict:
    response = client.get(LEARNING, headers=headers)
    assert response.status_code == 200, response.text
    LearningSnapshot.model_validate_json(response.text)
    return response.json()


def _command(client, headers, action: str, **fields):
    identity = str(uuid4())
    revision = _snapshot(client, headers)["revision"]
    return client.post(LEARNING + "/commands", headers={**headers, "Idempotency-Key": identity},
                       json={"command_id": identity, "client_session_id": headers["X-Client-Session"],
                             "revision": revision, "action": action, **fields})


def _ok(response) -> dict:
    assert response.status_code == 200, response.text
    LearningReceipt.model_validate_json(response.text)
    receipt = response.json()
    assert receipt["status"] == "completed", receipt
    return receipt


def _refused(response, code: str) -> None:
    assert response.status_code in (409, 422), response.text
    assert response.json()["code"] == code, response.text


@pytest.fixture
def on(api, monkeypatch):
    client, headers, data, _ = api
    monkeypatch.setattr(serving.OllamaRuntime, "executable", lambda self: None)
    monkeypatch.setattr(sys, "platform", "win32")
    assert _ok(_command(client, headers, "turn_on", setup=SETUP))
    runner_module.configure(engine=FakeEngine(), dataset=FakeDataset(), lease=FakeLease(since_activity=3600.0))
    return client, headers, data


def _ready_version(data) -> None:
    jobs = runner_module.get().supervisor.jobs
    job = jobs.plan(steps_total=1, checkpoint_every=1)
    for state in ("preparing", "running", "evaluating", "ready"):
        jobs.move(job.id, state)
    folder = releases.version_dir(1, data)
    (folder / "adapter").mkdir(parents=True, exist_ok=True)
    (folder / "adapter" / "adapter_model.safetensors").write_bytes(b"a" * 1000)
    (folder / "adapter" / "adapter_config.json").write_text("{}", encoding="utf-8")
    (folder / "row-bot-personal-v1-Q8_0.gguf").write_bytes(b"g" * 5000)
    (folder / releases.MODEL_CARD_FILE).write_text("# Personal model v1", encoding="utf-8")
    (folder / releases.DATASET_FILE).write_text(
        '{"examples": 154, "validation": 15, "conversations": ["c1", "c2"], "providers": ["openai"],'
        ' "left_out": {"held_out": 11}, "terms_note": true}', encoding="utf-8")
    releases.write_state(folder, releases.VersionState(
        version=1, phase=releases.READY, job_id=job.id, model_ref=PERSONAL, gguf="row-bot-personal-v1-Q8_0.gguf",
        gguf_bytes=5000, adapter_bytes=1000, examples=154))


def _compared(data, recommend="document_lookups") -> None:
    model_compare._write_json(model_compare.comparison_path(1, data), {  # noqa: SLF001
        "format": model_compare.FORMAT, "schema_version": 1, "version": 1, "release_id": None,
        "compared_at": 1_800_000_000.0, "seconds": 60.0, "reviewed_at": None,
        "arms": [{"key": "base", "label": "the starting model", "role": "baseline", "ran": 30, "cached": 0,
                  "seconds_per_task": 4.2, "cloud_tokens_per_task": 0},
                 {"key": "v1", "label": "v1 (new)", "role": "new", "ran": 30, "cached": 0,
                  "seconds_per_task": 3.1, "cloud_tokens_per_task": 0},
                 {"key": "main", "label": "GPT-6.1 Sol", "role": "main", "ran": 30, "cached": 30,
                  "seconds_per_task": 9.0, "cloud_tokens_per_task": 41000}],
        "rows": [{"kind": "document_lookups", "label": "Document lookups", "total": 24, "verdict": "better",
                  "passed": {"base": 15, "v1": 21, "main": 22}, "counted": {"base": 24, "v1": 24, "main": 24}}],
        "overall": {"verdict": "better", "recommend": recommend, "line": "Better at document lookups."},
        "note": "Testing used 0 tokens (and 1.2M on this computer).", "stopped": "", "left_out": [
            model_compare.CODING_NOTE], "baseline_unavailable": None, "main_unavailable": None,
        "details": {"visible": {"v1": {"Document lookups": [5, 6]}}, "hidden": {"v1": {"Document lookups": [16, 18]}}},
        "runs": {}, "budget": {"task_tokens": 150000},
    })


def test_the_snapshot_shows_the_versions_their_comparison_and_needs_you(on):
    client, headers, data = on
    _ready_version(data)

    waiting = _snapshot(client, headers)
    assert waiting["personal_model"]["versions"][0] == {"version": 1, "ready": True, "file_bytes": 5000,
                                                        "trained_at": None, "compared": False, "uses": []}
    assert waiting["personal_model"]["comparison"]["state"] == "waiting" and waiting["needs_you"] == []

    _compared(data)
    shown = _snapshot(client, headers)
    comparison = shown["personal_model"]["comparison"]
    assert comparison["state"] == "done" and comparison["recommend"] == "document_lookups"
    assert sorted(comparison["rows"][0]["cells"], key=lambda cell: cell["key"]) == [
        {"key": "base", "passed": 15, "counted": 24}, {"key": "main", "passed": 22, "counted": 24},
        {"key": "v1", "passed": 21, "counted": 24}]
    assert comparison["coding_left_out"] is True and comparison["arms"][2]["cached"] == 30
    assert [item["title"] for item in shown["needs_you"]] == ["Personal model v1 is ready to compare"]
    assert "row-bot-personal-v1" not in str(shown["personal_model"]["comparison"])  # no file names (O54)


def test_use_starts_a_trial_and_keep_and_undo_answer_at_once(on):
    client, headers, data = on
    _ready_version(data)
    _compared(data)

    used = _ok(_command(client, headers, "use_version", version=1, kind="document_lookups"))
    assert used["summary"] == ("v1 now answers document lookups, on trial. Your main model finishes anything it "
                               "gets stuck on.")
    [entry] = used["snapshot"]["personal_model"]["uses"]
    assert (entry["kind"], entry["version"], entry["state"]) == ("document_lookups", 1, "trial")
    assert used["snapshot"]["needs_you"] == []  # the comparison was reviewed by using it
    assert runner_module.get().supervisor.jobs.all()[0].state == "trial"

    kept = _ok(_command(client, headers, "keep_version", kind="document_lookups"))
    assert kept["summary"] == "v1 is in use for document lookups."
    assert kept["snapshot"]["personal_model"]["uses"][0]["state"] == "in_use"

    undone = _ok(_command(client, headers, "undo_use", kind="document_lookups"))
    assert undone["summary"] == "Document lookups go back to your main model."
    assert undone["snapshot"]["personal_model"]["uses"] == [] and use.read(data).assignments == {}


def test_a_new_version_is_never_used_without_the_persons_command_even_in_automatic_mode(on):
    client, headers, data = on
    _ready_version(data)
    _compared(data)

    runner_module.get().check()

    assert use.read(data).assignments == {}  # D24: new versions always ask
    assert _snapshot(client, headers)["needs_you"][0]["type"] == "compare"


def test_not_now_keeps_the_comparison_and_stops_asking(on):
    client, headers, data = on
    _ready_version(data)
    _compared(data)

    receipt = _ok(_command(client, headers, "review_version", version=1))

    assert receipt["snapshot"]["needs_you"] == [] and receipt["snapshot"]["personal_model"]["comparison"]["reviewed"]


def test_use_needs_a_ready_version_and_learning_on(on):
    client, headers, data = on
    _refused(_command(client, headers, "use_version", version=1, kind="document_lookups"),
             "learning_version_changed")
    _refused(_command(client, headers, "use_version", version=1, kind="other"), "invalid_command")  # never other
    _ready_version(data)
    _ok(_command(client, headers, "turn_off"))
    _refused(_command(client, headers, "use_version", version=1, kind="document_lookups"), "learning_off")


def test_undo_export_and_delete_still_work_while_learning_is_off(on, tmp_path):
    client, headers, data = on
    _ready_version(data)
    _ok(_command(client, headers, "use_version", version=1, kind="tool_workflows"))
    _ok(_command(client, headers, "turn_off"))

    assert _ok(_command(client, headers, "undo_use", kind="tool_workflows"))["summary"] == \
        "Tool workflows go back to your main model."
    exported = _ok(_command(client, headers, "export_version", version=1, folder=str(tmp_path / "out")))
    assert exported["summary"].startswith("Exported v1 (") and (tmp_path / "out" / "row-bot-personal-v1").is_dir()
    withdrawn = []
    import row_bot.learning.training.serving as serving_module

    original = serving_module.withdraw
    serving_module.withdraw = lambda runtime, version, **kw: withdrawn.append(version)
    try:
        deleted = _ok(_command(client, headers, "delete_version", version=1))
    finally:
        serving_module.withdraw = original
    assert deleted["summary"].startswith("v1 deleted.") and withdrawn == [1]
    assert not releases.version_dir(1, data).exists()


def test_export_refuses_a_folder_inside_row_bots_data(on):
    client, headers, data = on
    _ready_version(data)

    _refused(_command(client, headers, "export_version", version=1, folder=str(data / "exports")),
             "learning_export_unavailable")


def test_experiment_details_have_counts_and_file_names_never_a_hidden_task(on):
    client, headers, data = on
    _ready_version(data)
    _compared(data)

    response = client.get(LEARNING + "/versions/1", headers=headers)

    assert response.status_code == 200, response.text
    LearningVersionDetails.model_validate_json(response.text)
    details = response.json()
    assert details["data"]["examples"] == 154 and details["data"]["conversations"] == 2
    assert details["data"]["terms_note"] is True and details["data"]["left_out"] == [
        {"reason": "kept back as hidden tests", "count": 11}]
    assert {item["name"] for item in details["files"]} >= {"row-bot-personal-v1-Q8_0.gguf",
                                                           "adapter/adapter_model.safetensors"}
    assert details["runs"] == [
        {"suite": "Visible tasks", "arm": "v1 (new)", "passed": 5, "total": 6},
        {"suite": "Hidden tasks and your held-back work", "arm": "v1 (new)", "passed": 16, "total": 18}]
    assert "c1" not in response.text  # conversation ids are counted, never shown
    assert client.get(LEARNING + "/versions/9", headers=headers).status_code == 409


def test_advanced_saves_clamped_values_and_resets(on):
    client, headers, data = on
    values = {"starting_model": "qwen3.5-2b", "rank": 64, "learning_rate": 0.0001, "epochs": 2, "context": 131072,
              "method": "standard"}

    saved = _ok(_command(client, headers, "save_advanced", advanced=values))

    shown = saved["snapshot"]["personal_model"]["advanced"]
    assert saved["summary"] == "Saved. The next training run uses these settings."
    assert shown["values"]["rank"] == 64 and shown["values"]["context"] == 16384 and shown["changed"] is True
    reset = _ok(_command(client, headers, "reset_advanced"))
    assert reset["snapshot"]["personal_model"]["advanced"]["changed"] is False


def test_retrain_without_plans_a_new_version_and_dismiss_hides_a_line(on):
    client, headers, data = on
    _ready_version(data)
    planned = []

    class Planner:
        def plan(self, *, force=False, retrain=False):
            planned.append(retrain)
            from types import SimpleNamespace

            return SimpleNamespace(job=SimpleNamespace(version=2), reason=None)

    runner_module.configure(planner=Planner())
    receipt = _ok(_command(client, headers, "retrain_without", version=1))
    assert receipt["summary"] == "v2 is planned without that chat. It trains during quiet hours."
    assert planned == [True]
    assert _ok(_command(client, headers, "dismiss", item_id="retrain:v1"))["summary"] == "Dismissed."


def test_use_choices_are_part_of_the_revision(on):
    client, headers, data = on
    _ready_version(data)
    before = _snapshot(client, headers)["revision"]

    use.start_trial(1, "document_lookups", PERSONAL, root=data)

    assert _snapshot(client, headers)["revision"] != before
