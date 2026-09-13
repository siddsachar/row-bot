from __future__ import annotations

import importlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem


class CountingEmbedding:
    def __init__(self):
        self.batches = []
        self.queries = []
        self.failure = None

    def embed_documents(self, texts):
        self.batches.append(list(texts))
        if self.failure:
            raise self.failure
        return [[float(len(text)), 1.0, 2.0] for text in texts]

    def embed_query(self, text):
        self.queries.append(text)
        return [float(len(text)), 1.0, 2.0]


@pytest.fixture
def projection_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    from row_bot import knowledge_graph, embedding_config, wiki_vault

    kg = importlib.reload(knowledge_graph)
    embedding = CountingEmbedding()
    fingerprint = {"version": 1, "provider": "fake", "model": "review-v1", "dimension": 3, "normalize": True}
    config = {"batch_size": 32}
    monkeypatch.setattr(embedding_config, "get_embedding_config", lambda: config)
    monkeypatch.setattr(embedding_config, "active_embedding_metadata", lambda *_a, **_kw: dict(fingerprint))
    monkeypatch.setattr(kg, "_get_embedding_model", lambda **_kw: embedding)
    monkeypatch.setattr(kg, "_skip_reindex", True)
    monkeypatch.setattr(wiki_vault, "is_enabled", lambda: False)
    return kg, embedding, fingerprint, config


def add(kg, subject="Synthetic fact", source="test"):
    return kg.save_entity("fact", subject, "Synthetic deterministic knowledge body.", source=source)


def test_source_commit_survives_provider_failure_and_explicit_repair(projection_stack):
    kg, embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    embedding.failure = RuntimeError("synthetic model failure")
    with pytest.raises(RuntimeError):
        kg.rebuild_index()
    assert kg.get_entity(entity["id"])["description"] == entity["description"]
    assert not kg.memory_vector_status()["ready"]
    with sqlite3.connect(kg.DB_PATH) as conn:
        assert conn.execute("SELECT semantic_pending FROM knowledge_projection_work WHERE entity_id=?", (entity["id"],)).fetchone()[0] == 1
    embedding.failure = None
    kg.rebuild_index()
    assert kg.memory_vector_status()["ready"]


def test_generation_write_failure_preserves_selected_bytes(projection_stack, monkeypatch):
    import faiss

    kg, _embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    kg.rebuild_index()
    before = kg._projection_state()
    selected = kg._VECTOR_DIR / "generations" / before["generation"]
    original = {file.name: file.read_bytes() for file in selected.iterdir()}
    kg.update_entity(entity["id"], "Changed synthetic source remains saved.")
    writer = faiss.write_index

    def fail_after_write(index, path):
        writer(index, path)
        raise OSError("synthetic publication fault")

    monkeypatch.setattr(faiss, "write_index", fail_after_write)
    with pytest.raises(OSError):
        kg.rebuild_index()
    assert kg._projection_state()["generation"] == before["generation"]
    assert {file.name: file.read_bytes() for file in selected.iterdir()} == original
    assert not kg.memory_vector_status()["ready"]


def test_source_edit_during_embedding_cannot_publish_as_current(projection_stack, monkeypatch):
    kg, embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    original = embedding.embed_documents

    def mutate(texts):
        result = original(texts)
        kg.update_entity(entity["id"], "Newer revision must remain pending.")
        return result

    monkeypatch.setattr(embedding, "embed_documents", mutate)
    with pytest.raises(kg.KnowledgeProjectionIncomplete):
        kg.rebuild_index()
    assert kg._projection_state()["generation"] is None
    assert kg.get_entity(entity["id"])["description"] == "Newer revision must remain pending."


def test_direct_sql_entity_and_relation_writes_invalidate_process_graph(projection_stack):
    kg, _embedding, _fingerprint, _config = projection_stack
    first, second = add(kg, "First"), add(kg, "Second")
    kg._ensure_graph()
    with sqlite3.connect(kg.DB_PATH) as conn:
        conn.execute("UPDATE entities SET description=? WHERE id=?", ("Changed outside graph wrapper", first["id"]))
        conn.execute("""INSERT INTO relations(id,source_id,target_id,relation_type,created_at,updated_at)
            VALUES(?,?,?,?,?,?)""", ("direct-edge", first["id"], second["id"], "works_on", "2026", "2026"))
    graph = kg._ensure_graph()
    assert graph.nodes[first["id"]]["description"] == "Changed outside graph wrapper"
    assert graph.has_edge(first["id"], second["id"], "direct-edge")


def test_native_deserializer_is_never_used_for_selected_or_legacy_data(projection_stack, monkeypatch):
    import faiss

    kg, _embedding, _fingerprint, _config = projection_stack
    add(kg)
    kg._VECTOR_DIR.mkdir(parents=True, exist_ok=True)
    (kg._VECTOR_DIR / "index.faiss").write_bytes(b"untrusted legacy content")
    (kg._VECTOR_DIR / "id_map.json").write_text(json.dumps(["foreign"]))
    monkeypatch.setattr(faiss, "read_index", lambda *_a: pytest.fail("native index decoder must remain unreachable"))
    assert not kg.memory_vector_status()["ready"]
    kg.rebuild_index()
    assert kg.memory_vector_status()["ready"]
    assert kg.semantic_search("Synthetic deterministic knowledge body", threshold=-1)
    assert (kg._VECTOR_DIR / "index.faiss").read_bytes() == b"untrusted legacy content"


def test_restart_retains_pending_source_work_and_reuses_unchanged_vectors(projection_stack, monkeypatch):
    kg, embedding, _fingerprint, _config = projection_stack
    first, second = add(kg, "First"), add(kg, "Second")
    kg.rebuild_index()
    kg.update_entity(first["id"], "Changed source saved before restart")
    embedding.batches.clear()
    reloaded = importlib.reload(kg)
    monkeypatch.setattr(reloaded, "_get_embedding_model", lambda **_kw: embedding)
    assert not reloaded.memory_vector_status()["ready"]
    result = reloaded.repair_projections(max_entities=1)
    assert result["complete"]
    assert sum(map(len, embedding.batches)) == 1
    assert reloaded.get_entity(second["id"])
    assert reloaded.memory_vector_status()["ready"]


def test_metadata_budget_failure_preserves_previous_generation(projection_stack, monkeypatch):
    kg, _embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    kg.rebuild_index()
    before = kg._projection_state()["generation"]
    kg.update_entity(entity["id"], "Changed but unpublishable source")
    monkeypatch.setattr(kg, "_PROJECTION_METADATA_BYTES", 64)
    with pytest.raises(ValueError, match="budget"):
        kg.rebuild_index()
    assert kg._projection_state()["generation"] == before
    assert kg.get_entity(entity["id"])["description"] == "Changed but unpublishable source"


def test_wiki_projection_failure_remains_durable_until_successful_retry(projection_stack, monkeypatch, tmp_path):
    from row_bot import wiki_vault

    kg, _embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    monkeypatch.setattr(wiki_vault, "is_enabled", lambda: True)

    def fail(_entities, **_kwargs):
        raise OSError("Synthetic wiki publication fault")

    monkeypatch.setattr(wiki_vault, "export_entities_projection", fail)
    first = kg.repair_projections(max_entities=1)
    assert not first["complete"]
    assert first["pending"] == {"semantic": 0, "wiki": 1}
    assert first["failures"] == ["wiki_projection_incomplete"]
    assert kg.get_entity(entity["id"])
    monkeypatch.setattr(wiki_vault, "export_entities_projection", lambda entities, **_kw: [SimpleNamespace(complete=True) for _ in entities])
    second = kg.repair_projections(max_entities=1)
    assert second["complete"]
    assert second["pending"] == {"semantic": 0, "wiki": 0}


def test_wiki_drain_never_acknowledges_newer_source_revision(projection_stack, monkeypatch, tmp_path):
    from row_bot import wiki_vault

    kg, _embedding, _fingerprint, _config = projection_stack
    entity = add(kg)
    monkeypatch.setattr(wiki_vault, "is_enabled", lambda: True)

    def export(entities, **_kwargs):
        captured = list(entities)[0]
        conn = kg._get_conn()
        try:
            conn.execute("UPDATE entities SET description='Newer canonical source' WHERE id=?", (captured["id"],))
            conn.commit()
        finally:
            conn.close()
        return [SimpleNamespace(complete=True)]

    monkeypatch.setattr(wiki_vault, "export_entities_projection", export)
    result = kg.repair_projections(max_entities=1)
    assert not result["complete"]
    assert result["pending"] == {"semantic": 1, "wiki": 1}
    assert kg.get_entity(entity["id"])["description"] == "Newer canonical source"


def test_another_process_reader_preserves_obsolete_generation_until_release(projection_stack, monkeypatch):
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor

    kg, _embedding, _fingerprint, _config = projection_stack
    add(kg)
    kg.rebuild_index()
    old = kg._projection_state()["generation"]
    script = """
import json
from row_bot import knowledge_graph as kg
with kg._generation_access():
    state = kg._projection_state()
    metadata, directory = kg._load_vector_generation(state)
    print(state['generation'], flush=True)
    input()
    repeated, _ = kg._load_vector_generation(state)
    print(json.dumps({'preserved': repeated == metadata and directory.exists()}), flush=True)
"""
    process = subprocess.Popen([sys.executable, "-u", "-c", script], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(process.stdout.readline).result(timeout=15).strip() == old
        monkeypatch.setattr(kg, "_skip_reindex", False)
        add(kg, "Second generation")
        add(kg, "Third generation")
        assert (kg._VECTOR_DIR / "generations" / old).exists()
        assert kg.memory_vector_status()["retirement_pending"] == 1
        output, errors = process.communicate("\n", timeout=15)
        assert process.returncode == 0, errors
        assert json.loads(output)["preserved"]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    outcome = kg._retire_vector_generations()
    assert outcome["retired"] == 1 and outcome["pending"] == 0
    assert not (kg._VECTOR_DIR / "generations" / old).exists()
    assert kg.memory_vector_status()["ready"]


@pytest.mark.parametrize("damage", ["unknown_file", "modified_manifest", "corrupt_vectors", "unregistered"])
def test_retirement_preserves_unverified_generation_bytes(projection_stack, monkeypatch, damage):
    kg, _embedding, _fingerprint, _config = projection_stack
    add(kg)
    kg.rebuild_index()
    old = kg._projection_state()["generation"]
    directory = kg._VECTOR_DIR / "generations" / old
    with kg._generation_access():
        monkeypatch.setattr(kg, "_skip_reindex", False)
        add(kg, "Second generation")
        add(kg, "Third generation")
    if damage == "unknown_file":
        (directory / "user-notes.txt").write_text("Preserve synthetic private note")
    elif damage == "modified_manifest":
        (directory / "manifest.json").write_text("Externally replaced metadata")
    elif damage == "corrupt_vectors":
        (directory / "segment-000000.faiss").write_bytes(b"Uncertain bytes retained")
    else:
        conn = kg._get_conn()
        try:
            conn.execute("DELETE FROM knowledge_vector_generations WHERE generation=?", (old,))
            conn.commit()
        finally:
            conn.close()
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    for _ in range(2):
        kg._retire_vector_generations()
        assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert kg.memory_vector_status()["ready"]


def test_retirement_failure_after_capture_retries_without_losing_new_head(projection_stack, monkeypatch):
    from pathlib import Path

    kg, _embedding, _fingerprint, _config = projection_stack
    add(kg)
    kg.rebuild_index()
    old = kg._projection_state()["generation"]
    with kg._generation_access():
        monkeypatch.setattr(kg, "_skip_reindex", False)
        add(kg, "Second generation")
        add(kg, "Third generation")
    selected = kg._projection_state()["generation"]
    unlink = Path.unlink

    def fail_manifest(path, *args, **kwargs):
        if path.name == "manifest.json" and path.parent.name == f".retiring-{old}":
            raise OSError("Synthetic retirement interruption")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_manifest)
        assert kg._retire_vector_generations()["pending"] == 1
    assert kg._projection_state()["generation"] == selected
    assert kg.memory_vector_status()["ready"]
    assert kg._retire_vector_generations()["pending"] == 0


def test_retirement_preserves_linked_directory_with_matching_manifest(projection_stack, monkeypatch):
    import os

    kg, _embedding, _fingerprint, _config = projection_stack
    add(kg)
    kg.rebuild_index()
    old = kg._projection_state()["generation"]
    directory = kg._VECTOR_DIR / "generations" / old
    with kg._generation_access():
        monkeypatch.setattr(kg, "_skip_reindex", False)
        add(kg, "Second generation")
        add(kg, "Third generation")
    retained = directory.with_name("unregistered-retained-copy")
    directory.rename(retained)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(retained), str(directory))
        assert directory.is_junction()
    else:
        directory.symlink_to(retained, target_is_directory=True)
    before = {path.name: path.read_bytes() for path in retained.iterdir()}
    result = kg._retire_vector_generations()
    assert result["retired"] == 0 and result["pending"] == 1
    assert directory.exists()
    assert {path.name: path.read_bytes() for path in retained.iterdir()} == before
    assert kg.memory_vector_status()["ready"]
