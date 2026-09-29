from __future__ import annotations


def test_status_checks_log_per_check_timing(monkeypatch) -> None:
    import row_bot.status_checks as status_checks
    from row_bot.status_checks import CheckResult

    calls: list[dict] = []

    def _fake_log(name, elapsed_ms, **metadata):
        calls.append({"name": name, "elapsed_ms": elapsed_ms, **metadata})

    def _check():
        return CheckResult("Fake", "ok", "ready")

    monkeypatch.setattr(status_checks, "log_ui_perf", _fake_log)

    results = status_checks._run_timed_check(_check, kind="light")

    assert results[0].name == "Fake"
    assert calls[0]["name"] == "home.status_check._check"
    assert calls[0]["kind"] == "light"
    assert calls[0]["results"] == 1
    assert calls[0]["status_counts"] == {"ok": 1}


def test_status_checks_cache_expensive_probes(monkeypatch) -> None:
    import row_bot.status_checks as status_checks
    from row_bot.status_checks import CheckResult

    calls = {"count": 0}

    def _factory():
        calls["count"] += 1
        return CheckResult("Probe", "ok", "ready")

    monkeypatch.setattr(status_checks, "_probe_cache", {}, raising=False)

    assert status_checks._cached_probe("probe", _factory).detail == "ready"
    assert status_checks._cached_probe("probe", _factory).detail == "ready"
    assert calls["count"] == 1


def test_routine_heavy_refresh_reports_running_ollama_for_cloud_model(monkeypatch) -> None:
    import row_bot.models as models
    import row_bot.status_checks as status_checks

    calls: list[dict] = []
    probe_kwargs: list[dict] = []

    def _fake_log(name, elapsed_ms, **metadata):
        calls.append({"name": name, "elapsed_ms": elapsed_ms, **metadata})

    def _reachable(*_args, **kwargs):
        probe_kwargs.append(dict(kwargs))
        return True

    monkeypatch.setattr(status_checks, "_probe_cache", {}, raising=False)
    monkeypatch.setattr(status_checks, "HEAVY_CHECKS", [status_checks.check_ollama])
    monkeypatch.setattr(status_checks, "log_ui_perf", _fake_log)
    monkeypatch.setattr(models, "get_current_model", lambda: "model:codex:gpt-5.5")
    monkeypatch.setattr(models, "is_cloud_model", lambda _model: True)
    monkeypatch.setattr(models, "_ollama_reachable", _reachable)

    results = status_checks.run_heavy_checks(live_ollama_probe=False)

    assert len(results) == 1
    assert results[0].name == "Ollama"
    assert results[0].status == "ok"
    assert results[0].detail == "Server reachable"
    assert results[0].metadata["live_probe"] is False
    assert results[0].metadata["probe_timeout_seconds"] == 0.2
    assert "skip_reason" not in results[0].metadata
    assert probe_kwargs == [{"timeout": 0.2}]
    assert calls[0]["name"] == "home.status_check.check_ollama"
    assert calls[0]["live_probe"] is False
    assert calls[0]["probe_timeout_seconds"] == 0.2


def test_routine_heavy_refresh_reports_offline_ollama_for_cloud_model(monkeypatch) -> None:
    import row_bot.models as models
    import row_bot.status_checks as status_checks

    monkeypatch.setattr(status_checks, "_probe_cache", {}, raising=False)
    monkeypatch.setattr(status_checks, "HEAVY_CHECKS", [status_checks.check_ollama])
    monkeypatch.setattr(models, "get_current_model", lambda: "model:codex:gpt-5.5")
    monkeypatch.setattr(models, "is_cloud_model", lambda _model: True)
    monkeypatch.setattr(models, "_ollama_reachable", lambda **_kwargs: False)

    results = status_checks.run_heavy_checks(live_ollama_probe=False)

    assert results[0].name == "Ollama"
    assert results[0].status == "inactive"
    assert results[0].detail == "Server offline"
    assert results[0].metadata["live_probe"] is False
    assert results[0].metadata["probe_timeout_seconds"] == 0.2


def test_routine_heavy_refresh_warns_when_selected_local_ollama_is_offline(monkeypatch) -> None:
    import row_bot.models as models
    import row_bot.status_checks as status_checks

    monkeypatch.setattr(status_checks, "_probe_cache", {}, raising=False)
    monkeypatch.setattr(status_checks, "HEAVY_CHECKS", [status_checks.check_ollama])
    monkeypatch.setattr(models, "get_current_model", lambda: "model:ollama:qwen3:14b")
    monkeypatch.setattr(models, "is_cloud_model", lambda _model: False)
    monkeypatch.setattr(models, "_ollama_reachable", lambda **_kwargs: False)

    results = status_checks.run_heavy_checks(live_ollama_probe=False)

    assert results[0].name == "Ollama"
    assert results[0].status == "warn"
    assert results[0].detail == "Local model server unreachable"
    assert results[0].metadata["live_probe"] is False
    assert results[0].metadata["probe_timeout_seconds"] == 0.2


def test_live_heavy_refresh_still_probes_ollama(monkeypatch) -> None:
    import row_bot.models as models
    import row_bot.status_checks as status_checks

    probed = {"count": 0}
    probe_kwargs: list[dict] = []

    def _reachable(*_args, **kwargs):
        probed["count"] += 1
        probe_kwargs.append(dict(kwargs))
        return True

    monkeypatch.setattr(status_checks, "_probe_cache", {}, raising=False)
    monkeypatch.setattr(status_checks, "HEAVY_CHECKS", [status_checks.check_ollama])
    monkeypatch.setattr(models, "get_current_model", lambda: "model:codex:gpt-5.5")
    monkeypatch.setattr(models, "is_cloud_model", lambda _model: True)
    monkeypatch.setattr(models, "_ollama_reachable", _reachable)

    results = status_checks.run_heavy_checks(live_ollama_probe=True)

    assert probed["count"] == 1
    assert probe_kwargs == [{"timeout": 1.0}]
    assert results[0].name == "Ollama"
    assert results[0].status == "ok"
    assert results[0].metadata["live_probe"] is True
    assert results[0].metadata["probe_timeout_seconds"] == 1.0


def test_live_ollama_refresh_ignores_stale_probe_cache(monkeypatch) -> None:
    import time

    import row_bot.models as models
    import row_bot.status_checks as status_checks
    from row_bot.status_checks import CheckResult

    probed = {"count": 0}

    def _reachable(*_args, **_kwargs):
        probed["count"] += 1
        return True

    monkeypatch.setattr(
        status_checks,
        "_probe_cache",
        {"ollama:live": (time.time(), CheckResult("Ollama", "inactive", "stale"))},
        raising=False,
    )
    monkeypatch.setattr(status_checks, "HEAVY_CHECKS", [status_checks.check_ollama])
    monkeypatch.setattr(models, "get_current_model", lambda: "model:codex:gpt-5.5")
    monkeypatch.setattr(models, "is_cloud_model", lambda _model: True)
    monkeypatch.setattr(models, "_ollama_reachable", _reachable)

    results = status_checks.run_heavy_checks(live_ollama_probe=True)

    assert probed["count"] == 1
    assert results[0].status == "ok"
    assert results[0].detail == "Server reachable"
