"""Files sent to the person's devices (F7): every one of the owner's signed-in devices sees the
delivery and downloads it with its session; without one, or once the copy expired, nothing is
served. The desktop window saves into Exports instead; a send that didn't go to a chat app can be
sent to the devices instead by the person's click.
"""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, _remote_client, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

PDF = b"%PDF-1.7\n" + b"x" * 4096


@pytest.fixture
def sent(service, tmp_path, monkeypatch):
    """A conversation with report.pdf sent to the devices, and a send that didn't go to Telegram."""
    from row_bot import file_deliveries, threads
    from row_bot.application import app_notices, conversation_creation
    from row_bot.application.attachments import register_attachment

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(file_deliveries, "_RECORDS", {})
    monkeypatch.setattr(file_deliveries, "_LOADED_ROOT", None)
    monkeypatch.setattr(app_notices, "app_notices", app_notices.NoticeJournal())
    exports = tmp_path / "Documents" / "Row-Bot"
    exports.mkdir(parents=True)
    monkeypatch.setattr(conversation_creation, "configured_workspace_root", lambda: exports.resolve())
    conversation = threads.create_thread("Report", seed_default_skills=False)
    register_attachment(conversation, "report.pdf", PDF)
    sources = file_deliveries.resolve(["report.pdf"], conversation)
    devices = file_deliveries.send_to_devices(conversation, sources)
    failed = file_deliveries.not_sent(conversation, sources, file_deliveries.TargetProblem(
        "too_large", "report.pdf (4 KB) is too big for Telegram (1 KB limit).", limit=1024),
        channel="telegram", label="Telegram", place="your Telegram chat")
    return {"conversation": conversation, "devices": devices, "failed": failed, "exports": exports,
            "deliveries": file_deliveries}


def test_a_signed_in_device_lists_and_downloads_what_was_sent(service, sent):
    devices = sent["devices"].delivery_id
    with _client(service) as client:
        _, headers = bootstrap(client)
        page = client.get("/api/v1/deliveries", params={"conversation_id": sent["conversation"]}, headers=headers)
        assert page.status_code == 200, page.text
        assert [item["delivery_id"] for item in page.json()["items"]] == [sent["failed"].delivery_id, devices]
        view = client.get(f"/api/v1/deliveries/{devices}", headers=headers).json()
        assert (view["target"], view["status"], view["files"][0]["name"], view["files"][0]["downloadable"]) == (
            "devices", "sent", "report.pdf", True)

        download = client.get(f"/api/v1/deliveries/{devices}/files/0", headers=headers)
        assert download.status_code == 200 and download.content == PDF
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["content-disposition"] == "attachment; filename*=UTF-8''report.pdf"
        assert download.headers["x-content-type-options"] == "nosniff"

        for missing in (f"/api/v1/deliveries/{devices}/files/1", f"/api/v1/deliveries/{devices}/files/x",
                        "/api/v1/deliveries/send-00000000/files/0", "/api/v1/deliveries/not-a-delivery",
                        f"/api/v1/deliveries/{sent['failed'].delivery_id}/files/0"):
            refused = client.get(missing, headers=headers)
            assert refused.status_code == 404 and refused.json()["code"] == "sent_file_unavailable", missing


def test_nothing_is_served_without_the_person_s_session(service, sent):
    url = f"/api/v1/deliveries/{sent['devices'].delivery_id}/files/0"
    with _client(service) as client:
        _, headers = bootstrap(client)
        for partial in ({}, {"Origin": headers["Origin"]}, {**headers, "X-CSRF-Token": "wrong"},
                        {**headers, "X-Client-Session": "00000000-0000-4000-8000-000000000000"}):
            refused = client.get(url, headers=partial)
            assert refused.status_code in {401, 403}, (partial, refused.status_code)
            assert refused.content != PDF

    remote, active = _remote_client(service)
    with remote as client:
        _, headers = bootstrap(client)
        assert client.get(url, headers=headers).content == PDF  # Another of the owner's devices.
        active["value"] = False  # That device is signed out.
        signed_out = client.get(url, headers=headers)
        assert signed_out.status_code == 401 and signed_out.content != PDF


def test_the_link_expires_with_its_copy(service, sent, monkeypatch):
    from row_bot.file_deliveries import DEVICE_KEEP_SECONDS

    delivery = sent["devices"]
    monkeypatch.setattr(sent["deliveries"], "_now", lambda: delivery.created_at + DEVICE_KEEP_SECONDS + 1)
    with _client(service) as client:
        _, headers = bootstrap(client)
        expired = client.get(f"/api/v1/deliveries/{delivery.delivery_id}/files/0", headers=headers)
        assert expired.status_code == 410 and expired.json()["code"] == "sent_file_expired"
        view = client.get(f"/api/v1/deliveries/{delivery.delivery_id}", headers=headers).json()
        assert view["status"] == "expired" and not view["files"][0]["downloadable"]


def test_the_desktop_window_saves_into_exports_and_other_devices_download_instead(service, sent):
    url = f"/api/v1/deliveries/{sent['devices'].delivery_id}/files/0/save"
    with _client(service) as client:
        _, headers = bootstrap(client)
        saved = client.post(url, headers=headers, json={})
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"file_name": "report.pdf", "folder": "Row-Bot › Exports"}
        assert (sent["exports"] / "Exports" / "report.pdf").read_bytes() == PDF
    remote, _active = _remote_client(service)
    with remote as client:
        _, headers = bootstrap(client)
        elsewhere = client.post(url, headers=headers, json={})
        assert elsewhere.status_code == 409 and elsewhere.json()["code"] == "desktop_only"


def test_a_send_that_didn_t_go_is_sent_to_the_devices_by_the_person(service, sent):
    failed = sent["failed"].delivery_id
    with _client(service) as client:
        _, headers = bootstrap(client)
        before = client.get(f"/api/v1/deliveries/{failed}", headers=headers).json()
        assert (before["status"], before["problem"], before["limit_bytes"], before["can_send_to_devices"]) == (
            "failed", "too_large", 1024, True)
        moved = client.post(f"/api/v1/deliveries/{failed}/to-devices", headers=headers, json={})
        assert moved.status_code == 200, moved.text
        copy = moved.json()
        assert (copy["target"], copy["status"], copy["files"][0]["downloadable"]) == ("devices", "sent", True)
        again = client.post(f"/api/v1/deliveries/{failed}/to-devices", headers=headers, json={})
        assert again.json()["delivery_id"] == copy["delivery_id"]  # Once, however often it's pressed.
        after = client.get(f"/api/v1/deliveries/{failed}", headers=headers).json()
        assert (after["sent_instead"], after["can_send_to_devices"]) == (copy["delivery_id"], False)
        refused = client.post(f"/api/v1/deliveries/{sent['devices'].delivery_id}/to-devices", headers=headers,
                              json={})
        assert refused.status_code == 404


def test_a_notice_links_to_its_conversation_only_when_it_has_one():
    from row_bot.api.v1 import schemas as dto
    from row_bot.application.app_notices import NoticeJournal

    journal = NoticeJournal()
    plain = journal.post(title="Workflow finished", message="Daily digest", requested=True)
    linked = journal.post(title="Row-Bot sent you report.pdf", message="", requested=True, source="deliveries",
                          conversation_id="chat-1")
    assert "conversation_id" not in plain.view()
    assert linked.view()["conversation_id"] == "chat-1"
    for notice in (plain, linked):
        dto.Notice.model_validate(notice.view())
