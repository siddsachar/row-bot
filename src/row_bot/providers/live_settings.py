"""Provider connection cards for the interactive settings page."""
from __future__ import annotations

from row_bot.docs_capture import docs_capture_fake_provider_status, docs_capture_provider_cards
from row_bot.providers.status import provider_status_cards


def read_live_provider_cards() -> dict:
    """Read the same connection facts as NiceGUI without exposing credentials."""
    cards = []
    source_cards = (
        docs_capture_provider_cards()
        if docs_capture_fake_provider_status()
        else provider_status_cards(refresh_tokens=False)
    )
    for card in source_cards:
        probe = card.get("last_runtime_probe") or {}
        if not isinstance(probe, dict):
            probe = {}
        cards.append({
            "provider_id": card["provider_id"],
            "display_name": card["display_name"],
            "group": {
                "Local": "local",
                "Subscription Accounts": "subscription",
                "API Providers": "api",
                "Custom Endpoints": "custom",
            }[card["group"]],
            "icon": card.get("icon") or "AI",
            "configured": bool(card.get("configured")),
            "source": card.get("source") or "",
            "runtime_enabled": bool(card.get("runtime_enabled")),
            "model_count": card.get("model_count"),
            "model_count_source": card.get("model_count_source") or "",
            "chat_count": int(card.get("chat_count") or 0),
            "media_count": int(card.get("media_count") or 0),
            "plan_type": card.get("plan_type") or "",
            "fingerprint": card.get("fingerprint") or "",
            "account_id_hash": card.get("account_id_hash") or "",
            "user_hash": card.get("user_hash") or "",
            "oauth_client_id_fingerprint": card.get("oauth_client_id_fingerprint") or "",
            "oauth_client_id_configured": bool(card.get("oauth_client_id_configured")),
            "external_reference_exists": bool(card.get("external_reference_exists")),
            "reconnect_required": str(card.get("token_health") or "") in {"missing", "expired", "error", "revoked"},
            "risk_label": card.get("risk_label") or "api_key",
            "last_runtime_probe_ok": bool(probe.get("ok")) if probe else None,
        })
    return {"schema_version": 1, "providers": cards}
