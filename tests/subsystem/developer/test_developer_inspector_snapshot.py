from __future__ import annotations

import time

import pytest

from tests.fixtures.developer import fake_pending_change, fake_workspace


pytestmark = pytest.mark.subsystem


def test_inspector_snapshot_fingerprint_includes_sandbox_pending_changes(tmp_path) -> None:
    from row_bot.developer.change_ledger import ChangeSet
    from row_bot.developer.devcontainer import DevcontainerInfo
    from row_bot.developer.inspector_snapshot import InspectorSnapshot, _fingerprint_snapshot
    from row_bot.developer.review import DiffStats
    from row_bot.developer.sandbox_runtime import SandboxProbe, SandboxStatus

    workspace = fake_workspace(tmp_path, execution_mode="docker", sandbox_network="ask")
    pending = fake_pending_change(workspace.id)
    base = InspectorSnapshot(
        workspace_id=workspace.id,
        thread_id="thread-1",
        version=0,
        created_at=time.time(),
        workspace=workspace,
        git_summary={"branch": "main"},
        todos=[],
        changed_files=[],
        diff_stats=DiffStats(files=0, additions=0, deletions=0),
        agent_changes=[],
        command_specs=[],
        devcontainer=DevcontainerInfo(present=False),
        sandbox_probe=SandboxProbe(available=True, binary="docker", version="test", message="ok"),
        sandbox_status=SandboxStatus(available=True, backend="docker", running=True, container_name="row-bot-test", image=workspace.sandbox_image),
        sandbox_pending_changes=[pending],
    )
    changed = InspectorSnapshot(
        **{
            **base.__dict__,
            "sandbox_pending_changes": [],
            "agent_changes": [
                ChangeSet(
                    id="change-1",
                    workspace_id=workspace.id,
                    thread_id="thread-1",
                    created_at=time.time(),
                    summary="change",
                    files=[],
                )
            ],
        }
    )

    assert _fingerprint_snapshot(base) != _fingerprint_snapshot(changed)
