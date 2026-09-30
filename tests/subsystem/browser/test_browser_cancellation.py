from __future__ import annotations

import queue

from row_bot.cancellation import CancellationScope, use_cancellation_scope
from row_bot.tools.browser_tool import BrowserSession


class _AliveThread:
    def is_alive(self) -> bool:
        return True


def test_browser_session_returns_stopped_when_generation_scope_already_cancelled() -> None:
    session = BrowserSession.__new__(BrowserSession)
    session._closed = False
    session._pw_thread = _AliveThread()
    session._work_q = queue.Queue()
    scope = CancellationScope()
    scope.cancel("test")

    with use_cancellation_scope(scope):
        result = session._run_on_pw_thread(lambda: "should not run")

    assert result == "Browser action stopped by user."
    assert session._work_q.empty()
