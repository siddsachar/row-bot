"""C1 (A10): a version's kept rows are learning data: they go as soon as a conversation they came from is deleted
or switched off, and are handed back only while they match the version's record."""

from __future__ import annotations

import json

import pytest

from row_bot.learning.store import LearningStore
from row_bot.learning.training import kept_rows, releases

pytestmark = [pytest.mark.subsystem]

TRAIN = [{"messages": [{"role": "user", "content": "Plan the trip"}, {"role": "assistant", "content": "Here."}]}]
VALIDATION = [{"messages": [{"role": "user", "content": "And back?"}, {"role": "assistant", "content": "Sunday."}]}]


def _version(root, number: int, conversations: list[str]):
    folder = releases.version_dir(number, root)
    data = root / f"data-{number}"
    data.mkdir(parents=True)
    for name, rows in ((kept_rows.EXAMPLES, TRAIN), (kept_rows.VALIDATION, VALIDATION)):
        (data / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    manifest = {"version": 2, "recipe": "16gb-v1", "max_seq_length": 16384, "rendering": {"name": "served-v2"},
                "conversations": conversations}
    manifest["sha256"] = kept_rows.digest(manifest, TRAIN, VALIDATION)
    folder.mkdir(parents=True)
    (folder / releases.DATASET_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    assert kept_rows.keep(data, folder, manifest)
    return folder, manifest


def test_deleting_a_chat_removes_the_kept_rows_of_every_version_that_learned_from_it(learning_on):
    first, manifest = _version(learning_on, 1, ["chat-a", "chat-b"])
    second, other = _version(learning_on, 2, ["chat-c"])

    assert kept_rows.forget(["chat-b"], learning_on) == 1

    assert kept_rows.read(first, manifest)[0] == "not_kept" and not (first / kept_rows.FOLDER).exists()
    assert kept_rows.read(second, other)[0] == "kept"  # learned from another chat only


def test_switching_a_chat_off_removes_its_kept_rows(learning_on):
    from row_bot.learning import scope

    LearningStore(learning_on).create()
    folder, manifest = _version(learning_on, 1, ["chat-a"])

    scope.set_chat_opt_out("chat-a", True, store=LearningStore(learning_on))

    assert kept_rows.read(folder, manifest)[0] == "not_kept"


def test_kept_rows_are_handed_back_only_while_they_match_and_their_chats_exist(learning_on):
    folder, manifest = _version(learning_on, 1, ["chat-a"])

    assert kept_rows.read(folder, manifest, exists=lambda chat: True) == ("kept", TRAIN, VALIDATION)
    assert kept_rows.read(folder, manifest, exists=lambda chat: False)[0] == "source_deleted"
    rows = folder / kept_rows.FOLDER / kept_rows.EXAMPLES
    rows.write_text(rows.read_text(encoding="utf-8").replace("trip", "tour"), encoding="utf-8")
    assert kept_rows.read(folder, manifest)[0] == "changed"
