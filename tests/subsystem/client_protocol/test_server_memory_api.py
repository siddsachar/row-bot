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
    (root / "memory.current").write_text(f"{int(size * 0.97)}\n", encoding="ascii")
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

    # Railway (R1): its service's memory limit, and its dashboard.
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "railway")
    with client:
        _, headers = bootstrap(client)
        [problem] = problems(client, headers)
    assert problem["detail"].endswith(
        "needs about 3.2 GB. Raise the service's memory limit to 4 GB in Railway, or use cloud speech-to-text."
    )
    assert problem["links"] == [GUIDE, {"label": "Manage your service in Railway", "url": "https://railway.com/dashboard"}]

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


@pytest.mark.parametrize(("size", "words"), [(8_000_000_000, "8 GB"), (999_997_440, "1 GB")])
def test_railways_limits_are_said_as_railway_sets_them(cgroup, monkeypatch, size, words):
    # Railway sets limits in decimal GB: 8 GB, and its trial's 1 GB (as the kernel keeps it, in whole pages).
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "railway")
    ran_out_of_memory(cgroup, size)
    client, _, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        [problem] = problems(client, headers)
    assert problem["detail"].startswith(f"Your server has {words}. Local speech-to-text")


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

# What Railway gives every service (A2).
RAILWAY_IDS = {
    "RAILWAY_PROJECT_ID": "0f6e3c1a-5b2d-4c8e-9a71-2d4b6f8e1c30",
    "RAILWAY_SERVICE_ID": "7a1c9e42-3f5b-4d6a-8e20-b1c3d5e7f901",
    "RAILWAY_ENVIRONMENT_ID": "c2e4a6b8-1d3f-4a5c-9e7b-0f2d4c6e8a10",
}
SERVICE = (
    "https://railway.com/project/0f6e3c1a-5b2d-4c8e-9a71-2d4b6f8e1c30"
    "/service/7a1c9e42-3f5b-4d6a-8e20-b1c3d5e7f901"
)
ENVIRONMENT = "?environmentId=c2e4a6b8-1d3f-4a5c-9e7b-0f2d4c6e8a10"
TRIAL = {
    "note": "Railway's trial gives Row-Bot 1 GB: chats, memory and schedules work. "
    "Browser tasks and local speech-to-text need Railway's Hobby plan.",
    "upgrade_url": "https://railway.com/workspace/plans",
}


def test_on_railway_the_notice_links_the_services_settings_and_on_a_trial_names_the_plan(cgroup, monkeypatch):
    """A2: with the service's IDs, "Raise the memory limit" opens its settings. A4: on the trial's 1 GB,
    only Railway's Hobby plan gives more, so that is the step and the link."""
    monkeypatch.setenv("ROW_BOT_HOSTED_BY", "railway")
    for name, value in RAILWAY_IDS.items():
        monkeypatch.setenv(name, value)
    ran_out_of_memory(cgroup, 2_000_000_000)
    client, _, _ = client_app(remote=True)
    with client:
        _, headers = bootstrap(client)
        [problem] = problems(client, headers)
    assert problem["detail"].endswith(
        "needs about 3.2 GB. Raise the service's memory limit to 4 GB in Railway, or use cloud speech-to-text."
    )
    assert problem["links"] == [GUIDE, {"label": "Raise the memory limit", "url": SERVICE + "/settings" + ENVIRONMENT}]

    server_memory.dismiss()
    ran_out_of_memory(cgroup, 999_997_440)
    with client:
        _, headers = bootstrap(client)
        [problem] = problems(client, headers)
    assert problem["detail"] == (
        "Your server has 1 GB. Local speech-to-text together with the search model and browser needs about "
        "3.2 GB. Move to Railway's Hobby plan and raise the service's memory limit to 4 GB, or use cloud "
        "speech-to-text."
    )
    assert problem["links"] == [GUIDE, {"label": "Upgrade in Railway", "url": "https://railway.com/workspace/plans"}]
