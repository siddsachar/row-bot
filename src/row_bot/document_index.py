"""Transactional, per-document FAISS shards with legacy-read compatibility."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import pathlib
import pickletools
import re
import shutil
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from langchain_core.documents import Document

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.document_jobs import INDEX_SEGMENT_CHUNKS
from row_bot.flat_vector_storage import MAX_VECTOR_BYTES, decode_flat_vectors
from row_bot.embedding_config import (
    active_embedding_metadata,
    get_embedding_config,
    index_metadata_matches,
)

logger = logging.getLogger(__name__)

DATA_DIR = get_row_bot_data_dir()
DOCUMENT_INDEX_DIR = DATA_DIR / "document_index"
LEGACY_VECTOR_STORE_DIR = DATA_DIR / "vector_store"
CORPUS_MANIFEST_NAME = "manifest.json"
DOCUMENTS_DIR_NAME = "documents"
MANIFEST_VERSION = 1
_manifest_lock = threading.RLock()
_METADATA_LIMIT = 32 * 1024 * 1024
_VECTOR_LIMIT = MAX_VECTOR_BYTES
_VALUE_LIMIT = 1_000_000


def _bounded_file(directory: pathlib.Path, name: str, limit: int) -> bytes:
    path = directory / name
    resolved = path.resolve(strict=True)
    if resolved.parent != directory.resolve():
        raise ValueError("Index file escapes its managed directory")
    with resolved.open("rb") as handle:
        value = handle.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Index file exceeds the safe read budget; rebuild required")
    return value


def _plain_metadata(value: Any) -> Any:
    """Copy only bounded JSON values, rejecting cycles and expansion bombs."""
    remaining = _VALUE_LIMIT

    def visit(item: Any, depth: int) -> Any:
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 32:
            raise ValueError("Index metadata exceeds the safe structure budget")
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        if type(item) in (list, tuple):
            return [visit(child, depth + 1) for child in item]
        if type(item) is dict and all(type(key) is str for key in item):
            return {key: visit(child, depth + 1) for key, child in item.items()}
        raise ValueError("Unsupported document metadata; rebuild required")

    return visit(value, 0)


@dataclass
class _LegacyType:
    name: str


@dataclass
class _LegacyRecord:
    """Inert historical record; never instantiate the class named by a pickle."""

    name: str
    state: dict[str, Any] | None = None


def _legacy_metadata(data: bytes) -> tuple[dict[str, Any], dict[int, str]]:
    """Decode only the historical FAISS docstore data grammar, without unpickling.

    GLOBAL names are checked literals and NEWOBJ/BUILD become inert records.
    No import, callable, reducer, extension, persistent ID or state hook runs.
    Unsupported historical objects stay on disk for an explicit source rebuild.
    """
    stack: list[Any] = []
    memo: dict[int, Any] = {}
    marker = object()
    allowed_types = {
        ("langchain_community.docstore.in_memory", "InMemoryDocstore"),
        ("langchain.docstore.in_memory", "InMemoryDocstore"),
        ("langchain_core.documents.base", "Document"),
        ("langchain.schema.document", "Document"),
    }

    def marked() -> list[Any]:
        position = next(i for i in range(len(stack) - 1, -1, -1) if stack[i] is marker)
        values = stack[position + 1:]
        del stack[position:]
        return values

    for count, (opcode, argument, position) in enumerate(pickletools.genops(data)):
        if count > _VALUE_LIMIT or len(stack) > _VALUE_LIMIT or len(memo) > _VALUE_LIMIT:
            raise ValueError("Legacy metadata exceeds safe decode budget")
        name = opcode.name
        if name in {"PROTO", "FRAME"}:
            continue
        if name == "MARK":
            stack.append(marker)
        elif name in {"NONE", "NEWTRUE", "NEWFALSE"}:
            stack.append({"NONE": None, "NEWTRUE": True, "NEWFALSE": False}[name])
        elif name in {"SHORT_BINUNICODE", "BINUNICODE", "BINUNICODE8", "UNICODE",
                      "BININT", "BININT1", "BININT2", "LONG1", "LONG4", "BINFLOAT"}:
            stack.append(argument)
        elif name in {"EMPTY_DICT", "EMPTY_LIST", "EMPTY_TUPLE", "EMPTY_SET"}:
            stack.append({"EMPTY_DICT": dict, "EMPTY_LIST": list,
                          "EMPTY_TUPLE": tuple, "EMPTY_SET": set}[name]())
        elif name in {"MEMOIZE", "BINPUT", "LONG_BINPUT"}:
            memo[len(memo) if name == "MEMOIZE" else argument] = stack[-1]
        elif name in {"BINGET", "LONG_BINGET"}:
            stack.append(memo[argument])
        elif name in {"STACK_GLOBAL", "GLOBAL"}:
            if name == "STACK_GLOBAL":
                class_name = stack.pop()
                module = stack.pop()
            else:
                module, class_name = argument.split(" ")
            if type(module) is not str or type(class_name) is not str or (module, class_name) not in allowed_types:
                raise ValueError("Unsupported legacy metadata type; rebuild required")
            stack.append(_LegacyType(class_name))
        elif name == "NEWOBJ":
            arguments, kind = stack.pop(), stack.pop()
            if type(kind) is not _LegacyType or arguments != ():
                raise ValueError("Invalid legacy metadata construction")
            stack.append(_LegacyRecord(kind.name))
        elif name == "BUILD":
            state = stack.pop()
            record = stack[-1]
            if type(record) is not _LegacyRecord or record.state is not None or type(state) is not dict:
                raise ValueError("Invalid legacy metadata state")
            record.state = state
        elif name in {"TUPLE", "TUPLE1", "TUPLE2", "TUPLE3"}:
            if name == "TUPLE":
                values = marked()
            else:
                length = int(name[-1])
                values = stack[-length:]
                del stack[-length:]
            stack.append(tuple(values))
        elif name in {"SETITEM", "SETITEMS"}:
            values = marked() if name == "SETITEMS" else [stack.pop(), stack.pop()][::-1]
            if type(stack[-1]) is not dict or len(values) % 2 or any(type(key) not in (str, int) for key in values[::2]):
                raise ValueError("Invalid legacy metadata mapping")
            if any(type(key) is int and not 0 <= key < _VALUE_LIMIT for key in values[::2]):
                raise ValueError("Legacy vector index key exceeds safe decode budget")
            stack[-1].update(zip(values[::2], values[1::2]))
        elif name in {"APPEND", "APPENDS", "ADDITEMS"}:
            values = marked() if name != "APPEND" else [stack.pop()]
            if name == "ADDITEMS" and type(stack[-1]) is set:
                if any(type(value) is not str for value in values):
                    raise ValueError("Invalid legacy document field names")
                stack[-1].update(values)
            elif name != "ADDITEMS" and type(stack[-1]) is list:
                stack[-1].extend(values)
            else:
                raise ValueError("Invalid legacy metadata collection")
        elif name == "STOP":
            if len(stack) != 1 or position != len(data) - 1:
                raise ValueError("Invalid legacy metadata termination")
            break
        else:
            raise ValueError(f"Unsupported legacy metadata opcode {name}; rebuild required")
    value = stack[0]
    if type(value) is not tuple or len(value) != 2:
        raise ValueError("Invalid legacy FAISS metadata")
    docstore, mapping = value
    if type(docstore) is not _LegacyRecord or docstore.name != "InMemoryDocstore":
        raise ValueError("Invalid legacy docstore")
    if type(docstore.state) is not dict or set(docstore.state) != {"_dict"}:
        raise ValueError("Invalid legacy docstore state")
    rows = docstore.state["_dict"]
    if type(rows) is not dict or type(mapping) is not dict:
        raise ValueError("Invalid legacy document mapping")
    documents = {}
    for identifier, record in rows.items():
        if type(record) is not _LegacyRecord or record.name != "Document" or not record.state:
            raise ValueError("Invalid legacy document")
        document = record.state.get("__dict__")
        if type(document) is not dict:
            raise ValueError("Invalid legacy document state")
        documents[identifier] = {
            "id": document.get("id"),
            "page_content": document.get("page_content"),
            "metadata": document.get("metadata", {}),
        }
    return _plain_metadata(documents), mapping


def _load_safe_segment(
    directory: pathlib.Path,
    embedding: Any,
    *,
    expected_dimension: int | None = None,
    expected_count: int | None = None,
) -> Any:
    """Load validated flat numeric vectors and JSON or inert legacy metadata."""
    from langchain_community.docstore.in_memory import InMemoryDocstore
    from langchain_community.vectorstores import FAISS
    from langchain_community.vectorstores.faiss import dependable_faiss_import

    if (directory / "index.json").exists():
        payload = json.loads(_bounded_file(directory, "index.json", _METADATA_LIMIT))
        if type(payload) is not dict or payload.get("version") != 1:
            raise ValueError("Unsupported index metadata version; rebuild required")
        documents = payload.get("documents")
        identifiers = payload.get("index_to_docstore_id")
        if type(identifiers) is not list:
            raise ValueError("Invalid index document mapping")
        mapping = dict(enumerate(identifiers))
        vector_digest = payload.get("vector_sha256")
        if type(vector_digest) is not str or len(vector_digest) != 64:
            raise ValueError("Missing vector publication digest; rebuild required")
    else:
        documents, mapping = _legacy_metadata(_bounded_file(directory, "index.pkl", _METADATA_LIMIT))
        vector_digest = None

    data = _bounded_file(directory, "index.faiss", _VECTOR_LIMIT)
    if vector_digest is not None and hashlib.sha256(data).hexdigest() != vector_digest:
        raise ValueError("Vector file and metadata generation disagree; rebuild required")
    decoded = decode_flat_vectors(data, expected_dimension=expected_dimension,
                                  expected_count=expected_count)
    dimension, total = decoded.dimension, decoded.count
    if type(documents) is not dict or len(documents) != total or len(mapping) != total:
        raise ValueError("Vector and document counts disagree; rebuild required")
    if any(type(key) is not int or key < 0 or key >= total for key in mapping):
        raise ValueError("Invalid vector to document mapping")
    if any(type(identifier) is not str for identifier in mapping.values()) or set(mapping.values()) != set(documents):
        raise ValueError("Vector and document identities disagree; rebuild required")
    rows = {}
    for identifier, document in documents.items():
        if type(document) is not dict or type(document.get("page_content")) is not str:
            raise ValueError("Invalid indexed document text")
        metadata = _plain_metadata(document.get("metadata", {}))
        if type(metadata) is not dict or document.get("id") is not None and type(document["id"]) is not str:
            raise ValueError("Invalid indexed document metadata")
        rows[identifier] = Document(page_content=document["page_content"], metadata=metadata, id=document.get("id"))
    faiss = dependable_faiss_import()
    native = faiss.IndexFlatL2(dimension) if decoded.metric == "l2" else faiss.IndexFlatIP(dimension)
    native.add(decoded.vectors)
    return FAISS(embedding, native, InMemoryDocstore(rows), mapping)


def _save_safe_segment(store: Any, directory: pathlib.Path) -> None:
    """Write only JSON document metadata alongside the existing flat vector file."""
    from langchain_community.vectorstores.faiss import dependable_faiss_import

    identifiers = [store.index_to_docstore_id[i] for i in range(store.index.ntotal)]
    rows = {}
    for identifier in identifiers:
        document = store.docstore.search(identifier)
        rows[identifier] = {
            "id": document.id,
            "page_content": document.page_content,
            "metadata": _plain_metadata(document.metadata),
        }
    directory.mkdir(parents=True, exist_ok=False)
    faiss = dependable_faiss_import()
    faiss.write_index(store.index, str(directory / "index.faiss"))
    vector_data = _bounded_file(directory, "index.faiss", _VECTOR_LIMIT)
    payload = {
        "version": 1,
        "documents": rows,
        "index_to_docstore_id": identifiers,
        "vector_sha256": hashlib.sha256(vector_data).hexdigest(),
    }
    if len(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")) + 1 > _METADATA_LIMIT:
        raise ValueError("Index metadata exceeds safe publication budget")
    _atomic_write_json(directory / "index.json", payload)
    _load_safe_segment(
        directory,
        store.embedding_function,
        expected_dimension=store.index.d,
        expected_count=store.index.ntotal,
    )


def _empty_manifest() -> dict[str, Any]:
    return {"version": MANIFEST_VERSION, "documents": []}


def _read_json(path: pathlib.Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return default


def _atomic_write_json(
    path: pathlib.Path,
    value: Any,
    *,
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None] = os.replace,
    validate: Callable[[], None] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    if validate is not None:
        validate()
    try:
        replace(temp, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()
        raise


def read_corpus_manifest(index_root: pathlib.Path = DOCUMENT_INDEX_DIR) -> dict[str, Any]:
    manifest = _read_json(index_root / CORPUS_MANIFEST_NAME, _empty_manifest())
    if not isinstance(manifest, dict) or not isinstance(manifest.get("documents"), list):
        return _empty_manifest()
    return manifest


def initialize_index(index_root: pathlib.Path = DOCUMENT_INDEX_DIR) -> None:
    (index_root / DOCUMENTS_DIR_NAME).mkdir(parents=True, exist_ok=True)
    manifest_path = index_root / CORPUS_MANIFEST_NAME
    if not manifest_path.exists():
        _atomic_write_json(manifest_path, _empty_manifest())


def _document_directory(index_root: pathlib.Path, entry: dict[str, Any]) -> pathlib.Path:
    """Resolve one immutable corpus snapshot, including pre-generation indexes."""
    if not isinstance(entry, dict):
        raise ValueError("Invalid document index entry")
    document_id = str(entry.get("document_id") or "")
    generation = entry.get("generation")
    components = [document_id] if generation is None else [document_id, generation]
    if any(
        not isinstance(part, str)
        or not part
        or part in {".", ".."}
        or any(character in part for character in '/\\:')
        for part in components
    ):
        raise ValueError("Invalid document index identity")
    owner = index_root / DOCUMENTS_DIR_NAME
    directory = owner.joinpath(*components)
    if owner.resolve() not in directory.resolve().parents:
        raise ValueError("Document index directory escapes its owner")
    return directory


def _safe_remove_tree(path: pathlib.Path, owner: pathlib.Path) -> None:
    resolved = path.resolve()
    resolved_owner = owner.resolve()
    if resolved == resolved_owner or resolved_owner not in resolved.parents:
        raise ValueError(f"Refusing cleanup outside {resolved_owner}")
    if resolved.exists():
        shutil.rmtree(resolved)


def _segment_parts(
    batches: Iterable[list[Document]],
    *,
    segment_chunks: int,
) -> Iterator[tuple[list[Document], bool]]:
    """Yield pieces plus a flag indicating that a segment is full."""
    count = 0
    for batch in batches:
        offset = 0
        while offset < len(batch):
            room = segment_chunks - count
            part = batch[offset : offset + room]
            offset += len(part)
            count += len(part)
            full = count >= segment_chunks
            yield part, full
            if full:
                count = 0


def build_unpublished_document(
    *,
    document_id: str,
    original_name: str,
    stored_name: str,
    content_sha256: str,
    chunks: Iterable[Document],
    work_document_dir: pathlib.Path,
    embedding: Any,
    check_cancelled: Callable[[], None] | None = None,
    progress: Callable[[int], None] | None = None,
    embedding_metadata: dict[str, Any] | None = None,
    segment_chunks: int = INDEX_SEGMENT_CHUNKS,
) -> dict[str, Any]:
    """Build fixed-size FAISS segments under an unpublished work directory."""
    from langchain_community.vectorstores import FAISS
    from row_bot.documents import iter_chunk_batches

    if work_document_dir.exists():
        _safe_remove_tree(work_document_dir, work_document_dir.parent)
    work_document_dir.mkdir(parents=True, exist_ok=False)

    segment_number = 0
    segment_count = 0
    total_chunks = 0
    segment_store = None
    segments: list[dict[str, Any]] = []

    def save_segment() -> None:
        nonlocal segment_number, segment_count, segment_store
        if segment_store is None or segment_count == 0:
            return
        if check_cancelled:
            check_cancelled()
        name = f"segment-{segment_number:04d}"
        segment_path = work_document_dir / name
        _save_safe_segment(segment_store, segment_path)
        segments.append({"name": name, "chunk_count": segment_count})
        segment_number += 1
        segment_count = 0
        segment_store = None

    try:
        batches = iter_chunk_batches(chunks)
        for part, segment_full in _segment_parts(batches, segment_chunks=segment_chunks):
            if check_cancelled:
                check_cancelled()
            if not part:
                continue
            if segment_store is None:
                segment_store = FAISS.from_documents(part, embedding=embedding)
            else:
                segment_store.add_documents(part)
            segment_count += len(part)
            total_chunks += len(part)
            if progress:
                progress(total_chunks)
            if segment_full:
                save_segment()
        save_segment()
        if not total_chunks:
            raise ValueError("No valid text content found in the document.")
        manifest = {
            "version": MANIFEST_VERSION,
            "document_id": document_id,
            "original_name": original_name,
            "stored_name": stored_name,
            "content_sha256": content_sha256,
            "embedding": embedding_metadata or active_embedding_metadata(),
            "created_at": datetime.now(UTC).isoformat(),
            "chunk_count": total_chunks,
            "segment_count": len(segments),
            "segments": segments,
            "complete": True,
        }
        _atomic_write_json(work_document_dir / "manifest.json", manifest)
        return manifest
    except Exception:
        _safe_remove_tree(work_document_dir, work_document_dir.parent)
        raise


def publish_document(
    work_document_dir: pathlib.Path,
    document_manifest: dict[str, Any],
    *,
    index_root: pathlib.Path = DOCUMENT_INDEX_DIR,
    replace: Callable[[str | os.PathLike[str], str | os.PathLike[str]], None] = os.replace,
    validate: Callable[[], None] | None = None,
) -> None:
    """Expose a complete immutable generation with one corpus manifest commit.

    Previously published generations stay in place for readers holding an older
    corpus snapshot and for recovery. Their eventual retirement belongs to the
    document deletion/reset owner, not the publication critical section.
    """
    with _manifest_lock:
        if validate is not None:
            validate()
        initialize_index(index_root)
        document_id = str(document_manifest["document_id"])
        generation = f"generation-{uuid4().hex}"
        generation_dir = _document_directory(
            index_root, {"document_id": document_id, "generation": generation}
        )
        generation_dir.parent.mkdir(parents=True, exist_ok=True)
        if validate is not None:
            validate()
        os.replace(work_document_dir, generation_dir)

        manifest_path = index_root / CORPUS_MANIFEST_NAME
        corpus = read_corpus_manifest(index_root)
        entries = [
            entry
            for entry in corpus["documents"]
            if str(entry.get("document_id")) != document_id
        ]
        entries.append(
            {
                "document_id": document_id,
                "generation": generation,
                "original_name": document_manifest["original_name"],
                "stored_name": document_manifest["stored_name"],
                "content_sha256": document_manifest["content_sha256"],
                "created_at": document_manifest["created_at"],
            }
        )
        entries.sort(key=lambda item: (str(item.get("created_at", "")), str(item["document_id"])))
        _atomic_write_json(
            manifest_path,
            {"version": MANIFEST_VERSION, "documents": entries},
            replace=replace,
            **({"validate": validate} if validate is not None else {}),
        )


def _embedding_matches(stored: Any, active: dict[str, Any]) -> bool:
    if not isinstance(stored, dict):
        return False
    keys = ("provider", "model", "dimension")
    return all(stored.get(key) == active.get(key) for key in keys)


def _candidate_key(
    score: float,
    document: Document,
    *,
    ordinal: int,
) -> tuple[float, str, str, int, int]:
    metadata = document.metadata or {}
    chunk_index = metadata.get("chunk_index")
    if type(chunk_index) is str and len(chunk_index) <= 20:
        try:
            chunk_index = int(chunk_index)
        except ValueError:
            chunk_index = ordinal
    if type(chunk_index) is not int or not 0 <= chunk_index < _VALUE_LIMIT:
        chunk_index = ordinal
    return (
        float(score),
        str(metadata.get("source") or ""),
        str(metadata.get("document_id") or ""),
        chunk_index,
        ordinal,
    )


class DocumentIndexRetriever:
    def __init__(self, facade: "DocumentVectorStoreFacade", k: int) -> None:
        self.facade = facade
        self.k = max(1, int(k))

    def invoke(self, query: str, *_args: Any, **_kwargs: Any) -> list[Document]:
        return self.facade.similarity_search(query, k=self.k)

    def get_relevant_documents(self, query: str) -> list[Document]:
        return self.invoke(query)


class DocumentVectorStoreFacade:
    """Search sharded documents and the compatible legacy index sequentially."""

    def __init__(
        self,
        *,
        index_root: pathlib.Path = DOCUMENT_INDEX_DIR,
        legacy_root: pathlib.Path = LEGACY_VECTOR_STORE_DIR,
        embedding_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.index_root = pathlib.Path(index_root)
        self.legacy_root = pathlib.Path(legacy_root)
        self.embedding_factory = embedding_factory

    def as_retriever(self, *, search_kwargs: dict[str, Any] | None = None, **_kwargs: Any) -> DocumentIndexRetriever:
        kwargs = search_kwargs or {}
        return DocumentIndexRetriever(self, int(kwargs.get("k", 4)))

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        return [document for _score, _key, document in self._search(query, k)]

    def similarity_search_with_score(
        self,
        query: str,
        k: int = 4,
    ) -> list[tuple[Document, float]]:
        return [(document, score) for score, _key, document in self._search(query, k)]

    def _search(
        self,
        query: str,
        k: int,
    ) -> list[tuple[float, tuple[float, str, str, int, int], Document]]:
        from row_bot.documents import get_embedding_model_for_recall

        k = max(1, int(k))
        config = get_embedding_config()
        embedding = (
            self.embedding_factory()
            if self.embedding_factory is not None
            else get_embedding_model_for_recall(config)
        )
        active = active_embedding_metadata() if self.embedding_factory is not None else active_embedding_metadata(config)
        corpus = read_corpus_manifest(self.index_root)
        visible_entries: list[dict[str, Any]] = []
        for entry in corpus["documents"]:
            if isinstance(entry, dict) and entry.get("document_id"):
                visible_entries.append(entry)
        name_counts: dict[str, int] = {}
        for entry in visible_entries:
            name = str(entry.get("original_name") or "")
            name_counts[name] = name_counts.get(name, 0) + 1

        candidates: list[tuple[float, tuple[float, str, str, int, int], Document]] = []
        query_vector: list[float] | None = None
        query_failed = False

        def search_store(store: Any) -> list[tuple[Document, float]]:
            nonlocal query_vector, query_failed
            if not store.index.ntotal:
                return []
            if query_failed:
                return []
            if query_vector is None:
                try:
                    # Use FAISS's existing Embeddings/callable compatibility,
                    # once per request after a readable store is admitted.
                    raw = store._embed_query(query)
                    if not 0 < len(raw) <= 65536:
                        raise ValueError("Invalid query vector shape")
                    vector = [float(value) for value in raw]
                    if any(not math.isfinite(value) or abs(value) > 3.4028234663852886e38 for value in vector):
                        raise ValueError("Query vector exceeds finite float32 bounds")
                    query_vector = vector
                except Exception:
                    # A failed embedding belongs to this request, not each
                    # segment. Keep the existing graceful failure behavior.
                    query_failed = True
                    raise
            if len(query_vector) != store.index.d:
                raise ValueError("Query vector and document index dimension disagree")
            return store.similarity_search_with_score_by_vector(query_vector, k=k)

        def retain_candidate(
            candidate: tuple[float, tuple[float, str, str, int, int], Document],
        ) -> None:
            candidates.append(candidate)
            candidates.sort(key=lambda item: item[1])
            del candidates[k:]

        ordinal = 0
        for entry in visible_entries:
            document_id = str(entry["document_id"])
            try:
                document_dir = _document_directory(self.index_root, entry)
            except ValueError:
                logger.warning("Skipping invalid document index identity")
                continue
            document_manifest = _read_json(document_dir / "manifest.json", {})
            if (
                not isinstance(document_manifest, dict)
                or not document_manifest.get("complete")
                or not _embedding_matches(document_manifest.get("embedding"), active)
            ):
                continue
            original_name = str(document_manifest.get("original_name") or entry.get("original_name") or "")
            source = original_name
            if name_counts.get(original_name, 0) > 1:
                ingested = str(document_manifest.get("created_at") or "")[:10]
                disambiguator = (
                    f"{ingested} · {document_id[:8]}"
                    if ingested
                    else document_id[:8]
                )
                source = f"{original_name} ({disambiguator})"
            segments = document_manifest.get("segments")
            if not isinstance(segments, list):
                continue
            for segment_number, segment in enumerate(segments):
                if not isinstance(segment, dict):
                    continue
                segment_name = str(segment.get("name") or "")
                if not segment_name or segment_name in {".", ".."} or any(c in segment_name for c in '/\\:'):
                    continue
                try:
                    segment_dir = document_dir / segment_name
                    if segment_dir.resolve().parent != document_dir.resolve():
                        raise ValueError("Segment escapes its managed document generation")
                    store = _load_safe_segment(
                        segment_dir,
                        embedding,
                        expected_dimension=active.get("dimension"),
                        expected_count=segment.get("chunk_count"),
                    )
                    hits = search_store(store)
                except Exception:
                    logger.warning(
                        "Skipping unreadable document segment %s/%s",
                        document_id,
                        segment_name,
                        exc_info=True,
                    )
                    if query_failed:
                        return []
                    continue
                for chunk_number, (document, raw_score) in enumerate(hits):
                    metadata = dict(document.metadata or {})
                    metadata.update(
                        {
                            "source": source,
                            "original_name": original_name,
                            "document_id": document_id,
                            "segment": segment_number,
                            "chunk_index": metadata.get("chunk_index", chunk_number),
                        }
                    )
                    document.metadata = metadata
                    key = _candidate_key(float(raw_score), document, ordinal=ordinal)
                    retain_candidate((float(raw_score), key, document))
                    ordinal += 1
                del store

        if self.legacy_root.exists() and index_metadata_matches(self.legacy_root, active):
            try:
                tombstone_path = self.index_root / "legacy_tombstones.json"
                tombstones_value = _read_json(tombstone_path, [])
                tombstones = {
                    str(item)
                    for item in tombstones_value
                    if isinstance(tombstones_value, list)
                }
                legacy = _load_safe_segment(
                    self.legacy_root,
                    embedding,
                    expected_dimension=active.get("dimension"),
                )
                for document, raw_score in search_store(legacy):
                    metadata = dict(document.metadata or {})
                    if str(metadata.get("source") or "") in tombstones:
                        continue
                    metadata.setdefault("legacy", True)
                    metadata.setdefault("document_id", "")
                    document.metadata = metadata
                    key = _candidate_key(float(raw_score), document, ordinal=ordinal)
                    retain_candidate((float(raw_score), key, document))
                    ordinal += 1
                del legacy
            except Exception:
                logger.warning("Compatible legacy document index could not be queried", exc_info=True)

        return candidates

    def clear_cache(self) -> None:
        """The facade intentionally retains no loaded FAISS segments."""


def remove_document_shard(
    document_id: str,
    *,
    index_root: pathlib.Path = DOCUMENT_INDEX_DIR,
    retirement_id: str | None = None,
    expected_entry: dict | None = None,
    validate: Callable[[], None] | None = None,
) -> bool:
    with _manifest_lock:
        if validate is not None:
            validate()
        live = _document_directory(index_root, {"document_id": document_id})
        retirement_id = retirement_id or uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", retirement_id):
            raise ValueError("Invalid document retirement ID")
        retired = index_root / "retired" / f"{document_id}-{retirement_id}"
        if index_root.resolve() not in retired.resolve().parents:
            raise ValueError("Document retirement path is outside its index owner")
        manifest = read_corpus_manifest(index_root)
        entries = manifest["documents"]
        if any(not isinstance(entry, dict) for entry in entries):
            raise ValueError("Invalid corpus document entry")
        current_entry = next((entry for entry in entries if str(entry.get("document_id")) == document_id), None)
        if expected_entry is not None and current_entry is not None and current_entry != expected_entry:
            raise ValueError("Document generation changed since removal was requested")
        kept = [entry for entry in entries if str(entry.get("document_id")) != document_id]
        removed = len(kept) != len(entries)
        if removed:
            if validate is not None:
                validate()
            _atomic_write_json(
                index_root / CORPUS_MANIFEST_NAME,
                {"version": MANIFEST_VERSION, "documents": kept},
                **({"validate": validate} if validate is not None else {}),
            )
        if live.exists():
            if retired.exists():
                raise ValueError("Both live and retired document generations exist; preserve for review")
            retired.parent.mkdir(parents=True, exist_ok=True)
            if validate is not None:
                validate()
            os.rename(live, retired)
        return removed or retired.exists()


def reset_sharded_index(
    *,
    index_root: pathlib.Path = DOCUMENT_INDEX_DIR,
) -> pathlib.Path | None:
    with _manifest_lock:
        retired: pathlib.Path | None = None
        if index_root.exists():
            retired = index_root.with_name(
                f"{index_root.name}.retired-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
            )
            os.replace(index_root, retired)
        initialize_index(index_root)
        return retired


def index_health(
    *,
    index_root: pathlib.Path = DOCUMENT_INDEX_DIR,
    legacy_root: pathlib.Path = LEGACY_VECTOR_STORE_DIR,
) -> dict[str, Any]:
    active = active_embedding_metadata()
    corpus_path = index_root / CORPUS_MANIFEST_NAME
    corpus = read_corpus_manifest(index_root)
    stale = 0
    readable = 0
    partial = 0
    for entry in corpus["documents"]:
        try:
            document_dir = _document_directory(index_root, entry)
        except ValueError:
            partial += 1
            continue
        manifest = _read_json(document_dir / "manifest.json", {})
        if not isinstance(manifest, dict) or not manifest.get("complete"):
            partial += 1
        elif not _embedding_matches(manifest.get("embedding"), active):
            stale += 1
        else:
            readable += 1
    visible_ids = {
        str(entry.get("document_id") or "")
        for entry in corpus["documents"]
        if isinstance(entry, dict)
    }
    documents_root = index_root / DOCUMENTS_DIR_NAME
    orphan_documents = (
        sum(
            child.is_dir() and child.name not in visible_ids
            for child in documents_root.iterdir()
        )
        if documents_root.exists()
        else 0
    )
    legacy_exists = legacy_root.exists()
    legacy_compatible = legacy_exists and index_metadata_matches(legacy_root, active)
    return {
        "exists": corpus_path.exists() or legacy_exists,
        "sharded_documents": len(corpus["documents"]),
        "readable_documents": readable,
        "stale_documents": stale,
        "partial_documents": partial,
        "orphan_documents": int(orphan_documents),
        "legacy_exists": legacy_exists,
        "legacy_compatible": legacy_compatible,
        "stale": bool(stale or (legacy_exists and not legacy_compatible)),
        "active": active,
    }
