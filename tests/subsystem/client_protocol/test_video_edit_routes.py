"""The client's video edits (F3): every one listed (running ones first), one read live, and Stop for
any of them with no approval. A finished edit's result is in its conversation's files, and an
edit_video step's card follows its edit by id. Nothing runs ffmpeg here: the job is a fake.
"""

from __future__ import annotations

import threading

import pytest

from row_bot import video_edits
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

_MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


@pytest.fixture
def jobs(tmp_path, monkeypatch):
    """Edits whose work waits until it is stopped, or saves a tiny video at once."""
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    finish = {"now": False}

    def run(edit, request):
        from row_bot.application.attachments import register_attachment

        if finish["now"]:
            saved = register_attachment(edit.conversation_id, edit.output_name, _MP4)
            video_edits._set(edit, status="saved", media_ref=saved["attachment_ref"], mime_type="video/mp4",
                             size_bytes=len(_MP4), expected_seconds=3.0)
        else:
            video_edits._set(edit, status="editing", expected_seconds=3.0)
            edit.stop_event.wait(10)
            video_edits._set(edit, status="stopped", stopped_by=edit.stopped_by or "you")
        edit.ended_at = edit.started_at + 1
        edit.done.set()

    monkeypatch.setattr(video_edits, "_run", run)
    yield finish
    video_edits.stop_all(timeout=2)


def _start(conversation: str, *, kind: str = "video") -> video_edits.VideoEdit:
    from row_bot.media_sources import Media

    edit = video_edits.start(video_edits.Request(
        conversation_id=conversation, kind=kind, sources=[Media(label="demo.mp4", kind="video", ref="x")],
        output_name="demo-edited.mp4" if kind == "video" else "frame"))
    for _ in range(200):
        if edit.status != "preparing":
            break
        threading.Event().wait(0.01)
    return edit


def test_edits_are_listed_read_and_stopped_by_the_person(service, jobs):  # noqa: F811
    from row_bot import threads
    from row_bot.application.attachments import read_attachment

    chat = threads.create_thread("Demo cut", seed_default_skills=False)
    other = threads.create_thread("Other", seed_default_skills=False)
    jobs["now"] = True
    saved = _start(other)
    jobs["now"] = False
    running = _start(chat)

    with _client(service) as client:
        _, headers = bootstrap(client)
        page = client.get("/api/v1/video-edits", headers=headers)
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["running"] == 1
        assert [item["edit_id"] for item in body["items"]] == [running.edit_id, saved.edit_id]
        first = body["items"][0]
        assert {key: first[key] for key in ("conversation_id", "kind", "status", "summary", "media_ref",
                                            "problem", "progress")} == {
            "conversation_id": chat, "kind": "video", "status": "editing", "summary": "Converted to MP4",
            "media_ref": None, "problem": "", "progress": None}
        assert [item["edit_id"] for item in client.get(
            "/api/v1/video-edits", params={"conversation_id": chat}, headers=headers).json()["items"]] == [
            running.edit_id]
        done = client.get(f"/api/v1/video-edits/{saved.edit_id}", headers=headers).json()
        assert (done["status"], done["mime_type"], done["duration_seconds"]) == ("saved", "video/mp4", 3.0)
        assert read_attachment(done["media_ref"])[1] == _MP4  # In the conversation's files.

        stopped = client.post(f"/api/v1/video-edits/{running.edit_id}/stop", headers=headers)
        assert stopped.status_code == 200, stopped.text
        assert (stopped.json()["status"], stopped.json()["stopped_by"]) == ("stopped", "you")

        assert client.get("/api/v1/video-edits/edit-00000000", headers=headers).status_code == 404
        assert client.get("/api/v1/video-edits/not-an-edit", headers=headers).status_code == 404
        assert client.post("/api/v1/video-edits/edit-00000000/stop", headers=headers).status_code == 404


def test_an_edit_video_step_shows_its_edit_s_card():
    from row_bot.application.conversation_traces import _public_specialization, specialize_tool_result

    for content in ("Video edit edit-0a1b2c3d: saved 0:03 of video (12 KB) to this chat's files as demo.mp4; …",
                    "Approval: asked; approved by you\nVideo edit edit-0a1b2c3d: getting ffmpeg, 3 MB of 30 MB."):
        specialization = specialize_tool_result({"name": "edit_video", "content": content})
        public = _public_specialization(specialization)
        assert (public["kind"], public["edit_id"]) == ("video_edit", "edit-0a1b2c3d")
    assert specialize_tool_result({"name": "run_command", "content": "Video edit edit-0a1b2c3d: x"}) is None
    assert specialize_tool_result({"name": "edit_video", "content": "Couldn't find demo.mp4."}) is None
