"""One way to save a file (5.3.3): every Download and Export button asks for an offer of its file, then
the desktop app's Save dialog, a browser download or (while the window reconnects) Exports reads it.
An offer is read once, only by the session that asked for it, and only for a few minutes.
"""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

import base64

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, _remote_client, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

CODE = "print('tides')\n".encode()


def _offer(client, headers, name: str = "tides.py", data: bytes = CODE):
    return client.post("/api/v1/saves", headers=headers, json={"source": {
        "kind": "content", "name": name, "content_base64": base64.b64encode(data).decode()}})


def test_an_offer_is_read_once_by_the_session_that_asked_for_it(service):
    with _client(service) as client:
        _, headers = bootstrap(client)
        offer = _offer(client, headers)
        assert offer.status_code == 200, offer.text
        reference = offer.json()["reference"]
        assert offer.json()["file_name"] == "tides.py" and reference.startswith("save:")

        remote, _active = _remote_client(service)
        with remote as other:
            _, other_headers = bootstrap(other)
            # Another device of the owner's never reads this session's offer, nor spends it.
            assert other.get(f"/api/v1/attachments/{reference}", headers=other_headers).status_code == 404

        saved = client.get(f"/api/v1/attachments/{reference}", headers=headers)
        assert saved.status_code == 200 and saved.content == CODE
        assert saved.headers["content-disposition"] == "attachment; filename*=UTF-8''tides.py"
        assert client.get(f"/api/v1/attachments/{reference}", headers=headers).status_code == 404


def test_names_are_made_plain_and_bad_content_is_refused(service):
    with _client(service) as client:
        _, headers = bootstrap(client)
        named = _offer(client, headers, name="Q3 report: final.pdf")
        assert named.status_code == 200, named.text
        assert named.json()["file_name"] == "Q3 report- final.pdf"
        bad = client.post("/api/v1/saves", headers=headers, json={"source": {
            "kind": "content", "name": "x.txt", "content_base64": "not base64!"}})
        assert bad.status_code == 422
        unknown = client.post("/api/v1/saves", headers=headers, json={"source": {"kind": "anything"}})
        assert unknown.status_code == 422


def test_an_offer_is_gone_after_a_few_minutes(service, monkeypatch):
    from row_bot.application import save_offers

    with _client(service) as client:
        _, headers = bootstrap(client)
        reference = _offer(client, headers).json()["reference"]
        later = save_offers.time.monotonic() + save_offers.TTL_SECONDS + 1
        monkeypatch.setattr(save_offers.time, "monotonic", lambda: later)
        assert client.get(f"/api/v1/attachments/{reference}", headers=headers).status_code == 404


def test_one_session_keeps_only_its_newest_offers():
    from row_bot.application import save_offers
    from row_bot.application.attachments import AttachmentError

    references = [save_offers.offer("session-a", "tides.py", CODE)["reference"]
                  for _ in range(save_offers.MAX_PER_SESSION + 1)]
    other = save_offers.offer("session-b", "tides.py", CODE)["reference"]
    with pytest.raises(AttachmentError):
        save_offers.take(references[0], "session-a")
    assert save_offers.take(references[-1], "session-a")[1] == CODE
    assert save_offers.take(other, "session-b")[1] == CODE  # Another session's offers are its own.
