from __future__ import annotations

from row_bot.providers.config import load_provider_config


def task_route_model(purpose: str) -> str:
    """The model named for one background purpose, or "".

    ``task_routes`` in providers.json maps a purpose (``extraction``, ``dream``,
    ``verifier`` ...) to a provider-qualified model ref. Nothing sets it today and
    there is no setting for it (D1), so background calls keep their own model rule.
    It is the place a cheaper model for one purpose would be named later, without
    redesigning the background resolver.
    """
    routes = load_provider_config().get("task_routes")
    if not isinstance(routes, dict):
        return ""
    value = routes.get(str(purpose or ""))
    return value.strip() if isinstance(value, str) else ""
