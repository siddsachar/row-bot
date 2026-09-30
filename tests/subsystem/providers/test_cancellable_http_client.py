import asyncio
import threading

import httpx

from row_bot.cancellation import CancellationScope, use_cancellation_scope
from row_bot.providers.transports.cancellable_http import cancellable_async_http_client, cancellable_http_client


class _BlockingByteStream(httpx.SyncByteStream):
    """A response body that blocks on read until told to close.

    Mirrors a stalled network read from a real provider SDK: the reader is
    parked past the point where it can observe cancellation until something
    closes the underlying connection out from under it.
    """

    def __init__(self) -> None:
        self.reading = threading.Event()
        self.closed = threading.Event()
        self.close_calls = 0

    def __iter__(self):
        self.reading.set()
        assert self.closed.wait(timeout=1), "stream was never closed"
        return iter(())

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


class _CloseCountingAsyncStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.close_calls = 0

    async def __aiter__(self):
        if False:
            yield b""

    async def aclose(self) -> None:
        self.close_calls += 1


def test_cancellable_http_client_closes_response_when_scope_is_cancelled():
    stream = _BlockingByteStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    client = cancellable_http_client(transport=httpx.MockTransport(handler))
    scope = CancellationScope()
    result: dict[str, object] = {}

    def consume() -> None:
        with use_cancellation_scope(scope):
            with client.stream("GET", "http://example.test/stream") as response:
                result["status_code"] = response.status_code
                list(response.iter_bytes())

    worker = threading.Thread(target=consume)
    worker.start()
    assert stream.reading.wait(timeout=1)

    scope.cancel("test")
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert stream.close_calls == 1
    assert result["status_code"] == 200


def test_cancellable_http_client_no_active_scope_is_a_no_op():
    stream = _BlockingByteStream()
    stream.closed.set()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    client = cancellable_http_client(transport=httpx.MockTransport(handler))

    with client.stream("GET", "http://example.test/stream") as response:
        list(response.iter_bytes())

    assert stream.close_calls == 1


def test_cancellable_http_client_preserves_caller_event_hooks():
    seen: list[str] = []

    def custom_hook(response: httpx.Response) -> None:
        seen.append("custom")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = cancellable_http_client(
        transport=httpx.MockTransport(handler),
        event_hooks={"response": [custom_hook]},
    )

    client.get("http://example.test/ping")

    assert seen == ["custom"]


def test_cancellable_async_http_client_closes_response_when_scope_is_cancelled():
    async def run_case() -> None:
        client = cancellable_async_http_client()
        stream = _CloseCountingAsyncStream()
        response = httpx.Response(200, request=httpx.Request("GET", "http://example.test/stream"), stream=stream)
        scope = CancellationScope()

        try:
            with use_cancellation_scope(scope):
                for hook in client.event_hooks["response"]:
                    await hook(response)

            assert stream.close_calls == 0
            scope.cancel("test")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert stream.close_calls == 1
        finally:
            await client.aclose()

    asyncio.run(run_case())
