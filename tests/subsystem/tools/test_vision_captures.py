"""What the vision tool may capture, and when it asks (B327).

A page in Row-Bot's own browser or a workspace file needs no capture. The person's screen or camera can show
anything, so capturing them needs approval every time and says what it captures: Ask asks, Auto's reviewer
approves only a capture the person clearly asked for (A0), Hands-off allows it, and Block refuses it.
"""

from __future__ import annotations

import pytest

from row_bot.tools import approval_gate, browser_tool, vision_tool

pytestmark = pytest.mark.subsystem


class FakeVision:
    def __init__(self) -> None:
        self.captured: list[str] = []
        self.analyzed: list[tuple[bytes, str]] = []

    def capture_and_analyze(self, question: str, source: str = "camera", file_path: str = "") -> str:
        self.captured.append(source)
        return f"saw the {source}"

    def analyze(self, image: bytes, question: str) -> str:
        self.analyzed.append((image, question))
        return "saw the page"


class FakeBrowser:
    def __init__(self, page: bytes | None) -> None:
        self.page = page

    def take_screenshot(self, thread_id: str) -> bytes | None:
        return self.page


@pytest.fixture
def setup(monkeypatch):
    vision = FakeVision()
    asked: list[dict] = []
    answer = {"value": True}
    mode = {"value": "approve"}
    monkeypatch.setattr(vision_tool, "_get_vision_service", lambda: vision)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: mode["value"])
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: asked.append(payload) or answer["value"])
    monkeypatch.setattr(browser_tool, "get_session_manager", lambda: FakeBrowser(b"page-png"))
    return vision, asked, answer, mode


def test_a_browser_page_is_looked_at_without_asking_or_capturing_the_desktop(setup) -> None:
    vision, asked, _answer, _mode = setup

    assert vision_tool._analyze_image("What's at the top?", source="browser") == "saw the page"

    assert asked == []
    assert vision.captured == []
    assert vision.analyzed == [(b"page-png", "This is the page open in Row-Bot's browser. What's at the top?")]


def test_no_open_page_says_so_instead_of_capturing_anything(setup, monkeypatch) -> None:
    vision, asked, _answer, _mode = setup
    monkeypatch.setattr(browser_tool, "get_session_manager", lambda: FakeBrowser(None))

    assert "No page is open in Row-Bot's browser" in vision_tool._analyze_image("Look", source="browser")
    assert asked == [] and vision.captured == []


@pytest.mark.parametrize("approval_mode", ["approve", "auto"])
@pytest.mark.parametrize(("source", "label"), [("screen", "Capture your whole screen?"),
                                               ("camera", "Take a picture with your camera?")])
def test_the_screen_and_camera_ask_every_time_unless_the_reviewer_approves(setup, monkeypatch, approval_mode, source,
                                                                           label) -> None:
    from row_bot.approval_review import ReviewVerdict

    vision, asked, _answer, mode = setup
    mode["value"] = approval_mode
    monkeypatch.setattr("row_bot.approval_review.review",
                        lambda *_a, **_k: ReviewVerdict("ask", "You didn't ask to be looked at."))

    result = vision_tool._analyze_image("What's showing?", source=source)

    assert [payload["label"] for payload in asked] == [label]
    assert ("every open window" if source == "screen" else "webcam") in asked[0]["description"]
    assert vision.captured == [source]
    assert result.endswith(f"saw the {source}")


def test_a_denied_capture_captures_nothing(setup) -> None:
    vision, asked, answer, _mode = setup
    answer["value"] = False

    assert "Capture cancelled by user." in vision_tool._analyze_image("What's showing?", source="screen")
    assert len(asked) == 1 and vision.captured == []


def test_block_refuses_a_capture_without_asking(setup) -> None:
    vision, asked, _answer, mode = setup
    mode["value"] = "block"

    assert vision_tool._analyze_image("What's showing?", source="camera").startswith("BLOCKED:")
    assert asked == [] and vision.captured == []


def test_a_workspace_file_needs_no_approval(setup) -> None:
    vision, asked, _answer, _mode = setup

    assert vision_tool._analyze_image("Describe it", source="file", file_path="photo.jpg") == "saw the file"
    assert asked == []


@pytest.mark.parametrize("source", ["webcam", "Camera feed", "clipboard", ""])
def test_a_source_that_is_not_offered_never_reaches_the_camera(setup, source) -> None:
    """Any other value used to capture the webcam without asking; the tool now offers only its four sources."""
    vision, asked, _answer, _mode = setup

    result = vision_tool._analyze_image("What's in front of me?", source=source or "  ")

    assert vision.captured == [] and asked == []
    assert result.startswith("Unknown source")


@pytest.mark.parametrize(("approval_mode", "first_line"), [
    ("auto", "Approval: approved by the reviewer: You asked what's on your screen"),
    ("full", "Approval: allowed by Hands-off"),
])
def test_a_capture_the_reviewer_approves_or_full_autonomy_allows_runs_without_asking(setup, monkeypatch, approval_mode,
                                                                                     first_line) -> None:
    from row_bot.approval_review import ReviewVerdict

    vision, asked, _answer, mode = setup
    mode["value"] = approval_mode
    risks: list[tuple] = []
    monkeypatch.setattr("row_bot.approval_review.review", lambda _payload, **kw: risks.append(
        tuple(kw.get("risks", ()))) or ReviewVerdict("approve", "You asked what's on your screen."))

    result = vision_tool._analyze_image("What's on my screen?", source="screen")

    assert asked == [] and vision.captured == ["screen"]
    assert result.splitlines()[0] == first_line
    assert risks == ([("captures the person's screen",)] if approval_mode == "auto" else [])
