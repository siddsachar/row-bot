from __future__ import annotations

import pytest
import base64

from row_bot.computer_use.client import CuaClient, build_cua_environment, parse_cua_result
from row_bot.computer_use.readiness import cancel_disclosure
from tests.fixtures.fake_cua import FakeCuaTransport, FakeScenario
from row_bot.mcp_client.results import RawCallContent, RawCallResult


def test_client_forces_session_window_capture_config_before_discovery(fake_client, fake_transport) -> None:
    fake_client.start()
    assert fake_transport.calls[:2] == [
        ("set_config", {"capture_scope": "window", "max_image_dimension": 1456}),
        ("start_session", {"session": "row-bot-test-session"}),
    ]


def test_environment_disables_update_check_but_does_not_override_telemetry(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    env = build_cua_environment("session", {"HOME": "/home/test", "SECRET": "no", "CUA_DRIVER_RS_TELEMETRY_ENABLED": "0"})
    assert env["CUA_DRIVER_RS_UPDATE_CHECK"] == "0"
    assert env["CUA_DRIVER_EMBEDDED"] == "1"
    assert "CUA_DRIVER_RS_TELEMETRY_ENABLED" not in env
    assert "SECRET" not in env


def test_no_process_opens_before_disclosure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path))
    cancel_disclosure()
    transport = FakeCuaTransport()
    client = CuaClient("fake.exe", transport_factory=lambda *_args: transport)
    with pytest.raises(PermissionError, match="disclosure"):
        client.start()
    assert transport.opened is False
    assert transport.calls == []


def test_capture_parses_text_image_and_capped_semantics(fake_client) -> None:
    response = fake_client.call_action("capture", {"pid": 4242, "window_id": 101})
    assert response.image_bytes and response.image_bytes.startswith(b"\x89PNG")
    assert (response.image_width, response.image_height) == (1, 1)
    assert [element.token for element in response.elements] == ["g1-element-0", "g1-element-1", "g1-element-2"]


def test_malformed_image_fails_closed(fake_transport) -> None:
    fake_transport.scenario = FakeScenario(malformed_image=True)
    with pytest.raises(ValueError, match="base64"):
        parse_cua_result(fake_transport.call_raw("get_window_state", {"pid": 1, "window_id": 2}))


def test_top_level_background_unavailable_error_code_is_preserved() -> None:
    result = RawCallResult(
        (RawCallContent(kind="text", text="background unavailable"),),
        {"error": True, "error_code": "background_unavailable"},
        True,
    )

    response = parse_cua_result(result)

    assert response.is_error is True
    assert response.error_code == "background_unavailable"


def test_oversized_tree_is_deterministically_capped(fake_transport) -> None:
    fake_transport.scenario = FakeScenario(oversized_tree=True)
    response = parse_cua_result(fake_transport.call_raw("get_window_state", {}))
    assert response.truncated is True
    assert len(response.elements) <= 250
    assert all(element.depth <= 12 for element in response.elements)


def test_forbidden_driver_tool_cannot_be_called(fake_client) -> None:
    with pytest.raises(PermissionError):
        fake_client._call("start_recording", {"output_dir": "secret"})


def test_disconnect_is_not_retried_by_client_or_service(fake_client, fake_transport) -> None:
    from row_bot.computer_use.service import ComputerUseError, ComputerUseService, LeaseOwner

    service = ComputerUseService(client_factory=lambda: fake_client, approval_callback=lambda _payload: True)
    owner = LeaseOwner("disconnect", "generation", "task")
    service.acquire(owner, validate_context=False)
    fake_transport.scenario.disconnect = True
    with pytest.raises(ComputerUseError, match="disconnected"):
        service.list_apps(owner)
    assert service.status_snapshot()["active"] is False
    assert service.status_snapshot()["state"] == "failed"


def test_image_mime_magic_mismatch_and_decoded_size_fail_closed() -> None:
    mismatch = RawCallResult((RawCallContent(kind="image", data=base64.b64encode(b"not a png").decode(), mime_type="image/png"),), {})
    with pytest.raises(ValueError, match="magic"):
        parse_cua_result(mismatch)
    oversized = RawCallResult((RawCallContent(kind="image", data=base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (8 * 1024 * 1024)).decode(), mime_type="image/png"),), {})
    with pytest.raises(ValueError, match="8 MiB"):
        parse_cua_result(oversized)
