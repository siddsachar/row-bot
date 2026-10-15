"""The client's recordings (F2) and a conversation's local addresses (F4).

Recordings: every one listed (running ones first), one read live, Stop for any of them with no
approval; the finished video is saved to the conversation's files. A recording also stops when
its conversation is deleted and when Row-Bot quits, and one a crash cut short is marked on the
next start. Local addresses: a conversation's grants listed, and Remove. Nothing records here:
the capture is a fake that "writes" a tiny MP4.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from row_bot import recordings
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

_MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


class _Capture:
    """Writes its 'video' when asked to stop, as a backend finalizes its file."""

    def __init__(self, record: recordings.Recording, path: Path, *, video: bool = True):
        self.record, self.path, self.video = record, path, video
        self.stopped: list[str] = []

    def request_stop(self, reason: str) -> None:
        self.stopped.append(reason)
        if self.video:
            self.path.write_bytes(_MP4)
        recordings.finished(self.record, reason, self.path if self.video else None)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    yield tmp_path / "data"
    recordings.stop_all(timeout=1)


def _start(conversation: str, kind: str = "browser", **kwargs) -> tuple[recordings.Recording, list[_Capture]]:
    made: list[_Capture] = []

    def begin(record, path):
        made.append(_Capture(record, path, **kwargs))
        return made[-1]

    return recordings.start(conversation, kind, max_minutes=5, begin=begin), made


def test_recordings_are_listed_read_and_stopped_by_the_person(service, data):  # noqa: F811
    from row_bot import threads
    from row_bot.application.attachments import read_attachment

    chat = threads.create_thread("Sign-up flow", seed_default_skills=False)
    other = threads.create_thread("Research", seed_default_skills=False)
    old, _ = _start(other, "screen")
    recordings.stop(old.recording_id, by="agent")
    live, captures = _start(chat)

    with _client(service) as client:
        _, headers = bootstrap(client)
        page = client.get("/api/v1/recordings", headers=headers)
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["recording"] == 1
        assert [item["recording_id"] for item in body["items"]] == [live.recording_id, old.recording_id]
        first = body["items"][0]
        assert {key: first[key] for key in ("conversation_id", "kind", "status", "max_seconds", "media_ref",
                                            "problem")} == {
            "conversation_id": chat, "kind": "browser", "status": "recording", "max_seconds": 300,
            "media_ref": None, "problem": ""}
        assert [item["recording_id"] for item in client.get(
            "/api/v1/recordings", params={"conversation_id": chat}, headers=headers).json()["items"]] == [
            live.recording_id]

        stopped = client.post(f"/api/v1/recordings/{live.recording_id}/stop", headers=headers)
        assert stopped.status_code == 200, stopped.text
        view = stopped.json()
        assert (view["status"], view["stopped_by"], view["mime_type"]) == ("saved", "you", "video/mp4")
        assert captures[0].stopped == ["you"]
        assert read_attachment(view["media_ref"])[1] == _MP4  # In the conversation's files.
        assert not recordings.working_path(live).exists()  # The working file goes.

        again = client.get(f"/api/v1/recordings/{live.recording_id}", headers=headers)
        assert again.status_code == 200 and again.json()["status"] == "saved"
        assert client.get("/api/v1/recordings/rec-00000000", headers=headers).status_code == 404
        assert client.get("/api/v1/recordings/not-a-recording", headers=headers).status_code == 404
        assert client.post("/api/v1/recordings/rec-00000000/stop", headers=headers).status_code == 404


def test_a_recording_that_stops_itself_says_why(service, data):  # noqa: F811
    from row_bot import threads

    chat = threads.create_thread("Demo", seed_default_skills=False)
    record, captures = _start(chat)
    captures[0].request_stop("limit")  # The backend reached the time limit.

    assert (record.status, record.stopped_by) == ("saved", "limit")
    assert recordings.summary(record).startswith(f"Recording {record.recording_id}: saved 0:")


def test_recordings_stop_with_their_conversation_and_when_row_bot_quits(service, data):  # noqa: F811
    from row_bot import threads

    chat = threads.create_thread("Demo", seed_default_skills=False)
    other = threads.create_thread("Other", seed_default_skills=False)
    in_chat, chat_captures = _start(chat)
    elsewhere, other_captures = _start(other, "screen")

    assert recordings.stop_conversation(chat) == 1
    assert chat_captures[0].stopped == ["conversation"] and other_captures[0].stopped == []
    assert in_chat.stopped_by == "conversation"

    assert recordings.stop_all(by="quit") == 1
    assert other_captures[0].stopped == ["quit"] and elsewhere.status == "saved"

    assert recordings.delete_conversation(chat) == 1
    assert recordings.get(in_chat.recording_id) is None


def test_one_screen_recording_at_a_time(service, data):  # noqa: F811
    from row_bot import threads

    chat = threads.create_thread("Demo", seed_default_skills=False)
    other = threads.create_thread("Other", seed_default_skills=False)
    _start(chat, "screen")
    with pytest.raises(recordings.RecordingError, match="already being recorded in another chat"):
        _start(other, "screen")
    _start(other, "browser")  # A browser recording isn't the screen.


def test_a_recording_cut_short_by_a_crash_is_marked_and_its_files_go(service, data, monkeypatch):  # noqa: F811
    from row_bot import threads

    chat = threads.create_thread("Demo", seed_default_skills=False)
    record, _captures = _start(chat, "screen")
    folder = recordings.working_path(record)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "turn-00001").mkdir()
    (folder / "turn-00001" / "action.json").write_text("{}", encoding="utf-8")

    # Row-Bot crashed: the next start reads the record it left.
    before = recordings._RECORDS
    monkeypatch.setattr(recordings, "_RECORDS", {})
    assert recordings.sweep_leftovers() == 1
    reread = recordings.get(record.recording_id)
    assert (reread.status, reread.problem, reread.stopped_by) == ("failed", "not_saved", "quit")
    assert not folder.exists()
    # Nothing of the "crashed" run stays in memory once the dictionary goes back (Buddy reads it).
    before.pop(record.recording_id, None)


def test_a_conversation_s_local_addresses_are_listed_and_removed(service, monkeypatch):  # noqa: F811
    from row_bot import threads
    from row_bot.browser.service import BrowserSessionManager
    from row_bot.tools import browser_tool

    monkeypatch.setenv("ROW_BOT_PORT", "8080")
    manager = BrowserSessionManager()
    monkeypatch.setattr(browser_tool, "_session_manager", manager)
    chat = threads.create_thread("Local app", seed_default_skills=False)
    other = threads.create_thread("Other", seed_default_skills=False)
    with _client(service) as client:
        _, headers = bootstrap(client)
        # Nothing started a browser: the list is empty and no browser starts to read it.
        empty = client.get(f"/api/v1/conversations/{chat}/browser/local-addresses", headers=headers)
        assert empty.status_code == 200 and empty.json() == {"conversation_id": chat, "items": []}
        assert not manager.has_active_session()

        session = manager.get_session(chat)
        vite = session.allow_local_address(chat, "http://localhost:5173", source="you")
        session.allow_local_address(chat, "http://127.0.0.1:8000", source="row_bot_server")
        session.allow_local_address(other, "http://localhost:3000", source="reviewer")

        page = client.get(f"/api/v1/conversations/{chat}/browser/local-addresses", headers=headers).json()
        assert [(item["address"], item["source"]) for item in page["items"]] == [
            ("localhost:5173", "you"), ("127.0.0.1:8000", "row_bot_server")]

        assert client.post(f"/api/v1/conversations/{other}/browser/local-addresses/{vite.id}/remove",
                           headers=headers).status_code == 404  # Not that conversation's.
        removed = client.post(f"/api/v1/conversations/{chat}/browser/local-addresses/{vite.id}/remove",
                              headers=headers)
        assert removed.status_code == 200, removed.text
        assert [item["address"] for item in removed.json()["items"]] == ["127.0.0.1:8000"]
        assert client.post(f"/api/v1/conversations/{chat}/browser/local-addresses/{vite.id}/remove",
                           headers=headers).status_code == 404
        assert client.post(f"/api/v1/conversations/{chat}/browser/local-addresses/not-hex/remove",
                           headers=headers).status_code == 404
    manager.kill_all()


def test_recordings_need_a_signed_in_client(service):  # noqa: F811
    with _client(service) as client:
        assert client.get("/api/v1/recordings").status_code == 401
        assert client.post("/api/v1/recordings/rec-00000000/stop").status_code in {401, 403}


@pytest.mark.parametrize(("tool", "text"), [
    ("browser_record", "Approval: asked; approved by you\nRecording rec-1a2b3c4d: recording Row-Bot's browser"),
    ("computer_use", "Recording rec-1a2b3c4d: saved 0:42 of the screen (3.1 MB) to this chat's files."),
])
def test_the_card_names_the_recording_in_the_public_trace(tool, text):
    from row_bot.application.conversation_traces import project_assistant_row_traces

    rows = project_assistant_row_traces([
        {"row": {"id": "assistant-1", "role": "assistant", "blocks": []},
         "tool_calls": [{"id": "call-1", "name": tool, "args": {"action": "start"}}]},
        {"row": {"id": "tool-1", "role": "tool", "tool_call_id": "call-1", "message_id": "tool-1",
                 "blocks": [{"type": "text", "text": text}]}},
    ])

    item = rows[0]["traces"][0]["items"][0]
    assert item["specialization"]["kind"] == "recording"
    assert item["specialization"]["recording_id"] == "rec-1a2b3c4d"


def test_another_tool_quoting_a_recording_line_gets_no_card():
    from row_bot.application.conversation_traces import specialize_tool_result

    line = "Recording rec-1a2b3c4d: saved 0:42 of the screen."
    assert specialize_tool_result({"name": "browser_snapshot", "content": line}) is None
    assert specialize_tool_result({"name": "browser_record", "content": "Page text\n" + line}) is None


def test_an_approval_card_carries_the_tools_own_question():
    from row_bot.application.approval_projection import project_approval_context
    from row_bot.browser.local_access import approval_request

    projected = project_approval_context(approval_request("http://localhost:5173"))
    assert projected["question"] == "Allow Row-Bot's browser to open localhost:5173 in this chat?"
    assert projected["reason"].startswith("localhost:5173 is on this computer.")
    # Two actions resolved together keep the generic question.
    assert "question" not in project_approval_context([approval_request("http://localhost:5173"),
                                                       approval_request("http://localhost:3000")])
