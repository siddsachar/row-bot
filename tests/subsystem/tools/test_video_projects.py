"""Video projects (5.3.1 B3): a video as scenes, versioned, shared by the Video panel and Row-Bot.

Real attachment storage in an isolated data folder; the voice, ffmpeg and the edit job are fakes,
so nothing is spoken, encoded or downloaded.
"""
from __future__ import annotations

import threading

import pytest

from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401
from tests.subsystem.agents.test_linked_conversations import linked, settle  # noqa: F401

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
    voices: list[dict] = []
    started: list = []

    def speak(text, **options):
        said.append(text)
        voices.append(options)
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
    yield {"said": said, "voices": voices, "started": started}
    # A make runs on its own thread; finish it here, so it never writes into the next test's fresh databases.
    for thread in threading.enumerate():
        if thread.name.startswith("row-bot-vid-"):
            thread.join(timeout=10)


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
    render = studio["started"][0]  # Its render is a draft, version 1 of the project; its voice lines are working.
    assert (render.role, render.output_id, render.version) == ("draft", project.project_id, 1)
    from row_bot.application.attachments import list_conversation_files

    assert {item["role"] for item in list_conversation_files("conversation-a")
            if item["name"].endswith(".wav")} == {"working"}
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


def test_the_finished_check_knows_what_a_recording_did_and_which_character_a_shot_shows(studio):
    """C8: words over a clicked button and AI shots of one character drifting apart are read from what each scene's
    footage is: its recording's action log, and its file's character or style tag in Library › Media."""
    import time

    from row_bot import media_library, recordings, video_projects
    from row_bot.application.attachments import register_attachment
    from row_bot.video_check import Footage

    saved = register_attachment("conversation-a", "generated-recording.mp4", MP4)
    record = recordings.Recording(recording_id="rec-0c8c8c8c", conversation_id="conversation-a", kind="browser",
                                  started_at=time.time() - 60, max_seconds=300, status="saved",
                                  media_ref=saved["attachment_ref"], mime_type="video/mp4",
                                  actions=[[1.0, 2.0, "click", 0.4, 0.9, 0.1, 0.05, "button \"Send\""]])
    recordings._ensure_loaded()
    recordings._RECORDS[record.recording_id] = record
    shot = register_attachment("conversation-a", "maya-at-the-door.mp4", MP4)
    media_library.set_tags(shot["attachment_ref"], ["character: maya", "draft"])
    project = video_projects.create("conversation-a", "Maya", [
        {"source": "rec-0c8c8c8c", "end": 3, "line": "Send it."}, {"card": "Later", "end": 2},
        {"source": "maya-at-the-door.mp4", "line": "Maya comes home."}])
    video_projects.make(project, 1)
    _wait(project.project_id, 1)

    assert studio["started"][0].footage == [
        Footage(actions=((1.0, 2.0, "click", 0.4, 0.9, 0.1, 0.05, "button \"Send\""),)), Footage(),
        Footage(look="character: maya")]


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
    # The version is no part of the name (W4): a title that has one is never "… v2 v1.mp4".
    assert request.output_name == "Row-Bot 5.3.1 beta - Linked conversations, from meeting note.mp4"


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


def test_a_title_with_its_own_version_number_names_every_version_once(studio):
    """W4: "Living Buddy v2" was saved as "Living Buddy v2 v1.mp4"; a version now lives in the file's record."""
    from row_bot import media_sources, video_projects
    from row_bot.application.attachments import register_attachment

    project = video_projects.create("conversation-a", "Living Buddy v2", SCENES)
    video_projects.make(project, 1)
    _wait(project.project_id, 1)
    video_projects.change(project, by="you", base=1, scenes=[SCENES[0]], note="Shorter")
    video_projects.make(video_projects.get(project.project_id), 2)
    _wait(project.project_id, 2)

    renders = studio["started"]
    saved = [register_attachment("conversation-a", request.output_name, MP4 + bytes([request.version]),
                                 role=request.role, output_id=request.output_id, version=request.version)
             for request in renders]

    assert [request.output_name for request in renders] == ["Living Buddy v2.mp4"] * 2
    assert [item["name"] for item in saved] == ["Living Buddy v2.mp4"] * 2
    [older] = media_sources.find("Living Buddy v2@v1", "conversation-a")
    [newest] = media_sources.find("Living Buddy v2", "conversation-a")
    assert (older.ref, newest.ref) == (saved[0]["attachment_ref"], saved[1]["attachment_ref"])


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
    assert tall.steps[0].aspect == "9:16" and tall.output_name == "Launch demo 9x16.mp4"
    video_projects.export(project, "gif")
    gif = studio["started"][-1]
    assert gif.gif and [media.ref for media in gif.sources] == [saved["attachment_ref"]]
    exports = video_projects.view(video_projects.get(project.project_id))["exports"]
    assert [item["shape"] for item in exports] == ["9:16", "gif"]
    assert video_projects.get(project.project_id).latest.edit_id == made.edit_id, "the version's own video stays"
    with pytest.raises(video_projects.ProjectError, match="Export as"):
        video_projects.export(project, "4:3")


def test_an_export_that_cannot_be_made_says_why_in_the_videos_summary(studio, monkeypatch):
    """R5 (12 Nov): a 9:16 export twice made no file and said nothing; the summary never listed exports."""
    import threading

    from row_bot import media_sources, video_edits, video_projects
    from row_bot.application.attachments import register_attachment

    project = video_projects.create("conversation-a", "Launch demo", SCENES)
    video_projects.make(project, 1)
    made = video_edits.get(_wait(project.project_id, 1).edit_id)
    saved = register_attachment("conversation-a", "Launch demo v1.mp4", MP4)
    video_edits._set(made, status="saved", media_ref=saved["attachment_ref"], mime_type="video/mp4")

    def gone(value, conversation, **_options):
        raise media_sources.SourceError(f"{value} isn't in this chat any more.")

    found = media_sources.resolve
    monkeypatch.setattr(media_sources, "resolve", gone)
    video_projects.export(project, "9:16")
    for _ in range(300):
        if "9:16" in video_projects.get(project.project_id).latest.export_problems:
            break
        threading.Event().wait(0.01)
    summary = video_projects.summary(video_projects.get(project.project_id))
    assert "Export 9:16: not made: Footage missing: scene 1 (signup.mp4), scene 2 (pricing.mp4)" in summary
    assert video_projects.get(project.project_id).latest.edit_id == made.edit_id, "the version's own video stays"

    monkeypatch.setattr(media_sources, "resolve", found)
    video_projects.export(project, "9:16")
    for _ in range(300):
        if "9:16" in video_projects.get(project.project_id).latest.exports:
            break
        threading.Event().wait(0.01)
    summary = video_projects.summary(video_projects.get(project.project_id))
    assert "not made" not in summary and "Export 9:16:" in summary, "trying again clears the old reason"


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


def test_how_recordings_show_and_who_says_the_lines_are_the_persons_choice(studio):
    """5.3.2: nothing about a video's look or voice is fixed: the brand kit sets it for every video, and a video
    may choose its own voice; choosing another voice says its lines again in it."""
    from row_bot import video_brand, video_projects

    for wrong, words in (({"framing": "tilted"}, "Footage is"), ({"camera": "shaky"}, "The camera is"),
                         ({"voice": "nobody"}, "no voice called"), ({"voice_speed": 3}, "speed is")):
        with pytest.raises(video_brand.BrandError, match=words):
            video_brand.save(wrong)
    video_brand.save({"framing": "full", "camera": "still", "voice": "bm_george", "voice_speed": 0.9})

    project = video_projects.create("conversation-a", "Launch demo", SCENES)
    video_projects.make(project, 1)
    _wait(project.project_id, 1)
    look = studio["started"][-1].steps[0].look
    assert (look.framing, look.camera) == ("full", "still")
    assert studio["voices"] and all(options == {"voice": "bm_george", "speed": 0.9} for options in studio["voices"])

    studio["voices"].clear()
    own = video_projects.change(project, by="you", voice="af_heart", note="Another voice")
    video_projects.make(project, own.number)
    _wait(project.project_id, own.number)
    assert len(studio["voices"]) == len(SCENES) and studio["voices"][0] == {"voice": "af_heart", "speed": 0.9}, \
        "every line said again in the video's own voice, at the kit's speed"
    view = video_projects.view(video_projects.get(project.project_id))
    assert (view["voice"], view["voice_speed"]) == ("af_heart", 0.0)
    studio["voices"].clear()
    again = video_projects.change(project, by="you", note="Same voice")
    video_projects.make(project, again.number)
    _wait(project.project_id, again.number)
    assert studio["voices"] == [], "lines already said in that voice aren't said again"


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
    monkeypatch.setattr(video_project_tool, "MAKE_SECONDS", 0.0)
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


def test_a_made_video_comes_with_a_review_sheet_the_checks_already_looked_at(studio):
    """5.3.2: Row-Bot checks what it made, as a viewer sees it, before it says the video is finished; since C8
    the check looks at the sheet once itself, and the agent no longer runs its own vision passes."""
    from row_bot import video_edits, video_projects

    assert video_edits.review_times([0.0, 5.0, 6.0]) == [(1, 1.0), (1, 4.4), (2, 5.5)], \
        "a second into each scene and near its end; a short scene once"
    assert len(video_edits.review_times([float(at) for at in range(0, 200, 5)])) == video_edits.REVIEW_TILES
    project = video_projects.create("conversation-a", "Launch demo", SCENES)
    video_projects.make(project, 1)
    made = video_edits.get(_wait(project.project_id, 1).edit_id)
    assert "Review sheet" not in video_projects.summary(video_projects.get(project.project_id)), "not before it's made"
    video_edits._set(made, status="saved", sheet="Launch demo v1 review.jpg")

    words = video_projects.summary(video_projects.get(project.project_id))
    assert "Review sheet: Launch demo v1 review.jpg" in words
    # C8: the checks already include one look at the sheet; Row-Bot fixes what they list, it doesn't look again.
    assert "don't look at it again with analyze_image" in words and "Look at it with analyze_image" not in words
    assert ("If a problem is still listed after two makes, stop, tell the person what's left, and ask whether to "
            "present it as it is.") in words
    assert video_projects.view(video_projects.get(project.project_id))["review_sheet"] == "Launch demo v1 review.jpg"


def test_a_narrated_video_needing_ffmpeg_and_the_voice_asks_once_for_both(studio, monkeypatch):
    """U25: one card before making it, naming both downloads, not two in a row."""
    from row_bot import ffmpeg_runtime, video_projects
    from row_bot.tools import approval_gate, video_project_tool

    monkeypatch.setattr(video_project_tool, "_conversation", lambda: "conversation-a")
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: None)
    wheel = ffmpeg_runtime.WHEELS["windows-x86_64"]
    monkeypatch.setattr(ffmpeg_runtime, "current_wheel", lambda: wheel)
    monkeypatch.setattr("row_bot.tts.TTSService.is_installed", lambda self: False)
    asked: list[str] = []
    monkeypatch.setattr(approval_gate, "approval_check", lambda payload, **_: asked.append(payload["question"]) or (
        "The person didn't allow the downloads; nothing was downloaded or made.", ""))
    project = video_projects.create("conversation-a", "Launch demo", SCENES)

    said = video_project_tool._video(action="make", project=project.project_id)

    assert said.startswith("The person didn't allow the downloads")
    assert asked == [f"Making this video needs ffmpeg (about {ffmpeg_runtime.size_words(wheel.size)}) and Row-Bot's "
                     "voice (Kokoro, about 206 MB). Download both now?"]
    assert video_projects.get(project.project_id, "conversation-a").latest.status != "rendering", "nothing made"


def test_a_wait_in_a_recorded_scene_needs_attention_once_its_line_is_said(studio):
    """Run 10: a "Thinking… 26s" shot survived into a finished demo; a spinner or a ticking timer moves, so the
    still check missed it. What the recording logged (clicks, typing, scrolls) shows where nothing was done: a
    wait of 8 s or more on the video after the scene's line needs attention; one sped up below that doesn't."""
    import time

    from row_bot import recordings, video_projects
    from row_bot.application.attachments import register_attachment
    from row_bot.video_editing import InputVideo, SceneCut, Step, VideoInfo, build_plan

    saved = register_attachment("conversation-a", "generated-recording.mp4", MP4)
    record = recordings.Recording(recording_id="rec-1a2b3c4d", conversation_id="conversation-a", kind="browser",
                                  started_at=time.time() - 60, max_seconds=300, status="saved",
                                  media_ref=saved["attachment_ref"], mime_type="video/mp4",
                                  actions=[[1.0, 2.0, "click"], [3.0, 6.0, "type"], [40.0, 41.0, "scroll"]])
    record.duration_seconds = 50.0
    recordings._ensure_loaded()
    recordings._RECORDS[record.recording_id] = record

    waits = video_projects.scene_waits("rec-1a2b3c4d", 0.0, 50.0)
    assert waits == ((0.0, 1.0), (2.0, 3.0), (6.0, 40.0), (41.0, 50.0))

    recording = [InputVideo("input-1.mp4", VideoInfo(50.0, 640, 360, 30.0, False))]

    def review(speed: float) -> list[str]:
        scene = SceneCut(input=0, start=0.0, end=50.0, speed=speed, line="Row-Bot reads the brief.",
                         voice="line-1.wav", voice_seconds=5.0, waits=waits)
        return build_plan(recording, [Step(op="scenes", scenes=[scene])], "output.mp4",
                          sounds={"line-1.wav": "sound-1.wav"}).review

    assert [line for line in review(1.0) if line.startswith("Waiting")] == [
        "Waiting: 0:06–0:40 shows nothing being done. Speed that part up or cut it.",
        "Waiting: 0:41–0:50 shows nothing being done. Speed that part up or cut it."]
    assert not [line for line in review(8.0) if line.startswith("Waiting")], "sped up, the waits pass quickly"


def test_make_returns_the_finished_video_when_it_is_made_within_the_wait(studio, monkeypatch):
    """92 status calls in one chat (22 Nov): make waits for the video instead of being polled."""
    from row_bot import ffmpeg_runtime, video_edits, waits
    from row_bot.tools import video_project_tool

    monkeypatch.setattr(video_project_tool, "_conversation", lambda: "conversation-a")
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: object())
    capture = video_edits.start

    def made(request):  # The renderer: done as soon as it starts.
        edit = capture(request)
        video_edits._set(edit, status="saved", ended_at=2.0)
        edit.done.set()
        return edit

    monkeypatch.setattr(video_edits, "start", made)

    result = video_project_tool._video(action="create", title="Launch demo", scenes=SCENES)

    assert result.splitlines()[0].endswith(
        ': Output "Launch demo" v1 (draft) · Launch demo.mp4; version 1 of 1, 2 scenes, made.')
    assert "checks back" not in result and waits.waiting("conversation-a") == []


def test_a_video_that_takes_longer_checks_back_by_itself_when_it_is_made(studio, linked, platform, monkeypatch):  # noqa: F811
    from row_bot import ffmpeg_runtime, video_edits, waits
    from row_bot.tools import video_project_tool
    from tests.subsystem.workflows.test_workflow_runs_as_chat import Recording, reply

    fake = Recording(reply("The video is made: here it is."))
    platform.stream_factory = fake.stream
    monkeypatch.setattr(video_project_tool, "_conversation", lambda: "conversation-a")
    monkeypatch.setattr(video_project_tool, "MAKE_SECONDS", 0.0)
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: object())

    result = video_project_tool._video(action="create", title="Launch demo", scenes=SCENES)

    assert "Row-Bot checks back here when it's made" in result and "Don't call status meanwhile" in result
    project_id = result.split()[2].rstrip(":")
    edit = video_edits.get(_wait(project_id, 1).edit_id)
    [record] = waits.waiting("conversation-a")
    assert (record["kind"], record["label"]) == ("video", "“Launch demo” version 1 is made")
    assert "checks back" in video_project_tool._video(action="status", project=project_id)
    assert len(waits.waiting("conversation-a")) == 1, "asking again sets no second wait"
    assert waits.tick(record["next_at"] + 1) == 0 and fake.prompts == [], "nothing runs while it's being made"

    video_edits._set(edit, status="saved", ended_at=2.0)
    assert waits.tick(record["next_at"] + 1 + waits.TICK_SECONDS) == 1
    settle(platform, "conversation-a")

    assert fake.prompts[0].startswith("Checked back: “Launch demo” version 1 is made.")
    assert "2 scenes, made." in fake.prompts[0] and waits.waiting("conversation-a") == []


def test_a_kit_saved_before_full_bleed_was_the_default_loads_full_and_a_chosen_framing_stays(studio):
    import json

    from row_bot import video_brand
    from row_bot.data_paths import get_row_bot_data_dir

    path = get_row_bot_data_dir() / "video-brand.json"
    path.write_text(json.dumps({"colour": "#112233", "framing": "framed"}), encoding="utf-8")
    assert (video_brand.load().framing, video_brand.load().colour) == ("full", "#112233"), \
        "its old default, not a choice"
    video_brand.update({"framing": "framed"})
    assert video_brand.load().framing == "framed", "chosen since: kept"


def test_how_the_person_says_videos_should_look_is_saved_for_every_next_video(studio):
    """R3 (12 Nov): "full screen, no captions" was said in three chats and never kept; a new kit is full screen."""
    from row_bot import video_brand
    from row_bot.tools.video_project_tool import _video

    assert video_brand.load().framing == "full", "edge to edge unless the person chooses framed"
    video_brand.save({"colour": "#112233", "intro": "Row-Bot", "framing": "framed"})

    said = _video(action="brand", framing="full", music_mood="upbeat")

    kit = video_brand.load()
    assert (kit.framing, kit.music_mood) == ("full", "upbeat")
    assert (kit.colour, kit.intro) == ("#112233", "Row-Bot"), "what wasn't said stays as it was"
    assert "every new video starts this way" in said and "framing full" in said
    assert _video(action="brand", music_mood="loud").startswith("The music mood is"), "a wrong value is refused"
    assert video_brand.load().music_mood == "upbeat"


# ── Any footage (C2) ────────────────────────────────────────────────────────


@pytest.fixture
def footage(studio, tmp_path, monkeypatch):
    """A workspace with a clip, a camera folder elsewhere with a photo, and the person answering approvals."""
    from row_bot.tools import approval_gate, registry

    workspace = tmp_path / "workspace"
    (workspace / "clips").mkdir(parents=True)
    (workspace / "clips" / "beach.mp4").write_bytes(MP4)
    camera = tmp_path / "camera"
    camera.mkdir()
    (camera / "DSC0001.JPG").write_bytes(b"\xff\xd8\xff photo")
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    state = {"workspace": workspace, "photo": camera / "DSC0001.JPG", "asked": [], "answer": True, **studio}
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")  # Ask: the person answers.
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: state["asked"].append(payload) or state["answer"])
    return state


def test_a_picture_is_a_scene_held_for_its_line_and_never_sped_or_trimmed(footage):
    from row_bot import video_projects
    from row_bot.application.attachments import register_attachment

    register_attachment("conversation-a", "sunset.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    project = video_projects.create("conversation-a", "Holiday", [
        {"source": "sunset.png", "start": 4, "speed": 2, "line": "The last evening."},
        {"source": "sunset.png", "end": 6, "zoom": [0.6, 0.2, 0.3, 0.3]},
        {"source": "signup.mp4", "start": 1, "end": 4}])
    held, pushed, clip = project.latest.scenes
    assert (held.start, held.end, held.speed) == (0.0, None, 1.0), "a picture has no time of its own to cut"
    assert (pushed.end, pushed.zoom) == (6.0, [0.6, 0.2, 0.3, 0.3]), "shown 6 s, pushed in to its region"
    text = video_projects.summary(project)
    assert "sunset.png picture, as long as its line (else 3.5 s)" in text and "sunset.png picture, 6 s" in text

    video_projects.make(project, 1)
    _wait(project.project_id, 1)
    request = footage["started"][0]
    assert [(media.label, media.kind) for media in request.sources] == [("sunset.png", "image"),
                                                                       ("signup.mp4", "video")]


def test_footage_in_the_workspace_and_elsewhere_is_used_where_it_is_never_copied_into_the_chat(footage):
    """Real footage is big: a phone's clip in the workspace or a camera's photo elsewhere is read in place when the
    video is made, never copied into the chat (whose files hold 200 MB at most). A file elsewhere is asked about
    once, when it joins the video."""
    from row_bot import video_projects
    from row_bot.application.attachments import list_conversation_files

    photo = str(footage["photo"])
    before = [item["name"] for item in list_conversation_files("conversation-a")]
    project = video_projects.create("conversation-a", "Holiday", [
        {"source": "clips/beach.mp4", "start": 2, "end": 9}, {"source": photo}, {"source": photo, "end": 2}])
    assert [asked["description"] for asked in footage["asked"]] == [
        f"Outside the workspace: {footage['photo'].resolve()}"], "asked once, for the file outside the workspace"

    video_projects.make(project, 1)
    _wait(project.project_id, 1)
    [request] = footage["started"]
    assert [(media.path, media.ref) for media in request.sources] == [
        (footage["workspace"] / "clips" / "beach.mp4", ""), (footage["photo"].resolve(), "")], "read in place"
    assert [item["name"] for item in list_conversation_files("conversation-a")] == before, "nothing copied in"

    video_projects.change(project, by="row-bot", scenes=[
        *[{"scene_id": scene.scene_id, "source": scene.source, "start": scene.start, "end": scene.end}
          for scene in project.latest.scenes], {"card": "The end"}])
    assert len(footage["asked"]) == 1, "footage already in the video isn't asked about again"


def test_footage_moved_or_deleted_later_is_named_by_its_scene(footage):
    from row_bot import video_projects

    project = video_projects.create("conversation-a", "Holiday", [
        {"card": "Day one"}, {"source": "clips/beach.mp4", "start": 2, "end": 9}, {"source": "signup.mp4"}])
    (footage["workspace"] / "clips" / "beach.mp4").unlink()

    words = ("Footage missing: scene 2 (clips/beach.mp4): moved, renamed or deleted since it joined the video. Ask "
             "the person to put it back, or give those scenes other footage.")
    assert words in video_projects.summary(video_projects.get(project.project_id))
    video_projects.make(project, 1)
    failed = _wait(project.project_id, 1)
    assert failed.status == "failed" and failed.problem.startswith(words) and footage["started"] == []


def test_a_video_holds_two_hundred_scenes_from_a_hundred_videos_and_pictures(footage):
    """60 scenes and 10 different sources were the most a video could have: too few for real footage."""
    from row_bot import video_edits, video_projects

    clips = footage["workspace"] / "clips"
    for index in range(100):
        (clips / (f"clip-{index:02d}.mp4" if index < 80 else f"photo-{index:02d}.jpg")).write_bytes(MP4)
    names = [f"clips/clip-{index:02d}.mp4" if index < 80 else f"clips/photo-{index:02d}.jpg" for index in range(100)]
    scenes = [{"source": names[number % 100], "start": 5, "end": 8} for number in range(200)]
    project = video_projects.create("conversation-a", "Year in review", scenes)
    assert len(project.latest.scenes) == 200

    video_projects.make(project, 1)
    edit = video_edits.get(_wait(project.project_id, 1).edit_id)
    assert len(footage["started"][0].sources) == 100 and len(footage["started"][0].steps[0].scenes) == 200
    video_edits._set(edit, status="saved", marks=[round(3.0 * index, 1) for index in range(201)])
    video_edits.sweep_leftovers()  # Read back from its saved record, as after a restart.
    shown = video_projects.view(video_projects.get(project.project_id))["scenes"]
    assert (shown[199]["at"], shown[199]["length"]) == (597.0, 3.0), "every scene's place on the made video is kept"

    for too_many, words in ((scenes + [{"card": "One more"}], "At most 200 scenes"),
                            ([*scenes[:100], {"source": "signup.mp4"}], "at most 100 different videos and pictures")):
        with pytest.raises(video_projects.ProjectError, match=words):
            video_projects.create("conversation-a", "Too long", too_many)


def test_a_file_elsewhere_is_allowed_for_that_video_only_and_a_new_one_asks_again(footage, tmp_path):
    """Allowing a camera's photo for one video is never a general permission to read outside the workspace: the
    project remembers that exact file, another file elsewhere asks, and another video asks for the same file."""
    from row_bot import video_projects

    photo = str(footage["photo"])
    other = tmp_path / "camera" / "DSC0002.JPG"
    other.write_bytes(b"\xff\xd8\xff photo")
    asked = footage["asked"]
    project = video_projects.create("conversation-a", "Holiday", [{"source": photo}])
    assert len(asked) == 1 and project.outside == [{"path": str(footage["photo"].resolve()), "by": "person"}]

    video_projects.make(project, 1)
    _wait(project.project_id, 1)
    assert len(asked) == 1 and footage["started"][0].sources[0].path == footage["photo"].resolve(), \
        "made without asking again"
    video_projects.change(project, by="row-bot", scenes=[{"source": photo}, {"source": str(other)}])
    assert [item["description"] for item in asked[1:]] == [f"Outside the workspace: {other.resolve()}"], \
        "only the new file is asked about"
    video_projects.create("conversation-a", "Another video", [{"source": photo}])
    assert [item["description"] for item in asked[2:]] == [f"Outside the workspace: {footage['photo'].resolve()}"], \
        "another video asks for the same file"

    footage["answer"] = False
    with pytest.raises(video_projects.ProjectError, match="nothing outside the workspace was read"):
        video_projects.create("conversation-a", "Refused", [{"source": str(other)}])


def test_footage_inside_row_bots_data_folder_is_refused_by_its_full_path(footage):
    """A chat's own files are named by their name, never by where Row-Bot keeps them."""
    from row_bot import video_projects
    from row_bot.data_paths import get_row_bot_data_dir

    kept = get_row_bot_data_dir() / "media" / "secret.mp4"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(MP4)
    with pytest.raises(video_projects.ProjectError, match="Row-Bot's private data folder"):
        video_projects.create("conversation-a", "Peek", [{"source": str(kept)}])
    assert footage["asked"] == [], "refused before anything is asked"


def test_a_file_elsewhere_the_reviewer_allows_on_auto_is_recorded_as_the_reviewers_and_shown_in_the_step(
        footage, monkeypatch):
    """The person sees an outside read the reviewer granted: the step's approval line says so, and the project
    keeps who allowed each file."""
    import json

    from row_bot import approval_review, ffmpeg_runtime, video_projects
    from row_bot.tools import approval_gate, video_project_tool

    def reviewer(messages, _runtime):
        case = json.loads(messages[1]["content"])
        if case["action"]["tool"] == "video" and "holiday" in case["person_asked"][0]:
            return '{"decision": "approve", "reason": "You asked for a video of your holiday photos."}'
        return '{"decision": "ask", "reason": "Not what you asked for."}'

    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "auto")
    monkeypatch.setattr(approval_review, "current_request", lambda _runtime=None: approval_review.ReviewRequest(
        messages=("Make a video of my holiday photos from the camera folder",)))
    monkeypatch.setattr(approval_review, "_model_ref", lambda _runtime: "model:openai:gpt-test")
    monkeypatch.setattr(approval_review, "_call_reviewer", reviewer)
    monkeypatch.setattr(video_project_tool, "_conversation", lambda: "conversation-a")
    monkeypatch.setattr(video_project_tool, "MAKE_SECONDS", 0.0)
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: object())

    result = video_project_tool._video(action="create", title="Holiday",
                                       scenes=[{"source": str(footage["photo"])}])

    assert footage["asked"] == [], "the reviewer allowed it: the person wasn't interrupted"
    assert result.splitlines()[0] == ("Approval: approved by the reviewer: You asked for a video of your holiday "
                                      "photos")
    [project] = video_projects.list_projects("conversation-a")
    assert project.outside == [{"path": str(footage["photo"].resolve()), "by": "reviewer"}]
