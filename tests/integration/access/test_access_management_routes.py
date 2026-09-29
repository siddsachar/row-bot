from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from starlette.testclient import TestClient

from row_bot.access.config import AccessConfig
from row_bot.access.middleware import AccessMiddleware
from row_bot.access.routes import register_access_routes
from row_bot.access import routes as access_routes
from row_bot.access.service import AccessService
from row_bot.access.store import AccessStore


def _application(tmp_path, *, mode: str = "server", tailscale_controller=None):
    config = AccessConfig.build(
        deployment_mode=mode,
        allowed_hosts=("localhost",),
    )
    service = AccessService(AccessStore(tmp_path / "mobile.db"))
    app = FastAPI()
    registration = register_access_routes(
        app,
        service=service,
        config=config,
        tailscale_controller=tailscale_controller,
    )
    app.add_middleware(
        AccessMiddleware,
        config=config,
        session_authenticator=registration.authenticator,
    )
    client = TestClient(
        app,
        base_url="http://localhost:8080",
        client=("127.0.0.1", 51000),
        follow_redirects=False,
    )
    return client, service, registration


def _session_cookie(
    service: AccessService,
    registration,
    *,
    name: str,
) -> tuple[str, str, str]:
    created = service.create_invitation(
        intended_origin="http://localhost:8080",
    )
    claimed = service.claim_invitation(
        created.token,
        intended_origin="http://localhost:8080",
        display_name=name,
    )
    cookie = f"{registration.cookies.names.http}={claimed.session_token}"
    return cookie, claimed.device.id, claimed.session.id


def test_local_desktop_owner_can_create_desktop_invitation(tmp_path) -> None:
    client, _service, _registration = _application(tmp_path, mode="desktop")

    response = client.post(
        "/api/access/invitations",
        json={
            "layout": "desktop",
            "session_lifetime": "trusted",
            "origin": "http://localhost:8080",
        },
        headers={"origin": "http://localhost:8080"},
    )

    assert response.status_code == 201
    assert response.json()["invitation"]["layout"] == "desktop"
    assert "profile" not in response.json()["invitation"]
    assert response.json()["invitation"]["session_lifetime"] == "trusted"
    assert "token" not in response.json()
    query = parse_qs(urlsplit(response.json()["invitation_url"]).query)
    assert query["invitation"]
    assert query["next"] == ["/app-v2/"]


def test_route_selection_is_revalidated_before_invitation_creation(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(access_routes, "discover_private_lan_addresses", lambda: ())
    client, service, _registration = _application(tmp_path, mode="desktop")
    listed = client.get("/api/access/routes")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert all("token" not in row for row in listed.json()["routes"])
    route = next(
        row for row in listed.json()["routes"] if row["kind"] == "current_server"
    )

    created = client.post(
        "/api/access/invitations",
        json={
            "route_id": route["id"],
            "origin": "https://wrong.example",
            "session_lifetime": "temporary",
        },
        headers={"origin": "http://localhost:8080"},
    )
    assert created.status_code == 201
    assert created.json()["invitation"]["intended_origin"] == route["origin"]

    rejected = client.post(
        "/api/access/invitations",
        json={"route_id": "stale-route"},
        headers={"origin": "http://localhost:8080"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"] == "route_changed"
    assert len(service.list_invitations()) == 1


def test_same_wifi_is_offered_only_while_row_bot_listens_on_the_network(
    tmp_path,
    monkeypatch,
) -> None:
    """B184: a launch bound to this computer never offers a Same Wi-Fi code.

    The saved listen mode can say "network" while an explicit host (the
    launcher's ROW_BOT_HOST, ``--host``) binds loopback only; a code for the
    Wi-Fi address would then never reach the server.
    """
    from row_bot.access.access_routes import AccessRouteConfigStore

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(
        access_routes, "discover_private_lan_addresses", lambda: ("192.168.1.23",)
    )
    AccessRouteConfigStore().set_listen_mode("local_network")
    client, _service, _registration = _application(tmp_path, mode="desktop")

    def lan_state() -> tuple[list[bool], bool]:
        listed = client.get("/api/access/routes").json()
        return (
            [row["available"] for row in listed["routes"] if row["kind"] == "lan"],
            listed["listening_on_network"],
        )

    monkeypatch.setenv("ROW_BOT_HOST", "127.0.0.1")
    assert lan_state() == ([False], False)
    lan_id = next(
        row["id"]
        for row in client.get("/api/access/routes").json()["routes"]
        if row["kind"] == "lan"
    )
    refused = client.post(
        "/api/access/invitations",
        json={"route_id": lan_id},
        headers={"origin": "http://localhost:8080"},
    )
    assert refused.status_code == 409

    monkeypatch.setenv("ROW_BOT_HOST", "0.0.0.0")
    assert lan_state() == ([True], True)


def test_routes_require_owner(tmp_path) -> None:
    client, _service, _registration = _application(tmp_path)
    response = client.get("/api/access/routes")
    assert response.status_code == 401


def test_local_owner_changes_listen_mode_once_and_rejects_stale_request(
    tmp_path,
    monkeypatch,
) -> None:
    from row_bot.access import launcher_control

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    restarts = []
    monkeypatch.setattr(
        launcher_control,
        "request_launcher_restart",
        lambda: restarts.append(1) or type("Result", (), {"accepted": False})(),
    )
    client, _service, _registration = _application(tmp_path, mode="desktop")
    headers = {"origin": "http://localhost:8080"}
    payload = {"expected_mode": "local_only", "listen_mode": "local_network"}
    changed = client.post("/api/access/routes/listen", json=payload, headers=headers)
    stale = client.post("/api/access/routes/listen", json=payload, headers=headers)
    assert changed.status_code == 200
    assert changed.json()["restart_required"] is True
    assert changed.json()["listen_mode"] == "local_network"
    assert stale.status_code == 409
    assert restarts == [1]


def test_trusted_origin_requires_live_policy_and_exact_revision(
    tmp_path,
    monkeypatch,
) -> None:
    from row_bot.access.access_routes import AccessRouteConfigStore
    from row_bot.access.runtime_policy import RuntimeAccessPolicy

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.delenv("ROW_BOT_ALLOWED_HOSTS", raising=False)
    client, _service, registration = _application(tmp_path, mode="desktop")
    headers = {"origin": "http://localhost:8080"}
    payload = {
        "action": "add",
        "origin": "https://example.test",
        "expected_origins": [],
    }
    unavailable = client.post(
        "/api/access/routes/origins", json=payload, headers=headers
    )
    assert unavailable.status_code == 503
    assert AccessRouteConfigStore().load_or_default().configured_origins == ()

    policy = RuntimeAccessPolicy(registration.config)
    client.app.state.row_bot_access_runtime_policy = policy
    added = client.post("/api/access/routes/origins", json=payload, headers=headers)
    stale = client.post("/api/access/routes/origins", json=payload, headers=headers)
    assert added.status_code == 200
    assert added.json()["configured_origins"] == ["https://example.test"]
    assert stale.status_code == 409
    assert policy.snapshot().configured_origins == ("https://example.test",)

    removed = client.post(
        "/api/access/routes/origins",
        json={
            "action": "remove",
            "origin": "https://example.test",
            "expected_origins": ["https://example.test"],
        },
        headers=headers,
    )
    assert removed.status_code == 200
    assert AccessRouteConfigStore().load_or_default().configured_origins == ()


def test_remote_owner_cannot_change_route_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    client, service, registration = _application(tmp_path)
    cookie, _device, _session = _session_cookie(service, registration, name="Remote")
    headers = {"cookie": cookie, "origin": "http://localhost:8080"}
    assert (
        client.post(
            "/api/access/routes/listen",
            json={
                "expected_mode": "local_only",
                "listen_mode": "local_network",
            },
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/access/routes/origins",
            json={
                "action": "add",
                "origin": "https://example.test",
                "expected_origins": [],
            },
            headers=headers,
        ).status_code
        == 403
    )


def test_tailscale_is_passive_until_explicit_check_and_deduplicates_apply(
    tmp_path,
    monkeypatch,
) -> None:
    from row_bot.access import launcher_control
    from row_bot.access.tailscale import (
        TailscaleOperationResult,
        TailscalePlanAction,
        TailscaleServePlan,
        TailscaleState,
        TailscaleStatus,
    )

    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(
        launcher_control,
        "request_launcher_restart",
        lambda: type("Result", (), {"accepted": False})(),
    )
    ready = TailscaleStatus(state=TailscaleState.READY, detail="Ready")
    active = TailscaleStatus(state=TailscaleState.ACTIVE_OWNED, detail="Active")

    class FakeTailscale:
        detect_count = 0
        apply_count = 0

        def detect(self, *, port):
            self.detect_count += 1
            assert port == 8080
            return ready

        def plan(self, *, port):
            assert port == 8080
            return TailscaleServePlan(
                action=TailscalePlanAction.ENABLE,
                status=ready,
                port=port,
                target="http://127.0.0.1:8080",
                command=("fake",),
                description="Enable private route",
            )

        def apply(self, plan):
            self.apply_count += 1
            assert plan.action is TailscalePlanAction.ENABLE
            return TailscaleOperationResult(success=True, status=active)

        def disable_owned(self):
            return TailscaleOperationResult(
                success=False, status=active, error="Ownership changed"
            )

    fake = FakeTailscale()
    client, _service, _registration = _application(
        tmp_path,
        mode="desktop",
        tailscale_controller=fake,
    )
    assert client.get("/api/access/tailscale").json()["status"] is None
    assert fake.detect_count == 0
    headers = {"origin": "http://localhost:8080"}
    checked = client.post("/api/access/tailscale/check", headers=headers)
    assert checked.json()["status"]["state"] == "ready"
    assert fake.detect_count == 1
    payload = {"action": "enable", "command_id": "command-1"}
    first = client.post("/api/access/tailscale/actions", json=payload, headers=headers)
    second = client.post("/api/access/tailscale/actions", json=payload, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["success"] is True
    assert first.json()["restart_required"] is True
    assert fake.apply_count == 1
    # The fake did not write an ownership record, so the passive verified
    # status cache correctly refuses to advertise an owned route.
    assert client.get("/api/access/tailscale").json()["status"] is None
    denied = client.post(
        "/api/access/tailscale/actions",
        json={
            "action": "disable",
            "command_id": "command-1",
        },
        headers=headers,
    )
    assert denied.status_code == 409


def test_remote_owner_cannot_probe_or_change_tailscale(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "profile"))

    class FakeTailscale:
        def detect(self, *, port):
            raise AssertionError("should not probe")

    client, service, registration = _application(
        tmp_path, tailscale_controller=FakeTailscale()
    )
    cookie, _device, _session = _session_cookie(service, registration, name="Remote")
    headers = {"cookie": cookie, "origin": "http://localhost:8080"}
    assert client.get("/api/access/tailscale", headers=headers).status_code == 200
    assert (
        client.post("/api/access/tailscale/check", headers=headers).status_code == 403
    )
    assert (
        client.post(
            "/api/access/tailscale/actions",
            json={
                "action": "enable",
                "command_id": "command-remote",
            },
            headers=headers,
        ).status_code
        == 403
    )


def test_remote_owner_can_list_status_invitations_devices_and_sessions(
    tmp_path,
) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, owner_device_id, owner_session_id = _session_cookie(
        service,
        registration,
        name="Owner",
    )
    headers = {"cookie": owner_cookie}

    status = client.get("/api/access/status", headers=headers)
    invitations = client.get("/api/access/invitations", headers=headers)
    devices = client.get("/api/access/devices", headers=headers)

    assert status.status_code == 200
    assert status.json()["devices"] == 1
    assert status.json()["sessions"] == 1
    assert invitations.status_code == 200
    assert devices.status_code == 200
    device = devices.json()["devices"][0]
    assert device["id"] == owner_device_id
    assert device["sessions"][0]["id"] == owner_session_id
    assert "token_hash" not in devices.text
    assert "token_salt" not in devices.text


def test_phone_owner_can_use_access_management_routes(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    phone_cookie, device_id, _session_id = _session_cookie(
        service,
        registration,
        name="Phone",
    )
    safe_headers = {"cookie": phone_cookie}
    unsafe_headers = {
        **safe_headers,
        "origin": "http://localhost:8080",
    }

    responses = [
        client.get("/api/access/status", headers=safe_headers),
        client.get("/api/access/invitations", headers=safe_headers),
        client.get("/api/access/devices", headers=safe_headers),
        client.post(
            "/api/access/invitations",
            json={"layout": "compact"},
            headers=unsafe_headers,
        ),
    ]

    assert all(response.status_code in {200, 201} for response in responses)
    assert device_id


def test_owner_creates_lists_and_cancels_invitation_without_relisting_secret(
    tmp_path,
    caplog,
) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, _device_id, _session_id = _session_cookie(
        service,
        registration,
        name="Owner",
    )
    headers = {
        "cookie": owner_cookie,
        "origin": "http://localhost:8080",
    }

    with caplog.at_level(logging.WARNING):
        created = client.post(
            "/api/access/invitations",
            json={
                "layout": "compact",
                "session_lifetime": "temporary",
            },
            headers=headers,
        )
    token = parse_qs(urlsplit(created.json()["invitation_url"]).query)["invitation"][0]
    invitation_id = created.json()["invitation"]["id"]
    listed = client.get(
        "/api/access/invitations",
        headers={"cookie": owner_cookie},
    )
    cancelled = client.post(
        f"/api/access/invitations/{invitation_id}/cancel",
        headers=headers,
    )

    assert created.status_code == 201
    assert created.json()["invitation"]["layout"] == "compact"
    assert "profile" not in created.json()["invitation"]
    assert created.json()["invitation"]["session_lifetime"] == "temporary"
    assert listed.status_code == 200
    assert token not in listed.text
    assert "secret_hash" not in listed.text
    assert "secret_salt" not in listed.text
    assert token not in caplog.text
    assert cancelled.json() == {"ok": True, "cancelled": True}
    assert service.inspect_invitation(token).status == "cancelled"


def test_owner_revokes_device_and_its_sessions_immediately(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, _owner_device_id, _owner_session_id = _session_cookie(
        service,
        registration,
        name="Owner",
    )
    phone_cookie, phone_device_id, phone_session_id = _session_cookie(
        service,
        registration,
        name="Phone",
    )

    response = client.post(
        f"/api/access/devices/{phone_device_id}/revoke",
        headers={
            "cookie": owner_cookie,
            "origin": "http://localhost:8080",
        },
    )
    phone_status = client.get(
        "/api/access/session",
        headers={"cookie": phone_cookie},
    )

    assert response.json() == {"ok": True, "revoked": True}
    assert service.store.get_session(phone_session_id).revoked_at is not None
    assert phone_status.json()["authenticated"] is False


def test_owner_revokes_one_session_without_revoking_its_device(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, _owner_device_id, _owner_session_id = _session_cookie(
        service,
        registration,
        name="Owner",
    )
    phone_cookie, phone_device_id, phone_session_id = _session_cookie(
        service,
        registration,
        name="Phone",
    )

    response = client.post(
        f"/api/access/sessions/{phone_session_id}/revoke",
        headers={
            "cookie": owner_cookie,
            "origin": "http://localhost:8080",
        },
    )

    assert response.json() == {"ok": True, "revoked": True}
    assert service.store.get_device(phone_device_id).revoked_at is None
    assert (
        client.get(
            "/api/access/session",
            headers={"cookie": phone_cookie},
        ).json()["authenticated"]
        is False
    )


def test_self_session_revoke_clears_access_cookies(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, _owner_device_id, owner_session_id = _session_cookie(
        service,
        registration,
        name="Owner",
    )

    response = client.post(
        f"/api/access/sessions/{owner_session_id}/revoke",
        headers={
            "cookie": owner_cookie,
            "origin": "http://localhost:8080",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "revoked": True}
    assert len(response.headers.get_list("set-cookie")) == 4


def test_access_management_body_bound_and_rate_limit(tmp_path, monkeypatch) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, _owner_device_id, _owner_session_id = _session_cookie(
        service,
        registration,
        name="Owner",
    )
    headers = {
        "cookie": owner_cookie,
        "origin": "http://localhost:8080",
        "content-type": "application/json",
    }

    oversized = client.post(
        "/api/access/invitations",
        content=b"x" * (access_routes.ACCESS_REQUEST_BODY_LIMIT + 1),
        headers=headers,
    )
    monkeypatch.setattr(access_routes, "_MANAGEMENT_RATE_LIMIT", 2)
    first = client.post(
        "/api/access/invitations",
        json={"layout": "invalid"},
        headers=headers,
    )
    second = client.post(
        "/api/access/invitations",
        json={"layout": "invalid"},
        headers=headers,
    )
    limited = client.post(
        "/api/access/invitations",
        json={"layout": "desktop"},
        headers=headers,
    )

    assert oversized.status_code == 413
    assert oversized.json()["error"] == "request_too_large"
    assert first.status_code == 400
    assert second.status_code == 429
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"


def test_self_revoke_clears_current_instance_and_legacy_cookies(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, owner_device_id, _owner_session_id = _session_cookie(
        service,
        registration,
        name="Owner",
    )

    response = client.post(
        f"/api/access/devices/{owner_device_id}/revoke",
        headers={
            "cookie": owner_cookie,
            "origin": "http://localhost:8080",
        },
    )

    assert response.status_code == 200
    set_cookies = response.headers.get_list("set-cookie")
    assert len(set_cookies) == 4
    assert any(registration.cookies.names.http in value for value in set_cookies)
    assert any("__Host-row_bot_mobile" in value for value in set_cookies)


def test_management_mutations_require_exact_same_origin(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, device_id, session_id = _session_cookie(
        service,
        registration,
        name="Owner",
    )

    missing = client.post(
        "/api/access/invitations",
        json={"layout": "compact"},
        headers={"cookie": owner_cookie},
    )
    wrong = client.post(
        f"/api/access/devices/{device_id}/revoke",
        headers={
            "cookie": owner_cookie,
            "origin": "https://attacker.example",
        },
    )
    wrong_session = client.post(
        f"/api/access/sessions/{session_id}/revoke",
        headers={
            "cookie": owner_cookie,
            "origin": "https://attacker.example",
        },
    )

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert wrong_session.status_code == 403
    assert missing.json()["error"] == "origin_required"
    assert wrong.json()["error"] == "origin_required"
    assert wrong_session.json()["error"] == "origin_required"
    assert service.store.get_session(session_id).revoked_at is None


def test_invalid_invitation_options_fail_without_creating_records(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, _device_id, _session_id = _session_cookie(
        service,
        registration,
        name="Owner",
    )
    headers = {
        "cookie": owner_cookie,
        "origin": "http://localhost:8080",
    }
    before = len(service.list_invitations())

    layout = client.post(
        "/api/access/invitations",
        json={"layout": "restricted"},
        headers=headers,
    )
    legacy_profile = client.post(
        "/api/access/invitations",
        json={"profile": "companion"},
        headers=headers,
    )
    origin = client.post(
        "/api/access/invitations",
        json={"origin": "javascript:alert(1)"},
        headers=headers,
    )

    assert layout.status_code == 400
    assert legacy_profile.status_code == 400
    assert "choose a layout" in legacy_profile.json()["detail"]
    assert origin.status_code == 400
    assert len(service.list_invitations()) == before


def _current_marks(devices: list[dict]) -> tuple[set[str], set[str]]:
    return (
        {device["id"] for device in devices if device["current"]},
        {
            session["id"]
            for device in devices
            for session in device["sessions"]
            if session["current"]
        },
    )


def test_devices_mark_the_device_making_the_request_as_this_device(
    tmp_path,
) -> None:
    """B141: "This device" is the device whose session made the request."""
    client, service, registration = _application(tmp_path)
    phone_cookie, phone_id, phone_session = _session_cookie(
        service, registration, name="Phone"
    )
    laptop_cookie, laptop_id, laptop_session = _session_cookie(
        service, registration, name="Laptop"
    )

    from_phone = client.get("/api/access/devices", headers={"cookie": phone_cookie})
    from_laptop = client.get(
        "/api/access/devices", headers={"cookie": laptop_cookie}
    )

    assert _current_marks(from_phone.json()["devices"]) == (
        {phone_id},
        {phone_session},
    )
    assert _current_marks(from_laptop.json()["devices"]) == (
        {laptop_id},
        {laptop_session},
    )


def test_the_owner_on_this_computer_is_not_a_listed_device(tmp_path) -> None:
    client, service, registration = _application(tmp_path, mode="desktop")
    _session_cookie(service, registration, name="Phone")

    devices = client.get("/api/access/devices").json()["devices"]

    assert _current_marks(devices) == (set(), set())


def test_devices_report_the_address_they_were_last_seen_from(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, _owner_id, _owner_session = _session_cookie(
        service, registration, name="Owner"
    )
    phone_cookie, phone_id, phone_session = _session_cookie(
        service, registration, name="Phone"
    )

    def phone_row() -> dict:
        devices = client.get(
            "/api/access/devices", headers={"cookie": owner_cookie}
        ).json()["devices"]
        return next(device for device in devices if device["id"] == phone_id)

    def from_address(address: str) -> TestClient:
        return TestClient(
            client.app,
            base_url="http://localhost:8080",
            client=(address, 51000),
            follow_redirects=False,
        )

    assert phone_row()["last_address"] is None
    seen = from_address("192.168.1.23").get(
        "/api/access/session", headers={"cookie": phone_cookie}
    )
    assert seen.json()["authenticated"] is True
    assert phone_row()["last_address"] == "192.168.1.23"
    assert phone_row()["last_seen_at"]

    # A revoked session never moves the device's last address again.
    service.revoke_session(phone_session)
    refused = from_address("10.0.0.9").get(
        "/api/access/session", headers={"cookie": phone_cookie}
    )
    assert refused.json()["authenticated"] is False
    assert phone_row()["last_address"] == "192.168.1.23"


def test_owner_renames_a_device_with_exact_origin_and_a_bounded_name(
    tmp_path,
) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, _owner_id, _owner_session = _session_cookie(
        service, registration, name="Owner"
    )
    _phone_cookie, phone_id, _phone_session = _session_cookie(
        service, registration, name="Connected browser"
    )
    headers = {"cookie": owner_cookie, "origin": "http://localhost:8080"}

    def rename(device_id: str, name: object, **extra):
        return client.post(
            f"/api/access/devices/{device_id}/rename",
            json={"display_name": name},
            headers={**headers, **extra},
        )

    renamed = rename(phone_id, "  Kitchen tablet  ")
    assert renamed.status_code == 200
    assert renamed.json()["device"]["display_name"] == "Kitchen tablet"
    assert service.store.get_device(phone_id).display_name == "Kitchen tablet"

    assert rename(phone_id, "Evil", origin="https://attacker.example").status_code == (
        403
    )
    for bad in ("", "   ", "x" * 81, "Line" + chr(10) + "break", 42):
        refused = rename(phone_id, bad)
        assert refused.status_code == 400
        assert refused.json()["error"] == "invalid_device_name"
    assert rename("missing-device", "Nobody").status_code == 404
    service.revoke_device(phone_id)
    assert rename(phone_id, "Signed out").status_code == 404
    assert service.store.get_device(phone_id).display_name == "Kitchen tablet"


def test_a_claimed_invitation_names_the_device_it_connected(tmp_path) -> None:
    client, service, registration = _application(tmp_path)
    owner_cookie, _owner_id, _owner_session = _session_cookie(
        service, registration, name="Owner"
    )
    headers = {"cookie": owner_cookie, "origin": "http://localhost:8080"}
    created = client.post(
        "/api/access/invitations", json={"layout": "compact"}, headers=headers
    )
    token = parse_qs(urlsplit(created.json()["invitation_url"]).query)[
        "invitation"
    ][0]
    waiting = client.get("/api/access/invitations", headers=headers).json()
    assert waiting["invitations"][0]["claimed_device_id"] is None

    claimed = service.claim_invitation(
        token,
        intended_origin="http://localhost:8080",
        display_name="Phone",
    )
    listed = client.get("/api/access/invitations", headers=headers).json()
    row = next(
        item
        for item in listed["invitations"]
        if item["id"] == created.json()["invitation"]["id"]
    )

    assert row["claimed_device_id"] == claimed.device.id
    assert token not in str(listed)
