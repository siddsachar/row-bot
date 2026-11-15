"""D1 the activity log over HTTP: Settings › Activity pages and filters it, Monitor counts today, the CSV
the person saves is safe to open in a spreadsheet, and only a signed-in session clears it."""

# ruff: noqa: F401, F811 -- imported pytest fixtures are requested by name.

from __future__ import annotations

import csv
import io
import time

import pytest

from row_bot import activity_log
from tests.subsystem.client_protocol.test_protocol_application import _client, service
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = pytest.mark.subsystem

_ASKED = {"stage": "asking", "level": "approve"}


def _decided(outcome: str, by: str = "person", **fields):
    return {"stage": "decided", "level": "approve", "outcome": outcome, "by": by, **fields}


@pytest.fixture
def log(tmp_path, monkeypatch, service):
    from row_bot import threads

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    chat = threads.create_thread("Tidy my files", thread_id="chat-activity", seed_default_skills=False)
    now = time.time()
    calls = [
        activity_log.ToolCall(call_id="c1", tool="read_notes", thread_id=chat, at=now - 50),
        activity_log.ToolCall(call_id="c2", tool="delete_file", thread_id=chat, at=now - 40, decisions=(
            _ASKED, _decided("allow", description="Delete old.txt"))),
        activity_log.ToolCall(call_id="c3", tool="delete_file", thread_id=chat, at=now - 30, decisions=(
            _ASKED, _decided("deny", description="=HYPERLINK(\"https://example.test\")"))),
        activity_log.ToolCall(call_id="c4", tool="run_command", thread_id=chat, at=now - 20, level="block",
                              decisions=(_decided("block", "block"),)),
        activity_log.ToolCall(call_id="c5", tool="read_notes", thread_id="other-chat", at=now - 10,
                              surface="proactive"),
    ]
    for item in calls:
        activity_log.record(item)
    assert activity_log.flush()
    yield chat
    activity_log.flush()


def test_the_log_pages_newest_first_and_filters(service, log):
    with _client(service) as client:
        _, headers = bootstrap(client)
        first = client.get("/api/v1/activity?limit=2", headers=headers)
        assert first.status_code == 200, first.text
        page = first.json()
        assert [item["tool"] for item in page["items"]] == ["read_notes", "run_command"]
        assert page["items"][1]["conversation_title"] == "Tidy my files"
        rest = client.get(f"/api/v1/activity?limit=10&before={page['next_before']}", headers=headers).json()
        assert [item["outcome"] for item in rest["items"]] == ["denied", "approved_by_you", "ran"]
        assert rest["next_before"] is None

        def outcomes(query: str) -> list[str]:
            response = client.get(f"/api/v1/activity?{query}", headers=headers)
            assert response.status_code == 200, response.text
            return [item["outcome"] for item in response.json()["items"]]

        assert outcomes("outcome=needed") == ["denied", "approved_by_you"]
        assert outcomes("outcome=denied") == ["denied"]
        assert outcomes("outcome=blocked") == ["blocked"]
        assert outcomes("surface=proactive") == ["ran"]
        assert outcomes(f"conversation={log}&q=old.txt") == ["approved_by_you"]
        for bad in ("limit=0", "limit=201", "before=-1", "surface=everywhere", "outcome=ran"):
            assert client.get(f"/api/v1/activity?{bad}", headers=headers).status_code == 422


def test_monitor_counts_today(service, log):
    with _client(service) as client:
        _, headers = bootstrap(client)
        today = client.get(f"/api/v1/activity/summary?since={time.time() - 3600}", headers=headers).json()
        assert (today["total"], today["asked"], today["denied"]) == (5, 2, 1)
        later = client.get(f"/api/v1/activity/summary?since={time.time() + 60}", headers=headers).json()
        assert later["total"] == 0


def test_the_csv_export_is_a_file_to_save_and_never_runs_a_formula(service, log):
    with _client(service) as client:
        _, headers = bootstrap(client)
        offer = client.post("/api/v1/activity/export", headers=headers, json={"outcome": "needed"})
        assert offer.status_code == 200, offer.text
        assert offer.json()["file_name"].startswith("row-bot-activity-") and offer.json()["file_name"].endswith(".csv")
        saved = client.get(f"/api/v1/attachments/{offer.json()['reference']}", headers=headers)
        assert saved.status_code == 200
        rows = list(csv.reader(io.StringIO(saved.content.decode("utf-8-sig"))))
        assert rows[0] == ["Time", "Chat", "Where", "Tool", "App", "What", "Level", "Outcome", "Decided by", "Reason"]
        assert [row[7] for row in rows[1:]] == ["You said no", "You approved"]
        assert rows[1][5].startswith("'=HYPERLINK")
        assert rows[1][1] == "Tidy my files" and rows[1][8] == "You"
        assert client.post("/api/v1/activity/export", headers=headers, json={"outcome": "everything"}).status_code == 422


def test_only_a_signed_in_session_clears_the_log(service, log):
    with _client(service) as client:
        refused = client.delete("/api/v1/activity", headers={"Origin": "http://localhost"})
        assert refused.status_code in {401, 403}
        assert len(activity_log.query(limit=50)[0]) == 5

        _, headers = bootstrap(client)
        cleared = client.delete("/api/v1/activity", headers=headers)
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["removed"] == 5
        assert client.get("/api/v1/activity", headers=headers).json()["items"] == []
