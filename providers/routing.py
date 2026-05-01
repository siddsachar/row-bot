from __future__ import annotations

from providers.config import load_provider_config


def list_routing_profiles() -> list[dict]:
    return [route for route in load_provider_config().get("routes", []) if isinstance(route, dict)]


def get_routing_profile(route_id: str) -> dict | None:
    for route in list_routing_profiles():
        if route.get("id") == route_id:
            return route
    return None