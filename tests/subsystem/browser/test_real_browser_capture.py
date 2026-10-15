"""Row-Bot's real managed browser on a local page (F4, F2): the conversation's grant opens it and
another conversation's page can't load it; phone size, screenshots and a recording work end to
end. Loopback only; skipped where no reviewed browser runtime is installed.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import threading
from pathlib import Path

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.slow]

_PAGE = b"""<!doctype html><html><head><title>Local app</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{margin:0;font:16px sans-serif} #tick{font-size:48px} .tall{height:30000px;background:linear-gradient(#fff,#36c)}</style>
</head><body><h1>Local app</h1><input type="password" value="hunter2" style="font-size:40px">
<div id="tick">0</div><div class="tall"></div>
<script>let n=0;setInterval(()=>{document.getElementById('tick').textContent=String(++n)},50)</script>
</body></html>"""


class _Local(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        # /plain is the same page without its password field.
        body = _PAGE if self.path != "/plain" else _PAGE.replace(b'<input type="password"', b'<input type="text"')
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def browser(tmp_path, monkeypatch):
    from row_bot.browser import service as browser_service
    from row_bot.browser.recording import encoder_problem
    from row_bot.browser.runtime import check_managed_browser_runtime, check_packaged_browser_runtime

    if browser_service._installed_channel() is None and not (
        check_packaged_browser_runtime().ready or check_managed_browser_runtime().ready
    ):
        pytest.skip("No reviewed browser runtime is installed")
    if encoder_problem():
        pytest.skip("PyAV with H.264 isn't installed")
    monkeypatch.setenv("ROW_BOT_BROWSER_HEADLESS", "1")
    monkeypatch.setenv("ROW_BOT_PORT", "8080")
    monkeypatch.setattr(browser_service, "PROFILE_DIR", tmp_path / "profile")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Local)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = browser_service.ManagedBrowserService()
    yield session, f"http://127.0.0.1:{server.server_address[1]}"
    session.close()
    server.shutdown()
    server.server_close()


def test_a_granted_local_page_opens_is_captured_at_phone_size_and_recorded(browser, tmp_path):
    from PIL import Image
    import av

    session, origin = browser
    assert session.navigate(origin + "/", "other").startswith("BLOCKED:")  # Not allowed anywhere yet.

    session.allow_local_address("chat", origin, source="you")
    opened = session.navigate(origin + "/", "chat")
    assert "Local app" in opened
    # Another conversation's page still can't load it.
    assert session.navigate(origin + "/", "other").startswith("BLOCKED:")

    assert session.set_viewport("phone", None, "chat").startswith("The page now shows at phone size")
    image, words = session.capture_page("chat")
    assert image is not None and "phone size at 3× scale" in words
    picture = Image.open(io.BytesIO(image)).convert("RGB")
    assert picture.size == (390 * 3, 844 * 3)
    # The password field is covered.
    raw = picture.tobytes()
    cover = bytes((38, 40, 46))
    assert sum(raw[index:index + 3] == cover for index in range(0, len(raw), 3)) > 1000

    full, words = session.capture_page("chat", full_page=True)
    assert full is not None and "pixels of a longer page" in words
    width, height = Image.open(io.BytesIO(full)).size
    assert width >= 390 * 3 and height <= 16_384

    assert "Local app" in session.navigate(origin + "/plain", "chat")
    ended = threading.Event()
    result: dict = {}

    def on_end(reason: str, video: Path | None) -> None:
        result.update(reason=reason, video=video)
        ended.set()

    recorder = session.start_recording(tmp_path / "rec.mp4", max_seconds=2, size_limit=20_000_000,
                                       on_end=on_end, thread_id="chat")
    assert ended.wait(30)
    assert result["reason"] == "limit" and recorder.frames >= 12
    with av.open(str(result["video"])) as container:
        frames = [frame for frame in container.decode(video=0)]
    assert len(frames) >= 12
    # The page kept counting: the recording isn't one still picture.
    assert frames[0].to_image().tobytes() != frames[-1].to_image().tobytes()
