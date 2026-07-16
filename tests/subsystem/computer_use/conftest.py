from __future__ import annotations

import pytest

from row_bot.computer_use.client import CuaClient
from row_bot.computer_use.readiness import acknowledge_disclosure
from row_bot.computer_use.service import ComputerUseService
from tests.fixtures.fake_cua import FakeCuaTransport


@pytest.fixture
def fake_transport(tmp_path, monkeypatch) -> FakeCuaTransport:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    acknowledge_disclosure()
    return FakeCuaTransport()


@pytest.fixture
def fake_client(fake_transport: FakeCuaTransport) -> CuaClient:
    return CuaClient(
        "fake-cua-driver.exe",
        session_id="row-bot-test-session",
        transport_factory=lambda _exe, _session, _env: fake_transport,
    )


@pytest.fixture
def service(fake_client: CuaClient) -> ComputerUseService:
    return ComputerUseService(client_factory=lambda: fake_client, approval_callback=lambda _payload: True)
