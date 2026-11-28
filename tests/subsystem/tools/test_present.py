"""Finals first: each file a tool makes has a role, and ``present`` shows the finished result in its reply.

AI-made pictures and videos show like finals; recordings, screenshots, contact sheets, frames, voice lines and
edits are working files; a video project's render is a draft. ``present`` marks a file final and the reply
that called it carries it as a final, read back through the transcript API as a reload reads it. A film the
agent rendered in the workspace becomes the chat's own output, once. Nothing here records, renders or
reaches the network: captures and renders are files the test writes.
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from tests.contracts.client_platform.test_headless_lifecycle import platform  # noqa: F401

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

PNG = b"\x89PNG\r\n\x1a\nfixture"
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200
CHAT = "conversation-a"


@pytest.fixture
def chat(platform, monkeypatch, tmp_path):  # noqa: F811
    """conversation-a with a workspace, a turn's attachment scope and a tool step that keeps its media."""
    from row_bot.api.v1.schemas import TranscriptPage
    from row_bot.application.attachment_context import AttachmentExecutionCaches, _current, tool_attachment_scope
    from row_bot.application.generated_media import capture_generated_media
    from row_bot.threads import append_checkpoint_messages

    (tmp_path / "workspace" / "renders").mkdir(parents=True)
    monkeypatch.setattr("row_bot.tool_outputs.current_thread_id", lambda: CHAT)
    turn = _current.set(AttachmentExecutionCaches(CHAT))
    steps: list[str] = []

    def step(name: str, args: dict, run) -> tuple[str, list[dict]]:
        """One tool step as the agent's wrapper runs it: its result, then the media it made or presented."""
        with tool_attachment_scope() as caches:
            content = run()
            media = capture_generated_media(CHAT, caches)
        call = f"call-{len(steps)}"
        steps.append(call)
        append_checkpoint_messages(CHAT, [
            AIMessage(content="", id=f"{call}-owner", tool_calls=[{"id": call, "name": name, "args": args}]),
            ToolMessage(content=content, id=f"{call}-result", tool_call_id=call, name=name,
                        additional_kwargs={"platform_media": media} if media else {}),
        ])
        return content, media

    def reloaded() -> list[dict]:
        """Each step's media as the transcript API gives it after a reload."""
        page = platform.transcript(CHAT)
        TranscriptPage.model_validate(page)
        return [(item["canonical_name"], (item["specialization"] or {}).get("media") or [])
                for row in page["rows"] for group in row.get("traces") or [] for item in group["items"]]

    yield SimpleNamespace(step=step, reloaded=reloaded, workspace=tmp_path / "workspace")
    _current.reset(turn)


def _lineage(reference: str) -> dict:
    from row_bot.application.attachments import attachment_lineage

    return attachment_lineage(reference)


def test_each_file_a_tool_makes_has_its_role(chat, tmp_path):
    from row_bot import recordings
    from row_bot.tools.browser_tool import _keep_image
    from row_bot.tools.image_gen_tool import _set_pending_image
    from row_bot.tools.sound_tool import _save as save_sound
    from row_bot.tools.vision_tool import _keep_sheet

    _content, made = chat.step("generate_image", {"prompt": "A fox"},
                               lambda: _set_pending_image(base64.b64encode(PNG).decode()) or "Generated.")
    _content, shot = chat.step("browser_screenshot", {}, lambda: _keep_image(PNG, "screenshot-1.png") and "Shot.")
    _content, sheet = chat.step("analyze_image", {}, lambda: _keep_sheet(b"\xff\xd8\xffsheet") or "Looked.")
    voice = save_sound("line-01.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", CHAT)

    class Capture:
        def __init__(self, record, path: Path) -> None:
            self.record, self.path = record, path

        def request_stop(self, reason: str) -> None:
            self.path.write_bytes(MP4)
            recordings.finished(self.record, reason, self.path)

    record = recordings.start(CHAT, "browser", max_minutes=1, begin=Capture)
    recordings.stop(record.recording_id, by="agent")
    recordings.wait(record, 5)

    assert [item["payload"]["role"] for item in made] == ["final"]  # AI-made: shown like a final.
    assert [item["payload"]["role"] for item in shot + sheet] == ["working", "working"]
    assert _lineage(recordings.get(record.recording_id).media_ref)["role"] == "working"
    assert _lineage(recordings.get(record.recording_id).media_ref)["output_id"] == record.recording_id
    from row_bot.application.attachments import list_conversation_files

    files = {item["name"]: item for item in list_conversation_files(CHAT)}
    assert files[voice]["role"] == "working" and files["contact-sheet.jpg"]["role"] == "working"
    assert recordings.summary(recordings.get(record.recording_id)).startswith(
        f'Recording {record.recording_id}: Output "recording" v1 (working) · generated-recording.mp4\nSaved ')
    # The reload reads each step's role, as the live events said it.
    assert [(name, [media.get("role") for media in found]) for name, found in chat.reloaded() if found] == [
        ("generate_image", ["final"]), ("Browser Screenshot", ["working"]), ("analyze_image", ["working"])]


def test_present_marks_a_working_file_final_and_its_reply_carries_it(chat):
    from row_bot.application.attachments import list_conversation_files
    from row_bot.tools.browser_tool import _keep_image
    from row_bot.tools.present_tool import _present

    _content, [shot] = chat.step("browser_screenshot", {}, lambda: _keep_image(PNG, "pricing-page.png") and "Shot.")
    reference = shot["payload"]["media_ref"]

    result, media = chat.step("present", {"files": ["pricing-page.png"], "caption": "The new pricing page"},
                              lambda: _present(files=["pricing-page.png"], caption="The new pricing page"))

    assert result.startswith('Output "pricing-page" v1 (final) · pricing-page.png\nPresented in this reply')
    # Presenting doesn't end the work: what else they asked for comes first, then a line about it.
    assert result.endswith("Finish anything else they asked for, then end with a line saying what it is.")
    assert _lineage(reference)["role"] == "final"
    assert media == [{"type": "media.available", "payload": {
        "media_ref": reference, "mime_type": "image/png", "role": "final", "name": "pricing-page.png",
        "prompt": "The new pricing page"}}]
    assert [item["name"] for item in list_conversation_files(CHAT)] == ["pricing-page.png"], "no second copy"
    _name, presented = chat.reloaded()[-1]
    assert presented == [{"media_ref": reference, "mime_type": "image/png", "role": "final",
                          "name": "pricing-page.png", "prompt": "The new pricing page"}]


def test_a_film_rendered_in_the_workspace_becomes_the_chats_own_output_once(chat):
    from row_bot.application.attachments import list_conversation_files
    from row_bot.tools.present_tool import _present

    film = chat.workspace / "renders" / "Birthday montage.mp4"
    film.write_bytes(MP4)

    first, [shown] = chat.step("present", {}, lambda: _present(files=["renders/Birthday montage.mp4"]))
    again, [same] = chat.step("present", {}, lambda: _present(files=["renders/Birthday montage.mp4"]))
    film.write_bytes(MP4 + b"recut")
    _third, [recut] = chat.step("present", {}, lambda: _present(files=["renders/Birthday montage.mp4"]))

    assert first.startswith('Output "Birthday montage" v1 (final) · Birthday montage.mp4\nPresented')
    assert same["payload"]["media_ref"] == shown["payload"]["media_ref"], "the same bytes are the same output"
    assert again.startswith('Output "Birthday montage" v1 (final) · Birthday montage.mp4\nMarked final: it '
                            "already shows in this chat")
    first_lineage, recut_lineage = _lineage(shown["payload"]["media_ref"]), _lineage(recut["payload"]["media_ref"])
    assert first_lineage["output_id"] == recut_lineage["output_id"]  # One output, two versions.
    assert (first_lineage["version"], recut_lineage["version"]) == (1, 2)
    assert first_lineage["made_from"] == ["renders/Birthday montage.mp4"]
    # One name for the output: its versions share the film's name, told apart by their numbers (W4).
    assert [item["name"] for item in list_conversation_files(CHAT)] == ["Birthday montage.mp4"] * 2
    assert _third.startswith('Output "Birthday montage" v2 (final) · Birthday montage.mp4\n')


def test_a_generated_video_presented_is_marked_final_and_never_shown_twice(chat, tmp_path):
    from row_bot.tools.present_tool import _present
    from row_bot.tools.video_gen_tool import _set_pending_video

    original = tmp_path / "media" / CHAT / "vid_original.mp4"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(MP4)
    _content, [made] = chat.step("generate_video", {"prompt": "Waves at dusk"},
                                 lambda: _set_pending_video({"path": str(original)}) or "Generated.")
    from row_bot.application.attachments import inspect_attachment

    file_name = inspect_attachment(made["payload"]["media_ref"])["name"]
    result, [again] = chat.step("present", {}, lambda: _present(files=[file_name]))

    assert result.startswith(f'Output "video" v1 (final) · {file_name}\nMarked final: it already shows '
                             "in this chat, so it isn't shown twice")
    assert again["payload"]["media_ref"] == made["payload"]["media_ref"]  # The client folds it into the first.


def test_present_refuses_what_it_cant_show_and_shows_nothing(chat, tmp_path):
    from row_bot.tools.present_tool import MAX_FILES, _present

    outside = tmp_path / "secret.mp4"
    outside.write_bytes(MP4)
    for files in ([str(outside)], ["../secret.mp4"], ["missing.mp4"], ["vid-1a2b3c4d"], []):
        result, media = chat.step("present", {}, lambda files=files: _present(files=files))
        assert not result.startswith("Presented") and media == [], (files, result)
    assert _present(files=[f"f{index}.png" for index in range(MAX_FILES + 1)]).startswith("Present at most 8")



def test_a_page_made_in_this_chat_already_shows_so_naming_it_is_no_error(chat):
    """F18: right after page_create the agent named the page's id, and present answered that it couldn't find it."""
    from row_bot import pages
    from row_bot.tools.browser_tool import _keep_image
    from row_bot.tools.present_tool import _present

    page = pages.create(CHAT, "Weekend plan", "<h1>Weekend plan</h1>")

    alone, media = chat.step("present", {}, lambda: _present(files=[page.page_id]))
    assert alone == (f"{page.page_id} (“Weekend plan”) already shows in this chat as a page card, so there is "
                     "nothing to present. Finish anything else the person asked for, then end with one line about "
                     "the page.")
    assert media == []

    chat.step("browser_screenshot", {}, lambda: _keep_image(PNG, "map.png") and "Shot.")
    both, [shown] = chat.step("present", {}, lambda: _present(files=["map.png", page.page_id]))
    assert both.startswith('Output "map" v1 (final) · map.png\n'
                           f"{page.page_id} (“Weekend plan”) already shows in this chat as a page card.\nPresented")
    assert shown["payload"]["name"] == "map.png"
    # Another chat's page is not this chat's: it stays unknown.
    other = pages.create("conversation-b", "Their plan", "<h1>Theirs</h1>")
    refused, _media = chat.step("present", {}, lambda: _present(files=[other.page_id]))
    assert "Nothing was presented" in refused
