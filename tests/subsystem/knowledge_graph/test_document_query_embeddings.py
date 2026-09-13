"""Request-local query reuse through real safe FAISS stores and counting fakes."""
from __future__ import annotations

import importlib
import json

import pytest
from langchain_core.documents import Document

from tests.subsystem.knowledge_graph.test_document_index_shards import FakeEmbeddings

pytestmark = pytest.mark.subsystem


class CountingEmbeddings(FakeEmbeddings):
    def __init__(self):
        super().__init__()
        self.queries = []
        self.error = None
        self.vector = None

    def embed_query(self, text):
        self.queries.append(text)
        if self.error is not None:
            raise self.error
        return self.vector if self.vector is not None else super().embed_query(text)


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    from row_bot import document_index
    from row_bot.embedding_config import write_index_metadata
    from langchain_community.vectorstores import FAISS

    module = importlib.reload(document_index)
    active = {"provider": "fake", "model": "v1", "dimension": 3}
    monkeypatch.setattr(module, "active_embedding_metadata", lambda: active)
    embedding = CountingEmbeddings()
    root, legacy = tmp_path / "index", tmp_path / "legacy"
    paths = []

    def build(*, segments=3, include_legacy=True, stale=False):
        if segments:
            work = tmp_path / "work"
            manifest = module.build_unpublished_document(
                document_id="managed", original_name="managed.txt", stored_name="managed.txt",
                content_sha256="a" * 64,
                chunks=[Document(page_content=f"managed-{i}", metadata={"chunk_index": i}) for i in range(segments)],
                work_document_dir=work, embedding=embedding,
                embedding_metadata=dict(active, model="old") if stale else active, segment_chunks=1,
            )
            module.publish_document(work, manifest, index_root=root)
            entry = module.read_corpus_manifest(root)["documents"][0]
            directory = module._document_directory(root, entry)
            paths.extend(directory / segment["name"] for segment in manifest["segments"])
        if include_legacy:
            module._save_safe_segment(FAISS.from_documents(
                [Document(page_content="legacy", metadata={"source": "legacy.txt"})], embedding), legacy)
            write_index_metadata(legacy, dict(active, model="old") if stale else active)
            paths.append(legacy)
        facade = module.DocumentVectorStoreFacade(index_root=root, legacy_root=legacy, embedding_factory=lambda: embedding)
        return facade

    return module, embedding, root, legacy, paths, active, build


@pytest.mark.parametrize("segments,legacy", [(1, False), (3, False), (0, True), (3, True)])
def test_one_query_embedding_per_request_across_compatible_stores(stores, segments, legacy):
    _module, embedding, _root, _legacy, _paths, _active, build = stores
    facade = build(segments=segments, include_legacy=legacy)
    first = facade.similarity_search_with_score("query", k=10)
    assert len(first) == segments + int(legacy)
    assert embedding.queries == ["query"]
    assert facade.similarity_search_with_score("query", k=10) == first
    assert embedding.queries == ["query", "query"]
    facade.as_retriever(search_kwargs={"k": 1}).invoke("different")
    assert embedding.queries == ["query", "query", "different"]


@pytest.mark.parametrize("k", [1, 2, 10])
def test_by_vector_scores_and_order_match_original_faiss_query_path(stores, k):
    module, embedding, _root, _legacy, paths, _active, build = stores
    facade = build()
    expected = []
    for path in paths:
        store = module._load_safe_segment(path, FakeEmbeddings(), expected_dimension=3)
        expected.extend((document.page_content, float(score))
                        for document, score in store.similarity_search_with_score("query", k=k))
    expected.sort(key=lambda item: (item[1], item[0]))
    actual = [(document.page_content, float(score))
              for document, score in facade.similarity_search_with_score("query", k=k)]
    assert actual == expected[:k]
    assert embedding.queries == ["query"]


@pytest.mark.parametrize("condition", ["missing", "stale", "corrupt", "empty", "incomplete"])
def test_no_query_embedding_without_usable_stores(stores, condition):
    module, embedding, root, legacy, paths, active, build = stores
    if condition == "missing":
        facade = build(segments=0, include_legacy=False)
    elif condition == "empty":
        import faiss
        from langchain_community.docstore.in_memory import InMemoryDocstore
        from langchain_community.vectorstores import FAISS
        from row_bot.embedding_config import write_index_metadata

        facade = build(segments=0, include_legacy=False)
        module._save_safe_segment(FAISS(embedding, faiss.IndexFlatL2(3), InMemoryDocstore({}), {}), legacy)
        write_index_metadata(legacy, active)
    else:
        facade = build(stale=condition == "stale", include_legacy=condition != "incomplete")
        if condition == "corrupt":
            for path in paths:
                (path / "index.json").write_text("corrupt fixture", encoding="utf-8")
        elif condition == "incomplete":
            entry = module.read_corpus_manifest(root)["documents"][0]
            path = module._document_directory(root, entry) / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["complete"] = False
            path.write_text(json.dumps(manifest), encoding="utf-8")
    assert facade.similarity_search("query") == []
    assert embedding.queries == []


def test_corrupt_segment_and_tombstoned_legacy_preserve_healthy_results(stores):
    _module, embedding, root, _legacy, paths, _active, build = stores
    facade = build()
    (paths[0] / "index.json").write_text("corrupt fixture", encoding="utf-8")
    (root / "legacy_tombstones.json").write_text('["legacy.txt"]', encoding="utf-8")
    results = facade.similarity_search("query", k=10)
    assert [item.page_content for item in results] == ["managed-1", "managed-2"]
    assert [item.metadata["chunk_index"] for item in results] == [1, 2]
    assert embedding.queries == ["query"]


def test_embedding_failure_is_not_retried_per_segment_or_cached_next_request(stores):
    _module, embedding, _root, _legacy, _paths, _active, build = stores
    facade = build()
    embedding.error = RuntimeError("Fake provider unavailable")
    assert facade.similarity_search("query", k=10) == []
    assert embedding.queries == ["query"]
    embedding.error = None
    assert len(facade.similarity_search("query", k=10)) == 4
    assert embedding.queries == ["query", "query"]


@pytest.mark.parametrize("vector", [[], [1.0, 2.0], [float("nan"), 0.0, 1.0],
                                    [float("inf"), 0.0, 1.0], [1e100, 0.0, 1.0], [[1.0], 0.0, 1.0]])
def test_invalid_query_vector_never_reaches_native_search(stores, monkeypatch, vector):
    from langchain_community.vectorstores import FAISS

    _module, embedding, _root, _legacy, _paths, _active, build = stores
    facade = build()
    searched = []
    monkeypatch.setattr(FAISS, "similarity_search_with_score_by_vector", lambda *_a, **_kw: searched.append(True) or [])
    embedding.vector = vector
    assert facade.similarity_search("query") == []
    assert embedding.queries == ["query"] and searched == []


def test_next_request_rechecks_active_fingerprint(stores):
    _module, embedding, _root, _legacy, _paths, active, build = stores
    facade = build()
    assert len(facade.similarity_search("query", k=10)) == 4
    active["model"] = "v2"
    assert facade.similarity_search("query", k=10) == []
    assert embedding.queries == ["query"]


def test_stale_managed_fingerprint_leaves_compatible_legacy_queryable(stores):
    module, embedding, root, _legacy, _paths, _active, build = stores
    facade = build()
    entry = module.read_corpus_manifest(root)["documents"][0]
    path = module._document_directory(root, entry) / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["embedding"]["model"] = "stale"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert [item.page_content for item in facade.similarity_search("query", k=10)] == ["legacy"]
    assert embedding.queries == ["query"]


def test_loaded_store_search_failure_preserves_healthy_siblings(stores, monkeypatch):
    from langchain_community.vectorstores import FAISS

    _module, embedding, _root, _legacy, _paths, _active, build = stores
    facade = build()
    search = FAISS.similarity_search_with_score_by_vector

    def interleave(store, vector, **kwargs):
        document = store.docstore.search(store.index_to_docstore_id[0])
        if document.page_content == "managed-0":
            raise ValueError("Unreadable fixture store")
        return search(store, vector, **kwargs)

    monkeypatch.setattr(FAISS, "similarity_search_with_score_by_vector", interleave)
    assert {item.page_content for item in facade.similarity_search("query", k=10)} == {"legacy", "managed-1", "managed-2"}
    assert embedding.queries == ["query"]


def test_existing_callable_embedding_compatibility_is_preserved(stores):
    _module, embedding, _root, _legacy, _paths, _active, build = stores
    facade = build()
    facade.embedding_factory = lambda: embedding.embed_query
    assert len(facade.similarity_search("query", k=10)) == 4
    assert embedding.queries == ["query"]
