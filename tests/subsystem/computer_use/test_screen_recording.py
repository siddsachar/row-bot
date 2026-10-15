"""Recording the screen through Cua Driver (F2): only with Computer use set up, always asking first.

Without Computer use the tool says plainly what's needed and how to set it up; without ffmpeg
(Windows) it says that instead and leaves nothing running. The recording runs on a Cua client of
its own, always into one private folder under Row-Bot's data, which goes once the video is saved
to the conversation. The model's actions and the generic client paths still can't reach the
recording tools. Cua is a fake transport here: nothing records.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from row_bot import recordings
from row_bot.computer_use.client import CuaClient
from row_bot.computer_use.readiness import CuaReadiness, ReadinessCode
from row_bot.mcp_client.results import RawCallContent, RawCallResult
from row_bot.tools import approval_gate
from tests.subsystem.client_protocol.test_protocol_application import service  # noqa: F401

pytestmark = pytest.mark.subsystem

_MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


class _RecordingTransport:
    """Cua's recording tools: start writes turn folders and a video into output_dir; stop finalizes it."""

    def __init__(self, *, video_active: bool = True, last_error: str = "") -> None:
        self.video_active, self.last_error = video_active, last_error
        self.calls: list[tuple[str, dict]] = []
        self.closed = False
        self.folder: Path | None = None

    def open(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def call_raw(self, name: str, arguments: dict | None = None) -> RawCallResult:
        args = dict(arguments or {})
        self.calls.append((name, args))
        state: dict = {}
        if name == "start_recording":
            self.folder = Path(args["output_dir"])
            (self.folder / "turn-00001").mkdir(parents=True)
            (self.folder / "turn-00001" / "action.json").write_text('{"text": "secret"}', encoding="utf-8")
            state = {"recording": True, "video_active": self.video_active, "last_error": self.last_error or None}
        elif name == "stop_recording":
            if self.video_active and self.folder is not None:
                (self.folder / "recording.mp4").write_bytes(_MP4)
            state = {"recording": False, "video_active": False,
                     "last_video_path": str(self.folder / "recording.mp4") if self.folder else None}
        return RawCallResult((RawCallContent(kind="text", text="ok"),), state, False)


@pytest.fixture
def screen(service, tmp_path, monkeypatch):  # noqa: F811
    from row_bot import threads
    from row_bot.computer_use.readiness import acknowledge_disclosure

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("platform.system", lambda: "Windows")
    acknowledge_disclosure()
    chat = threads.create_thread("Demo", seed_default_skills=False)
    state = SimpleNamespace(chat=chat, mode="approve", answer=True, asked=[], transport=_RecordingTransport(),
                            ready=ReadinessCode.READY)
    monkeypatch.setattr("row_bot.agent.get_current_thread_id", lambda: state.chat)
    monkeypatch.setattr(approval_gate, "current_approval_mode", lambda: state.mode)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: state.asked.append(payload) or state.answer)
    monkeypatch.setattr("row_bot.computer_use.readiness.readiness",
                        lambda **_kw: CuaReadiness(state.ready, "fixture"))
    monkeypatch.setattr("row_bot.computer_use.recording._default_client",
                        lambda: CuaClient("fake-cua-driver.exe",
                                          transport_factory=lambda *_args: state.transport))
    yield state
    recordings.stop_all(timeout=5)


def _computer(action: str, **kwargs) -> str:
    from row_bot.tools.computer_use_tool import ComputerUseTool

    [tool] = ComputerUseTool().as_langchain_tools()
    return tool.func(action, **kwargs)


@pytest.mark.parametrize("code", [ReadinessCode.NOT_INSTALLED, ReadinessCode.DISCLOSURE_REQUIRED,
                                  ReadinessCode.HASH_MISMATCH])
def test_without_computer_use_set_up_it_says_how_to_set_it_up(screen, code):
    screen.ready = code
    result = _computer("start_recording")

    assert result == (
        "Recording your screen needs Computer use set up. Open Settings › System › Browser and computer use, "
        "turn on Computer use and finish its setup (it installs Cua Driver after you review it), then ask again."
    )
    assert screen.asked == [] and screen.transport.calls == []


def test_it_asks_records_into_a_private_folder_and_saves_the_video_to_the_chat(screen):
    from row_bot.application.attachments import read_attachment
    from row_bot.data_paths import get_row_bot_data_dir

    started = _computer("start_recording", minutes=2)

    [asked] = screen.asked
    assert asked["question"] == "Record your screen?"
    assert "everything on your main screen, including other apps and notifications, for up to 2 minutes" in (
        asked["description"])
    assert started.startswith("Recording rec-")
    record = recordings.active_for(screen.chat, "screen")
    assert record is not None and record.max_seconds == 120
    name, args = screen.transport.calls[-1]
    assert name == "start_recording" and set(args) == {"output_dir", "record_video"}
    assert args["record_video"] is True
    assert Path(args["output_dir"]).is_relative_to(get_row_bot_data_dir())

    stopped = _computer("stop_recording")

    assert stopped.startswith(f"Recording {record.recording_id}: saved ")
    assert (record.status, record.stopped_by) == ("saved", "agent")
    assert read_attachment(record.media_ref)[1] == _MP4
    assert screen.transport.closed is True
    assert not Path(args["output_dir"]).exists()  # Cua's turn folders (screenshots, typed text) go too.


def test_without_ffmpeg_it_says_so_and_leaves_nothing_running(screen):
    screen.transport = _RecordingTransport(video_active=False, last_error="ffmpeg not found on PATH")
    result = _computer("start_recording")

    assert result.startswith("Recording your screen on this computer needs ffmpeg, which isn't installed.")
    assert [name for name, _args in screen.transport.calls if "recording" in name] == [
        "start_recording", "stop_recording"]
    assert screen.transport.closed is True
    assert recordings.list_recordings(screen.chat) == []
    assert not screen.transport.folder.exists()


def test_block_refuses_and_a_denial_records_nothing(screen):
    screen.mode = "block"
    assert _computer("start_recording") == "BLOCKED: Recording the screen needs approval, and this chat is on Block."
    screen.mode, screen.answer = "approve", False
    assert "didn't allow recording the screen" in _computer("start_recording")
    assert screen.transport.calls == []


def test_hands_off_records_without_asking(screen):
    screen.mode = "full"
    assert _computer("start_recording").startswith("Recording rec-")
    assert screen.asked == []


def test_the_recording_tools_stay_out_of_the_models_and_generic_paths(screen):
    client = CuaClient("fake-cua-driver.exe", transport_factory=lambda *_args: screen.transport)
    with pytest.raises(PermissionError):
        client._call("start_recording", {"output_dir": "/tmp/x"})
    with pytest.raises(PermissionError):
        client.call_reviewed_driver_tool("stop_recording")
    with pytest.raises(ValueError, match="Unsupported Computer action"):
        client.call_action("start_recording")
    with pytest.raises(PermissionError):
        client.call_recording("install_ffmpeg", {"confirm": True})
    with pytest.raises(PermissionError):
        client.call_recording("start_recording", {"output_dir": "relative/folder"})
    with pytest.raises(PermissionError):
        client.call_recording("start_recording", {"output_dir": str(Path.cwd()), "record_video": False})
    assert screen.transport.calls == []


@pytest.mark.parametrize("context", [{"runtime_surface": "channel"}, {"runtime_surface": "workflow"},
                                     {"background_workflow": True}, {"runtime_surface": "agent"}])
def test_only_an_interactive_chat_on_this_computer_records_the_screen(screen, monkeypatch, context):
    monkeypatch.setattr("row_bot.agent.get_active_runtime_context", lambda: dict(context))

    assert _computer("start_recording").startswith("Recording the screen works only in a chat in Row-Bot")
    assert screen.asked == [] and screen.transport.calls == []
