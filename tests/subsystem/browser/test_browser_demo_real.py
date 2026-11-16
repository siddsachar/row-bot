"""Show Row-Bot how (C3) in a real browser on a local page: the person's clicks and typing become steps
named by role and visible name, and nothing typed into the password field is kept. Loopback only;
skipped where no browser runtime is installed. The same rules run against a fake page in the pull
request lane (``test_browser_demo.py``).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.slow]

_PAGE = b"""<!doctype html><html><head><title>Orders</title></head><body>
<nav aria-label="Account"><a id="statements" href="#statements" style="display:inline-block;padding:8px">Statements</a></nav>
<main><h1>Find an order</h1>
<label for="order">Order number</label> <input id="order" style="display:block;width:300px;height:30px">
<button id="find" style="display:block;padding:8px">Find order</button></main>
</body></html>"""
_SIGN_IN = b"""<!doctype html><html><head><title>Sign in</title></head><body><form>
<label for="user">Email</label> <input id="user" autocomplete="username" style="display:block;width:300px;height:30px">
<label for="pass">Password</label> <input id="pass" type="password" style="display:block;width:300px;height:30px">
</form></body></html>"""
SECRET = "correct-horse-7"


class _Local(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = _SIGN_IN if self.path.startswith("/sign-in") else _PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def browser(tmp_path, monkeypatch):
    from row_bot.browser import demo
    from row_bot.browser import service as browser_service
    from row_bot.browser.runtime import check_managed_browser_runtime, check_packaged_browser_runtime

    if browser_service._installed_channel() is None and not (
        check_packaged_browser_runtime().ready or check_managed_browser_runtime().ready
    ):
        pytest.skip("No reviewed browser runtime is installed")
    monkeypatch.setenv("ROW_BOT_PORT", "8080")
    monkeypatch.setattr(browser_service, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(demo, "_DEMOS", {})
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Local)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    session = browser_service.BrowserEngine()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    session.allow_local_address("chat", origin, source="you")
    yield session, origin
    session.close()
    server.shutdown()
    server.server_close()


def _centre(session, selector: str) -> tuple[float, float]:
    box = session._run(lambda: session._chats["chat"].active.page.evaluate(
        "(s) => { const r = document.querySelector(s).getBoundingClientRect(); return [r.x + r.width / 2, "
        "r.y + r.height / 2]; }", selector))
    return float(box[0]), float(box[1])


def _click(session, selector: str) -> None:
    x, y = _centre(session, selector)
    session.person_input("chat", [{"type": "mouse", "action": "down", "x": x, "y": y, "button": "left", "clicks": 1},
                                  {"type": "mouse", "action": "up", "x": x, "y": y, "button": "left", "clicks": 1}])


def _type(session, text: str) -> None:
    session.person_input("chat", [event for char in text for event in (
        {"type": "key", "action": "down", "key": char}, {"type": "key", "action": "up", "key": char})])


def test_real_clicks_and_typing_become_named_steps_and_a_password_is_never_kept(browser):
    from row_bot.browser import demo

    session, origin = browser
    session.person_action("chat", "navigate", url=origin + "/orders?ref=abc")
    recorded = demo.start("chat", url=session.current_url("chat"), title="Orders")

    _click(session, "#statements")
    _click(session, "#order")
    _type(session, "AB-1042")
    _click(session, "#find")

    steps = [(step.kind, step.role, step.name) for step in recorded.steps]
    assert steps == [("open", "", ""), ("click", "link", "Statements"), ("type", "textbox", "Order number"),
                     ("click", "button", "Find order")]
    assert recorded.steps[1].context == "Account" and recorded.steps[2].value == "AB-1042"
    assert recorded.steps[0].path == "/orders" and "ref=abc" not in json.dumps(demo.page("chat"))

    session.person_action("chat", "navigate", url=origin + "/sign-in")
    _click(session, "#user")
    _type(session, "sam@example.org")
    _click(session, "#pass")
    _type(session, SECRET)

    user, password = recorded.steps[-2:]
    assert (password.kind, password.name, password.secret, password.value) == ("type", "Password", "password", "")
    assert (user.secret, user.value) == ("sign_in", "")
    assert SECRET not in repr(recorded) and SECRET not in json.dumps(demo.page("chat"))
    typed = session._run(lambda: session._chats["chat"].active.page.evaluate("document.querySelector('#pass').value"))
    assert typed == SECRET  # It reached the page: the person signed in themselves.
