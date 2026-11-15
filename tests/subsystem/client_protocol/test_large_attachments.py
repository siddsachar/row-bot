"""A file above the old 25 MB cap attaches and reads back like any other (U16)."""
from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from tests.subsystem.client_protocol.test_attachment_thumbnails import _conversation
from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

OLD_CAP = 25 * 1024 * 1024


def test_a_file_over_25_mb_attaches_and_reports_its_metadata(service):
    from row_bot.application.attachments import register_attachment

    data = b"order_id,region\n" + b"1,North\n" * ((OLD_CAP + 1024) // 8)
    with _client(service) as client:
        _, headers = bootstrap(client)
        conversation = _conversation(client, headers)
        # The desktop picker saves the chosen file, then the window reads its metadata.
        reference = register_attachment(conversation, "sales.csv", data)["attachment_ref"]
        metadata = client.get(f"/api/v1/attachments/{reference}/metadata", headers=headers)
        # A browser sends the same file through an upload session.
        started = client.post("/api/v1/uploads/sessions", headers=headers, json={
            "conversation_id": conversation, "name": "sales.csv", "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "batch_id": str(uuid4())})
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["size_bytes"] == len(data) > OLD_CAP
    assert started.status_code == 200, started.text
    assert started.json()["size_bytes"] == len(data)
