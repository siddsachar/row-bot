"""The client's background processes (F1, F6): every one listed with its conversation, one read
live, its output in pages, and Stop for any of them with no approval. Nothing runs: the
tracked-process owner is a fake."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from tests.subsystem.client_protocol.test_protocol_application import _client, service  # noqa: F401
from tests.subsystem.client_protocol.test_protocol_security import bootstrap

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


class _Tracked:
    def __init__(self, on_quiesced, on_output):
        self.on_quiesced, self.on_output = on_quiesced, on_output
        self.process = SimpleNamespace(pid=None)
        self.child_pid = None
        self.done = threading.Event()
        self.stop_requested = False
        self.code = ""
        self.exit_code = None

    def exit(self, code: int) -> None:
        if not self.done.is_set():
            self.exit_code = code
            self.on_quiesced(self)
            self.done.set()


@pytest.fixture
def processes(tmp_path, monkeypatch):
    from row_bot import background_processes
    from row_bot.developer import runtime

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    launched: list[_Tracked] = []

    def launch(_root, _argv, _command, *, on_quiesced=None, on_output=None, **_kw):
        launched.append(_Tracked(on_quiesced, on_output))
        return launched[-1]

    def stop(state):
        state.stop_requested = True
        state.exit(130)

    monkeypatch.setattr(runtime, "launch_tracked_process", launch)
    monkeypatch.setattr(runtime, "stop_tracked_process", stop)
    monkeypatch.setattr(runtime, "retire_tracked_process", lambda _state: True)
    yield SimpleNamespace(manager=background_processes, launched=launched, folder=str(tmp_path))
    background_processes.stop_all(timeout=1)


def test_every_process_is_listed_read_paged_and_stopped(service, processes):  # noqa: F811
    from row_bot import threads

    trip = threads.create_thread("Fix the login page", seed_default_skills=False)
    other = threads.create_thread("Research", seed_default_skills=False)
    server = processes.manager.start(trip, ["npm"], command="npm run dev", folder=processes.folder,
                                     name="Dev server", env={"PORT": "5173"})
    processes.launched[0].on_output("stdout", "VITE ready\n  Local: http://localhost:5173/\n" + "x" * 5000 + "\n")
    tests = processes.manager.start(other, ["pytest"], command="pytest -q", folder=processes.folder)
    processes.launched[1].on_output("stdout", "3 failed\n")
    processes.launched[1].exit(1)

    with _client(service) as client:
        _, headers = bootstrap(client)
        page = client.get("/api/v1/processes", headers=headers)
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["running"] == 1 and body["limit_per_conversation"] == 5 and body["limit_total"] == 12
        items = {item["process_id"]: item for item in body["items"]}
        assert [item["process_id"] for item in body["items"]] == [server.process_id, tests.process_id]
        assert {key: items[server.process_id][key] for key in (
            "conversation_id", "conversation_title", "name", "command", "status", "kind", "local_urls",
            "env_names")} == {
            "conversation_id": trip, "conversation_title": "Fix the login page", "name": "Dev server",
            "command": "npm run dev", "status": "running", "kind": "process",
            "local_urls": ["http://localhost:5173"], "env_names": ["PORT"]}
        assert "5173" not in str(items[server.process_id]["env_names"])
        assert (items[tests.process_id]["status"], items[tests.process_id]["exit_code"]) == ("failed", 1)

        mine = client.get("/api/v1/processes", params={"conversation_id": other}, headers=headers).json()
        assert [item["process_id"] for item in mine["items"]] == [tests.process_id]

        one = client.get(f"/api/v1/processes/{server.process_id}", headers=headers)
        assert one.status_code == 200 and one.json()["status"] == "running"

        first = client.get(f"/api/v1/processes/{server.process_id}/output", params={"limit": 1024},
                           headers=headers).json()
        assert first["text"].startswith("VITE ready") and first["start"] == 0 and first["end"] == 1024
        rest = client.get(f"/api/v1/processes/{server.process_id}/output", params={"after": first["end"]},
                          headers=headers).json()
        assert first["text"] + rest["text"] == server.log.text()[0]
        assert rest["end"] == rest["size"] and rest["running"] and not rest["earlier_dropped"]

        stopped = client.post(f"/api/v1/processes/{server.process_id}/stop", headers=headers)
        assert stopped.status_code == 200, stopped.text
        assert (stopped.json()["status"], stopped.json()["stopped_by"]) == ("stopped", "you")
        assert processes.launched[0].stop_requested
        again = client.post(f"/api/v1/processes/{server.process_id}/stop", headers=headers)
        assert again.status_code == 200 and again.json()["status"] == "stopped"

        for path in ("/api/v1/processes/proc-00000000", "/api/v1/processes/not-a-process",
                     "/api/v1/processes/proc-00000000/output"):
            missing = client.get(path, headers=headers)
            assert missing.status_code == 404 and missing.json()["code"] == "process_unavailable"
        assert client.post("/api/v1/processes/proc-00000000/stop", headers=headers).status_code == 404
        bad = client.get(f"/api/v1/processes/{server.process_id}/output", params={"after": -1}, headers=headers)
        assert bad.status_code == 422


def test_processes_need_a_signed_in_client(service, processes):  # noqa: F811
    with _client(service) as client:
        assert client.get("/api/v1/processes").status_code == 401
        assert client.post("/api/v1/processes/proc-00000000/stop").status_code in {401, 403}


def test_the_card_names_the_process_in_the_public_trace():
    from row_bot.application.conversation_traces import project_assistant_row_traces

    rows = project_assistant_row_traces([
        {"row": {"id": "assistant-1", "role": "assistant", "blocks": []},
         "tool_calls": [{"id": "call-1", "name": "start_process", "args": {"command": "npm run dev"}}]},
        {"row": {"id": "tool-1", "role": "tool", "tool_call_id": "call-1", "message_id": "tool-1",
                 "blocks": [{"type": "text", "text": "Approval: asked; approved by you\n"
                             "Background process proc-1a2b3c4d: running for 3s.\n$ npm run dev"}]}},
    ])

    item = rows[0]["traces"][0]["items"][0]
    assert item["specialization"]["kind"] == "background_process"
    assert item["specialization"]["process_id"] == "proc-1a2b3c4d"
