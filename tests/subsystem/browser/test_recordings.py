"""Recording Row-Bot's browser (F2): starting asks by the approval level, stopping never does, and a
recording stops at its time limit and before its video outgrows the chat.

The approval tests use a fake capture (nothing records); the encoder tests write a real H.264
MP4 from a few pictures through PyAV, with a clock that runs fast.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from row_bot import recordings
from row_bot.approval_review import ReviewVerdict
from row_bot.browser.recording import PageRecorder, encoder_problem
from row_bot.browser.service import BrowserSessionManager
from row_bot.tools import approval_gate, browser_tool

pytestmark = pytest.mark.subsystem


class _Capture:
    def __init__(self, on_end):
        self.on_end = on_end
        self.stopped: list[str] = []

    def request_stop(self, reason: str) -> None:
        self.stopped.append(reason)
        self.on_end(reason, None)


@pytest.fixture
def browser(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    manager = BrowserSessionManager()
    monkeypatch.setattr(browser_tool, "_session_manager", manager)
    state = SimpleNamespace(mode="approve", answer=True, asked=[], reviewed=[], captures=[],
                            verdict=ReviewVerdict("approve", "You asked for a recording of the sign-up."))
    monkeypatch.setattr(browser_tool, "_get_thread_id", lambda: "conversation-a")
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: state.mode)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: state.asked.append(payload) or state.answer)
    monkeypatch.setattr("row_bot.approval_review.review",
                        lambda payload, **_kw: state.reviewed.append(payload) or state.verdict)
    monkeypatch.setattr("row_bot.browser.recording.encoder_problem", lambda: "")
    session = manager.get_session("conversation-a")
    monkeypatch.setattr(session, "current_url", lambda _thread="default": "http://localhost:5173/signup")

    def start_recording(path, *, max_seconds, size_limit, on_end, thread_id="default"):
        capture = _Capture(on_end)
        state.captures.append((thread_id, max_seconds, size_limit, capture))
        return capture

    monkeypatch.setattr(session, "start_recording", start_recording)
    yield state
    recordings.stop_all(timeout=1)
    manager.kill_all()


def _record(action: str, **kwargs) -> str:
    tools = {tool.name: tool for tool in browser_tool.BrowserTool().as_langchain_tools()}
    return tools["browser_record"].func(action, **kwargs)


def test_ask_asks_before_recording_and_says_what_is_recorded(browser):
    result = _record("start", max_minutes=3)

    [asked] = browser.asked
    assert asked["question"] == "Record Row-Bot's browser in this chat?"
    assert "for up to 3 minutes" in asked["description"] and "Password and card fields are hidden" in asked["description"]
    assert result.startswith("Recording rec-")
    [(conversation, max_seconds, size_limit, _capture)] = browser.captures
    assert (conversation, max_seconds, size_limit) == ("conversation-a", 180, recordings.SIZE_LIMIT)


def test_a_denied_recording_never_starts(browser):
    browser.answer = False
    result = _record("start")

    assert "didn't allow recording" in result
    assert browser.captures == [] and recordings.list_recordings("conversation-a") == []


def test_auto_asks_the_reviewer_and_hands_off_doesnt_ask(browser):
    browser.mode = "auto"
    _record("start")
    assert browser.asked == [] and len(browser.reviewed) == 1
    _record("stop")

    browser.mode = "full"
    _record("start")
    assert browser.asked == [] and len(browser.reviewed) == 1
    assert len(browser.captures) == 2


def test_block_refuses_recording(browser):
    browser.mode = "block"
    result = _record("start")

    assert result == "BLOCKED: Recording needs approval, and this chat is on Block."
    assert browser.captures == []


def test_stopping_never_asks_and_says_what_happened(browser):
    _record("start")
    browser.mode = "block"  # Even on Block, a stop goes through.
    result = _record("stop")

    [(_, _, _, capture)] = browser.captures
    assert capture.stopped == ["agent"]
    assert result.startswith("Recording rec-") and "nothing was recorded" in result
    [record] = recordings.list_recordings("conversation-a")
    assert (record.status, record.stopped_by, record.problem) == ("failed", "agent", "nothing_recorded")
    assert len(browser.asked) == 1


def test_one_browser_recording_at_a_time_per_chat(browser):
    first = _record("start")
    second = _record("start")

    assert second == first.split(" Do the steps")[0]
    assert len(browser.captures) == 1 and len(browser.asked) == 1


def test_without_a_page_there_is_nothing_to_record(browser, monkeypatch):
    session = browser_tool._session_manager.get_session("conversation-a")
    monkeypatch.setattr(session, "current_url", lambda _thread="default": "")

    assert _record("start").startswith("Open a page with browser_navigate first")
    assert browser.asked == []


def test_a_copy_without_video_support_says_so_plainly(browser, monkeypatch):
    monkeypatch.setattr("row_bot.browser.recording.encoder_problem",
                        lambda: "Recording needs Row-Bot's video support, which isn't installed in this copy.")

    assert _record("start").startswith("Recording needs Row-Bot's video support")
    assert browser.asked == []


# ── The encoder ─────────────────────────────────────────────────────────────

needs_encoder = pytest.mark.skipif(bool(encoder_problem()), reason="PyAV with H.264 isn't installed")


def _jpeg(colour: tuple[int, int, int], size=(320, 200)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG")
    return buffer.getvalue()


class _Clock:
    def __init__(self, step: float) -> None:
        self.now, self.step = 0.0, step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@needs_encoder
def test_a_recording_stops_at_its_time_limit_with_a_playable_video(tmp_path):
    ended: list[tuple[str, Path | None]] = []
    recorder = PageRecorder(tmp_path / "rec.mp4", max_seconds=2, size_limit=10_000_000,
                            on_end=lambda reason, video: ended.append((reason, video)), clock=_Clock(0.25), tick=0.001)
    recorder.offer_frame(_jpeg((200, 30, 30)))
    recorder.start()
    assert recorder.join(10)

    [(reason, video)] = ended
    assert reason == "limit" and video == tmp_path / "rec.mp4"
    data = video.read_bytes()
    assert data[4:8] == b"ftyp"  # An MP4 a chat can show.
    import av

    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        assert stream.codec_context.name == "h264"
        assert (stream.codec_context.width, stream.codec_context.height) == (320, 200)


@needs_encoder
def test_a_recording_keeps_its_size_whatever_size_its_pictures_come_at(tmp_path):
    """A narrow picture first (the page at a narrow pane's size for a moment) never sets the video's shape."""
    ended: list[tuple[str, Path | None]] = []
    clock = _Clock(0.05)
    recorder = PageRecorder(tmp_path / "rec.mp4", max_seconds=1, size_limit=10_000_000, size=(1920, 1080),
                            on_end=lambda reason, video: ended.append((reason, video)), clock=clock, tick=0.001)
    recorder.offer_frame(_jpeg((200, 30, 30), size=(632, 1840)))
    recorder.start()
    while clock.now < 0.5:
        time.sleep(0.001)
    recorder.offer_frame(_jpeg((30, 30, 200), size=(1920, 1080)))
    assert recorder.join(10)

    [(_reason, video)] = ended
    import av

    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        assert (stream.codec_context.width, stream.codec_context.height) == (1920, 1080)
        last = list(container.decode(video=0))[-1].to_image()
    assert last.getpixel((10, 540))[2] > 150, "the full-size picture fills the frame, edge to edge"


def test_a_recording_keeps_where_its_pointer_went_for_another_shape(tmp_path):
    clock = _Clock(0.0)
    recorder = PageRecorder(tmp_path / "rec.mp4", max_seconds=60, size_limit=10_000_000, on_end=lambda *_: None,
                            clock=clock)
    recorder.started_at = 1.0
    clock.now = 3.5
    recorder.pointer(0.25, 0.75, click=True)
    clock.now = 4.0
    recorder.pointer(1.4, -2)

    assert recorder.trail == [(2.5, 0.25, 0.75), (3.0, 1.0, 0.0)], "seconds into it, and on the page"


def test_a_recording_logs_what_was_done_and_where_for_the_videos_camera(tmp_path):
    """5.3.2: a video's camera follows what Row-Bot did, so each action is kept with the control's box."""
    clock = _Clock(0.0)
    recorder = PageRecorder(tmp_path / "rec.mp4", max_seconds=60, size_limit=10_000_000, on_end=lambda *_: None,
                            clock=clock)
    recorder.started_at = 1.0
    clock.now = 3.0
    recorder.act("type", (0.8, 0.1, 0.5, 0.05), 'textbox "Email"')
    clock.now = 5.5
    recorder.act_done()
    recorder.act("open", note="example.com")

    assert recorder.actions == [[2.0, 4.5, "type", 0.8, 0.1, 0.2, 0.05, 'textbox "Email"'],
                                [4.5, 4.5, "open", 0.0, 0.0, 1.0, 1.0, "example.com"]], \
        "from when to when, the box kept on the page, and the whole page for a page opened"
    with pytest.raises(ValueError):
        recorder.act("wave")


@needs_encoder
def test_a_recording_stops_before_its_video_outgrows_the_chat(tmp_path):
    ended: list[str] = []
    recorder = PageRecorder(tmp_path / "rec.mp4", max_seconds=600, size_limit=1,
                            on_end=lambda reason, _video: ended.append(reason), clock=_Clock(1.0), tick=0.001)
    recorder.offer_frame(_jpeg((30, 200, 30)))
    recorder.start()
    assert recorder.join(10)

    assert ended == ["size"]


@needs_encoder
def test_a_hidden_page_is_recorded_as_a_blank_frame(tmp_path):
    import av

    ended: list[Path | None] = []
    recorder = PageRecorder(tmp_path / "rec.mp4", max_seconds=1, size_limit=10_000_000,
                            on_end=lambda _reason, video: ended.append(video), clock=_Clock(0.25), tick=0.001)
    recorder.offer_frame(_jpeg((250, 250, 250)))
    recorder.shielded = True  # A password field is on the page.
    recorder.start()
    assert recorder.join(10)

    with av.open(str(ended[0])) as container:
        frame = next(container.decode(video=0)).to_image()
    red, green, blue = frame.resize((1, 1)).getpixel((0, 0))
    assert max(red, green, blue) < 80  # Dark "hidden" frame, not the white page.


@needs_encoder
def test_stopping_before_any_picture_saves_nothing(tmp_path):
    ended: list[tuple[str, Path | None]] = []
    recorder = PageRecorder(tmp_path / "rec.mp4", max_seconds=60, size_limit=10_000_000,
                            on_end=lambda reason, video: ended.append((reason, video)))
    recorder.start()
    recorder.request_stop("you")
    assert recorder.join(10)

    assert ended == [("you", None)]
    assert not (tmp_path / "rec.mp4").exists()


# ── The drawn pointer, paced steps and beats (5.3.1 B4) ─────────────────────


def test_the_drawn_pointer_eases_to_each_target_and_rings_where_it_clicks():
    from row_bot.browser.recording import MOVE_SECONDS, RING_SECONDS, draw_pointer, pointer_at

    events = [(1.0, 0.2, 0.2, False, 0.2, 0.2), (2.0, 0.8, 0.6, True, 0.2, 0.2)]
    assert pointer_at(events, 0.5) is None, "no pointer before the first step points"
    assert pointer_at(events, 1.5) == (0.2, 0.2, 0.0)
    halfway = pointer_at(events, 2.0 + MOVE_SECONDS / 2)
    assert halfway[:2] == (0.5, 0.4) and 0 < halfway[2] < 1, "half way there, eased, with its ring growing"
    assert pointer_at(events, 2.0 + MOVE_SECONDS)[:2] == (0.8, 0.6)
    assert pointer_at(events, 2.0 + RING_SECONDS + 0.01)[2] == 0.0, "the ring is gone"

    from PIL import Image

    picture = Image.new("RGB", (400, 300), (200, 30, 30))
    draw_pointer(picture, 0.5, 0.5)
    assert picture.getpixel((202, 154)) == (255, 255, 255), "a white arrow at the point"
    assert picture.getpixel((380, 20)) == (200, 30, 30), "nothing elsewhere"

    tall = Image.new("RGB", (632, 1920), (200, 30, 30))
    draw_pointer(tall, 0.5, 0.5, size=32)
    assert tall.getpixel((322, 976)) == (255, 255, 255), "a white arrow at the point"
    assert tall.getpixel((322, 1000)) == (200, 30, 30), "as tall as asked, not sized from a tall picture's height"


@needs_encoder
def test_a_recording_draws_the_pointer_and_keeps_its_beats(tmp_path):
    import av

    ended: list[Path | None] = []
    clock = _Clock(0.05)
    recorder = PageRecorder(tmp_path / "rec.mp4", max_seconds=1.5, size_limit=10_000_000,
                            on_end=lambda _reason, video: ended.append(video), clock=clock, tick=0.001)
    recorder.offer_frame(_jpeg((200, 30, 30), size=(400, 300)))
    recorder.start()
    recorder.pointer(0.5, 0.5)
    recorder.beat("work", "Waiting for the build")
    with pytest.raises(ValueError):
        recorder.beat("slow")
    assert recorder.join(10)

    with av.open(str(ended[0])) as container:
        last = list(container.decode(video=0))[-1].to_image()
    assert min(last.getpixel((204, 156))) > 200, "the pointer is drawn into the video"
    [(at, kind, note, _line)] = recorder.beats
    assert kind == "work" and note == "Waiting for the build" and at >= 0


def test_beats_are_marked_while_recording_and_kept_with_the_saved_recording(browser, monkeypatch):
    class _Pointed(_Capture):
        def __init__(self, on_end):
            super().__init__(on_end)
            self.beats: list[tuple[float, str, str]] = []
            self.actions = [[3.0, 4.0, "click", 0.1, 0.2, 0.3, 0.1, "Save"]]

        def pointer(self, x, y, *, click=False):
            pass

        def beat(self, kind, note="", line=""):
            self.beats.append((12.0, kind, note, line))
            return 12.0

    session = browser_tool._session_manager.get_session("conversation-a")
    monkeypatch.setattr(session, "start_recording", lambda path, *, max_seconds, size_limit, on_end,
                        thread_id="default": browser.captures.append(_Pointed(on_end)) or browser.captures[-1])
    assert "isn't being recorded" in _record("beat", beat="work")
    _record("start")
    assert _record("beat", beat="work", note="Waiting for the build") == "Marked work at 0:12: sped up in the video."
    assert _record("beat", beat="live", note="Orders", line="Here are this morning's orders, newest first.") == (
        "Marked live at 0:12: shown as it happens. Its line takes about 3 s to say: stay on this step about that long.")
    record = recordings.active_for("conversation-a", "browser")
    video = Path(recordings.working_path(record))
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64)
    from row_bot.application import attachments

    monkeypatch.setattr(attachments, "register_attachment", lambda conversation, name, data: {
        "attachment_ref": f"{conversation}:rec", "mime_type": "video/mp4"})
    recordings.finished(record, "agent", video)
    assert record.beats == [[12.0, "work", "Waiting for the build", ""],
                            [12.0, "live", "Orders", "Here are this morning's orders, newest first."]]
    assert record.actions == [[3.0, 4.0, "click", 0.1, 0.2, 0.3, 0.1, "Save"]]
    assert recordings._load(recordings._record_path(record)).actions == record.actions, "kept with the recording"
    assert "Beats: 0:12 work (Waiting for the build)" in recordings.summary(record)
    assert recordings._load(recordings._record_path(record)).beats == record.beats


def test_while_recording_clicks_and_typing_go_at_a_viewers_pace_with_the_pointer(browser, monkeypatch):
    import asyncio

    pointed: list[tuple[float, float, bool]] = []
    typed: list[tuple[str, str]] = []

    acted: list[tuple] = []

    class _Capture2:
        def pointer(self, x, y, *, click=False):
            pointed.append((round(x, 2), round(y, 2), click))

        def act(self, kind, box=None, note=""):
            acted.append((kind, tuple(round(value, 2) for value in box), note))

        def act_done(self):
            acted.append(("done",))

    class _Locator:
        async def bounding_box(self, timeout=None):
            return {"x": 100, "y": 50, "width": 40, "height": 20}

        async def click(self, **_kw):
            typed.append(("click", ""))

        async def fill(self, text, timeout=None):
            typed.append(("fill", text))

        async def press_sequentially(self, text, delay=0, timeout=None):
            typed.append(("keys", text))

    class _Mouse:
        async def move(self, x, y, steps=1):
            typed.append(("move", f"{x},{y}"))

    tab = SimpleNamespace(page=SimpleNamespace(mouse=_Mouse(), evaluate=lambda _js: _async((400, 200))))
    session = browser_tool._session_manager.get_session("conversation-a")
    monkeypatch.setattr(session, "inspect", lambda _thread, _ref: {"name": "Email", "role": "textbox"})
    monkeypatch.setattr(session, "locate", lambda _tab, _ref: _async(_Locator()))
    monkeypatch.setattr(browser_tool, "_run", lambda _session, _thread, _label, step, **_kw: asyncio.run(step(tab)))
    monkeypatch.setattr(browser_tool, "_recording", lambda _thread: _Capture2())
    monkeypatch.setattr("row_bot.browser.recording.MOVE_SECONDS", 0)

    assert browser_tool.browser_click("e1") == 'Clicked textbox "Email".'
    assert pointed == [(0.3, 0.3, False), (0.3, 0.3, True)], "to the middle of the control, then a click ring"
    typed.clear()
    browser_tool.browser_type("e1", "ada@example.com")
    assert [kind for kind, _ in typed] == ["move", "fill", "keys"] and typed[-1][1] == "ada@example.com"
    assert acted == [("click", (0.25, 0.25, 0.1, 0.1), 'textbox "Email"'), ("done",),
                     ("type", (0.25, 0.25, 0.1, 0.1), 'textbox "Email"'), ("done",)], \
        "each action logged on the control's box, never the words typed"


async def _async(value):
    return value
