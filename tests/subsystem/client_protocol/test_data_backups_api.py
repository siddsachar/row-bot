"""Settings › Data backup and restore through the API: local owner only, reviewed restores."""
from __future__ import annotations

import time
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from row_bot.application import client_data_backup as data
from tests.subsystem.client_protocol.test_protocol_security import _native_proof, bootstrap, client_app

pytestmark = pytest.mark.subsystem


@pytest.fixture
def places(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "tools_config.json").write_text('{"enabled": ["web_search"]}', encoding="utf-8")
    (profile / "api_keys.json").write_text('{"openai": "sk-SYNTHETIC-never-archived"}', encoding="utf-8")
    backups = tmp_path / "Row-Bot" / "Backups"
    backups.mkdir(parents=True)
    monkeypatch.setattr(data, "_data_dir", lambda: profile)
    monkeypatch.setattr(data, "_backups_folder", lambda: backups)
    monkeypatch.setattr(data, "_folder_words", lambda: "Row-Bot › Backups")
    data._JOB.clear()
    data._REVIEWS.clear()
    return profile, backups


def _command(client, headers, action, **extra):
    identity = str(uuid4())
    return client.post("/api/v1/data/backup/commands",
                       headers={**headers, "Idempotency-Key": identity},
                       json={"command_id": identity, "client_session_id": headers["X-Client-Session"],
                             "action": action, **extra})


def _settled(client, headers):
    for _ in range(200):
        state = client.get("/api/v1/data/backup", headers=headers).json()
        if not state["job"] or state["job"]["status"] != "running":
            return state
        time.sleep(0.02)
    raise AssertionError("the job did not finish")


def _pick(client, proof, path: Path) -> str:
    picked = client.post("/api/v1/native/selections/complete", headers={"Origin": "http://localhost"},
                         json={**proof, "selection_kind": "file", "intent_id": str(uuid4()),
                               "intent": "restore_backup", "conversation_id": None,
                               "destination": "data-restore", "path": str(path)})
    assert picked.status_code == 200, picked.text
    assert str(path) not in picked.text
    return picked.json()["reference"]


def test_back_up_then_restore_on_the_next_start_only_after_confirming(places):
    profile, backups = places
    client, _service, _active = client_app()
    with client:
        proof, headers = _native_proof(client)
        state = client.get("/api/v1/data/backup", headers=headers).json()
        assert state["local_owner"] is True and state["last_backup_at"] is None
        started = _command(client, headers, "backup")
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "accepted"
        state = _settled(client, headers)
        assert state["job"]["status"] == "completed"
        archive = backups / state["last_backup_name"]
        assert archive.is_file() and state["folder"] == "Row-Bot › Backups"
        assert "api_keys.json" not in zipfile.ZipFile(archive).namelist()

        reviewed = _command(client, headers, "inspect_restore", file_grant=_pick(client, proof, archive))
        assert reviewed.status_code == 200, reviewed.text
        review = reviewed.json()["review"]
        assert review["source_name"] == archive.name and len(review["review_id"]) == 64
        # Looking at a backup changes nothing.
        assert _settled(client, headers)["pending_restore"] is None

        wrong = _command(client, headers, "restore", review_id="0" * 64)
        assert wrong.status_code == 409 and wrong.json()["code"] == "backup_review_expired"
        confirmed = _command(client, headers, "restore", review_id=review["review_id"])
        assert confirmed.status_code == 200, confirmed.text
        state = _settled(client, headers)
        assert state["pending_restore"]["source_name"] == archive.name
        # One confirmation, one use.
        again = _command(client, headers, "restore", review_id=review["review_id"])
        assert again.status_code == 409
        cancelled = _command(client, headers, "cancel_restore")
        assert cancelled.json()["state"]["pending_restore"] is None
        assert (profile / "tools_config.json").read_text(encoding="utf-8") == '{"enabled": ["web_search"]}'


def test_a_foreign_archive_is_refused_before_anything_is_staged(places, tmp_path):
    profile, _backups = places
    foreign = tmp_path / "photos.zip"
    with zipfile.ZipFile(foreign, "w") as bundle:
        bundle.writestr("holiday.txt", "not a backup")
    client, _service, _active = client_app()
    with client:
        proof, headers = _native_proof(client)
        refused = _command(client, headers, "inspect_restore", file_grant=_pick(client, proof, foreign))
        assert refused.status_code == 422 and refused.json()["code"] == "backup_not_row_bot"
        assert client.get("/api/v1/data/backup", headers=headers).json()["pending_restore"] is None
    assert not (profile / ".restore-pending").exists()


def test_other_devices_cannot_back_up_or_restore(places):
    client, _service, _active = client_app(remote=True)
    with client:
        _handshake, headers = bootstrap(client)
        state = client.get("/api/v1/data/backup", headers=headers)
        assert state.status_code == 200 and state.json()["local_owner"] is False
        assert state.json()["last_backup_name"] is None
        refused = _command(client, headers, "backup")
        assert refused.status_code == 403 and refused.json()["code"] == "owner_local_only"
