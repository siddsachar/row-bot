"""Row-Bot's tools, listed to Claude Code as an MCP server that runs nothing.

Claude Code starts this once per run (``python -I -S -B claude_plan_tools.py <tools.json> <results folder>``).
It answers ``initialize`` and ``tools/list`` with the tools in the file. A ``tools/call`` runs nothing: it
waits until Row-Bot's agent loop has run that tool call itself, through its own approval gate, and written
the result into the results folder (``results-<n>.json``, see ``claude_plan_cli``), then answers with that
result. When the folder is gone the run is over, and a call still waiting is refused. Standard library only;
it reads nothing but its tools file and the results folder.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

REFUSAL = "Row-Bot runs its tools itself, after its own approval check. Nothing ran here."
_POLL_SECONDS = 0.05


def _same(arguments: object, expected: object) -> bool:
    return json.dumps(arguments, sort_keys=True) == json.dumps(expected, sort_keys=True)


class Results:
    """The results Row-Bot wrote, each handed to one ``tools/call``: the first not yet used with the same name
    and arguments, else the first not yet used with the same name."""

    def __init__(self, folder: Path | None) -> None:
        self.folder = folder
        self.used: set[tuple[str, int]] = set()
        # A results file is written whole once and never changes, so each is read once.
        self.read: dict[str, list] = {}

    def _entries(self) -> list[tuple[tuple[str, int], dict]]:
        assert self.folder is not None
        for path in sorted(self.folder.glob("results-*.json")):
            if path.name not in self.read:
                try:
                    rows = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                self.read[path.name] = rows if isinstance(rows, list) else []
        return [((name, index), row) for name, rows in sorted(self.read.items())
                for index, row in enumerate(rows) if isinstance(row, dict)]

    def take(self, name: str, arguments: object) -> dict | None:
        """The result for one call, once Row-Bot has written it; None when the run is over."""
        while self.folder is not None and self.folder.is_dir():
            open_entries = [(key, row) for key, row in self._entries() if key not in self.used and row.get("name") == name]
            match = next((entry for entry in open_entries if _same(arguments, entry[1].get("arguments"))), None)
            match = match or (open_entries[0] if open_entries else None)
            if match is not None:
                self.used.add(match[0])
                return {"isError": bool(match[1].get("isError")), "content": match[1].get("content") or []}
            time.sleep(_POLL_SECONDS)
        return None


def answer(message: object, tools: list, results: Results | None = None) -> dict | None:
    """The JSON-RPC reply to one message; None for a notification, which gets none."""
    if not isinstance(message, dict) or "id" not in message:
        return None
    method = message.get("method")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "initialize":
        result: dict = {"protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
                        "capabilities": {"tools": {}}, "serverInfo": {"name": "row-bot", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": tools}
    elif method == "tools/call":
        found = results.take(str(params.get("name") or ""), params.get("arguments")) if results else None
        result = found or {"isError": True, "content": [{"type": "text", "text": REFUSAL}]}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def main(argv: list[str]) -> int:
    with open(argv[1], encoding="utf-8") as handle:
        tools = json.load(handle)
    results = Results(Path(argv[2]) if len(argv) > 2 else None)
    for raw in sys.stdin.buffer:
        try:
            message = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        reply = answer(message, tools, results)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
