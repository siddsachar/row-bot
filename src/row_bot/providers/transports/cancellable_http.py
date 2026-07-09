"""httpx clients that let an active CancellationScope abort in-flight requests.

Row-Bot's own transports (``openai_compatible.py``, ``codex_responses.py``,
etc.) stream raw HTTP themselves and register the in-flight response with
``current_cancellation_scope()`` so Stop can close a stalled connection.
Chat models built directly from provider SDK wrappers delegate all networking
to those SDKs, which gives Row-Bot no such hook by default -- a stalled stream
on those models cannot be aborted by Stop and leaves the thread stuck
"Thinking" until the app is restarted. Passing clients built here into SDKs
that support caller-owned HTTP clients closes that gap without needing a full
custom transport.
"""

from __future__ import annotations

import asyncio

import httpx

from row_bot.cancellation import current_cancellation_scope


def _register_response_with_active_scope(response: httpx.Response) -> None:
    scope = current_cancellation_scope()
    if scope is None:
        return
    close = getattr(response, "close", None)
    if callable(close):
        scope.register(close, "cancellable_http_client.response.close")


async def _register_async_response_with_active_scope(response: httpx.Response) -> None:
    scope = current_cancellation_scope()
    if scope is None:
        return
    aclose = getattr(response, "aclose", None)
    if not callable(aclose):
        return
    loop = asyncio.get_running_loop()

    def close_response() -> None:
        try:
            loop.call_soon_threadsafe(lambda: loop.create_task(aclose()))
        except RuntimeError:
            pass

    scope.register(close_response, "cancellable_async_http_client.response.aclose")


def cancellable_http_client(**kwargs: object) -> httpx.Client:
    """Build an ``httpx.Client`` whose responses can be closed by Stop.

    Each response made through the returned client registers its ``close``
    with whatever ``CancellationScope`` is active on the calling thread, so a
    stalled read is aborted the same way Row-Bot's own streaming transports
    already behave.
    """

    event_hooks = dict(kwargs.pop("event_hooks", None) or {})
    event_hooks["response"] = [*event_hooks.get("response", []), _register_response_with_active_scope]
    return httpx.Client(event_hooks=event_hooks, **kwargs)


def cancellable_async_http_client(**kwargs: object) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` whose responses can be closed by Stop."""

    event_hooks = dict(kwargs.pop("event_hooks", None) or {})
    event_hooks["response"] = [*event_hooks.get("response", []), _register_async_response_with_active_scope]
    return httpx.AsyncClient(event_hooks=event_hooks, **kwargs)
