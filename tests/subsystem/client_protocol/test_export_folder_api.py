"""Conversation exports saved into Exports on this computer (B238).

When the desktop window cannot show its Save dialog, the local owner's export
is written by the server into the workspace folder's Exports folder.
"""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

import os
import stat

import pytest

from row_bot.application import export_folder
from tests.subsystem.client_protocol.test_protocol_application import (
    _client,
    _command,
    service,
)
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

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
    monkeypatch.setattr(export_folder, "_show_in_folder", shown.append)
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


def test_a_remote_session_cannot_save_or_show_files_here():
    client, _service, _active = client_app(remote=True)
    with client:
        _handshake, headers = bootstrap(client)
        for path, body in (("/api/v1/attachments/c:00000000-0000-4000-8000-000000000000/save", {}),
                           ("/api/v1/exports/reveal", {"file_name": "export.md"})):
            response = client.post(path, headers=headers, json=body)
            assert response.status_code == 403 and response.json()["code"] == "owner_local_only"


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
