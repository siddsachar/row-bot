"""Workflow templates that use apps: a starting point, created switched off.

Each creates a scheduled workflow (it checks on its schedule; nothing outside can start it) whose
step uses only its app, as an @mention would, under the workflow's profile and the Ask approval
mode: reading runs, anything that changes asks, and approval-locked tools ask even under Allow all.
No template sets a webhook or changes how Row-Bot can be reached. A template whose app is not
connected yet says so, and its page offers Connect.
"""
from __future__ import annotations

from row_bot.integrations import apps

TEMPLATES = (
    {"id": "github_pr_digest", "name": "GitHub pull-request digest", "icon": "🔀", "apps": ("github",),
     "description": "Every morning, the pull requests waiting for you and what each needs.",
     "schedule": "daily:09:00", "schedule_label": "Every day at 09:00",
     "prompt": "Using GitHub, list the open pull requests in my repositories that are waiting for my review, or that "
               "changed in the last day. For each: repository, title, author, the state of its checks and reviews, and "
               "what it needs from me. Only read: don't comment, approve, merge or change anything."},
    {"id": "linear_triage", "name": "Linear triage", "icon": "🧭", "apps": ("linear",),
     "description": "Every morning, new Linear issues without a priority or owner, with suggestions.",
     "schedule": "daily:08:30", "schedule_label": "Every day at 08:30",
     "prompt": "Using Linear, find issues created in the last day that have no priority, assignee or project. For each, "
               "suggest a priority, an owner or team and the next step, with one line on why. Don't change any issue: "
               "list the suggestions so I can apply the ones I agree with."},
    {"id": "notion_weekly_summary", "name": "Notion weekly summary", "icon": "🗒️", "apps": ("notion",),
     "description": "Every Friday, what changed in your Notion pages this week.",
     "schedule": "weekly:fri:16:00", "schedule_label": "Every Friday at 16:00",
     "prompt": "Using Notion, find the pages created or edited in the last 7 days that I've shared with Row-Bot. Summarize "
               "what changed by project or area: decisions, open questions and anything that needs me, naming each page. "
               "Only read: don't create or edit pages."},
)


def _connected(app_id: str) -> str | None:
    """The app's connection (or built-in way with chat tools) that is set up and ready, if any."""
    from row_bot.integrations import scope
    ready = [item for item in scope._items() if (item.get("app") or {}).get("id") == app_id
             and item["lifecycle"] == "installed" and item.get("readiness") == "ready"]
    return ready[0]["id"] if ready else None


def listing() -> list[dict]:
    """The templates, each with its apps and whether they are connected (local data only)."""
    known = apps.catalog()[0]
    found = []
    for template in TEMPLATES:
        used = []
        for app_id in template["apps"]:
            app = known.get(app_id)
            item_id = _connected(app_id)
            used.append({"app_id": app_id, "name": app.name if app else app_id, "icon": app.ref()["icon"] if app else
                         apps.letter(app_id), "connected": item_id is not None})
        found.append({key: template[key] for key in ("id", "name", "description", "icon", "schedule_label")} | {"apps": used})
    return found


def use(template_id: str) -> dict:
    """Create the template's workflow, switched off; ``app_not_connected`` until its apps are."""
    from row_bot import tasks
    template = next((t for t in TEMPLATES if t["id"] == template_id), None)
    if template is None:
        raise ValueError("not_found")
    item_ids = [_connected(app_id) for app_id in template["apps"]]
    if None in item_ids:
        raise ValueError("app_not_connected")
    taken = {str(task.get("name") or "") for task in tasks.list_tasks()}
    name = next(candidate for candidate in (template["name"], *(f"{template['name']} ({n})" for n in range(2, 100)))
                if candidate not in taken)
    steps = [{"id": "read", "type": "prompt", "prompt": template["prompt"], "apps": item_ids, "on_error": "stop"},
             {"id": "tell", "type": "notify", "channel": "desktop",
              "message": f"{name} is ready. Open the workflow's latest run to read it."}]
    task_id = tasks.create_task(name=name, description=template["description"], icon=template["icon"],
                                schedule=template["schedule"], steps=steps, enabled=False, safety_mode="approve",
                                agent_profile_id=tasks.DEFAULT_WORKFLOW_AGENT_PROFILE_ID, apply_default_skills=False)
    return {"task_id": task_id, "name": name}
