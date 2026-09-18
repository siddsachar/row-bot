from __future__ import annotations

from types import ModuleType, SimpleNamespace
import sys
import hashlib
import asyncio
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest

pytestmark = [pytest.mark.subsystem, pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Strict document processing requires a descriptor-backed SQLite path bridge",
)]


@pytest.fixture(autouse=True)
def no_unreviewed_network(monkeypatch):
    import socket
    from contextvars import ContextVar
    in_socketpair = ContextVar("test_asyncio_socketpair",default=False)
    original_connect,original_pair = socket.socket.connect,socket.socketpair
    def connect(sock,*args,**kwargs):
        if not in_socketpair.get():
            pytest.fail("unexpected socket connection")
        return original_connect(sock,*args,**kwargs)
    def pair(*args,**kwargs):
        token = in_socketpair.set(True)
        try:
            return original_pair(*args,**kwargs)
        finally:
            in_socketpair.reset(token)
    monkeypatch.setattr(socket.socket,"connect",connect)
    monkeypatch.setattr(socket,"socketpair",pair)


@pytest.fixture
def runtime(monkeypatch):
    from row_bot.providers import runtime
    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider: "captured-secret")
    return runtime


def fake_openai(monkeypatch):
    calls = []
    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.chat = SimpleNamespace(completions=object())
            self.embeddings = object()
    sdk = ModuleType("openai")
    sdk.OpenAI = sdk.AsyncOpenAI = Client
    module = ModuleType("langchain_openai")
    class Chat:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
        def invoke(self, messages):
            return SimpleNamespace(content="accepted", messages=messages)
    module.ChatOpenAI = Chat
    monkeypatch.setitem(sys.modules, "openai", sdk)
    monkeypatch.setitem(sys.modules, "langchain_openai", module)
    return calls


def test_capture_does_not_construct_or_call_provider(runtime, monkeypatch):
    calls = fake_openai(monkeypatch)
    captured = runtime.capture_chat_runtime("model:openai:gpt-4o")
    assert calls == []
    assert captured.resolved.provider_id == "openai"
    assert captured.credential == "captured-secret"
    assert "captured-secret" not in repr(captured)


def test_strict_factory_binds_credentials_endpoint_and_disables_environment(runtime, monkeypatch):
    calls = fake_openai(monkeypatch)
    captured = runtime.capture_chat_runtime("model:openai:gpt-4o")
    monkeypatch.setattr(runtime, "get_provider_secret", lambda provider: pytest.fail("credential reread"))
    monkeypatch.setenv("OPENAI_API_KEY", "new-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foreign.invalid/v1")
    monkeypatch.setenv("OPENAI_PROJECT", "foreign-project")
    with runtime.captured_chat_model(captured, validate=lambda: None) as model:
        assert model.invoke(["synthetic"]).content == "accepted"
        assert model._model.kwargs["max_retries"] == 0
    assert len(calls) == 2
    for kwargs in calls:
        assert kwargs["api_key"] == "captured-secret"
        assert kwargs["base_url"] == "https://api.openai.com/v1"
        assert kwargs["project"] == "" and kwargs["organization"] == ""
        assert kwargs["max_retries"] == 0
        assert kwargs["http_client"].follow_redirects is False
        assert kwargs["http_client"].is_closed


def test_revocation_after_construction_blocks_invoke(runtime, monkeypatch):
    fake_openai(monkeypatch)
    captured = runtime.capture_chat_runtime("model:openai:gpt-4o")
    revoked = False
    sentinel = RuntimeError("synthetic authority rejection")
    def validate():
        if revoked:
            raise sentinel
    with runtime.captured_chat_model(captured, validate=validate) as model:
        revoked = True
        with pytest.raises(RuntimeError) as caught:
            model.invoke(["synthetic"])
        assert caught.value is sentinel


def test_actual_request_hook_revalidates_and_rejects_endpoint_escape(runtime):
    revoked = False
    def validate():
        if revoked:
            raise RuntimeError("revoked")
    with runtime.captured_http_clients("https://provider.invalid/v1", validate) as (sync, _):
        hook = sync.event_hooks["request"][0]
        hook(httpx.Request("POST", "https://provider.invalid/v1/chat/completions"))
        with pytest.raises(ValueError, match="endpoint_changed"):
            hook(httpx.Request("POST", "https://provider.invalid/other"))
        with pytest.raises(ValueError, match="endpoint_changed"):
            hook(httpx.Request("POST", "https://foreign.invalid/v1/chat/completions"))
        revoked = True
        with pytest.raises(RuntimeError, match="revoked"):
            hook(httpx.Request("POST", "https://provider.invalid/v1/chat/completions"))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("outcome", ["complete", "revoked", "bytes", "deadline", "early_close"])
def test_captured_response_stream_bounds_and_cleanup(runtime, monkeypatch, asynchronous, outcome):
    from row_bot.providers.transports import cancellable_http
    now, state = [10.0], {"revoked":False,"closed":0,"requests":0}
    sentinel = RuntimeError("authority revoked during response")
    monkeypatch.setattr(runtime.time,"monotonic",lambda:now[0])
    def validate():
        if state["revoked"]:
            raise sentinel
    def chunks():
        yield b"first"
        if outcome == "revoked":
            state["revoked"] = True
        if outcome == "deadline":
            now[0] += 120
        yield b"x" * (8 * 1024 * 1024) if outcome == "bytes" else b"last"
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield from chunks()
        def close(self):
            state["closed"] += 1
    class AsyncStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in chunks():
                yield chunk
        async def aclose(self):
            state["closed"] += 1
    def respond(request):
        state["requests"] += 1
        return httpx.Response(200,stream=AsyncStream() if asynchronous else Stream())
    monkeypatch.setattr(cancellable_http,"cancellable_http_client",
        lambda **kwargs:httpx.Client(transport=httpx.MockTransport(respond),**kwargs))
    monkeypatch.setattr(cancellable_http,"cancellable_async_http_client",
        lambda **kwargs:httpx.AsyncClient(transport=httpx.MockTransport(respond),**kwargs))
    received = []
    async def consume(client):
        async with client.stream("POST","https://provider.invalid/v1/chat") as response:
            async for chunk in response.aiter_raw():
                received.append(chunk)
                if outcome == "early_close":
                    break
    with runtime.captured_http_clients("https://provider.invalid/v1",validate) as (sync, async_client):
        def run():
            if asynchronous:
                asyncio.run(consume(async_client))
            else:
                with sync.stream("POST","https://provider.invalid/v1/chat") as response:
                    for chunk in response.iter_raw():
                        received.append(chunk)
                        if outcome == "early_close":
                            break
        if outcome in {"revoked","bytes","deadline"}:
            with pytest.raises((RuntimeError,ValueError)) as caught:
                run()
            if outcome == "revoked":
                assert caught.value is sentinel
            else:
                assert str(caught.value) == "document_provider_response_" + ("too_large" if outcome == "bytes" else "deadline")
        else:
            run()
    assert received == ([b"first",b"last"] if outcome == "complete" else [b"first"])
    assert state["closed"] == 1 and state["requests"] == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_captured_buffered_response_is_bounded(runtime, asynchronous):
    with runtime.captured_http_clients("https://provider.invalid/v1",lambda:None) as (sync, async_client):
        request = httpx.Request("POST","https://provider.invalid/v1/chat")
        sync.event_hooks["request"][0](request)
        response = httpx.Response(200,request=request,content=b"x" * (8 * 1024 * 1024 + 1))
        with pytest.raises(ValueError,match="response_too_large"):
            if asynchronous:
                asyncio.run(async_client.event_hooks["response"][0](response))
            else:
                sync.event_hooks["response"][0](response)
        assert response.is_closed


@pytest.mark.parametrize("provider", ["codex", "claude_subscription", "xai_oauth"])
def test_missing_captured_subscription_never_falls_back(runtime, monkeypatch, provider):
    from row_bot.providers import auth_store
    monkeypatch.setattr(auth_store,"read_provider_oauth_bundle_snapshot",lambda provider:subscription_snapshot(provider,token=""))
    with pytest.raises(ValueError, match="provider_unavailable"):
        runtime.capture_chat_runtime("model:" + provider + ":synthetic")


def subscription_snapshot(provider, *, token="captured-token"):
    method = "oauth_device" if provider == "codex" else "oauth_pkce"
    return ({"access_token":token,"refresh_token":"captured-refresh","id_token":"",
             "account":"captured-account","user_id":"captured-user"},
            {"auth_method":method,"source":method,"expires_at":"2030-01-01T00:00:00+00:00"},"a"*64)


def test_captured_configuration_is_independent_of_mutable_custom_endpoint(runtime, monkeypatch):
    from row_bot.providers import resolution
    endpoint = {"enabled":True,"base_url":"https://provider.invalid/v1","auth_required":True,
                "transport":"openai_chat","headers":{"X-Synthetic":"initial"}}
    from row_bot.providers import custom
    monkeypatch.setattr(custom, "get_custom_endpoint", lambda provider: endpoint)
    monkeypatch.setattr(runtime, "custom_endpoint_secret", lambda provider: "captured-secret")
    captured = runtime.capture_chat_runtime("model:custom_openai_test:synthetic")
    endpoint["headers"]["X-Synthetic"] = "changed"
    assert captured.resolved.endpoint["headers"]["X-Synthetic"] == "initial"
    assert resolution.resolve_provider_config("model:custom_openai_test:synthetic").endpoint["headers"]["X-Synthetic"] == "changed"


def test_cloud_embedding_factory_does_not_reuse_legacy_cached_account(runtime, monkeypatch):
    from row_bot import embedding_providers as embeddings
    from row_bot.providers import auth_store
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda provider: "embedding-captured")
    captured = embeddings.capture_embedding_runtime({"provider":"cloud","cloud_model":"openai:text-embedding-3-small"})
    calls = fake_openai(monkeypatch)
    class FakeEmbedding:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
        def embed_documents(self, texts):
            return [[1.0] for text in texts]
    sys.modules["langchain_openai"].OpenAIEmbeddings = FakeEmbedding
    monkeypatch.setattr(embeddings, "get_embedding_provider", lambda *a:pytest.fail("mutable global cache"))
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda provider:pytest.fail("credential reread"))
    monkeypatch.setenv("OPENAI_API_KEY", "foreign")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://foreign.invalid")
    with embeddings.captured_embedding_provider(captured, validate=lambda:None) as provider:
        assert provider.embed_documents(["synthetic"]) == [[1.0]]
        assert provider.inner.kwargs["max_retries"] == 0
    assert all(call["api_key"] == "embedding-captured" for call in calls)
    assert all(call["base_url"] == "https://api.openai.com/v1" for call in calls)


def test_real_google_wrappers_use_explicit_nonvertex_captured_client(runtime, monkeypatch):
    from google import genai
    from row_bot import embedding_providers as embeddings
    from row_bot.providers import auth_store
    calls = []
    def client(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(models=object())
    monkeypatch.setattr(genai, "Client", client)
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda provider:"captured-secret")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "foreign")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "https://foreign.invalid")
    chat = runtime.capture_chat_runtime("model:google:gemini-2.5-flash")
    with runtime.captured_chat_model(chat, validate=lambda:None) as model:
        assert model._model.vertexai is False
    embedding = embeddings.capture_embedding_runtime({"provider":"cloud","cloud_model":"google:gemini-embedding-001"})
    with embeddings.captured_embedding_provider(embedding, validate=lambda:None) as provider:
        assert provider.inner.vertexai is False
    assert len(calls) == 2
    for kwargs in calls:
        assert kwargs["vertexai"] is False
        assert kwargs["api_key"] == "captured-secret"
        assert kwargs["http_options"]["base_url"] == "https://generativelanguage.googleapis.com"
        assert kwargs["http_options"]["retry_options"] == {"attempts":1}
        assert kwargs["http_options"]["httpx_client"].is_closed


def test_real_anthropic_wrapper_binds_both_sdk_clients(runtime, monkeypatch):
    import anthropic
    calls = []
    def client(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()
    monkeypatch.setattr(anthropic, "Client", client)
    monkeypatch.setattr(anthropic, "AsyncClient", client)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://foreign.invalid")
    monkeypatch.setenv("ANTHROPIC_PROXY", "https://foreign.invalid")
    captured = runtime.capture_chat_runtime("model:anthropic:claude-sonnet-4-5")
    with runtime.captured_chat_model(captured, validate=lambda:None) as model:
        assert model._model._client is not None
        assert model._model._async_client is not None
    assert len(calls) == 2
    assert all(call["api_key"] == "captured-secret" for call in calls)
    assert all(call["base_url"] == "https://api.anthropic.com" for call in calls)
    assert all(call["max_retries"] == 0 for call in calls)


def test_nested_embedding_scope_reuses_exact_provider_and_restores_context(monkeypatch):
    from row_bot import embedding_providers as owner
    cfg = {"provider":"cloud","cloud_model":"openai:text-embedding-3-small"}
    first,second = object(),object()
    monkeypatch.setattr(owner, "get_embedding_config", lambda:dict(cfg))
    with owner.use_captured_embeddings(cfg, first, lambda:None):
        assert owner.get_embedding_provider() is first
        assert owner.get_embedding_provider_for_recall(cfg) is first
        with owner.use_captured_embeddings(cfg, second, lambda:None):
            assert owner.get_embedding_provider() is second
        assert owner.get_embedding_provider() is first
        with pytest.raises(ValueError, match="policy_changed"):
            owner.get_embedding_provider({**cfg,"cloud_model":"google:gemini-embedding-001"})
    assert owner._captured_provider.get() is None


def test_embedding_scope_does_not_leak_across_threads(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from row_bot import embedding_providers as owner
    cfg = {"provider":"local","local_model":"mxbai-large-v1"}
    with owner.use_captured_embeddings(cfg, object(), lambda:None):
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(owner._captured_provider.get).result() is None


def test_local_queued_inference_rechecks_authority_after_executor_admission(monkeypatch):
    from concurrent.futures import Future
    from row_bot import embedding_providers as owner
    calls = []
    revoked = False
    def validate():
        if revoked:
            raise RuntimeError("revoked while waiting")
    class Executor:
        def submit(self, function):
            nonlocal revoked
            revoked = True
            future = Future()
            try:
                future.set_result(function())
            except Exception as error:
                future.set_exception(error)
            return future
    monkeypatch.setattr(owner, "_LOCAL_EMBEDDING_EXECUTOR", Executor())
    inner = SimpleNamespace(embed_documents=lambda texts:calls.append(texts))
    adapted = owner._DimensionAdapter(owner._SerializedLocalEmbeddings(inner), 256)
    provider = owner._CapturedEmbeddings(adapted, validate)
    with pytest.raises(RuntimeError, match="waiting"):
        provider.embed_documents(["synthetic"])
    assert calls == []


def test_contradiction_callback_keeps_original_rejection_and_legacy_default(monkeypatch):
    from row_bot.tools.memory_tool import _check_contradiction
    sentinel = RuntimeError("authority rejected")
    def invoke(prompt):
        raise sentinel
    with pytest.raises(RuntimeError) as caught:
        _check_contradiction("old", "new", "subject", invoke=invoke)
    assert caught.value is sentinel
    assert _check_contradiction("old", "new", "subject", invoke=lambda prompt:"NO") is None


@pytest.fixture
def raw_copy(tmp_path, monkeypatch):
    from row_bot import document_extraction as extraction, wiki_vault
    root = tmp_path / "vault"
    root.mkdir()
    source = tmp_path / "source.txt"
    source.write_bytes(b"synthetic original bytes")
    monkeypatch.setattr(wiki_vault, "is_enabled", lambda:True)
    monkeypatch.setattr(wiki_vault, "get_vault_path", lambda:root)
    return extraction,root,source,{"document_id":"synthetic-id","original_name":"source.txt",
        "expected_sha256":hashlib.sha256(source.read_bytes()).hexdigest()}


def test_strict_raw_copy_retains_source_and_exact_owned_retry(raw_copy):
    extraction,root,source,options = raw_copy
    result = extraction._copy_to_vault_raw(str(source), "source.txt", validate=lambda:None, **options)
    assert result.read_bytes() == source.read_bytes()
    assert extraction._copy_to_vault_raw(str(source), "source.txt", validate=lambda:None, **options) == result
    result.write_bytes(b"external edit")
    with pytest.raises(ValueError, match="copy_conflict"):
        extraction._copy_to_vault_raw(str(source), "source.txt", validate=lambda:None, **options)
    assert result.read_bytes() == b"external edit"
    assert source.read_bytes() == b"synthetic original bytes"


def test_strict_raw_copy_revocation_after_fsync_never_publishes(raw_copy, monkeypatch):
    extraction,root,source,options = raw_copy
    revoked = False
    original = extraction.os.fsync
    def fsync(fd):
        nonlocal revoked
        original(fd)
        revoked = True
    monkeypatch.setattr(extraction.os, "fsync", fsync)
    sentinel = RuntimeError("revoked")
    def validate():
        if revoked:
            raise sentinel
    with pytest.raises(RuntimeError) as caught:
        extraction._copy_to_vault_raw(str(source), "source.txt", validate=validate, **options)
    assert caught.value is sentinel
    assert not (root / "raw/source.txt").exists()
    assert (root / "raw/.source.txt.synthetic-id.copying").read_bytes() == source.read_bytes()


def test_strict_raw_copy_wrong_source_hash_retains_candidate_without_adopting(raw_copy):
    extraction,root,source,options = raw_copy
    source.write_bytes(b"changed outside the saved source")
    with pytest.raises(ValueError, match="source_changed"):
        extraction._copy_to_vault_raw(str(source), "source.txt", validate=lambda:None, **options)
    assert not (root / "raw/source.txt").exists()
    assert (root / "raw/.source.txt.synthetic-id.copying").read_bytes() == source.read_bytes()


@pytest.fixture
def snapshot_job(tmp_path, monkeypatch):
    from row_bot import document_jobs as jobs
    monkeypatch.setattr(jobs, "_wake_supervisor", lambda:None)
    service = jobs.DocumentJobService(tmp_path / "data")
    batch = service.create_batch()
    job = service.create_staging_job(batch,0,"source.txt")
    from pathlib import Path
    source = Path(job.staged_path)
    source.parent.mkdir()
    source.write_bytes(b"reviewed source bytes")
    job = service.complete_staging(job.id,hashlib.sha256(source.read_bytes()).hexdigest(),source.stat().st_size,source)
    return jobs,service,job,source


def test_parser_source_snapshot_ignores_original_leaf_replacement(snapshot_job):
    from row_bot.documents import iter_document_pages
    jobs,service,job,source = snapshot_job
    with service.source_snapshot(job,validate=lambda:None) as (path, extension, check):
        source.rename(source.with_name("retained-original.txt"))
        source.write_bytes(b"foreign replacement must not reach provider")
        check()
        assert "".join(page.page_content for page in iter_document_pages(path,original_extension=extension)) == "reviewed source bytes"
    assert source.read_bytes() == b"foreign replacement must not reach provider"


def test_snapshot_rejects_original_replacement_before_capture(snapshot_job):
    jobs,service,job,source = snapshot_job
    source.write_bytes(b"foreign replacement")
    with pytest.raises(jobs.DocumentJobError,match="source changed"):
        with service.source_snapshot(job,validate=lambda:None):
            pytest.fail("foreign source admitted")


def test_snapshot_candidate_is_pinned_and_handle_released(snapshot_job):
    from pathlib import Path
    jobs,service,job,source = snapshot_job
    candidate = service.work_root / job.id / f"source-{job.content_sha256}{job.extension}"
    with service.source_snapshot(job,validate=lambda:None) as (path, extension, check):
        assert Path(path).read_bytes() == source.read_bytes()
        if jobs.os.name == "nt":
            with pytest.raises(OSError):
                candidate.rename(candidate.with_name("moved.txt"))
            with pytest.raises(OSError):
                candidate.write_bytes(b"foreign")
        else:
            candidate.rename(candidate.with_name("retained.txt"))
            candidate.write_bytes(b"foreign")
            assert Path(path).read_bytes() == source.read_bytes()
            check()
    if jobs.os.name == "nt":
        candidate.rename(candidate.with_name("released.txt"))
    else:
        assert not Path(path).exists()


def test_snapshot_reads_never_materialize_whole_source(snapshot_job, monkeypatch):
    jobs,service,job,source = snapshot_job
    from dataclasses import replace
    with source.open("wb") as output:
        for _ in range(6):
            output.write(b"x" * 1024**2)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    job = replace(job,size_bytes=6 * 1024**2,content_sha256=digest.hexdigest())
    original = jobs.os.read
    sizes = []
    def read(fd,size):
        sizes.append(size)
        return original(fd,size)
    monkeypatch.setattr(jobs.os,"read",read)
    with service.source_snapshot(job,validate=lambda:None):
        pass
    assert len(sizes) >= 14
    assert max(sizes) == jobs.UPLOAD_CHUNK_BYTES == 1024**2


def test_snapshot_rejects_unknown_existing_candidate_without_deleting(snapshot_job):
    jobs,service,job,source = snapshot_job
    candidate = service.work_root / job.id / f"source-{job.content_sha256}{job.extension}"
    candidate.parent.mkdir()
    candidate.write_bytes(b"unknown bytes")
    with pytest.raises(jobs.DocumentJobError,match="snapshot changed"):
        with service.source_snapshot(job,validate=lambda:None):
            pytest.fail("unknown snapshot admitted")
    assert candidate.read_bytes() == b"unknown bytes"


@pytest.fixture
def processing(tmp_path, monkeypatch):
    from row_bot import document_jobs as jobs, embedding_providers as embeddings, embedding_config
    from row_bot.application import document_processing as api, document_upload_commands as uploads
    from row_bot.providers import runtime, auth_store
    from row_bot.runtime import admissions
    from tests.subsystem.knowledge_graph.test_document_upload_commands import Stream
    monkeypatch.setenv("ROW_BOT_DATA_DIR",str(tmp_path / "data"))
    monkeypatch.setattr(jobs,"_wake_supervisor",lambda:None)
    service = jobs.DocumentJobService(tmp_path / "data")
    context = {"model_override":"model:openai:gpt-4o","approval_mode":"approve",
        "reasoning_snapshot":{"model_ref":"model:openai:gpt-4o","selection":{"kind":"provider_default"},"capabilities":None}}
    cfg = {**embedding_config.DEFAULT_CONFIG,"provider":"cloud","cloud_model":"openai:text-embedding-3-small","dimension":1536}
    state = {"revoked":False,"secret":"captured-secret"}
    monkeypatch.setattr(runtime,"get_provider_secret",lambda provider:state["secret"])
    monkeypatch.setattr(auth_store,"get_provider_secret",lambda provider:state["secret"])
    monkeypatch.setattr(embeddings,"get_embedding_config",lambda:dict(cfg))
    monkeypatch.setattr(embedding_config,"get_embedding_config",lambda:dict(cfg))
    def validate():
        if state["revoked"]:
            raise RuntimeError("session revoked")
    policy = api.DocumentProcessingPolicy(read_context=lambda:dict(context),validate=validate,
        validate_action=lambda action:None,owner_id="test-owner",authority_id="test-session",conversation_id="test-conversation")
    admissions.keyed_digest({"initialize":"synthetic"})
    upload = {"command_id":str(uuid4()),"type":"document.upload","payload":{"files":[{"name":"source.txt","size_bytes":21}],"review_id":"review"}}
    saved = asyncio.run(uploads.execute_document_upload(upload,[Stream(b"reviewed source bytes")],service=service,
        key=upload["command_id"],owner_id=policy.owner_id,authority_id=policy.authority_id,
        validate=validate,validate_action=lambda action:None,validate_review=lambda command,review:None,
        disk_free=lambda path:10 * 1024**3))
    assert saved["status"] == "completed"
    service.processing_policy_resolver = lambda proof:policy
    return api,service,policy,saved["batch_id"],context,cfg,state


def admit(processing, **callbacks):
    api,service,policy,batch,*_ = processing
    from row_bot.application.document_job_commands import _snapshot
    revision = api.common._digest(_snapshot([batch]))
    command = {"command_id":str(uuid4()),"type":"document.batch.process",
        "payload":{"batch_id":batch,"revision":revision,"review_id":"review","conversation_id":policy.conversation_id}}
    result = api.execute_document_processing(command,service=service,key=command["command_id"],policy=policy,
        validate_review=callbacks.get("validate_review",lambda command,review:None))
    return command,result


def replacement_processing(processing):
    api, service, policy, batch, *rest = processing
    replacement = api.DocumentProcessingPolicy(read_context=policy.read_context,
        validate=lambda:None,validate_action=lambda action:None,owner_id="new-processing-owner",
        authority_id="new-authenticated-session",conversation_id="reviewed-new-conversation")
    service.processing_policy_resolver = lambda proof:replacement
    return api,service,replacement,batch,*rest


def test_fresh_review_reauthorizes_after_restart_without_changing_upload_ownership(processing):
    _,service,old_policy,batch,*_ = processing
    original,_ = admit(processing)
    with service._connect() as conn:
        before = dict(conn.execute("SELECT * FROM document_batch_admissions WHERE batch_id=?",(batch,)).fetchone())
    service.processing_policy_resolver = None
    assert service.claim_next("restarted-worker") is None
    assert service.get_batch(batch).status == "paused"
    replacement = replacement_processing(processing)
    command,result = admit(replacement)
    assert result["processing"] == "admitted"
    proof = service.processing_admission(batch)
    assert set(proof) == {"batch_id","owner_id","processing_owner_id","conversation_id",
        "processing_command_id","source_digest","policy_digest","authority_digest"}
    assert proof["owner_id"] == old_policy.owner_id
    assert proof["processing_owner_id"] == replacement[2].owner_id
    assert proof["conversation_id"] == replacement[2].conversation_id
    assert proof["processing_command_id"] == command["command_id"] != original["command_id"]
    with service._connect() as conn:
        after = dict(conn.execute("SELECT * FROM document_batch_admissions WHERE batch_id=?",(batch,)).fetchone())
    assert (after["owner_id"],after["command_id"]) == (before["owner_id"],before["command_id"])
    assert service.claim_next("restarted-worker").status == "indexing"


def test_old_receipt_cannot_restore_previous_processing_authority(processing,monkeypatch):
    api,service,old_policy,batch,*_ = processing
    original,first = admit(processing)
    service.pause_batch(batch)
    replacement = replacement_processing(processing)
    admit(replacement)
    proof = service.processing_admission(batch)
    monkeypatch.setattr(old_policy,"capture",lambda:pytest.fail("old receipt recaptured policy"))
    assert api.execute_document_processing(original,service=service,key=original["command_id"],policy=old_policy,
        validate_review=lambda *args:pytest.fail("old receipt reviewed again")) == first
    assert service.processing_admission(batch) == proof


def test_initial_claim_retains_private_proof_before_any_job_admission(processing,monkeypatch):
    api,service,policy,batch,*_ = processing
    from row_bot.runtime import admissions
    original = admissions.claim_command
    claimed = []
    def crash(*args,**kwargs):
        original(*args,**kwargs)
        claimed.append(args[2]["command_id"])
        raise RuntimeError("simulated crash after atomic claim")
    monkeypatch.setattr(admissions,"claim_command",crash)
    with pytest.raises(RuntimeError,match="atomic claim"):
        admit(processing)
    command_id, = claimed
    result = api.read_document_processing_command(command_id=command_id,policy=policy)
    assert result == {"command_id":command_id,"status":"partial","code":"document_processing_uncertain"}
    private = admissions.read_command_receipt(policy.owner_id,command_id)["_document_processing"]
    assert private["processing_owner_id"] == policy.owner_id and private["conversation_id"] == policy.conversation_id
    with service._connect() as conn:
        assert conn.execute("SELECT state FROM document_batch_admissions WHERE batch_id=?",(batch,)).fetchone()[0] == "staged"


def test_conversation_change_invalidates_worker_proof_even_with_same_models(processing):
    _,service,policy,batch,*_ = processing
    admit(processing)
    policy.conversation_id = "another-conversation"
    assert service.claim_next("worker") is None
    assert service.get_batch(batch).status == "paused"


def test_wrong_command_conversation_is_rejected_before_claim_or_review(processing):
    api,service,policy,batch,*_ = processing
    from row_bot.runtime import admissions
    command = {"command_id":str(uuid4()),"type":"document.batch.process",
        "payload":{"batch_id":batch,"revision":"unused","review_id":"unused","conversation_id":"wrong-conversation"}}
    with pytest.raises(Exception,match="invalid_document_processing"):
        api.execute_document_processing(command,service=service,key=command["command_id"],policy=policy,
            validate_review=lambda *args:pytest.fail("Wrong conversation reached review"))
    assert admissions.read_command_metadata(policy.owner_id,command["command_id"]) is None


@pytest.mark.parametrize("phase",["worker","finalizer"])
def test_actual_processing_scope_blocks_authority_replacement_until_exit(processing,monkeypatch,phase):
    api,service,policy,batch,*_ = processing
    from row_bot import document_jobs,embedding_providers,documents
    from row_bot.application.document_job_commands import _snapshot
    admit(processing)
    original = service.processing_admission(batch)
    @contextmanager
    def captured(capture,*,validate):
        yield SimpleNamespace()
    monkeypatch.setattr(embedding_providers,"captured_embedding_provider",captured)
    attempts = []
    def reauthorize(*args,**kwargs):
        with pytest.raises(document_jobs.DocumentJobError,match="processing is active"):
            service.authorize_processing(batch,expected_snapshot=_snapshot([batch]),
                proof={**original,"processing_command_id":str(uuid4())},validate=lambda:None)
        attempts.append(True)
        assert service.processing_admission(batch) == original
        return False
    supervisor = document_jobs.DocumentSupervisor(service)
    if phase == "worker":
        monkeypatch.setattr(documents,"index_document_job",reauthorize)
        monkeypatch.setattr(service,"mark_searchable",lambda *args,**kwargs:None)
        supervisor._process_job(service.claim_next("worker"))
    else:
        with service._connect() as conn:
            conn.execute("UPDATE document_jobs SET status='completed' WHERE batch_id=?",(batch,))
        monkeypatch.setattr(document_jobs,"_finalize_shared_knowledge_indexes",reauthorize)
        supervisor._finalize_ready_batches()
    assert attempts == [True]
    with service._processing_scope_lock(batch):
        pass  # Physical scope exit, not job status/lease expiry, released admission.


def test_processing_scope_native_lock_survives_until_process_exit(processing):
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    _,service,_,batch,*_ = processing
    from row_bot import document_jobs
    path = service.root / f"processing-{batch}.lock"
    script = """import os, pathlib, sys
from row_bot.document_jobs import _SupervisorProcessLock
lock = _SupervisorProcessLock(pathlib.Path(sys.argv[1]))
assert lock.acquire()
print('held', flush=True)
sys.stdin.read(1)
os._exit(0)
"""
    process = subprocess.Popen([sys.executable,"-u","-c",script,str(path)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        assert pool.submit(process.stdout.readline).result(timeout=15).strip() == "held"
        with pytest.raises(document_jobs.DocumentJobError,match="processing is active"):
            with service._processing_scope_lock(batch):
                pytest.fail("Active native scope was replaced")
        process.stdin.write("x")
        process.stdin.flush()
        assert process.wait(timeout=15) == 0
        with service._processing_scope_lock(batch):
            pass  # The OS releases on crash/exit even without explicit unlock.
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)
        pool.shutdown(wait=True)


def test_missing_new_processing_identity_never_adopts_upload_identity(processing):
    _,service,_,batch,*_ = processing
    admit(processing)
    with service._connect() as conn:
        conn.execute("UPDATE document_batch_admissions SET processing_owner_id='',conversation_id='' WHERE batch_id=?",(batch,))
    assert service.claim_next("worker") is None
    assert service.get_batch(batch).status == "paused"
    new = replacement_processing(processing)
    assert admit(new)[1]["processing"] == "admitted"
    assert service.processing_admission(batch)["processing_owner_id"] == new[2].owner_id


def test_review_and_durable_admission_bind_policy_without_constructing_models(processing, monkeypatch):
    api,service,policy,batch,context,cfg,state = processing
    from row_bot import embedding_providers
    monkeypatch.setattr(embedding_providers,"get_embedding_provider",lambda *a:pytest.fail("provider at review"))
    command,result = admit(processing)
    assert result == {"command_id":command["command_id"],"status":"completed","batch_id":batch,"processing":"admitted"}
    proof = service.processing_admission(batch)
    assert proof["processing_command_id"] == command["command_id"]
    assert service.claim_next("worker").status == "indexing"
    assert "secret" not in str(result) and "_document_processing" not in str(result)


@pytest.mark.parametrize("change",["credential","model","approval","missing_proof","missing_resolver"])
def test_worker_refuses_changed_policy_or_missing_proof_without_legacy_fallback(processing, change):
    api,service,policy,batch,context,cfg,state = processing
    command,_ = admit(processing)
    if change == "credential":
        state["secret"] = "changed-secret"
    elif change == "model":
        context["model_override"] = "model:openai:another-model"
    elif change == "approval":
        context["approval_mode"] = "block"
    elif change == "missing_resolver":
        service.processing_policy_resolver = None
    else:
        from row_bot.runtime import admissions
        with admissions.transaction() as conn:
            conn.execute("DELETE FROM client_commands WHERE command_id=?",(command["command_id"],))
    assert service.claim_next("worker") is None
    assert service.get_batch(batch).status == "paused"
    assert service.list_jobs(batch)[0].status == "queued"


def test_original_processing_command_receipt_never_reenables_or_recaptures_policy(processing, monkeypatch):
    api,service,policy,batch,*_ = processing
    command,first = admit(processing)
    service.pause_batch(batch)
    monkeypatch.setattr(policy,"capture",lambda:pytest.fail("receipt policy effect"))
    assert api.execute_document_processing(command,service=service,key=command["command_id"],policy=policy,
        validate_review=lambda *a:pytest.fail("new review on original receipt")) == first
    assert service.get_batch(batch).status == "paused"


def test_strict_indexing_worker_uses_reviewed_snapshot_and_captured_embeddings(processing, monkeypatch, tmp_path):
    api,service,policy,batch,*_ = processing
    from row_bot import document_jobs, documents, embedding_providers
    from langchain_core.embeddings import Embeddings
    from pathlib import Path
    calls = []
    class Fake(Embeddings):
        def embed_documents(self,texts):
            calls.extend(texts)
            return [[1.0]+[0.0]*1535 for text in texts]
        def embed_query(self,text):
            return [1.0]+[0.0]*1535
    @contextmanager
    def captured(capture, *, validate):
        yield embedding_providers._CapturedEmbeddings(Fake(),validate)
    monkeypatch.setattr(embedding_providers,"captured_embedding_provider",captured)
    monkeypatch.setattr(documents,"DOCUMENT_INDEX_DIR",tmp_path / "document-index")
    admit(processing)
    job = service.claim_next("worker")
    original_snapshot = service.source_snapshot
    @contextmanager
    def snapshot(job, *, validate):
        with original_snapshot(job,validate=validate) as result:
            source = Path(job.staged_path)
            source.rename(source.with_name("retained-original.txt"))
            source.write_bytes(b"foreign content must not reach embeddings")
            yield result
    monkeypatch.setattr(service,"source_snapshot",snapshot)
    with pytest.raises(document_jobs.DocumentJobError,match="identity changed|bytes changed"):
        document_jobs.DocumentSupervisor(service)._process_job(job)
    assert calls == ["reviewed source bytes"]
    assert service.get_job(job.id).status == "indexing"
    assert service.list_document_records() == []


def test_strict_worker_completes_canonical_pipeline_without_legacy_provider_selection(processing, monkeypatch, tmp_path):
    import importlib
    from pathlib import Path
    from langchain_core.embeddings import Embeddings
    from row_bot import document_jobs, documents, embedding_providers, knowledge_graph, wiki_vault
    from row_bot.providers import runtime
    api,service,policy,batch,*_ = processing
    kg = importlib.reload(knowledge_graph)
    monkeypatch.setattr(wiki_vault,"is_enabled",lambda:False)
    monkeypatch.setattr(documents,"DOCUMENT_INDEX_DIR",tmp_path / "document-index")
    monkeypatch.setattr(document_jobs,"_notify_batch_complete",lambda *a:None)
    calls = {"embed":[],"chat":[]}
    class Fake(Embeddings):
        def embed_documents(self,texts):
            calls["embed"].append(list(texts))
            return [[1.0]+[0.0]*1535 for text in texts]
        def embed_query(self,text):
            return [1.0]+[0.0]*1535
    @contextmanager
    def embedding(capture, *, validate):
        yield embedding_providers._CapturedEmbeddings(Fake(),validate)
    @contextmanager
    def chat(capture, *, validate):
        def invoke(messages):
            validate()
            calls["chat"].append(messages[0].content)
            text = "[]" if len(calls["chat"]) == 3 else "A detailed synthetic article describing the reviewed source and its retained information."
            return SimpleNamespace(content=text)
        yield SimpleNamespace(invoke=invoke)
    monkeypatch.setattr(embedding_providers,"captured_embedding_provider",embedding)
    monkeypatch.setattr(runtime,"captured_chat_model",chat)
    monkeypatch.setattr(runtime,"create_chat_model",lambda *a,**k:pytest.fail("legacy provider factory"))
    admit(processing)
    supervisor = document_jobs.DocumentSupervisor(service)
    indexing = service.claim_next("worker")
    supervisor._process_job(indexing)
    assert service.get_job(indexing.id).status == "searchable"
    extracting = service.claim_next("worker")
    assert extracting.status == "extracting"
    supervisor._process_job(extracting)
    assert service.get_job(indexing.id).status == "completed"
    assert service.get_batch(batch).status != "completed"
    supervisor._finalize_ready_batches()
    assert service.get_batch(batch).status == "completed"
    assert len(calls["chat"]) == 3
    assert calls["embed"][0] == ["reviewed source bytes"]
    assert kg.memory_vector_status()["ready"]
    assert Path(service.get_job(indexing.id).staged_path).read_bytes() == b"reviewed source bytes"


def test_pause_finishes_admitted_stage_without_claiming_next_and_resume_is_explicit(processing, monkeypatch):
    from row_bot import document_jobs, documents, embedding_providers
    api,service,policy,batch,*_ = processing
    @contextmanager
    def embedding(capture, *, validate):
        yield object()
    def index(job, service, *, validate, **kwargs):
        service.pause_batch(batch)
        validate()  # Pause is distinct from cancellation of the admitted stage.
    monkeypatch.setattr(embedding_providers,"captured_embedding_provider",embedding)
    monkeypatch.setattr(documents,"index_document_job",index)
    admit(processing)
    job = service.claim_next("worker")
    document_jobs.DocumentSupervisor(service)._process_job(job)
    assert service.get_job(job.id).status == "searchable"
    assert not service.get_job(job.id).cancel_requested
    assert service.claim_next("worker") is None
    assert service.get_batch(batch).status == "paused"
    service.pause_batch(batch,False)
    assert service.claim_next("worker").status == "extracting"


def test_cancel_during_admitted_stage_stops_searchable_publication(processing, monkeypatch):
    from row_bot import document_jobs, documents, embedding_providers
    api,service,policy,batch,*_ = processing
    @contextmanager
    def embedding(capture, *, validate):
        yield object()
    def index(job, service, *, validate, **kwargs):
        service.cancel_batch(batch)
        validate()
    monkeypatch.setattr(embedding_providers,"captured_embedding_provider",embedding)
    monkeypatch.setattr(documents,"index_document_job",index)
    admit(processing)
    job = service.claim_next("worker")
    with pytest.raises(document_jobs.DocumentCancelled):
        document_jobs.DocumentSupervisor(service)._process_job(job)
    assert service.list_document_records() == []
    assert service.get_batch(batch).status == "cancelled"


def test_source_row_revocation_after_sqlite_admission_rolls_back(processing, monkeypatch):
    import importlib
    from row_bot import knowledge_graph
    api,service,policy,batch,context,cfg,state = processing
    kg = importlib.reload(knowledge_graph)
    original = kg._get_conn
    class Connection:
        def __init__(self):
            self.connection = original()
        def execute(self,sql,*args):
            result = self.connection.execute(sql,*args)
            if sql == "BEGIN IMMEDIATE":
                state["revoked"] = True
            return result
        def __getattr__(self,name):
            return getattr(self.connection,name)
    monkeypatch.setattr(kg,"_get_conn",Connection)
    with pytest.raises(RuntimeError,match="session revoked"):
        kg.save_entity("fact","Synthetic","Synthetic body retained only if authorized.",validate=policy.validate)
    with original() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0


def test_wiki_projection_revocation_after_writer_admission_creates_no_files(processing, monkeypatch, tmp_path):
    import importlib
    from row_bot import knowledge_graph, wiki_vault
    api,service,policy,batch,context,cfg,state = processing
    kg = importlib.reload(knowledge_graph)
    monkeypatch.setattr(kg,"_skip_reindex",True)
    monkeypatch.setattr(wiki_vault,"is_enabled",lambda:False)
    entity = kg.save_entity("fact","Synthetic","Synthetic article long enough for the managed wiki projection to publish.")
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(wiki_vault,"is_enabled",lambda:True)
    monkeypatch.setattr(wiki_vault,"get_vault_path",lambda:vault)
    original = kg._get_conn
    class Connection:
        def __init__(self):
            self.connection = original()
        def execute(self,sql,*args):
            result = self.connection.execute(sql,*args)
            if sql == "BEGIN IMMEDIATE":
                state["revoked"] = True
            return result
        def __getattr__(self,name):
            return getattr(self.connection,name)
    monkeypatch.setattr(kg,"_get_conn",Connection)
    with pytest.raises(RuntimeError,match="session revoked"):
        wiki_vault.export_entities_projection([entity],validate=policy.validate)
    assert list(vault.iterdir()) == []


@pytest.mark.parametrize("provider",["codex","claude_subscription","xai_oauth","ollama_cloud"])
@pytest.mark.parametrize("status",[200,401])
def test_actual_retained_transport_captured_request_no_refresh_or_retry(runtime, monkeypatch, provider, status):
    import json
    from row_bot.providers import auth_store, codex, claude_subscription, xai_oauth
    from row_bot.providers.transports import cancellable_http
    snapshot = subscription_snapshot(provider)
    monkeypatch.setattr(auth_store,"read_provider_oauth_bundle_snapshot",lambda provider:snapshot)
    captured = runtime.capture_chat_runtime("model:" + provider + ":synthetic")
    monkeypatch.setattr(auth_store,"read_provider_oauth_bundle_snapshot",lambda provider:pytest.fail("account reread"))
    for owner,name in [(codex,"refresh_codex_token"),(claude_subscription,"refresh_claude_subscription_token"),(xai_oauth,"refresh_xai_oauth_token")]:
        monkeypatch.setattr(owner,name,lambda *a,**kw:pytest.fail("implicit OAuth refresh"))
    monkeypatch.setenv("ANTHROPIC_API_KEY","foreign-api-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN","foreign-oauth-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL","https://foreign.invalid")
    calls = []
    def response(request):
        calls.append(request)
        if status == 401:
            return httpx.Response(401,json={"error":{"type":"authentication_error","message":"synthetic failure"}})
        if provider == "claude_subscription":
            return httpx.Response(200,json={"id":"synthetic","type":"message","role":"assistant","model":"synthetic",
                "content":[{"type":"text","text":"accepted"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}})
        if provider == "ollama_cloud":
            return httpx.Response(200,json={"message":{"role":"assistant","content":"accepted"},"done":True})
        payload = {"type":"response.completed","response":{"id":"synthetic","status":"completed",
            "output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"accepted"}]}]}}
        stream = 'event: response.output_text.delta\ndata: {"delta":"accepted"}\n\n'
        stream += "event: response.completed\ndata: " + json.dumps(payload) + "\n\n"
        return httpx.Response(200,text=stream,headers={"content-type":"text/event-stream"})
    monkeypatch.setattr(cancellable_http,"cancellable_http_client",lambda **kwargs:httpx.Client(transport=httpx.MockTransport(response),**kwargs))
    with runtime.captured_chat_model(captured,validate=lambda:None) as model:
        if status == 401:
            with pytest.raises(Exception):
                model.invoke(["synthetic"])
        else:
            assert model.invoke(["synthetic"]).content == "accepted"
        if provider in {"codex","xai_oauth","claude_subscription"}:
            assert "captured-token" not in str(model._model.model_dump())
    assert len(calls) == 1
    assert calls[0].url.host != "foreign.invalid"
    expected = "captured-secret" if provider == "ollama_cloud" else "captured-token"
    assert calls[0].headers["authorization"] == "Bearer " + expected
    if provider == "claude_subscription":
        assert not calls[0].headers.get("x-api-key")
    if provider == "codex":
        assert calls[0].headers["chatgpt-account-id"] == "captured-account"
    if provider == "xai_oauth":
        assert "max_output_tokens" not in json.loads(calls[0].content)


def test_custom_compatible_retains_headers_normalization_and_captured_context(runtime, monkeypatch):
    import json
    from row_bot.providers import custom
    from row_bot.providers.transports import cancellable_http
    from row_bot import models
    endpoint = {"provider_id":"custom_openai_test","enabled":True,"base_url":"https://provider.invalid/v1",
        "auth_required":True,"transport":"openai_chat","api_key_header":"X-Captured-Key","headers":{"X-Synthetic":"captured"},
        "supports_runtime_context_override":True,"context_param_name":"context_size"}
    monkeypatch.setattr(custom,"get_custom_endpoint",lambda provider:endpoint)
    monkeypatch.setattr(runtime,"custom_endpoint_secret",lambda provider:"captured-secret")
    monkeypatch.setattr(models,"get_context_policy",lambda *a,**kw:SimpleNamespace(effective_limit_tokens=8192))
    captured = runtime.capture_chat_runtime("model:custom_openai_test:synthetic")
    monkeypatch.setattr(models,"get_context_policy",lambda *a:pytest.fail("context reread"))
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200,json={"choices":[{"message":{"content":"accepted"}}]})
    monkeypatch.setattr(cancellable_http,"cancellable_http_client",lambda **kwargs:httpx.Client(transport=httpx.MockTransport(response),**kwargs))
    with runtime.captured_chat_model(captured,validate=lambda:None) as model:
        assert model.invoke(["synthetic"]).content == "accepted"
    assert calls[0].headers["x-captured-key"] == "captured-secret"
    assert calls[0].headers["x-synthetic"] == "captured"
    assert json.loads(calls[0].content)["context_size"] == 8192


@pytest.mark.parametrize("kind",["chat","embedding"])
def test_real_sdk_hook_authority_error_survives_sdk_wrapping(runtime, monkeypatch,kind):
    from row_bot import embedding_providers as embeddings
    from row_bot.providers import auth_store
    from row_bot.providers.transports import cancellable_http
    import tiktoken
    monkeypatch.setattr(tiktoken,"encoding_for_model",lambda *a:pytest.fail("implicit tokenizer download"))
    monkeypatch.setattr(auth_store,"get_provider_secret",lambda provider:"captured-secret")
    sentinel = RuntimeError("authority rejected at request admission")
    arrived = False
    calls = []
    def validate():
        if arrived:
            raise sentinel
    def client(**kwargs):
        hook = kwargs["event_hooks"]["request"][0]
        def gated(request):
            nonlocal arrived
            arrived = True
            hook(request)
        kwargs["event_hooks"]["request"] = [gated]
        return httpx.Client(transport=httpx.MockTransport(lambda request:calls.append(request)),**kwargs)
    monkeypatch.setattr(cancellable_http,"cancellable_http_client",client)
    capture = (runtime.capture_chat_runtime("model:openai:gpt-4o") if kind == "chat" else
        embeddings.capture_embedding_runtime({"provider":"cloud","cloud_model":"openai:text-embedding-3-small"}))
    factory = runtime.captured_chat_model if kind == "chat" else embeddings.captured_embedding_provider
    with factory(capture,validate=validate) as model:
        with pytest.raises(RuntimeError) as caught:
            if kind == "chat":
                model.invoke(["synthetic"])
            else:
                model.embed_documents(["synthetic"])
        assert caught.value is sentinel
    assert calls == []


@pytest.mark.parametrize("transport",["openai_chat","openai_responses","anthropic_messages","google_genai"])
def test_opencode_captured_factory_retains_each_canonical_transport(runtime, monkeypatch, transport):
    import anthropic
    from google import genai
    from row_bot.providers import opencode
    from row_bot.providers.models import TransportMode
    route = opencode.OpenCodeModelRoute("opencode_zen","synthetic","Synthetic",TransportMode(transport),8192)
    monkeypatch.setattr(opencode,"opencode_model_route",lambda *a:route)
    monkeypatch.setattr(opencode,"opencode_known_route",lambda *a:route)
    captured = runtime.capture_chat_runtime("model:opencode_zen:synthetic")
    monkeypatch.setattr(opencode,"opencode_model_route",lambda *a:pytest.fail("route reread"))
    calls = fake_openai(monkeypatch)
    def sdk(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(models=object())
    monkeypatch.setattr(anthropic,"Client",sdk)
    monkeypatch.setattr(anthropic,"AsyncClient",sdk)
    monkeypatch.setattr(genai,"Client",sdk)
    with runtime.captured_chat_model(captured,validate=lambda:None) as model:
        inner = model._model
        if transport == "openai_chat":
            assert inner._llm_type == "openai_compatible_chat"
            assert inner.endpoint["profile"] == "opencode"
        elif transport == "openai_responses":
            assert inner.kwargs["use_responses_api"] is True
        elif transport == "anthropic_messages":
            assert inner._client is not None
        else:
            assert inner.client is not None
            assert calls[0]["http_options"]["api_version"] == "v1"
    assert captured.resolved.transport.value == transport


def test_openrouter_captured_sdk_has_no_automatic_request_retry(runtime,monkeypatch):
    import openrouter
    calls = []
    original = openrouter.OpenRouter
    def construct(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(openrouter,"OpenRouter",construct)
    captured = runtime.capture_chat_runtime("model:openrouter:synthetic")
    with runtime.captured_chat_model(captured,validate=lambda:None):
        pass
    assert len(calls) == 1
    assert calls[0]["retry_config"] is None
    assert calls[0]["api_key"] == "captured-secret"


def test_index_publication_revocation_after_manifest_lock_keeps_staged_bytes(tmp_path,monkeypatch):
    from row_bot import document_index
    work,root = tmp_path / "work",tmp_path / "index"
    work.mkdir()
    (work / "synthetic.txt").write_bytes(b"retained prepared generation")
    revoked = False
    class Lock:
        def __enter__(self):
            nonlocal revoked
            revoked = True
        def __exit__(self,*args):
            pass
    def validate():
        if revoked:
            raise RuntimeError("revoked under manifest lock")
    monkeypatch.setattr(document_index,"_manifest_lock",Lock())
    with pytest.raises(RuntimeError,match="manifest lock"):
        document_index.publish_document(work,{},index_root=root,validate=validate)
    assert not root.exists()
    assert (work / "synthetic.txt").read_bytes() == b"retained prepared generation"


def test_cold_local_capture_has_no_inventory_show_or_network_probe(runtime,monkeypatch):
    from row_bot import models
    monkeypatch.setattr(models,"_model_max_ctx_cache",{})
    monkeypatch.setattr(models,"_observed_local_context",{})
    monkeypatch.setattr(models,"list_local_models",lambda:pytest.fail("daemon list during review"))
    monkeypatch.setattr(models,"_ollama_client",lambda:pytest.fail("daemon client during review"))
    monkeypatch.setattr(models,"_ollama_http_json",lambda *a,**kw:pytest.fail("daemon HTTP during review"))
    captured = runtime.capture_chat_runtime("model:ollama:synthetic-family")
    assert captured.resolved.runtime_model == "synthetic-family"
    assert captured.context_size > 0
    assert models.get_model_max_context("model:ollama:synthetic-family",allow_probe=False) is None
    assert models._model_max_ctx_cache == {}


def test_passive_local_context_retains_cached_limit_and_explicit_selection(runtime,monkeypatch):
    from row_bot import models
    monkeypatch.setattr(models,"_model_max_ctx_cache",{"synthetic:tag":4096})
    monkeypatch.setattr(models,"_observed_local_context",{})
    monkeypatch.setattr(models,"list_local_models",lambda:pytest.fail("alias probe"))
    monkeypatch.setattr(models,"_ollama_client",lambda:pytest.fail("metadata probe"))
    captured = runtime.capture_chat_runtime("model:ollama:synthetic:tag")
    assert captured.resolved.runtime_model == "synthetic:tag"
    assert 0 < captured.context_size <= 4096


def test_socket_tripwire_allows_only_internal_socketpair():
    import socket
    one,two = socket.socketpair()
    one.close()
    two.close()
    for target in [("127.0.0.1",11434),("203.0.113.1",443)]:
        with socket.socket() as connection:
            with pytest.raises(pytest.fail.Exception,match="unexpected socket"):
                connection.connect(target)


def test_captured_capability_gate_rejects_incompatible_selection_before_factory(runtime,monkeypatch):
    monkeypatch.setattr(runtime,"_capability_snapshot_for_selection",lambda *a:{"tasks":["embedding"]})
    with pytest.raises(ValueError,match="not compatible with chat"):
        runtime.capture_chat_runtime("model:openai:synthetic-embedding")


def test_worker_policy_digest_captures_capability_revision(processing,monkeypatch):
    from row_bot.providers import runtime
    api,service,policy,batch,*_ = processing
    capability = {"tasks":["chat"],"input_modalities":["text"],"output_modalities":["text"],"context_window":8192}
    monkeypatch.setattr(runtime,"_capability_snapshot_for_selection",lambda *a:capability)
    captured = policy.capture()
    capability["context_window"] = 16384
    assert captured.chat.capabilities["context_window"] == 8192
    assert policy.capture().digest != captured.digest
