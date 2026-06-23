from __future__ import annotations

import time
from pathlib import Path

from row_bot.developer.change_ledger import ChangeSet, FileChange
from row_bot.developer.sandbox_runtime import SandboxPendingChange
from row_bot.developer.state import DeveloperWorkspace


def fake_workspace(tmp_path: Path, *, execution_mode: str = "local", sandbox_network: str = "off") -> DeveloperWorkspace:
    root = tmp_path / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return DeveloperWorkspace(
        id="ws-test",
        name="Fixture Workspace",
        path=str(root),
        execution_mode=execution_mode,  # type: ignore[arg-type]
        sandbox_network=sandbox_network,  # type: ignore[arg-type]
    )


def fake_pending_change(workspace_id: str = "ws-test") -> SandboxPendingChange:
    return SandboxPendingChange(
        id="pending-1",
        workspace_id=workspace_id,
        thread_id="thread-test",
        command="python script.py",
        patch="diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
        files=["app.py"],
        created_at="2026-01-01T00:00:00",
    )


def fake_change_set(workspace_id: str = "ws-test") -> ChangeSet:
    return ChangeSet(
        id="change-1",
        workspace_id=workspace_id,
        thread_id="thread-test",
        created_at=time.time(),
        summary="Imported sandbox change",
        files=[
            FileChange(
                path="app.py",
                action="update",
                before_hash="old",
                after_hash="new",
                before_text="old",
                patch="@@ -1 +1 @@\n-old\n+new\n",
            )
        ],
    )
