"""Bounded document parsing and compatibility access to document retrieval."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import logging
import os
import pathlib
import shutil
import threading
import uuid
from copy import deepcopy
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from typing import Any

from langchain_core.documents import Document

from row_bot.data_paths import get_row_bot_data_dir
from row_bot.document_jobs import EMBEDDING_BATCH_SIZE, TERMINAL_JOB_STATUSES, DocumentJob, DocumentJobService
from row_bot.embedding_config import (
    active_embedding_metadata,
    describe_active_embedding,
    get_embedding_config,
    read_index_metadata,
)
from row_bot.embedding_providers import (
    ensure_embedding_runtime_available,
    get_embedding_provider,
    get_embedding_provider_for_recall,
    release_embedding_resources,
)

logger = logging.getLogger(__name__)

DATA_DIR = get_row_bot_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_FILES_PATH = DATA_DIR / "processed_files.json"
VECTOR_STORE_DIR = DATA_DIR / "vector_store"  # read-only legacy compatibility
DOCUMENT_INDEX_DIR = DATA_DIR / "document_index"

CHUNK_SIZE = 1_500
CHUNK_OVERLAP = 150
TEXT_PAGE_CHARS = 64 * 1_024
TEXT_ENCODING_SAMPLE_BYTES = 64 * 1_024

_processed_files_lock = threading.RLock()
_embedding_lock = threading.Lock()
_vector_store = None


def load_processed_files() -> set[str]:
    """Return legacy names plus display names from durable document records."""
    processed: set[str] = set()
    if PROCESSED_FILES_PATH.exists():
        try:
            value = json.loads(PROCESSED_FILES_PATH.read_text(encoding="utf-8"))
            if isinstance(value, list):
                processed.update(str(item) for item in value)
        except (json.JSONDecodeError, OSError, TypeError):
            logger.warning("Ignoring unreadable legacy processed-files metadata")
    try:
        service = DocumentJobService(DATA_DIR)
        processed.update(
            str(record["original_name"]) for record in service.list_document_records()
        )
    except Exception:
        logger.debug("Durable document records unavailable", exc_info=True)
    return processed


def save_processed_file(file_path: str) -> None:
    """Atomically add a legacy processed-file marker."""
    with _processed_files_lock:
        processed = load_processed_files()
        processed.add(str(file_path))
        temp = PROCESSED_FILES_PATH.with_name(f".{PROCESSED_FILES_PATH.name}.tmp")
        temp.write_text(
            json.dumps(sorted(processed), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp, PROCESSED_FILES_PATH)


def is_file_processed(file_path: str) -> bool:
    return str(file_path) in load_processed_files()


def clear_processed_files() -> None:
    with contextlib.suppress(FileNotFoundError):
        PROCESSED_FILES_PATH.unlink()


def _text_encoding(path: pathlib.Path) -> str:
    with path.open("rb") as handle:
        sample = handle.read(TEXT_ENCODING_SAMPLE_BYTES)
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8"


class _BoundedTextLoader:
    """Small compatibility loader whose lazy path never reads the whole file."""

    def __init__(self, path: str) -> None:
        self.path = pathlib.Path(path)

    def lazy_load(self) -> Iterator[Document]:
        encoding = _text_encoding(self.path)
        with self.path.open(
            "r",
            encoding=encoding,
            errors="replace",
            newline=None,
        ) as handle:
            page = 0
            while True:
                content = handle.read(TEXT_PAGE_CHARS)
                if not content:
                    return
                yield Document(
                    page_content=content,
                    metadata={"source": str(self.path), "page": page},
                )
                page += 1

    def load(self) -> list[Document]:
        """Retain the upstream loader API for legacy direct callers."""
        return list(self.lazy_load())


def _upstream_loader(module_name: str, class_name: str):
    def build(path: str):
        try:
            module = importlib.import_module(module_name)
            loader_class = getattr(module, class_name)
        except Exception as exc:
            raise RuntimeError(
                f"{class_name} is unavailable; install its optional parser dependencies."
            ) from exc
        return loader_class(path)

    return build


class DocumentLoader:
    supported_file_types = {
        ".pdf": _upstream_loader(
            "langchain_community.document_loaders.pdf",
            "PyPDFLoader",
        ),
        ".docx": _upstream_loader(
            "langchain_community.document_loaders.word_document",
            "UnstructuredWordDocumentLoader",
        ),
        ".doc": _upstream_loader(
            "langchain_community.document_loaders.word_document",
            "UnstructuredWordDocumentLoader",
        ),
        ".txt": _BoundedTextLoader,
        ".md": _BoundedTextLoader,
        ".html": _upstream_loader(
            "langchain_community.document_loaders.html_bs",
            "BSHTMLLoader",
        ),
        ".htm": _upstream_loader(
            "langchain_community.document_loaders.html_bs",
            "BSHTMLLoader",
        ),
        ".epub": _upstream_loader(
            "langchain_community.document_loaders.epub",
            "UnstructuredEPubLoader",
        ),
    }


def iter_document_pages(path: str | pathlib.Path, *, original_extension: str | None = None) -> Iterator[Document]:
    """Yield non-empty pages, preferring an upstream loader's lazy API."""
    source = pathlib.Path(path)
    extension = original_extension if original_extension is not None else source.suffix.lower()
    loader_class = DocumentLoader.supported_file_types.get(extension)
    if loader_class is None:
        raise ValueError(f"Unsupported file type: {extension}")
    loader = loader_class(str(source))
    lazy_load = getattr(loader, "lazy_load", None)
    pages = lazy_load() if callable(lazy_load) else iter(loader.load())
    for page in pages:
        content = getattr(page, "page_content", None)
        if not isinstance(content, str) or not content.strip():
            continue
        clean = content.encode("utf-8", errors="surrogatepass").decode(
            "utf-8", errors="replace"
        )
        yield Document(
            page_content=clean,
            metadata=dict(getattr(page, "metadata", {}) or {}),
        )


def _iter_page_chunks(
    text: str,
    *,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> Iterator[str]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Invalid document chunk bounds")
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + chunk_size)
        chunk = text[start:end]
        if chunk.strip():
            yield chunk
        if end >= length:
            break
        start = end - overlap


def iter_document_chunks(
    path: str | pathlib.Path,
    metadata: dict[str, Any] | None = None,
    *,
    check_cancelled=None,
    original_extension: str | None = None,
) -> Iterator[Document]:
    """Split and yield one bounded chunk at a time without page/chunk lists."""
    base_metadata = dict(metadata or {})
    chunk_index = 0
    for page_index, page in enumerate(iter_document_pages(path,
            **({"original_extension":original_extension} if original_extension is not None else {}))):
        if check_cancelled:
            check_cancelled()
        page_metadata = dict(page.metadata or {})
        page_metadata.update(base_metadata)
        page_metadata.setdefault("page", page_index)
        for content in _iter_page_chunks(page.page_content):
            if check_cancelled:
                check_cancelled()
            chunk_metadata = dict(page_metadata)
            chunk_metadata["chunk_index"] = chunk_index
            yield Document(page_content=content, metadata=chunk_metadata)
            chunk_index += 1


def iter_chunk_batches(
    chunks: Iterable[Document],
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> Iterator[list[Document]]:
    """Yield fixed embedding batches; the reviewed maximum is always 32."""
    bounded_size = min(max(1, int(batch_size)), EMBEDDING_BATCH_SIZE)
    batch: list[Document] = []
    for chunk in chunks:
        batch.append(chunk)
        if len(batch) >= bounded_size:
            yield batch
            batch = []
    if batch:
        yield batch


def get_embedding_model(config: dict[str, Any] | None = None) -> Any:
    """Bind explicit indexing to its captured embedding configuration."""
    with _embedding_lock:
        if config is None:
            ensure_embedding_runtime_available()
            return get_embedding_provider()
        captured = deepcopy(config)
        ensure_embedding_runtime_available(captured)
        return get_embedding_provider(captured)


def get_embedding_model_for_recall(config: dict[str, Any] | None = None) -> Any:
    return get_embedding_provider_for_recall(deepcopy(config)) if config is not None else get_embedding_provider_for_recall()


def get_vector_store():
    """Return the shard/legacy compatibility facade used by document search."""
    global _vector_store
    if _vector_store is None:
        from row_bot.document_index import DocumentVectorStoreFacade

        _vector_store = DocumentVectorStoreFacade(
            index_root=DOCUMENT_INDEX_DIR,
            legacy_root=VECTOR_STORE_DIR,
        )
    return _vector_store


def index_document_job(job: DocumentJob, service: DocumentJobService, *,
                       embedding_config: dict | None = None, embedding=None,
                       validate: Callable[[], None] | None = None,
                       source_path: str | None = None, original_extension: str | None = None) -> None:
    """Build and transactionally publish one durable job's document shards."""
    from row_bot.document_index import build_unpublished_document, publish_document

    def check():
        service.raise_if_cancelled(job.id)
        if validate is not None:
            validate()
    check()
    service.update_progress(job.id, stage="parse", current=0, total=0)
    metadata = {
        "source": job.original_name,
        "original_name": job.original_name,
        "stored_name": job.stored_name,
        "document_id": job.id,
        "content_sha256": job.content_sha256,
    }
    chunks = iter_document_chunks(
        source_path if source_path is not None else job.staged_path,
        metadata,
        check_cancelled=check,
        **({"original_extension":original_extension} if original_extension is not None else {}),
    )
    work_document_dir = service.work_root / job.id / "index" / "document"
    embedding_config = deepcopy(embedding_config) if embedding_config is not None else get_embedding_config()
    manifest = build_unpublished_document(
        document_id=job.id,
        original_name=job.original_name,
        stored_name=job.stored_name,
        content_sha256=job.content_sha256,
        chunks=chunks,
        work_document_dir=work_document_dir,
        embedding=embedding if embedding is not None else get_embedding_model(embedding_config),
        check_cancelled=check,
        progress=lambda current: service.update_progress(
            job.id,
            stage="embed",
            current=current,
            total=0,
        ),
        embedding_metadata=active_embedding_metadata(embedding_config),
    )
    check()
    service.update_progress(
        job.id,
        stage="index_commit",
        current=int(manifest["chunk_count"]),
        total=int(manifest["chunk_count"]),
    )
    publish_document(
        work_document_dir,
        manifest,
        index_root=DOCUMENT_INDEX_DIR,
        **({"validate": check} if validate is not None else {}),
    )


def _copy_existing_file_into_job(
    service: DocumentJobService,
    job: DocumentJob,
    source: pathlib.Path,
) -> DocumentJob:
    final = pathlib.Path(job.staged_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    temp = final.with_name(f".{final.name}.copying")
    digest = hashlib.sha256()
    size = 0
    try:
        with source.open("rb") as reader, temp.open("xb") as writer:
            while True:
                data = reader.read(1024 * 1024)
                if not data:
                    break
                writer.write(data)
                digest.update(data)
                size += len(data)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temp, final)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()
        raise
    return service.complete_staging(job.id, digest.hexdigest(), size, final)


def load_and_vectorize_document(
    file_path: str,
    skip_if_processed: bool = True,
    display_name: str | None = None,
) -> None:
    """Bounded synchronous compatibility entry point for older callers."""
    record_name = display_name or file_path
    if skip_if_processed and is_file_processed(record_name):
        logger.info("Skipping already processed file: %s", record_name)
        return
    service = DocumentJobService(DATA_DIR)
    batch_id = service.create_batch()
    job = service.create_staging_job(batch_id, 0, record_name)
    try:
        job = _copy_existing_file_into_job(service, job, pathlib.Path(file_path))
        service.finish_batch_staging(batch_id)
        if job.status == "skipped_duplicate":
            return
        service.transition_job(job.id, "indexing", stage="parse")
        index_document_job(service.get_job(job.id), service)
        with service._write_lock:
            service.mark_searchable(job.id)
            if service.latest_removal(job.id) is not None:
                raise RuntimeError("Document removal was requested before marker publication")
            save_processed_file(record_name)
    except Exception as exc:
        current = service.get_job(job.id)
        if current.status not in {"failed", "cancelled", "skipped_duplicate"}:
            service.mark_failed(job.id, "compatibility_index_failed", str(exc), stage=current.stage)
        raise


def load_document_text(file_path: str) -> tuple[str, str]:
    """Legacy compatibility helper; the durable extraction path does not use it."""
    parts = (page.page_content for page in iter_document_pages(file_path))
    full_text = "\n\n".join(parts)
    if not full_text:
        raise ValueError(f"No text content found in: {file_path}")
    return full_text, pathlib.Path(file_path).stem


def document_vector_status() -> dict[str, Any]:
    from row_bot.document_index import index_health

    health = index_health(
        index_root=DOCUMENT_INDEX_DIR,
        legacy_root=VECTOR_STORE_DIR,
    )
    health.update(
        {
            "stored": read_index_metadata(VECTOR_STORE_DIR),
            "active_label": describe_active_embedding(),
        }
    )
    return health


def release_document_embedding_resources(
    reason: str = "document work complete",
) -> None:
    global _vector_store
    if _vector_store is not None:
        with contextlib.suppress(Exception):
            _vector_store.clear_cache()
    if reason != "embedding settings changed" and not get_embedding_config().get(
        "auto_unload", False
    ):
        return
    _vector_store = None
    release_embedding_resources(reason)


def _recoverable_retire(path: pathlib.Path, label: str) -> pathlib.Path | None:
    if not path.exists():
        return None
    retired = path.with_name(
        f"{path.name}.{label}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    )
    os.replace(path, retired)
    return retired


_removal_lock = threading.RLock()


def _removal_result(removal_id: str, document_id: str) -> dict:
    return {"removal_id": removal_id, "document_id": document_id,
            "status": "pending", "removed": False, "derived_entities_removed": 0,
            "stages": {}, "retained_copies": [], "failures": []}


def _retained(result: dict, kind: str, path: pathlib.Path) -> None:
    item = {"kind": kind, "path": str(path)}
    if item not in result["retained_copies"]:
        result["retained_copies"].append(item)


def _contained_file(root: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    root, path = root.absolute(), path.absolute()
    if root == path or root not in path.parents:
        raise ValueError("Document copy is outside its owner")
    current = root
    if current.is_symlink() or current.is_junction():
        raise ValueError("Linked document copy owner")
    for part in path.relative_to(root).parts:
        if part in {".", ".."}:
            raise ValueError("Invalid document copy path")
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ValueError("Linked document copy is preserved for review")
    return path


def _strict_names(path: pathlib.Path) -> set[str]:
    if not path.exists():
        return set()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("Invalid document source markers; preserve for recovery")
    return set(value)


def _write_names(path: pathlib.Path, names: set[str], *, validate: Callable[[], None] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(sorted(names), handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    if validate is not None:
        validate()
    os.replace(temporary, path)


def _legacy_index_revision(path: pathlib.Path) -> dict | None:
    """Capture the read-only legacy directory without interpreting its contents."""
    if not path.exists():
        return None
    if not path.is_dir() or path.is_symlink() or path.is_junction():
        raise ValueError("Legacy index owner is not a plain directory")
    identity = path.stat()
    files = {}
    for item in path.rglob("*"):
        _contained_file(path, item)
        if len(files) >= 10_000:
            raise ValueError("Legacy index contains too many entries for safe removal")
        relative = item.relative_to(path).as_posix()
        if item.is_file():
            files[relative] = _hash_file(item)
        elif item.is_dir():
            files[relative + "/"] = "directory"
        else:
            raise ValueError("Legacy index contains an unsupported file type")
    return {"device": identity.st_dev, "inode": identity.st_ino, "files": files}


def _removal_snapshot(document_id: str, service: DocumentJobService, *, retire_raw: bool) -> dict:
    from dataclasses import asdict
    from row_bot import knowledge_graph as kg, wiki_vault
    from row_bot.document_index import read_corpus_manifest

    records = {str(record["document_id"]): record for record in service.list_document_records()}
    registered = document_id in records
    record = records.get(document_id)
    entries = read_corpus_manifest(DOCUMENT_INDEX_DIR)["documents"]
    if any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("Invalid corpus document entry")
    indexed = any(str(entry.get("document_id")) == document_id for entry in entries)
    active = False
    if record is None:
        try:
            job = service.get_job(document_id)
            active = job.status not in TERMINAL_JOB_STATUSES
            if indexed or active:
                record = dict(asdict(job), document_id=job.id)
        except KeyError:
            pass
    index_entry = next((entry for entry in entries if str(entry.get("document_id")) == document_id), None)
    legacy = record is None and not indexed
    source = f"document:{document_id}"
    entities = [dict({key: entity.get(key, "") for key in ("id", "entity_type", "subject", "description")},
                     wiki_revision=wiki_vault._source_revision(entity))
                for entity in kg.iter_entities_snapshot() if entity.get("source") == source]
    names = _strict_names(PROCESSED_FILES_PATH)
    return {"record": record, "legacy": legacy, "source": source, "entities": entities,
            "index_entry": index_entry, "retire_raw": retire_raw,
            "known": bool(registered or indexed or active or document_id in names or entities)}


def _retire_raw_copy(service: DocumentJobService, snapshot: dict, result: dict, *,
                     validate: Callable[[], None] | None = None) -> None:
    from row_bot import wiki_vault

    record = snapshot.get("record")
    if not record:
        return
    stored_name = str(record["stored_name"])
    if pathlib.Path(stored_name).name != stored_name or "/" in stored_name or "\\" in stored_name:
        raise ValueError("Invalid document raw-copy name")
    raw_root = wiki_vault.get_vault_path() / "raw"
    raw = _contained_file(raw_root, raw_root / stored_name)
    if not snapshot["retire_raw"]:
        if raw.exists():
            _retained(result, "raw_copy", raw)
        return
    # Keep raw retirement on its own filesystem (vaults may live on another drive).
    retained = raw_root / ".row-bot-retired" / result["removal_id"] / stored_name
    _contained_file(raw_root, retained)
    expected = str(record.get("content_sha256") or "")
    pending = [path for path in retained.parent.iterdir()
               if path.name.startswith(f".{stored_name}.") and path.name.endswith(".retiring")] if retained.parent.exists() else []
    if not raw.exists() and not retained.exists() and len(pending) == 1:
        _contained_file(raw_root, pending[0])
        if expected and _hash_file(pending[0]) != expected:
            _retained(result, "raw_copy", pending[0])
            raise ValueError("Interrupted raw copy differs from its saved hash; preserve for review")
        if validate is not None:
            validate()
        os.link(pending[0], retained)
    if retained.is_file():
        _retained(result, "raw_copy", retained)
        if raw.exists() or (expected and _hash_file(retained) != expected):
            raise ValueError("Raw copy has concurrent or changed versions; preserve for review")
        return
    if not raw.exists():
        return
    if expected and _hash_file(raw) != expected:
        _retained(result, "externally_edited_raw", raw)
        raise ValueError("Raw copy was externally edited; it is preserved")
    retained.parent.mkdir(parents=True, exist_ok=True)
    _contained_file(raw_root, raw)
    _contained_file(raw_root, retained)
    intermediate = retained.with_name(f".{stored_name}.{uuid.uuid4().hex}.retiring")
    if validate is not None:
        validate()
    os.rename(raw, intermediate)
    _retained(result, "raw_copy", intermediate)
    if validate is not None:
        validate()
    os.link(intermediate, retained)
    _retained(result, "raw_copy", retained)
    if expected and _hash_file(retained) != expected:
        raise ValueError("Raw copy changed during retirement; its copy is preserved")


def remove_document_details(document_id: str, *, removal_id: str | None = None,
                            validate: Callable[[], None] | None = None,
                            validate_snapshot: Callable[[str, dict], None] | None = None) -> dict:
    """Remove one document through a durable, retryable owner operation.

    Returned retained-copy paths are private local evidence. API adapters must
    project safe labels, not expose these paths to another client. This function
    relies on the calling surface's existing deletion authorization.
    """
    return _remove_document_details(document_id, removal_id=removal_id, retire_raw=True,
                                     validate=validate, validate_snapshot=validate_snapshot)


def _remove_document_details(document_id: str, *, removal_id: str | None, retire_raw: bool,
                             validate: Callable[[], None] | None = None,
                             validate_snapshot: Callable[[str, dict], None] | None = None) -> dict:
    global _vector_store
    from row_bot import knowledge_graph as kg
    from row_bot.document_index import remove_document_shard

    if not isinstance(document_id, str) or not document_id or len(document_id) > 4096:
        raise ValueError("Invalid document removal target")
    authority_error = None
    def checked():
        nonlocal authority_error
        if validate is not None:
            try:
                validate()
            except Exception as exc:
                authority_error = exc
                raise
    checks = {"validate": checked} if validate is not None else {}
    with _removal_lock:
        checked()
        service = DocumentJobService(DATA_DIR)
        intent = service.get_removal(removal_id) if removal_id else service.latest_removal(document_id)
        if intent and intent["target"] != document_id:
            raise ValueError("Removal operation belongs to a different document")
        if intent and intent["result"]["status"] == "complete" and removal_id is None:
            current = _removal_snapshot(document_id, service, retire_raw=retire_raw)
            if current["known"]:
                intent = None
        if intent is None:
            operation_id = removal_id or uuid.uuid4().hex
            snapshot = _removal_snapshot(document_id, service, retire_raw=retire_raw)
            if validate_snapshot is not None:
                validate_snapshot(document_id, snapshot)
            checked()
            intent = service.begin_removal(document_id, snapshot,
                                           _removal_result(operation_id, document_id), removal_id=operation_id)
        snapshot, result = intent["snapshot"], intent["result"]
        if result["status"] == "complete":
            return result
        result["failures"] = []
        stage = "worker"
        try:
            checked()
            try:
                job = service.get_job(document_id)
            except KeyError:
                job = None
            if job is not None and job.status not in TERMINAL_JOB_STATUSES:
                job = service.cancel_job(document_id, **checks)
                if job.status in {"indexing", "extracting"}:
                    result["status"] = "pending"
                    result["stages"]["worker"] = "pending"
                    result["failures"] = [{"stage": "worker", "code": "cancellation_pending",
                                           "message": "Waiting for active document work to acknowledge cancellation."}]
                    service.save_removal_result(result["removal_id"], result)
                    return result
            result["stages"]["worker"] = "complete"
            if not snapshot.get("entities_captured"):
                # Extraction can finish a write before acknowledging cancellation.
                # Freeze that complete work set durably before SQL/projection cleanup.
                from row_bot import wiki_vault
                stage = "derived_snapshot"
                if snapshot.get("index_entry") is None:
                    # A worker may publish its first generation after its last
                    # cancellation check. Its acknowledgement closes that gap.
                    current = _removal_snapshot(document_id, service, retire_raw=retire_raw)
                    if current.get("index_entry") is not None:
                        snapshot["index_entry"] = current["index_entry"]
                        snapshot["legacy"] = False
                        snapshot["known"] = True
                        if snapshot.get("record") is None:
                            snapshot["record"] = current.get("record")
                entities = {str(entity["id"]): entity for entity in snapshot["entities"]}
                for entity in kg.iter_entities_snapshot():
                    if entity.get("source") == snapshot["source"]:
                        entities[str(entity["id"])] = dict(
                            {key: entity.get(key, "") for key in ("id", "entity_type", "subject", "description")},
                            wiki_revision=wiki_vault._source_revision(entity),
                        )
                snapshot["entities"] = list(entities.values())
                snapshot["entities_captured"] = True
                snapshot["known"] = bool(snapshot["known"] or entities)
                service.save_removal_snapshot(result["removal_id"], snapshot)
            if snapshot.get("record"):
                for current in service.list_document_records():
                    if str(current["document_id"]) == document_id and any(
                        current[key] != snapshot["record"][key] for key in ("stored_name", "content_sha256")
                    ):
                        raise ValueError("Document source identity changed since removal was requested")
            if not snapshot["known"]:
                result["status"] = "complete"
                service.save_removal_result(result["removal_id"], result)
                return result

            for stage in ("index", "source", "raw_copy", "derived_knowledge", "markers", "record"):
                if result["stages"].get(stage) == "complete":
                    continue
                checked()
                if stage == "index":
                    if snapshot["legacy"]:
                        tombstones = DOCUMENT_INDEX_DIR / "legacy_tombstones.json"
                        values = _strict_names(tombstones)
                        values.add(document_id)
                        _write_names(tombstones, values, **checks)
                        if VECTOR_STORE_DIR.exists():
                            _retained(result, "legacy_index", VECTOR_STORE_DIR)
                    else:
                        remove_document_shard(document_id, index_root=DOCUMENT_INDEX_DIR,
                                              retirement_id=result["removal_id"],
                                              expected_entry=snapshot.get("index_entry"), **checks)
                        retired = DOCUMENT_INDEX_DIR / "retired" / f"{document_id}-{result['removal_id']}"
                        if retired.exists():
                            _retained(result, "document_index", retired)
                    _vector_store = None
                elif stage == "source" and snapshot.get("record"):
                    retired = service.retire_document_source(document_id, record=snapshot["record"],
                                                              retirement_id=result["removal_id"], **checks)
                    if retired:
                        # A failed earlier stage may have recorded the live
                        # source as a recovery reference. Canonical retirement
                        # has now verified its bytes at the new location.
                        previous_source = pathlib.Path(str(snapshot["record"]["staged_path"]))
                        if previous_source != retired and not previous_source.exists():
                            result["retained_copies"] = [item for item in result["retained_copies"]
                                if item != {"kind": "document_source", "path": str(previous_source)}]
                        _retained(result, "document_source", retired)
                elif stage == "raw_copy":
                    _retire_raw_copy(service, snapshot, result, **checks)
                elif stage == "derived_knowledge":
                    kg.delete_entities_by_source(snapshot["source"], retry_entities=snapshot["entities"], **checks)
                    result["derived_entities_removed"] = sum(
                        kg.get_entity(str(entity["id"])) is None for entity in snapshot["entities"])
                    # User-edited/unowned markdown remains intentionally recoverable.
                    from row_bot import wiki_vault
                    wiki = wiki_vault.get_vault_path() / "wiki"
                    if wiki.exists():
                        _retained(result, "wiki_external_and_recovery_copies", wiki)
                elif stage == "markers":
                    with _processed_files_lock:
                        checked()
                        names = _strict_names(PROCESSED_FILES_PATH)
                        names.discard(document_id)
                        record = snapshot.get("record")
                        if record and not VECTOR_STORE_DIR.exists():
                            others = service.list_document_records()
                            if not any(str(row["document_id"]) != document_id and row["original_name"] == record["original_name"]
                                       for row in others):
                                names.discard(str(record["original_name"]))
                        _write_names(PROCESSED_FILES_PATH, names, **checks)
                        # Marker writers merge durable records. Retire this row
                        # before releasing their lock so they cannot restore it.
                        service.remove_document_record(document_id, **checks)
                elif stage == "record":
                    service.remove_document_record(document_id, **checks)
                result["stages"][stage] = "complete"
                service.save_removal_result(result["removal_id"], result)
            result["removed"] = True
            result["status"] = "complete"
            service.save_removal_result(result["removal_id"], result)
        except Exception as exc:
            result["status"] = "partial"
            result["failures"] = [{"stage": stage, "code": f"{stage}_failed", "message": str(exc)}]
            record = snapshot.get("record")
            if record:
                operation_copies = service.root / "retired" / result["removal_id"]
                if operation_copies.is_dir():
                    _retained(result, "interrupted_source_copies", operation_copies)
                for path in (pathlib.Path(str(record["staged_path"])),
                             service.root / "retired" / result["removal_id"] / str(record["stored_name"])):
                    try:
                        service._validate_source_path(path)
                        if path.is_file():
                            _retained(result, "document_source", path)
                    except (OSError, ValueError, RuntimeError):
                        pass
            try:
                service.save_removal_result(result["removal_id"], result)
            except Exception:
                logger.warning("Document removal result could not be saved; original intent remains", exc_info=True)
            if authority_error is not None:
                raise authority_error
        return result


def clear_documents_details(*, removal_id: str | None = None,
                            validate: Callable[[], None] | None = None,
                            validate_snapshot: Callable[[str, dict], None] | None = None) -> dict:
    """Clear the captured document set, retaining raw copies as the legacy clear did."""
    global _vector_store
    from row_bot import knowledge_graph as kg
    from row_bot.document_index import read_corpus_manifest

    authority_error = None
    def checked():
        nonlocal authority_error
        if validate is not None:
            try:
                validate()
            except Exception as exc:
                authority_error = exc
                raise
    checks = {"validate": checked} if validate is not None else {}
    with _removal_lock:
        checked()
        service = DocumentJobService(DATA_DIR)
        intent = service.get_removal(removal_id) if removal_id else service.latest_removal("*")
        if intent and removal_id is None and intent["result"]["status"] == "complete":
            intent = None
        if intent and intent["target"] != "*":
            raise ValueError("Removal operation is not a bulk document clear")
        if intent is None:
            identifiers = {str(row["document_id"]) for row in service.list_document_records()}
            identifiers.update(job.id for job in service.list_jobs() if job.status not in TERMINAL_JOB_STATUSES)
            identifiers.update(_strict_names(PROCESSED_FILES_PATH))
            for entry in read_corpus_manifest(DOCUMENT_INDEX_DIR)["documents"]:
                if not isinstance(entry, dict):
                    raise ValueError("Invalid corpus document entry")
                identifiers.add(str(entry["document_id"]))
            identifiers.update(str(entity["source"])[len("document:"):]
                               for entity in kg.iter_entities_snapshot()
                               if str(entity.get("source", "")).startswith("document:"))
            operation_id = removal_id or uuid.uuid4().hex
            snapshot = {"documents": sorted(identifiers),
                        "legacy_revision": _legacy_index_revision(VECTOR_STORE_DIR),
                        "batches": [batch.id for batch in service.list_batches(include_finished=False)]}
            if validate_snapshot is not None:
                validate_snapshot("*", snapshot)
            checked()
            intent = service.begin_removal("*", snapshot,
                                           _removal_result(operation_id, "*"), removal_id=operation_id)
        result = intent["result"]
        if result["status"] == "complete":
            return result
        stage = "bulk_removal"
        try:
            for batch_id in intent["snapshot"].get("batches", []):
                try:
                    checked()
                    service.cancel_batch(batch_id, **checks)
                except KeyError:
                    pass
            result["failures"] = []
            result["derived_entities_removed"] = 0
            pending = False
            for identifier in intent["snapshot"]["documents"]:
                child_id = uuid.uuid5(uuid.UUID(result["removal_id"]), identifier).hex
                checked()
                child = _remove_document_details(identifier, removal_id=child_id, retire_raw=False,
                                                   validate=checked if validate is not None else None,
                                                   validate_snapshot=validate_snapshot)
                result["stages"][identifier] = child["status"]
                result["derived_entities_removed"] += child["derived_entities_removed"]
                for retained in child["retained_copies"]:
                    if retained not in result["retained_copies"]:
                        result["retained_copies"].append(retained)
                result["failures"].extend(child["failures"])
                pending = pending or child["status"] == "pending"
                service.save_removal_result(result["removal_id"], result)
            if result["failures"]:
                result["status"] = "pending" if pending else "partial"
            else:
                try:
                    checked()
                    retained = VECTOR_STORE_DIR.with_name(f"{VECTOR_STORE_DIR.name}.retired-{result['removal_id']}")
                    expected_legacy = intent["snapshot"].get("legacy_revision")
                    if expected_legacy is None:
                        if VECTOR_STORE_DIR.exists():
                            _retained(result, "legacy_index_created_after_request", VECTOR_STORE_DIR)
                    elif VECTOR_STORE_DIR.exists():
                        if _legacy_index_revision(VECTOR_STORE_DIR) != expected_legacy:
                            raise ValueError("Legacy index changed since this clear was requested")
                        if retained.exists():
                            raise ValueError("Both live and retired legacy indexes exist; preserve for review")
                        checked()
                        os.rename(VECTOR_STORE_DIR, retained)
                        if _legacy_index_revision(retained) != expected_legacy:
                            if not VECTOR_STORE_DIR.exists():
                                os.rename(retained, VECTOR_STORE_DIR)
                            raise ValueError("Legacy index changed during retirement; preserve for review")
                    elif not retained.exists() or _legacy_index_revision(retained) != expected_legacy:
                        raise ValueError("Captured legacy index is missing or changed; preserve for review")
                    if expected_legacy is not None and retained.exists():
                        result["retained_copies"] = [item for item in result["retained_copies"]
                                                     if item != {"kind": "legacy_index", "path": str(VECTOR_STORE_DIR)}]
                        _retained(result, "legacy_index", retained)
                    result["status"] = "complete"
                    result["removed"] = bool(intent["snapshot"]["documents"] or expected_legacy is not None)
                    _vector_store = None
                except Exception as exc:
                    if authority_error is not None:
                        raise authority_error
                    if retained.exists() and not VECTOR_STORE_DIR.exists():
                        result["retained_copies"] = [item for item in result["retained_copies"]
                                                     if item != {"kind": "legacy_index", "path": str(VECTOR_STORE_DIR)}]
                        _retained(result, "legacy_index", retained)
                    result["status"] = "partial"
                    result["failures"] = [{"stage": "legacy_index", "code": "legacy_index_failed", "message": str(exc)}]
            service.save_removal_result(result["removal_id"], result)
        except Exception as exc:
            result["status"] = "partial"
            result["failures"].append({"stage": stage, "code": "bulk_removal_failed", "message": str(exc)})
            try:
                service.save_removal_result(result["removal_id"], result)
            except Exception:
                logger.warning("Bulk removal result could not be saved; its intent remains", exc_info=True)
            if authority_error is not None:
                raise authority_error

        return result


def reset_vector_store() -> None:
    """Compatibility clear entry point; partial outcomes cannot report success."""
    result = clear_documents_details()
    if result["status"] != "complete":
        raise RuntimeError(f"Document clear is {result['status']}; retry removal {result['removal_id']}")


def remove_document(document_id: str) -> bool:
    """Compatibility removal entry point for existing callers."""
    result = remove_document_details(document_id)
    if result["status"] != "complete":
        raise RuntimeError(f"Document removal is {result['status']}; retry removal {result['removal_id']}")
    return bool(result["removed"])


def _hash_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            data = handle.read(1024 * 1024)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def rebuild_vector_store_from_vault() -> int:
    """Boundedly rebuild all vault raw copies into an atomic shard directory."""
    from row_bot.document_index import (
        build_unpublished_document,
        initialize_index,
        publish_document,
    )

    try:
        import row_bot.wiki_vault as wiki_vault

        raw_dir = wiki_vault.get_vault_path() / "raw"
    except Exception:
        raw_dir = DATA_DIR / "vault" / "raw"
    if not raw_dir.exists():
        raise FileNotFoundError("No vault/raw document copies were found to rebuild from.")

    rebuild_id = uuid.uuid4().hex
    temp_root = DOCUMENT_INDEX_DIR.with_name(f"{DOCUMENT_INDEX_DIR.name}.rebuild-{rebuild_id}")
    temp_work = DATA_DIR / "document_ingestion" / "work" / f"rebuild-{rebuild_id}"
    initialize_index(temp_root)
    indexed = 0
    try:
        for path in sorted(raw_dir.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file() or path.suffix.lower() not in DocumentLoader.supported_file_types:
                continue
            content_hash = _hash_file(path)
            document_id = uuid.uuid5(uuid.NAMESPACE_URL, f"row-bot:{path.name}:{content_hash}").hex
            original_name = path.name
            metadata_path = raw_dir / ".metadata" / f"{path.name}.json"
            metadata = {}
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
                pass
            original_name = str(metadata.get("original_name") or original_name)
            chunks = iter_document_chunks(
                path,
                {
                    "source": original_name,
                    "original_name": original_name,
                    "stored_name": path.name,
                    "document_id": document_id,
                    "content_sha256": content_hash,
                },
            )
            work_document_dir = temp_work / document_id
            try:
                manifest = build_unpublished_document(
                    document_id=document_id,
                    original_name=original_name,
                    stored_name=path.name,
                    content_sha256=content_hash,
                    chunks=chunks,
                    work_document_dir=work_document_dir,
                    embedding=get_embedding_model(),
                    embedding_metadata=active_embedding_metadata(),
                )
            except ValueError:
                logger.warning("No valid text content found in vault copy: %s", path)
                continue
            publish_document(work_document_dir, manifest, index_root=temp_root)
            indexed += 1
        if indexed == 0:
            raise ValueError("No valid text content was found in vault/raw documents.")

        backup = None
        if DOCUMENT_INDEX_DIR.exists():
            backup = DOCUMENT_INDEX_DIR.with_name(
                f"{DOCUMENT_INDEX_DIR.name}.rebuild-backup-{rebuild_id}"
            )
            os.replace(DOCUMENT_INDEX_DIR, backup)
        try:
            os.replace(temp_root, DOCUMENT_INDEX_DIR)
        except Exception:
            if backup is not None and backup.exists() and not DOCUMENT_INDEX_DIR.exists():
                os.replace(backup, DOCUMENT_INDEX_DIR)
            raise
        _recoverable_retire(VECTOR_STORE_DIR, "legacy-backup")
        clear_processed_files()
        global _vector_store
        _vector_store = None
        return indexed
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root)
        if temp_work.exists():
            shutil.rmtree(temp_work)
        release_document_embedding_resources("document vector rebuild")
