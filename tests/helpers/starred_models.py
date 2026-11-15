"""A scratch profile with saved catalog models and stars: your models, without any provider.

Writes ``providers.json``, ``model_catalog_cache.json`` and ``model_settings.json``
under the given folder and fixes provider statuses, so model lists, pickers and
the status tool read only these files.
"""
from __future__ import annotations

import json
from pathlib import Path

CHAT = {"tasks": ["chat"], "input_modalities": ["text"], "output_modalities": ["text"], "tool_calling": True}


def starred_profile(folder: Path, monkeypatch, models: dict[str, str], *, stars: list[str] | None = None,
                    connected: set[str] | None = None, snapshot: dict | None = None) -> Path:
    """Save ``models`` ({ref: label}) in the catalog and star ``stars`` (default: all of them).

    Providers in ``connected`` (default: every provider named in ``models``) read as
    connected and running. Returns the ``model_settings.json`` path.
    """
    import row_bot.providers.config as provider_config
    import row_bot.providers.model_catalog_cache as cache
    from row_bot.providers import model_catalog, model_visibility

    folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(provider_config, "CONFIG_PATH", folder / "providers.json")
    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", folder / "model_catalog_cache.json")
    cloud = {}
    for ref, label in models.items():
        provider = ref.split(":")[1]
        cloud[ref] = {"provider": provider, "label": label, "ctx": 128_000,
                      "capabilities_snapshot": dict(snapshot or CHAT)}
    cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(
        cache.CACHE_VERSION, 1.0, cloud, [], {}, (), "test"))
    settings = folder / "model_settings.json"
    settings.write_text(json.dumps({"model_visibility": {"favourites": list(models if stars is None else stars)}}),
                        encoding="utf-8")
    monkeypatch.setattr(model_visibility, "_settings_path", lambda: settings)
    ready = {"configured": True, "runtime_enabled": True}
    providers = {ref.split(":")[1] for ref in models} if connected is None else connected
    monkeypatch.setattr(model_catalog, "_provider_status_by_id", lambda: {provider: ready for provider in providers})
    return settings
