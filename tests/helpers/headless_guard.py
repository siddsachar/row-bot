"""``python -m tests.helpers.headless_guard RECORD ARGS...``: Row-Bot's own command line, watched.

Runs ``row_bot.launcher.main(ARGS)`` in this process after refusing every connection and name lookup that
isn't loopback, and wrapping the install count's scheduler and the updater's check and scheduler, so a
test of ``row-bot run`` can read in RECORD (JSON lines) whatever tried to leave the machine or start.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import sys
from pathlib import Path
from typing import Any


def _loopback(host: Any) -> bool:
    text = str(host or "")
    if text in {"localhost", ""}:
        return True
    try:
        return ipaddress.ip_address(text.strip("[]")).is_loopback
    except ValueError:
        return False


def main() -> None:
    record = Path(sys.argv[1])

    def note(kind: str, detail: str) -> None:
        with record.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": kind, "detail": detail}) + "\n")

    connect, lookup = socket.socket.connect, socket.getaddrinfo

    def guarded_connect(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if self.family in {socket.AF_INET, socket.AF_INET6} and not _loopback(host):
            note("connect", str(host))
            raise OSError("this test allows loopback connections only")
        return connect(self, address)

    def guarded_lookup(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _loopback(host.decode() if isinstance(host, bytes) else host):
            note("lookup", str(host))
            raise socket.gaierror("this test looks up no names")
        return lookup(host, *args, **kwargs)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_lookup

    from row_bot import install_count, updater

    for module, name in ((install_count, "start"), (install_count, "tick"), (updater, "check_for_updates"),
                         (updater, "start_update_scheduler")):
        original = getattr(module, name)

        def watched(*args: Any, _name: str = f"{module.__name__}.{name}", _original: Any = original,
                    **kwargs: Any) -> Any:
            note("started", _name)
            return _original(*args, **kwargs)

        setattr(module, name, watched)

    from row_bot.launcher import main as row_bot

    row_bot(sys.argv[2:])


if __name__ == "__main__":
    main()
