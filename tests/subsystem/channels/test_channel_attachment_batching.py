"""Several attachments sent together become one agent turn (F53).

Each image keeps its own vision pre-call and each voice note its own
transcription; only their text joins the conversation, never the images.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from row_bot.channels.media import AttachmentCoalescer, InboundAttachment


pytestmark = pytest.mark.subsystem


@pytest.fixture
def readers(monkeypatch):
    """Vision, transcription and file saving, recorded instead of run."""
    import row_bot.tools.image_gen_tool as image_tools
    from row_bot.channels import media

    calls: dict[str, list[Any]] = {"vision": [], "transcribe": [], "saved": []}
    # Inbound images are offered to the image tools; keep that cache to this test.
    monkeypatch.setattr(image_tools, "_image_cache", {})
    monkeypatch.setattr(image_tools, "_image_cache_thread_id", None)

    def analyze(data: bytes, question: str) -> str:
        calls["vision"].append((data, question))
        return f"a picture of {data.decode()}"

    def transcribe(data: bytes, _ext: str) -> str:
        calls["transcribe"].append(data)
        return f"spoken {data.decode()}"

    def save(data: bytes, filename: str):
        calls["saved"].append(filename)
        return f"/inbox/{filename}"

    monkeypatch.setattr(media, "analyze_image", analyze)
    monkeypatch.setattr(media, "transcribe_audio", transcribe)
    monkeypatch.setattr(media, "save_inbound_file", save)
    monkeypatch.setattr(media, "copy_to_workspace", lambda saved: f"Received Files/{saved.rsplit('/', 1)[-1]}")
    return calls


def test_one_message_with_several_attachments_is_one_user_message(readers) -> None:
    from row_bot.channels.media import build_attachment_turn

    turn = build_attachment_turn([
        InboundAttachment("cat.jpg", b"cat", "image/jpeg"),
        InboundAttachment("note1.ogg", b"first", "audio/ogg"),
        InboundAttachment("dog.jpg", b"dog", "image/jpeg"),
        InboundAttachment("note2.ogg", b"second", "audio/ogg"),
        InboundAttachment("notes.txt", b"plain text body", "text/plain"),
    ], "which one is cuter?")

    assert turn.usable is True
    assert readers["vision"] == [(b"cat", "which one is cuter?"), (b"dog", "which one is cuter?")]
    assert readers["transcribe"] == [b"first", b"second"]
    assert turn.text.startswith(
        "which one is cuter?\n\n"
        "[Voice message 1 of 2 transcription]: spoken first\n\n"
        "[Voice message 2 of 2 transcription]: spoken second\n\n"
        "<row_bot_attachment_context>\n"
        "[Attached image: cat.jpg — ALREADY ANALYZED, do NOT call analyze_image]\na picture of cat"
    )
    assert "[Attached image: dog.jpg — ALREADY ANALYZED" in turn.text
    assert "[Attached file: notes.txt]\nWorkspace path: Received Files/" in turn.text
    assert "plain text body" in turn.text


def test_images_enter_the_conversation_only_as_their_descriptions(readers) -> None:
    from row_bot.channels.media import build_attachment_turn
    from row_bot.message_projection import strip_file_context

    raw = b"\x89PNG-binary-pixels"
    turn = build_attachment_turn([InboundAttachment("shot.png", raw, "image/png")], "")

    assert "binary-pixels" not in turn.text and "base64" not in turn.text
    assert readers["vision"] == [(raw, "Describe this image in detail.")]
    # The app shows the attachment as a badge, as for the desktop client.
    assert strip_file_context(turn.text) == "📎 shot.png"


def test_album_items_named_alike_keep_their_own_names(readers) -> None:
    from row_bot.channels.media import build_attachment_turn

    cached: list[str] = []
    turn = build_attachment_turn(
        [InboundAttachment("image.jpg", b"one", "image/jpeg"), InboundAttachment("image.jpg", b"two", "image/jpeg")],
        "",
        cache_image=lambda name, _data: cached.append(name) or name,
    )

    assert cached == ["image.jpg", "image_2.jpg"]
    assert "[Attached image: image_2.jpg —" in turn.text


def test_nothing_readable_and_no_words_is_not_a_turn(readers, monkeypatch) -> None:
    from row_bot.channels import media

    monkeypatch.setattr(media, "analyze_image", lambda _data, _question: "Vision analysis failed: no model")
    monkeypatch.setattr(media, "transcribe_audio", lambda _data, _ext: "")

    silent = media.build_attachment_turn(
        [InboundAttachment("a.png", b"a", "image/png"), InboundAttachment("b.ogg", b"b", "audio/ogg")],
        "",
    )
    worded = media.build_attachment_turn([InboundAttachment("a.png", b"a", "image/png")], "look")

    assert silent.usable is False
    assert "[Attached image: a.png - vision analysis failed]" in silent.text
    assert "[Attached audio: b.ogg - could not be transcribed]" in silent.text
    assert worded.usable is True


# ── Albums that arrive as separate messages ──────────────────────────


def _collector():
    ready = threading.Event()
    batches: list[tuple[str, list[Any]]] = []

    def on_ready(key: str, items: list[Any]) -> None:
        batches.append((key, items))
        ready.set()

    return ready, batches, on_ready


def test_items_announced_before_the_batch_closes_join_it_in_arrival_order() -> None:
    ready, batches, on_ready = _collector()
    coalescer = AttachmentCoalescer(on_ready, settle_s=0)

    orders = [coalescer.begin("chat-1") for _ in range(3)]
    for order, item in zip(reversed(orders), ["third", "second", "first"]):
        coalescer.add("chat-1", order, item)

    assert ready.wait(10)
    assert batches == [("chat-1", ["first", "second", "third"])]


def test_an_item_that_never_downloads_does_not_hold_its_batch() -> None:
    ready, batches, on_ready = _collector()
    coalescer = AttachmentCoalescer(on_ready, settle_s=0)

    kept = coalescer.begin("chat-1")
    coalescer.begin("chat-1")
    coalescer.add("chat-1", kept, "photo")
    coalescer.cancel("chat-1")

    assert ready.wait(10)
    assert batches == [("chat-1", ["photo"])]


class _RecordingBatches:
    def __init__(self) -> None:
        self.items: list[tuple[str, Any]] = []
        self._order = 0

    def begin(self, key: str) -> int:
        self._order += 1
        return self._order

    def add(self, key: str, _order: int, item: Any) -> None:
        self.items.append((key, item))

    def cancel(self, key: str) -> None:
        return None


@pytest.fixture
def store(tmp_path, reload_for_data_dir):
    tasks, threads = reload_for_data_dir(tmp_path / "data", "row_bot.tasks", "row_bot.threads")
    return SimpleNamespace(tasks=tasks, threads=threads)


def test_whatsapp_media_waits_for_its_album_and_the_album_is_one_turn(store, readers, monkeypatch) -> None:
    import base64

    from row_bot.channels import whatsapp

    batches = _RecordingBatches()
    texts: list[str] = []
    monkeypatch.setattr(whatsapp, "_media_batches", batches)
    monkeypatch.setattr(whatsapp, "_is_authorised", lambda _chat: True)
    monkeypatch.setattr(whatsapp, "_run_text_turn", lambda _chat, text, _key, **_kw: texts.append(text))

    for data in (b"cat", b"dog"):
        whatsapp._process_inbound({
            "from": "chat@s.whatsapp.net", "body": "both?", "hasMedia": True,
            "mediaType": "image/jpeg", "filename": "image.jpg",
            "mediaData": base64.b64encode(data).decode(), "msgKey": {"id": data.decode()},
        })
    assert texts == []  # nothing runs per item
    whatsapp._run_media_batch("chat@s.whatsapp.net", [item for _key, item in batches.items])

    (text,) = texts
    assert text.startswith("both?\n\n<row_bot_attachment_context>")
    assert "a picture of cat" in text and "a picture of dog" in text
    assert "[Attached image: image_2.jpg —" in text


def test_telegram_album_is_one_turn(store, readers, monkeypatch) -> None:
    from row_bot.channels import telegram

    texts: list[str] = []

    async def run_agent(_update, _context, text: str) -> None:
        texts.append(text)

    monkeypatch.setattr(telegram, "_run_agent_for_message", run_agent)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=77), message=SimpleNamespace())
    context = SimpleNamespace(chat_data={})
    items = [
        telegram._AlbumItem(InboundAttachment(name, data, "image/jpeg"), caption, update, context)
        for name, data, caption in (("p1.jpg", b"cat", "our pets"), ("p2.jpg", b"dog", ""))
    ]

    asyncio.run(telegram._answer_album(items))

    (text,) = texts
    assert text.startswith("our pets\n\n<row_bot_attachment_context>")
    assert "a picture of cat" in text and "a picture of dog" in text
    assert readers["vision"] == [(b"cat", "our pets"), (b"dog", "our pets")]


def test_a_telegram_photo_in_an_album_waits_for_the_rest(store, monkeypatch) -> None:
    from row_bot.channels import telegram

    batches = _RecordingBatches()
    ran: list[str] = []

    async def run_agent(*_args: Any) -> None:
        ran.append("turn")

    class _File:
        async def download_as_bytearray(self) -> bytearray:
            return bytearray(b"pixels")

    class _Photo:
        file_unique_id = "u1"

        async def get_file(self) -> _File:
            return _File()

    monkeypatch.setattr(telegram, "_album_batches", lambda: batches)
    monkeypatch.setattr(telegram, "_is_authorised", lambda _update: True)
    monkeypatch.setattr(telegram, "_run_agent_for_message", run_agent)
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=77),
        message=SimpleNamespace(photo=[_Photo()], caption="look", media_group_id="g1"),
    )

    asyncio.run(telegram._handle_photo(update, SimpleNamespace(chat_data={})))

    assert ran == []
    ((key, item),) = batches.items
    assert key == "77:g1"
    assert item.attachment == InboundAttachment("telegram_photo_u1.jpg", b"pixels", "image/jpeg")
    assert item.caption == "look"


# ── A lone Telegram attachment uses the same attachment block (E1a decision 6) ──


class _TelegramMessage:
    def __init__(self, *, caption: str = "", **media: Any) -> None:
        self.caption = caption
        self.media_group_id = None
        self.replies: list[str] = []
        self.photo = media.get("photo")
        self.voice = media.get("voice")
        self.audio = None
        self.document = media.get("document")

    async def reply_text(self, text: str, **_kwargs: Any) -> None:
        self.replies.append(text)


class _TelegramFile:
    def __init__(self, data: bytes, **fields: Any) -> None:
        self._data = data
        for key, value in fields.items():
            setattr(self, key, value)

    async def get_file(self) -> "_TelegramFile":
        return self

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(self._data)


def _single(monkeypatch, message: _TelegramMessage) -> tuple[Any, list[str]]:
    from row_bot.channels import telegram

    texts: list[str] = []

    async def run_agent(_update, _context, text: str, **_kwargs: Any) -> None:
        texts.append(text)

    async def react(*_args: Any) -> None:
        return None

    monkeypatch.setattr(telegram, "_is_authorised", lambda _update: True)
    monkeypatch.setattr(telegram, "_run_agent_for_message", run_agent)
    monkeypatch.setattr(telegram, "_react", react)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=77), message=message)
    return update, texts


def test_a_lone_telegram_photo_is_the_attachment_block_after_the_caption(store, readers, monkeypatch) -> None:
    import row_bot.tools.image_gen_tool as image_tools
    from row_bot.channels import telegram
    from row_bot.message_projection import strip_file_context

    message = _TelegramMessage(caption="what breed?", photo=[_TelegramFile(b"cat", file_unique_id="u1")])
    update, texts = _single(monkeypatch, message)

    asyncio.run(telegram._handle_photo(update, SimpleNamespace(chat_data={})))

    (text,) = texts
    assert text.startswith(
        "what breed?\n\n<row_bot_attachment_context>\n"
        "[Attached image: telegram_photo_u1.jpg — ALREADY ANALYZED, do NOT call analyze_image]\na picture of cat"
    )
    assert "User said:" not in text
    assert readers["vision"] == [(b"cat", "what breed?")]
    assert image_tools._image_cache == {"telegram_photo_u1.jpg": b"cat"}
    # The app shows the photo as a badge with the person's words, as for the desktop client.
    assert strip_file_context(text) == "📎 telegram_photo_u1.jpg\n\nwhat breed?"


def test_a_lone_telegram_document_is_the_attachment_block(store, readers, monkeypatch) -> None:
    from row_bot.channels import media, telegram

    monkeypatch.setattr(media, "extract_document_text", lambda data, _name, **_kw: data.decode())
    message = _TelegramMessage(
        caption="summarise this",
        document=_TelegramFile(b"plain text body", file_name="notes.txt", mime_type="text/plain"),
    )
    update, texts = _single(monkeypatch, message)

    asyncio.run(telegram._handle_document(update, SimpleNamespace(chat_data={})))

    (text,) = texts
    assert text.startswith("summarise this\n\n<row_bot_attachment_context>\n[Attached file: notes.txt]\n")
    assert "Workspace path: Received Files/notes.txt" in text and "plain text body" in text
    assert "[User sent a file:" not in text and "User's message:" not in text
    assert readers["saved"] == ["notes.txt"]


def test_a_lone_telegram_voice_note_is_its_transcription_and_echoed(store, readers, monkeypatch) -> None:
    from row_bot.channels import telegram

    message = _TelegramMessage(voice=_TelegramFile(b"hello there", mime_type="audio/ogg"))
    update, texts = _single(monkeypatch, message)

    asyncio.run(telegram._handle_voice(update, SimpleNamespace(chat_data={})))

    assert texts == ["[Voice message transcription]: spoken hello there"]
    assert message.replies == ["🎤 _spoken hello there_"]


def test_an_unreadable_lone_telegram_attachment_starts_no_turn(store, readers, monkeypatch) -> None:
    from row_bot.channels import media, telegram

    monkeypatch.setattr(media, "transcribe_audio", lambda _data, _ext: "")
    message = _TelegramMessage(voice=_TelegramFile(b"noise", mime_type="audio/ogg"))
    update, texts = _single(monkeypatch, message)

    asyncio.run(telegram._handle_voice(update, SimpleNamespace(chat_data={})))

    assert texts == []
    assert message.replies == ["⚠️ Could not transcribe your voice note."]
