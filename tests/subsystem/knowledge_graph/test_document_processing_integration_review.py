"""Adversarial review of captured response and durable worker lifetimes."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import gzip
import sys
from types import SimpleNamespace

import httpx
import pytest

from tests.subsystem.knowledge_graph.test_document_processing_policy import (
    admit,
    no_unreviewed_network as no_unreviewed_network,
    processing as processing,
)

pytestmark = [pytest.mark.subsystem, pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Strict document processing requires a descriptor-backed SQLite path bridge",
)]


@pytest.fixture
def response_owner(monkeypatch):
    from row_bot.providers import runtime
    from row_bot.providers.transports import cancellable_http
    state = {"chunks":[],"closed":0,"revoked":False,"headers":{},"cleanup_error":False,"now":0.0}
    sentinel = RuntimeError("review authority revoked")
    monkeypatch.setattr(runtime,"time",SimpleNamespace(monotonic=lambda:state["now"]))
    def validate():
        if state["revoked"]:
            raise sentinel
    def chunks():
        for item in state["chunks"]:
            if callable(item):
                item()
            else:
                yield item
    def close():
        state["closed"] += 1
        if state["cleanup_error"]:
            raise OSError("synthetic close failure")
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield from chunks()
        def close(self):
            close()
    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in chunks():
                yield chunk
        async def aclose(self):
            close()
    monkeypatch.setattr(cancellable_http,"cancellable_http_client",lambda **kwargs:
        httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,
            stream=Stream(),headers=state["headers"])),**kwargs))
    monkeypatch.setattr(cancellable_http,"cancellable_async_http_client",lambda **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,
            stream=AsyncStream(),headers=state["headers"])),**kwargs))
    def consume(asynchronous):
        with runtime.captured_http_clients("https://provider.invalid/v1",validate) as (sync,async_client):
            if asynchronous:
                return asyncio.run(async_client.get("https://provider.invalid/v1/response"))
            return sync.get("https://provider.invalid/v1/response")
    return state,sentinel,consume


@pytest.mark.parametrize("asynchronous",[False,True])
def test_compressed_provider_response_cannot_escape_byte_budget(response_owner,asynchronous):
    state,_,consume = response_owner
    compressed = gzip.compress(b"x" * (8 * 1024 * 1024 + 1))
    assert len(compressed) < 16384
    state["headers"] = {"Content-Encoding":"gzip"}
    state["chunks"] = [compressed]
    with pytest.raises(ValueError,match="response_too_large|response_encoding"):
        consume(asynchronous)
    assert state["closed"] == 1


@pytest.mark.parametrize("asynchronous",[False,True])
def test_cleanup_error_does_not_replace_original_authority_rejection(response_owner,asynchronous):
    state,sentinel,consume = response_owner
    state["chunks"] = [b"first",lambda:state.update(revoked=True),b"last"]
    state["cleanup_error"] = True
    with pytest.raises(RuntimeError) as caught:
        consume(asynchronous)
    assert caught.value is sentinel
    assert state["closed"] == 1


@pytest.mark.parametrize("asynchronous",[False,True])
def test_empty_final_stream_checks_observed_deadline(response_owner,asynchronous):
    state,_,consume = response_owner
    state["chunks"] = [lambda:state.update(now=120.0)]
    with pytest.raises(ValueError,match="response_deadline"):
        consume(asynchronous)
    assert state["closed"] == 1


@pytest.mark.parametrize("asynchronous",[False,True])
def test_standalone_stream_close_failure_remains_explicit(response_owner,asynchronous):
    state,_,consume = response_owner
    state["chunks"] = [b"valid"]
    state["cleanup_error"] = True
    with pytest.raises(OSError,match="synthetic close failure"):
        consume(asynchronous)
    assert state["closed"] == 1


@pytest.mark.parametrize("active_error",[False,True])
def test_client_cleanup_attempts_both_and_preserves_primary_error(monkeypatch,active_error):
    from row_bot.providers import runtime
    from row_bot.providers.transports import cancellable_http
    calls = []
    first,second,primary = OSError("first close"),OSError("second close"),RuntimeError("authority")
    def close():
        calls.append("sync")
        raise first
    async def aclose():
        calls.append("async")
        raise second
    monkeypatch.setattr(cancellable_http,"cancellable_http_client",lambda **kwargs:SimpleNamespace(close=close))
    monkeypatch.setattr(cancellable_http,"cancellable_async_http_client",lambda **kwargs:SimpleNamespace(aclose=aclose))
    with pytest.raises((OSError,RuntimeError)) as caught:
        with runtime.captured_http_clients("https://provider.invalid/v1",lambda:None):
            if active_error:
                raise primary
    assert caught.value is (primary if active_error else first)
    assert calls == ["sync","async"]


def test_async_client_construction_failure_closes_created_sync_client(monkeypatch):
    from row_bot.providers import runtime
    from row_bot.providers.transports import cancellable_http
    calls = []
    primary = RuntimeError("construction failed")
    def close():
        calls.append("sync")
        raise OSError("cleanup failure")
    def fail(**kwargs):
        raise primary
    monkeypatch.setattr(cancellable_http,"cancellable_http_client",lambda **kwargs:SimpleNamespace(close=close))
    monkeypatch.setattr(cancellable_http,"cancellable_async_http_client",fail)
    with pytest.raises(RuntimeError) as caught:
        with runtime.captured_http_clients("https://provider.invalid/v1",lambda:None):
            pytest.fail("construction must fail")
    assert caught.value is primary and calls == ["sync"]


@pytest.mark.parametrize("change",["resolver_removed","session_replaced","proof_removed"])
def test_active_worker_scope_rechecks_current_resolver_before_embedding(processing,monkeypatch,change):
    api,service,policy,batch,*_ = processing
    from row_bot import embedding_providers
    from row_bot.runtime import admissions
    command,_ = admit(processing)
    state = {"embedded":0,"closed":0}
    class Fake:
        def embed_documents(self,texts):
            state["embedded"] += 1
            return [[1.0] for _ in texts]
    @contextmanager
    def captured(capture,*,validate):
        try:
            yield embedding_providers._CapturedEmbeddings(Fake(),validate)
        finally:
            state["closed"] += 1
    monkeypatch.setattr(embedding_providers,"captured_embedding_provider",captured)
    with service.processing_scope(batch) as worker:
        if change == "resolver_removed":
            service.processing_policy_resolver = None
        elif change == "session_replaced":
            replacement = api.DocumentProcessingPolicy(read_context=policy.read_context,
                validate=lambda:None,validate_action=lambda action:None,
                owner_id=policy.owner_id,authority_id="replacement-session",conversation_id=policy.conversation_id)
            service.processing_policy_resolver = lambda proof:replacement
        else:
            with admissions.transaction() as conn:
                conn.execute("DELETE FROM client_commands WHERE command_id=?",(command["command_id"],))
        with pytest.raises(Exception,match="reviewed policy|authority_changed|proof_unavailable"):
            worker.embedding.embed_documents(["synthetic private source"])
    assert state == {"embedded":0,"closed":1}
