from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from row_bot.application import attachments, generated_media
from row_bot.application.attachment_context import AttachmentExecutionCaches
from row_bot.api.v1.schemas import MediaAvailable, MediaError
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401

pytestmark = pytest.mark.subsystem


@pytest.fixture
def conversation(service):
    from row_bot import threads
    threads._save_thread_meta("fixture", "Fixture")
    folder = threads._MEDIA_DIR / "fixture"
    folder.mkdir()
    return folder


def test_generated_outputs_use_owned_opaque_refs_and_leave_original_video(conversation):
    image = b"\x89PNG\r\n\x1a\nfixture"
    video = b"\x00\x00\x00\x18ftypisom" + b"fixture"
    source = conversation / "private-provider-file.mp4"
    source.write_bytes(video)
    cache = AttachmentExecutionCaches("fixture", pending_image=base64.b64encode(image).decode(),
                                      pending_video={"path": str(source), "provider": "private"})
    results = generated_media.capture_generated_media("fixture", cache)
    assert [item["type"] for item in results] == ["media.available", "media.available"]
    for result, content in zip(results, (image, video)):
        MediaAvailable.model_validate(result["payload"])
        assert attachments.read_attachment(result["payload"]["media_ref"])[1] == content
    assert results[1]["payload"]["mime_type"] == "video/mp4"
    assert source.read_bytes() == video
    assert "private" not in json.dumps(results)
    assert str(conversation) not in json.dumps(results)
    assert cache.pending_image is None and cache.pending_video is None
    assert generated_media.capture_generated_media("fixture", cache) == []


def test_a_shown_picture_keeps_the_name_its_tool_gave_it(conversation):
    """A screenshot is saved as screenshot-<time>.png, so a later step can tell it from another picture."""
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()
    jpeg = base64.b64encode(b"\xff\xd8\xfffixture").decode()
    named = AttachmentExecutionCaches("fixture", pending_image=png,
                                      pending_image_name="screenshot-2026-10-29-115700.png")
    wrong_suffix = AttachmentExecutionCaches("fixture", pending_image=jpeg, pending_image_name="screenshot-1.png")
    unnamed = AttachmentExecutionCaches("fixture", pending_image=png)

    refs = [generated_media.capture_generated_media("fixture", cache)[0]["payload"]["media_ref"]
            for cache in (named, wrong_suffix, unnamed)]

    assert [attachments.read_attachment(ref)[0]["name"] for ref in refs] == [
        "screenshot-2026-10-29-115700.png", "screenshot-1.jpg", "generated-image.png"]  # The bytes decide the type.
    assert named.pending_image_name == ""


def test_oversized_video_preflight_never_reads_or_deletes_source(conversation, monkeypatch):
    source = conversation / "fixture.mp4"
    data = b"\x00\x00\x00\x18ftypisom" + b"fixture"
    source.write_bytes(data)
    monkeypatch.setattr(generated_media, "MAX_ATTACHMENT_BYTES", 12)
    original_open = generated_media._open
    class SizeOnly:
        def __init__(self):
            self.file = original_open(conversation, source)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.file.close()
        def fileno(self):
            return self.file.fileno()
        def read(self, size):
            pytest.fail("Oversized generated video read before size admission")
    monkeypatch.setattr(generated_media, "_open", lambda *args: SizeOnly())
    result = generated_media.capture_generated_media("fixture", AttachmentExecutionCaches(
        "fixture", pending_video={"path": str(source)}))
    assert result == [{"type": "media.error", "payload": {"code": "payload_too_large"}}]
    MediaError.model_validate(result[0]["payload"])
    assert source.read_bytes() == data


def test_other_conversation_video_is_rejected_without_open_and_image_success_survives(conversation, monkeypatch):
    foreign = conversation.parent / "other"
    foreign.mkdir()
    source = foreign / "private.mp4"
    source.write_bytes(b"\x00\x00\x00\x18ftypisom")
    monkeypatch.setattr(generated_media, "_open", lambda *args: pytest.fail("Opened a foreign media path"))
    cache = AttachmentExecutionCaches("fixture", pending_image=base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode(),
                                      pending_video={"path": str(source)})
    outcomes = generated_media.capture_generated_media("fixture", cache)
    assert outcomes[0]["type"] == "media.available"
    assert outcomes[1] == {"type": "media.error", "payload": {"code": "media_unavailable"}}
    assert "private" not in json.dumps(outcomes)


def test_reparse_generated_video_and_foreign_cache_fail_closed(conversation, monkeypatch):
    source = conversation / "video.mp4"
    source.write_bytes(b"\x00\x00\x00\x18ftypisom")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == source or original(path))
    for cache in (AttachmentExecutionCaches("fixture", pending_video={"path": str(source)}),
                  AttachmentExecutionCaches("other", pending_image="aW1hZ2U=")):
        assert generated_media.capture_generated_media("fixture", cache) == [
            {"type": "media.error", "payload": {"code": "media_unavailable"}}]
    assert source.read_bytes() == b"\x00\x00\x00\x18ftypisom"


def test_invalid_and_oversized_encoded_image_fail_without_registering(conversation, monkeypatch):
    monkeypatch.setattr(generated_media, "MAX_ATTACHMENT_BYTES", 3)
    monkeypatch.setattr(generated_media, "register_attachment", lambda *args: pytest.fail("Registered rejected bytes"))
    for value, expected in (("not base64!", "payload_too_large"), ("!!!!", "media_unavailable"),
                            (base64.b64encode(b"1234").decode(), "payload_too_large")):
        result = generated_media.capture_generated_media("fixture", AttachmentExecutionCaches("fixture", pending_image=value))
        assert result == [{"type": "media.error", "payload": {"code": expected}}]


def test_parallel_original_generated_outputs_have_distinct_exclusive_owners(conversation, monkeypatch):
    barrier = Barrier(2)
    original_write = generated_media._write
    def synchronized_write(root, path, data):
        barrier.wait(timeout=10)
        return original_write(root, path, data)
    monkeypatch.setattr(generated_media, "_write", synchronized_write)
    def save(data):
        return generated_media.save_generated_output("fixture", data, prefix="vid", extension="mp4")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, (b"first original", b"second original")))
    assert len(set(results)) == 2
    assert [Path(path).read_bytes() for path in results] == [b"first original", b"second original"]
    assert all(Path(path).parent == conversation and Path(path).name.startswith("vid_") for path in results)


def test_original_save_preserves_large_file_and_capture_limit_is_separate(conversation, monkeypatch):
    data = b"\x00\x00\x00\x18ftypisom" + b"larger than fixture transport bound"
    monkeypatch.setattr(generated_media, "MAX_ATTACHMENT_BYTES", 12)
    path = generated_media.save_generated_output("fixture", data, prefix="vid", extension="mp4")
    result = generated_media.capture_generated_media("fixture", AttachmentExecutionCaches(
        "fixture", pending_video={"path": path}))
    assert result == [{"type": "media.error", "payload": {"code": "payload_too_large"}}]
    assert Path(path).read_bytes() == data


@pytest.mark.parametrize("prefix,extension", [("../outside", "mp4"), ("vid", "../mp4"), ("vid", ".mp4")])
def test_original_save_rejects_path_components(conversation, prefix, extension):
    with pytest.raises(attachments.AttachmentError, match="invalid_command"):
        generated_media.save_generated_output("fixture", b"fixture", prefix=prefix, extension=extension)
    assert list(conversation.iterdir()) == []


def _video_bytes(number: int) -> bytes:
    return b"\x00\x00\x00\x18ftypisom" + f"shot {number}".encode()


def test_five_videos_finishing_at_once_in_one_chat_get_five_names_each_found_by_its_own(conversation):
    """7 Nov: five shots all arrived as generated-video.mp4, and the editor could only ever open one of them."""
    from row_bot import media_sources

    barrier = Barrier(5)

    def finish(number: int) -> str:
        path = generated_media.save_generated_output("fixture", _video_bytes(number), prefix="vid", extension="mp4")
        cache = AttachmentExecutionCaches("fixture", pending_video={"path": path})
        barrier.wait(timeout=10)
        [outcome] = generated_media.capture_generated_media("fixture", cache)
        return outcome["payload"]["media_ref"]

    with ThreadPoolExecutor(max_workers=5) as pool:
        refs = list(pool.map(finish, range(1, 6)))

    names = {item["attachment_ref"]: item["name"] for item in attachments.list_conversation_files("fixture")}
    assert sorted(names[ref] for ref in refs) == [
        "generated-video-2.mp4", "generated-video-3.mp4", "generated-video-4.mp4", "generated-video-5.mp4",
        "generated-video.mp4"]
    for number, ref in enumerate(refs, start=1):
        [found] = media_sources.resolve(names[ref], "fixture")
        assert (found.label, found.read_bytes()) == (names[ref], _video_bytes(number))


def test_a_picture_whose_name_is_taken_gets_the_next_free_one(conversation):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()
    caches = [AttachmentExecutionCaches("fixture", pending_image=png, pending_image_name="screenshot.png"),
              AttachmentExecutionCaches("fixture", pending_image=png, pending_image_name="Screenshot.png"),
              AttachmentExecutionCaches("fixture", pending_image=png),
              AttachmentExecutionCaches("fixture", pending_image=png)]

    refs = [generated_media.capture_generated_media("fixture", cache)[0]["payload"]["media_ref"] for cache in caches]

    assert [attachments.read_attachment(ref)[0]["name"] for ref in refs] == [
        "screenshot.png", "Screenshot-2.png", "generated-image.png", "generated-image-2.png"]


def test_files_added_under_one_name_each_get_their_own(conversation):
    """A second photo.jpg added to a chat is photo-2.jpg, and the client is told so."""
    first = attachments.register_attachment("fixture", "photo.jpg", b"\xff\xd8\xfffirst")
    second = attachments.register_attachment("fixture", "photo.jpg", b"\xff\xd8\xffsecond")
    unnamed_type = attachments.register_attachment("fixture", "notes", b"notes")
    again = attachments.register_attachment("fixture", "notes", b"more notes")

    assert [first["name"], second["name"], unnamed_type["name"], again["name"]] == [
        "photo.jpg", "photo-2.jpg", "notes", "notes-2"]
    assert {item["attachment_ref"]: item["name"] for item in attachments.list_conversation_files("fixture")} == {
        saved["attachment_ref"]: saved["name"] for saved in (first, second, unnamed_type, again)}


def _kept_before_names_were_unique(folder: Path, name: str, data: bytes, at: int) -> str:
    """A file as Row-Bot kept it before each file had its own name: its record is left exactly as it was."""
    attachment_id = str(uuid4())
    ref = f"fixture:{attachment_id}"
    content = folder / f"attachment_{attachment_id}.bin"
    content.write_bytes(data)
    record = folder / f"attachment_{attachment_id}.json"
    record.write_text(json.dumps({"attachment_ref": ref, "name": name, "mime_type": "video/mp4",
                                  "size_bytes": len(data), "revision": "1",
                                  "sha256": hashlib.sha256(data).hexdigest()}), encoding="utf-8")
    for path in (content, record):
        os.utime(path, ns=(at, at))
    return ref


def test_a_chat_that_already_holds_same_named_videos_can_name_each_one(conversation):
    from row_bot import media_library, media_sources

    second = 1_000_000_000
    refs = [_kept_before_names_were_unique(conversation, "generated-video.mp4", _video_bytes(number),
                                           1_700_000_000 * second + number * second) for number in range(1, 6)]
    records = {path: path.read_bytes() for path in conversation.glob("*.json")}

    names = {item["attachment_ref"]: item["name"] for item in attachments.list_conversation_files("fixture")}
    # The newest keeps the name, so it still finds what it found before; the older ones count back from it.
    assert [names[ref] for ref in refs] == [
        "generated-video-5.mp4", "generated-video-4.mp4", "generated-video-3.mp4", "generated-video-2.mp4",
        "generated-video.mp4"]
    for number, ref in enumerate(refs, start=1):
        [found] = media_sources.resolve(names[ref], "fixture")
        assert found.read_bytes() == _video_bytes(number)
    assert [item["name"] for item in media_library.items(kind="video")] == [
        "generated-video.mp4", "generated-video-2.mp4", "generated-video-3.mp4", "generated-video-4.mp4",
        "generated-video-5.mp4"]
    assert media_library.bring(refs[0], "fixture")["name"] == "generated-video-5.mp4"

    original = generated_media.save_generated_output("fixture", _video_bytes(6), prefix="vid", extension="mp4")
    [outcome] = generated_media.capture_generated_media("fixture", AttachmentExecutionCaches(
        "fixture", pending_video={"path": original}))
    after = {item["attachment_ref"]: item["name"] for item in attachments.list_conversation_files("fixture")}
    assert after == {**names, outcome["payload"]["media_ref"]: "generated-video-6.mp4"}
    assert {path: path.read_bytes() for path in records} == records



def _run_tool(name: str, args: dict, run) -> str:
    """One tool call through the platform's tool wrapper, in the fixture chat; the stored result."""
    import contextvars

    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    from row_bot import agent
    from row_bot.application import attachment_context

    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([StructuredTool.from_function(func=run, name=name, description="A test tool.")],
                                     wrap_tool_call=agent._wrap_platform_tool_call))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    call = {"id": "call-0", "name": name, "args": args, "type": "tool_call"}

    def invoke() -> dict:
        agent._current_thread_id_var.set("fixture")
        attachment_context._current.set(AttachmentExecutionCaches("fixture"))
        return graph.compile().invoke({"messages": [AIMessage(content="", tool_calls=[call], id="ai-0")]})

    return str(contextvars.copy_context().run(invoke)["messages"][-1].content)


def test_a_made_video_is_named_by_its_chat_file_never_its_private_path(conversation):
    """R2 (12 Nov): generate_video said "Saved to: <private path>", which analyze_image refused, and every
    clip was generated-video.mp4."""
    from row_bot import media_sources
    from row_bot.tools import video_gen_tool

    saved: list[str] = []

    def generate_video(prompt: str) -> str:
        path = generated_media.save_generated_output("fixture", _video_bytes(1), prefix="vid", extension="mp4")
        saved.append(path)
        video_gen_tool._set_pending_video({"path": path, "provider": "Fixture"})
        return f"Video generated successfully. Provider: Fixture\nSaved to: {path}"

    text = _run_tool("generate_video", {"prompt": "Waves on a beach at dusk, slow pan"}, generate_video)

    assert saved and saved[0] not in text and "Saved to:" not in text
    assert "“waves-on-a-beach-at-dusk.mp4”" in text
    [found] = media_sources.resolve("waves-on-a-beach-at-dusk.mp4", "fixture")
    assert found.read_bytes() == _video_bytes(1)


def test_analyze_image_finds_a_chat_file_named_as_its_file_path(conversation, monkeypatch):
    """R2: the video tool's review sheet is a chat file; file_path looked only in the workspace."""
    from row_bot.tools import vision_tool

    looked: list[list[str]] = []
    monkeypatch.setattr(vision_tool, "_analyze_files",
                        lambda question, files, sheet, frames: looked.append(files) or "Seen.")
    monkeypatch.setattr(vision_tool, "_get_vision_service", lambda: type("Service", (), {
        "capture_and_analyze": staticmethod(lambda question, source, file_path: f"Image file not found: {file_path}"),
    })())

    assert vision_tool._analyze_image("Is the text legible?", source="file",
                                      file_path="Launch v1 review.jpg") == "Seen."
    assert looked == [["Launch v1 review.jpg"]]


def test_a_later_turn_edits_the_chats_last_picture_or_one_by_its_name(conversation, monkeypatch):
    """T8: "make it night" in the next turn couldn't find the picture the turn before made."""
    from row_bot.application.attachments import register_attachment
    from row_bot.tools import image_gen_tool

    first = b"\x89PNG\r\n\x1a\nfirst-picture"
    second = b"\x89PNG\r\n\x1a\nsecond-picture"
    register_attachment("fixture", "lighthouse-at-dawn.png", first)
    register_attachment("fixture", "lighthouse-at-noon.png", second)
    monkeypatch.setattr("row_bot.tool_outputs.current_thread_id", lambda: "fixture")
    # A new turn: nothing in this turn's own cache.
    monkeypatch.setattr(image_gen_tool, "_execution_image_cache", lambda: {})

    assert image_gen_tool._resolve_image_source("last") == second
    assert image_gen_tool._resolve_image_source("lighthouse-at-dawn.png") == first
