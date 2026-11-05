"""Conversation exports saved into the workspace's Exports folder (B238).

When the desktop window cannot show its Save dialog, the export is written by
the server into the workspace folder's Exports folder. Any signed-in device
may save one there; Show in folder opens the file manager on the computer
Row-Bot runs on, so only the desktop app there offers it.
"""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

import os
import stat

import pytest

from row_bot.application import export_folder
from tests.subsystem.client_protocol.test_protocol_application import (
    _client,
    _command,
    _remote_client,
    service,
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from row_bot.application import conversation_creation

    root = tmp_path / "Documents" / "Row-Bot"
    root.mkdir(parents=True)
    monkeypatch.setattr(conversation_creation, "configured_workspace_root", lambda: root.resolve())
    return root.resolve()


def test_the_local_owner_saves_an_export_into_exports_and_shows_it(service, workspace, monkeypatch):
    from row_bot.application.attachments import register_attachment

    shown = []
    monkeypatch.setattr(export_folder, "show_in_folder", shown.append)
    with _client(service) as client:
        _, headers = bootstrap(client)
        created = _command(client, headers, "conversation.create", {"title": "Export owner"})
        assert created.status_code == 200, created.text
        attachment = register_attachment(created.json()["conversation_id"], "con.md", b"# Export\n")
        url = f"/api/v1/attachments/{attachment['attachment_ref']}/save"

        first = client.post(url, headers=headers, json={})
        assert first.status_code == 200, first.text
        # A device name is never used as a file name, and no path is returned.
        assert first.json() == {"file_name": "export-con.md", "folder": "Row-Bot › Exports"}
        assert str(workspace) not in first.text
        saved = workspace / "Exports" / "export-con.md"
        assert saved.read_bytes() == b"# Export\n"
        if os.name != "nt":
            assert stat.S_IMODE(saved.stat().st_mode) == 0o600
        # Saving again never replaces the first copy.
        again = client.post(url, headers=headers, json={})
        assert again.json()["file_name"] == "export-con 2.md"
        assert saved.read_bytes() == b"# Export\n"

        reveal = client.post("/api/v1/exports/reveal", headers=headers, json={"file_name": "export-con.md"})
        assert reveal.status_code == 200 and reveal.json() == {"status": "opened"}
        assert shown == [saved]
        for name in ("../export-con.md", "missing.md", "Exports"):
            refused = client.post("/api/v1/exports/reveal", headers=headers, json={"file_name": name})
            assert refused.json() == {"status": "not_found"}, name
        assert shown == [saved]


def test_another_device_saves_into_exports_but_the_file_manager_stays_on_that_computer(
        service, workspace, monkeypatch):
    from row_bot.application.attachments import register_attachment

    shown = []
    monkeypatch.setattr(export_folder, "show_in_folder", shown.append)
    remote, active = _remote_client(service)
    with remote as client:
        _, headers = bootstrap(client)
        created = _command(client, headers, "conversation.create", {"title": "Export elsewhere"})
        assert created.status_code == 200, created.text
        attachment = register_attachment(created.json()["conversation_id"], "con.md", b"# Export\n")
        saved = client.post(f"/api/v1/attachments/{attachment['attachment_ref']}/save", headers=headers, json={})
        assert saved.status_code == 200, saved.text
        assert saved.json() == {"file_name": "export-con.md", "folder": "Row-Bot › Exports"}
        assert (workspace / "Exports" / "export-con.md").read_bytes() == b"# Export\n"
        reveal = client.post("/api/v1/exports/reveal", headers=headers, json={"file_name": "export-con.md"})
        assert reveal.status_code == 409 and reveal.json()["code"] == "desktop_only"
        active["value"] = False
        signed_out = client.post(f"/api/v1/attachments/{attachment['attachment_ref']}/save", headers=headers, json={})
        assert signed_out.status_code == 401
    assert shown == []


def test_export_names_are_made_safe_and_stay_inside_exports(workspace):
    def save(name: str) -> str:
        return export_folder.save_export(name, b"x", validate=lambda: None)["file_name"]

    assert save("../../escape?.md") == "escape.md"
    assert save("..\\..\\windows.md") == "windows.md"
    assert save(".hidden") == "hidden"
    assert save("") == "export"
    assert save("Résumé 1.pdf") == "Résumé 1.pdf"
    long = save("a" * 300 + ".pdf")
    assert long.endswith(".pdf") and len(long) <= 120
    assert sorted(path.name for path in (workspace / "Exports").iterdir()) == sorted(
        ["escape.md", "windows.md", "hidden", "export", "Résumé 1.pdf", long])
    assert sorted(path.name for path in workspace.iterdir()) == ["Exports"]

    def refuse() -> None:
        raise PermissionError("signed out")

    with pytest.raises(PermissionError):
        export_folder.save_export("late.md", b"x", validate=refuse)
    assert not (workspace / "Exports" / "late.md").exists()
