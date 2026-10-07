"""The last result of each detected check command an agent ran, per workspace (B302).

Checks used to read "Checks not run" right after the agent ran `npm test`: only the Run tab's reviewed processes
counted. Agent runs of the project's own detected commands (test, lint, typecheck) are kept here for this session.
"""

from __future__ import annotations

import threading
from datetime import datetime

_LOCK = threading.Lock()
_RESULTS: dict[str, dict[str, tuple[int, str]]] = {}


def record(workspace_id: str, workspace_path: str, command: str, exit_code: int | None) -> None:
    """Keep the result when ``command`` is one of the workspace's detected check commands."""
    if not workspace_id or exit_code is None:
        return
    from row_bot.developer.runtime import detect_project_commands

    text = " ".join(str(command or "").split())
    if text not in {spec.command for spec in detect_project_commands(workspace_path) if spec.kind != "server"}:
        return
    with _LOCK:
        _RESULTS.setdefault(workspace_id, {})[text] = (int(exit_code), datetime.now().isoformat(timespec="seconds"))


def latest(workspace_id: str) -> dict[str, tuple[int, str]]:
    """Each recorded command's exit code and when it finished."""
    with _LOCK:
        return dict(_RESULTS.get(workspace_id, {}))
