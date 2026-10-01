"""The launch smoke fails when the app it tested may not be ours or is not React (B197).

It used to pass on a busy port without launching anything, and only warned when
`/` did not answer 200. `/` must now send a browser to the React client (the
desktop app redirects to /app-v2/, a server-mode app via Connect with
next=/app-v2/), the page it ends on must load, and /app-v2/ must serve the
React shell.
"""
from __future__ import annotations

import json

import pytest

import scripts.smoke_app as smoke_app

pytestmark = [pytest.mark.subsystem, pytest.mark.installer]

SHELL = b'<!doctype html><html><body><div id="root"></div></body></html>'


class FakeProcess:
    pid = 4321
    returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class FakeResponse:
    status = 200

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit=None):
        return self._body


@pytest.fixture
def launched(monkeypatch):
    """A launch whose readiness probes answer; `pages` decides what GETs see."""
    pages: dict[str, tuple[int, str, bytes]] = {}

    def fake_urlopen(request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        if url.endswith("/api/launcher-ping"):
            return FakeResponse(b'{"app":"row-bot"}')
        return FakeResponse(json.dumps({"ready": True}).encode())

    monkeypatch.setattr(smoke_app, "_port_open", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(smoke_app.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(smoke_app.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(smoke_app, "_http_get", lambda url: pages[url.split(":8126", 1)[1]])
    return pages


def _run(tmp_path):
    return smoke_app.run_app_smoke(command=["python", "app.py"], cwd=tmp_path, port=8126,
                                   timeout=1, data_dir=tmp_path / "data")


def test_a_busy_port_fails_without_launching(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(smoke_app, "_port_open", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(smoke_app.subprocess, "Popen",
                        lambda *_args, **_kwargs: pytest.fail("nothing may launch on a busy port"))

    result = smoke_app.run_app_smoke(cwd=tmp_path, port=8123, timeout=0.1)

    assert result.ok is False
    assert result.messages[0][0] == "FAIL"


def test_the_desktop_app_redirects_to_the_react_shell(launched, tmp_path) -> None:
    launched["/"] = (307, "/app-v2/", b"")
    launched["/app-v2/"] = (200, "", SHELL)

    result = _run(tmp_path)

    assert result.ok is True
    assert ("FAIL", ) not in [message[:1] for message in result.messages]


def test_a_server_mode_app_sends_the_browser_through_connect(launched, tmp_path) -> None:
    launched["/"] = (303, "/connect?next=%2Fapp-v2%2F", b"")
    launched["/connect?next=%2Fapp-v2%2F"] = (200, "", b"<form>Connect</form>")

    assert _run(tmp_path).ok is True


def test_a_root_page_that_is_not_a_redirect_fails(launched, tmp_path) -> None:
    launched["/"] = (200, "", b"<html>something else</html>")

    result = _run(tmp_path)

    assert result.ok is False


def test_a_react_page_that_does_not_load_fails(launched, tmp_path) -> None:
    launched["/"] = (307, "/app-v2/", b"")
    launched["/app-v2/"] = (503, "", b"The Row-Bot client is not built.")

    assert _run(tmp_path).ok is False


def test_a_page_without_the_react_shell_fails(launched, tmp_path) -> None:
    launched["/"] = (307, "/app-v2/", b"")
    launched["/app-v2/"] = (200, "", b"<html>not the client</html>")

    assert _run(tmp_path).ok is False
