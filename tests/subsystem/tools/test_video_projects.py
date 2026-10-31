"""Video projects (5.3.1 B3): a video as scenes, versioned, shared by the Video panel and Row-Bot.

Real attachment storage in an isolated data folder; the voice, ffmpeg and the edit job are fakes,
so nothing is spoken, encoded or downloaded.
"""
from __future__ import annotations

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64


def _wav(seconds: float) -> bytes:
    """A silent WAV this long: the length a scene's voice line is measured by."""
    import io
    import wave

    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * int(8000 * seconds))
    return out.getvalue()


@pytest.fixture
def studio(platform, monkeypatch):  # noqa: F811
    """A chat holding two clips; speaking returns a tiny voice file; edits are captured, not run."""
    from row_bot import sound, video_edits
    from row_bot.application.attachments import register_attachment

    for name in ("signup.mp4", "pricing.mp4"):
        register_attachment("conversation-a", name, MP4)
    said: list[str] = []
    started: list = []

    def speak(text, **_options):
        said.append(text)
        return sound.Speech(wav=_wav(1.5), seconds=1.5, voice="test-voice")

    def start(request):
        started.append(request)
        edit = video_edits.VideoEdit(edit_id=f"edit-{len(started):08x}", conversation_id=request.conversation_id,
                                     kind="video", summary="", output_name=request.output_name, started_at=1.0,
                                     status="editing")
        video_edits._ensure_loaded()
        video_edits._RECORDS[edit.edit_id] = edit
        return edit

    monkeypatch.setattr(sound, "speak", speak)
    monkeypatch.setattr("row_bot.tts.TTSService.is_installed", lambda self: True)  # Row-Bot's voice is here.
    monkeypatch.setattr(video_edits, "start", start)
    return {"said": said, "started": started}


def _wait(project_id: str, number: int):
    import threading

    from row_bot import video_projects

    for _ in range(300):
        version = video_projects.get(project_id).version(number)
        if version.edit_id or version.status == "failed":
            return version
        threading.Event().wait(0.01)
    raise AssertionError("the video was never started")


SCENES = [
    {"source": "signup.mp4", "start": 1, "end": 4, "line": "First, sign up.", "card": "Sign up"},
    {"source": "pricing.mp4", "speed": 2, "zoom": [0.5, 0, 0.5, 0.5], "line": "Then pick a plan."},
]


def test_every_change_is_a_new_version_that_keeps_voices_whose_lines_stayed_and_going_back_is_one_more(studio):
    from row_bot import video_projects

    project = video_projects.create("conversation-a", "Launch demo", SCENES, music="", note="First cut")
    first = project.latest
    assert (first.number, first.by, first.note, len(first.scenes)) == (1, "row-bot", "First cut", 2)
    video_projects.make(project, 1)
    made = _wait(project.project_id, 1)
    assert studio["said"] == ["First, sign up.", "Then pick a plan."], "each line is voiced in the person's voice"
    assert all(scene.voice and scene.voiced == scene.line for scene in made.scenes)

    scenes = [dict(SCENES[0], scene_id=made.scenes[0].scene_id),
              dict(SCENES[1], scene_id=made.scenes[1].scene_id, line="Then pick the plan that fits.")]
    second = video_projects.change(project, by="you", base=1, scenes=scenes, note="Edited in the panel")
    assert (second.number, second.by) == (2, "you")
    assert video_projects.changed_scenes(project, second) == [made.scenes[1].scene_id]
    assert second.scenes[0].voice == made.scenes[0].voice, "an unchanged line keeps its voice"
    assert [scene.line for scene in video_projects.needs_voice(second)] == ["Then pick the plan that fits."]

    with pytest.raises(video_projects.ProjectError) as refused:
        video_projects.change(project, by="you", base=1, scenes=scenes)
    assert refused.value.stale, "a change made on an older version never overwrites a newer one"

    back = video_projects.restore(project, 1, by="you")
    assert (back.number, back.note, back.edit_id) == (3, "Back to version 1", made.edit_id), "its video is reused"
    assert [item.number for item in video_projects.get(project.project_id).versions] == [1, 2, 3]


def test_a_change_saved_while_the_panel_reads_the_project_waits_for_the_read_and_is_kept(studio, monkeypatch):
    """On Windows a file open for reading refuses a save's replace (seen on CI): the save waits, then lands."""
    import pathlib
    import threading

    from row_bot import video_projects

    project = video_projects.create("conversation-a", "Launch demo", SCENES, note="First cut")
    reading, release = threading.Event(), threading.Event()
    read_text = pathlib.Path.read_text

    def slow_read(self, *args, **kwargs):
        if self.name == f"{project.project_id}.json" and threading.current_thread() is reader:
            with open(self, encoding="utf-8"):  # The file is open, as a reader holds it.
                reading.set()
                release.wait(5)
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", slow_read)
    reader = threading.Thread(target=video_projects.get, args=(project.project_id,))
    reader.start()
    assert reading.wait(5)
    saved = threading.Event()
    errors: list[BaseException] = []

    def save():
        try:
            video_projects.change(project, by="you", base=1, title="Launch demo, shorter")
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)
        saved.set()

    writer = threading.Thread(target=save)
    writer.start()
    assert not saved.wait(0.2), "the save waits while the project is being read"
    release.set()
    reader.join(5)
    writer.join(5)
    assert errors == []
    assert video_projects.get(project.project_id).latest.number == 2


def test_making_a_version_renders_its_scenes_in_one_edit_with_its_voices_music_and_checks(studio):
    from row_bot import video_projects
    from row_bot.application.attachments import register_attachment

    register_attachment("conversation-a", "music-calm.wav", b"RIFF\x00\x00\x00\x00WAVE" + b"0" * 32)
    project = video_projects.create("conversation-a", "Launch demo", SCENES, music="music-calm.wav",
                                    captions=True)
    video_projects.make(project, 1)
    version = _wait(project.project_id, 1)

    request = studio["started"][0]
    assert [media.label for media in request.sources] == ["signup.mp4", "pricing.mp4"]
    scenes, music = request.steps
    assert scenes.op == "scenes" and scenes.style == "caption"
    assert [(cut.input, cut.start, cut.end, cut.speed, cut.card) for cut in scenes.scenes] == [
        (0, 1.0, 4.0, 1.0, "Sign up"), (1, 0.0, None, 2.0, "")]
    assert scenes.scenes[1].zoom == (0.5, 0.0, 0.5, 0.5)
    assert (music.op, music.role, music.source, music.volume) == ("sound", "music", "music-calm.wav", 0.3)
    assert set(request.sounds) == {cut.voice for cut in scenes.scenes} | {"music-calm.wav"}
    assert [cut.voice_seconds for cut in scenes.scenes] == [1.5, 1.5], "each scene lasts at least its line"
    assert request.check, "the review card's checks are read from the finished video"
    view = video_projects.view(video_projects.get(project.project_id))
    assert (view["status"], view["edit"]["edit_id"]) == ("rendering", version.edit_id)


def test_each_scene_says_its_own_line_when_the_title_has_a_version_number_and_runs_long(studio):
    """A title like "Row-Bot 5.3.1 beta | …" once named every scene's voice "Row-Bot 5.3.wav", so each scene
    played the last one's line."""
    from row_bot import video_projects

    title = "Row-Bot 5.3.1 beta | Linked conversations, from meeting notes to a checklist in a sandbox"
    project = video_projects.create("conversation-a", title, SCENES)
    video_projects.make(project, 1)
    _wait(project.project_id, 1)

    request = studio["started"][0]
    voices = [cut.voice for cut in request.steps[0].scenes]
    assert len(set(voices)) == 2 and all(voice.startswith("Row-Bot 5.3.1 beta") for voice in voices)
    assert set(request.sounds) == set(voices)
    assert request.output_name.startswith("Row-Bot 5.3.1 beta") and request.output_name.endswith(" v1.mp4")


def test_narration_said_over_the_whole_video_is_part_of_the_project_and_its_lines_are_not_said_again(studio):
    """The Release kit dubbed a whole-video voice onto a picture-only version outside the project, so the Video
    panel showed a silent video. Narration is the project's own now."""
    from row_bot import video_projects
    from row_bot.application.attachments import register_attachment

    register_attachment("conversation-a", "launch-script.wav", b"RIFF\x00\x00\x00\x00WAVE" + b"0" * 32)
    project = video_projects.create("conversation-a", "Launch demo", SCENES, narration="launch-script.wav")
    video_projects.make(project, 1)
    _wait(project.project_id, 1)

    request = studio["started"][0]
    scenes, narration = request.steps
    assert studio["said"] == [] and all(not cut.voice for cut in scenes.scenes), "no line is said one by one"
    assert (narration.op, narration.role, narration.source, narration.start) == (
        "sound", "voice", "launch-script.wav", 0.0)
    assert set(request.sounds) == {"launch-script.wav"}
    assert video_projects.view(video_projects.get(project.project_id))["narration"] == "launch-script.wav"
    assert "Narration: launch-script.wav" in video_projects.summary(video_projects.get(project.project_id))

    back = video_projects.change(video_projects.get(project.project_id), by="you", narration="")
    video_projects.make(video_projects.get(project.project_id), back.number)
    _wait(project.project_id, back.number)
    assert studio["said"] == ["First, sign up.", "Then pick a plan."], "without it, each scene says its line"


def test_a_title_card_scene_needs_no_video_and_a_video_of_cards_alone_needs_none_at_all(studio):
    """5.3.1 beta F17: a 3-second title card couldn't be made without a source clip, so the model made background
    videos with ffmpeg in the person's workspace."""
    from row_bot import video_projects

    project = video_projects.create("conversation-a", "Launch demo", [
        {"card": "Row-Bot 5.3", "line": "Meet Row-Bot.", "speed": 2, "zoom": [0, 0, 0.5, 0.5]},
        {"source": "signup.mp4", "start": 1, "end": 4},
        {"card": "row-bot.ai", "end": 2}])
    title, _clip, end = project.latest.scenes
    assert (title.source, title.start, title.end, title.speed, title.zoom) == ("", 0.0, 3.0, 1.0, None)
    assert (end.start, end.end) == (0.0, 2.0)
    view = video_projects.view(project)
    assert view["scenes"][0]["source_ref"] is None, "the preview shows it as a still card"
    assert "1. [" in (text := video_projects.summary(project)) and "title card 0–3 s" in text

    video_projects.make(project, 1)
    _wait(project.project_id, 1)
    request = studio["started"][0]
    assert len(request.sources) == 1, "only the clip is an input"
    cuts = request.steps[0].scenes
    assert [cut.input for cut in cuts] == [None, 0, None] and cuts[0].voice, "the card still says its line"

    cards = video_projects.create("conversation-a", "Teaser", [{"card": "Coming soon"}])
    video_projects.make(cards, 1)
    _wait(cards.project_id, 1)
    assert studio["started"][1].sources == [], "a video of cards alone has no input"


def test_a_made_version_exports_in_another_shape_and_as_a_gif(studio):
    from row_bot import video_edits, video_projects
    from row_bot.application.attachments import register_attachment

    project = video_projects.create("conversation-a", "Launch demo", SCENES)
    with pytest.raises(video_projects.ProjectError, match="Make this version first"):
        video_projects.export(project, "9:16")
    video_projects.make(project, 1)
    made = video_edits.get(_wait(project.project_id, 1).edit_id)
    saved = register_attachment("conversation-a", "Launch demo v1.mp4", MP4)
    video_edits._set(made, status="saved", media_ref=saved["attachment_ref"], mime_type="video/mp4")

    video_projects.export(project, "9:16")
    for _ in range(300):
        if "9:16" in video_projects.get(project.project_id).latest.exports:
            break
        __import__("threading").Event().wait(0.01)
    tall = studio["started"][-1]
    assert tall.steps[0].aspect == "9:16" and tall.output_name == "Launch demo v1 9x16.mp4"
    video_projects.export(project, "gif")
    gif = studio["started"][-1]
    assert gif.gif and [media.ref for media in gif.sources] == [saved["attachment_ref"]]
    exports = video_projects.view(video_projects.get(project.project_id))["exports"]
    assert [item["shape"] for item in exports] == ["9:16", "gif"]
    assert video_projects.get(project.project_id).latest.edit_id == made.edit_id, "the version's own video stays"
    with pytest.raises(video_projects.ProjectError, match="Export as"):
        video_projects.export(project, "4:3")


def test_the_brand_kit_is_used_for_every_video_unless_a_version_turns_it_off(studio):
    import io

    from PIL import Image

    from row_bot import video_brand, video_projects
    from row_bot.application.attachments import register_attachment

    assert video_brand.load() == video_brand.Brand(), "a plain default until it's saved"
    picture = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 0, 0)).save(picture, format="PNG")
    logo = register_attachment("conversation-a", "logo.png", picture.getvalue())["attachment_ref"]
    clip = next(item["ref"] for item in __import__("row_bot.media_library", fromlist=["items"]).items()
                if item["kind"] == "video")
    for wrong, words in (({"colour": "red"}, "hex colour"), ({"captions": "huge"}, "Captions are"),
                         ({"logo": clip}, "a picture from Library")):
        with pytest.raises(video_brand.BrandError, match=words):
            video_brand.save(wrong)
    saved = video_brand.save({"colour": "#112233", "captions": "bold", "logo": logo, "intro": "  Row-Bot  ",
                              "outro": "row-bot.ai", "music_mood": "upbeat"})
    assert video_brand.load() == saved and saved.intro == "Row-Bot"

    project = video_projects.create("conversation-a", "Launch demo", SCENES)
    video_projects.make(project, 1)
    _wait(project.project_id, 1)
    look = studio["started"][-1].steps[0].look
    assert (look.colour, look.captions, look.intro, look.outro) == ((17, 34, 51), "bold", "Row-Bot", "row-bot.ai")
    assert look.logo.startswith(b"\x89PNG"), "the logo, ready to lay over the video"

    plain = video_projects.change(project, by="you", brand=False, note="Without the brand")
    video_projects.make(project, plain.number)
    _wait(project.project_id, plain.number)
    assert studio["started"][-1].steps[0].look is None
    assert video_projects.view(video_projects.get(project.project_id))["brand"] is False


def test_a_recordings_beats_become_its_scenes_live_as_it_is_work_sped_up_and_cuts_left_out(studio):
    import time

    from row_bot import recordings, video_projects
    from row_bot.application.attachments import register_attachment

    saved = register_attachment("conversation-a", "generated-recording.mp4", MP4)
    record = recordings.Recording(recording_id="rec-0a0b0c0d", conversation_id="conversation-a", kind="browser",
                                  started_at=time.time() - 30, max_seconds=300, status="saved",
                                  media_ref=saved["attachment_ref"], mime_type="video/mp4",
                                  beats=[[4.0, "work", "Waiting for the build"],
                                         [16.5, "live", "Your shop's orders", "Here are this morning's orders."],
                                         [22.0, "cut", "", ""], [25.0, "live", "", ""]])
    recordings._ensure_loaded()
    recordings._RECORDS[record.recording_id] = record

    scenes = video_projects.scenes_from_beats("rec-0a0b0c0d", "conversation-a")
    assert [(scene["start"], scene["end"], scene["speed"]) for scene in scenes] == [
        (0.0, 4.0, 1.0), (4.0, 16.5, 4.0), (16.5, 22.0, 1.0), (25.0, None, 1.0)]
    assert [(scene["card"], scene["line"]) for scene in scenes[1:3]] == [
        ("Waiting for the build", ""), ("Your shop's orders", "Here are this morning's orders.")], \
        "each beat's note is its scene's title and its line is said over it (a beat from before 5.3.2 has none)"
    project = video_projects.create("conversation-a", "Sign-up walkthrough", scenes)
    assert [scene.source for scene in project.latest.scenes] == ["rec-0a0b0c0d"] * 4
    with pytest.raises(video_projects.ProjectError, match="isn't a recording in this chat"):
        video_projects.scenes_from_beats("rec-0a0b0c0d", "conversation-b")


def test_the_live_preview_gets_each_scenes_video_and_its_voice_while_its_line_is_unchanged(studio):
    from row_bot import video_projects
    from row_bot.application.attachments import list_conversation_files

    project = video_projects.create("conversation-a", "Launch demo", SCENES)
    files = {item["name"]: item["attachment_ref"] for item in list_conversation_files("conversation-a")}
    before = video_projects.view(video_projects.get(project.project_id))["scenes"]
    assert [scene["source_ref"] for scene in before] == [files["signup.mp4"], files["pricing.mp4"]]
    assert [scene["voice_ref"] for scene in before] == [None, None], "nothing voiced yet"

    video_projects.make(project, 1)
    made = _wait(project.project_id, 1)
    voices = {item["name"]: item["attachment_ref"] for item in list_conversation_files("conversation-a")}
    after = video_projects.view(video_projects.get(project.project_id))["scenes"]
    assert [scene["voice_ref"] for scene in after] == [voices[scene.voice] for scene in made.scenes]

    edited = [dict(SCENES[0], scene_id=made.scenes[0].scene_id, line="A new first line."),
              dict(SCENES[1], scene_id=made.scenes[1].scene_id)]
    changed = video_projects.change(project, by="you", scenes=edited)
    scenes = video_projects.view(video_projects.get(project.project_id), changed.number)["scenes"]
    assert scenes[0]["voice_ref"] is None and scenes[1]["voice_ref"] == after[1]["voice_ref"]


def test_scenes_that_cannot_be_used_are_refused_plainly(studio):
    from row_bot import video_projects

    for scenes, words in (([], "at least one scene"), ([{"source": "missing.mp4"}], "Scene 1"),
                          ([{"source": "signup.mp4", "speed": 9}], "speed is 0.25 to 4"),
                          ([{"source": "signup.mp4", "start": 5, "end": 4}], "ends before it starts"),
                          ([{"line": "Hello."}], "or give it a card")):
        with pytest.raises(video_projects.ProjectError, match=words):
            video_projects.create("conversation-a", "Demo", scenes)


def test_the_video_tool_makes_a_project_and_its_step_shows_the_project_card(studio, monkeypatch):
    from row_bot import ffmpeg_runtime
    from row_bot.application.conversation_traces import _public_specialization, specialize_tool_result
    from row_bot.tools import video_project_tool

    monkeypatch.setattr(video_project_tool, "_conversation", lambda: "conversation-a")
    monkeypatch.setattr(video_project_tool, "INLINE_SECONDS", 0.5)
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: object())

    result = video_project_tool._video(action="create", title="Launch demo", scenes=SCENES, note="First cut")
    first = result.splitlines()[0]
    assert first.startswith("Video project vid-") and '"Launch demo", version 1 of 1, 2 scenes' in first
    assert '1. [s' in result and 'signup.mp4 1–4 s, card "Sign up" — "First, sign up."' in result
    project_id = first.split()[2].rstrip(":")
    public = _public_specialization(specialize_tool_result({"name": "video", "content": result}))
    assert (public["kind"], public["project_id"]) == ("video_project", project_id)

    assert "no earlier version" in video_project_tool._video(action="undo", project=project_id)
    assert "isn't a video project in this chat" in video_project_tool._video(action="status", project="vid-00000000")


def test_a_scene_follows_where_its_recordings_pointer_was(studio):
    """V3: a square or tall cut of a scene without a zoom is centred on the pointer, so what's used stays in frame."""
    import time

    from row_bot import recordings, video_projects

    record = recordings.Recording(recording_id="rec-0f0e0d0c", conversation_id="conversation-a", kind="browser",
                                  started_at=time.time() - 30, max_seconds=300, status="saved",
                                  trail=[[1.0, 0.2, 0.3], [2.0, 0.25, 0.35], [3.0, 0.3, 0.3],
                                         [11.0, 0.9, 0.1], [12.0, 0.95, 0.05]])
    recordings._ensure_loaded()
    recordings._RECORDS[record.recording_id] = record

    assert video_projects.pointer_focus("rec-0f0e0d0c", 0.0, 5.0) == (0.25, 0.3)
    assert video_projects.pointer_focus("rec-0f0e0d0c", 10.0, None) == (0.925, 0.075)
    assert video_projects.pointer_focus("rec-0f0e0d0c", 5.0, 9.0) is None, "no moves then: the middle"
    assert video_projects.pointer_focus("clip.mp4", 0.0, 5.0) is None, "a file has no pointer"


def test_making_a_narrated_video_without_rowbots_voice_asks_for_it_first(studio, monkeypatch):
    from row_bot import ffmpeg_runtime, video_projects
    from row_bot.tools import approval_gate, video_project_tool

    monkeypatch.setattr(video_project_tool, "_conversation", lambda: "conversation-a")
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: object())
    monkeypatch.setattr("row_bot.tts.TTSService.is_installed", lambda self: False)
    asked: list[str] = []
    monkeypatch.setattr(approval_gate, "approval_check", lambda payload, **_: asked.append(payload["question"]) or (
        "The person didn't allow downloading Row-Bot's voice; nothing was downloaded.", ""))
    project = video_projects.create("conversation-a", "Launch demo", SCENES)

    said = video_project_tool._video(action="make", project=project.project_id)

    assert said.startswith("The person didn't allow downloading Row-Bot's voice")
    assert asked == ["Narrating this video needs Row-Bot's voice (Kokoro, about 206 MB) from GitHub. Download it now?"]
    assert video_projects.get(project.project_id, "conversation-a").latest.status != "rendering", "nothing made"
