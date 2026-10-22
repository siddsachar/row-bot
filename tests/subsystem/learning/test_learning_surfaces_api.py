"""Learning's API for its surfaces (L1a): the preview flag both ways, the snapshot, and the
revision-checked commands with their admission receipts: turning on with the setup choices,
pause and resume, off, Teach and its Undo, "Don't learn from this chat", the download answer
and deleting learning data. Hardware detection and the past-chat window have their own files."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from row_bot import learning, threads  # noqa: F401 - bind threads to the session's folder first
from row_bot.api.v1.schemas import (
    LearningConversation,
    LearningHardware,
    LearningLessonPage,
    LearningReceipt,
    LearningSnapshot,
)
from row_bot.learning import scope
from row_bot.learning.contracts import Decision, Episode, Signal
from row_bot.learning.store import LearningStore
from tests.fixtures.learning import (
    FakeThreads,
    START,
    all_record_text,
    learning_files,
    save_learning_settings,
    set_learning_preview,
)
from tests.subsystem.client_protocol.test_settings_snapshot_api import BASE, api

# ruff: noqa: F401, F811 -- the imported pytest fixture is requested by name.

pytestmark = pytest.mark.subsystem

LEARNING = "/api/v1/learning"
SETUP = {
    "sources": {"my_chats": True, "workflows_and_goals": True, "past_chats": False},
    "apply_mode": "ask_first",
    "monthly_token_limit": 2_000_000,
    "train_in_quiet_hours": True,
    "train_where": "this_computer",
}


@pytest.fixture
def chats(monkeypatch):
    """The conversations scope sees: a chat in the app, a shared channel chat, and a Telegram
    chat on a channel set up without its owner's id."""

    fake = FakeThreads().chat("chat-a").chat("chat-b").channel("group-1", "telegram", "-100", shared=True)
    fake.channel("tg-paired", "telegram", "4242")
    monkeypatch.setattr(scope, "SavedThreads", lambda: fake)
    return fake


@pytest.fixture
def no_channels(monkeypatch):
    from row_bot.application import client_learning

    monkeypatch.setattr(client_learning, "_owner_unset_channels", lambda: [])


def _snapshot(client, headers) -> dict:
    response = client.get(LEARNING, headers=headers)
    assert response.status_code == 200, response.text
    LearningSnapshot.model_validate_json(response.text)
    return response.json()


def _command(client, headers, action: str, revision: str | None = None, command_id: str | None = None,
             **fields):
    identity = command_id or str(uuid4())
    if revision is None:
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


def _turn_on(client, headers, **choices) -> dict:
    return _ok(_command(client, headers, "turn_on", setup={**SETUP, **choices}))


# ── The preview flag (O34) ───────────────────────────────────────────────────


def test_without_the_preview_flag_learning_is_unavailable_and_off_whatever_was_saved(api, monkeypatch):
    client, headers, data, _ = api
    set_learning_preview(monkeypatch, False)
    save_learning_settings(data, enabled=True)
    before = learning_files(data)

    assert learning.is_enabled() is False
    assert learning.read_settings().enabled is False
    for path in ("", "/hardware", "/lessons", "/conversations/chat-a", f"/commands/{uuid4()}"):
        response = client.get(LEARNING + path, headers=headers)
        assert response.status_code == 404 and response.json()["code"] == "learning_unavailable", path
    refused = _command(client, headers, "turn_on", revision="0" * 64, setup=SETUP)
    assert refused.status_code == 404 and refused.json()["code"] == "learning_unavailable"
    handshake = client.post("/api/v1/handshake", json={}, headers={"Origin": "http://localhost"}).json()
    assert "learning:preview" not in handshake["application_capabilities"]
    settings = client.get(BASE, headers=headers).json()
    assert settings["learning"]["availability"] == "unavailable"
    request = {"settings_revision": settings["revision"], "page": "learning", "field": "enabled", "value": True}
    review = client.post(BASE + "/review", headers=headers, json=request)
    assert review.status_code != 200
    # Nothing new was created: only the settings file written above.
    assert learning_files(data) == before == ["learning_config.json"]


def test_with_the_preview_flag_the_app_offers_learning_off_with_the_setup_defaults(api, no_channels):
    client, headers, data, _ = api
    handshake = client.post("/api/v1/handshake", json={}, headers={"Origin": "http://localhost"}).json()
    assert "learning:preview" in handshake["application_capabilities"]

    snapshot = _snapshot(client, headers)

    assert snapshot["state"] == "off"
    assert snapshot["sources"] == {"my_chats": True, "workflows_and_goals": True, "past_chats": True}
    assert snapshot["apply_mode"] == "ask_first"
    assert snapshot["spending"]["limit_tokens"] == 2_000_000 and snapshot["spending"]["used_tokens"] == 0
    assert snapshot["spending"]["limit_reached"] is False
    # Quiet hours are Dream Cycle's hours; nothing runs in them yet, so there is no next look.
    dream = client.get(BASE, headers=headers).json()["preferences"]["dream_cycle"]
    assert snapshot["quiet_hours"] == {"window_start": dream["window_start"], "window_end": dream["window_end"],
                                       "next_look_at": None}
    assert snapshot["past_chats"] == {"state": "none", "total": 0, "done": 0}
    model = snapshot["personal_model"]
    assert model["starting_model"]["name"] == "Qwen3.5 2B" and model["download"] == "not_asked"
    assert model["engine"] == "not_downloaded"
    assert snapshot["data"] == {"exists": False, "bytes": 0}
    # Reading Learning creates nothing.
    assert learning_files(data) == []


# ── Turning on, pause, resume, off ───────────────────────────────────────────


def test_turning_on_saves_every_setup_choice_creates_the_store_and_is_admitted_once(api, no_channels):
    client, headers, data, _ = api
    before = _snapshot(client, headers)
    command_id = str(uuid4())

    receipt = _ok(_command(client, headers, "turn_on", revision=before["revision"], command_id=command_id,
                           setup={**SETUP, "apply_mode": "automatic", "monthly_token_limit": 500_000,
                                  "train_where": "hugging_face"}))

    assert receipt["summary"] == "Learning is on."
    snapshot = receipt["snapshot"]
    assert snapshot["state"] == "on" and snapshot["revision"] != before["revision"]
    assert snapshot["apply_mode"] == "automatic" and snapshot["spending"]["limit_tokens"] == 500_000
    assert snapshot["sources"]["past_chats"] is False
    assert snapshot["personal_model"]["train_where"] == "hugging_face"
    assert snapshot["data"]["exists"] is True
    saved = json.loads((data / learning.SETTINGS_FILE).read_text(encoding="utf-8"))
    assert saved["enabled"] is True and saved["paused"] is False
    assert learning.is_enabled() is True
    # The same command again answers the same receipt and changes nothing.
    again = _command(client, headers, "turn_on", revision=before["revision"], command_id=command_id,
                     setup={**SETUP, "apply_mode": "automatic", "monthly_token_limit": 500_000,
                            "train_where": "hugging_face"})
    assert again.json()["summary"] == receipt["summary"]
    receipt_read = client.get(f"{LEARNING}/commands/{command_id}", headers=headers)
    assert receipt_read.status_code == 200 and receipt_read.json()["summary"] == "Learning is on."


def test_a_command_with_a_stale_revision_is_refused_and_changes_nothing(api, no_channels):
    client, headers, data, _ = api
    stale = _snapshot(client, headers)["revision"]
    _turn_on(client, headers)

    response = _command(client, headers, "pause", revision=stale)

    assert response.status_code == 409 and response.json()["code"] == "learning_revision_conflict"
    assert _snapshot(client, headers)["state"] == "on"


def test_pause_records_nothing_and_resume_carries_on(api, no_channels, chats, monkeypatch):
    client, headers, data, _ = api
    _turn_on(client, headers)
    started: list[bool] = []
    monkeypatch.setattr(learning, "start_past_chat_import", lambda: started.append(True))

    paused = _ok(_command(client, headers, "pause"))

    assert paused["snapshot"]["state"] == "paused"
    assert learning.is_enabled() is False
    assert scope.check("chat-a").reason == scope.LEARNING_PAUSED
    # Teach is refused while paused (nothing new is recorded) ...
    refused = _command(client, headers, "teach", conversation_id="chat-a", note="Use the ID")
    assert refused.status_code == 409 and refused.json()["code"] == "learning_paused"
    assert LearningStore(data).find("signal") == []
    # ... and the conversation says so: no Teach, but its menu item stays.
    state = client.get(f"{LEARNING}/conversations/chat-a", headers=headers).json()
    assert state["state"] == "paused"

    resumed = _ok(_command(client, headers, "resume"))

    assert resumed["snapshot"]["state"] == "on" and learning.is_enabled() is True
    assert started == [True]


def test_turning_off_keeps_the_data_and_says_so(api, no_channels):
    client, headers, data, _ = api
    _turn_on(client, headers)

    receipt = _ok(_command(client, headers, "turn_off"))

    assert receipt["snapshot"]["state"] == "off"
    assert receipt["summary"] == "Learning is off. Your learning data is kept until you delete it."
    assert receipt["snapshot"]["data"]["exists"] is True
    assert learning.is_enabled() is False


def test_the_download_answer_is_recorded_and_reads_as_ready_where_nothing_can_download(api, no_channels):
    # No graphics card here (the Learning tests' default check): the answer is kept, and the
    # personal model reads as Ready to download (O36). The download itself: test_learning_downloads.
    client, headers, _data, _ = api
    _turn_on(client, headers)

    waiting = _ok(_command(client, headers, "answer_download", download="download"))
    assert waiting["snapshot"]["personal_model"]["download"] == "waiting"
    assert waiting["summary"] == "Ready to download."
    assert waiting["snapshot"]["personal_model"]["download_state"]["state"] == "ready"
    not_now = _ok(_command(client, headers, "answer_download", download="not_now"))
    assert not_now["snapshot"]["personal_model"]["download"] == "not_now"


# ── Teach and Undo ──────────────────────────────────────────────────────────


def _captured_reply(data, thread_id: str, message_id: str) -> Decision:
    """A reply Learning captured earlier (another process: only the store knows it)."""

    store = LearningStore(data)
    message = {"type": "ai", "data": {"content": "It's the draft.", "id": message_id, "type": "ai"}}
    sha = store.put_blob(json.dumps(message).encode("utf-8"))
    store.put(Episode(id="ep_t1", thread_id=thread_id, source="my_chat", started_at=START))
    found = Decision(id="dec_t1", episode_id="ep_t1", thread_id=thread_id, sequence=0, kind="model_call",
                     created_at=START, raw_output_blob=sha, executed="raw")
    store.put(found)
    return found


def test_teach_saves_a_signal_on_the_replys_captured_decision_and_undo_removes_it(api, no_channels, chats):
    client, headers, data, _ = api
    _turn_on(client, headers)
    reply = _captured_reply(data, "chat-a", "run-msg-1")

    receipt = _ok(_command(client, headers, "teach", conversation_id="chat-a", message_id="run-msg-1",
                           category="wrong_answer", note="Use the approved budget, not the draft."))

    assert receipt["summary"] == "Thanks. Row-Bot will learn from this."
    saved = LearningStore(data).get("signal", receipt["signal_id"])
    assert isinstance(saved, Signal) and saved.kind == "teach"
    assert (saved.decision_id, saved.episode_id) == (reply.id, reply.episode_id)
    assert (saved.category, saved.note) == ("wrong_answer", "Use the approved budget, not the draft.")

    undone = _ok(_command(client, headers, "undo_teach", signal_id=receipt["signal_id"]))

    assert undone["summary"] == "Removed."
    assert LearningStore(data).find("signal") == []
    assert "approved budget" not in all_record_text(data)


def test_teach_on_a_failed_step_links_that_step(api, no_channels, chats):
    client, headers, data, _ = api
    _turn_on(client, headers)
    store = LearningStore(data)
    store.put(Episode(id="ep_s1", thread_id="chat-a", source="my_chat", started_at=START))
    store.put(Decision(id="dec_step", episode_id="ep_s1", thread_id="chat-a", sequence=1, kind="tool_call",
                       created_at=START, status="error", tool_name="read_file", tool_call_id="call-9"))

    receipt = _ok(_command(client, headers, "teach", conversation_id="chat-a", tool_call_id="call-9",
                           category="wrong_tool_or_step"))

    saved = LearningStore(data).get("signal", receipt["signal_id"])
    assert isinstance(saved, Signal) and saved.decision_id == "dec_step" and saved.note is None


def test_a_teach_note_is_redacted_like_captured_text(api, no_channels, chats):
    client, headers, data, _ = api
    _turn_on(client, headers)
    secret = "-".join(("sk", "proj", "abcdefghijklmnopqrstuvwxyz0123456789"))  # made up, built at run time

    receipt = _ok(_command(client, headers, "teach", conversation_id="chat-b", note=f"Use key {secret} next time"))

    saved = LearningStore(data).get("signal", receipt["signal_id"])
    assert isinstance(saved, Signal) and secret not in (saved.note or "")
    assert saved.decision_id is None  # nothing of that chat was captured: kept on the conversation


def test_teach_is_refused_in_a_chat_with_other_people_and_the_next_command_still_runs(api, no_channels, chats):
    client, headers, data, _ = api
    _turn_on(client, headers)

    refused = _command(client, headers, "teach", conversation_id="group-1", note="Wrong")

    assert refused.status_code == 409 and refused.json()["code"] == "learning_chat_not_used"
    assert LearningStore(data).find("signal") == []
    # The refusal was final: the same chat takes the next command.
    _ok(_command(client, headers, "opt_out", conversation_id="group-1"))


def test_the_conversation_read_tells_a_chat_whether_to_offer_teach(api, chats, monkeypatch):
    client, headers, _data, _ = api
    from row_bot.application import client_learning

    note = "Telegram chats are used once your Telegram user ID is set in Settings › Apps › Telegram."
    monkeypatch.setattr(client_learning, "_owner_unset_channels",
                        lambda: [{"channel": "telegram", "name": "Telegram", "note": note}])

    def state(conversation: str) -> dict:
        response = client.get(f"{LEARNING}/conversations/{conversation}", headers=headers)
        assert response.status_code == 200, response.text
        LearningConversation.model_validate_json(response.text)
        return response.json()

    assert state("chat-a") == {"conversation_id": "chat-a", "state": "off", "reason": ""}
    _turn_on(client, headers)
    assert state("chat-a")["state"] == "learning"
    assert state("group-1") == {"conversation_id": "group-1", "state": "not_used",
                                "reason": "Chats with other people are never used."}
    assert state("tg-paired") == {"conversation_id": "tg-paired", "state": "not_used", "reason": note}


# ── Don't learn from this chat ──────────────────────────────────────────────


def test_dont_learn_from_this_chat_purges_what_was_recorded_and_can_be_undone(api, no_channels, chats):
    client, headers, data, _ = api
    _turn_on(client, headers)
    _captured_reply(data, "chat-a", "run-msg-2")
    LearningStore(data).put(Episode(id="ep_other", thread_id="chat-b", source="my_chat", started_at=START))

    receipt = _ok(_command(client, headers, "opt_out", conversation_id="chat-a"))

    assert receipt["summary"] == "Row-Bot won’t learn from this chat. What it recorded from it is deleted."
    assert receipt["snapshot"]["opted_out_conversations"] == ["chat-a"]
    store = LearningStore(data)
    assert store.owned_by("chat-a") == [] and len(store.owned_by("chat-b")) == 1
    assert client.get(f"{LEARNING}/conversations/chat-a", headers=headers).json()["state"] == "opted_out"

    back = _ok(_command(client, headers, "opt_in", conversation_id="chat-a"))

    assert back["snapshot"]["opted_out_conversations"] == []
    assert client.get(f"{LEARNING}/conversations/chat-a", headers=headers).json()["state"] == "learning"


# ── Delete learning data ────────────────────────────────────────────────────


def test_delete_learning_data_removes_it_and_switched_off_chats_stay_off(api, no_channels, chats):
    client, headers, data, _ = api
    _turn_on(client, headers)
    _captured_reply(data, "chat-b", "run-msg-3")
    _ok(_command(client, headers, "opt_out", conversation_id="chat-a"))

    receipt = _ok(_command(client, headers, "delete_data"))

    assert receipt["summary"].startswith("Learning data deleted (")
    store = LearningStore(data)
    assert store.find("decision") == [] and store.find("episode") == []
    assert store.opted_out_chats() == ["chat-a"]
    assert receipt["snapshot"]["opted_out_conversations"] == ["chat-a"]


def test_only_delete_data_ever_deletes_learning_data(api, no_channels, chats, monkeypatch):
    # The command dispatcher's last branch deletes everything: every other action the API accepts, whatever
    # its fields, ends in its own branch (or is refused) and never reaches it.
    import typing

    from row_bot.api.v1 import security
    from row_bot.api.v1.schemas import LearningCommand

    client, headers, data, _ = api
    monkeypatch.setattr(security.ClientSecurity, "rate", lambda self, session, lane: None)  # ~70 requests in a row
    _turn_on(client, headers)
    _captured_reply(data, "chat-a", "run-msg-3")  # the actions below name chat-b (opt_out removes its own)
    deleted: list[str] = []
    monkeypatch.setattr(LearningStore, "delete_all", lambda self: deleted.append("store") or None)
    actions = [action for action in typing.get_args(LearningCommand.model_fields["action"].annotation)
               if action != "delete_data"]
    actions.sort(key=lambda action: action in ("turn_off", "pause"))  # everything else while Learning is on
    fields = {"version": 1, "kind": "document_lookups", "job_id": "job_unknown", "runtime": "ollama",
              "folder": "C:/nowhere", "item_id": "item_unknown", "model": "qwen3.5-2b", "download": "not_now",
              "conversation_id": "chat-b", "message_id": "run-msg-3", "signal_id": "sig_unknown",
              "category": "wrong_answer"}
    extra = {"turn_on": {"setup": SETUP},
             "save_advanced": {"advanced": {"starting_model": "qwen3.5-2b", "rank": 16, "learning_rate": 0.0002,
                                            "epochs": 3, "context": 16384}}}

    reached = 0
    for action in actions:
        response = _command(client, headers, action, **fields, **extra.get(action, {}))
        reached += response.status_code != 422  # the API took it (it reached Learning's own checks)

    assert len(actions) >= 30 and reached == len(actions) and deleted == []
    assert LearningStore(data).owned_by("chat-a")  # what Learning recorded is still there


def test_delete_with_nothing_to_delete_is_refused(api, no_channels):
    client, headers, _data, _ = api

    response = _command(client, headers, "delete_data")

    assert response.status_code == 409 and response.json()["code"] == "learning_data_missing"


# ── The other reads ─────────────────────────────────────────────────────────


def test_the_hardware_read_reports_the_check_and_free_space(api, monkeypatch):
    client, headers, _data, _ = api
    from row_bot.learning.training import hardware

    checks: list[bool] = []

    def fake_check(*, refresh: bool = False, detector=None):
        checks.append(refresh)
        return hardware.classify([hardware.Gpu("NVIDIA GeForce RTX 5070 Laptop GPU", 8151, "596.08")])

    monkeypatch.setattr(hardware, "check", fake_check)

    response = client.get(LEARNING + "/hardware", headers=headers)
    again = client.get(LEARNING + "/hardware?refresh=true", headers=headers)

    assert response.status_code == 200 and again.status_code == 200
    value = LearningHardware.model_validate_json(response.text)
    assert value.state == "gpu" and value.gpu and value.gpu.label == "NVIDIA GeForce RTX 5070 Laptop (8\u00a0GB)"
    assert value.tier and value.tier.certified and value.tier.summary.startswith("Can train small models")
    assert [model.name for model in value.starting_models] == ["Qwen3.5 2B"]
    assert value.free_bytes is not None and value.free_bytes > 0
    assert checks == [False, True]


def test_lessons_lists_what_row_bot_saved_for_itself_as_not_tested(api, monkeypatch):
    client, headers, _data, _ = api
    from row_bot import knowledge_graph

    asked: list[str] = []

    def summaries(entity_type=None, **_kwargs):
        asked.append(entity_type)
        return [{"id": "mem-1", "subject": "Browser: login page detection", "description": "Wait for the form.",
                 "updated_at": "2026-10-10T09:00:00"}, {"id": "mem-2", "subject": "", "description": "no title"}]

    monkeypatch.setattr(knowledge_graph, "list_entity_summaries", summaries)

    response = client.get(LEARNING + "/lessons", headers=headers)

    assert response.status_code == 200, response.text
    page = LearningLessonPage.model_validate_json(response.text)
    assert asked == ["self_knowledge"]
    assert [(item.title, item.status, item.origin) for item in page.items] == [
        ("Browser: login page detection", "not_tested", "agent_saved")]
