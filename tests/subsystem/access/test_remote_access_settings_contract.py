from __future__ import annotations

from row_bot.access.access_routes import (
    AccessRouteKind,
    build_route_inventory,
)
from row_bot.access.config import AccessConfig
from row_bot.access.runtime_policy import RuntimeAccessPolicy
from row_bot.tunnel import TunnelManager, TunnelProvider


def test_managed_ngrok_inventory_tracks_registration(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    managed_origin = "https://managed-phone.ngrok-free.app"
    operator_origin = "https://operator-phone.ngrok-free.app"

    class FakeProvider(TunnelProvider):
        def __init__(self) -> None:
            self.active: dict[int, str] = {}

        def start(self, port: int, label: str = "") -> str:  # noqa: ARG002
            self.active[port] = managed_origin
            return managed_origin

        def stop(self, port: int) -> None:
            self.active.pop(port, None)

        def stop_all(self) -> None:
            self.active.clear()

        def get_url(self, port: int) -> str | None:
            return self.active.get(port)

        def is_available(self) -> bool:
            return True

        def active_tunnels(self) -> dict[int, str]:
            return dict(self.active)

    provider = FakeProvider()
    policy = RuntimeAccessPolicy(
        AccessConfig.build(
            deployment_mode="server",
            allowed_hosts=("localhost", "operator-phone.ngrok-free.app"),
            public_origins=(operator_origin,),
        )
    )
    manager = TunnelManager(managed_origin_registrar=policy)
    manager.set_provider(provider)

    def inventory():
        return build_route_inventory(
            port=8080,
            ngrok_url=manager.get_url(8080),
            reverse_proxy_origins=(operator_origin,),
        )

    provider.active[8080] = operator_origin
    operator_inventory = inventory()
    assert operator_inventory.by_kind(AccessRouteKind.NGROK) == ()
    assert operator_inventory.by_kind(AccessRouteKind.REVERSE_PROXY)[0].origin == (
        operator_origin
    )
    provider.active.clear()

    assert manager.start_tunnel(8080, label="main_app") == managed_origin
    active_inventory = inventory()
    managed_route = active_inventory.by_kind(AccessRouteKind.NGROK)[0]
    assert managed_route.origin == managed_origin
    assert managed_route in active_inventory.invitation_routes

    manager.stop_tunnel(8080)
    assert inventory().by_kind(AccessRouteKind.NGROK) == ()


def test_route_inventory_can_be_injected_without_detection() -> None:
    inventory = build_route_inventory(port=9090)

    assert inventory.preferred_invitation_origin() is None
