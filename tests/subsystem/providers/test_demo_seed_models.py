"""The docs and demo capture star their local models through the app's own model list."""

from __future__ import annotations

from pathlib import Path

from scripts.docs import seed_real_app_demo_data as seeder


def test_the_demo_seed_stars_its_local_models(tmp_path: Path, reload_for_data_dir, monkeypatch) -> None:
    cache, visibility = reload_for_data_dir(
        tmp_path / "data",
        "row_bot.providers.model_catalog_cache",
        "row_bot.providers.model_visibility",
    )
    monkeypatch.setattr(cache, "refresh_model_catalog_cache", lambda **_kwargs: None)

    seeder._seed_model_catalog()

    stars = visibility.read_model_visibility().favourites
    assert {"model:ollama:llama3.1:8b", "model:ollama:qwen3:8b"} <= set(stars)
