"""Independent plugin-worker authority and failed-quiescence regressions."""
from __future__ import annotations

import asyncio

import pytest

from tests.subsystem.plugins.test_plugin_worker import (
    _channel_load,
    _load,
    worker_fixture as prepared_worker_fixture,
)

pytestmark = pytest.mark.subsystem
worker_fixture = prepared_worker_fixture


@pytest.mark.parametrize("changed", ["source", "environment"])
@pytest.mark.parametrize("entry", ["channel", "webhook", "callback"])
def test_every_worker_boundary_rejects_changed_prepared_generation(worker_fixture, changed, entry):
    from row_bot.plugins import webhooks
    from row_bot.plugins.api import PluginWebhookRequest

    fixture = worker_fixture
    api, channel = _channel_load(fixture)
    root = fixture.source if changed == "source" else fixture.environment
    (root / "review_generation_changed.py").write_text("value = 1\n", encoding="utf-8")
    try:
        with pytest.raises(RuntimeError, match=f"worker_{changed}_changed"):
            if entry == "channel":
                channel.send_message("synthetic-recipient", "must not send")
            elif entry == "webhook":
                request = PluginWebhookRequest("POST", "/fixture", {}, {}, b"synthetic")
                record = webhooks._webhooks[("sample-plugin", "fixture")]
                asyncio.run(record.handler(request))
            else:
                # A persistent listener can request a host callback independently
                # of a host-to-worker call; use the actual callback dispatcher.
                api._worker._handle_request("api", {
                    "method": "set_config", "args": ["late", "must not save"], "kwargs": {},
                })
        assert fixture.state.get_plugin_config("sample-plugin", "sent") is None
        assert fixture.state.get_plugin_config("sample-plugin", "late") is None
    finally:
        fixture.loader._cleanup_plugin_runtime("sample-plugin")


def test_failed_process_quiescence_blocks_replacement_without_pending_callbacks(worker_fixture, monkeypatch):
    fixture = worker_fixture
    _load(fixture)
    old_api = fixture.loader._registrations["sample-plugin"]
    old_worker = old_api._worker
    real_close = old_worker.close

    def unconfirmed_stop():
        # Model the actual close contract when native tree termination fails:
        # authority is retired, but the process has not acknowledged quiescence.
        old_worker._closed.set()
        return False

    monkeypatch.setattr(old_worker, "close", unconfirmed_stop)
    try:
        assert not old_worker.has_pending_callbacks()
        fixture.loader._cleanup_plugin_runtime("sample-plugin")
        assert old_worker._process.poll() is None
        replacement = fixture.loader._load_single_plugin(fixture.source)
        assert not replacement.success, "A replacement started while the retired native worker was still alive"
        assert fixture.loader._registrations.get("sample-plugin") is old_api
        # A later real cleanup proof releases the same existing admission;
        # failure must not require deleting state or replacing the old owner.
        monkeypatch.setattr(old_worker, "close", real_close)
        assert real_close()
        assert not old_worker.has_pending_work()
        assert fixture.loader._load_single_plugin(fixture.source).success
        assert fixture.loader._registrations["sample-plugin"] is not old_api
    finally:
        monkeypatch.setattr(old_worker, "close", real_close)
        real_close()
        fixture.loader._cleanup_plugin_runtime("sample-plugin")
