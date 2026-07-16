from __future__ import annotations

import concurrent.futures

from row_bot.cancellation import CancellationScope
from row_bot.tools.browser_tool import _BrowserWorkItem


def test_cancelled_queued_browser_work_cannot_begin_dispatch() -> None:
    called = []
    future = concurrent.futures.Future()
    scope = CancellationScope()
    work = _BrowserWorkItem(lambda: called.append(True), future, scope)
    scope.cancel("stop")
    work.cancel()
    assert work.begin_dispatch() is False
    assert called == []
    assert future.cancelled()
