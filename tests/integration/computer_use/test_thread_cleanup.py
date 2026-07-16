from __future__ import annotations

from row_bot.computer_use.client import CuaClient
from row_bot.computer_use.service import ComputerUseService, LeaseOwner
from tests.fixtures.fake_cua import FakeCuaTransport


def test_thread_cleanup_releases_matching_lease(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    from row_bot.computer_use.readiness import acknowledge_disclosure
    acknowledge_disclosure()
    transport = FakeCuaTransport()
    client = CuaClient("fake.exe", transport_factory=lambda *_args: transport)
    service = ComputerUseService(client_factory=lambda: client, approval_callback=lambda _payload: True)
    owner = LeaseOwner("cleanup-thread", "generation", "task")
    service.acquire(owner, validate_context=False)
    service.close_for_thread("other-thread")
    assert service.status_snapshot()["active"] is True
    service.close_for_thread("cleanup-thread")
    assert service.status_snapshot()["active"] is False
