"""Workflows with apps: templates are created switched off and scheduled, never with a webhook, and only
once their app is connected. Each is one message, run as a chat turn with every tool a chat has (F31).
Fakes only."""
from __future__ import annotations

import pytest

from row_bot.application import client_integrations as api
from row_bot.application.client_platform import ClientPlatformError
from row_bot.integrations import scope

pytestmark = pytest.mark.subsystem

GITHUB = {"id": "mcp:github", "kind": "mcp", "server": "GitHub", "tools": [], "app": {"id": "github"},
          "lifecycle": "installed", "readiness": "ready"}
NOTION = {"id": "mcp:notion", "kind": "mcp", "server": "Notion", "tools": [], "app": {"id": "notion"},
          "lifecycle": "installed", "readiness": "needs_sign_in"}
GMAIL = {"id": "builtin:account:google", "kind": "builtin", "tools": ["gmail"], "app": {"id": "gmail"},
         "lifecycle": "installed", "readiness": "ready"}


@pytest.fixture
def tasks(tmp_path, monkeypatch, reload_for_data_dir):
    module, = reload_for_data_dir(tmp_path / "data", "row_bot.tasks")
    monkeypatch.setattr(scope, "_items", lambda strict=False: [GITHUB, NOTION, GMAIL])
    return module


def test_templates_say_which_apps_are_connected_and_offer_connect_otherwise(tasks):
    listed = {row["id"]: row for row in api.workflow_templates()["items"]}
    assert set(listed) == {"web_daily_brief", "weekly_chat_summary", "github_pr_digest", "linear_triage",
                           "notion_weekly_summary"}
    assert listed["github_pr_digest"]["apps"][0] | {"icon": ""} == {"app_id": "github", "name": "GitHub", "icon": "",
                                                                   "connected": True}
    assert listed["notion_weekly_summary"]["apps"][0]["connected"] is False  # Set up, but not signed in.
    assert listed["linear_triage"]["apps"][0]["connected"] is False
    with pytest.raises(ClientPlatformError, match="app_not_connected"):
        api.use_workflow_template("linear_triage")
    with pytest.raises(ClientPlatformError, match="not_found"):
        api.use_workflow_template("nothing")
    assert tasks.list_tasks() == []


def test_a_template_makes_a_switched_off_scheduled_one_message_workflow(tasks):
    created = api.use_workflow_template("github_pr_digest")
    again = api.use_workflow_template("github_pr_digest")
    assert again["name"] == "GitHub pull-request digest (2)"  # Never overwrites one the person has.
    task = tasks.get_task(created["task_id"])
    assert task["name"] == "GitHub pull-request digest" and task["enabled"] in (False, 0)
    assert task["schedule"] == "daily:09:00" and not task.get("trigger")  # Scheduled; never a webhook.
    assert task["safety_mode"] == "auto"  # Where new workflows start (Auto until the person picks another level).
    [message] = task["prompts"]
    assert message.startswith("Using GitHub,")


@pytest.mark.parametrize("template", ["web_daily_brief", "weekly_chat_summary"])
def test_templates_without_apps_work_with_nothing_connected(tasks, monkeypatch, template):
    monkeypatch.setattr(scope, "_items", lambda strict=False: [])
    listed = {row["id"]: row for row in api.workflow_templates()["items"]}
    assert listed[template]["apps"] == []  # Nothing to connect: offered as ready.
    from row_bot.api.v1.schemas import WorkflowTemplateList
    WorkflowTemplateList.model_validate(api.workflow_templates())
    created = api.use_workflow_template(template)
    task = tasks.get_task(created["task_id"])
    assert task["enabled"] in (False, 0) and task["schedule"] and not task.get("trigger")
    assert task["safety_mode"] == "auto"  # Where new workflows start (Auto until the person picks another level).
    assert len(task["prompts"]) == 1
