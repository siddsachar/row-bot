from __future__ import annotations

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


def test_home_lazily_builds_non_workflow_tabs_and_graph() -> None:
    src = _read("ui/home.py")
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
    home_src = _read("ui/home.py")
    status_src = _read("ui/status_bar.py")
    picker_src = _read("ui/chat_components.py")

    assert "home.status_bar.cached" in status_src
    assert "home.status_bar.force_refresh" in status_src
    assert "home.status_bar.async_refresh" in status_src
    assert "home.tab.build.workflows" in home_src
    assert "home.tab.build.developer" in home_src
    assert "home.tab.build.designer" in home_src
    assert "home.tab.build.knowledge" in home_src
    assert "home.tab.build.activity" in home_src
    assert "chat.model_picker.options.load" in picker_src
    assert "chat.model_picker.options.apply" in picker_src
