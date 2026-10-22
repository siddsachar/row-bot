"""The release check's own rules (S1): scratch folders only, the shared card booked, resume, the simulated
person's fixed choices. The people's real runs are opt-in (``tests/e2e``); here the person's process is a fake.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tests.e2e.learning.seeded import generator, release_check, scratch
from tests.e2e.learning.seeded.live import State
from tests.e2e.learning.seeded.person import SimulatedPerson, approval_decision, draw

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture(autouse=True)
def _a_tier_not_yet_certified(monkeypatch):
    """These harness tests run the 8 GB tier beside one that isn't certified, which is reported and never run.
    L2x certified the 32 GB tier, so it stands in for an uncertified one here."""

    real = release_check.ReleaseCheck.tier_status
    monkeypatch.setattr(release_check.ReleaseCheck, "tier_status",
                        lambda self, tier: "not certified yet (L2x certifies the larger tiers)" if tier == "32gb"
                        else real(self, tier))


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    (folder / ".row-bot").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: folder))
    monkeypatch.setenv(scratch.PROTECT_ENV, str(tmp_path / "backups"))
    return folder


def test_scratch_folders_never_reach_the_everyday_profile_its_backups_the_eval_profile_or_a_checkout(tmp_path, home):
    eval_profile = tmp_path / "eval-profile"
    for refused in (home / ".row-bot", home / ".row-bot" / "scratch", home, tmp_path / "backups" / "run",
                    eval_profile / "inside"):
        with pytest.raises(scratch.ScratchError):
            scratch.check_scratch(refused, eval_profile)
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "src" / "row_bot").mkdir(parents=True)
    with pytest.raises(scratch.ScratchError):
        scratch.check_scratch(checkout / ".local" / "s1", eval_profile)
    assert scratch.check_scratch(tmp_path / "scratch" / "s1", eval_profile) == (tmp_path / "scratch" / "s1").resolve()


QUIET = {"quiet": lambda: None, "after_quiet": lambda: False, "card": lambda: None}


def test_the_gpu_is_booked_after_other_lanes_and_the_persons_own_training(tmp_path):
    booking = tmp_path / "gpu-booking.txt"
    booking.write_text("19:10 lane-A START L2w serve (expect 15 min, 6 GB)\n", encoding="utf-8")
    training = [False, True, False]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:
            booking.write_text(booking.read_text(encoding="utf-8") + "19:30 lane-A END L2w serve\n", encoding="utf-8")

    gate = scratch.GpuGate(booking, "lane-B", label="S1 test", clock=lambda: "20:00", sleep=sleep,
                           training=lambda: training.pop(0) if training else False, say=lambda _text: None, **QUIET)
    gate.acquire("training v1", expect="30 min, 9 GB")
    assert len(waits) == 2  # lane A's open booking, then the person's own training
    gate.acquire("comparison")  # already holding: no second line
    lines = booking.read_text(encoding="utf-8").splitlines()
    # While lane A held it, a note said once that lane B waits (O116: the lanes yield between steps).
    assert lines[1] == "20:00 lane-B note: S1 test waits for the card (next: training v1; expect 30 min, 9 GB)"
    assert lines[-1] == "20:00 lane-B START S1 test: training v1 (expect 30 min, 9 GB; yields to the person's Learning engine)"
    assert scratch.open_bookings(booking.read_text(encoding="utf-8")) == {"lane-B": lines[-1].split("START ", 1)[1]}
    gate.release()
    assert booking.read_text(encoding="utf-8").splitlines()[-1] == "20:00 lane-B END S1 test"
    assert scratch.open_bookings(booking.read_text(encoding="utf-8")) == {}


def test_after_a_crash_the_lanes_own_open_booking_is_taken_over_not_doubled(tmp_path):
    booking = tmp_path / "gpu-booking.txt"
    booking.write_text("01:00 lane-B START S1: training (yields to the person's Learning engine)\n"
                       "01:05 lane-A note: L2x waits for the card\n", encoding="utf-8")
    waits: list[float] = []
    gate = scratch.GpuGate(booking, "lane-B", clock=lambda: "01:10", sleep=waits.append, training=lambda: False,
                           say=lambda _text: None, **QUIET)
    gate.acquire("training")
    assert len(booking.read_text(encoding="utf-8").splitlines()) == 2
    assert waits == []  # nor does it yield: the booking is still ours
    gate.release()
    assert scratch.open_bookings(booking.read_text(encoding="utf-8")) == {}


def test_another_lanes_exclusive_window_holds_the_card_until_its_exclusive_end(tmp_path):
    booking = tmp_path / "gpu-booking.txt"
    booking.write_text("14:00 lane-A EXCLUSIVE START tier certification (expect 120 min, 30 GB)\n"
                       "14:01 lane-A START 32 GB tier timed run\n14:40 lane-A END 32 GB tier timed run\n",
                       encoding="utf-8")
    assert scratch.exclusive_open(booking.read_text(encoding="utf-8")) == ["lane-A"]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            booking.write_text(booking.read_text(encoding="utf-8") + "15:30 lane-A EXCLUSIVE END\n", encoding="utf-8")

    gate = scratch.GpuGate(booking, "lane-B", clock=lambda: "15:31", sleep=sleep, training=lambda: False,
                           say=lambda _text: None, **QUIET)
    gate.acquire("training", expect="30 min, 9 GB")
    assert len(waits) == 2  # lane A's ordinary END inside the window didn't open it
    assert scratch.exclusive_open(booking.read_text(encoding="utf-8")) == []
    gate.release()


def test_a_lane_that_wrote_it_waits_books_first_and_a_late_one_doesnt_hold_the_card(tmp_path):
    booking = tmp_path / "gpu-booking.txt"
    booking.write_text("13:00 lane-A START L2x ablation\n13:40 lane-A END L2x ablation\n"
                       "13:41 lane-A note: L2x waits for the card (next: 9B training; expect 35 min, 27 GB)\n",
                       encoding="utf-8")
    assert scratch.waiting_lanes(booking.read_text(encoding="utf-8")) == {
        "lane-A": "L2x waits for the card (next: 9B training; expect 35 min, 27 GB)"}
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 1:  # lane A takes its turn, then ends it
            booking.write_text(booking.read_text(encoding="utf-8") + "13:42 lane-A START L2x 9B training\n",
                               encoding="utf-8")
        if len(waits) == 3:
            booking.write_text(booking.read_text(encoding="utf-8") + "14:20 lane-A END L2x 9B training\n",
                               encoding="utf-8")

    gate = scratch.GpuGate(booking, "lane-B", clock=lambda: "14:21", sleep=sleep, training=lambda: False,
                           say=lambda _text: None, **QUIET)
    gate.acquire("comparison")
    assert len(waits) == 3 and scratch.open_bookings(booking.read_text(encoding="utf-8")) == {
        "lane-B": "S1: comparison (yields to the person's Learning engine)"}
    gate.release()
    # A waiting note with nobody booking after it gets a few minutes, not the card for ever.
    booking.write_text(booking.read_text(encoding="utf-8") + "14:30 lane-A note: L2x waits for the card\n",
                       encoding="utf-8")
    waits.clear()
    gate.acquire("comparison")
    assert len(waits) == int(scratch.YIELD_SECONDS / scratch.WAIT_SECONDS)
    gate.release()
    # When we wrote that we wait and the other lane then yields (its END, then its own waiting note), it's our turn.
    booking.write_text(booking.read_text(encoding="utf-8") + "15:00 lane-A START L2x 24 GB training\n"
                       "15:05 lane-B note: S1 waits for the card\n15:06 lane-A END L2x (yielding: Lane B waits)\n"
                       "15:06 lane-A note: L2x waiting for the card; I book in Lane B's next gap\n", encoding="utf-8")
    waits.clear()
    gate.acquire("comparison")
    assert waits == []
    gate.release()


def test_no_card_in_the_persons_quiet_hours_and_after_them_not_while_something_else_holds_it(tmp_path):
    assert not scratch.in_quiet_hours(datetime(2026, 10, 22, 0, 39))
    assert scratch.in_quiet_hours(datetime(2026, 10, 22, 0, 40)) and scratch.in_quiet_hours(datetime(2026, 10, 22, 4, 59))
    assert not scratch.in_quiet_hours(datetime(2026, 10, 22, 5, 0))
    assert scratch.quiet_reason(datetime(2026, 10, 23, 2, 0)) and scratch.quiet_reason(datetime(2026, 10, 23, 13, 0)) is None
    assert scratch.after_quiet_hours(datetime(2026, 10, 23, 5, 0)) and not scratch.after_quiet_hours(datetime(2026, 10, 23, 8, 0))
    booking = tmp_path / "gpu-booking.txt"
    booking.write_text("", encoding="utf-8")
    quiet = ["quiet hours", "quiet hours", None, None, None, None]
    card = [9_000, 7_000, 3_000]
    said: list[str] = []
    gate = scratch.GpuGate(booking, "lane-B", clock=lambda: "05:03", sleep=lambda _s: None, training=lambda: False,
                           quiet=lambda: quiet.pop(0), after_quiet=lambda: True, card=lambda: card.pop(0),
                           say=said.append)
    gate.acquire("comparison")
    assert said == ["GPU: quiet hours; waiting",
                    "GPU: the card holds 9,000 MiB that no lane booked (after the quiet hours); waiting",
                    "GPU: the card holds 7,000 MiB that no lane booked (after the quiet hours); waiting"]
    assert gate.waited == 4 * scratch.WAIT_SECONDS and not card
    gate.release()


def test_the_persons_own_training_is_seen_from_its_engine_process(tmp_path):
    profile = tmp_path / ".row-bot"
    engine = str(profile / "runtimes" / "learning" / "engine" / "abc" / "py" / "python.exe")
    assert scratch.person_is_training(lambda: ["C:/other/python.exe", engine], profile)
    assert not scratch.person_is_training(lambda: ["C:/other/python.exe", ""], profile)


def _args(tmp_path, **extra):
    values = ["--out", str(tmp_path / "out"), "--scratch", str(tmp_path / "scratch"), "--eval-profile",
              str(tmp_path / "eval-profile"), "--days", "2", "--people", "all", "--tiers", "8gb,32gb",
              "--gpu-booking", str(tmp_path / "gpu-booking.txt")]
    for key, value in extra.items():
        values += [f"--{key.replace('_', '-')}", str(value)]
    return release_check.parse(values)


class FakePerson:
    """The person's process: writes its state and result where it was told, exits as scripted."""

    def __init__(self, exits: dict[str, list[int]] | None = None, tokens: int = 1_000) -> None:
        self.exits = exits or {}
        self.tokens = tokens
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, command: list[str], env: dict[str, str], log: Path) -> int:
        person = command[command.index("--person") + 1]
        run_dir = Path(command[command.index("--run-dir") + 1])
        self.calls.append((person, dict(env)))
        state = State(run_dir / "state.json")
        state.data["starts"] = int(state.data.get("starts", 0)) + 1
        state.save()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("ran\n", encoding="utf-8")
        planned = self.exits.get(person) or []
        code = planned.pop(0) if planned else 0
        if code == 0:
            (run_dir / "result.json").write_text(json.dumps({
                "person": person, "name": person, "tier": "8gb", "requests": [], "versions": {}, "survival": {},
                "gpu": {}, "tokens": {"ledger": {}, "sim_person": {"total": 0}, "cloud_total": self.tokens},
            }), encoding="utf-8")
        return code


def _files(root: Path) -> set[str]:
    return {item.relative_to(root).as_posix() for item in root.rglob("*")}


def test_each_person_runs_in_their_own_scratch_profile_and_nothing_is_written_elsewhere(tmp_path, home, monkeypatch):
    monkeypatch.setenv("ROW_BOT_SOMETHING_ELSE", "x")
    (tmp_path / "eval-profile").mkdir()
    before = _files(tmp_path)
    fake = FakePerson()
    check = release_check.ReleaseCheck(_args(tmp_path), runner=fake, say=lambda _text: None)
    built = check.execute()
    # Every person's work first, then every person's benchmark (O116).
    assert [person for person, _env in fake.calls] == generator.people() * 2
    for person, env in fake.calls:
        data = Path(env["ROW_BOT_DATA_DIR"])
        assert data == (tmp_path / "scratch" / "8gb" / person / "data").resolve()
        assert env["ROW_BOT_LEARNING_PREVIEW"] == "1" and env["ROW_BOT_DESKTOP_NOTIFICATIONS"] == "off"
        assert "ROW_BOT_SOMETHING_ELSE" not in env  # nothing of this shell's Row-Bot settings reaches a person
    written = _files(tmp_path) - before
    assert written and all(path.startswith(("out", "scratch")) for path in written)
    assert (tmp_path / "out" / "report.md").is_file() and (tmp_path / "out" / "report.json").is_file()
    assert built["tiers"]["32gb"].startswith("not certified")  # reported, never run
    assert "32gb" not in {path.split("/")[1] for path in written if path.startswith("out/") and path.count("/") > 1}


def test_a_planned_crash_starts_the_person_again_and_a_full_cap_stops_cleanly(tmp_path, home):
    (tmp_path / "eval-profile").mkdir()
    fake = FakePerson(exits={"idris": [release_check.EXIT_PLANNED_CRASH, 0], "mara": [release_check.EXIT_TOKEN_CAP]},
                      tokens=600)
    check = release_check.ReleaseCheck(_args(tmp_path, cap_tokens=1_000), runner=fake, say=lambda _text: None)
    check.execute()
    runs = check.run["people_runs"]
    assert [item["code"] for item in runs["8gb/idris"]["exits"]] == [86, 0] and runs["8gb/idris"]["state"] == "done"
    assert runs["8gb/mara"]["state"] == "token_cap"
    assert runs["8gb/wren"] == {"state": "token_cap"}  # 600 spent by idris + mara's cap: wren isn't started
    state = json.loads((tmp_path / "out" / "8gb" / "idris" / "state.json").read_text(encoding="utf-8"))
    assert state["starts"] == 2  # the same state, continued


def test_the_same_command_again_continues_and_one_person_or_stage_can_run_alone(tmp_path, home):
    (tmp_path / "eval-profile").mkdir()
    fake = FakePerson()
    release_check.ReleaseCheck(_args(tmp_path), runner=fake, say=lambda _text: None).execute()
    again = FakePerson()
    check = release_check.ReleaseCheck(_args(tmp_path, people="wren", stages="days"), runner=again,
                                       say=lambda _text: None)
    check.execute()
    ((person, _env),) = again.calls
    assert person == "wren"
    command_stages = check.stages
    assert command_stages == ["days"]
    state = json.loads((tmp_path / "out" / "8gb" / "wren" / "state.json").read_text(encoding="utf-8"))
    assert state["starts"] == 3  # its work and its benchmark, then the days again
    assert json.loads((tmp_path / "out" / "run.json").read_text(encoding="utf-8"))["started_at"]


def test_a_person_kept_under_another_name_runs_again_fresh_and_is_reported_apart(tmp_path, home):
    (tmp_path / "eval-profile").mkdir()
    release_check.ReleaseCheck(_args(tmp_path), runner=FakePerson(tokens=700), say=lambda _text: None).execute()
    check = release_check.ReleaseCheck(_args(tmp_path), runner=FakePerson(tokens=300), say=lambda _text: None)
    kept = check.archive("mara@before-O106")
    assert {path.parent.parent.name for path in kept} == {"out", "scratch"}
    assert not (tmp_path / "out" / "8gb" / "mara").exists()
    with pytest.raises(scratch.ScratchError):
        check.archive("mara/../idris@x")
    built = check.execute()  # Mara again, fresh: her new state starts at nothing
    state = json.loads((tmp_path / "out" / "8gb" / "mara" / "state.json").read_text(encoding="utf-8"))
    assert state["starts"] == 2  # her work and her benchmark, in the fresh run
    assert [person["kept_as"] for person in built["earlier"]] == ["mara@before-O106"]
    assert [person["person"] for person in built["people"]] == generator.people()
    assert built["tokens"]["total_cloud"] == 3 * 300 + 700  # the kept run's tokens count in the spend
    assert "## Earlier runs, kept for the record" in (tmp_path / "out" / "report.md").read_text(encoding="utf-8")


def test_the_state_file_remembers_what_is_done(tmp_path):
    state = State(tmp_path / "run" / "state.json")
    state.data["requests"].append({"id": "mara-d01-0"})
    state.finish("past")
    again = State(tmp_path / "run" / "state.json")
    assert again.done("past") and not again.done("days")
    assert again.request_done("mara-d01-0") and not again.request_done("mara-d01-1")


def test_the_simulated_persons_choices_are_fixed_by_the_seed():
    person = generator.load_person("mara")
    sim = SimulatedPerson(person, seed=7)
    choices = [sim.choose(f"mara-d{day:02d}-0", 1) for day in range(1, 200)]
    assert choices == [SimulatedPerson(person, seed=7).choose(f"mara-d{day:02d}-0", 1) for day in range(1, 200)]
    share = choices.count("teach") / len(choices)
    assert abs(share - person["policy"]["teach_share"]) < 0.1
    assert sim.choose("x", 2) == "correct" and sim.choose("x", 3) == "give_up"
    assert draw(1, "a") == draw(1, "a") != draw(2, "a")
    assert sim.stop_now(tool_calls=person["policy"]["patience_tool_calls"] + 1, seconds=1)
    assert not sim.stop_now(tool_calls=1, seconds=1)


def test_approvals_are_given_only_for_what_the_request_named():
    allowed = ("inbox/delivery-2026-11-03.txt",)
    move = {"tool": "workspace_move_file", "args": {"source_path": "inbox/delivery-2026-11-03.txt",
                                                    "destination_path": "archive/deliveries/delivery-2026-11-03.txt"}}
    assert approval_decision(move, allowed)
    assert not approval_decision({**move, "args": {"source_path": "records/donations.csv"}}, allowed)
    assert not approval_decision({"tool": "workspace_file_delete", "args": {"file_path": "policies/x.md"}}, allowed)
    assert approval_decision({"tool": "learning_mail_send", "args": {}}, allowed)  # nothing destructive


def test_the_persons_words_come_from_the_model_and_a_template_stands_in_when_it_fails():
    person = generator.load_person("idris")
    history = generator.generate("idris", seed=1, days=2)
    request = history.stream[0]

    class Reply:
        content = "That total should leave out the refunded line, please redo it."
        usage_metadata = {"input_tokens": 420, "output_tokens": 18}

    class Model:
        def __init__(self, fail: bool) -> None:
            self.fail = fail
            self.prompts: list[str] = []

        def invoke(self, messages):
            self.prompts.append(messages[0].content)
            if self.fail:
                raise ConnectionError("offline")
            return Reply()

    sim = SimulatedPerson(person, seed=3)
    sim._llm = Model(fail=False)
    attempt = 1 if sim.choose(request.id, 1) == "correct" else 2
    reaction = sim.react(request, "It's 12.", "expected 15", attempt)
    assert reaction.action == "correct" and reaction.source == "model" and reaction.text.startswith("That total")
    assert sim.tokens.total == 438 and sim.tokens.calls == 1
    prompt = sim._llm.prompts[0]
    assert request.intent in prompt and person["name"] in prompt
    assert "expected 15" in prompt  # what went wrong, in words; never the checker itself
    failing = SimulatedPerson(person, seed=3)
    failing._llm = Model(fail=True)
    fallback = failing.react(request, "It's 12.", "expected 15", attempt)
    assert fallback.source == "template" and request.intent in fallback.text and failing.tokens.failures == 2
