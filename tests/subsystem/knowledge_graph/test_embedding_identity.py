"""Explicit indexing keeps provider construction bound to captured metadata."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.subsystem


def config():
    from row_bot.embedding_config import DEFAULT_CONFIG
    return {**deepcopy(DEFAULT_CONFIG), "provider": "cloud", "cloud_model": "openai:text-embedding-3-small", "dimension": 512}


def test_explicit_provider_does_not_reread_or_alias_mutating_configuration(monkeypatch):
    from row_bot import embedding_providers as providers
    from row_bot.embedding_config import active_embedding_metadata
    captured = config()
    expected = active_embedding_metadata(captured)
    monkeypatch.setattr(providers, "_provider", None)
    monkeypatch.setattr(providers, "_provider_key", None)
    monkeypatch.setattr(providers, "get_embedding_config", lambda: pytest.fail("Captured provider reread active configuration"))
    monkeypatch.setattr(providers, "ensure_embedding_runtime_available", lambda cfg: None)
    def build(cfg):
        captured["cloud_model"] = "openai:text-embedding-3-large"
        return SimpleNamespace(metadata=active_embedding_metadata(cfg))
    monkeypatch.setattr(providers, "_build_provider", build)
    provider = providers.get_embedding_provider(captured)
    assert provider.metadata == expected
    assert captured["cloud_model"] != config()["cloud_model"]


def test_document_factory_passes_one_captured_config_through_readiness_and_construction(monkeypatch):
    from row_bot import documents
    captured = config()
    observed = []
    def ready(cfg):
        observed.append(deepcopy(cfg))
        captured["cloud_model"] = "openai:text-embedding-3-large"
    def provider(cfg):
        observed.append(deepcopy(cfg))
        return "synthetic provider"
    monkeypatch.setattr(documents, "ensure_embedding_runtime_available", ready)
    monkeypatch.setattr(documents, "get_embedding_provider", provider)
    assert documents.get_embedding_model(captured) == "synthetic provider"
    assert observed == [config(), config()]


def test_cloud_recall_preserves_captured_identity_without_active_config_read(monkeypatch):
    from row_bot import embedding_providers as providers
    captured = config()
    observed = []
    monkeypatch.setattr(providers, "get_embedding_config", lambda: pytest.fail("Recall reread configuration"))
    def build(cfg):
        captured["dimension"] = 3072
        observed.append(deepcopy(cfg))
        return "captured cloud provider"
    monkeypatch.setattr(providers, "get_embedding_provider", build)
    assert providers.get_embedding_provider_for_recall(captured) == "captured cloud provider"
    assert observed == [config()]


def test_background_local_load_and_status_share_captured_config(monkeypatch):
    from row_bot import embedding_providers as providers
    from row_bot.embedding_config import DEFAULT_CONFIG
    captured = deepcopy(DEFAULT_CONFIG)
    expected = deepcopy(captured)
    observed = []
    monkeypatch.setattr(providers, "get_embedding_config", lambda: pytest.fail("Background load reread configuration"))
    monkeypatch.setattr(providers, "_begin_local_load", lambda key, force=False: (123, True))
    class SyntheticThread:
        def __init__(self, *, target, args, **kwargs):
            self.args = args
            observed.append(deepcopy(args[0]))
            captured["local_model"] = "nomic-v1.5"
        def start(self):
            observed.append(deepcopy(self.args[0]))
    monkeypatch.setattr(providers.threading, "Thread", SyntheticThread)
    def status(*, probe_cache, config):
        assert probe_cache is False
        observed.append(deepcopy(config))
        return {"state": "loading"}
    monkeypatch.setattr(providers, "get_local_embedding_status", status)
    assert providers.start_local_embedding_load(config=captured) == {"state": "loading"}
    assert observed == [expected, expected, expected]
