"""One identity, one resolver (W4): every media and file tool finds a file the same way, and names what it made
on its result's first line, which history masking keeps.

A film had six identities and three resolvers that disagreed, its name line was the last line of the result
(masking keeps the first), "Living Buddy v2" became "… v2 v1.mp4", "£480" became "-480", and the media tools
took a chat's code folder for "the workspace" while the file tools used the workspace folder. The chat, the
fake ffmpeg and the stores come from edit_video's own fixture; nothing renders, records or reaches the network.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from types import SimpleNamespace

import pytest

from tests.subsystem.tools.test_video_edit_tool import MP4, studio  # noqa: F401

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

TRIM = [{"op": "trim", "start": 0, "end": 3}]


def _edit_id(result: str) -> str:
    return re.search(r"edit-[0-9a-f]{8}", result).group(0)


def _versions(chat: str, name: str = "Sam's birthday.mp4") -> list[dict]:
    """Two versions of one output, as a video project's renders are kept."""
    from row_bot.application.attachments import register_attachment

    return [register_attachment(chat, name, MP4 + bytes([number]), role="draft", output_id="vid-0000abcd",
                                version=number) for number in (1, 2)]


def test_every_way_of_naming_a_file_goes_through_one_resolver(studio, monkeypatch, tmp_path):  # noqa: F811
    from row_bot import media_sources, recordings, video_projects
    from row_bot.media_sources import SourceError

    first, second = _versions(studio.chat)
    edit_id = _edit_id(studio.edit(action="edit", inputs=["demo.mp4"], steps=TRIM, name="cut"))
    made = studio.edits.get(edit_id)
    monkeypatch.setattr(recordings, "get", lambda value: SimpleNamespace(
        conversation_id=studio.chat, status="saved", media_ref=first["attachment_ref"], kind="browser")
        if value == "rec-0000abcd" else None)
    project = SimpleNamespace(title="Sam's birthday", versions=[SimpleNamespace(number=1, edit_id=edit_id)])
    project.version = lambda number: next((item for item in project.versions if item.number == number), None)
    monkeypatch.setattr(video_projects, "get", lambda value, chat=None: project
                        if (value, chat) == ("vid-0000abcd", studio.chat) else None)
    outside = tmp_path / "elsewhere" / "clip.mp4"
    outside.parent.mkdir()
    outside.write_bytes(MP4)
    monkeypatch.setattr("row_bot.tools.filesystem_tool.outside_workspace_refusal", lambda _op, _paths: ("", ""))

    def found(value: str, **options) -> str:
        [media] = media_sources.find(value, studio.chat, **options)
        return media.ref or str(media.path)

    assert second["name"] == first["name"] == "Sam's birthday.mp4", "a version keeps its output's name"
    assert found("Sam's birthday") == second["attachment_ref"], "the output's name: its newest version"
    assert found("Sam's birthday.mp4") == second["attachment_ref"]
    assert found("Sam's birthday@v1") == first["attachment_ref"]
    assert found("demo") == found("demo.mp4"), "a chat file by its own name or its output's"
    assert found("clips/intro.mp4") == str(studio.workspace / "clips" / "intro.mp4")
    assert found(str(studio.workspace / "clips" / "intro.mp4")) == str(studio.workspace / "clips" / "intro.mp4")
    assert found("rec-0000abcd") == first["attachment_ref"]
    assert found(edit_id) == found("vid-0000abcd") == found("vid-0000abcd@v1") == made.media_ref
    assert found(str(outside)) == str(outside.resolve()), "elsewhere: read in place once the person allows it"
    with pytest.raises(SourceError, match="outside the workspace"):
        found(str(outside), kinds=None, outside=False)  # send_file and present never read one.
    with pytest.raises(SourceError, match="no version 3 in this chat; it has v1, v2"):
        found("Sam's birthday@v3")
    with pytest.raises(SourceError, match=r"Couldn't find nothing\.mp4 .* This chat's outputs: “cut”, "
                                          "“Sam's birthday”, “demo”"):
        found("nothing.mp4")


@pytest.mark.parametrize("options", [{}, {"kinds": None, "outside": False}])
def test_the_data_folder_is_always_refused(studio, options):  # noqa: F811
    from row_bot import media_sources

    private = studio.data / "api_keys.json"
    private.write_text("{}", encoding="utf-8")
    for value in (str(private), str(studio.data / "media" / studio.chat)):
        with pytest.raises(media_sources.SourceError, match="private data folder"):
            media_sources.find(value, studio.chat, **options)


def test_a_media_result_names_its_output_on_its_first_line_which_masking_keeps(studio):  # noqa: F811
    from row_bot.agent import _summarize_tool_result
    from row_bot.tools.sound_tool import _make_music

    result = studio.edit(action="edit", inputs=["demo.mp4"], steps=TRIM, name="Sam's birthday")
    first = result.splitlines()[0]

    assert first == f'Video edit {_edit_id(result)}: Output "Sam\'s birthday" v1 (working) · Sam\'s birthday.mp4'
    assert _summarize_tool_result("edit_video", result + "\n" + "More words. " * 200) == f"[edit_video]: {first}"
    music = _make_music(1.0, name="Birthday tune")
    assert _summarize_tool_result("make_music", music) == (
        '[make_music]: Output "Birthday tune" v1 (working) · Birthday tune.wav')


def test_masking_keeps_the_output_after_a_results_approval_lines(studio):  # noqa: F811
    """A download asked for, or a file elsewhere allowed by the reviewer, leads its result with an approval line;
    the masked result still names the output, and a result that is only approval lines keeps the first."""
    from row_bot.agent import _summarize_tool_result
    from row_bot.tools.approval_gate import with_approval

    result = studio.edit(action="edit", inputs=["demo.mp4"], steps=TRIM, name="Sam's birthday")
    approved = with_approval("Approval: asked; approved by you\nApproval: reviewer allowed it",
                             result + "\n" + "More words. " * 200)

    assert approved.startswith("Approval: asked; approved by you\n")  # The approval line stays first.
    assert _summarize_tool_result("edit_video", approved) == f"[edit_video]: {result.splitlines()[0]}"
    assert _summarize_tool_result("edit_video", "Approval: asked; refused by you") == (
        "[edit_video]: Approval: asked; refused by you")


def test_names_keep_what_the_person_wrote(studio, monkeypatch):  # noqa: F811
    """"£480" was "-480"; currency signs and accents are fine in every system's file names."""
    from row_bot import agent
    from row_bot.tools.video_editing_tool import output_name

    result = studio.edit(action="edit", inputs=["demo.mp4"], steps=TRIM, name="£480 offer at the Café")

    assert result.splitlines()[0].endswith(" · £480 offer at the Café.mp4")
    assert output_name('Q3: "best"? <draft>', default="video") == "Q3- -best- -draft.mp4"
    assert agent._media_label({"name": "generate_video", "args": {"prompt": "A £480 deal at the café"}}) == (
        "a-£480-deal-at-the-café")


def test_one_name_in_the_chat_the_workspace_save_and_the_phone_send(studio, monkeypatch):  # noqa: F811
    from row_bot import file_deliveries
    from row_bot.application import app_notices
    from row_bot.tools import file_delivery_tool

    monkeypatch.setattr(file_deliveries, "_RECORDS", {})
    monkeypatch.setattr(file_deliveries, "_LOADED_ROOT", None)
    monkeypatch.setattr(file_delivery_tool, "_REPLY_COUNTS", OrderedDict())
    monkeypatch.setattr(app_notices, "app_notices", app_notices.NoticeJournal())
    result = studio.edit(action="edit", inputs=["demo.mp4"], steps=TRIM, name="Sam's birthday")
    edit_id = _edit_id(result)

    saved = studio.edit(action="save", edit_id=edit_id, path="films")
    sent = file_delivery_tool._send_file(files=["Sam's birthday"], to="devices")
    [delivery] = file_deliveries.list_deliveries()

    assert result.splitlines()[0].endswith(" · Sam's birthday.mp4")
    assert saved == "Saved films/Sam's birthday.mp4 in the workspace."
    assert (studio.workspace / "films" / "Sam's birthday.mp4").read_bytes() == MP4
    assert [item.name for item in delivery.files] == ["Sam's birthday.mp4"], sent


def test_the_media_tools_use_the_workspace_folder_even_when_the_chat_has_a_code_folder(studio, monkeypatch,  # noqa: F811
                                                                                       tmp_path):
    """The media tools took a chat's code folder for "the workspace"; the file tools use the workspace folder."""
    from row_bot import media_sources
    from row_bot.developer import storage, tool_context

    code = tmp_path / "code"
    (code / "clips").mkdir(parents=True)
    (code / "clips" / "intro.mp4").write_bytes(MP4 + b"code")
    monkeypatch.setattr(tool_context, "get_workspace_id", lambda: "code-folder")
    monkeypatch.setattr(tool_context, "infer_workspace_id_from_thread", lambda _chat: "code-folder")
    monkeypatch.setattr(storage, "get_workspace", lambda _id: SimpleNamespace(id="code-folder", name="App",
                                                                              path=str(code)))

    [media] = media_sources.find("clips/intro.mp4", studio.chat)
    edit_id = _edit_id(studio.edit(action="edit", inputs=["demo.mp4"], steps=TRIM, name="cut"))
    saved = studio.edit(action="save", edit_id=edit_id, path="films/cut.mp4")

    assert media.path == studio.workspace / "clips" / "intro.mp4"
    assert saved == "Saved films/cut.mp4 in the workspace."
    assert (studio.workspace / "films" / "cut.mp4").is_file() and not (code / "films").exists()


def test_present_with_no_files_lists_this_chats_outputs(studio, monkeypatch):  # noqa: F811
    from row_bot.application.attachment_context import AttachmentExecutionCaches, _current
    from row_bot.application.attachments import register_attachment
    from row_bot.tools.present_tool import _present

    _versions(studio.chat)
    register_attachment(studio.chat, "frame-01.jpg", b"\xff\xd8\xffframe", role="working")
    register_attachment(studio.chat, "scene-1.wav", b"RIFF\x00\x00\x00\x00WAVEfmt ", role="working")
    token = _current.set(AttachmentExecutionCaches(studio.chat))
    try:
        listed = _present()
    finally:
        _current.reset(token)

    # Newest first, each output once at its newest version; a working picture (a frame, a sheet) isn't one.
    assert listed.splitlines() == [
        "This chat's outputs, newest first (name one in any media tool, present or send_file; name@v2 for an "
        "older version):",
        'Output "scene-1" v1 (working) · scene-1.wav',
        'Output "Sam\'s birthday" v2 (draft) · Sam\'s birthday.mp4',
        'Output "demo" v1 · demo.mp4',
    ]
