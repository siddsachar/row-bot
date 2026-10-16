"""Personal-model training (L2a): the host ↔ worker protocol and the job state machine.

The protocol: every message carries its version and exactly its own fields; malformed,
unknown, other-version and out-of-order messages are refused; the job spec's lease and
output are the job's own folders; a checkpoint is used only when its files match its
manifest. The state machine: every legal move, every illegal one refused, timestamps and
reasons kept, and a v1 job record still reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from row_bot.learning import contracts
from row_bot.learning.contracts import ContractError, Job, JobTransition
from row_bot.learning.training import protocol
from row_bot.learning.training.jobs import TERMINAL, TRANSITIONS, JobStateError, can_move, transition
from row_bot.learning.training.protocol import ProtocolError, Session

pytestmark = pytest.mark.contract


def _spec(run: Path, **changes) -> dict:
    spec = {
        "job_id": "job_1", "version": 1, "steps_total": 10, "checkpoint_every": 5, "resume_from": None,
        "lease": {"dataset": str(run / "data"), "examples": 12}, "output": str(run / "checkpoints"),
        "recipe": {"name": "recommended", "settings": {"report_to": "none", "rank": 16, "lr": 0.0002,
                                                       "gradient_checkpointing": True}},
    }
    spec.update(changes)
    return spec


def _frame(message: dict) -> bytes:
    return json.dumps(message).encode() + b"\n"


# ── Messages ────────────────────────────────────────────────────────────────


def test_every_message_round_trips_through_one_frame(tmp_path):
    messages = [
        protocol.start(_spec(tmp_path)), protocol.pause(), protocol.advance(3),
        protocol.message("hello", worker="fake"),
        protocol.message("started", step=0, steps_total=10),
        protocol.message("progress", step=1, steps_total=10, loss=1.94, seconds_per_step=3.6),
        protocol.message("progress", step=2, steps_total=10, loss=None, seconds_per_step=None),
        protocol.message("checkpoint", step=5, name="checkpoint-5"),
        protocol.message("artifacts", files=[{"name": "adapter.bin", "bytes": 10, "sha256": "a" * 64}]),
        protocol.message("paused", step=5), protocol.message("finished", step=10),
        protocol.message("error", reason="out_of_gpu_memory", step=4),
        protocol.message("error", reason="engine_error", step=None),
    ]
    for message in messages:
        raw = protocol.encode(message)
        assert raw.endswith(b"\n") and raw.count(b"\n") == 1
        assert protocol.check_message(protocol.decode(raw)) == message


@pytest.mark.parametrize("message, code", [
    ({"v": 2, "type": "hello", "worker": "fake"}, "unsupported_version"),
    ({"v": True, "type": "hello", "worker": "fake"}, "unsupported_version"),
    ({"type": "hello", "worker": "fake"}, "unsupported_version"),
    ({"v": 1, "type": "launch"}, "unknown_type"),
    ({"v": 1, "type": "hello"}, "invalid_message"),
    ({"v": 1, "type": "hello", "worker": "fake", "extra": 1}, "invalid_message"),
    ({"v": 1, "type": "hello", "worker": "../evil"}, "invalid_message"),
    ({"v": 1, "type": "progress", "step": 0, "steps_total": 10, "loss": 1.0, "seconds_per_step": 1.0},
     "invalid_message"),
    ({"v": 1, "type": "progress", "step": 1, "steps_total": 10, "loss": -1.0, "seconds_per_step": 1.0},
     "invalid_message"),
    ({"v": 1, "type": "progress", "step": "1", "steps_total": 10, "loss": 1.0, "seconds_per_step": 1.0},
     "invalid_message"),
    ({"v": 1, "type": "progress", "step": True, "steps_total": 10, "loss": 1.0, "seconds_per_step": 1.0},
     "invalid_message"),
    ({"v": 1, "type": "checkpoint", "step": 5, "name": "checkpoint-6"}, "invalid_message"),
    ({"v": 1, "type": "checkpoint", "step": 5, "name": "../checkpoint-5"}, "invalid_message"),
    ({"v": 1, "type": "error", "reason": "Traceback (most recent call last)", "step": 1}, "invalid_message"),
    ({"v": 1, "type": "advance", "steps": 0}, "invalid_message"),
    ({"v": 1, "type": "artifacts", "files": [{"name": "a.bin", "bytes": 1}]}, "invalid_message"),
])
def test_malformed_messages_are_refused(message, code):
    with pytest.raises(ProtocolError) as error:
        protocol.check_message(message)
    assert error.value.code == code


@pytest.mark.parametrize("raw", [
    b'{"v": 1, "type": "hello", "worker": "fake"}',  # no line ending
    b'{"v": 1, "type": "hello", "type": "hello", "worker": "fake"}\n',  # a duplicate key
    b'{"v": 1, "type": "progress", "step": 1, "steps_total": 2, "loss": NaN, "seconds_per_step": 1}\n',
    b"not json\n",
    b"[1, 2]\n",
    b"\xff\xfe\n",
    b'{"v": 1, "type": "hello", "worker": "' + b"x" * protocol.MAX_FRAME + b'"}\n',
], ids=["no-ending", "duplicate-key", "nan", "not-json", "not-an-object", "not-utf8", "too-large"])
def test_frames_that_are_not_one_small_json_object_are_refused(raw):
    with pytest.raises(ProtocolError):
        protocol.decode(raw)


def test_the_session_accepts_messages_only_in_their_order(tmp_path):
    def session(**changes) -> Session:
        found = Session(_spec(tmp_path, **changes))
        found.accept(protocol.message("hello", worker="fake"))
        return found

    def refused(found: Session, message: dict) -> str:
        with pytest.raises(ProtocolError) as error:
            found.accept(message)
        return error.value.code

    fresh = Session(_spec(tmp_path))
    assert refused(fresh, protocol.message("started", step=0, steps_total=10)) == "out_of_order"  # before hello

    running = session()
    assert refused(running, protocol.message("hello", worker="fake")) == "out_of_order"
    assert refused(running, protocol.message("progress", step=1, steps_total=10, loss=1.0,
                                             seconds_per_step=1.0)) == "out_of_order"  # before started
    assert refused(running, protocol.message("started", step=4, steps_total=10)) == "out_of_order"  # wrong step
    running.accept(protocol.message("started", step=0, steps_total=10))
    running.accept(protocol.message("progress", step=1, steps_total=10, loss=1.0, seconds_per_step=1.0))
    assert refused(running, protocol.message("progress", step=1, steps_total=10, loss=1.0,
                                             seconds_per_step=1.0)) == "out_of_order"  # doesn't move forward
    assert refused(running, protocol.message("progress", step=2, steps_total=9, loss=1.0,
                                             seconds_per_step=1.0)) == "out_of_order"  # another plan
    assert refused(running, protocol.message("checkpoint", step=2, name="checkpoint-2")) == "out_of_order"  # ahead
    assert refused(running, protocol.message("paused", step=1)) == "out_of_order"  # nobody asked
    assert refused(running, protocol.message("finished", step=1)) == "out_of_order"
    running.accept(protocol.message("checkpoint", step=1, name="checkpoint-1"))
    assert refused(running, protocol.message("checkpoint", step=1, name="checkpoint-1")) == "out_of_order"
    running.request_pause()
    running.accept(protocol.message("paused", step=1))
    assert running.ended
    assert refused(running, protocol.message("progress", step=2, steps_total=10, loss=1.0,
                                             seconds_per_step=1.0)) == "out_of_order"  # after the last message

    resumed = session(resume_from="checkpoint-5")
    resumed.accept(protocol.message("started", step=5, steps_total=10))
    for step in range(6, 11):
        resumed.accept(protocol.message("progress", step=step, steps_total=10, loss=None, seconds_per_step=None))
    resumed.accept(protocol.message("artifacts", files=[]))
    resumed.accept(protocol.message("finished", step=10))
    assert resumed.ended

    failing = session()
    failing.accept(protocol.message("error", reason="out_of_gpu_memory", step=None))
    assert failing.ended


# ── The job spec and its scoped lease ───────────────────────────────────────


def test_a_spec_whose_lease_or_output_is_not_the_jobs_own_folder_is_refused(tmp_path):
    run = tmp_path / "learning_models" / "runs" / "job_1"
    (run / "data").mkdir(parents=True)
    (run / "checkpoints").mkdir()
    assert protocol.validate_spec(_spec(run), run)["job_id"] == "job_1"
    profile = tmp_path
    for spec in (
        _spec(run, lease={"dataset": str(profile), "examples": 1}),
        _spec(run, lease={"dataset": str(profile / "threads.db"), "examples": 1}),
        _spec(run, lease={"dataset": str(profile / "learning.db"), "examples": 1}),
        _spec(run, lease={"dataset": str(run / "data" / ".." / ".." / ".."), "examples": 1}),
        _spec(run, output=str(profile / "memory")),
        _spec(run, output=str(run)),
    ):
        with pytest.raises(ProtocolError) as error:
            protocol.validate_spec(spec, run)
        assert error.value.code == "lease_outside_job"


@pytest.mark.parametrize("changes", [
    {"job_id": "../x"}, {"version": 0}, {"steps_total": 0}, {"checkpoint_every": 11},
    {"resume_from": "checkpoint-11"}, {"resume_from": "../checkpoint-1"},
    {"lease": {"dataset": "relative/data", "examples": 1}}, {"output": ""},
    {"recipe": {"name": "x", "settings": {"Bad Key": 1}}},
    {"recipe": {"name": "x", "settings": {"nested": {"a": 1}}}},
    {"recipe": {"name": "x", "settings": {"token": float("inf")}}},
    {"extra": True},
])
def test_a_malformed_spec_is_refused(tmp_path, changes):
    with pytest.raises(ProtocolError):
        protocol.check_spec_shape(_spec(tmp_path, **changes))


def test_a_checkpoint_is_used_only_when_its_files_match_its_manifest(tmp_path):
    folder = tmp_path / "checkpoint-5"
    folder.mkdir()
    (folder / "weights.bin").write_bytes(b"w" * 100)
    (folder / "state.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ProtocolError):
        protocol.read_checkpoint_manifest(folder)  # no manifest yet: still being written
    manifest = protocol.write_checkpoint_manifest(folder, 5)
    assert protocol.read_checkpoint_manifest(folder) == manifest
    assert sorted(item["name"] for item in manifest["files"]) == ["state.json", "weights.bin"]

    (folder / "weights.bin").write_bytes(b"x" * 100)  # same size, other bytes
    with pytest.raises(ProtocolError):
        protocol.read_checkpoint_manifest(folder)
    (folder / "weights.bin").write_bytes(b"w" * 99)
    with pytest.raises(ProtocolError):
        protocol.read_checkpoint_manifest(folder)
    (folder / "weights.bin").unlink()
    with pytest.raises(ProtocolError):
        protocol.read_checkpoint_manifest(folder)
    other = tmp_path / "checkpoint-6"
    folder.rename(other)  # a manifest for another step
    with pytest.raises(ProtocolError):
        protocol.read_checkpoint_manifest(other)


# ── The job state machine ───────────────────────────────────────────────────

STATES = ("planned", "waiting", "preparing", "running", "pausing", "paused", "evaluating", "ready", "trial",
          "in_use", "not_used", "failed", "stopped", "unknown_remote")
LEGAL = {
    ("planned", "waiting"), ("planned", "preparing"), ("planned", "stopped"),
    ("waiting", "preparing"), ("waiting", "planned"), ("waiting", "paused"), ("waiting", "stopped"),
    ("waiting", "failed"),
    ("preparing", "running"), ("preparing", "waiting"), ("preparing", "paused"), ("preparing", "failed"),
    ("preparing", "stopped"),
    ("running", "pausing"), ("running", "paused"), ("running", "evaluating"), ("running", "failed"),
    ("running", "stopped"), ("running", "unknown_remote"),
    ("pausing", "paused"), ("pausing", "evaluating"), ("pausing", "failed"), ("pausing", "stopped"),
    ("paused", "waiting"), ("paused", "preparing"), ("paused", "failed"), ("paused", "stopped"),
    ("evaluating", "ready"), ("evaluating", "not_used"), ("evaluating", "failed"), ("evaluating", "stopped"),
    ("ready", "trial"), ("ready", "not_used"),
    ("trial", "in_use"), ("trial", "not_used"),
    ("in_use", "not_used"),
    ("unknown_remote", "running"), ("unknown_remote", "evaluating"), ("unknown_remote", "failed"),
    ("unknown_remote", "stopped"),
}


def _job(state: str, remote: bool = False, **fields) -> Job:
    backend = "hugging_face" if remote or state == "unknown_remote" else "local"
    return Job(id="job_1", kind="training", created_at=10.0, updated_at=20.0, state=state,  # type: ignore[arg-type]
               backend=backend, steps_total=100, **fields)


def test_the_state_machine_names_every_state_and_its_legal_moves():
    assert set(TRANSITIONS) == set(STATES) == set(get_args(contracts.JobState))
    assert {(state, to) for state, moves in TRANSITIONS.items() for to in moves} == LEGAL
    assert TERMINAL == {"not_used", "failed", "stopped"}


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("to", STATES)
def test_every_move_is_legal_or_refused_without_changing_anything(state, to):
    job = _job(state, remote=to == "unknown_remote")  # only a Hugging Face run can be unknown
    if (state, to) in LEGAL:
        moved = transition(job, to, at=30.0, reason="why")
        assert moved.state == to and can_move(state, to)
        assert moved.updated_at == 30.0 and moved.transitions[-1] == JobTransition(state=to, at=30.0, reason="why")
    else:
        with pytest.raises(JobStateError) as error:
            transition(job, to, at=30.0)
        assert error.value.code == "illegal_transition"
        assert job.state == state and job.transitions == ()


def test_moves_keep_time_in_order_and_the_pause_reason_only_while_paused():
    job = _job("running", steps_done=40, checkpoint_step=40)
    pausing = transition(job, "pausing", at=5.0, reason="in_use", pause_reason="in_use")
    assert pausing.updated_at == 20.0  # a clock that went back never moves time backwards
    paused = transition(pausing, "paused", at=40.0, reason="in_use", pause_reason="in_use")
    assert paused.pause_reason == "in_use"
    resumed = transition(paused, "preparing", at=50.0, reason="train_now")
    assert resumed.pause_reason is None
    assert [(item.state, item.reason) for item in resumed.transitions] == [
        ("pausing", "in_use"), ("paused", "in_use"), ("preparing", "train_now")]
    with pytest.raises(ContractError):
        transition(job, "evaluating", at=60.0, pause_reason="in_use")  # a reason only while pausing or paused


def test_the_transition_history_is_bounded_and_keeps_when_it_was_planned():
    job = _job("paused", transitions=(JobTransition(state="planned", at=10.0),))
    for index in range(300):
        job = transition(job, "waiting", at=100.0 + index)
        job = transition(job, "paused", at=100.5 + index, pause_reason="you")
    assert len(job.transitions) == 200
    assert job.transitions[0] == JobTransition(state="planned", at=10.0)
    assert job.transitions[-1].state == "paused"


def test_a_version_1_job_reads_as_one_with_no_training_fields():
    v1 = {"record": "job", "schema_version": 1, "id": "job_old", "kind": "past_chat_import", "created_at": 1.0,
          "updated_at": 2.0, "state": "running", "steps_done": 61, "steps_total": 214}
    job = contracts.record_from_dict(v1)
    assert isinstance(job, Job)
    assert job.version is None and job.pause_reason is None and job.transitions == ()
    assert job.to_dict()["schema_version"] == 2


@pytest.mark.parametrize("fields", [
    {"state": "running", "pause_reason": "in_use"},
    {"state": "paused", "steps_done": 10, "checkpoint_step": 20},
    {"state": "paused", "pause_reason": "lunch"},
    {"state": "paused", "trigger": "cron"},
    {"state": "paused", "recovered": "guessed"},
])
def test_invalid_training_jobs_are_refused(fields):
    with pytest.raises(ContractError):
        _job(**fields)
