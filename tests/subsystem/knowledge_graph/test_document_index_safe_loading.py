from __future__ import annotations

import importlib
import hashlib
import json
import pickle
import struct

import pytest
from langchain_core.documents import Document

from tests.subsystem.knowledge_graph.test_document_index_shards import FakeEmbeddings


@pytest.fixture
def codec(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    import row_bot.document_index as document_index
    from langchain_community.vectorstores import FAISS

    module = importlib.reload(document_index)
    embedding = FakeEmbeddings()
    store = FAISS.from_documents([
        Document(page_content="first", metadata={"source": "first.txt", "nested": [1, True, None]}),
        Document(page_content="second", metadata={"source": "second.txt", "score": 1.5}),
    ], embedding=embedding)
    return module, store, embedding, tmp_path / "segment"


def test_new_segments_roundtrip_without_any_pickle_or_native_deserializer(codec, monkeypatch):
    module, store, embedding, directory = codec
    import faiss
    from langchain_community.vectorstores import FAISS

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unsafe deserializer invoked")

    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)
    monkeypatch.setattr(FAISS, "load_local", forbidden)
    monkeypatch.setattr(faiss, "read_index", forbidden)
    module._save_safe_segment(store, directory)
    assert not (directory / "index.pkl").exists()
    loaded = module._load_safe_segment(directory, embedding, expected_dimension=3, expected_count=2)
    assert loaded.similarity_search_with_score("query", k=2) == store.similarity_search_with_score("query", k=2)


@pytest.mark.parametrize("protocol", [4, 5])
def test_historical_metadata_is_decoded_as_data_and_preserves_sources(codec, monkeypatch, protocol):
    module, store, embedding, directory = codec
    store.save_local(str(directory))
    metadata_path = directory / "index.pkl"
    metadata_path.write_bytes(pickle.dumps((store.docstore, store.index_to_docstore_id), protocol=protocol))
    before = {path.name: path.read_bytes() for path in directory.iterdir()}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("pickle unpickler invoked")

    monkeypatch.setattr(pickle, "load", forbidden)
    monkeypatch.setattr(pickle, "loads", forbidden)
    loaded = module._load_safe_segment(directory, embedding, expected_dimension=3)
    assert loaded.similarity_search_with_score("query", k=2) == store.similarity_search_with_score("query", k=2)
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_malicious_legacy_reducer_never_executes(codec):
    module, store, embedding, directory = codec
    store.save_local(str(directory))
    marker = directory / "must-not-exist.txt"

    class Payload:
        def __reduce__(self):
            return (eval, (f"__import__('pathlib').Path({str(marker)!r}).write_text('executed')",))

    path = directory / "index.pkl"
    payload = pickle.dumps(Payload())
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="Unsupported legacy metadata type"):
        module._load_safe_segment(directory, embedding)
    assert not marker.exists()
    assert path.read_bytes() == payload


@pytest.mark.parametrize("payload", [
    b"\x80\x04\x82\x01.",  # EXT1 cannot use process-global registered callables.
    b"\x80\x04NQ.",  # Persistent ID callbacks are never installed.
    b"\x80\x04)R.",  # Reducers are never invoked, even without a GLOBAL.
])
def test_legacy_execution_opcodes_are_rejected(codec, payload):
    module, _store, _embedding, _directory = codec
    with pytest.raises(ValueError, match="Unsupported legacy metadata opcode"):
        module._legacy_metadata(payload)


@pytest.mark.parametrize("change", ["count", "ids", "text", "nan", "version"])
def test_forged_json_metadata_fails_closed(codec, change):
    module, store, embedding, directory = codec
    module._save_safe_segment(store, directory)
    path = directory / "index.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    first_id = next(iter(payload["documents"]))
    if change == "count":
        payload["index_to_docstore_id"].pop()
    elif change == "ids":
        payload["index_to_docstore_id"][0] = "foreign-id"
    elif change == "text":
        payload["documents"][first_id]["page_content"] = {"invalid": True}
    elif change == "nan":
        payload["documents"][first_id]["metadata"] = {"score": float("nan")}
    else:
        payload["version"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        module._load_safe_segment(directory, embedding)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == payload["version"]


@pytest.mark.parametrize("change", ["magic", "dimension", "count", "truncated", "trailing", "nan"])
def test_malformed_native_vector_file_is_rejected_without_loading(codec, monkeypatch, change):
    module, store, embedding, directory = codec
    module._save_safe_segment(store, directory)
    path = directory / "index.faiss"
    data = bytearray(path.read_bytes())
    if change == "magic":
        data[:4] = b"evil"
    elif change == "dimension":
        struct.pack_into("<i", data, 4, 2**30)
    elif change == "count":
        struct.pack_into("<q", data, 8, 2**50)
    elif change == "truncated":
        data.pop()
    elif change == "trailing":
        data.extend(b"unexpected")
    else:
        struct.pack_into("<f", data, 45, float("nan"))
    path.write_bytes(data)
    # Even forged metadata with a matching digest cannot bypass structural checks.
    metadata_path = directory / "index.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["vector_sha256"] = hashlib.sha256(data).hexdigest()
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        module._load_safe_segment(directory, embedding)
    assert path.read_bytes() == data


def test_metadata_and_vector_budgets_and_manifest_disagreement(codec, monkeypatch):
    module, store, embedding, directory = codec
    module._save_safe_segment(store, directory)
    with pytest.raises(ValueError, match="embedding fingerprint"):
        module._load_safe_segment(directory, embedding, expected_dimension=42)
    with pytest.raises(ValueError, match="segment manifest"):
        module._load_safe_segment(directory, embedding, expected_count=42)
    monkeypatch.setattr(module, "_VECTOR_LIMIT", 4)
    with pytest.raises(ValueError, match="safe read budget"):
        module._load_safe_segment(directory, embedding)
    monkeypatch.setattr(module, "_METADATA_LIMIT", 4)
    with pytest.raises(ValueError, match="safe read budget"):
        module._load_safe_segment(directory, embedding)


def test_cyclic_or_expanding_legacy_metadata_is_bounded(codec, monkeypatch):
    module, _store, _embedding, _directory = codec
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="structure budget"):
        module._plain_metadata(cyclic)
    monkeypatch.setattr(module, "_VALUE_LIMIT", 100)
    growing = ["leaf"]
    for _ in range(10):
        growing = [growing, growing]
    with pytest.raises(ValueError, match="structure budget"):
        module._plain_metadata(growing)


@pytest.mark.parametrize("managed", [False, True])
def test_forged_compatible_corpus_cannot_execute_legacy_payload(codec, monkeypatch, managed):
    module, store, embedding, directory = codec
    root = directory.parent / "corpus"
    metadata = {"provider": "fake", "model": "fake", "dimension": 3}
    monkeypatch.setattr(module, "active_embedding_metadata", lambda: metadata)
    monkeypatch.setattr(module, "index_metadata_matches", lambda *_: True)
    if managed:
        directory = root / "documents" / "imported" / "segment-0000"
        module._atomic_write_json(root / "manifest.json", {
            "version": 1, "documents": [{"document_id": "imported", "original_name": "imported.txt"}],
        })
        module._atomic_write_json(directory.parent / "manifest.json", {
            "complete": True, "document_id": "imported", "embedding": metadata,
            "segments": [{"name": "segment-0000"}],
        })
    store.save_local(str(directory))
    marker = root / "never-executed"

    class Payload:
        def __reduce__(self):
            return (eval, (f"__import__('pathlib').Path({str(marker)!r}).touch()",))

    payload = pickle.dumps(Payload())
    (directory / "index.pkl").write_bytes(payload)
    facade = module.DocumentVectorStoreFacade(
        index_root=root,
        legacy_root=root / "absent" if managed else directory,
        embedding_factory=lambda: embedding,
    )
    assert facade.similarity_search("query") == []
    assert not marker.exists()
    assert (directory / "index.pkl").read_bytes() == payload


def test_legacy_mapping_keys_cannot_trigger_recursive_hash_work(codec):
    module, _store, _embedding, _directory = codec
    # A tuple as a mapping key is outside the historical string-ID/int-index grammar.
    with pytest.raises(ValueError, match="Invalid legacy metadata mapping"):
        module._legacy_metadata(pickle.dumps({("tuple",): "value"}, protocol=4))


@pytest.mark.parametrize("key", [-1, 1_000_000, 2**127 - 1])
def test_legacy_integer_keys_are_bounded_vector_offsets(codec, key):
    module, _store, _embedding, _directory = codec
    with pytest.raises(ValueError, match="safe decode budget"):
        module._legacy_metadata(pickle.dumps({key: "value"}, protocol=4))


def test_generation_digest_rejects_mixed_metadata_and_vectors(codec):
    module, store, embedding, directory = codec
    module._save_safe_segment(store, directory)
    path = directory / "index.faiss"
    data = bytearray(path.read_bytes())
    struct.pack_into("<f", data, 45, 42.0)
    path.write_bytes(data)
    with pytest.raises(ValueError, match="metadata generation disagree"):
        module._load_safe_segment(directory, embedding)


def test_legacy_large_frames_and_memo_ids_preserve_document_count(codec):
    module, _store, embedding, directory = codec
    from langchain_community.vectorstores import FAISS

    documents = [Document(page_content=f"{i}: " + "synthetic " * 120, metadata={"ordinal": i}) for i in range(150)]
    store = FAISS.from_documents(documents, embedding=embedding)
    store.save_local(str(directory))
    loaded = module._load_safe_segment(directory, embedding)
    assert loaded.index.ntotal == 150
    assert sorted(doc.metadata["ordinal"] for doc in loaded.docstore._dict.values()) == list(range(150))


@pytest.mark.parametrize("segments", [[None], ["bad"], {"bad": 1}, None, 12])
def test_corrupt_segment_manifest_does_not_hide_healthy_sibling(codec, monkeypatch, segments):
    module, store, embedding, directory = codec
    root = directory.parent / "corpus"
    metadata = {"provider": "fake", "model": "fake", "dimension": 3}
    monkeypatch.setattr(module, "active_embedding_metadata", lambda: metadata)
    module._save_safe_segment(store, root / "documents" / "healthy" / "segment-0000")
    module._atomic_write_json(root / "manifest.json", {
        "version": 1, "documents": [{"document_id": "healthy"}, {"document_id": "corrupt"}, None],
    })
    for identifier, segment_value in [("healthy", [{"name": "segment-0000"}]), ("corrupt", segments)]:
        module._atomic_write_json(root / "documents" / identifier / "manifest.json", {
            "complete": True, "embedding": metadata, "segments": segment_value,
        })
    facade = module.DocumentVectorStoreFacade(
        index_root=root, legacy_root=root / "absent", embedding_factory=lambda: embedding,
    )
    assert sorted(doc.page_content for doc in facade.similarity_search("query")) == ["first", "second"]
    assert module.index_health(index_root=root, legacy_root=root / "absent")["partial_documents"] == 1


@pytest.mark.parametrize("chunk_index", ["bad", {}, [], -1, 10**30, True, None, 1.5])
def test_invalid_chunk_index_preserves_other_search_candidates(codec, monkeypatch, chunk_index):
    module, store, embedding, directory = codec
    root = directory.parent / "corpus"
    metadata = {"provider": "fake", "model": "fake", "dimension": 3}
    monkeypatch.setattr(module, "active_embedding_metadata", lambda: metadata)
    first = next(iter(store.docstore._dict.values()))
    first.metadata["chunk_index"] = chunk_index
    module._save_safe_segment(store, root / "documents" / "document" / "segment-0000")
    module._atomic_write_json(root / "manifest.json", {
        "version": 1, "documents": [{"document_id": "document"}],
    })
    module._atomic_write_json(root / "documents" / "document" / "manifest.json", {
        "complete": True, "embedding": metadata, "segments": [{"name": "segment-0000"}],
    })
    facade = module.DocumentVectorStoreFacade(
        index_root=root, legacy_root=root / "absent", embedding_factory=lambda: embedding,
    )
    assert sorted(doc.page_content for doc in facade.similarity_search("query")) == ["first", "second"]
    assert module._candidate_key(1.0, first, ordinal=9)[3] == 9


@pytest.mark.parametrize("chunk_index, expected", [(0, 0), (2, 2), ("0", 0), ("2", 2)])
def test_compatible_chunk_offsets_keep_their_order(codec, chunk_index, expected):
    module, _store, _embedding, _directory = codec
    document = Document(page_content="text", metadata={"chunk_index": chunk_index})
    assert module._candidate_key(1.0, document, ordinal=9)[3] == expected
