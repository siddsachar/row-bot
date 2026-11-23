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


def test_the_panel_gives_lines_to_characters_their_voices_and_scenes_their_effects(service, studio):  # noqa: F811
    """C6: who says each line, each character's voice and each scene's effects, read and written by the panel;
    the brand kit's loudness with the rest of the kit."""
    from row_bot import threads, video_projects
    from row_bot.application.attachments import register_attachment

    chat = threads.create_thread("Late", seed_default_skills=False)
    register_attachment(chat, "door.mp4", _MP4)
    project = video_projects.create(chat, "Late", [
        {"source": "door.mp4", "end": 3, "line": "Who's there?", "speaker": "Maya", "cue": "rain"},
        {"card": "Later", "end": 2, "line": "Only the wind."}], music="fit")
    path = f"/api/v1/video-projects/{project.project_id}"

    with _client(service) as client:
        _, headers = bootstrap(client)
        board = client.get(path, headers=headers).json()
        assert [(scene["speaker"], scene["effects"]) for scene in board["scenes"]] == [("Maya", ["rain"]), ("", [])]
        assert [member["name"] for member in board["cast"]] == ["Maya"] and board["music"] == "fit"
        scenes = [{key: scene[key] for key in ("scene_id", "source", "start", "end", "speed", "zoom", "card", "line")}
                  for scene in board["scenes"]]
        scenes[1].update(speaker="Sam", effects=["wind", "whoosh"])
        changed = client.put(path, headers=headers, json={"base": 1, "scenes": scenes,
                                                          "cast": [{"name": "Sam", "voice": "bm_george"}]})
        assert changed.status_code == 200, changed.text
        view = changed.json()
        assert [(scene["speaker"], scene["effects"]) for scene in view["scenes"]] == [
            ("Maya", ["rain"]), ("Sam", ["wind", "whoosh"])], "a scene left as it was keeps its own"
        assert {member["name"]: member["voice"] for member in view["cast"]}["Sam"] == "bm_george"
        bad = client.put(path, headers=headers, json={"base": 2, "scenes": [dict(scenes[1], effects=["thunder"])]})
        assert (bad.status_code, bad.json()["code"]) == (422, "video_project_invalid")

        kit = client.get("/api/v1/video-brand", headers=headers).json()
        assert kit["loudness"] == -14.0
        assert client.put("/api/v1/video-brand", headers=headers, json={**kit, "loudness": -16}).json()[
            "loudness"] == -16.0
        assert client.put("/api/v1/video-brand", headers=headers, json={**kit, "loudness": -30}).status_code == 422


def test_the_panel_sets_a_videos_look_and_the_brand_kit_its_type_and_titles(service, studio):  # noqa: F811
    """C5: the panel reads a video's look as it's made (its recipe's, else its own) and changes one choice, which
    makes it again; the kit keeps its font and title style."""
    from row_bot import threads, video_projects
    from row_bot.application.attachments import register_attachment

    chat = threads.create_thread("Trip", seed_default_skills=False)
    register_attachment(chat, "beach.mp4", _MP4)
    project = video_projects.create(chat, "Trip", [{"source": "beach.mp4", "end": 3}], look={"recipe": "film"})
    video_projects.make(project, 1)
    path = f"/api/v1/video-projects/{project.project_id}"

    with _client(service) as client:
        _, headers = bootstrap(client)
        view = _made(client, headers, project.project_id)
        assert (view["recipe"], view["grade"], view["grain"], view["letterbox"], view["transitions"]) == (
            "film", "cinematic", True, True, "cut")
        scenes = [{key: scene[key] for key in ("scene_id", "source", "start", "end", "speed", "zoom", "card", "line")}
                  for scene in view["scenes"]]
        changed = client.put(path, headers=headers, json={"base": 1, "scenes": scenes, "grade": "bw",
                                                          "letterbox": False})
        assert changed.status_code == 200, changed.text
        again = _made(client, headers, project.project_id)
        assert (again["number"], again["grade"], again["grain"], again["letterbox"]) == (2, "bw", True, False)
        assert client.put(path, headers=headers, json={"base": 2, "scenes": scenes, "grade": "sepia"}
                          ).status_code == 422

        kit = client.get("/api/v1/video-brand", headers=headers).json()
        assert (kit["font"], kit["titles"]) == ("sans", "studio")
        saved = client.put("/api/v1/video-brand", headers=headers, json={**kit, "font": "serif", "titles": "minimal"})
        assert (saved.json()["font"], saved.json()["titles"]) == ("serif", "minimal")
        assert client.put("/api/v1/video-brand", headers=headers, json={**kit, "font": "comic"}).status_code == 422


def test_a_storyboard_is_approved_by_scene_its_frames_load_and_a_change_makes_nothing(service, studio):  # noqa: F811
    """C1: the panel's cards. Approving marks scenes on their version; each card's frame is its own route; a change
    to a storyboard stays a storyboard (making it is the person's call), and footage still to make can't be made."""
    import io

    from PIL import Image

    from row_bot import threads, video_projects
    from row_bot.application.attachments import register_attachment

    chat = threads.create_thread("First snow", seed_default_skills=False)
    picture = io.BytesIO()
    Image.new("RGB", (640, 360), (200, 40, 40)).save(picture, format="PNG")
    register_attachment(chat, "town.png", picture.getvalue())
    project = video_projects.create(chat, "First snow", [
        {"card": "Winter", "line": "It came early."},
        {"footage": "ai", "shows": "a fox in deep snow", "end": 4, "cue": "strings swell"},
        {"source": "town.png", "line": "Then the town woke."}], logline="A fox and a town meet winter.")
    path = f"/api/v1/video-projects/{project.project_id}"

    with _client(service) as client:
        _, headers = bootstrap(client)
        board = client.get(path, headers=headers).json()
        assert (board["status"], board["logline"]) == ("draft", "A fox and a town meet winter.")
        assert [(scene["footage"], scene["shows"], scene["cue"], scene["has_frame"]) for scene in board["scenes"]] == [
            ("card", "", "", False), ("ai", "a fox in deep snow", "strings swell", False), ("picture", "", "", True)]
        card, fox, town = (scene["scene_id"] for scene in board["scenes"])

        frame = client.get(f"{path}/scenes/{town}/frame", headers=headers)
        assert (frame.status_code, frame.headers["content-type"]) == (200, "image/jpeg")
        assert Image.open(io.BytesIO(frame.content)).getpixel((5, 5))[0] > 150
        assert client.get(f"{path}/scenes/{card}/frame", headers=headers).status_code == 404, "a card draws itself"
        assert client.get(f"{path}/scenes/not-a-scene/frame", headers=headers).status_code == 404

        approved = client.post(f"{path}/approve", headers=headers,
                               json={"base": 1, "scene_ids": [card, town], "approved": True})
        assert approved.status_code == 200, approved.text
        assert [scene["approved"] for scene in approved.json()["scenes"]] == [True, False, True]
        assert approved.json()["latest"] == 1, "approving is no new version"
        stale = client.post(f"{path}/approve", headers=headers, json={"base": 2, "scene_ids": [fox], "approved": True})
        assert (stale.status_code, stale.json()["code"]) == (409, "video_project_stale")

        scenes = [{key: scene[key] for key in ("scene_id", "source", "start", "end", "speed", "zoom", "card", "line")}
                  for scene in board["scenes"]]
        scenes[0]["line"] = "Winter came early."
        changed = client.put(path, headers=headers, json={"base": 1, "scenes": scenes})
        assert changed.status_code == 200, changed.text
        assert (changed.json()["number"], changed.json()["status"]) == (2, "draft"), "still a storyboard"
        assert [scene["approved"] for scene in changed.json()["scenes"]] == [False, False, True]
        assert changed.json()["scenes"][1]["shows"] == "a fox in deep snow", "the panel's change keeps the plan"
        assert studio == [], "nothing voiced"

        refused = client.post(f"{path}/make", headers=headers)
        assert (refused.status_code, refused.json()["code"]) == (422, "video_project_invalid"), \
            "the AI shot is still to make: Row-Bot makes it"
