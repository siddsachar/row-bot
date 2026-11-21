"""``llama-server``'s router as Row-Bot's own model server talks to it, in process (no process, no port), for
checks of what loads when on a card that holds one model at a time (``--models-max 1``: two 27Bs on a 32 GB card).

An unload takes a few looks at the model list to finish, and until then the model is listed loaded. A load asked
for while another model is still loaded or unloading is refused ("model limit reached") and leaves the model
unloaded (15 Nov; 21 Nov: the starting 27B's load came while the trained 27B unloaded). Every load asked for,
refused and accepted is kept in order.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class OneAtATimeRouter:
    def __init__(self, *, unload_looks: int = 3, unloading: dict[str, int] | None = None) -> None:
        self.clock = Clock()
        self.unload_looks = unload_looks
        self.status: dict[str, str] = {}
        self.unloading: dict[str, int] = {}
        self.asked: list[str] = []
        self.refused: list[str] = []
        self.accepted: list[str] = []
        for name, looks in (unloading or {}).items():  # a model another check unloaded just before
            self.status[name], self.unloading[name] = "loaded", looks

    def request(self, method: str, url: str, *, key: str | None = None, body: Any = None,
                timeout: float | None = None) -> tuple[int, Any]:
        name = str((body or {}).get("model") or "")
        if url.endswith("/models/load"):
            self.asked.append(name)
            self.status.setdefault(name, "unloaded")
            if any(other != name and value != "unloaded" for other, value in self.status.items()):
                self.refused.append(name)
                return 400, {"error": {"message": "model limit reached"}}
            self.status[name] = "loaded"
            self.accepted.append(name)
            return 200, {"success": True}
        if url.endswith("/models/unload"):
            if self.status.get(name) == "loaded":
                self.unloading.setdefault(name, self.unload_looks)
            return 200, {"success": True}
        if "/models" in url:  # the list (and its reload)
            self.clock.now += 1.0  # each look is a second later
            for other in list(self.unloading):
                self.unloading[other] -= 1
                if self.unloading[other] <= 0:
                    del self.unloading[other]
                    self.status[other] = "unloaded"
            return 200, {"data": [{"id": item, "status": {"value": value}}
                                  for item, value in sorted(self.status.items())]}
        return 200, {"status": "ok"}


def managed_server(router: OneAtATimeRouter, root: Any, monkeypatch: Any) -> Any:
    """Row-Bot's own :class:`ManagedServer` for ``root``, running and talking to ``router`` (no waits)."""

    from row_bot.learning.training import llama_server

    connection = llama_server.Connection("http://127.0.0.1:1/v1", "key", 1)
    server = llama_server.ManagedServer(root, files=lambda: SimpleNamespace(installed=True, state="installed"),
                                        http=router, clock=router.clock, card_bytes=lambda: None, monitor=False)
    monkeypatch.setattr(server, "ensure", lambda: connection)
    monkeypatch.setattr(server, "_connection", connection)
    monkeypatch.setattr(server, "_process", SimpleNamespace(alive=lambda: True, pid=1))
    monkeypatch.setattr(llama_server.time, "sleep", lambda _seconds: None)
    return server
