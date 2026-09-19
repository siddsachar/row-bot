"""Canonical wiki control effects, reviewed conflicts and passive recovery."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from row_bot.application import wiki_commands as controls
from row_bot.file_ownership import directory_identity
from row_bot.runtime import admissions
from tests.integration.wiki_vault.conftest import wiki_stack  # noqa: F401

pytestmark = pytest.mark.subsystem


@pytest.fixture
def stack(wiki_stack, monkeypatch):  # noqa: F811
    from row_bot import tasks
    monkeypatch.setattr(tasks, "_DB_PATH", wiki_stack["data_dir"] / "tasks.db")
    monkeypatch.setattr(tasks, "_SCHEMA_READY_PATH", None)
    admissions.instance_identity()
    wiki_stack["wiki_vault"].set_enabled(True)
    scope = controls.WikiScope("synthetic-vault", wiki_stack["vault"], directory_identity(wiki_stack["vault"], parent=True))
    wiki_stack["scope"] = scope
    return wiki_stack


def noop(*_args):
    pass


def article(stack, subject="Synthetic article"):
    wiki, kg = stack["wiki_vault"], stack["kg"]
    entity = kg.save_entity("person", subject, "Original database description is long enough.")
    return entity, wiki.export_entity(entity)


def prepare(stack, action, **fields):
    scope = stack["scope"]
    payload = {"revision": stack["wiki_vault"].read_control_config()[1], **fields}
    review = controls.review_wiki_command(action, payload, scope=scope, validate=noop)
    command = {"command_id": str(uuid4()), "type": action, "expected_revision": "0",
               "payload": {**payload, "review_id": "synthetic-reviewed-nonce"}}
    return command, review


def execute(stack, command, review, **kwargs):
    def accepted(_command, actual):
        assert actual == review
    return controls.execute_wiki_command(command, owner_id="synthetic-owner", authority_id="synthetic-session",
        key=command["command_id"], scope=stack["scope"], validate=kwargs.pop("validate", noop),
        validate_action=kwargs.pop("validate_action", noop), validate_review=kwargs.pop("validate_review", accepted), **kwargs)


def receipt(stack, command, **kwargs):
    return controls.read_wiki_receipt(owner_id=kwargs.pop("owner_id", "synthetic-owner"),
        authority_id=kwargs.pop("authority_id", "synthetic-session"), command_id=command["command_id"],
        scope=stack["scope"], validate=noop)


def item(stack):
    page = controls.read_wiki_articles(scope=stack["scope"], validate=noop)
    return next(row for row in page["items"] if row["entity_id"])


def test_cold_import_and_status_do_not_create_data(tmp_path):
    cold = tmp_path / "absent"
    code = """
import os, sys
from pathlib import Path
os.environ['ROW_BOT_DATA_DIR'] = sys.argv[1]
from row_bot.application.wiki_commands import read_wiki_status
assert read_wiki_status(scope=None, validate=lambda:None)['availability'] == 'scope_required'
assert not Path(sys.argv[1]).exists()
assert 'row_bot.knowledge_graph' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code, str(cold)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_status_open_and_check_are_passive_and_path_free(stack, monkeypatch):
    entity, path = article(stack)
    before = {str(p): p.read_bytes() for p in stack["vault"].rglob("*") if p.is_file()}
    monkeypatch.setattr(stack["kg"], "update_entity", lambda *_a, **_k: pytest.fail("No update on read"))
    monkeypatch.setattr(stack["wiki_vault"], "rebuild_vault", lambda *_a, **_k: pytest.fail("No rebuild on read"))
    status = controls.read_wiki_status(scope=stack["scope"], validate=noop)
    row = item(stack)
    result = controls.read_wiki_article(scope=stack["scope"], article_id=row["article_id"], validate=noop)
    assert status["articles"] == 1 and status["edited"] == 0
    assert result["vault_text"] == path.read_text(encoding="utf-8") and result["entity_id"] == entity["id"]
    assert str(stack["vault"]) not in json.dumps([status, row, result])
    assert before == {str(p): p.read_bytes() for p in stack["vault"].rglob("*") if p.is_file()}


def test_explicit_config_is_atomic_retains_unknown_fields_and_does_not_rebuild(stack, monkeypatch):
    wiki = stack["wiki_vault"]
    cfg = wiki._load_config()
    cfg["legacy_option"] = {"saved": True}
    wiki._save_config(cfg)
    monkeypatch.setattr(wiki, "rebuild_vault", lambda **_k: pytest.fail("Configure is not rebuild"))
    command, review = prepare(stack, "wiki.configure", enabled=False)
    result = execute(stack, command, review)
    assert result["status"] == "completed" and not wiki.is_enabled()
    assert wiki._load_config()["legacy_option"] == {"saved": True}
    assert execute(stack, command, review) == result


def test_config_publish_failure_retains_old_config(stack, monkeypatch):
    from row_bot.providers import saved_model_settings
    wiki = stack["wiki_vault"]
    command, review = prepare(stack, "wiki.configure", enabled=False)
    before = wiki._CONFIG_PATH.read_bytes()
    monkeypatch.setattr(saved_model_settings, "write_provider_metadata", lambda *_: (_ for _ in ()).throw(OSError("synthetic")))
    assert execute(stack, command, review)["status"] == "partial"
    assert wiki._CONFIG_PATH.read_bytes() == before


@pytest.mark.parametrize("action", ["wiki.publish", "wiki.rebuild"])
def test_explicit_publish_rebuild_exact_receipt_and_no_replay(stack, monkeypatch, action):
    entity, _ = article(stack)
    command, review = prepare(stack, action, **({"entity_id": entity["id"]} if action.endswith("publish") else {}))
    result = execute(stack, command, review)
    assert result["status"] == "completed" and result["count"] == 1
    monkeypatch.setattr(stack["wiki_vault"], "_stage", lambda *_a, **_k: pytest.fail("No replay publication"))
    assert receipt(stack, command) == result and execute(stack, command, review) == result


def test_rebuild_preserves_collisions_external_files_and_duplicate_titles(stack):
    _one, first = article(stack, "Same / name")
    _two, second = article(stack, "Same / name")
    assert first != second
    first.write_bytes(b"External edited article")
    unowned = first.parent / "unowned.md"
    unowned.write_bytes(b"Authored file")
    command, review = prepare(stack, "wiki.rebuild")
    result = execute(stack, command, review)
    assert result["conflicts"] == 1 and result["code"] == "wiki_conflict"
    assert first.read_bytes() == b"External edited article" and unowned.read_bytes() == b"Authored file"
    assert second.exists()


def test_incomplete_enumeration_never_retires_owned_files(stack, monkeypatch):
    entity, path = article(stack)
    command, review = prepare(stack, "wiki.rebuild")
    before = path.read_bytes()
    def partial(*_):
        yield entity
        raise RuntimeError("Synthetic incomplete enumeration")
    monkeypatch.setattr(stack["kg"], "iter_entities_snapshot", partial)
    result = execute(stack, command, review)
    assert result["status"] == "partial" and path.read_bytes() == before
    assert execute(stack, command, review) == result


def test_cancellation_after_staging_preserves_original_and_partial_receipt(stack, monkeypatch):
    _, path = article(stack)
    command, review = prepare(stack, "wiki.rebuild")
    before = path.read_bytes()
    stopped = False
    original = stack["wiki_vault"]._stage
    def stage(*args, **kwargs):
        nonlocal stopped
        result = original(*args, **kwargs)
        stopped = True
        return result
    monkeypatch.setattr(stack["wiki_vault"], "_stage", stage)
    with pytest.raises(Exception, match="wiki_cancelled"):
        execute(stack, command, review, cancelled=lambda: stopped)
    assert receipt(stack, command)["status"] == "partial" and path.read_bytes() == before


@pytest.mark.parametrize("changed", ["vault", "database", "configuration", "authority"])
def test_review_changes_deny_before_effect(stack, monkeypatch, changed):
    entity, path = article(stack)
    command, review = prepare(stack, "wiki.publish", entity_id=entity["id"])
    if changed == "vault":
        path.write_bytes(path.read_bytes() + b"External")
    elif changed == "database":
        stack["kg"].update_entity(entity["id"], "A newer database version requiring another review")
    elif changed == "configuration":
        stack["wiki_vault"].set_enabled(False)
    before = path.read_bytes()
    monkeypatch.setattr(stack["wiki_vault"], "_stage", lambda *_a, **_k: pytest.fail("No effect before valid review"))
    def deny(*_):
        raise ValueError("authority_revoked")
    with pytest.raises((Exception, AssertionError)):
        execute(stack, command, review, validate_action=deny if changed == "authority" else noop)
    assert path.read_bytes() == before
    assert admissions.read_command_metadata("synthetic-owner", command["command_id"]) is None


def test_sync_conflict_requires_paired_review_and_preserves_both_versions(stack, monkeypatch):
    entity, path = article(stack)
    vault = path.read_text(encoding="utf-8").replace("Original database description is long enough.", "Vault version for explicit acceptance.")
    path.write_text(vault, encoding="utf-8")
    stack["kg"].update_entity(entity["id"], "Later database description preserved for recovery.")
    identifier = item(stack)["article_id"]
    with pytest.raises(Exception, match="wiki_conflict_review_required"):
        prepare(stack, "wiki.sync", article_ids=[identifier])
    command, review = prepare(stack, "wiki.import", article_ids=[identifier])
    assert "Later database" in review["articles"][0]["database_text"]
    assert "Vault version" in review["articles"][0]["vault_text"]
    monkeypatch.setattr(stack["kg"], "_upsert_index", lambda *_a, **_k: pytest.fail("No embedding from import"))
    result = execute(stack, command, review)
    assert result["status"] == "completed" and result["count"] == 1
    assert stack["kg"].get_entity(entity["id"])["description"] == "Vault version for explicit acceptance."
    candidates = list((stack["vault"] / "wiki/.row-bot-recovery").glob("*.candidate"))
    assert any(b"Later database" in candidate.read_bytes() for candidate in candidates)


def test_sync_nonconflicting_edits_and_missing_article_status(stack):
    entity, path = article(stack)
    path.write_text(path.read_text(encoding="utf-8").replace("Original database", "External vault"), encoding="utf-8")
    command, review = prepare(stack, "wiki.sync", article_ids=[item(stack)["article_id"]])
    assert execute(stack, command, review)["count"] == 1
    assert "External vault" in stack["kg"].get_entity(entity["id"])["description"]
    path.unlink()
    assert item(stack)["status"] == "missing"


@pytest.mark.parametrize("action", ["wiki.configure", "wiki.rebuild", "wiki.import"])
def test_lost_receipt_uses_exact_canonical_proof_without_repeating(stack, monkeypatch, action):
    _, path = article(stack)
    fields = {}
    if action == "wiki.configure":
        fields["enabled"] = False
    if action == "wiki.import":
        path.write_bytes(path.read_bytes() + b"\nExternal edit")
        fields["article_ids"] = [item(stack)["article_id"]]
    command, review = prepare(stack, action, **fields)
    monkeypatch.setattr(admissions, "complete_command", lambda *_: (_ for _ in ()).throw(OSError("Lost publication")))
    result = execute(stack, command, review)
    assert result["status"] == "completed"
    monkeypatch.setattr(stack["wiki_vault"], "_stage", lambda *_a, **_k: pytest.fail("No repeated write"))
    assert receipt(stack, command) == result and execute(stack, command, review) == result


def test_lost_import_manifest_is_uncertain_never_blindly_reapplied(stack, monkeypatch):
    entity, path = article(stack)
    path.write_text(path.read_text(encoding="utf-8").replace("Original database", "External vault"), encoding="utf-8")
    command, review = prepare(stack, "wiki.import", article_ids=[item(stack)["article_id"]])
    monkeypatch.setattr(stack["wiki_vault"], "_write_manifest", lambda *_a, **_k: (_ for _ in ()).throw(OSError("Lost manifest")))
    result = execute(stack, command, review)
    assert result["status"] == "partial"
    monkeypatch.setattr(stack["kg"], "update_entity", lambda *_a, **_k: pytest.fail("No repeated import"))
    assert execute(stack, command, review) == result
    assert "External vault" in stack["kg"].get_entity(entity["id"])["description"]


def test_foreign_receipt_denied_and_changed_command_rejected(stack):
    command, review = prepare(stack, "wiki.configure", enabled=False)
    execute(stack, command, review)
    for options in ({"owner_id": "foreign"}, {"authority_id": "foreign"}):
        with pytest.raises(Exception, match="wiki_operation_unavailable"):
            receipt(stack, command, **options)
    command["payload"]["enabled"] = True
    with pytest.raises(Exception, match="idempotency_mismatch"):
        execute(stack, command, review)


def test_paging_stale_and_review_size_fail_closed(stack):
    for index in range(3):
        article(stack, f"Article {index}")
    first = controls.read_wiki_articles(scope=stack["scope"], limit=1, validate=noop)
    second = controls.read_wiki_articles(scope=stack["scope"], limit=1, cursor=first["next_cursor"], validate=noop)
    assert first["items"][0] != second["items"][0] and second["total"] == 3
    _, path = article(stack, "Another article")
    with pytest.raises(Exception, match="cursor_expired"):
        controls.read_wiki_articles(scope=stack["scope"], limit=1, cursor=first["next_cursor"], validate=noop)
    path.write_bytes(path.read_bytes() + "😀".encode() * 30_000)
    row = next(row for row in controls.read_wiki_articles(scope=stack["scope"], validate=noop)["items"] if row["title"] == "Another article")
    with pytest.raises(Exception, match="wiki_review_too_large"):
        controls.read_wiki_article(scope=stack["scope"], article_id=row["article_id"], validate=noop)


def test_linked_or_incomplete_enumeration_never_claims_sync(stack, monkeypatch):
    _, path = article(stack)
    original = Path.lstat
    def fail(current):
        if current == path:
            raise PermissionError("Synthetic denied read")
        return original(current)
    monkeypatch.setattr(Path, "lstat", fail)
    status = controls.read_wiki_status(scope=stack["scope"], validate=noop)
    assert status["availability"] == "unavailable" and status["edited"] is None
    with pytest.raises(PermissionError):
        controls.read_wiki_articles(scope=stack["scope"], validate=noop)
