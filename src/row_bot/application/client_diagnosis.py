"""Explicit, bounded system diagnosis for the local React client."""

from __future__ import annotations

from row_bot.status_checks import order_status_results, run_all_checks


def run_system_diagnosis() -> dict[str, object]:
    """Run the same checks as the NiceGUI diagnosis on explicit user request."""
    results = order_status_results(run_all_checks())[:64]
    return {
        "schema_version": 1,
        "checks": [
            {
                "name": result.name[:128],
                "status": result.status if result.status in {"ok", "warn", "error", "inactive"} else "error",
                "detail": result.detail[:512],
                "checked_at": result.checked_at,
                "settings_tab": result.settings_tab[:64],
            }
            for result in results
        ],
    }
