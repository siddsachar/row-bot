"""edit_video (F3): an edit runs as a background job through the process manager and is saved to
the chat's files; reading and writing stay inside the chat's files and the workspace.

The tracked-process owner is a fake that "runs ffmpeg" by writing the file its arguments name,
so the real job manager, process manager and file owner are exercised with nothing started.
"""

from __future__ import annotations

import io
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 200


def _jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 36), (200, 40, 40)).save(output, format="JPEG")
    return output.getvalue()


class _Tracked:
    def __init__(self, on_quiesced, on_output):
        self.on_quiesced, self.on_output = on_quiesced, on_output
        self.process = SimpleNamespace(pid=None)
        self.child_pid = None
        self.done = threading.Event()
        self.stop_requested = False
        self.code = ""
        self.exit_code = None

    def exit(self, code: int) -> None:
        if not self.done.is_set():
            self.exit_code = code
            self.on_quiesced(self)
            self.done.set()


@pytest.fixture
def studio(tmp_path, monkeypatch, reload_for_data_dir):
    """A chat with a video in its files, a workspace folder, a fake ffmpeg and a fake process owner."""
    (threads,) = reload_for_data_dir(tmp_path / "data", "row_bot.threads")
    from row_bot import background_processes, ffmpeg_runtime, video_edits
    from row_bot.application.attachments import register_attachment
    from row_bot.developer import runtime
    from row_bot.tools import approval_gate, registry
    from row_bot.tools import video_editing_tool as video_tool
    from row_bot.video_editing import VideoInfo

    chat = threads.create_thread("Demo", seed_default_skills=False)
    other = threads.create_thread("Other", seed_default_skills=False)
    register_attachment(chat, "demo.mp4", MP4)
    workspace = tmp_path / "workspace"
    (workspace / "clips").mkdir(parents=True)
    (workspace / "clips" / "intro.mp4").write_bytes(MP4)
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    state = SimpleNamespace(chat=chat, other=other, workspace=workspace, started=[], hold=False, fail="",
                            asked=[], answer=True, mode="approve", tracked=[], data=tmp_path / "data")

    def launch(root, argv, command, *, on_quiesced=None, on_output=None, **_kw):
        tracked = _Tracked(on_quiesced, on_output)
        state.started.append({"argv": list(argv), "command": command, "folder": Path(root),
                              "graph": (Path(root) / "graph.txt").read_text(encoding="utf-8")
                              if (Path(root) / "graph.txt").exists() else "", "tracked": tracked})
        state.tracked.append(tracked)
        if state.hold:
            return tracked
        finish(tracked, Path(root), argv)
        return tracked

    def finish(tracked, root, argv):
        if state.fail:
            tracked.on_output("stderr", f"[out#0] {state.fail}\n")
            tracked.exit(1)
            return
        for arg in argv:
            if arg.startswith("file:") and argv[argv.index(arg) - 1] != "-i":
                name = arg[len("file:"):]
                (root / name).write_bytes(_jpeg() if name.endswith(".jpg") else MP4)
        tracked.on_output("stdout", "out_time_us=3000000\nprogress=end\n")
        tracked.exit(0)

    def stop(tracked):
        tracked.stop_requested = True
        tracked.exit(130)

    monkeypatch.setattr(runtime, "launch_tracked_process", launch)
    monkeypatch.setattr(runtime, "stop_tracked_process", stop)
    monkeypatch.setattr(runtime, "retire_tracked_process", lambda _state: True)
    fake = ffmpeg_runtime.Ffmpeg(executable="ffmpeg", source="row_bot", version="7.1",
                                 licence="GPL version 3 or later")
    state.ffmpeg = fake
    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: fake)
    infos = {"input-1.mp4": VideoInfo(6.0, 640, 360, 30.0, True),
             "output.mp4": VideoInfo(3.0, 640, 360, 30.0, True)}
    monkeypatch.setattr(video_edits, "probe_video",
                        lambda _ffmpeg, path, _folder: infos.get(path, VideoInfo(4.0, 320, 240, 25.0, False)))
    monkeypatch.setattr("row_bot.tool_outputs.current_thread_id", lambda: state.chat)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: state.mode)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: state.asked.append(payload) or state.answer)
    state.finish = finish
    state.edit = lambda **kwargs: video_tool._edit_video(**kwargs)
    state.edits = video_edits
    yield state
    video_edits.stop_all(timeout=2)
    background_processes.stop_all(timeout=1)


def _edit_id(result: str) -> str:
    line = result.splitlines()[-1] if result.startswith("Approval:") else result
    return line.split(":", 1)[0].removeprefix("Video edit ")


def test_an_edit_runs_ffmpeg_in_the_background_manager_and_saves_a_new_video_to_the_chat(studio):
    from row_bot import background_processes
    from row_bot.application.attachments import read_attachment

    result = studio.edit(inputs=["demo.mp4"], steps=[{"op": "trim", "start": 1, "end": 4}], name="cut")

    assert result.startswith("Video edit edit-") and ": saved 0:03 of video" in result
    assert "to this chat's files as cut.mp4; the person sees it in the chat. Trimmed." in result
    assert studio.asked == []  # A new file in the chat's files replaces nothing: no approval.
    edit = studio.edits.get(_edit_id(result))
    view = studio.edits.view(edit)
    assert (view["status"], view["mime_type"], view["progress"]) == ("saved", "video/mp4", 1.0)
    assert view["ffmpeg"] == "ffmpeg 7.1 (GPL version 3 or later), downloaded from PyPI"
    assert read_attachment(view["media_ref"])[1] == MP4
    [run] = studio.started
    assert run["argv"][0] == "ffmpeg" and "-/filter_complex" in run["argv"]
    assert "trim=start=1:end=4" in run["graph"]
    assert run["folder"] == studio.edits.working_folder(edit)
    assert not run["folder"].exists()  # The working folder goes once it is saved.
    [process] = background_processes.list_processes(studio.chat)
    assert process.name == "Video edit" and process.status == "finished"


def test_a_long_edit_carries_on_with_progress_and_stops_with_nothing_saved(studio, monkeypatch):
    from row_bot.application.attachments import list_conversation_files
    from row_bot.tools import video_editing_tool as video_tool

    monkeypatch.setattr(video_tool, "INLINE_SECONDS", 0.05)
    studio.hold = True

    result = studio.edit(inputs=["demo.mp4"], steps=[{"op": "speed", "factor": 2}])
    edit = studio.edits.get(_edit_id(result))
    assert "carries on in the background and the chat shows its progress with Stop" in result
    for _ in range(500):  # The job records its process just after the process starts.
        if studio.tracked and edit.status == "editing":
            break
        threading.Event().wait(0.01)
    studio.tracked[0].on_output("stdout", "out_time_us=1500000\n")
    view = studio.edits.view(edit)
    assert (view["status"], view["progress"], view["duration_seconds"]) == ("editing", 0.5, 3.0)

    studio.edits.stop(edit.edit_id, by="you")
    assert studio.edits.wait(edit, 5)
    assert (edit.status, edit.stopped_by) == ("stopped", "you")
    assert [item["name"] for item in list_conversation_files(studio.chat)] == ["demo.mp4"]
    assert studio.edits.summary(edit).endswith("stopped by the person; nothing was saved.")


@pytest.mark.parametrize("source", ["../outside.mp4", "clips/../../outside.mp4", ".git/x.mp4"])
def test_paths_outside_the_workspace_are_refused(studio, source):
    (studio.workspace.parent / "outside.mp4").write_bytes(MP4)

    result = studio.edit(inputs=[source])

    assert result == f"{source} isn't a file in the workspace (paths can't leave it or go through links)."
    assert studio.started == [] and studio.edits.list_edits() == []


def test_absolute_paths_outside_the_workspace_and_row_bot_s_data_are_refused(studio):
    outside = studio.workspace.parent / "outside.mp4"
    outside.write_bytes(MP4)
    private = studio.data / "media" / "secret.mp4"
    private.write_bytes(MP4)

    for path in (outside, private):
        assert studio.edit(inputs=[str(path)]) == f"{path} is outside the workspace; only files in it can be used."
    assert studio.edit(inputs=["missing.mp4"]).startswith("Couldn't find missing.mp4 in this chat's files or the "
                                                          "workspace.")
    assert studio.started == []


def test_a_link_out_of_the_workspace_is_refused(studio):
    outside = studio.workspace.parent / "outside.mp4"
    outside.write_bytes(MP4)
    try:
        os.symlink(outside, studio.workspace / "clips" / "linked.mp4")
    except (OSError, NotImplementedError):
        pytest.skip("This computer can't make symbolic links here.")

    assert studio.edit(inputs=["clips/linked.mp4"]) == (
        "clips/linked.mp4 isn't a file in the workspace (paths can't leave it or go through links).")
    assert studio.started == []


def test_workspace_videos_are_read_in_place_and_joined(studio):
    result = studio.edit(inputs=["demo.mp4", "clips/intro.mp4"])

    assert "Joined 2 videos" in result
    [run] = studio.started
    assert f"file:{studio.workspace / 'clips' / 'intro.mp4'}" in run["argv"]
    assert "file:input-1.mp4" in run["argv"]  # A chat file is copied in (checked) first.


def test_a_recording_or_edit_from_another_chat_is_refused(studio, monkeypatch):
    first = studio.edit(inputs=["demo.mp4"])
    edit_id = _edit_id(first)
    monkeypatch.setattr("row_bot.tool_outputs.current_thread_id", lambda: studio.other)

    assert studio.edit(inputs=[edit_id]) == f"{edit_id} isn't a video edit in this chat."
    assert studio.edit(inputs=["rec-0000abcd"]) == "rec-0000abcd isn't a recording in this chat."
    assert studio.edit(action="status", edit_id=edit_id) == f"{edit_id} isn't a video edit in this chat."
    assert studio.edit(action="save", edit_id=edit_id, path="x.mp4") == f"{edit_id} isn't a video edit in this chat."


def test_an_earlier_edit_can_be_edited_again_without_replacing_it(studio):
    from row_bot.application.attachments import list_conversation_files

    first = _edit_id(studio.edit(inputs=["demo.mp4"], name="demo"))
    second = studio.edit(inputs=[first], steps=[{"op": "text", "text": "v2"}], name="demo")

    assert ": saved" in second
    names = [item["name"] for item in list_conversation_files(studio.chat)]
    assert names.count("demo.mp4") == 3  # The original, the first edit and the second: none replaced.


def test_saving_into_the_workspace_writes_a_new_file_and_replacing_one_asks(studio):
    edit_id = _edit_id(studio.edit(inputs=["demo.mp4"], name="demo"))
    target = studio.workspace / "videos" / "demo.mp4"

    assert studio.edit(action="save", edit_id=edit_id, path="videos/demo.mp4") == \
        "Saved videos/demo.mp4 in the workspace."
    assert target.read_bytes() == MP4 and studio.asked == []

    target.write_bytes(b"the person's own video")
    studio.answer = False
    refused = studio.edit(action="save", edit_id=edit_id, path="videos/demo.mp4")
    [asked] = studio.asked
    assert asked["question"] == "Replace videos/demo.mp4 in the workspace?"
    assert "nothing was saved" in refused and target.read_bytes() == b"the person's own video"

    studio.answer = True
    assert studio.edit(action="save", edit_id=edit_id, path="videos/demo.mp4") == (
        "Approval: asked; approved by you\nSaved videos/demo.mp4 in the workspace.")
    assert target.read_bytes() == MP4

    studio.mode = "block"
    target.write_bytes(b"again")
    assert studio.edit(action="save", edit_id=edit_id, path="videos/demo.mp4").startswith("BLOCKED: Replacing")
    assert target.read_bytes() == b"again"
    assert studio.edit(action="save", edit_id=edit_id, path="../escape.mp4") == (
        "../escape.mp4 isn't a path Row-Bot can write in the workspace (paths can't leave it).")
    outside = studio.workspace.parent / "escape.mp4"
    assert studio.edit(action="save", edit_id=edit_id, path=str(outside)) == (
        f"{outside} is outside the workspace; only files in it can be used.")
    assert not outside.exists()


def test_frames_are_saved_as_stills_with_one_contact_sheet(studio):
    from row_bot.application.attachments import list_conversation_files

    result = studio.edit(action="frames", inputs=["demo.mp4"], count=3)

    assert ": saved 3 frames (at 0:01, 0:03, 0:05)" in result
    assert "files=['edit-" in result
    files = list_conversation_files(studio.chat)
    assert sorted(item["name"] for item in files if item["name"].startswith("frame")) == [
        "frame-01.jpg", "frame-02.jpg", "frame-03.jpg"]
    edit = studio.edits.get(_edit_id(result))
    view = studio.edits.view(edit)
    assert (view["kind"], view["frame_count"], view["mime_type"]) == ("frames", 3, "image/jpeg")


def test_a_failed_edit_says_what_ffmpeg_said(studio):
    studio.fail = "Error opening output file output.mp4: Permission denied"

    result = studio.edit(inputs=["demo.mp4"])

    assert result.endswith("failed. ffmpeg couldn't make it: [out#0] Error opening output file output.mp4: "
                           "Permission denied")
    edit = studio.edits.get(_edit_id(result))
    assert (edit.status, edit.problem) == ("failed", "edit_failed")


def test_a_result_too_large_for_the_chat_says_how_to_make_it_smaller(studio, monkeypatch):
    monkeypatch.setattr("row_bot.application.attachments.MAX_ATTACHMENT_BYTES", 100)

    result = studio.edit(inputs=["clips/intro.mp4"])

    assert "more than a chat can hold (25 MB)" in result
    assert studio.edits.get(_edit_id(result)).problem == "too_large"


def test_without_ffmpeg_the_edit_asks_to_download_it_first_and_shows_the_download(studio, monkeypatch):
    from row_bot import ffmpeg_runtime
    from row_bot.application.conversation_traces import specialize_tool_result
    from row_bot.tools import video_editing_tool as video_tool

    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: None)
    wheel = ffmpeg_runtime.WHEELS["windows-x86_64"]
    monkeypatch.setattr(ffmpeg_runtime, "current_wheel", lambda *_a, **_k: wheel)
    monkeypatch.setattr(video_tool, "INLINE_SECONDS", 0.05)
    release = threading.Event()
    # The download so far (the fake below reports nothing new until it is released).
    monkeypatch.setattr(ffmpeg_runtime, "_STATE", ffmpeg_runtime.DownloadState(
        status="downloading", received=12 * 1024 * 1024, total=wheel.size))

    def install(*, check):
        while not release.wait(0.01):
            check()
        return studio.ffmpeg

    monkeypatch.setattr(ffmpeg_runtime, "install", install)

    result = studio.edit(inputs=["demo.mp4"], steps=[{"op": "trim", "end": 3}])

    [asked] = studio.asked
    assert asked["question"] == "Video editing needs ffmpeg (about 30 MB) from PyPI. Download it now?"
    assert result.startswith("Approval: asked; approved by you\nVideo edit edit-")
    assert "getting ffmpeg, 12 MB of 30 MB" in result
    edit = studio.edits.get(_edit_id(result))
    view = studio.edits.view(edit)
    assert (view["status"], view["download_received"], view["download_total"]) == (
        "getting_ffmpeg", 12 * 1024 * 1024, wheel.size)
    trace = specialize_tool_result({"name": "edit_video", "content": result})
    assert (trace.kind, trace.edit_id) == ("video_edit", edit.edit_id)

    release.set()
    assert studio.edits.wait(edit, 5) and edit.status == "saved"


def test_a_denied_download_starts_nothing(studio, monkeypatch):
    from row_bot import ffmpeg_runtime

    monkeypatch.setattr(ffmpeg_runtime, "find", lambda: None)
    monkeypatch.setattr(ffmpeg_runtime, "current_wheel", lambda *_a, **_k: ffmpeg_runtime.WHEELS["linux-x86_64"])
    studio.answer = False

    result = studio.edit(inputs=["demo.mp4"])

    assert result == ("Approval: asked; denied by you — did not run\n"
                      "The person didn't allow downloading ffmpeg; nothing was downloaded or edited.")
    assert studio.edits.list_edits() == []


def test_edits_stop_with_their_conversation_and_a_crash_s_leftover_is_marked(studio, monkeypatch):
    from row_bot.tools import video_editing_tool as video_tool

    monkeypatch.setattr(video_tool, "INLINE_SECONDS", 0.05)
    studio.hold = True
    edit = studio.edits.get(_edit_id(studio.edit(inputs=["demo.mp4"])))

    assert studio.edits.stop_conversation(studio.chat) == 1
    assert (edit.status, edit.stopped_by) == ("stopped", "conversation")

    edit.status, edit.stopped_by = "editing", ""  # As if Row-Bot closed while it ran.
    studio.edits._save(edit)
    assert studio.edits.sweep_leftovers() == 1
    swept = studio.edits.get(edit.edit_id)
    assert (swept.status, swept.problem, swept.detail) == (
        "failed", "not_saved", "Row-Bot closed before the edit finished.")
    assert studio.edits.delete_conversation(studio.chat) == 1
    assert studio.edits.get(edit.edit_id) is None


def test_the_tool_waits_for_and_stops_an_edit_by_its_id(studio, monkeypatch):
    from row_bot.tools import video_editing_tool as video_tool

    monkeypatch.setattr(video_tool, "INLINE_SECONDS", 0.05)
    monkeypatch.setattr(video_tool, "STATUS_WAIT_SECONDS", 0.05)
    studio.hold = True
    edit_id = _edit_id(studio.edit(inputs=["demo.mp4"]))

    assert "carries on in the background" in studio.edit(action="status", edit_id=edit_id)
    assert studio.edit(action="stop", edit_id=edit_id).endswith("stopped by you; nothing was saved.")
