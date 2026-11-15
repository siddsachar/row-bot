"""``row-bot mcp``: the stdio process an editor starts to reach the running Row-Bot.

It does no work itself. It speaks MCP (newline-delimited JSON-RPC 2.0 on stdin and stdout, with
the standard library only), lists Row-Bot's three tools without asking the app, and sends each
tool call to the running app over loopback, ``http://127.0.0.1:<port>`` from the data folder's
launcher state, with this editor's token from the keychain. No proxy is ever used, no other
address is ever tried, and the token is never logged. With Row-Bot closed, every call answers
"Row-Bot isn't running". Nothing but protocol messages goes to stdout; logs go to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import IO, Any

from row_bot.editors import catalog

logger = logging.getLogger(__name__)

LAUNCHER_STATE = "launcher_state.json"
CALL_PATH = "/api/editors/call"
LOOPBACK = "127.0.0.1"
PROGRESS_SECONDS = 10.0
STARTING = "Row-Bot is starting. Try again in a moment."
INSTRUCTIONS = ("Row-Bot is the person's own assistant on this computer. Use ask_row_bot to ask it something "
                "or hand it a task, search_memory to look up what it remembers about the person, and "
                "list_conversations to find a Row-Bot chat to continue.")

# One tool call: (editor id, tool, arguments) -> (text, is_error).
Caller = Callable[[str, str, dict], tuple[str, bool]]


class _Unavailable(Exception):
    pass


def _app_port() -> tuple[int, str] | None:
    """The running app's port and phase, from the launcher state in the data folder."""
    from row_bot.data_paths import get_row_bot_data_dir

    try:
        state = json.loads((get_row_bot_data_dir(create=False) / LAUNCHER_STATE).read_text(encoding="utf-8"))
        port = int(state["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not 1 <= port <= 65535:
        return None
    return port, str(state.get("phase") or "")


def call_app(editor_id: str, tool: str, arguments: dict, *, timeout: float = 660.0) -> tuple[str, bool]:
    """Send one tool call to the running app on this computer."""
    found = _app_port()
    if found is None:
        return catalog.NOT_RUNNING, True
    port, phase = found
    from row_bot.editors.store import read_token

    token = read_token(editor_id)
    if not token:
        return catalog.NOT_CONNECTED, True
    request = urllib.request.Request(
        f"http://{LOOPBACK}:{port}{CALL_PATH}",
        data=json.dumps({"tool": tool, "arguments": arguments}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
        method="POST",
    )
    # Never a proxy: HTTP_PROXY and the system's proxy settings would send the token elsewhere.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            body = json.loads(response.read(4 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return catalog.NOT_CONNECTED, True
        logger.warning("Row-Bot refused the call (%s)", exc.code)
        return f"Row-Bot refused the call ({exc.code}).", True
    except (urllib.error.URLError, OSError, ValueError):
        return (STARTING if phase == "starting" else catalog.NOT_RUNNING), True
    if not isinstance(body, dict) or not isinstance(body.get("text"), str):
        return "Row-Bot's answer couldn't be read.", True
    return body["text"], bool(body.get("is_error"))


class Bridge:
    """One editor's MCP session over a pair of byte streams."""

    def __init__(self, editor_id: str, output: IO[bytes], *, caller: Caller | None = None,
                 progress_seconds: float = PROGRESS_SECONDS) -> None:
        self.editor_id = editor_id
        self.output = output
        self.caller = caller or call_app
        self.progress_seconds = progress_seconds
        self._lock = threading.Lock()
        self._calls: list[threading.Thread] = []

    def send(self, message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            self.output.write(data)
            self.output.flush()

    def _reply(self, request_id: Any, result: dict) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _fail(self, request_id: Any, code: int, message: str) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def handle_line(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            message = json.loads(line)
        except ValueError:
            self._fail(None, -32700, "Parse error")
            return
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            self._fail(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid request")
            return
        method, params = message["method"], message.get("params")
        params = params if isinstance(params, dict) else {}
        if "id" not in message:
            return  # A notification (initialized, cancelled, ...): nothing to answer.
        request_id = message["id"]
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in catalog.PROTOCOL_VERSIONS else catalog.PROTOCOL_VERSIONS[0]
            from row_bot.version import __version__

            self._reply(request_id, {"protocolVersion": version,
                                     "capabilities": {"tools": {"listChanged": False}},
                                     "serverInfo": {"name": catalog.SERVER_NAME, "title": "Row-Bot",
                                                    "version": __version__},
                                     "instructions": INSTRUCTIONS})
        elif method == "ping":
            self._reply(request_id, {})
        elif method == "tools/list":
            self._reply(request_id, {"tools": list(catalog.TOOLS)})
        elif method == "tools/call":
            name, arguments = params.get("name"), params.get("arguments")
            if name not in catalog.TOOL_NAMES:
                self._fail(request_id, -32602, f"Unknown tool: {str(name)[:64]}")
                return
            if arguments is not None and not isinstance(arguments, dict):
                self._fail(request_id, -32602, "Arguments must be an object")
                return
            meta = params.get("_meta")
            token = meta.get("progressToken") if isinstance(meta, dict) else None
            thread = threading.Thread(target=self._call, args=(request_id, str(name), arguments or {}, token),
                                      name=f"row-bot-mcp-{name}", daemon=True)
            self._calls.append(thread)
            thread.start()
        else:
            self._fail(request_id, -32601, f"Method not found: {method[:64]}")

    def _call(self, request_id: Any, name: str, arguments: dict, progress_token: Any) -> None:
        done = threading.Event()
        if progress_token is not None and isinstance(progress_token, (str, int)):
            def keep_posted() -> None:
                count = 0
                while not done.wait(self.progress_seconds):
                    count += 1
                    self.send({"jsonrpc": "2.0", "method": "notifications/progress",
                               "params": {"progressToken": progress_token, "progress": count,
                                          "message": "Row-Bot is working on it…"}})
            threading.Thread(target=keep_posted, name="row-bot-mcp-progress", daemon=True).start()
        try:
            text, is_error = self.caller(self.editor_id, name, arguments)
        except Exception:
            logger.warning("A %s call failed", name, exc_info=True)
            text, is_error = "Row-Bot couldn't run that.", True
        finally:
            done.set()
        self._reply(request_id, {"content": [{"type": "text", "text": text}], "isError": is_error})

    def wait(self, timeout: float | None = None) -> None:
        for thread in list(self._calls):
            thread.join(timeout)


def serve(stdin: IO[bytes], stdout: IO[bytes], editor_id: str, *, caller: Caller | None = None) -> int:
    """Answer the editor until it closes stdin; calls still running finish first."""
    bridge = Bridge(editor_id, stdout, caller=caller)
    for line in iter(stdin.readline, b""):
        bridge.handle_line(line)
    bridge.wait()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="row-bot mcp",
                                     description="Serve Row-Bot to an editor over MCP (stdio).")
    parser.add_argument("--editor", required=True, choices=sorted(catalog.EDITORS),
                        help="The editor this runs for (its token is in the keychain).")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True,
                        format="row-bot mcp: %(levelname)s %(message)s")
    return serve(sys.stdin.buffer, sys.stdout.buffer, args.editor)
