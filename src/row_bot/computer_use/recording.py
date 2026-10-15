"""Record the screen through Cua Driver (F2): only when the person has Computer use set up.

The recording runs on a Cua client of its own, so the Computer use session (its lease, Stop
and Take over) never ends it by accident, and actions the agent takes through Computer use
aren't written into the recording's folder. Cua writes ``recording.mp4`` (the main display)
into a private folder under Row-Bot's data; once the video is saved to the conversation, the
whole folder goes. On Windows Cua needs ffmpeg for the video; on macOS it needs macOS 15.
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

VIDEO_NAME = "recording.mp4"
_POLL_SECONDS = 0.5

SETUP_STEPS = (
    "Open Settings › System › Browser and computer use, turn on Computer use and finish its setup "
    "(it installs Cua Driver after you review it), then ask again."
)


def setup_problem() -> str:
    """Why the screen can't be recorded here, in plain words; empty when Computer use is ready."""

    try:
        from row_bot.computer_use.readiness import ReadinessCode, readiness

        state = readiness(enabled=True)
    except Exception:
        return "Recording your screen needs Computer use set up. " + SETUP_STEPS
    if state.code in {ReadinessCode.READY, ReadinessCode.DEGRADED}:
        return ""
    if state.code is ReadinessCode.UNSUPPORTED:
        return "Recording your screen isn't available on this computer. Row-Bot's own browser can still be recorded."
    return "Recording your screen needs Computer use set up. " + SETUP_STEPS


def _video_problem(structured: dict[str, Any]) -> str:
    if platform.system().casefold() == "darwin":
        return (
            "Recording your screen needs macOS 15 or later, and Screen Recording allowed for Row-Bot in "
            "System Settings › Privacy & Security."
        )
    detail = str(structured.get("last_error") or "").casefold()
    if detail and "ffmpeg" not in detail:
        return "Cua Driver couldn't start recording the screen. Try again in a moment."
    return (
        "Recording your screen on this computer needs ffmpeg, which isn't installed. Install it "
        "(for example with: winget install Gyan.FFmpeg), restart Row-Bot and ask again. "
        "Row-Bot's own browser can be recorded without it."
    )


def _default_client() -> Any:
    from row_bot.computer_use.service import ComputerUseService

    return ComputerUseService._default_client()


class ScreenRecording:
    """One recording of the main display, from start to the saved video."""

    def __init__(
        self,
        folder: Path,
        *,
        max_seconds: float,
        size_limit: int,
        on_end: Callable[[str, Path | None], None],
        client_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.folder = Path(folder)
        self.max_seconds = float(max_seconds)
        self.size_limit = int(size_limit)
        self._on_end = on_end
        self._client_factory = client_factory or _default_client
        self._clock = clock
        self._stop = threading.Event()
        self._reason = ""
        self._client: Any = None
        self._started = 0.0
        self._thread = threading.Thread(target=self._watch, daemon=True, name="row-bot-screen-recording")

    @property
    def video(self) -> Path:
        return self.folder / VIDEO_NAME

    def begin(self) -> None:
        """Start Cua's recorder; raises ``RecordingError`` with the reason in plain words."""

        from row_bot.recordings import RecordingError

        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            client = self._client_factory()
            client.start()
        except Exception as exc:
            logger.info("Screen recording could not reach Cua Driver", exc_info=True)
            raise RecordingError("Recording your screen needs Computer use set up. " + SETUP_STEPS) from exc
        self._client = client
        try:
            response = client.call_recording("start_recording", {"output_dir": str(self.folder.resolve())})
        except Exception as exc:
            self._close()
            raise RecordingError("Cua Driver couldn't start recording the screen. Try again in a moment.") from exc
        structured = dict(getattr(response, "structured", None) or {})
        if getattr(response, "is_error", False) or not structured.get("video_active"):
            try:
                client.call_recording("stop_recording")
            except Exception:
                pass
            self._close()
            raise RecordingError(_video_problem(structured))
        self._started = self._clock()
        self._thread.start()

    def request_stop(self, reason: str) -> None:
        if not self._reason:
            self._reason = str(reason or "agent")
        self._stop.set()

    def join(self, timeout: float | None) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _video_size(self) -> int:
        try:
            return self.video.stat().st_size
        except OSError:
            return 0

    def _close(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            logger.debug("Cua's recording session didn't close cleanly", exc_info=True)

    def _watch(self) -> None:
        reason = "agent"
        while True:
            if self._stop.wait(_POLL_SECONDS):
                reason = self._reason or "agent"
                break
            if self._clock() - self._started >= self.max_seconds:
                reason = "limit"
                break
            if self._video_size() >= self.size_limit:
                reason = "size"
                break
        video: Path | None = None
        try:
            response = self._client.call_recording("stop_recording") if self._client is not None else None
            if response is not None and not getattr(response, "is_error", False) and self.video.is_file():
                video = self.video
        except Exception:
            logger.warning("Cua Driver didn't stop the screen recording cleanly", exc_info=True)
            reason = "error"
        finally:
            self._close()
        try:
            self._on_end(reason, video)
        except Exception:
            logger.warning("A finished screen recording could not be saved", exc_info=True)
