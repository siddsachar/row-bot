from __future__ import annotations

import asyncio
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_status_bar_render_cache_does_not_run_health_checks(monkeypatch) -> None:
    import row_bot.ui.status_bar as status_bar
    from row_bot.ui.status_checks import CheckResult

    def _fail(*_args, **_kwargs):
        raise AssertionError("render cache path must not run health checks")

    monkeypatch.setattr(status_bar, "_status_cache", {}, raising=False)
    monkeypatch.setattr(status_bar, "_cache_time", 0.0, raising=False)
    monkeypatch.setattr(status_bar, "run_all_checks", _fail)

    placeholder = status_bar._get_render_cached_results()
    assert [r.name for r in placeholder] == ["System"]
    assert placeholder[0].status == "inactive"

    cached = CheckResult("Cached", "ok", "ready")
    monkeypatch.setattr(status_bar, "_status_cache", {"Cached": cached}, raising=False)
    results = status_bar._get_render_cached_results()
    assert results == [cached]


def test_status_bar_force_refresh_populates_cache(monkeypatch) -> None:
    import row_bot.ui.status_bar as status_bar
    from row_bot.ui.status_checks import CheckResult

    result = CheckResult("Model", "ok", "test model")
    monkeypatch.setattr(status_bar, "run_all_checks", lambda: [result])
    monkeypatch.setattr(status_bar, "_status_cache", {}, raising=False)
    monkeypatch.setattr(status_bar, "_cache_time", 0.0, raising=False)

    assert status_bar._force_refresh() == [result]
    assert status_bar._status_cache == {"Model": result}
    assert status_bar._cache_time > 0


def test_status_bar_light_refresh_does_not_run_heavy_checks(monkeypatch) -> None:
    import row_bot.ui.status_bar as status_bar
    from row_bot.ui.status_checks import CheckResult

    light = CheckResult("Model", "ok", "ready")
    heavy = CheckResult("Network", "warn", "offline")
    monkeypatch.setattr(status_bar, "run_light_checks", lambda: [light])
    monkeypatch.setattr(status_bar, "run_heavy_checks", lambda: (_ for _ in ()).throw(AssertionError("heavy should not run")))
    monkeypatch.setattr(status_bar, "_status_cache", {"Network": heavy}, raising=False)
    monkeypatch.setattr(status_bar, "_cache_time", 0.0, raising=False)

    refreshed = status_bar._refresh_light_results(refresh_id=1)

    assert {result.name for result in refreshed} == {"Model", "Network"}
    assert status_bar._status_cache["Model"] is light
    assert status_bar._status_cache["Network"] is heavy


def test_status_bar_heavy_refresh_merges_full_result_set(monkeypatch) -> None:
    import row_bot.ui.status_bar as status_bar
    from row_bot.ui.status_checks import CheckResult

    model = CheckResult("Model", "ok", "ready")
    network = CheckResult("Network", "warn", "offline")
    captured: dict[str, object] = {}

    def _heavy(**kwargs):
        captured.update(kwargs)
        return [network]

    monkeypatch.setattr(status_bar, "run_heavy_checks", _heavy)
    monkeypatch.setattr(status_bar, "_status_cache", {"Model": model}, raising=False)
    monkeypatch.setattr(status_bar, "_cache_time", 0.0, raising=False)

    refreshed = status_bar._refresh_heavy_results(refresh_id=1)

    assert {result.name for result in refreshed} == {"Model", "Network"}
    assert [result.name for result in refreshed] == ["Model", "Network"]
    assert captured["live_ollama_probe"] is False


def test_status_bar_coalesces_full_refresh(monkeypatch) -> None:
    import row_bot.ui.status_bar as status_bar
    from row_bot.ui.status_checks import CheckResult

    calls = {"count": 0}
    result = CheckResult("Model", "ok", "ready")

    async def _io_bound(fn, *args, **kwargs):
        await asyncio.sleep(0.01)
        return fn(*args, **kwargs)

    def _refresh():
        calls["count"] += 1
        return [result]

    monkeypatch.setattr(status_bar.run, "io_bound", _io_bound)
    monkeypatch.setattr(status_bar, "_force_refresh_with_id", lambda refresh_id: _refresh())
    monkeypatch.setattr(status_bar, "_status_refresh_task", None, raising=False)
    monkeypatch.setattr(status_bar, "_status_refresh_task_id", 0, raising=False)

    async def _exercise():
        return await asyncio.gather(
            status_bar._coalesced_force_refresh(),
            status_bar._coalesced_force_refresh(),
        )

    first, second = asyncio.run(_exercise())

    assert calls["count"] == 1
    assert first == [result]
    assert second == [result]


def test_status_checks_log_per_check_timing(monkeypatch) -> None:
    import row_bot.ui.status_checks as status_checks
    from row_bot.ui.status_checks import CheckResult

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
    import row_bot.ui.status_checks as status_checks
    from row_bot.ui.status_checks import CheckResult

    calls = {"count": 0}

    def _factory():
        calls["count"] += 1
        return CheckResult("Probe", "ok", "ready")

    monkeypatch.setattr(status_checks, "_probe_cache", {}, raising=False)

    assert status_checks._cached_probe("probe", _factory).detail == "ready"
    assert status_checks._cached_probe("probe", _factory).detail == "ready"
    assert calls["count"] == 1


def test_routine_heavy_refresh_skips_ollama_probe_when_not_using_local_model(monkeypatch) -> None:
    import row_bot.models as models
    import row_bot.ui.status_checks as status_checks

    calls: list[dict] = []

    def _fake_log(name, elapsed_ms, **metadata):
        calls.append({"name": name, "elapsed_ms": elapsed_ms, **metadata})

    def _reachable(*_args, **_kwargs):
        raise AssertionError("routine refresh should not probe local Ollama when not in use")

    monkeypatch.setattr(status_checks, "_probe_cache", {}, raising=False)
    monkeypatch.setattr(status_checks, "HEAVY_CHECKS", [status_checks.check_ollama])
    monkeypatch.setattr(status_checks, "log_ui_perf", _fake_log)
    monkeypatch.setattr(models, "get_current_model", lambda: "model:codex:gpt-5.5")
    monkeypatch.setattr(models, "is_cloud_model", lambda _model: True)
    monkeypatch.setattr(models, "_ollama_reachable", _reachable)

    results = status_checks.run_heavy_checks(live_ollama_probe=False)

    assert len(results) == 1
    assert results[0].name == "Ollama"
    assert results[0].status == "inactive"
    assert results[0].metadata["live_probe"] is False
    assert results[0].metadata["skip_reason"] == "routine_refresh_not_using_local_ollama"
    assert calls[0]["name"] == "home.status_check.check_ollama"
    assert calls[0]["live_probe"] is False
    assert calls[0]["skip_reason"] == "routine_refresh_not_using_local_ollama"


def test_live_heavy_refresh_still_probes_ollama(monkeypatch) -> None:
    import row_bot.models as models
    import row_bot.ui.status_checks as status_checks

    probed = {"count": 0}

    def _reachable(*_args, **_kwargs):
        probed["count"] += 1
        return True

    monkeypatch.setattr(status_checks, "_probe_cache", {}, raising=False)
    monkeypatch.setattr(status_checks, "HEAVY_CHECKS", [status_checks.check_ollama])
    monkeypatch.setattr(models, "get_current_model", lambda: "model:codex:gpt-5.5")
    monkeypatch.setattr(models, "is_cloud_model", lambda _model: True)
    monkeypatch.setattr(models, "_ollama_reachable", _reachable)

    results = status_checks.run_heavy_checks(live_ollama_probe=True)

    assert probed["count"] == 1
    assert results[0].name == "Ollama"
    assert results[0].status == "ok"
    assert results[0].metadata["live_probe"] is True


def test_home_lazily_builds_non_workflow_tabs_and_graph() -> None:
    src = _read("src/row_bot/ui/home.py")
    assert "_tab_loaders" in src
    assert "_loaded_tabs" in src
    assert "_render_lazy_placeholder" in src
    assert '_tab_loaders["Developer"] = _build_developer_panel' in src
    assert '_tab_loaders["Designer"] = _build_designer_panel' in src
    assert '_tab_loaders["Knowledge"] = _build_knowledge_panel' in src
    assert '_tab_loaders["Activity"] = _build_activity_panel' in src
    assert 'if _initial_tab_name == "Knowledge":' in src
    assert "home.tab.build.knowledge" in src
    assert "build_graph_panel()" in src


def test_home_and_picker_perf_diagnostics_are_present() -> None:
    home_src = _read("src/row_bot/ui/home.py")
    status_src = _read("src/row_bot/ui/status_bar.py")
    picker_src = _read("src/row_bot/ui/chat_components.py")

    assert "home.status_bar.cached" in status_src
    assert "home.status_bar.force_refresh" in status_src
    assert "home.status_bar.aggregate" in status_src
    assert "home.status_bar.light_refresh" in status_src
    assert "home.status_bar.heavy_refresh" in status_src
    assert "home.status_bar.async_refresh" in status_src
    assert "await _coalesced_force_refresh()" in status_src
    assert "diag_results = _force_refresh()" not in status_src
    assert "home.tab.build.workflows" in home_src
    assert "home.tab.build.developer" in home_src
    assert "home.tab.build.designer" in home_src
    assert "home.tab.build.knowledge" in home_src
    assert "home.tab.build.activity" in home_src
    assert "chat.model_picker.options.load" in picker_src
    assert "chat.model_picker.options.apply" in picker_src
