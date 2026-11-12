"""Knowledge › Import memories through the API: chunked files, a scan, a preview, Apply and Undo,
each bound to the window that started it, with nothing saved before Apply."""
from __future__ import annotations

from uuid import uuid4

import pytest

from tests.fixtures.memory_exports import chatgpt_simple, zip_bytes
from tests.helpers.memory_import import memory_import_stack, settle
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem

API = "/api/v1/knowledge/import"
ANSWER = """```
preference | unknown | Recordings of the app are made in light theme
skill | 2026-02-01 | Plays the cello in a community orchestra
```"""


@pytest.fixture
def stack(tmp_path, monkeypatch, reload_for_data_dir):
    with memory_import_stack(tmp_path, monkeypatch, reload_for_data_dir) as built:
        yield built


def _post(client, headers, path, **body):
    return client.post(f"{API}{path}", headers=headers,
                       json={"client_session_id": headers["X-Client-Session"], **body})


def _command(client, headers, path, **body):
    identity = str(uuid4())
    return client.post(f"{API}{path}", headers={**headers, "Idempotency-Key": identity},
                       json={"command_id": identity, "client_session_id": headers["X-Client-Session"], **body})


def _send(client, headers, batch: str, name: str, data: bytes) -> str:
    begun = _post(client, headers, f"/batches/{batch}/files", name=name, size_bytes=len(data))
    assert begun.status_code == 200, begun.text
    file_id, step = begun.json()["file_id"], begun.json()["chunk_bytes"]
    for offset in range(0, len(data), step):
        sent = client.put(f"{API}/batches/{batch}/files/{file_id}/chunks", params={"offset": offset},
                          headers=headers, content=data[offset:offset + step])
        assert sent.status_code == 200, sent.text
    return file_id


def _done(client, headers, batch: str) -> dict:
    settle()
    view = client.get(f"{API}/batches/{batch}", headers=headers)
    assert view.status_code == 200, view.text
    assert view.json()["job"]["status"] == "completed", view.json()["job"]
    return view.json()


def test_an_import_runs_from_files_and_text_to_apply_and_undo(stack) -> None:
    kg = stack.kg
    dark = kg.save_entity("preference", "App recordings theme", "Recordings of the app are made in dark theme.")
    stack.close[dark["id"]] = 0.85
    client, _service, _active = client_app(remote=True)
    with client:
        _handshake, headers = bootstrap(client)
        sources = client.get(f"{API}/sources", headers=headers).json()
        assert {"chatgpt", "claude", "other"} <= {item["id"] for item in sources["sources"]}
        assert "category | date | fact" in sources["prompt"]
        assert sources["model"] == {"name": "fixture-model", "provider": "Local", "on_this_computer": True}
        assert (sources["conversation_limit"], sources["character_limit"]) == (200, 400_000)

        batch = _post(client, headers, "/batches").json()["batch_id"]
        export = zip_bytes({"conversations.json": [chatgpt_simple("c1", 1762900000.0, "how do I graft apples")]})
        _send(client, headers, batch, "chatgpt-export.zip", export)
        assert _post(client, headers, f"/batches/{batch}/text", text=ANSWER).json()["texts"] == 1
        assert _post(client, headers, f"/batches/{batch}/scan").status_code == 200
        view = _done(client, headers, batch)
        assert view["conversations"]["available"] == 1 and view["found"] == 2

        assert _post(client, headers, f"/batches/{batch}/conversations").status_code == 200
        view = _done(client, headers, batch)
        assert view["conversations"]["read"] == 1 and view["found"] == 3
        items = client.get(f"{API}/batches/{batch}/candidates", headers=headers).json()["items"]
        assert {item["origin"] for item in items} == {"export", "conversation"}

        chosen = [{"candidate_id": item["candidate_id"]} for item in items]
        chosen[0]["category"] = "fact"
        assert _post(client, headers, f"/batches/{batch}/preview", items=chosen).status_code == 200
        _done(client, headers, batch)
        preview = client.get(f"{API}/batches/{batch}/preview", headers=headers).json()
        assert sorted(item["action"] for item in preview["items"]) == ["add", "add", "supersede"]
        assert len(kg.list_entities(limit=50)) == 1  # nothing saved before Apply

        key = str(uuid4())
        mismatch = client.post(f"{API}/batches/{batch}/apply", headers={**headers, "Idempotency-Key": key},
                               json={"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
                                     "preview_revision": preview["revision"]})
        assert mismatch.status_code == 409 and mismatch.json()["code"] == "idempotency_mismatch"
        applied = _command(client, headers, f"/batches/{batch}/apply", preview_revision=preview["revision"])
        assert applied.status_code == 200, applied.text
        receipt = applied.json()
        assert (receipt["added"], receipt["superseded"], receipt["status"]) == (2, 1, "applied")
        assert len(kg.list_entities(limit=50)) == 4
        again = _command(client, headers, f"/batches/{batch}/apply", preview_revision=preview["revision"])
        assert again.json()["command_id"] == receipt["command_id"] and len(kg.list_entities(limit=50)) == 4

        # Undo works from another signed-in window, and lists the import until then.
        _other, other = bootstrap(client)
        recent = client.get(f"{API}/sources", headers=other).json()["recent"]
        assert [item["batch_id"] for item in recent] == [batch]
        undone = _command(client, other, f"/batches/{batch}/undo")
        assert undone.status_code == 200 and undone.json()["status"] == "undone"
        assert [entity["id"] for entity in kg.list_entities(limit=50)] == [dark["id"]]


def test_a_batch_belongs_to_the_window_that_started_it_and_refusals_are_named(stack) -> None:
    client, _service, active = client_app(remote=True)
    with client:
        _handshake, headers = bootstrap(client)
        _other_handshake, other = bootstrap(client)
        batch = _post(client, headers, "/batches").json()["batch_id"]
        huge = _post(client, headers, f"/batches/{batch}/files", name="export.zip",
                     size_bytes=stack.service.FILE_MAX_BYTES + 1)
        assert huge.status_code == 413 and huge.json()["code"] == "import_file_too_large"
        assert _post(client, headers, f"/batches/{batch}/files", name="../x.zip", size_bytes=4).status_code == 422
        file_id = _post(client, headers, f"/batches/{batch}/files", name="export.zip",
                        size_bytes=4).json()["file_id"]
        oversized = client.put(f"{API}/batches/{batch}/files/{file_id}/chunks", params={"offset": 0},
                               headers=headers, content=bytes(stack.service.CHUNK_BYTES + 1))
        assert oversized.status_code == 413
        for answer in (client.get(f"{API}/batches/{batch}", headers=other),
                       client.get(f"{API}/batches/{batch}/candidates", headers=other),
                       client.put(f"{API}/batches/{batch}/files/{file_id}/chunks", params={"offset": 0},
                                  headers=other, content=b"abcd"),
                       _post(client, other, f"/batches/{batch}/scan"),
                       client.delete(f"{API}/batches/{batch}", headers=other)):
            assert answer.status_code == 404 and answer.json()["code"] == "not_found"
        early = _post(client, headers, f"/batches/{batch}/scan")
        assert early.status_code == 409 and early.json()["code"] == "upload_incomplete"
        stale = client.get(f"{API}/batches/{batch}/preview", headers=headers)
        assert stale.status_code == 409 and stale.json()["code"] == "import_preview_stale"
        # Another window's id in the body is refused.
        assert client.post(f"{API}/batches", headers=headers,
                           json={"client_session_id": other["X-Client-Session"]}).status_code == 422
        active["value"] = False
        assert client.get(f"{API}/batches/{batch}", headers=headers).status_code == 401
        active["value"] = True
        discarded = client.delete(f"{API}/batches/{batch}", headers=headers)
        assert discarded.status_code == 200 and discarded.json() == {"cancelled": True}
        gone = client.get(f"{API}/batches/{batch}", headers=headers)
        assert gone.status_code == 410 and gone.json()["code"] == "import_expired"
    assert list(stack.root.iterdir()) == []
