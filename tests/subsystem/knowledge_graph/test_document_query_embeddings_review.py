"""Independent request-isolation regressions for the real document facade."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests.subsystem.knowledge_graph.test_document_query_embeddings import stores as _stores_fixture

pytestmark = pytest.mark.subsystem
stores = _stores_fixture


def test_same_query_uses_new_provider_vector_on_next_request(stores):
    _module, embedding, _root, _legacy, _paths, _active, build = stores
    facade = build()
    embedding.vector = [6.0, 0.0, 1.0]
    first = facade.similarity_search_with_score("same query", k=1)
    assert [(document.page_content, score) for document, score in first] == [("legacy", 0.0)]

    embedding.vector = [9.0, 0.0, 1.0]
    second = facade.similarity_search_with_score("same query", k=1)
    assert [(document.page_content, score) for document, score in second] == [("managed-0", 0.0)]
    assert embedding.queries == ["same query", "same query"]


@pytest.mark.parametrize("first_fails", [False, True])
def test_concurrent_requests_do_not_share_vectors_or_failure_state(stores, monkeypatch, first_fails):
    _module, embedding, _root, _legacy, _paths, _active, build = stores
    facade = build()
    entered = Barrier(2)
    calls = []

    def query_vector(query):
        calls.append(query)
        entered.wait(timeout=5)
        if query == "first":
            if first_fails:
                raise RuntimeError("Synthetic request failure")
            return [6.0, 0.0, 1.0]
        return [9.0, 0.0, 1.0]

    monkeypatch.setattr(embedding, "embed_query", query_vector)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(facade.similarity_search_with_score, "first", 1)
        second = executor.submit(facade.similarity_search_with_score, "second", 1)
        a, b = first.result(timeout=10), second.result(timeout=10)
    assert sorted(calls) == ["first", "second"]
    assert [(document.page_content, score) for document, score in a] == (
        [] if first_fails else [("legacy", 0.0)]
    )
    assert [(document.page_content, score) for document, score in b] == [("managed-0", 0.0)]
