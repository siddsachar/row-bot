from __future__ import annotations


def test_dream_snapshot_preserves_unknown_provider_model_counts(monkeypatch):
    import row_bot.dream_cycle as dream_cycle

    monkeypatch.setattr("row_bot.providers.selection.list_quick_choices", lambda _surface: [])
    monkeypatch.setattr(
        "row_bot.providers.status.provider_status_cards",
        lambda: [
            {
                "display_name": "ChatGPT / Codex",
                "configured": True,
                "source": "external_cli",
                "runtime_enabled": True,
                "model_count": None,
                "model_count_status": "unknown",
            }
        ],
    )

    snapshot = dream_cycle._collect_system_snapshot()

    assert "ChatGPT / Codex" in snapshot
    assert "models=unknown" in snapshot
    assert "models=0" not in snapshot


def test_provider_summary_distinguishes_unknown_from_verified_empty(monkeypatch):
    import row_bot.providers.status as provider_status

    monkeypatch.setattr(provider_status, "list_quick_choices", lambda _surface: [])
    monkeypatch.setattr(
        provider_status,
        "provider_status_cards",
        lambda: [
            {
                "provider_id": "codex",
                "display_name": "ChatGPT / Codex",
                "configured": True,
                "runtime_enabled": True,
                "source": "external_cli",
                "model_count": None,
                "model_count_status": "unknown",
            },
            {
                "provider_id": "openrouter",
                "display_name": "OpenRouter",
                "configured": True,
                "runtime_enabled": True,
                "source": "keyring",
                "model_count": 0,
                "model_count_status": "empty_verified",
            },
        ],
    )

    summary = provider_status.summarize_providers()

    assert "ChatGPT / Codex: configured (external_cli), catalog count unknown" in summary
    assert "OpenRouter: configured (keyring), 0 catalog model(s)" in summary
