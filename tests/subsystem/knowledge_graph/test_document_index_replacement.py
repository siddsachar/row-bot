from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from langchain_core.documents import Document

from tests.subsystem.knowledge_graph.test_document_index_shards import FakeEmbeddings


@pytest.fixture
def index(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    import row_bot.document_index as document_index

    module = importlib.reload(document_index)
    metadata = {"provider": "fake", "model": "fake", "dimension": 3}
    monkeypatch.setattr(module, "active_embedding_metadata", lambda: metadata)
    root = tmp_path / "index"

    def build(label):
        work = tmp_path / "work" / label
        manifest = module.build_unpublished_document(
            document_id="same-id",
            original_name="notes.txt",
            stored_name="notes.txt",
            content_sha256=label,
            chunks=[Document(page_content=f"{label}-{i}") for i in range(3)],
            work_document_dir=work,
            embedding=FakeEmbeddings(),
            embedding_metadata=metadata,
            segment_chunks=1,
        )
        return work, manifest

    def search():
        facade = module.DocumentVectorStoreFacade(
            index_root=root,
            legacy_root=tmp_path / "no-legacy",
            embedding_factory=FakeEmbeddings,
        )
        return sorted(doc.page_content for doc in facade.similarity_search("query", k=9))

    work, manifest = build("old")
    module.publish_document(work, manifest, index_root=root)
    return module, root, build, search


def test_same_id_failed_manifest_publication_preserves_old_generation(index):
    module, root, build, search = index
    before = (root / "manifest.json").read_bytes()
    work, manifest = build("new")

    def fail(_source, _destination):
        assert search() == ["old-0", "old-1", "old-2"]
        raise OSError("manifest commit failure")

    with pytest.raises(OSError, match="manifest commit failure"):
        module.publish_document(work, manifest, index_root=root, replace=fail)
    assert (root / "manifest.json").read_bytes() == before
    assert search() == ["old-0", "old-1", "old-2"]
    assert module.index_health(index_root=root, legacy_root=root / "absent")["readable_documents"] == 1


def test_reader_pinned_before_commit_finishes_old_generation(index, monkeypatch):
    module, root, build, search = index
    work, manifest = build("new")
    snapshot_ready = threading.Event()
    resume_reader = threading.Event()
    read_manifest = module.read_corpus_manifest

    def paused_snapshot(index_root):
        snapshot = read_manifest(index_root)
        if threading.current_thread().name.startswith("old-reader"):
            snapshot_ready.set()
            assert resume_reader.wait(10), "reader was not released"
        return snapshot

    monkeypatch.setattr(module, "read_corpus_manifest", paused_snapshot)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-reader") as pool:
        future = pool.submit(search)
        try:
            assert snapshot_ready.wait(10), "reader did not take a corpus snapshot"
            module.publish_document(work, manifest, index_root=root)
            assert search() == ["new-0", "new-1", "new-2"]
        finally:
            resume_reader.set()
        assert future.result(timeout=10) == ["old-0", "old-1", "old-2"]


def test_same_id_directory_rename_failure_preserves_old_generation(index, monkeypatch):
    module, root, build, search = index
    work, manifest = build("new")
    replace = os.replace

    def fail(source, destination):
        if source == work:
            raise OSError("directory rename failure")
        replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError, match="directory rename failure"):
        module.publish_document(work, manifest, index_root=root)
    assert search() == ["old-0", "old-1", "old-2"]
    assert work.exists()


@pytest.mark.parametrize("commit", [False, True])
def test_restart_observes_only_committed_complete_generation(index, commit):
    module, root, build, search = index
    work, manifest = build("new")

    def crash(source, destination):
        if commit:
            os.replace(source, destination)
        raise SystemExit("simulated process interruption")

    with pytest.raises(SystemExit):
        module.publish_document(work, manifest, index_root=root, replace=crash)
    result = subprocess.run(
        [sys.executable, "-c", """
import json
import pathlib
import sys
from tests.subsystem.knowledge_graph.test_document_index_shards import FakeEmbeddings
from row_bot import document_index
document_index.active_embedding_metadata = lambda: {
    'provider': 'fake', 'model': 'fake', 'dimension': 3,
}
root = pathlib.Path(sys.argv[1])
facade = document_index.DocumentVectorStoreFacade(
    index_root=root, legacy_root=root / 'absent', embedding_factory=FakeEmbeddings,
)
print(json.dumps(sorted(doc.page_content for doc in facade.similarity_search('query', k=9))))
""", str(root)],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert json.loads(result.stdout) == [f"{'new' if commit else 'old'}-{i}" for i in range(3)]


def test_replacing_pre_generation_layout_keeps_snapshot_readable(index):
    module, root, build, search = index
    # Construct the supported older on-disk layout, independently of the writer.
    old_work, old_manifest = build("legacy")
    legacy_dir = root / "documents" / "legacy-id"
    os.replace(old_work, legacy_dir)
    corpus = module.read_corpus_manifest(root)
    corpus["documents"] = [{**old_manifest, "document_id": "legacy-id"}]
    module._atomic_write_json(root / "manifest.json", corpus)
    assert search() == ["legacy-0", "legacy-1", "legacy-2"]
    work, manifest = build("new")
    manifest["document_id"] = "legacy-id"

    def commit(source, destination):
        assert search() == ["legacy-0", "legacy-1", "legacy-2"]
        os.replace(source, destination)

    module.publish_document(work, manifest, index_root=root, replace=commit)
    assert search() == ["new-0", "new-1", "new-2"]
    assert (legacy_dir / "manifest.json").exists()


@pytest.mark.parametrize("generation", ["../outside", "..", "C:\\outside", "/outside", 12, ""])
def test_invalid_generation_cannot_escape_managed_document_directory(index, generation):
    module, root, _build, search = index
    corpus = module.read_corpus_manifest(root)
    corpus["documents"][0]["generation"] = generation
    module._atomic_write_json(root / "manifest.json", corpus)
    assert search() == []
    health = module.index_health(index_root=root, legacy_root=root / "absent")
    assert health["partial_documents"] == 1
    assert health["readable_documents"] == 0


def test_failed_publish_retry_and_delete_retire_all_same_id_generations(index):
    module, root, build, search = index
    work, manifest = build("failed")

    def fail(_source, _destination):
        raise OSError("commit failed")

    with pytest.raises(OSError, match="commit failed"):
        module.publish_document(work, manifest, index_root=root, replace=fail)
    work, manifest = build("retry")
    module.publish_document(work, manifest, index_root=root)
    assert search() == ["retry-0", "retry-1", "retry-2"]
    assert module.remove_document_shard("same-id", index_root=root)
    assert search() == []
    assert not (root / "documents" / "same-id").exists()
    retired = list((root / "retired").iterdir())
    assert len(retired) == 1
    assert len(list(retired[0].glob("generation-*"))) == 3
