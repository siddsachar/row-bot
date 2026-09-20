from __future__ import annotations

import pytest

from row_bot.computer_use.service import (
    ComputerUseError,
    ComputerUseService,
    LeaseOwner,
)


@pytest.mark.parametrize(
    "runtime_surface", ["remote_client", "client_remote", "paired_browser"]
)
def test_paired_remote_runtime_surfaces_cannot_acquire_native_computer_use(
    monkeypatch,
    runtime_surface: str,
) -> None:
    monkeypatch.setattr(
        "row_bot.agent.get_active_runtime_context",
        lambda: {
            "runtime_surface": runtime_surface,
            "background_workflow": False,
            "channel_streaming": False,
        },
    )
    service = ComputerUseService(client_factory=lambda: pytest.fail("driver started"))

    with pytest.raises(ComputerUseError, match="unavailable on this runtime surface"):
        service.acquire(LeaseOwner("thread-remote", "generation-remote", "task-remote"))
