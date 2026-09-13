"""Independent document queue bounds, retention and receipt probes."""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from tests.subsystem.knowledge_graph.test_document_job_commands import client as client
from tests.subsystem.knowledge_graph.test_document_job_commands import command, execute, queued

pytestmark = pytest.mark.subsystem


def test_retry_retains_replaced_work_and_never_replays_uncertain_effect(client, monkeypatch):
    from row_bot.developer import edits
    api, service, identity = client
    batch, job = queued(client)
    service.claim_next("worker")
    service.mark_failed(job.id, "parse_failed", "", stage="parse")
    work = service.work_root / job.id
    work.mkdir()
    (work / "saved.txt").write_text("original")
    value, _ = command(client, "document.job.retry", job.id)
    rename = edits._rename_edit_no_replace
    def replace(*args, **kwargs):
        work.rename(service.work_root / "independent-original")
        work.mkdir()
        (work / "saved.txt").write_text("new external work")
        return rename(*args, **kwargs)
    monkeypatch.setattr(edits, "_rename_edit_no_replace", replace)
    result = execute(client, value)
    assert result["status"] == "partial"
    assert service.get_job(job.id).status == "failed"
    assert (service.work_root / "independent-original/saved.txt").read_text() == "original"
    assert [p.read_text() for p in service.work_root.glob(".retry-*/saved.txt")] == ["new external work"]
    monkeypatch.setattr(service, "retry_failed", lambda *_a, **_k: pytest.fail("repeated uncertain retry"))
    assert execute(client, value) == result
    assert api.read_document_control_command(command_id=value["command_id"], **identity) == result
    assert Path(job.staged_path).read_bytes() == b"example document"


def test_retry_revocation_after_retention_is_partial_without_queue_publication(client, monkeypatch):
    from row_bot.developer import edits
    _, service, _ = client
    batch, job = queued(client)
    service.claim_next("worker")
    service.mark_failed(job.id, "parse_failed", "", stage="parse")
    work = service.work_root / job.id
    work.mkdir()
    (work / "saved.txt").write_text("retained")
    value, _ = command(client, "document.job.retry", job.id)
    rename, revoked = edits._rename_edit_no_replace, False
    def retire(*args, **kwargs):
        nonlocal revoked
        result = rename(*args, **kwargs)
        revoked = True
        return result
    monkeypatch.setattr(edits, "_rename_edit_no_replace", retire)
    def validate_action(_kind):
        if revoked:
            raise PermissionError("authority revoked")
    with pytest.raises(PermissionError, match="authority revoked"):
        execute(client, value, validate_action=validate_action)
    assert service.get_job(job.id).status == "failed"
    assert [p.read_text() for p in service.work_root.glob(".retry-*/saved.txt")] == ["retained"]
    monkeypatch.setattr(service, "retry_failed", lambda *_a, **_k: pytest.fail("repeated uncertain retry"))
    assert execute(client, value)["status"] == "partial"


@pytest.mark.parametrize("column,value", [("original_name", r"C:\private-owner\secret.txt"),
    ("cancel_requested", "not-a-boolean")])
def test_malformed_saved_queue_fields_are_not_rendered_as_valid_public_state(client, column, value):
    api, service, _ = client
    batch, job = queued(client)
    with contextlib.closing(service._connect()) as conn, conn:
        conn.execute(f"UPDATE document_jobs SET {column}=? WHERE id=?", (value, job.id))
    page = api.read_document_queue(kind="jobs", batch_id=batch, validate=lambda: None)
    assert page.availability == "unavailable" and not page.items
