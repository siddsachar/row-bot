"""Run on this computer (E1) seen from Learning: its managed endpoint counts as a model on this computer
(no tokens of the monthly limit), whether or not its server is running. Learning reads that from the
endpoint's loopback address, so the endpoint must keep one while the server is stopped."""

from __future__ import annotations

import pytest

# Imported at collection, before a test moves the data folder: patching its path then restores the real one.
import row_bot.providers.config as provider_config
from row_bot import local_runtime
from row_bot.local_runtime import models, server

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


def test_the_managed_endpoint_is_a_local_provider_with_its_server_stopped_and_running(data_dir, monkeypatch):
    from row_bot.learning.spending import is_local_provider
    from row_bot.providers import custom

    monkeypatch.setattr(provider_config, "CONFIG_PATH", data_dir / "providers.json")
    monkeypatch.setattr("row_bot.local_runtime.install.installed_models",
                        lambda root=None: [models.get("qwen3.8-27b-q2")])
    assert not is_local_provider(local_runtime.PROVIDER_ID)  # nothing registered yet

    server.save_endpoint()  # registered when a model finishes downloading; nothing is started
    assert server.get().state == "stopped"
    endpoint = custom.get_custom_endpoint(local_runtime.PROVIDER_ID)
    assert endpoint["base_url"].startswith("http://127.0.0.1")
    assert is_local_provider(local_runtime.PROVIDER_ID)

    server.save_endpoint(port=41234)  # what a start saves: its port; still loopback
    assert is_local_provider(local_runtime.PROVIDER_ID)
