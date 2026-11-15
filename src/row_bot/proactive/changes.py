"""Changes you care about: a workflow that didn't finish and hasn't since, and a sign-in that stopped working.

- **Workflows**: ``tasks.after_run_finished`` hands each run that failed (not one the person stopped or
  said no to) to ``workflow_finished``, which queues it in ``proactive.db``. The run's own warning goes out
  at once as it always did; the moment is for later, at a break two hours on, and only if the workflow
  hasn't completed a run since.
- **Sign-ins**: Monitor's kept checks (they run on their own schedule). A sign-in check that turns to an
  error becomes one moment; the first pass only remembers where things stand.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from row_bot.proactive import sources as proactive_sources
from row_bot.proactive import store
from row_bot.proactive.model import Candidate, Evidence
from row_bot.proactive.watchers import Watcher

FAILED = frozenset({"failed", "unsure"})
LATER_SECONDS = 2 * 3600
QUEUE_KEPT = 50


def workflow_finished(task: dict, run_id: str, status: str, notice: str, *, now: float | None = None) -> None:
    """A workflow run ended (``tasks.after_run_finished``): queue a failed one for the workflows watcher."""
    from row_bot.proactive import settings as proactive_settings

    if status not in FAILED:
        return
    chosen = proactive_settings.load()
    if chosen["level"] == "off" or not chosen["sources"]["workflows"]:
        return
    queued = list(store.get_state("workflow_runs") or [])
    queued.append({"run_id": run_id, "task_id": str(task.get("id") or ""), "name": str(task.get("name") or ""),
                   "status": status, "notice": str(notice or "")[:300], "at": time.time() if now is None else now})
    store.set_state("workflow_runs", queued[-QUEUE_KEPT:])


class WorkflowSource:
    name = "workflows"

    def available(self) -> bool:
        return True

    def completed_since(self, task_id: str, since: float) -> bool | None:
        """Whether the workflow completed a run that started after *since*; None if it's gone."""
        from row_bot import tasks

        if tasks.get_task(task_id) is None:
            return None
        conn = tasks._get_conn()
        try:
            row = conn.execute("SELECT 1 FROM task_runs WHERE task_id = ? AND started_at > ? AND status IN "
                               "('completed', 'completed_delivery_failed') LIMIT 1",
                               (task_id, datetime.fromtimestamp(since).isoformat())).fetchone()
        finally:
            conn.close()
        return row is not None


class WorkflowWatcher(Watcher):
    name = "workflows"
    source = "workflows"
    every_seconds = 60

    def __init__(self, runs: Any | None = None) -> None:
        self.runs = WorkflowSource() if runs is None else runs

    def scan(self, cursor: dict[str, Any], now: float) -> tuple[list[Candidate], dict[str, Any]]:
        queued = list(store.get_state("workflow_runs") or [])
        store.set_state("workflow_runs", [])
        found = [Candidate(
            kind="change", key=f"run:{run['run_id']}", title=f"“{run['name']}” didn't finish",
            summary=f"The workflow “{run['name']}” ended {run['status']} and hasn't completed a run since. "
                    f"Its last words: {run['notice']}",
            evidence=(Evidence("workflow_run", run["run_id"], run["name"], "workflows", seen=run["task_id"]),),
            topic=run["name"], due_at=run["at"] + LATER_SECONDS, expires_at=run["at"] + 24 * 3600,
            actions=({"id": "open_source", "label": "Open workflow"},)) for run in queued]
        return found, {}

    def recheck(self, moment: dict[str, Any], now: float) -> bool | None:
        evidence = moment["evidence"][0]
        completed = self.runs.completed_since(evidence["seen"], moment["created_at"] - LATER_SECONDS)
        return None if completed is None else not completed


class SignInWatcher(Watcher):
    name = "sign_ins"
    source = "monitor"
    every_seconds = 15 * 60

    def __init__(self, monitor: Any | None = None) -> None:
        self.monitor = proactive_sources.MonitorSource() if monitor is None else monitor

    @staticmethod
    def _sign_in(check: dict[str, Any]) -> bool:
        return (check.get("fix") or {}).get("kind") == "reconnect_account"

    def scan(self, cursor: dict[str, Any], now: float) -> tuple[list[Candidate], dict[str, Any]]:
        before: dict[str, str] | None = cursor.get("status")
        checks = self.monitor.checks()
        status = {str(check["id"]): str(check["status"]) for check in checks}
        found: list[Candidate] = []
        if before is not None:
            for check in checks:
                if check["status"] != "error" or before.get(check["id"]) == "error" or not self._sign_in(check):
                    continue
                found.append(Candidate(
                    kind="change", key=f"sign_in:{check['id']}:{int(now)}", title=f"{check['name']} needs you to sign in again",
                    summary=f"Monitor's check of {check['name']} failed: {check.get('detail', '')}",
                    evidence=(Evidence("check", str(check["id"]), str(check["name"]), "monitor", seen="error"),),
                    topic=str(check["name"]), expires_at=now + 3 * 86400,
                    actions=({"id": "open_fix", "label": "Sign in"},)))
        return found, {"status": status}

    def recheck(self, moment: dict[str, Any], now: float) -> bool | None:
        evidence = moment["evidence"][0]
        check = next((item for item in self.monitor.checks() if item["id"] == evidence["id"]), None)
        return check is not None and check["status"] == "error"
