"""The server's front door: `/` opens the React client for every caller.

NiceGUI used to answer `/` on this computer (the legacy UI); the server is
plain FastAPI now and `/` redirects to `/app-v2/`, keeping the query, so old
bookmarks and scripts land in React. A remote browser without a session is
sent to Connect first, then to React.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.subsystem

_FRONT_DOOR = textwrap.dedent('''
    import json, sys
    from starlette.testclient import TestClient
    import row_bot.app as server
    peer = sys.argv[1]
    client = TestClient(server.app, base_url="http://localhost:8080", client=(peer, 50123))
    answers = {}
    for method, path in (("GET", "/?tab=workflows"), ("HEAD", "/"), ("GET", "/favicon.ico")):
        response = client.request(method, path, headers={"accept": "text/html"}, follow_redirects=False)
        answers[f"{method} {path}"] = [response.status_code, response.headers.get("location", ""),
                                       response.headers.get("content-type", "")]
    print(json.dumps(answers))
''')

_MIDDLEWARE = textwrap.dedent('''
    import json
    import row_bot.app as server
    print(json.dumps([item.cls.__name__ for item in server.app.user_middleware]))
''')


def _probe(tmp_path, script: str, *args: str, **env: str):
    environment = {**os.environ, "ROW_BOT_DATA_DIR": str(tmp_path / "data"), "ROW_BOT_TEST_MODE": "1", **env}
    result = subprocess.run([sys.executable, "-c", script, *args], env=environment,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_this_computer_opens_react_from_the_root(tmp_path) -> None:
    answers = _probe(tmp_path, _FRONT_DOOR, "127.0.0.1")

    assert answers["GET /?tab=workflows"][:2] == [307, "/app-v2/?tab=workflows"]
    assert answers["HEAD /"][:2] == [307, "/app-v2/"]
    assert answers["GET /favicon.ico"][0] == 200
    assert answers["GET /favicon.ico"][2] in {"image/x-icon", "image/vnd.microsoft.icon"}


def test_a_remote_browser_connects_first_and_then_opens_react(tmp_path) -> None:
    answers = _probe(tmp_path, _FRONT_DOOR, "203.0.113.7",
                     ROW_BOT_DEPLOYMENT_MODE="server", ROW_BOT_ALLOWED_HOSTS="localhost")

    assert answers["GET /?tab=workflows"][0] == 303
    assert answers["GET /?tab=workflows"][1].startswith("/connect?next=%2Fapp-v2%2F")


def test_responses_are_compressed_outside_the_access_gate(tmp_path) -> None:
    # Outermost first: compression wraps the access gate (as it did under NiceGUI).
    assert _probe(tmp_path, _MIDDLEWARE) == ["GZipMiddleware", "AccessMiddleware"]
