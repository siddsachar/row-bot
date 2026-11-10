"""The backup model (#140): which failures it answers, which turns it may take, and its setting.

Pure decisions over fake exceptions and a scratch ``model_settings.json``; nothing calls a provider.
"""
from __future__ import annotations

import json

import pytest

from row_bot.providers import backup
from row_bot.providers.runtime import ProviderSignInRequired

pytestmark = pytest.mark.subsystem

MAIN = "model:ollama:qwen3:8b"
CLOUD = "model:openai:gpt-5-mini"
LOCAL = "model:ollama:llama4"


class StatusError(Exception):
    """A provider SDK's HTTP error, with its status code."""

    def __init__(self, status: int, text: str = "") -> None:
        super().__init__(text or f"Error code: {status}")
        self.status_code = status


class ConnectError(Exception):
    """As httpx names a refused connection."""


@pytest.fixture
def settings(monkeypatch, tmp_path):
    from row_bot.providers import saved_model_settings

    path = tmp_path / "model_settings.json"
    monkeypatch.setattr(saved_model_settings, "SETTINGS_PATH", path)
    return path


@pytest.mark.parametrize(("error", "why"), [
    (ConnectionError("Failed to connect to Ollama. Please check that Ollama is running."), "it couldn't be reached"),
    (ConnectError("[WinError 10061] No connection could be made because the target machine actively refused it"),
     "it couldn't be reached"),
    (RuntimeError("peer closed connection without sending complete message body"), "it couldn't be reached"),
    (StatusError(503, "Service Unavailable"), "it had a server error"),
    (StatusError(500), "it had a server error"),
    (TimeoutError("The read operation timed out"), "it timed out"),
    (RuntimeError("context deadline exceeded (Client.Timeout exceeded while awaiting headers)"), "it timed out"),
    (StatusError(429, "Rate limit reached for gpt-5"), "it's busy right now"),
    (StatusError(402, "Insufficient credits"), "it's out of credit"),
    (RuntimeError("You exceeded your current quota"), "it's out of credit"),
    (StatusError(401, "Incorrect API key provided"), "its sign-in expired"),
    (ProviderSignInRequired("codex", "ChatGPT", "Sign in to ChatGPT again"), "its sign-in expired"),
    (StatusError(404, "model 'qwen3:8b' not found"), "it isn't available"),
    (backup.MainModelNotReady("Ollama isn't running."), "it isn't available"),
])
def test_outages_sign_ins_and_limits_go_to_the_backup(error, why):
    assert backup.reason(error) == why
    wrapped = RuntimeError("The model call failed")
    wrapped.__cause__ = error
    assert backup.reason(wrapped) == why, "an outage stays one when a runtime wraps it"


@pytest.mark.parametrize("error", [
    StatusError(400, "Error code: 400 - invalid_request_error"),
    StatusError(413, "Request too large"),
    StatusError(422, "Unprocessable reasoning effort"),
    ValueError("Error code: 400 - prompt is too long: 250000 tokens > 200000 maximum"),
    RuntimeError("This model's maximum context length is 128000 tokens"),
    RuntimeError("This model does not support tools"),
    RuntimeError("Your request was flagged by our content policy"),
    RuntimeError("Something nobody recognises"),
])
def test_a_request_shaped_or_unknown_failure_never_goes_to_the_backup(error):
    assert backup.reason(error) is None


def test_the_setting_is_off_by_default_and_saved_beside_the_main_model(settings):
    assert backup.read_setting() == {"enabled": False, "ref": ""}
    assert backup.backup_model() is None
    settings.write_text(json.dumps({"model": MAIN, "context_size": 32768}), encoding="utf-8")

    backup.save_setting(ref=CLOUD)
    assert backup.backup_model() is None, "chosen but switched off"
    backup.save_setting(enabled=True)

    assert backup.backup_model() == CLOUD
    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved["model"] == MAIN and saved["context_size"] == 32768
    assert saved["backup_model"] == {"enabled": True, "ref": CLOUD}


def _config(model: str = MAIN, thread_id: str = "") -> dict:
    return {"configurable": {"thread_id": thread_id, "model_override": model}}


def test_a_turn_has_a_backup_only_when_switched_on_and_another_model(settings):
    assert backup.plan(_config()) is None
    backup.save_setting(enabled=True, ref=CLOUD)
    assert backup.plan(_config()) == backup.Plan(MAIN, CLOUD)
    assert backup.plan(_config(CLOUD)) is None, "the backup never backs itself up"
    assert backup.plan(_config("model:ollama:row-bot-personal-v3")) is None, "Learning finishes its own"


def test_an_incognito_chat_never_goes_to_a_cloud_backup(settings, tmp_path, reload_for_data_dir):
    (threads,) = reload_for_data_dir(tmp_path / "data", "row_bot.threads")
    threads.create_thread("Private", thread_id="incognito-chat", seed_default_skills=False)
    threads.set_thread_incognito("incognito-chat", True)

    backup.save_setting(enabled=True, ref=CLOUD)
    assert backup.plan(_config(thread_id="incognito-chat")) is None
    backup.save_setting(ref=LOCAL)
    assert backup.plan(_config(thread_id="incognito-chat")) == backup.Plan(MAIN, LOCAL)


def test_the_note_names_both_models_and_why():
    assert backup.note("qwen3:8b", "GPT-5 mini", "it couldn't be reached") == (
        "qwen3:8b didn't answer (it couldn't be reached), so GPT-5 mini took over.")


def test_models_are_named_as_the_catalog_names_them(monkeypatch, tmp_path):
    from row_bot.providers import model_catalog_cache as cache

    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    cache.write_model_catalog_cache(cache.CatalogCacheSnapshot(
        1, 1000, {CLOUD: {"provider": "openai", "model_id": "gpt-5-mini", "label": "GPT-5 mini"}}, [], {}, (), "test"))

    assert backup.model_name(CLOUD) == "GPT-5 mini"
    assert backup.model_name(MAIN) == "qwen3:8b", "a model the catalog doesn't name keeps its id"


def test_stop_and_a_request_shaped_error_keep_todays_error(settings):
    backup.save_setting(enabled=True, ref=CLOUD)
    backed: list[dict] = []

    def failing(error: BaseException, stop=None):
        def main():
            if stop is not None:
                stop.set()
            backup.note_failure(error)
            yield ("error", f"⚠️ API error: {error}")
        return main

    def answer(config):
        backed.append(config)
        yield ("done", "answered")

    import threading

    stop = threading.Event()
    stopped = list(backup.stream(failing(StatusError(503), stop), answer, _config(), "hi", stop_event=stop))
    refused = list(backup.stream(failing(StatusError(400, "bad request")), answer, _config(), "hi"))

    assert stopped == [("error", "⚠️ API error: Error code: 503")]
    assert refused == [("error", "⚠️ API error: bad request")]
    assert backed == [], "the backup is never asked"
