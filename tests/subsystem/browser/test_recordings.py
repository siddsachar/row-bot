"""Recording Row-Bot's browser (F2): starting asks by the approval level, stopping never does, and a
recording stops at its time limit and before its video outgrows the chat.

The approval tests use a fake capture (nothing records); the encoder tests write a real H.264
MP4 from a few pictures through PyAV, with a clock that runs fast.
"""

from __future__ import annotations

import io
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
