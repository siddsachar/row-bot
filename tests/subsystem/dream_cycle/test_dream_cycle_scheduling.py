from __future__ import annotations

import importlib

import pytest


pytestmark = pytest.mark.subsystem


def fresh_dream_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))
    import row_bot.knowledge_graph as kg
    import row_bot.dream_cycle as dream_cycle

    importlib.reload(kg)
    return importlib.reload(dream_cycle)


def test_should_dream_requires_enabled_idle_window_and_free_ollama(tmp_path, monkeypatch) -> None:
    dream_cycle = fresh_dream_cycle(tmp_path, monkeypatch)

    monkeypatch.setattr(dream_cycle, "is_enabled", lambda: True)
    monkeypatch.setattr(dream_cycle, "_already_ran_today", lambda: False)
    monkeypatch.setattr(dream_cycle, "_in_dream_window", lambda: True)
    monkeypatch.setattr(dream_cycle, "_is_idle", lambda: True)
    monkeypatch.setattr(dream_cycle, "_is_ollama_busy", lambda: False)

    assert dream_cycle._should_dream() is True

    monkeypatch.setattr(dream_cycle, "_is_idle", lambda: False)
    assert dream_cycle._should_dream() is False

    monkeypatch.setattr(dream_cycle, "_is_idle", lambda: True)
    monkeypatch.setattr(dream_cycle, "_is_ollama_busy", lambda: True)
    assert dream_cycle._should_dream() is False

    monkeypatch.setattr(dream_cycle, "_is_ollama_busy", lambda: False)
    monkeypatch.setattr(dream_cycle, "_already_ran_today", lambda: True)
    assert dream_cycle._should_dream() is False


def test_run_dream_cycle_below_minimum_records_journal_skip(tmp_path, monkeypatch) -> None:
    dream_cycle = fresh_dream_cycle(tmp_path, monkeypatch)

    summary = dream_cycle.run_dream_cycle()
    journal = dream_cycle.get_journal(limit=1)

    assert summary["merges"] == []
    assert summary["enrichments"] == []
    assert summary["inferred_relations"] == []
    assert "below minimum" in summary["summary"]
    assert journal[-1]["cycle_id"] == summary["cycle_id"]
