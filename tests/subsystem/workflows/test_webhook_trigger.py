"""Webhook triggers: only a request with the workflow's own secret runs it."""
from __future__ import annotations

import pytest

from tests.fixtures.tasks import fresh_tasks_module

pytestmark = pytest.mark.subsystem


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    module = fresh_tasks_module(tmp_path, monkeypatch)
    started = []
    monkeypatch.setattr(module, "run_task_background", lambda task_id, *a, **k: started.append(task_id))
    monkeypatch.setattr(module, "_prepare_task_thread", lambda task: "thread-1")
    module.started = started
    return module


def _webhook_task(tasks, secret):
    task_id = tasks.create_task(name="Hook", prompts=["Summarize"], enabled=True)
    tasks.update_task(task_id, trigger={"type": "webhook", "secret": secret})
    return task_id


def test_only_the_workflows_own_secret_runs_it(tasks):
    task_id = _webhook_task(tasks, "s" * 32)
    assert tasks.handle_webhook(task_id, "wrong")["status"] == "error"
    assert tasks.handle_webhook(task_id, None)["status"] == "error"
    assert tasks.handle_webhook(task_id, "s" * 32)["status"] == "ok"
    assert tasks.started == [task_id]


def test_a_webhook_without_a_secret_never_runs(tasks):
    """An empty stored secret used to accept any caller."""
    task_id = _webhook_task(tasks, "")
    for attempt in (None, "", "anything"):
        result = tasks.handle_webhook(task_id, attempt)
        assert result["status"] == "error"
    assert tasks.started == []


def test_the_secret_can_travel_in_a_header_and_older_addresses_keep_working(tasks):
    """B132: the secret had to travel in the query string, where proxies and
    logs keep it. A header carries it now; addresses copied before still work."""
    secret = "s" * 32
    header = tasks.WEBHOOK_SECRET_HEADER
    assert tasks.webhook_request_secret({header: secret}, {}) == secret
    assert tasks.webhook_request_secret({header.lower(): secret}, {}) == secret
    assert tasks.webhook_request_secret({"Authorization": f"Bearer {secret}"}, {}) == secret
    # An address copied before the header existed keeps working.
    assert tasks.webhook_request_secret({}, {"secret": secret}) == secret
    # Two different secrets in one request never run anything.
    assert tasks.webhook_request_secret({header: secret}, {"secret": "other"}) is None
    assert tasks.webhook_request_secret({}, {}) is None
    task_id = _webhook_task(tasks, secret)
    assert tasks.handle_webhook(task_id, tasks.webhook_request_secret({header: secret}, {}))["status"] == "ok"
