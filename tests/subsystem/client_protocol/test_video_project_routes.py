"""The Video panel's API (5.3.1 B3): read a project at any version, change it (a new version by
"you", made on the version the person saw), go back, make again. Nothing is spoken or encoded:
the voice and the edit job are fakes.
"""

from __future__ import annotations

import threading
import time

import pytest

from row_bot import video_edits
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

_MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """ffmpeg is here; speaking returns a tiny voice file; each edit saves a tiny video with its checks."""
    from row_bot import ffmpeg_runtime, sound

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: object())
    said: list[str] = []

    def speak(text, **_options):
        said.append(text)
        return sound.Speech(wav=b"RIFF\x00\x00\x00\x00WAVE" + b"0" * 32, seconds=1.0, voice="test-voice")

    def run(edit, request):
        from row_bot.application.attachments import register_attachment

        if request.gif:
            saved = register_attachment(edit.conversation_id, edit.output_name, b"GIF89a" + bytes(32))
            video_edits._set(edit, status="saved", media_ref=saved["attachment_ref"], mime_type="image/gif")
            edit.done.set()
            return
        saved = register_attachment(edit.conversation_id, edit.output_name, _MP4)
        scenes = request.steps[0].scenes
        video_edits._set(edit, status="saved", media_ref=saved["attachment_ref"], mime_type="video/mp4",
                         size_bytes=len(_MP4), expected_seconds=3.0 * len(scenes),
                         marks=[3.0 * index for index in range(len(scenes) + 1)],
                         checks=["Length 0:06, 640×360.", "Sound: present (average -18 dB, peak -2 dB)."],
                         checks_passed=True)
        edit.done.set()

    monkeypatch.setattr(sound, "speak", speak)
    monkeypatch.setattr(video_edits, "_run", run)
    yield said
    video_edits.stop_all(timeout=2)


def _made(client, headers, project_id):
    """The project once its video is made (waited for in-process, so the API's rate limit isn't spent)."""
    from row_bot import video_projects

    deadline = time.monotonic() + 30  # Generous: the whole suite may be running alongside.
    while time.monotonic() < deadline:
        if video_projects.view(video_projects.get(project_id))["status"] not in {"voicing", "rendering"}:
            return client.get(f"/api/v1/video-projects/{project_id}", headers=headers).json()
        threading.Event().wait(0.02)
    raise AssertionError("the video was never made")


def test_the_panel_reads_changes_and_goes_back_on_a_project(service, studio, monkeypatch):  # noqa: F811
    from row_bot import ffmpeg_runtime, threads, video_projects
    from row_bot.application.attachments import register_attachment

    chat = threads.create_thread("Launch demo", seed_default_skills=False)
    register_attachment(chat, "signup.mp4", _MP4)
    project = video_projects.create(chat, "Launch demo", [
        {"source": "signup.mp4", "end": 3, "line": "First, sign up."},
        {"source": "signup.mp4", "start": 3, "line": "Then pick a plan."}])

    with _client(service) as client:
        _, headers = bootstrap(client)
        # Made once the app is up: its start-up sweep marks an edit still running then as cut short.
        video_projects.make(project, 1)
        first = _made(client, headers, project.project_id)
        assert (first["status"], first["number"], first["checks_passed"]) == ("made", 1, True), first["problem"]
        assert [(scene["at"], scene["length"], scene["voiced"]) for scene in first["scenes"]] == [
            (0.0, 3.0, True), (3.0, 3.0, True)]
        assert first["edit"]["media_ref"] and first["checks"][1].startswith("Sound: present")

        scenes = [{key: scene[key] for key in ("scene_id", "source", "start", "end", "speed", "zoom", "card",
                                                "line")} for scene in first["scenes"]]
        scenes[1]["line"] = "Then pick the plan that fits."
        scenes.reverse()
        changed = client.put(f"/api/v1/video-projects/{project.project_id}", headers=headers,
                             json={"base": 1, "scenes": scenes, "captions": True})
        assert changed.status_code == 200, changed.text
        assert (changed.json()["number"], changed.json()["versions"][0]["by"]) == (2, "you")
        assert changed.json()["status"] in {"voicing", "rendering", "made"}, "the panel follows it being made"
        assert changed.json()["changed"] == [scenes[0]["scene_id"]]
        second = _made(client, headers, project.project_id)
        assert [scene["line"] for scene in second["scenes"]] == ["Then pick the plan that fits.", "First, sign up."]
        assert studio[-1] == "Then pick the plan that fits.", "only the changed line is voiced again"
        assert len(studio) == 3 and second["captions"]

        stale = client.put(f"/api/v1/video-projects/{project.project_id}", headers=headers,
                           json={"base": 1, "scenes": scenes})
        assert (stale.status_code, stale.json()["code"]) == (409, "video_project_stale")

        back = client.post(f"/api/v1/video-projects/{project.project_id}/restore", headers=headers,
                           json={"number": 1})
        assert back.status_code == 200, back.text
        assert (back.json()["number"], back.json()["status"], back.json()["edit"]["edit_id"]) == (
            3, "made", first["edit"]["edit_id"]), "going back reuses that version's video"
        older = client.get(f"/api/v1/video-projects/{project.project_id}", params={"version": 2}, headers=headers)
        assert older.json()["number"] == 2 and older.json()["latest"] == 3

        exported = client.post(f"/api/v1/video-projects/{project.project_id}/export", headers=headers,
                               json={"shape": "gif"})
        assert exported.status_code == 200, exported.text
        assert [item["shape"] for item in exported.json()["exports"]] == ["gif"]
        video_edits.wait(video_edits.get(exported.json()["exports"][0]["edit"]["edit_id"]), 10)
        gif = client.get(f"/api/v1/video-projects/{project.project_id}", headers=headers).json()["exports"][0]["edit"]
        assert (gif["status"], gif["mime_type"]) == ("saved", "image/gif"), "a GIF of the version's video"
        bad = client.post(f"/api/v1/video-projects/{project.project_id}/export", headers=headers,
                          json={"shape": "4:3"})
        assert bad.status_code == 422

        monkeypatch.setattr(ffmpeg_runtime, "find", lambda: None)
        refused = client.put(f"/api/v1/video-projects/{project.project_id}", headers=headers,
                             json={"base": 3, "scenes": scenes})
        assert (refused.status_code, refused.json()["code"]) == (409, "video_project_needs_ffmpeg")
        assert video_projects.get(project.project_id).latest.number == 3, "nothing changes when it can't be made"

        assert client.get("/api/v1/video-projects/vid-00000000", headers=headers).status_code == 404
        assert client.get("/api/v1/video-projects/not-a-project", headers=headers).status_code == 404
        assert client.get(f"/api/v1/video-projects/{project.project_id}", params={"version": 9},
                          headers=headers).status_code == 404


def test_the_brand_kit_is_read_and_saved_through_the_api(service, studio):  # noqa: F811
    with _client(service) as client:
        _, headers = bootstrap(client)
        kit = client.get("/api/v1/video-brand", headers=headers)
        assert kit.status_code == 200, kit.text
        assert (kit.json()["colour"], kit.json()["captions"], kit.json()["logo"]) == ("#1f2937", "box", "")
        saved = client.put("/api/v1/video-brand", headers=headers, json={
            **kit.json(), "colour": "#AA3300", "outro": "row-bot.ai", "music_mood": "warm"})
        assert saved.status_code == 200, saved.text
        assert client.get("/api/v1/video-brand", headers=headers).json() == saved.json()
        assert saved.json()["colour"] == "#aa3300"
        missing = client.put("/api/v1/video-brand", headers=headers, json={**kit.json(), "logo": "chat:not-a-file"})
        assert (missing.status_code, missing.json()["code"]) == (422, "video_brand_invalid")
        assert client.put("/api/v1/video-brand", headers=headers,
                          json={**kit.json(), "colour": "red"}).status_code == 422
