"""Explicit updater choices use the existing owner with isolated state."""

from __future__ import annotations

from uuid import uuid4
from threading import Event

import pytest

from row_bot.application import client_updates
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem


def _updater(tmp_path, monkeypatch):
    from row_bot import updater

    monkeypatch.setattr(updater, "_CONFIG_PATH", tmp_path / "update_config.json")
    monkeypatch.setattr(updater, "_DOWNLOAD_DIR", tmp_path / "updates")
    monkeypatch.setattr(updater, "_state", None)
    monkeypatch.setattr(updater, "is_dev_install", lambda: False)
    client_updates._RECEIPTS.clear()
    client_updates._INSTALLS.clear()
    client_updates._ACTIVE_INSTALL = None
    return updater


def _release(updater):
    return updater.UpdateInfo(
        version="99.0.0", channel="stable", published_at="2026-09-23T10:00:00Z",
        notes_md="Synthetic release notes", notes_summary="Synthetic",
        asset_name="Row-Bot-99.0.0-Windows-x64.exe",
        asset_url="https://github.com/example/fixture.exe", asset_size=12,
        sha256="a" * 64, html_url="https://github.com/example/releases/99.0.0",
        is_prerelease=False,
    )


def test_update_read_is_passive_and_check_failure_is_not_up_to_date(tmp_path, monkeypatch):
    updater = _updater(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(updater, "check_for_updates", lambda *, force: calls.append(force))
    first = client_updates.read_updates()
    assert first["available"] is None
    assert calls == []
    assert not updater._CONFIG_PATH.exists()
    command_id = str(uuid4())
    result = client_updates.execute_update_choice(
        owner_id="local", command_id=command_id, expected_revision=first["revision"],
        action="check",
    )
    assert result["status"] == "failed"
    assert calls == [True]
    assert client_updates.execute_update_choice(
        owner_id="local", command_id=command_id, expected_revision=first["revision"],
        action="check",
    ) == result
    assert calls == [True]


def test_update_command_is_unavailable_from_development_checkout(tmp_path, monkeypatch):
    updater = _updater(tmp_path, monkeypatch)
    monkeypatch.setattr(updater, "is_dev_install", lambda: True)
    first = client_updates.read_updates()
    assert first["dev_install"] is True
    with pytest.raises(Exception, match="update_unavailable"):
        client_updates.execute_update_choice(
            owner_id="local", command_id=str(uuid4()), expected_revision=first["revision"],
            action="check",
        )


def test_update_skip_and_clear_are_revision_bound(tmp_path, monkeypatch):
    updater = _updater(tmp_path, monkeypatch)
    updater.get_update_state().available = _release(updater)
    first = client_updates.read_updates()
    assert first["available"]["verified_manifest"] is True
    with pytest.raises(Exception, match="update_changed"):
        client_updates.execute_update_choice(
            owner_id="local", command_id=str(uuid4()), expected_revision=first["revision"],
            action="skip", version="98.0.0",
        )
    skipped = client_updates.execute_update_choice(
        owner_id="local", command_id=str(uuid4()), expected_revision=first["revision"],
        action="skip", version="99.0.0",
    )["snapshot"]
    assert skipped["available"] is None
    assert skipped["skipped_versions"] == ["99.0.0"]
    cleared = client_updates.execute_update_choice(
        owner_id="local", command_id=str(uuid4()), expected_revision=skipped["revision"],
        action="clear_skipped",
    )["snapshot"]
    assert cleared["skipped_versions"] == []


def test_settings_channel_save_refreshes_the_shared_updater_owner(tmp_path, monkeypatch):
    updater = _updater(tmp_path, monkeypatch)
    from row_bot.application.settings_commands import _write_json_setting

    assert client_updates.read_updates()["channel"] == "stable"
    _write_json_setting(tmp_path, "preferences", "updates.channel", "beta")
    assert updater.get_update_state().channel == "beta"
    assert client_updates.read_updates()["channel"] == "beta"


def test_update_api_requires_direct_owner_and_command_identity(tmp_path, monkeypatch):
    updater = _updater(tmp_path, monkeypatch)
    calls = []

    def fake_check(*, force):
        calls.append(force)
        updater.get_update_state().last_success = "2026-09-23T12:00:00Z"

    monkeypatch.setattr(updater, "check_for_updates", fake_check)
    local, _, _ = client_app()
    remote, _, _ = client_app(remote=True)
    with local, remote:
        _, headers = bootstrap(local)
        _, remote_headers = bootstrap(remote)
        assert remote.get("/api/v1/system/updates", headers=remote_headers).status_code == 403
        first = local.get("/api/v1/system/updates", headers=headers)
        assert first.status_code == 200, first.text
        browser_get = local.get(
            "/api/v1/system/updates",
            headers={key: value for key, value in headers.items() if key != "Origin"},
        )
        assert browser_get.status_code == 200, browser_get.text
        assert local.get(
            "/api/v1/system/updates",
            headers={**headers, "Origin": "http://other.invalid"},
        ).status_code == 403
        assert calls == []
        command_id = str(uuid4())
        body = {"command_id": command_id, "expected_revision": first.json()["revision"],
                "action": "check", "version": ""}
        assert remote.post("/api/v1/system/updates/commands", headers={**remote_headers, "Idempotency-Key": command_id}, json=body).status_code == 403
        assert local.post("/api/v1/system/updates/commands", headers=headers, json=body).status_code == 409
        result = local.post("/api/v1/system/updates/commands", headers={**headers, "Idempotency-Key": command_id}, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "completed"
    assert calls == [True]


def test_install_uses_fake_verified_download_and_handoff(tmp_path, monkeypatch):
    updater = _updater(tmp_path, monkeypatch)
    updater.get_update_state().available = _release(updater)
    start = client_updates.read_updates()
    done = Event()
    calls = []

    def download(info, *, progress):
        calls.append(("download", info.version))
        progress(12, 12)
        return tmp_path / "fixture.exe"

    def handoff(path):
        calls.append(("handoff", path.name))
        done.set()

    monkeypatch.setattr(updater, "download_update", download)
    monkeypatch.setattr(updater, "install_and_restart", handoff)
    command_id = str(uuid4())
    client_updates.start_update_install(
        owner_id="local", command_id=command_id,
        expected_revision=start["revision"], version="99.0.0", validate=lambda: None,
    )
    assert done.wait(5)
    status = client_updates.read_update_install(owner_id="local", command_id=command_id)
    assert status["phase"] == "handoff"
    assert status["downloaded"] == status["total"] == 12
    assert calls == [("download", "99.0.0"), ("handoff", "fixture.exe")]
    assert client_updates.start_update_install(
        owner_id="local", command_id=command_id,
        expected_revision=start["revision"], version="99.0.0", validate=lambda: None,
    ) == status


def test_install_cancel_stops_before_handoff(tmp_path, monkeypatch):
    updater = _updater(tmp_path, monkeypatch)
    updater.get_update_state().available = _release(updater)
    start = client_updates.read_updates()
    release = Event()
    finished = Event()
    installs = []
    finish = client_updates._finish_install

    def finish_and_signal(key, phase, message):
        finish(key, phase, message)
        finished.set()

    monkeypatch.setattr(client_updates, "_finish_install", finish_and_signal)

    def download(_info, *, progress):
        release.wait(5)
        progress(12, 12)
        return tmp_path / "fixture.exe"

    monkeypatch.setattr(updater, "download_update", download)
    monkeypatch.setattr(updater, "install_and_restart", lambda path: installs.append(path))
    command_id = str(uuid4())
    client_updates.start_update_install(
        owner_id="local", command_id=command_id,
        expected_revision=start["revision"], version="99.0.0", validate=lambda: None,
    )
    cancelled = client_updates.cancel_update_install(owner_id="local", command_id=command_id)
    assert cancelled["phase"] == "cancel_requested"
    release.set()
    assert finished.wait(5)
    status = client_updates.read_update_install(owner_id="local", command_id=command_id)
    assert status["phase"] == "cancelled"
    assert installs == []
    with pytest.raises(Exception, match="update_job_missing"):
        client_updates.read_update_install(owner_id="other", command_id=command_id)


def test_install_revalidates_owner_before_handoff(tmp_path, monkeypatch):
    updater = _updater(tmp_path, monkeypatch)
    updater.get_update_state().available = _release(updater)
    start = client_updates.read_updates()
    release = Event()
    finished = Event()
    installs = []
    authorized = [True]
    finish = client_updates._finish_install

    def finish_and_signal(key, phase, message):
        finish(key, phase, message)
        finished.set()

    monkeypatch.setattr(client_updates, "_finish_install", finish_and_signal)

    def download(_info, *, progress):
        release.wait(5)
        return tmp_path / "fixture.exe"

    def validate():
        if not authorized[0]:
            raise RuntimeError("revoked")

    monkeypatch.setattr(updater, "download_update", download)
    monkeypatch.setattr(updater, "install_and_restart", lambda path: installs.append(path))
    command_id = str(uuid4())
    client_updates.start_update_install(
        owner_id="local", command_id=command_id,
        expected_revision=start["revision"], version="99.0.0", validate=validate,
    )
    authorized[0] = False
    release.set()
    assert finished.wait(5)
    assert client_updates.read_update_install(owner_id="local", command_id=command_id)["phase"] == "failed"
    assert installs == []


def test_install_api_blocks_remote_owner_and_returns_fake_handoff(tmp_path, monkeypatch):
    updater = _updater(tmp_path, monkeypatch)
    updater.get_update_state().available = _release(updater)
    done = Event()
    monkeypatch.setattr(updater, "download_update", lambda info, *, progress: tmp_path / "fixture.exe")
    monkeypatch.setattr(updater, "install_and_restart", lambda path: done.set())
    local, _, _ = client_app()
    remote, _, _ = client_app(remote=True)
    with local, remote:
        _, headers = bootstrap(local)
        _, remote_headers = bootstrap(remote)
        first = local.get("/api/v1/system/updates", headers=headers)
        command_id = str(uuid4())
        body = {"command_id": command_id, "expected_revision": first.json()["revision"], "version": "99.0.0"}
        denied = remote.post("/api/v1/system/updates/installs", headers={**remote_headers, "Idempotency-Key": command_id}, json=body)
        assert denied.status_code == 403
        started = local.post("/api/v1/system/updates/installs", headers={**headers, "Idempotency-Key": command_id}, json=body)
        assert started.status_code == 200, started.text
        assert done.wait(5)
        status = local.get(f"/api/v1/system/updates/installs/{command_id}", headers=headers)
        assert status.status_code == 200, status.text
        assert status.json()["phase"] == "handoff"
        assert remote.get(f"/api/v1/system/updates/installs/{command_id}", headers=remote_headers).status_code == 403
