"""Profile backups and Learning (O39): a backup keeps ``learning.db``, the blobs and the audit
vault (the person's data), and leaves out what downloads or trains again: the training
engine, starting-model weights, training checkpoints and exported model files. A restore
leaves those where they are on this computer."""

from __future__ import annotations

import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from row_bot import profile_restore
from row_bot.application import profile_backup as backup
from row_bot.learning.store import LearningStore
from row_bot.learning.training import jobs as training_jobs
from tests.fixtures.learning import episode

pytestmark = pytest.mark.subsystem


def _bulk(root: Path) -> list[str]:
    """Personal-model bulk in a data folder, as relative paths."""

    files = {
        "runtimes/learning-engine/Lib/site-packages/torch/lib/torch_cuda.dll": b"engine",
        "learning_models/weights/qwen3.5-2b/model.safetensors": b"weights",
        "learning_models/runs/job_1/checkpoints/checkpoint-10/weights.bin": b"checkpoint",
        "learning_models/runs/job_1/data/examples.jsonl": b"{}\n",
        "learning_models/versions/v1/personal-model-v1-Q8_0.gguf": b"gguf",
    }
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return sorted(files)


def test_a_backup_keeps_learning_data_and_leaves_out_the_personal_model_bulk(learning_on):
    store = LearningStore(learning_on)
    store.create()
    store.put_blob(b"what the model saw")
    store.put(episode("chat-1"))
    with closing(sqlite3.connect(learning_on / "learning" / "audit.db")) as vault, vault:
        vault.execute("CREATE TABLE tasks (id TEXT)")
    bulk = _bulk(learning_on)
    assert training_jobs.models_dir(learning_on).is_dir()

    result = backup.create_backup(learning_on, learning_on.parent / "Backups", now=datetime(2026, 10, 16, 9, 0))
    with zipfile.ZipFile(result["path"]) as bundle:
        names = set(bundle.namelist())
    assert {"learning.db", "learning/audit.db", "learning_config.json"} <= names
    assert any(name.startswith("learning/blobs/") for name in names)
    for name in bulk:
        assert name not in names
    assert not any(name.startswith(("learning_models/", "runtimes/")) for name in names)
    assert "Personal model files (they download or train again)" in result["left_out"]


def test_a_backup_without_personal_model_files_says_nothing_about_them(data_dir):
    (data_dir / "threads.db").write_bytes(b"")
    result = backup.create_backup(data_dir, data_dir.parent / "Backups", now=datetime(2026, 10, 16, 9, 5))
    assert "Personal model files (they download or train again)" not in result["left_out"]


def test_a_restore_leaves_the_personal_model_bulk_where_it_is(learning_on):
    LearningStore(learning_on).create()
    archive = Path(backup.create_backup(learning_on, learning_on.parent / "Backups",
                                        now=datetime(2026, 10, 16, 9, 10))["path"])
    bulk = _bulk(learning_on)
    backup.stage_restore(archive, learning_on, source_name=archive.name)
    applied = profile_restore.apply_pending(learning_on, now=datetime(2026, 10, 16, 9, 15))
    assert applied["status"] == "applied"
    for name in bulk:
        assert (learning_on / name).is_file(), name  # kept in place on this computer
    assert (learning_on / "learning.db").is_file()
