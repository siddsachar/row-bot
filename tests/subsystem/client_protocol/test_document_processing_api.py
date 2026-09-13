"""Actual conversation-scoped processing admission and fake canonical workers."""
# ruff: noqa: F811 -- shared isolated application/job fixtures.
from __future__ import annotations

from contextlib import contextmanager
import importlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from row_bot.runtime import admissions
from tests.subsystem.client_protocol.test_document_queue_api import isolated_queue_environment, queue  # noqa: F401
from tests.subsystem.client_protocol.test_document_upload_api import fake_disk_capacity, reviewed as upload_review, send as upload_send  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


@pytest.fixture
def providers(monkeypatch, tmp_path, queue):
    from langchain_core.embeddings import Embeddings
    from row_bot import embedding_config, embedding_providers, documents, document_jobs, wiki_vault
    from row_bot.providers import runtime, auth_store, saved_model_settings
    state = {"secret": "synthetic-processing-credential", "embed": [], "chat": [], "factory": [], "start": []}
    config = {**embedding_config.DEFAULT_CONFIG, "provider": "cloud",
        "cloud_model": "openai:text-embedding-3-small", "dimension": 1536}
    monkeypatch.setattr(embedding_config, "get_embedding_config", lambda: dict(config))
    monkeypatch.setattr(embedding_providers, "get_embedding_config", lambda: dict(config))
    monkeypatch.setattr(runtime, "get_provider_secret", lambda _provider: state["secret"])
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda _provider: state["secret"])
    monkeypatch.setattr(saved_model_settings, "SETTINGS_PATH", tmp_path / "data" / "model_settings.json")
    monkeypatch.setattr(documents, "DOCUMENT_INDEX_DIR", tmp_path / "document-index")
    monkeypatch.setattr(wiki_vault, "is_enabled", lambda: False)
    monkeypatch.setattr(document_jobs, "_notify_batch_complete", lambda *_args: None)
    monkeypatch.setattr(document_jobs, "ensure_document_supervisor", lambda owner: state["start"].append(owner))
    class Fake(Embeddings):
        def embed_documents(self, texts):
            state["embed"].append(list(texts))
            return [[1.0] + [0.0] * 1535 for _ in texts]
        def embed_query(self, text):
            return [1.0] + [0.0] * 1535
    @contextmanager
    def embedding(capture, *, validate):
        state["factory"].append(("embedding", capture.provider))
        validate()
        yield embedding_providers._CapturedEmbeddings(Fake(), validate)
    @contextmanager
    def chat(capture, *, validate):
        state["factory"].append(("chat", capture.resolved.selection_ref))
        validate()
        def invoke(messages):
            validate()
            state["chat"].append(messages[0].content)
            text = "[]" if len(state["chat"]) % 3 == 0 else "A detailed synthetic article about the retained reviewed source information."
            return SimpleNamespace(content=text)
        yield SimpleNamespace(invoke=invoke)
    monkeypatch.setattr(embedding_providers, "captured_embedding_provider", embedding)
    monkeypatch.setattr(runtime, "captured_chat_model", chat)
    monkeypatch.setattr(runtime, "create_chat_model", lambda *a, **kw: pytest.fail("Legacy provider construction"))
    return state


def conversation(*, model="model:openai:gpt-4o", approval="approve", profile=""):
    from row_bot import threads
    return threads.create_thread("Synthetic document processing", model_override=model,
        approval_mode=approval, agent_profile_id=profile, seed_default_skills=False)


def uploaded(client, headers):
    _, command = upload_review(client, headers, [{"name": "source.txt", "size_bytes": 21}])
    result = upload_send(client, headers, command, (b"reviewed source bytes",))
    assert result.status_code == 200 and result.json()["status"] == "completed", result.text
    return result.json()["batch_id"], command


def base(identifier):
    return f"/api/v1/conversations/{identifier}/documents/processing"


def review_response(client, headers, identifier, batch):
    rows = client.get("/api/v1/documents/queue", headers=headers).json()["items"]
    row = next(row for row in rows if row["id"] == batch)
    return client.post(base(identifier) + "/review", headers=headers, json={"batch_id": batch, "revision": row["revision"]})


def reviewed(client, headers, identifier, batch):
    response = review_response(client, headers, identifier, batch)
    assert response.status_code == 200, response.text
    review = response.json()
    return review, {"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
        "type": "document.batch.process", "expected_revision": "0", "payload": {"conversation_id": identifier,
            "batch_id": batch, "revision": review["revision"], "review_id": review["review_id"]}}


def send(client, headers, identifier, command):
    return client.post(base(identifier) + "/commands", headers={**headers, "Idempotency-Key": command["command_id"]}, json=command)


def test_review_reads_canonical_conversation_selection_and_constructs_no_provider(service, queue, providers):
    identifier = conversation()
    with _client(service) as client:
        _, headers = bootstrap(client)
        batch, _ = uploaded(client, headers)
        review, _ = reviewed(client, headers, identifier, batch)
        assert review["conversation_id"] == identifier
        assert review["chat"]["model_ref"] == "model:openai:gpt-4o"
        assert review["knowledge_projection_scope"] == "saved_knowledge"
        assert review["provider_work"] and review["embedding"]["execution_location"] == "remote"
        assert providers["factory"] == providers["start"] == []
        assert queue.service.get_batch(batch).status == "paused"
        assert "synthetic-processing-credential" not in str(review)


def test_saved_default_is_used_only_when_conversation_has_no_override(service, queue, providers):
    from row_bot.providers.saved_model_settings import update_saved_model_settings
    update_saved_model_settings(lambda current: {**current, "model": "model:openai:gpt-4o-mini"})
    identifier = conversation(model="")
    with _client(service) as client:
        _, headers = bootstrap(client)
        batch, _ = uploaded(client, headers)
        review, _ = reviewed(client, headers, identifier, batch)
        assert review["chat"]["model_ref"] == "model:openai:gpt-4o-mini"
        assert providers["factory"] == []


@pytest.mark.parametrize("denial", ["approval", "profile", "missing_conversation"])
def test_current_conversation_policy_refuses_processing_review(service, queue, providers, denial):
    identifier = ("missing-conversation" if denial == "missing_conversation" else
        conversation(approval="block" if denial == "approval" else "approve", profile="research" if denial == "profile" else ""))
    with _client(service) as client:
        _, headers = bootstrap(client)
        batch, _ = uploaded(client, headers)
        result = review_response(client, headers, identifier, batch)
        assert result.status_code == (404 if denial == "missing_conversation" else 403), result.text
        assert providers["factory"] == providers["start"] == []
        assert queue.service.get_batch(batch).status == "paused"


@pytest.mark.parametrize("change", ["nonce", "credential", "model", "conversation"])
def test_review_identity_and_current_policy_checked_before_service_admission(service, queue, providers, monkeypatch, change):
    from row_bot import threads, document_jobs
    identifier = conversation()
    with _client(service) as client:
        _, headers = bootstrap(client)
        batch, _ = uploaded(client, headers)
        _, command = reviewed(client, headers, identifier, batch)
        if change == "nonce":
            command["payload"]["review_id"] = "forged"
        elif change == "credential":
            providers["secret"] = "replacement-synthetic-credential"
        elif change == "model":
            threads._set_thread_model_override(identifier, "model:openai:gpt-4o-mini")
        else:
            command["payload"]["conversation_id"] = conversation()
        monkeypatch.setattr(document_jobs, "_supervisor", None)
        monkeypatch.setattr(document_jobs, "get_document_job_service", lambda: pytest.fail("Invalid review initialized service"))
        result = send(client, headers, identifier, command)
        assert result.status_code in {403, 409, 422}, result.text
        assert admissions.read_command_metadata(headers["X-Client-Session"], command["command_id"]) is None
        assert queue.service.get_batch(batch).status == "paused" and providers["factory"] == []


def test_actual_admission_runs_canonical_index_extract_finalize_with_captured_fakes(service, queue, providers, monkeypatch):
    from row_bot import document_jobs, knowledge_graph
    kg = importlib.reload(knowledge_graph)
    identifier = conversation()
    with _client(service) as client:
        _, headers = bootstrap(client)
        batch, upload = uploaded(client, headers)
        _, command = reviewed(client, headers, identifier, batch)
        result = send(client, headers, identifier, command)
        assert result.status_code == 200 and result.json()["processing"] == "admitted", result.text
        assert providers["factory"] == [] and providers["start"] == [queue.service]
        proof = queue.service.processing_admission(batch)
        assert proof["conversation_id"] == identifier and proof["processing_owner_id"] == headers["X-Client-Session"]
        assert proof["owner_id"] == upload["client_session_id"]
        supervisor = document_jobs.DocumentSupervisor(queue.service)
        indexing = queue.service.claim_next("synthetic-worker")
        assert indexing.status == "indexing"
        supervisor._process_job(indexing)
        assert queue.service.get_job(indexing.id).status == "searchable"
        extracting = queue.service.claim_next("synthetic-worker")
        assert extracting.status == "extracting"
        supervisor._process_job(extracting)
        assert queue.service.get_job(indexing.id).status == "completed"
        supervisor._finalize_ready_batches()
        assert queue.service.get_batch(batch).status == "completed"
        assert providers["embed"][0] == ["reviewed source bytes"] and len(providers["chat"]) == 3
        assert ("chat", "model:openai:gpt-4o") in providers["factory"]
        assert kg.memory_vector_status()["ready"]
        assert Path(queue.service.get_job(indexing.id).staged_path).read_bytes() == b"reviewed source bytes"
        assert "_document_processing" not in result.text


def test_original_receipt_and_replay_are_passive_after_pause_and_model_change(service, queue, providers, monkeypatch):
    from row_bot import threads
    from row_bot.application.document_processing import DocumentProcessingPolicy
    identifier = conversation()
    with _client(service) as client:
        _, headers = bootstrap(client)
        batch, _ = uploaded(client, headers)
        _, command = reviewed(client, headers, identifier, batch)
        first = send(client, headers, identifier, command)
        assert first.status_code == 200 and first.json()["processing"] == "admitted", first.text
        queue.service.pause_batch(batch)
        threads._set_thread_model_override(identifier, "model:openai:gpt-4o-mini")
        monkeypatch.setattr(DocumentProcessingPolicy, "capture", lambda _self: pytest.fail("Receipt recaptured provider policy"))
        receipt = client.get(base(identifier) + "/commands/" + command["command_id"], headers=headers)
        assert receipt.status_code == 200 and receipt.json() == first.json()
        assert send(client, headers, identifier, command).json() == first.json()
        assert queue.service.get_batch(batch).status == "paused" and len(providers["start"]) == 1


def test_restart_requires_new_authenticated_review_and_preserves_upload_owner(service, queue, providers):
    identifier = conversation()
    with _client(service) as client:
        _, first = bootstrap(client)
        batch, upload = uploaded(client, first)
        _, original = reviewed(client, first, identifier, batch)
        assert send(client, first, identifier, original).json()["processing"] == "admitted"
        queue.service.processing_policy_resolver = None
        assert queue.service.claim_next("restarted-worker") is None
        assert queue.service.get_batch(batch).status == "paused"
        _, new = bootstrap(client)
        _, replacement = reviewed(client, new, identifier, batch)
        result = send(client, new, identifier, replacement)
        assert result.status_code == 200 and result.json()["processing"] == "admitted", result.text
        proof = queue.service.processing_admission(batch)
        assert proof["owner_id"] == upload["client_session_id"]
        assert proof["processing_owner_id"] == new["X-Client-Session"]
        assert proof["processing_command_id"] == replacement["command_id"]
        assert queue.service.claim_next("restarted-worker").status == "indexing"
        denied = client.get(base(identifier) + "/commands/" + original["command_id"], headers=new)
        assert denied.status_code == 409, denied.text


def test_auth_revocation_after_http_admission_prevents_worker_factory(service, queue, providers):
    from fastapi.testclient import TestClient
    from row_bot.access.config import AccessConfig, DeploymentMode
    from row_bot.access.request_context import SessionIdentity
    from row_bot.api.v1.routes import create_client_platform_app
    active = {"value": True}
    identifier = conversation()
    app = create_client_platform_app(service, access_config=AccessConfig(deployment_mode=DeploymentMode.SERVER),
        session_authenticator=lambda _scope, _provenance: SessionIdentity("synthetic-device", "synthetic-auth") if active["value"] else None,
        choices=lambda: {"models": [], "capabilities": []})
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 12345)) as client:
        _, headers = bootstrap(client)
        batch, _ = uploaded(client, headers)
        _, command = reviewed(client, headers, identifier, batch)
        result = send(client, headers, identifier, command)
        assert result.status_code == 200 and result.json()["processing"] == "admitted", result.text
        active["value"] = False
        assert queue.service.claim_next("late-worker") is None
        assert queue.service.get_batch(batch).status == "paused" and providers["factory"] == []
        assert client.get(base(identifier) + "/commands/" + command["command_id"], headers=headers).status_code == 401


def test_new_authenticated_admission_waits_for_actual_old_worker_scope_exit(service, queue, providers):
    identifier = conversation()
    with _client(service) as client:
        _, first = bootstrap(client)
        batch, upload = uploaded(client, first)
        _, original = reviewed(client, first, identifier, batch)
        assert send(client, first, identifier, original).json()["processing"] == "admitted"
        proof = queue.service.processing_admission(batch)
        with queue.service.processing_scope(batch):
            queue.service.pause_batch(batch)
            _, new = bootstrap(client)
            _, premature = reviewed(client, new, identifier, batch)
            blocked = send(client, new, identifier, premature)
            assert blocked.status_code == 200 and blocked.json()["status"] == "partial", blocked.text
            assert queue.service.processing_admission(batch) == proof
        # A fresh explicit review is needed after actual old-stage cleanup;
        # the uncertain original command is never replayed as a new effect.
        _, replacement = reviewed(client, new, identifier, batch)
        admitted = send(client, new, identifier, replacement)
        assert admitted.status_code == 200 and admitted.json()["processing"] == "admitted", admitted.text
        current = queue.service.processing_admission(batch)
        assert current["owner_id"] == upload["client_session_id"]
        assert current["processing_owner_id"] == new["X-Client-Session"]
        assert current["processing_command_id"] == replacement["command_id"]
        assert send(client, new, identifier, premature).json() == blocked.json()
        assert queue.service.processing_admission(batch) == current


@pytest.mark.parametrize("change", ["model", "credential", "profile"])
def test_in_scope_current_policy_change_denies_embedding_without_legacy_fallback(service, queue, providers, change):
    from row_bot import threads
    identifier = conversation()
    with _client(service) as client:
        _, headers = bootstrap(client)
        batch, _ = uploaded(client, headers)
        _, command = reviewed(client, headers, identifier, batch)
        result = send(client, headers, identifier, command)
        assert result.status_code == 200 and result.json()["processing"] == "admitted", result.text
        with queue.service.processing_scope(batch) as worker:
            if change == "model":
                threads._set_thread_model_override(identifier, "model:openai:gpt-4o-mini")
            elif change == "credential":
                providers["secret"] = "changed-synthetic-credential"
            else:
                # Canonical metadata setter preserves the same conversation ID.
                threads.create_thread("Synthetic changed policy", thread_id=identifier,
                    model_override="model:openai:gpt-4o", approval_mode="approve", agent_profile_id="research",
                    seed_default_skills=False)
            with pytest.raises(Exception, match="document_processing_policy_changed|document_processing_denied"):
                worker.embedding.embed_documents(["must not reach provider"])
        assert providers["embed"] == providers["chat"] == []
