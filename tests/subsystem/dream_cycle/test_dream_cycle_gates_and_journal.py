from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest


pytestmark = pytest.mark.subsystem


def fresh_dream_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))
    import row_bot.knowledge_graph as kg
    import row_bot.dream_cycle as dream_cycle

    importlib.reload(kg)
    return importlib.reload(dream_cycle)


def test_config_defaults_persist_and_invalid_windows_are_rejected(tmp_path, monkeypatch) -> None:
    dream_cycle = fresh_dream_cycle(tmp_path, monkeypatch)

    assert dream_cycle.get_config()["enabled"] is True
    dream_cycle.set_enabled(False)
    dream_cycle.set_window(22, 2)

    assert dream_cycle.is_enabled() is False
    assert dream_cycle.get_config()["window_start"] == 22
    assert dream_cycle.get_config()["window_end"] == 2

    for start, end in [(-1, 2), (1, 24), (5, 5)]:
        with pytest.raises(ValueError):
            dream_cycle.set_window(start, end)

    dream_cycle._CONFIG_FILE.write_text("{not-json", encoding="utf-8")
    assert dream_cycle.get_config()["enabled"] is True


def test_journal_is_append_only_capped_and_status_reports_last_run(tmp_path, monkeypatch) -> None:
    dream_cycle = fresh_dream_cycle(tmp_path, monkeypatch)

    for idx in range(105):
        dream_cycle._append_journal(
            {
                "cycle_id": f"cycle-{idx}",
                "timestamp": f"2026-06-23T00:{idx % 60:02d}:00+00:00",
                "summary": f"summary {idx}",
            }
        )

    journal = dream_cycle._load_journal()
    status = dream_cycle.get_dream_status()

    assert len(journal) == 100
    assert journal[0]["cycle_id"] == "cycle-5"
    assert dream_cycle.get_journal(limit=2)[0]["cycle_id"] == "cycle-103"
    assert status["last_run"] == "2026-06-23T00:44:00+00:00"
    assert status["last_summary"] == "summary 104"


def test_rejection_cache_records_unordered_pairs_and_expires(tmp_path, monkeypatch) -> None:
    dream_cycle = fresh_dream_cycle(tmp_path, monkeypatch)
    monkeypatch.setattr(dream_cycle, "_rejection_cache_ttl_days", lambda: 3)

    dream_cycle._record_rejection("b", "a")

    assert dream_cycle._is_pair_recently_rejected("a", "b") is True
    assert "a|b" in dream_cycle._load_rejection_cache()

    expired = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    dream_cycle._save_rejection_cache({"a|b": expired})
    assert dream_cycle._is_pair_recently_rejected("b", "a") is False


def test_window_and_today_checks_use_persisted_journal(tmp_path, monkeypatch) -> None:
    dream_cycle = fresh_dream_cycle(tmp_path, monkeypatch)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls(2026, 6, 23, 2, 30)
            return cls(2026, 6, 23, 2, 30, tzinfo=tz)

    monkeypatch.setattr(dream_cycle, "datetime", FixedDateTime)
    dream_cycle.set_window(1, 5)
    assert dream_cycle._in_dream_window() is True

    dream_cycle.set_window(22, 3)
    assert dream_cycle._in_dream_window() is True

    dream_cycle._save_journal([{"timestamp": "2026-06-23T01:00:00+00:00"}])
    assert dream_cycle._already_ran_today() is True

    dream_cycle._save_journal([{"timestamp": "2026-06-22T01:00:00+00:00"}])
    assert dream_cycle._already_ran_today() is False


def test_should_dream_rejects_each_gate_and_accepts_clean_path(tmp_path, monkeypatch) -> None:
    dream_cycle = fresh_dream_cycle(tmp_path, monkeypatch)

    gates = {
        "enabled": True,
        "today": False,
        "window": True,
        "idle": True,
        "ollama": False,
    }
    monkeypatch.setattr(dream_cycle, "is_enabled", lambda: gates["enabled"])
    monkeypatch.setattr(dream_cycle, "_already_ran_today", lambda: gates["today"])
    monkeypatch.setattr(dream_cycle, "_in_dream_window", lambda: gates["window"])
    monkeypatch.setattr(dream_cycle, "_is_idle", lambda: gates["idle"])
    monkeypatch.setattr(dream_cycle, "_is_ollama_busy", lambda: gates["ollama"])

    assert dream_cycle._should_dream() is True

    for key, value in [
        ("enabled", False),
        ("today", True),
        ("window", False),
        ("idle", False),
        ("ollama", True),
    ]:
        gates[key] = value
        assert dream_cycle._should_dream() is False
        gates[key] = {"enabled": True, "today": False, "window": True, "idle": True, "ollama": False}[key]


def test_ollama_busy_handles_active_loaded_and_failed_requests(tmp_path, monkeypatch) -> None:
    dream_cycle = fresh_dream_cycle(tmp_path, monkeypatch)

    import row_bot.models as models
    import urllib.request

    monkeypatch.setattr(models, "_ollama_base_url", lambda: "http://127.0.0.1:11434")

    class FakeResponse:
        def __init__(self, payload: bytes):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return self.payload

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(b'{"models":[{"num_requests":1}]}'),
    )
    assert dream_cycle._is_ollama_busy() is True

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(b'{"models":[{"num_requests":0,"size_vram":100}]}'),
    )
    assert dream_cycle._is_ollama_busy() is False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("down")))
    assert dream_cycle._is_ollama_busy() is False
