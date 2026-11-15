"""Row-Bot's real browser on a local page (F4, F2, N12-A): the conversation's grant opens it and
another conversation's page can't load it; the pane's live picture streams at the pane's size; phone size,
screenshots and a recording work end to end. Loopback only; skipped where no browser runtime is
installed.
"""

from __future__ import annotations

import base64
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


# A sign-in code split into boxes, filled from the paste event's own data (as code pages do); and a plain field.
_CODE = b"""<!doctype html><html><head><title>Enter code</title></head><body>
<div id="code"><input maxlength="1"><input maxlength="1"><input maxlength="1"><input maxlength="1"></div>
<input id="note" style="display:block;width:300px">
<script>
const boxes = Array.from(document.querySelectorAll('#code input'));
boxes[0].addEventListener('paste', (event) => {
  event.preventDefault();
  const text = event.clipboardData.getData('text/plain').replace(/[^A-Z0-9]/gi, '');
  boxes.forEach((box, index) => { box.value = text[index] || ''; });
});
</script></body></html>"""


class _Local(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        # /plain is the same page without its password field.
        body = _PAGE if self.path != "/plain" else _PAGE.replace(b'<input type="password"', b'<input type="text"')
        body = _CODE if self.path == "/code" else body
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
    monkeypatch.setenv("ROW_BOT_PORT", "8080")
    monkeypatch.setattr(browser_service, "PROFILE_DIR", tmp_path / "profile")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Local)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = browser_service.BrowserEngine()
    yield session, f"http://127.0.0.1:{server.server_address[1]}"
    session.close()
    server.shutdown()
    server.server_close()


def _open(session, chat: str, url: str) -> str:
    async def step(tab):
        try:
            await tab.page.goto(url, wait_until="domcontentloaded", timeout=10_000)
        except Exception as exc:
            return "BLOCKED: " + str(exc)[:120]
        return ""

    return session.act(chat, "Open", step).text


def test_the_persons_paste_reaches_the_page_as_its_own_paste(browser):
    """A code split into boxes reads the paste event: typed-in text alone filled one box (30 Oct sign-in)."""
    session, origin = browser
    session.allow_local_address("chat", origin, source="you")
    assert "Enter code" in _open(session, "chat", origin + "/code")

    def value(selector: str) -> str:
        return session._run(lambda: session._chats["chat"].active.page.evaluate(
            "(s) => Array.from(document.querySelectorAll(s)).map((box) => box.value).join('')", selector))

    session._run(lambda: session._chats["chat"].active.page.focus("#code input"))
    assert session.person_input("chat", [{"type": "paste", "text": "AB-12"}])["status"] == "ok"
    assert value("#code input") == "AB12"

    # A field that leaves paste to the browser gets the text as typed.
    session._run(lambda: session._chats["chat"].active.page.focus("#note"))
    session.person_input("chat", [{"type": "paste", "text": "Order 1042, damaged in transit"}])
    assert value("#note") == "Order 1042, damaged in transit"


def _inner_width(session, chat: str) -> int:
    return session._run(lambda: session._chats[chat].active.page.evaluate("innerWidth"))


def test_a_granted_local_page_opens_streams_is_captured_at_phone_size_and_recorded(browser, tmp_path):
    from PIL import Image
    import av

    session, origin = browser
    assert _open(session, "other", origin + "/").startswith("BLOCKED:")  # Not allowed anywhere yet.

    session.allow_local_address("chat", origin, source="you")
    opened = _open(session, "chat", origin + "/")
    assert "Local app" in opened
    # Another conversation's page still can't load it.
    assert _open(session, "other", origin + "/").startswith("BLOCKED:")

    # The pane's live picture: the page takes the pane's size (420×700 CSS pixels on a 2× screen),
    # so the site lays out for the pane and each JPEG is the pane's own pixels, shown 1:1.
    frames: list = []
    got = threading.Event()
    watch = session.watch("chat", kind="pane", width=420, height=700, scale=2.0,
                          on_frame=lambda frame: (frames.append(frame), len(frames) >= 3 and got.set()))
    assert got.wait(15)
    picture = Image.open(io.BytesIO(base64.b64decode(frames[-1].data)))
    assert picture.format == "JPEG" and (frames[-1].css_width, frames[-1].css_height) == (420, 700)
    assert picture.size == (840, 1400)
    assert _inner_width(session, "chat") == 420
    # With no pane showing, the page is a desktop window again.
    watch.close()
    session._run(lambda: session._follow_live("chat"))
    assert _inner_width(session, "chat") == 1280

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

    assert "Local app" in _open(session, "chat", origin + "/plain")
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
        decoded = [frame for frame in container.decode(video=0)]
    assert len(decoded) >= 12
    # The phone size chosen above is kept: the video is the phone's shape, at most 1920 tall.
    assert {(frame.width, frame.height) for frame in decoded} == {(886, 1920)}
    # The page kept counting: the recording isn't one still picture.
    assert decoded[0].to_image().tobytes() != decoded[-1].to_image().tobytes()
