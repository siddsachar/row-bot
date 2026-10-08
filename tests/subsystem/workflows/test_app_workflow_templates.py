"""Workflows with apps: templates are created switched off and scheduled, never with a webhook, and only
once their app is connected; a step that names its apps leaves every other app out. Fakes only."""
from __future__ import annotations

import pytest

from row_bot.application import client_integrations as api
from row_bot.application.client_platform import ClientPlatformError
from row_bot.integrations import scope, workflow_templates

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


def test_a_template_makes_a_switched_off_scheduled_workflow_limited_to_its_app(tasks):
    created = api.use_workflow_template("github_pr_digest")
    again = api.use_workflow_template("github_pr_digest")
    assert again["name"] == "GitHub pull-request digest (2)"  # Never overwrites one the person has.
    task = tasks.get_task(created["task_id"])
    assert task["name"] == "GitHub pull-request digest" and task["enabled"] in (False, 0)
    assert task["schedule"] == "daily:09:00" and not task.get("trigger")  # Scheduled; never a webhook.
    assert task["safety_mode"] == "approve"
    prompt = task["steps"][0]
    assert prompt["type"] == "prompt" and prompt["apps"] == ["mcp:github"]
    assert task["steps"][1]["type"] == "notify" and task["steps"][1]["channel"] == "desktop"


def test_a_step_that_names_apps_leaves_every_other_app_out(tasks):
    narrowed = scope.step_scope(["mcp:github"])
    assert narrowed == {"exclude_servers": ["Notion"], "exclude_tools": ["gmail"], "focus": ["mcp:github"], "skills": []}
    assert scope.step_scope([]) is None and scope.step_scope(None) is None
    assert scope.step_scope(["mcp:gone"])["exclude_servers"] == ["GitHub", "Notion"]  # A removed app: nothing else.


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
    assert task["safety_mode"] == "approve"
    assert task["steps"][0]["type"] == "prompt" and "apps" not in task["steps"][0]
