"""Several images in one look, and contact sheets (F5, decision D6).

Up to eight pictures go to the vision model in one call, each labelled; nine are refused
plainly. A contact sheet tiles many pictures or video frames into one numbered, captioned
picture, shown in the chat. A video gives frames through ffmpeg (asked for the first time).
When the model can't see images, nothing is gathered. The provider and ffmpeg are fakes.
"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from row_bot.contact_sheet import grid, make_sheet

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200


def _png(size=(320, 180), colour=(30, 120, 200)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, colour).save(output, format="PNG")
    return output.getvalue()


class _Model:
    def __init__(self) -> None:
        self.messages: list = []

    def invoke(self, messages):
        self.messages.append(messages)
        return SimpleNamespace(content="Picture 3 shows the error.")


@pytest.fixture
def looking(tmp_path, monkeypatch, reload_for_data_dir):
    (threads,) = reload_for_data_dir(tmp_path / "data", "row_bot.threads")
    import row_bot.vision as vision
    from row_bot.application.attachments import register_attachment
    from row_bot.tools import approval_gate, registry, vision_tool

    chat = threads.create_thread("Screens", seed_default_skills=False)
    register_attachment(chat, "demo.mp4", MP4)
    workspace = tmp_path / "workspace"
    (workspace / "shots").mkdir(parents=True)
    for index in range(1, 13):
        (workspace / "shots" / f"step-{index:02d}.png").write_bytes(_png(colour=(index * 20, 60, 120)))
    (workspace / "shots" / "huge.png").write_bytes(_png((4000, 3000)))
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    service = vision.VisionService()
    service._model, service._enabled = "model:codex:gpt-5.5", True
    model = _Model()
    shown: list[str] = []
    state = SimpleNamespace(chat=chat, service=service, model=model, shown=shown, asked=[], answer=True)
    monkeypatch.setattr(vision, "vision_model_compatibility", lambda _model: {"usable": True, "explicit": False})
    monkeypatch.setattr("row_bot.models.get_llm_for", lambda _model: model)
    monkeypatch.setattr(vision_tool, "_get_vision_service", lambda: service)
    monkeypatch.setattr("row_bot.tool_outputs.current_thread_id", lambda: chat)
    monkeypatch.setattr("row_bot.tools.image_gen_tool._set_pending_image", lambda data: shown.append(data))
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: "approve")
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: state.asked.append(payload) or state.answer)
    state.look = lambda question, **kwargs: vision_tool._analyze_image(question, **kwargs)
    return state


def _shots(count: int) -> list[str]:
    return [f"shots/step-{index:02d}.png" for index in range(1, count + 1)]


def test_eight_images_go_to_the_model_in_one_call_each_labelled(looking):
    result = looking.look("Which one shows an error?", files=_shots(7) + ["shots/huge.png"])

    assert result == "Looked at 8 images.\nPicture 3 shows the error."
    [[message]] = looking.model.messages
    parts = message.content
    assert parts[0]["type"] == "text" and parts[0]["text"].startswith("You are looking at 8 images, in this order:")
    assert "Image 8: shots/huge.png" in parts[0]["text"] and parts[0]["text"].endswith("Which one shows an error?")
    images = [part for part in parts[1:] if part["type"] == "image_url"]
    labels = [part["text"] for part in parts[1:] if part["type"] == "text"]
    assert len(images) == 8 and labels[0] == "Image 1: shots/step-01.png"
    huge = Image.open(io.BytesIO(base64.b64decode(images[-1]["image_url"]["url"].split(",", 1)[1])))
    assert huge.format == "JPEG" and max(huge.size) == 1568  # Sent at the size a model reads anyway.
    assert looking.shown == []


def test_nine_images_are_refused_plainly_and_nothing_is_sent(looking):
    result = looking.look("Compare them.", files=_shots(9))

    assert result == ("At most 8 images go in one look (this would be 9). Look at them in smaller groups, or set "
                      "contact_sheet to tile them onto one labelled picture.")
    assert looking.model.messages == []


def test_a_local_model_gets_every_image_in_one_message(looking, monkeypatch):
    import row_bot.vision as vision

    looking.service._model = "gemma3:4b"
    calls: list[dict] = []
    client = SimpleNamespace(chat=lambda **kwargs: calls.append(kwargs) or {"message": {"content": "three"}})
    monkeypatch.setattr(vision, "_ollama_mod", SimpleNamespace())
    monkeypatch.setattr("row_bot.models._ollama_client", lambda: client)
    monkeypatch.setattr("row_bot.models.ollama_chat_request_settings", lambda _model: {})

    assert looking.look("How many?", files=_shots(3)) == "Looked at 3 images.\nthree"
    [call] = calls
    assert len(call["messages"][0]["images"]) == 3
    assert "Image 2: shots/step-02.png" in call["messages"][0]["content"]


def test_a_contact_sheet_tiles_many_pictures_into_one_labelled_picture_shown_in_the_chat(looking):
    result = looking.look("Which step failed?", files=_shots(12), contact_sheet=True)

    assert result.startswith("Looked at a contact sheet of 12 pictures. The contact sheet is shown to the person")
    [[message]] = looking.model.messages
    [image] = [part for part in message.content if part["type"] == "image_url"]
    question = message.content[0]["text"]
    assert question.startswith("This is a contact sheet of 12 pictures, numbered 1 to 12 in reading order")
    assert question.endswith("Which step failed?")
    [shown] = looking.shown
    sheet = Image.open(io.BytesIO(base64.b64decode(shown)))
    assert sheet.format == "JPEG" and sheet.width <= 2048


def test_the_sheet_layout_and_labels():
    pictures = [(_png(), f"step-{index:02d}.png") for index in range(1, 13)]
    sheet, tiles = make_sheet(pictures)

    assert [grid(count) for count in (1, 2, 4, 8, 12, 48)] == [(1, 1), (2, 1), (2, 2), (3, 3), (4, 3), (7, 7)]
    assert [tile.label for tile in tiles[:2]] == ["1 · step-01.png", "2 · step-02.png"]
    assert [tile.number for tile in tiles] == list(range(1, 13))
    lefts = sorted({tile.box[0] for tile in tiles})
    tops = sorted({tile.box[1] for tile in tiles})
    assert (len(lefts), len(tops)) == (4, 3)  # Four across, three down, in reading order.
    assert tiles[4].box[0] == tiles[0].box[0] and tiles[4].box[1] > tiles[0].box[1]
    width, height = tiles[0].box[2] - tiles[0].box[0], tiles[0].box[3] - tiles[0].box[1]
    assert abs(width / height - 16 / 9) < 0.02  # Tiles keep the pictures' shape.
    assert Image.open(io.BytesIO(sheet)).size[0] <= 2048


def test_a_video_gives_frames_with_their_times_after_asking_to_download_ffmpeg(looking, monkeypatch):
    import subprocess

    from row_bot import ffmpeg_runtime, video_edits
    from row_bot.video_editing import VideoInfo

    fake = ffmpeg_runtime.Ffmpeg(executable="ffmpeg", source="row_bot", version="7.1",
                                 licence="GPL version 3 or later")
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: None)
    monkeypatch.setattr(ffmpeg_runtime, "current_wheel", lambda *_a, **_k: ffmpeg_runtime.WHEELS["macos-aarch64"])
    monkeypatch.setattr(ffmpeg_runtime, "install", lambda **_kw: fake)
    monkeypatch.setattr(video_edits, "probe_video", lambda *_a: VideoInfo(6.0, 640, 360, 30.0, True))
    runs: list[list[str]] = []

    def run(command, cwd=None, **_kwargs):
        runs.append(list(command))
        for index, arg in enumerate(command):
            if arg.startswith("file:still-") and command[index - 1] != "-i":
                (Path(cwd) / arg[len("file:"):]).write_bytes(_png())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)

    result = looking.look("What happens?", files=["demo.mp4", "shots/step-01.png"], contact_sheet=True, frames=4)

    [asked] = looking.asked
    assert asked["question"] == "Looking at frames of a video needs ffmpeg (about 20 MB) from PyPI. Download it now?"
    assert result.startswith("Approval: asked; approved by you\nLooked at a contact sheet of 5 pictures.")
    [[message]] = looking.model.messages
    assert len([part for part in message.content if part["type"] == "image_url"]) == 1
    [frames_run] = runs
    assert frames_run.count("-ss") == 4 and frames_run[0] == "ffmpeg"
    working = Path(os.environ["ROW_BOT_DATA_DIR"]) / "vision-frames"
    assert not working.exists() or list(working.iterdir()) == []  # The frames' working copies go.


def test_when_the_model_cannot_see_images_nothing_is_gathered(looking, monkeypatch):
    import row_bot.vision as vision
    from row_bot import ffmpeg_runtime

    monkeypatch.setattr(vision, "vision_model_compatibility",
                        lambda _model: {"usable": False, "explicit": True, "reason": "no images"})
    looking.service._model = ""
    monkeypatch.setattr(looking.service, "effective_model", lambda chat_model=None: "model:ollama:qwen3:8b")
    monkeypatch.setattr("row_bot.media_sources.resolve", lambda *_a: pytest.fail("nothing is gathered"))
    monkeypatch.setattr(ffmpeg_runtime, "ensure", lambda *_a, **_k: pytest.fail("no ffmpeg is fetched"))

    result = looking.look("What's in the recording?", files=["demo.mp4"], contact_sheet=True)

    assert result == ("Couldn't look at the images: The chat model can't see images. Choose a model that can for "
                      "Vision in Settings › Models (or switch this chat to one), then ask again.")
    assert looking.model.messages == [] and looking.shown == [] and looking.asked == []


def test_the_screen_and_camera_keep_their_own_question(looking):
    assert looking.look("Compare", source="screen", files=_shots(2)) == (
        "Look at the screen or the camera in a call of its own; it can't be mixed with files.")
    assert looking.asked == [] and looking.model.messages == []
    assert looking.look("Tile", contact_sheet=True, source="file") == (
        "A contact sheet needs files: the pictures or videos to tile.")
