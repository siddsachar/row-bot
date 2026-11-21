"""Training on Hugging Face (C1): the quote, the cap, the upload, the durable submission, lost contact, unknown
outcomes, cancel, download, cleanup and the release, against a fake Hub (no network, no GPU, no real account)."""

from __future__ import annotations

import json
import logging
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


def test_cancel_cancels_on_hugging_face_first_and_deletes_the_uploads(world, caplog):
    caplog.set_level(logging.INFO, logger=remote_training.__name__)
    job, record = _started(world)

    world.trainer.cancel(job.id)

    assert world.hub.jobs[job.remote_request_id].stage == "CANCELED"
    assert world.jobs.get(job.id).state == "stopped" and not world.hub.repos
    record = remote_training.read_record(job.id, world.root)
    assert record.cleaned_at is not None and (record.outcome, record.model_kept) == ("canceled", False)
    assert record.deleted == {"dataset": {"at": world.clock(), "reason": "canceled"},
                              "model": {"at": world.clock(), "reason": "canceled"}}
    assert _deletion_lines(caplog) == ["Learning deleted a Hugging Face dataset repository (canceled)",
                                       "Learning deleted a Hugging Face model repository (canceled)"]


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
    record = remote_training.read_record(job.id, world.root)
    assert record.cleaned_at is not None and not record.model_kept
    assert {kind: item["reason"] for kind, item in record.deleted.items()} == {"dataset": "after_ready",
                                                                               "model": "after_ready"}


# ── A finished, paid model is never deleted before it's downloaded ─────────────


def _deletion_lines(caplog) -> list[str]:
    """What Learning logged about its repositories on Hugging Face: never a repository's name or account."""

    lines = [item.getMessage() for item in caplog.records if item.name == remote_training.__name__
             and "Hugging Face" in item.getMessage() and "repositor" in item.getMessage()]
    assert not any(marker in line for line in lines for marker in ("row-bot-training", "row-bot-personal", "ada/"))
    return lines


def _finished_not_downloaded(world):
    """Training finished on Hugging Face (and was billed) but the download of its outputs failed: it is retried."""

    job, record = _started(world)
    world.hub.finish(job.remote_request_id, output_repo=record.output_repo)
    world.hub.fail["read_text"] = HubError("network")
    world.trainer.tick()
    found = remote_training.read_record(job.id, world.root)
    assert (world.jobs.get(job.id).state, found.outcome, found.phase) == ("running", "completed", "downloading")
    return job, found


def _stop_by_cancel(world, job):
    world.trainer.cancel(job.id)


def _stop_by_hand(world, job):
    world.jobs.move(job.id, "stopped", reason="you")
    world.trainer.tick()


def _fail_by_hand(world, job):
    world.jobs.move(job.id, "failed", reason="engine_error", error_code="engine_error")
    world.trainer.tick()


@pytest.mark.parametrize(("stop", "state", "reason"), [
    (_stop_by_cancel, "stopped", "canceled_after_completion"),  # Cancel while the download is retried
    (_stop_by_hand, "stopped", "stopped_after_completion"),
    (_fail_by_hand, "failed", "failed_after_completion"),
])
def test_a_finished_run_that_ends_before_its_download_keeps_its_model_and_deletes_the_rows(world, caplog, stop,
                                                                                         state, reason):
    caplog.set_level(logging.INFO, logger=remote_training.__name__)
    job, record = _finished_not_downloaded(world)
    calls = len(world.hub.calls)

    stop(world, job)

    assert world.jobs.get(job.id).state == state
    assert record.dataset_repo not in world.hub.repos  # the person's rows are gone from Hugging Face
    assert world.hub.repos[record.output_repo]["files"]["outputs.json"]  # the model they paid for is not
    assert "cancel" not in world.hub.calls[calls:]  # finished: nothing to cancel or bill
    kept = remote_training.read_record(job.id, world.root)
    assert (kept.outcome, kept.model_kept, kept.cleaned_at, kept.cleanup_failed) == ("completed", True, None, False)
    assert kept.deleted == {"dataset": {"at": world.clock(), "reason": reason}}
    assert _deletion_lines(caplog) == [
        f"Learning deleted a Hugging Face dataset repository ({reason})",
        f"Learning kept a Hugging Face model repository: training finished but its outputs aren't downloaded yet "
        f"({reason})"]
    for _ in range(3):
        world.clock.advance(60)
        world.trainer.tick()
    assert record.output_repo in world.hub.repos and world.hub.deleted == [record.dataset_repo]
    assert jobs_module.sweep_runs(world.jobs) == 0  # its record is the only note of that model: kept at start-up
    assert remote_training.read_record(job.id, world.root).model_kept
    # The Hugging Face panel says so, with a link to it in the person's account.
    from row_bot.application import learning_hugging_face

    shown = learning_hugging_face.last_run(world.jobs.all())
    assert (shown["model_kept"], shown["model_url"], shown["cleaned"]) == (
        True, f"https://huggingface.co/{record.output_repo}", False)


def test_a_failed_deletion_is_tried_again_and_still_keeps_the_finished_model(world):
    job, record = _finished_not_downloaded(world)
    world.hub.fail["delete_repo"] = HubError("network")

    world.trainer.cancel(job.id)

    failed = remote_training.read_record(job.id, world.root)
    assert (failed.cleanup_failed, failed.deleted, failed.model_kept) == (True, {}, True)
    assert record.dataset_repo in world.hub.repos and record.output_repo in world.hub.repos
    world.trainer.tick()
    retried = remote_training.read_record(job.id, world.root)
    assert (retried.cleanup_failed, retried.model_kept) == (False, True)
    assert retried.deleted["dataset"]["reason"] == "stopped_after_completion"
    assert record.dataset_repo not in world.hub.repos and record.output_repo in world.hub.repos


def test_a_finished_run_whose_job_is_gone_from_hugging_face_keeps_downloading_never_fails(world):
    job, record = _finished_not_downloaded(world)
    del world.hub.jobs[job.remote_request_id]  # the job's own record is gone; its model repository isn't

    world.trainer.tick()

    assert world.jobs.get(job.id).state == "evaluating"  # downloaded from the model repository, never "failed"
    assert remote_training.read_record(job.id, world.root).phase == "downloaded"
    assert record.dataset_repo in world.hub.repos and record.output_repo in world.hub.repos


def test_a_run_that_lost_contact_and_then_finished_is_downloaded_and_evaluated(world):
    job, _record = _started(world)
    world.hub.fail["job"] = HubError("network")
    world.trainer.tick()
    assert world.jobs.get(job.id).state == "unknown_remote"
    world.hub.finish(job.remote_request_id, output_repo=_record.output_repo)

    world.trainer.tick()

    assert world.jobs.get(job.id).state == "evaluating"
    assert remote_training.read_record(job.id, world.root).phase == "downloaded"


def test_a_downloaded_run_that_is_stopped_deletes_both_repositories(world, caplog):
    caplog.set_level(logging.INFO, logger=remote_training.__name__)
    job, record = _started(world)
    world.hub.finish(job.remote_request_id, output_repo=record.output_repo)
    world.trainer.tick()
    assert remote_training.read_record(job.id, world.root).phase == "downloaded"

    world.jobs.move(job.id, "stopped", reason="you")
    world.trainer.tick()

    assert not world.hub.repos
    cleaned = remote_training.read_record(job.id, world.root)
    assert (cleaned.phase, cleaned.model_kept, cleaned.cleaned_at) == ("cleaned", False, world.clock())
    assert set(cleaned.deleted) == {"dataset", "model"}
    assert _deletion_lines(caplog) == [
        "Learning deleted a Hugging Face dataset repository (stopped_after_completion)",
        "Learning deleted a Hugging Face model repository (stopped_after_completion)"]
    from row_bot.application import learning_hugging_face

    assert learning_hugging_face.last_run(world.jobs.all())["model_kept"] is False
    assert jobs_module.sweep_runs(world.jobs) == 1  # nothing left there: the stopped run's folder goes


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


# ── A model the person checked on this computer (C1 Part 4, O138) ─────────────

CHECKED = "hf:Qwen/Qwen3-14B"


def _checked(world, *, choose: bool = True):
    """Qwen3-14B's check passed here (faked steps); it trains on Hugging Face on its card (an L40S)."""

    from tests.fixtures.open_models import FakeSteps
    from tests.subsystem.learning.test_learning_open_models import _check, _record

    found = _check(world.root, FakeSteps(world.root), _record(world.root))
    if choose:
        save_learning_settings(world.root, hugging_face={"model": CHECKED, "monthly_cap_usd": 20,
                                                         "runs_confirmed": True})
    return found


def test_a_checked_model_is_offered_after_the_certified_ones_never_recommended(world):
    assert CHECKED not in [model.id for model in world.trainer.models()]  # not before its check passed
    _checked(world, choose=False)

    offered = world.trainer.models()

    assert [model.id for model in offered] == ["qwen3.8-27b", "qwen3.5-9b", CHECKED]
    assert not offered[2].certified and offered[2].checks["tools"]["state"] == "passed"
    assert world.trainer.choice().id == "qwen3.8-27b"  # nothing chosen: the strongest certified model
    world.trainer._card_mib = lambda: 8_192  # noqa: SLF001 - an 8 GB card can't run the 14.8B after (O131)
    assert [model.id for model in world.trainer.models()] == ["qwen3.5-9b"]


def test_a_checked_model_s_quote_is_an_estimate_on_the_card_its_check_chose(world):
    _checked(world)

    quote = world.trainer.quote(examples=90, upload_bytes=4_000_000)

    assert (quote.model_id, quote.model_name, quote.flavor) == (CHECKED, "Qwen3-14B", "l40sx1")
    assert quote.measured is False and quote.quantization == "Q4_K_M"  # estimates, never certified numbers
    assert [item["flavor"] for item in quote.choices] == ["l40sx1", "a100-large"]
    recipe = world.trainer.recipe()
    assert (recipe.certified, recipe.starting_model) == (False, CHECKED)


def test_a_checked_model_s_run_uploads_its_pin_and_trains_it_on_its_card(world):
    found = _checked(world)
    job = _planned(world)

    world.trainer.start(job, "train_now", confirmed=True)

    record = remote_training.read_record(job.id, world.root)
    assert world.jobs.get(job.id).state == "running" and record.model_id == CHECKED
    plan = json.loads(world.hub.repos[record.dataset_repo]["files"]["job.json"])
    assert plan["model"] == {"repo": "Qwen/Qwen3-14B", "revision": found.revision,
                             "files": [dict(item) for item in found.pinned]}  # exactly the files the check pinned
    assert plan["quantization"] == "Q4_K_M" and plan["base_gguf"] is True
    assert world.hub.jobs[world.jobs.get(job.id).remote_request_id].flavor == "l40sx1"


def test_a_checked_model_s_version_names_it_and_keeps_its_untouched_gguf_for_the_comparison(world):
    from row_bot.learning.training import base_model, weights

    found = _checked(world)
    job, record = _started(world)
    world.hub.finish(job.remote_request_id, output_repo=record.output_repo, base=True)
    world.trainer.tick()

    def server(runtime, **options):
        return serving.ServeOutcome("ready", runtime, model_ref="model:row_bot:row-bot-personal-v1",
                                    probe={"agent_ready": True}, tool_turn={"ok": True})

    maker = releases.ReleaseMaker(world.root, jobs=world.jobs, clock=world.clock, server=server,
                                  engine_status=lambda: SimpleNamespace(folder=None), runtime=lambda: "row_bot",
                                  start_threads=False)
    assert maker.tick()

    manifest = json.loads((releases.version_dir(job.version, world.root) / releases.MANIFEST_FILE).read_text())
    assert manifest["identity"]["base"] == {"name": "Qwen/Qwen3-14B", "version": found.revision, "sha256": "d" * 64}
    pinned = weights.model_for(CHECKED, world.root)
    built = base_model.read(pinned.id, world.root)  # where the comparison looks for the untouched model
    assert built is not None and (base_model.folder(pinned.id, world.root) / built.gguf).read_bytes() == (
        b"GGUF untouched starting model")
