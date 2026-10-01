"""The conversation's computer-use card: status, picture, Stop, Pause and Resume.

The real platform, approval store and computer-use service run here; only the
Cua driver (a fake transport) and the model (a scripted stream) are synthetic.
Nothing reaches a real desktop, provider or network.
"""
from __future__ import annotations

import base64
import json
import threading

from langchain_core.messages import AIMessage
import pytest

from row_bot.api.v1 import schemas as dto
from row_bot.computer_use.client import CuaClient
from row_bot.computer_use.readiness import acknowledge_disclosure
from row_bot.computer_use.service import ComputerUseService, LeaseOwner
from tests.contracts.client_platform.test_headless_lifecycle import command, platform  # noqa: F401
from tests.fixtures.fake_cua import FakeCuaTransport
from tests.helpers.client_platform_fakes import CheckpointCommit, ScriptedAgentStream, StreamBarrier, fixture_id

pytestmark = pytest.mark.subsystem

CONVERSATION = "conversation-a"
OTHER = "conversation-b"
OWNER = LeaseOwner(CONVERSATION, "generation-a", "task-a")


@pytest.fixture
def computer(tmp_path, monkeypatch):
    from row_bot.computer_use import service as computer_module

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    acknowledge_disclosure()
    transport = FakeCuaTransport()
    client = CuaClient(
        "fake-cua-driver.exe",
        session_id="row-bot-card-session",
        transport_factory=lambda _exe, _session, _env: transport,
    )
    result = ComputerUseService(client_factory=lambda: client, approval_callback=lambda _payload: True)
    monkeypatch.setattr(computer_module, "_SERVICE", result)
    yield result
    result.stop()


def _working(computer, owner: LeaseOwner = OWNER):
    computer.acquire(owner, validate_context=False)
    target = computer.list_windows(owner, app="Calculator")[0]["target_id"]
    return computer.capture(target, owner)


def _read(platform, conversation: str = CONVERSATION) -> dict:  # noqa: F811
    from row_bot.application.client_computer_controls import read_computer_controls

    snapshot = read_computer_controls(platform, conversation, validate=lambda: None)
    dto.ComputerUseSnapshot.model_validate_json(json.dumps(snapshot))
    return snapshot


def _preview(platform, revision: str, conversation: str = CONVERSATION) -> dict:  # noqa: F811
    from row_bot.application.client_computer_controls import read_computer_preview

    preview = read_computer_preview(platform, conversation, revision, validate=lambda: None)
    dto.ComputerUsePreview.model_validate_json(json.dumps(preview))
    return preview


def _card(action: str, label: str) -> dict:
    return {"command_id": fixture_id(label), "client_session_id": fixture_id("client-a"),
            "type": action}


def _execute(platform, card: dict, conversation: str = CONVERSATION, *, owner: str = "session-a") -> dict:  # noqa: F811
    from row_bot.application.client_computer_controls import execute_computer_command

    receipt = execute_computer_command(platform, card, conversation, owner_id=owner,
                                       key=card["command_id"], validate=lambda: None)
    dto.ComputerUseReceipt.model_validate_json(json.dumps(receipt))
    return receipt


def _approval(approval_id: str) -> dict:
    from row_bot.tasks import _get_conn

    with _get_conn() as conn:
        return dict(conn.execute("SELECT * FROM approval_requests WHERE id=?", (approval_id,)).fetchone())


def _settle(platform, conversation: str = CONVERSATION) -> None:  # noqa: F811
    for handle in list(platform.registry.active(conversation)):
        assert handle.producer_done.wait(10)


class PausedTurn:
    """A turn that uses the computer, then is paused by the person."""

    def __init__(self, platform, computer) -> None:  # noqa: F811
        self.platform, self.computer = platform, computer
        self.using = threading.Event()
        self.paused = threading.Event()
        self.order: list[tuple] = []
        self.model = ScriptedAgentStream((
            CheckpointCommit((AIMessage(id="resumed-output", content="Done."),), "resumed-output"),
            ("done", "Done."),
        ))
        platform.stream_factory, platform.resume_factory = self.stream, self.resume

    def stream(self, text, enabled, config, *, stop_event=None):
        generation = config["configurable"]["generation_id"]
        _working(self.computer, LeaseOwner(CONVERSATION, generation, "task-a"))
        self.using.set()
        assert self.paused.wait(10)
        yield ("interrupt", [{**self.computer.takeover_interrupt_payload(), "__interrupt_id": "pause-1"}])

    def resume(self, enabled, config, approved, *, interrupt_ids=None, stop_event=None):
        status = self.computer.status_snapshot()
        self.order.append(("agent", approved, tuple(interrupt_ids or ()), status["state"],
                           status["generation_id"] == config["configurable"]["generation_id"]))
        yield from self.model.resume(enabled, config, approved, interrupt_ids=interrupt_ids,
                                     stop_event=stop_event)

    def start(self, *, pause_with_card: bool = True):
        receipt = self.platform.execute(
            owner_id="fixture-owner", idempotency_key=fixture_id("paused:key"), target=CONVERSATION,
            command=command("conversation.submit", "paused", {
                "submission_id": fixture_id("paused"), "text": "Add 2 and 2 in Calculator",
                "attachment_refs": [],
                "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}}))
        assert self.using.wait(10)
        handle = self.platform.registry.get(receipt["execution_id"])
        if pause_with_card:
            paused = _execute(self.platform, _card("computer_use.pause", "pause"))
            assert paused["status"] == "completed", paused
        else:
            self.computer.take_over(thread_id=CONVERSATION, generation_id=handle.generation_id)
        self.paused.set()
        assert handle.producer_done.wait(10)
        assert handle.approval_id
        return handle


def test_the_card_sees_the_session_only_in_its_own_conversation(platform, computer):  # noqa: F811
    idle = _read(platform)
    assert (idle["active"], idle["state"], idle["can_stop"]) == (False, "stopped", False)

    observation = _working(computer)
    mine = _read(platform)
    assert {key: mine[key] for key in ("active", "state", "app", "has_picture", "approval_id",
                                       "can_pause", "can_resume", "can_stop")} == {
        "active": True, "state": "working", "app": "Calculator", "has_picture": True,
        "approval_id": None, "can_pause": True, "can_resume": False, "can_stop": True}

    other = _read(platform, OTHER)
    assert (other["active"], other["state"], other["app"], other["has_picture"]) == (False, "stopped", "", False)
    assert not (other["can_pause"] or other["can_resume"] or other["can_stop"])

    # Reading is passive; a new picture is a new revision.
    assert _read(platform)["revision"] == mine["revision"]
    computer.capture(observation.target.target_id, OWNER)
    assert _read(platform)["revision"] != mine["revision"]


def test_the_picture_is_bound_to_its_revision_and_hidden_while_paused(platform, computer):  # noqa: F811
    from row_bot.application.client_computer_controls import ClientComputerControlError

    observation = _working(computer)
    snapshot = _read(platform)
    preview = _preview(platform, snapshot["revision"])
    assert (preview["state"], preview["mime_type"]) == ("available", "image/png")
    assert base64.b64decode(preview["image_base64"]) == observation.screenshot

    with pytest.raises(ClientComputerControlError) as stale:
        _preview(platform, "0" * 64)
    assert (stale.value.code, stale.value.current_revision) == ("computer_use_revision_conflict", snapshot["revision"])
    with pytest.raises(ClientComputerControlError, match="invalid_computer_use_command"):
        _preview(platform, "not-a-revision")

    other = _read(platform, OTHER)
    assert _preview(platform, other["revision"], OTHER)["state"] == "inactive"

    computer.take_over(thread_id=CONVERSATION)
    paused = _read(platform)
    assert (paused["state"], paused["has_picture"], paused["can_pause"]) == ("paused", False, False)
    hidden = _preview(platform, paused["revision"])
    assert (hidden["state"], hidden["mime_type"], hidden["image_base64"]) == ("hidden", None, None)


def test_pause_hands_control_over_once_per_command(platform, computer):  # noqa: F811
    from row_bot.application.client_computer_controls import ClientComputerControlError

    _working(computer)
    pause = _card("computer_use.pause", "pause-once")
    first = _execute(platform, pause)
    assert (first["status"], first["code"], first["computer_use"]["state"]) == ("completed", None, "paused")
    assert computer.status_snapshot()["state"] == "waiting_user"

    # The same command reads its first outcome instead of pausing again.
    assert _execute(platform, pause) == first
    with pytest.raises(ClientComputerControlError, match="idempotency_mismatch"):
        _execute(platform, {**pause, "type": "computer_use.stop"})
    # A different Pause is refused while the person already has control.
    again = _execute(platform, _card("computer_use.pause", "pause-twice"))
    assert (again["status"], again["code"]) == ("rejected", "computer_use_busy")


def test_pause_and_resume_refuse_when_they_do_not_apply(platform, computer):  # noqa: F811
    idle = _execute(platform, _card("computer_use.pause", "pause-idle"))
    assert (idle["status"], idle["code"]) == ("rejected", "computer_use_inactive")

    _working(computer, LeaseOwner(OTHER, "generation-b", "task-b"))
    foreign = _execute(platform, _card("computer_use.pause", "pause-foreign"))
    assert (foreign["status"], foreign["code"]) == ("rejected", "computer_use_inactive")
    assert computer.status_snapshot()["state"] == "observing"

    working = _execute(platform, _card("computer_use.resume", "resume-working"), OTHER)
    assert (working["status"], working["code"]) == ("rejected", "computer_use_not_paused")


def test_resume_picks_up_from_a_fresh_capture_then_continues_the_turn(platform, computer):  # noqa: F811
    turn = PausedTurn(platform, computer)
    paused = turn.start()
    snapshot = _read(platform)
    assert (snapshot["state"], snapshot["approval_id"], snapshot["can_resume"], snapshot["can_stop"]) == (
        "paused", paused.approval_id, True, True)
    assert computer.status_snapshot()["generation_id"] == paused.generation_id

    resumed = _execute(platform, _card("computer_use.resume", "resume"))
    assert resumed["status"] == "completed", resumed
    _settle(platform)
    # The computer was running again (a fresh capture) before the agent went
    # on, and the continuing turn still holds the same session.
    assert turn.order == [("agent", True, ("pause-1",), "observing", True)]
    assert turn.model.calls[0]["generation_id"] == paused.generation_id
    assert _approval(paused.approval_id)["status"] == "approved"
    # The finished turn gives the computer back.
    assert computer.status_snapshot()["active"] is False


def test_a_resumed_turn_keeps_its_generation_so_its_stop_reaches_it(platform, computer):  # noqa: F811
    turn = PausedTurn(platform, computer)
    paused = turn.start()
    barrier = StreamBarrier(release_on_cancel=True)
    turn.model = ScriptedAgentStream((barrier,))
    assert _execute(platform, _card("computer_use.resume", "resume-running"))["status"] == "completed"
    assert barrier.entered.wait(10)
    (running,) = platform.registry.active(CONVERSATION)
    assert running.generation_id == paused.generation_id

    # A stop aimed at that turn (a Buddy click) reaches the run going on now.
    platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id("stop-resumed:key"),
                     target=CONVERSATION,
                     command=command("conversation.stop", "stop-resumed", {"generation_id": paused.generation_id}))
    assert running.cancel_scope.is_cancelled()
    _settle(platform)
    assert computer.status_snapshot()["active"] is False


def test_stop_releases_the_computer_ends_the_turn_and_withdraws_the_pause(platform, computer, tmp_path):  # noqa: F811
    turn = PausedTurn(platform, computer)
    paused = turn.start()

    stopped = _execute(platform, _card("computer_use.stop", "stop"))
    assert (stopped["status"], stopped["computer_use"]["state"]) == ("completed", "stopped")
    assert computer.status_snapshot()["active"] is False
    assert _approval(paused.approval_id)["status"] == "cancelled"
    assert turn.order == [] and turn.model.calls == []
    generation = platform.snapshot(CONVERSATION)["generation"]
    assert (generation["status"], generation["approval_id"]) == ("stopped", None)
    assert _read(platform)["can_stop"] is False
    # The picture never reaches a receipt or anything stored.
    assert "image" not in json.dumps(stopped)
    stored = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert b"iVBOR" not in stored


def test_approving_a_pause_elsewhere_resumes_the_computer_before_the_agent(platform, computer):  # noqa: F811
    turn = PausedTurn(platform, computer)
    paused = turn.start(pause_with_card=False)

    result = platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id("approve:key"),
                              target=paused.approval_id,
                              command=command("approval.resolve", "approve", {"decision": "approve"}))
    assert result["status"] == "accepted"
    _settle(platform)
    assert turn.order == [("agent", True, ("pause-1",), "observing", True)]


def test_approving_a_pause_from_another_device_is_refused(platform, computer):  # noqa: F811
    from row_bot.application.client_platform import ClientPlatformError

    turn = PausedTurn(platform, computer)
    paused = turn.start(pause_with_card=False)
    with pytest.raises(ClientPlatformError, match="computer_use_local_only"):
        platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id("remote:key"),
                         target=paused.approval_id, runtime_surface="remote_client",
                         command=command("approval.resolve", "remote", {"decision": "approve"}))
    assert computer.status_snapshot()["state"] == "waiting_user"
    assert _approval(paused.approval_id)["status"] == "pending" and turn.order == []


def test_denying_a_pause_stops_computer_use_instead_of_replaying_the_action(platform, computer):  # noqa: F811
    turn = PausedTurn(platform, computer)
    paused = turn.start(pause_with_card=False)

    result = platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id("deny:key"),
                              target=paused.approval_id,
                              command=command("approval.resolve", "deny", {"decision": "reject"}))
    assert result["status"] == "completed"
    assert computer.status_snapshot()["active"] is False
    assert turn.order == [] and turn.model.calls == []
    assert _approval(paused.approval_id)["status"] == "cancelled"


def test_stopping_a_conversation_stops_only_its_own_computer_use(platform, computer):  # noqa: F811
    _working(computer)
    platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id("stop-b:key"), target=OTHER,
                     command=command("conversation.stop", "stop-b", {}))
    platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id("stop-old:key"), target=CONVERSATION,
                     command=command("conversation.stop", "stop-old", {"generation_id": "generation-old"}))
    assert computer.status_snapshot()["active"] is True

    platform.execute(owner_id="fixture-owner", idempotency_key=fixture_id("stop-a:key"), target=CONVERSATION,
                     command=command("conversation.stop", "stop-a", {"generation_id": OWNER.generation_id}))
    assert computer.status_snapshot()["active"] is False


def test_a_finished_turn_gives_the_computer_back_but_a_waiting_one_keeps_it(platform, computer):  # noqa: F811
    def finishing(text, enabled, config, *, stop_event=None):
        _working(computer, LeaseOwner(CONVERSATION, config["configurable"]["generation_id"], "task-a"))
        yield ("done", "")

    def waiting(text, enabled, config, *, stop_event=None):
        _working(computer, LeaseOwner(CONVERSATION, config["configurable"]["generation_id"], "task-a"))
        yield ("interrupt", [{"__interrupt_id": "shell-1", "tool": "shell", "args": {}}])

    for label, factory, keeps in (("finishing", finishing, False), ("waiting", waiting, True)):
        platform.stream_factory = factory
        receipt = platform.execute(
            owner_id="fixture-owner", idempotency_key=fixture_id(label + ":key"), target=CONVERSATION,
            command=command("conversation.submit", label, {
                "submission_id": fixture_id(label), "text": "Use the calculator", "attachment_refs": [],
                "model_selection": {"provider_id": "fixture", "model_ref": "fixture/model"}}))
        assert platform.registry.get(receipt["execution_id"]).producer_done.wait(10)
        assert computer.status_snapshot()["active"] is keeps, label


def test_withdrawing_is_limited_to_computer_approvals(platform, computer):  # noqa: F811
    from row_bot.application.client_computer_controls import stop_computer_use
    from row_bot.tasks import create_approval_request

    _, shell = create_approval_request("pass-shell", "", "conversation", "Run a command", resume_kind="conversation",
                                       source_thread_id=CONVERSATION, parent_thread_id=CONVERSATION,
                                       approval_payload_json={"interrupt": [{"tool": "shell"}]})
    _, app = create_approval_request("pass-app", "", "conversation", "Allow Calculator", resume_kind="conversation",
                                     source_thread_id=CONVERSATION, parent_thread_id=CONVERSATION,
                                     approval_payload_json={"interrupt": [{"tool": "computer_use"}]})
    assert stop_computer_use(CONVERSATION) == [app]
    assert (_approval(shell)["status"], _approval(app)["status"]) == ("pending", "cancelled")


def test_reading_the_card_needs_no_computer_use_module(platform, monkeypatch):  # noqa: F811
    import sys

    monkeypatch.delitem(sys.modules, "row_bot.computer_use.service", raising=False)
    assert _read(platform)["state"] == "stopped"
    assert "row_bot.computer_use.service" not in sys.modules
