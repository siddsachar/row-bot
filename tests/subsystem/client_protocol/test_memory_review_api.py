"""Knowledge › Review memory through the API: an overview, a background run, its proposals, Apply (a backup
first, refused when stale) and Undo, each run bound to the window that started it."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from tests.helpers.memory_review import memory_review_stack, settle, snapshot
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem

API = "/api/v1/knowledge/review"


@pytest.fixture
def stack(tmp_path, monkeypatch, reload_for_data_dir):
    with memory_review_stack(tmp_path, monkeypatch, reload_for_data_dir) as built:
        yield built


def _post(client, headers, path, **body):
    return client.post(f"{API}{path}", headers=headers,
                       json={"client_session_id": headers["X-Client-Session"], **body})


def _command(client, headers, path, **body):
    identity = str(uuid4())
    return client.post(f"{API}{path}", headers={**headers, "Idempotency-Key": identity},
                       json={"command_id": identity, "client_session_id": headers["X-Client-Session"], **body})


def _done(client, headers, run: str) -> dict:
    settle()
    view = client.get(f"{API}/runs/{run}", headers=headers)
    assert view.status_code == 200, view.text
    assert view.json()["job"]["status"] == "completed", view.json()["job"]
    return view.json()


def test_a_review_runs_from_overview_to_apply_and_undo(stack) -> None:
    kg = stack.kg
    fixture = kg.save_entity("fact", "__MTEST_alpha", "Fixture memory made by a test run.")
    dentist = kg.save_entity("event", "Dentist", "Has a dentist appointment next week.")
    lesson = kg.save_entity("self_knowledge", "Check before done", "Run the tests before saying it is done.")
    client, _service, _active = client_app(remote=True)
    with client:
        _handshake, headers = bootstrap(client)
        overview = client.get(f"{API}/overview", headers=headers).json()
        assert (overview["memories"], overview["model_calls"], overview["recent"]) == (3, 1, [])
        assert overview["model"] == {"name": "fixture-model", "provider": "Local", "on_this_computer": True}
        assert client.get(f"{API}/overview", params={"import_batch": "nope"}, headers=headers).status_code == 422

        run = _post(client, headers, "/runs").json()["run_id"]
        view = _done(client, headers, run)
        assert view["counts"] == {"remove": 1, "merge": 0, "rewrite": 1, "split": 0, "keep": 1}
        page = client.get(f"{API}/runs/{run}/proposals", headers=headers).json()
        by_id = {item["memory_id"]: item for item in page["items"]}
        assert by_id[fixture["id"]]["reason"] == "Test data from development"
        assert by_id[lesson["id"]]["marks"] == ["lesson"] and by_id[lesson["id"]]["selected"] is False
        before = snapshot(kg.DB_PATH)

        chosen = [{"proposal_id": item["proposal_id"]} for item in page["items"] if item["selected"]]
        key = str(uuid4())
        mismatch = client.post(f"{API}/runs/{run}/apply", headers={**headers, "Idempotency-Key": key},
                               json={"command_id": str(uuid4()), "client_session_id": headers["X-Client-Session"],
                                     "revision": page["revision"], "choices": chosen})
        assert mismatch.status_code == 409 and mismatch.json()["code"] == "idempotency_mismatch"
        applied = _command(client, headers, f"/runs/{run}/apply", revision=page["revision"], choices=chosen)
        assert applied.status_code == 200, applied.text
        receipt = applied.json()
        assert (receipt["removed"], receipt["rewritten"], receipt["status"]) == (1, 1, "applied")
        assert (Path(kg.DB_PATH).parent / receipt["backup"]).is_file()
        assert kg.get_entity(fixture["id"]) is None
        assert "next week" not in kg.get_entity(dentist["id"])["description"]

        # Undo works from another signed-in window, which lists the review until then.
        _other, other = bootstrap(client)
        recent = client.get(f"{API}/overview", headers=other).json()["recent"]
        assert [item["run_id"] for item in recent] == [run]
        undone = _command(client, other, f"/runs/{run}/undo")
        assert undone.status_code == 200 and undone.json()["status"] == "undone"
        assert snapshot(kg.DB_PATH) == before


def test_a_run_belongs_to_its_window_and_refusals_are_named(stack) -> None:
    kg = stack.kg
    memory = kg.save_entity("project", "Assistant setup", "Uses Thoth as the desktop assistant.")
    client, _service, active = client_app(remote=True)
    with client:
        _handshake, headers = bootstrap(client)
        _other_handshake, other = bootstrap(client)
        run = _post(client, headers, "/runs").json()["run_id"]
        page_view = _done(client, headers, run)
        for answer in (client.get(f"{API}/runs/{run}", headers=other),
                       client.get(f"{API}/runs/{run}/proposals", headers=other),
                       _post(client, other, f"/runs/{run}/cancel"),
                       client.delete(f"{API}/runs/{run}", headers=other)):
            assert answer.status_code == 404 and answer.json()["code"] == "not_found"
        assert client.post(f"{API}/runs", headers=headers,
                           json={"client_session_id": other["X-Client-Session"]}).status_code == 422

        page = client.get(f"{API}/runs/{run}/proposals", headers=headers).json()
        kg.update_entity(memory["id"], "Uses a new desktop assistant.")
        stale = _command(client, headers, f"/runs/{run}/apply", revision=page_view["revision"],
                         choices=[{"proposal_id": page["items"][0]["proposal_id"]}])
        assert stale.status_code == 409 and stale.json()["code"] == "review_stale"
        assert client.get(f"{API}/runs/{run}", headers=headers).json()["stale"] is True
        kept = _command(client, headers, f"/runs/{run}/apply", revision=page_view["revision"],
                        choices=[{"proposal_id": page["items"][0]["proposal_id"], "remove": True}])
        # Removing what the review keeps needs a long record or a lesson, confirmed by its name.
        assert kept.status_code == 422 and kept.json()["code"] == "invalid_command"

        active["value"] = False
        assert client.get(f"{API}/runs/{run}", headers=headers).status_code == 401
        active["value"] = True
        discarded = client.delete(f"{API}/runs/{run}", headers=headers)
        assert discarded.status_code == 200 and discarded.json() == {"cancelled": True}
        gone = client.get(f"{API}/runs/{run}", headers=headers)
        assert gone.status_code == 410 and gone.json()["code"] == "review_expired"
