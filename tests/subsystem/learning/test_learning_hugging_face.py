"""Training on Hugging Face (C1): the quote, the cap, the upload, the durable submission, lost contact, unknown
outcomes, cancel, download, cleanup and the release, against a fake Hub (no network, no GPU, no real account)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from row_bot.learning.remote import hub as hub_module
from row_bot.learning.remote import training as remote_training
from row_bot.learning.remote.hub import HubError
from row_bot.learning.remote.training import RemoteError, RemoteTrainer
from row_bot.learning.store import LearningStore
from row_bot.learning.training import jobs as jobs_module
from row_bot.learning.training import recipes, releases, serving
from tests.fixtures.hugging_face import FakeFetcher, FakeHub, FakeSignIn
from tests.fixtures.learning import FakeClock, FakeDataset, save_learning_settings

pytestmark = [pytest.mark.subsystem]

CARD_MIB = 32_607  # a 32 GB card: it runs the 27B after (about 20 GB), so the 27B is offered first


@pytest.fixture
def world(learning_on):
    LearningStore(learning_on).create()
    save_learning_settings(learning_on, train_where="hugging_face",
                           hugging_face={"model": "", "monthly_cap_usd": 20, "runs_confirmed": True})
    clock = FakeClock()
    jobs = jobs_module.TrainingJobs(learning_on, clock=clock)
    hub = FakeHub()
    sign_in = FakeSignIn()
    trainer = RemoteTrainer(learning_on, jobs=jobs, hub=hub, sign_in=sign_in, clock=clock, fetcher=FakeFetcher(hub),
                            card_mib=lambda: CARD_MIB, start_threads=False,
                            flavors=lambda: [hub_module.Flavor("a100-large", "1x Nvidia A100", 80, 2.5 / 60), hub_module.Flavor("l40sx1", "1x Nvidia L40S", 48, 0.03)])
    remote_training.configure(trainer)
    yield SimpleNamespace(root=learning_on, clock=clock, jobs=jobs, hub=hub, sign_in=sign_in, trainer=trainer)
    remote_training.configure(None)


def _planned(world, examples: int = 90):
    recipe = recipes.load("hf-27b")
    steps = recipe.steps_for(examples)
    job = world.jobs.plan(steps_total=steps, checkpoint_every=recipe.checkpoint_interval(steps),
                          recipe=recipe.spec(None), backend="hugging_face")
    data = jobs_module.run_dir(job.id, world.root) / "data"
    data.mkdir(parents=True)
    FakeDataset(examples=examples, validation=5).prepare(job, data)
    (data / "dataset.json").write_text(json.dumps({"conversations": ["thread-secret-id"]}), encoding="utf-8")
    return job


def _started(world, **kwargs):
    job = _planned(world, **kwargs)
    world.trainer.start(job, "train_now", confirmed=True)
    moved = world.jobs.get(job.id)
    assert moved.state == "running"
    return moved, remote_training.read_record(job.id, world.root)


# ── The quote and the cap ────────────────────────────────────────────────────


def test_the_quote_says_the_gpu_time_price_and_what_is_uploaded(world):
    quote = world.trainer.quote(examples=90, upload_bytes=4_000_000)

    assert quote.model_id == "qwen3.8-27b"  # the strongest model this 32 GB card can run afterwards (O131)
    assert (quote.flavor, quote.price_per_hour, quote.gpu_memory_gb) == ("a100-large", 2.5, 80)  # Hugging Face's price
    assert quote.seconds > 0 and quote.cost_usd == remote_training.cost(quote.seconds, 2.5)
    assert quote.max_cost_usd >= quote.cost_usd and quote.timeout_seconds >= quote.seconds
    assert (quote.examples, quote.upload_bytes, quote.quantization) == (90, 4_000_000, "Q4_K_M")
    assert quote.dataset_repo.startswith("ada/row-bot-training-") and quote.fits_cap


def test_hugging_face_bills_whole_minutes():
    assert remote_training.cost(1, 1.8) == 0.03 and remote_training.cost(60, 1.8) == 0.03
    assert remote_training.cost(61, 1.8) == 0.06 and remote_training.cost(0, 1.8) == 0.0


def test_a_model_this_computer_cant_run_afterwards_is_never_offered(world):
    world.trainer._card_mib = lambda: 12_288  # noqa: SLF001 - a 12 GB card: the 9B runs, the 27B doesn't
    assert [model.id for model in world.trainer.models()] == ["qwen3.5-9b"]
    world.trainer._card_mib = lambda: None  # noqa: SLF001 - no NVIDIA card: nothing runs here after
    assert world.trainer.models() == ()
    with pytest.raises(RemoteError) as refused:
        world.trainer.quote(examples=90, upload_bytes=1)
    assert refused.value.code == "no_model"


def test_the_first_run_asks_and_a_run_over_the_cap_never_starts(world):
    save_learning_settings(world.root, hugging_face={"model": "", "monthly_cap_usd": 20, "runs_confirmed": False})
    job = _planned(world)
    with pytest.raises(RemoteError) as first:
        world.trainer.start(job, "quiet_hours")
    assert first.value.code == "not_confirmed"
    remote_training.record_spend("job_earlier", cost_usd=19.5, seconds=600, flavor="l40sx1", at=world.clock(),
                                 final=True, root=world.root)
    with pytest.raises(RemoteError) as over:
        world.trainer.start(job, "train_now", confirmed=True)
    assert over.value.code == "over_cap"
    assert world.jobs.get(job.id).state == "planned" and "run_job" not in world.hub.calls and not world.hub.repos


def test_the_jobs_timeout_never_lets_a_run_pass_the_cap(world):
    remote_training.record_spend("job_earlier", cost_usd=15.0, seconds=600, flavor="l40sx1", at=world.clock(),
                                 final=True, root=world.root)
    quote = world.trainer.quote(examples=90, upload_bytes=1)

    assert quote.timeout_seconds <= int((20 - 15.0) / 2.5 * 3600)
    assert quote.max_cost_usd <= 5.0 + 0.03


# ── Starting: the upload and the durable submission ──────────────────────────


def test_a_run_uploads_only_its_rows_and_engine_files_to_a_private_dataset_then_submits(world):
    job, record = _started(world)

    dataset = world.hub.repos[record.dataset_repo]
    assert dataset["kind"] == "dataset" and dataset["private"]
    assert world.hub.repos[record.output_repo]["private"]
    assert sorted(dataset["files"]) == sorted([
        "job.json", "data/examples.jsonl", "data/validation.jsonl", "code/hf_job.py", "code/protocol.py",
        "code/entry.py", "code/trainer.py", "code/engine/pyproject.toml", "code/engine/uv.lock"])
    uploaded = b"".join(dataset["files"].values())
    assert b"thread-secret-id" not in uploaded  # the manifest naming the person's chats stays here
    plan = json.loads(dataset["files"]["job.json"])
    assert plan["model"]["repo"] == "Qwen/Qwen3.8-27B" and all(item["sha256"] for item in plan["model"]["files"])
    assert plan["output_repo"] == record.output_repo and plan["quantization"] == "Q4_K_M"
    remote = world.hub.jobs[job.remote_request_id]
    assert remote.labels == {"row-bot-job": job.id}  # found again by this if an answer is lost
    assert remote.secrets == {"HF_TOKEN": FakeSignIn.TOKEN} and FakeSignIn.TOKEN not in json.dumps(remote.env)
    assert remote.env["HF_HUB_DISABLE_TELEMETRY"] == "1" and remote.flavor == "a100-large"
    assert remote.timeout == record.timeout_seconds > 0
    assert FakeSignIn.TOKEN not in jobs_module.run_dir(job.id, world.root).joinpath("remote.json").read_text()


def test_an_upload_that_fails_ends_the_run_and_deletes_what_was_made(world):
    world.hub.fail["upload"] = HubError("network")
    job = _planned(world)

    world.trainer.start(job, "train_now", confirmed=True)

    assert (world.jobs.get(job.id).state, world.jobs.get(job.id).error_code) == ("failed", "upload_failed")
    assert "run_job" not in world.hub.calls and not world.hub.repos


def test_a_submission_without_an_answer_is_found_by_its_label_and_never_sent_again(world):
    world.hub.fail["run_job"] = HubError("network")  # the job was created; its answer was lost
    job = _planned(world)

    world.trainer.start(job, "train_now", confirmed=True)

    assert world.jobs.get(job.id).state == "unknown_remote"
    world.trainer.tick()
    found = world.jobs.get(job.id)
    assert found.state == "running" and found.remote_request_id in world.hub.jobs
    assert world.hub.calls.count("run_job") == 1  # never submitted twice


def test_a_submission_that_never_reached_hugging_face_ends_as_not_started_after_several_looks(world):
    world.hub.fail["run_job"] = HubError("network")
    job = _planned(world)
    world.trainer.start(job, "train_now", confirmed=True)
    world.hub.jobs.clear()  # it never got there

    for _ in range(remote_training.NOT_STARTED_CHECKS - 1):
        world.clock.advance(60)
        world.trainer.tick()
        assert world.jobs.get(job.id).state == "unknown_remote"  # unknown while it may exist
    world.clock.advance(remote_training.NOT_STARTED_SECONDS)
    world.trainer.tick()

    assert (world.jobs.get(job.id).state, world.jobs.get(job.id).error_code) == ("failed", "not_started")
    assert world.hub.calls.count("run_job") == 1 and not world.hub.repos  # cleaned up, not retried


# ── While it runs ─────────────────────────────────────────────────────────────


def test_progress_and_cost_follow_the_jobs_own_lines(world):
    job, record = _started(world)
    remote = world.hub.jobs[job.remote_request_id]
    remote.stage, remote.running_seconds = "RUNNING", 1_830
    world.hub.say(remote.id, "stage", stage="training", steps_total=job.steps_total)
    world.hub.say(remote.id, "progress", step=12, steps_total=job.steps_total, loss=0.4, seconds_per_step=30.0)

    world.trainer.tick()

    moved = world.jobs.get(job.id)
    record = remote_training.read_record(job.id, world.root)
    assert (moved.state, moved.steps_done, record.stage) == ("running", 12, "training")
    assert record.cost_usd == remote_training.cost(1_830, 2.5) == 1.2917  # 31 minutes at $2.50 an hour
    assert remote_training.month_spent(world.clock(), world.root) == 1.2917


def test_no_answer_is_unknown_never_failed_and_the_next_answer_settles_it(world):
    job, _record = _started(world)
    world.hub.fail["job"] = HubError("network")

    world.trainer.tick()

    assert world.jobs.get(job.id).state == "unknown_remote"
    world.hub.jobs[job.remote_request_id].stage = "RUNNING"
    world.trainer.check_now(job.id)
    assert world.jobs.get(job.id).state == "running"
    assert world.hub.calls.count("run_job") == 1


def test_an_error_on_hugging_face_fails_with_its_reason_and_deletes_the_uploads(world):
    job, record = _started(world)
    remote = world.hub.jobs[job.remote_request_id]
    world.hub.say(remote.id, "error", reason="out_of_gpu_memory", stage="training")
    remote.stage, remote.running_seconds = "ERROR", 300

    world.trainer.tick()

    assert (world.jobs.get(job.id).state, world.jobs.get(job.id).error_code) == ("failed", "out_of_gpu_memory")
    assert record.dataset_repo not in world.hub.repos and record.output_repo not in world.hub.repos
    assert remote_training.run_cost(job.id, world.root)["cost_usd"] == 0.2083  # billed for the 5 minutes it ran


def test_cancel_cancels_on_hugging_face_first_and_deletes_the_uploads(world):
    job, record = _started(world)

    world.trainer.cancel(job.id)

    assert world.hub.jobs[job.remote_request_id].stage == "CANCELED"
    assert world.jobs.get(job.id).state == "stopped" and not world.hub.repos
    assert remote_training.read_record(job.id, world.root).cleaned_at is not None


# ── After: download, the version, cleanup ─────────────────────────────────────


def test_a_finished_run_downloads_its_checked_adapters_then_evaluates(world):
    job, record = _started(world)
    world.hub.finish(job.remote_request_id, output_repo=record.output_repo)

    world.trainer.tick()

    run = jobs_module.run_dir(job.id, world.root)
    assert world.jobs.get(job.id).state == "evaluating"
    assert (run / "checkpoints" / "pass-3" / "adapter_model.safetensors").read_bytes() == b"adapter weights pass 3"
    assert len(json.loads((run / jobs_module.PASSES_FILE).read_text())["files"]) == 3
    assert remote_training.run_cost(job.id, world.root)["final"] is True
    assert record.dataset_repo in world.hub.repos  # kept until the version is downloaded and checked (O133)


def test_a_tampered_file_is_never_used_and_the_download_is_tried_again(world):
    job, record = _started(world)
    world.hub.finish(job.remote_request_id, output_repo=record.output_repo)
    world.hub.repos[record.output_repo]["files"]["adapters/pass-1/adapter_model.safetensors"] = b"not what ran"

    world.trainer.tick()

    assert world.jobs.get(job.id).state == "running"  # not evaluating on a file that doesn't match
    assert remote_training.read_record(job.id, world.root).failure == "mismatch"


def test_the_version_is_made_ready_from_the_jobs_gguf_and_the_uploads_are_deleted_after(world, tmp_path):
    job, record = _started(world)
    world.hub.finish(job.remote_request_id, output_repo=record.output_repo, base=True)
    world.trainer.tick()
    served: list[dict] = []

    def server(runtime, **options):
        served.append({"runtime": runtime, **options})
        return serving.ServeOutcome("ready", runtime, model_ref="model:row_bot:row-bot-personal-v1",
                                    probe={"agent_ready": True}, tool_turn={"ok": True})

    maker = releases.ReleaseMaker(world.root, jobs=world.jobs, clock=world.clock, server=server,
                                  engine_status=lambda: SimpleNamespace(folder=None), runtime=lambda: "row_bot",
                                  start_threads=False)
    assert maker.tick()

    state = releases.read_state(releases.version_dir(job.version, world.root))
    assert (state.phase, state.quantization, state.kept_pass["pass"]) == (releases.READY, "Q4_K_M", 3)
    gguf = releases.version_dir(job.version, world.root) / state.gguf
    assert gguf.read_bytes() == b"GGUF pass 3" and served[0]["gguf"] == gguf
    manifest = json.loads((releases.version_dir(job.version, world.root) / releases.MANIFEST_FILE).read_text())
    assert manifest["identity"]["base"]["name"] == "Qwen/Qwen3.8-27B"  # judged and compared like a local one
    assert world.jobs.get(job.id).state == "ready"
    world.trainer.tick()
    assert not world.hub.repos  # deleted once the model is here and checked (O133)
    assert remote_training.read_record(job.id, world.root).cleaned_at is not None


# ── The card: the recipe's own or a reviewed alternative (28 Oct: no free L40S) ─────


def test_a_recipe_offers_its_own_card_first_then_its_reviewed_alternatives(world):
    offered = world.trainer.flavors(recipes.load("hf-9b"))

    assert [item["flavor"] for item in offered] == ["a100-large", "l40sx1"]  # measured first
    assert offered[1]["price_per_hour"] == 1.8 and offered[1]["seconds_per_step"] > offered[0]["seconds_per_step"]


@pytest.mark.parametrize("alternatives", [
    [{"flavor": "a100-large", "price_per_hour": 2.0}],  # the recipe's own card again
    [{"flavor": "a100-large"}],  # no price
    [{"flavor": "a100-large", "price_per_hour": 2.5, "image": "other"}],  # a key no alternative may set
    "a100-large",
])
def test_a_recipe_s_alternatives_are_checked(alternatives):
    remote = dict(recipes.load("hf-9b").remote)
    remote["alternatives"] = alternatives

    with pytest.raises(recipes.RecipeError):
        recipes._remote(remote, "hf-9b")  # noqa: SLF001


def test_the_quote_prices_every_card_and_the_chosen_one_is_the_run_s(world):
    first = world.trainer.quote(examples=90, upload_bytes=1)

    assert (first.flavor, [item["flavor"] for item in first.choices]) == ("a100-large", ["a100-large", "l40sx1"])

    l40s = first.choices[1]
    assert (l40s["label"], l40s["price_per_hour"], l40s["gpu_memory_gb"]) == ("1x Nvidia L40S", 1.8, 48)
    assert l40s["seconds"] > first.choices[0]["seconds"]  # its own (estimated) time a step
    save_learning_settings(world.root, train_where="hugging_face",
                           hugging_face={"model": "", "monthly_cap_usd": 20, "runs_confirmed": True,
                                         "flavor": "l40sx1"})

    chosen = world.trainer.quote(examples=90, upload_bytes=1)

    assert (chosen.flavor, chosen.price_per_hour, chosen.cost_usd) == ("l40sx1", 1.8, l40s["cost_usd"])
    job, record = _started(world)
    assert record.flavor == "l40sx1" and world.hub.jobs[job.remote_request_id].flavor == "l40sx1"
    assert record.price_per_hour == 1.8


def test_a_card_the_recipe_doesn_t_offer_is_never_used(world):
    save_learning_settings(world.root, train_where="hugging_face",
                           hugging_face={"model": "", "monthly_cap_usd": 20, "runs_confirmed": True,
                                         "flavor": "h200"})

    assert world.trainer.quote(examples=90, upload_bytes=1).flavor == "a100-large"


def test_while_hugging_face_waits_for_a_free_card_the_run_says_so_and_costs_nothing(world):
    from row_bot.application import learning_hugging_face

    job, _record = _started(world)
    world.trainer.tick()  # Hugging Face: SCHEDULING (no card free yet)

    waiting = learning_hugging_face.run_view(world.jobs.get(job.id))

    assert waiting["waiting_for_gpu"] is True and waiting["gpu_label"] == "1x Nvidia A100"
    assert waiting["cost_usd"] == 0
    world.hub.jobs[job.remote_request_id].stage = "RUNNING"
    world.trainer.tick()
    assert learning_hugging_face.run_view(world.jobs.get(job.id))["waiting_for_gpu"] is False
