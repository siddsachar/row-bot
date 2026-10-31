"""The server's memory through the client's API (H12, U8).

The handshake says the server's memory limit next to its place, so Voice and Documents say what fits;
the desktop has none. After a restart for lack of memory, the one attention list (Home's Needs you,
Monitor, the sidebar) holds the notice in the person's words with how to resize, until it is dismissed.
A folder of fake cgroup files stands in for the container's.
"""

from __future__ import annotations

import pytest

from row_bot import server_memory
from tests.subsystem.client_protocol.test_protocol_security import bootstrap, client_app

pytestmark = pytest.mark.subsystem

GB = 2**30
TITLE = "Row-Bot restarted because the server ran out of memory"
GUIDE = {"label": "How to resize", "url": "https://row-bot.ai/docs/operations/hosting#memory"}


@pytest.fixture
def cgroup(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ROW_BOT_HOSTED_BY", raising=False)
    monkeypatch.setattr(server_memory, "WATCH_SECONDS", 3600.0)
    root = tmp_path / "cgroup"
    root.mkdir()
    monkeypatch.setattr(server_memory, "CGROUP_ROOT", root)
    yield root
    server_memory.stopped()


def limit(root, value: int | str) -> None:
    (root / "memory.max").write_text(f"{value}\n", encoding="ascii")


def ran_out_of_memory(root, size: int = 2 * GB) -> None:
    """A run whose use came within 10% of the limit, stopped by the kernel, then the next start."""
    limit(root, size)
    (root / "memory.peak").write_text(f"{int(size * 0.97)}\n", encoding="ascii")
    server_memory.started()
    server_memory.started()


def problems(client, headers) -> list[dict]:
    response = client.get("/api/v1/monitor/attention", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["problems"]


def test_the_handshake_says_the_servers_memory_limit_beside_its_place(cgroup, monkeypatch):
    limit(cgroup, 2 * GB)
    client, _, _ = client_app(remote=True)
    with client:
        data, _ = bootstrap(client)
    assert data["place"] == {"kind": "server", "host_name": None}
    assert data["memory_limit_bytes"] == 2 * GB

    limit(cgroup, "max")  # A container without a limit.
    with client:
        assert bootstrap(client)[0]["memory_limit_bytes"] is None
    monkeypatch.setattr(server_memory, "CGROUP_ROOT", None)  # The desktop.
    local, _, _ = client_app()
    with local:
        assert bootstrap(local)[0]["memory_limit_bytes"] is None


def test_after_an_out_of_memory_restart_the_person_reads_why_and_how_to_resize(cgroup, monkeypatch):
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")
    ran_out_of_memory(cgroup)
    client, _, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        [problem] = problems(client, headers)
    assert problem == {
        "id": "memory:restarted",
        "title": TITLE,
        "detail": "Your server has 2 GB. Local speech-to-text together with the search model and browser needs "
                  "about 3.2 GB. Make the pod 4 GB in PikaPods, or use cloud speech-to-text.",
        "place": "memory",
        "fix": None,
        "links": [GUIDE, {"label": "Manage your pod in PikaPods", "url": "https://www.pikapods.com/pods"}],
    }

    # No known host: the server, and only the guide.
    monkeypatch.delenv("ROW_BOT_HOSTED_BY")
    with client:
        _, headers = bootstrap(client)
        [problem] = problems(client, headers)
    assert problem["detail"].endswith("needs about 3.2 GB. Give the server 4 GB, or use cloud speech-to-text.")
    assert problem["links"] == [GUIDE]


def test_a_big_server_that_ran_out_is_told_to_grow_not_to_become_4_gb(cgroup, monkeypatch):
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "pikapods")
    ran_out_of_memory(cgroup, 4 * GB)
    client, _, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        [problem] = problems(client, headers)
    assert problem["detail"].startswith("Your server has 4 GB.")
    assert problem["detail"].endswith("Make the pod bigger in PikaPods, or use cloud speech-to-text.")


def test_dismissing_the_notice_keeps_it_away_until_another_such_restart(cgroup):
    ran_out_of_memory(cgroup)
    client, _, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        assert [problem["title"] for problem in problems(client, headers)] == [TITLE]

        response = client.delete("/api/v1/monitor/memory-notice", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"dismissed": True}
        assert problems(client, headers) == []
        assert client.delete("/api/v1/monitor/memory-notice", headers=headers).json() == {"dismissed": False}

        server_memory.stopped()
        server_memory.started()  # A clean restart.
        assert problems(client, headers) == []
        ran_out_of_memory(cgroup)
        assert [problem["title"] for problem in problems(client, headers)] == [TITLE]
