import pathlib
import sqlite3

import pytest

import row_bot.api_keys as api_keys
import row_bot.providers.config as provider_config
from row_bot.providers.selection import (
    canonicalize_model_selection,
    ModelSelectionError,
    model_selection_diagnostics,
    resolve_selection,
)


def _isolated_provider_config(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_config, "CONFIG_PATH", tmp_path / "providers.json")
    monkeypatch.setattr(api_keys, "get_cloud_config", lambda: {"starred_models": []})
    provider_config.save_provider_config({
        "custom_endpoints": [{
            "id": "lmstudio",
            "name": "LM Studio",
            "base_url": "http://127.0.0.1:1234/v1",
            "auth_required": False,
            "models": [{"model_id": "qwen3.6-35b-a3b", "display_name": "Qwen Local"}],
        }],
    })


def _star_lmstudio_qwen(tmp_path, monkeypatch):
    import json

    from row_bot.providers import model_visibility
    import row_bot.providers.model_catalog_cache as cache

    monkeypatch.setattr(cache, "CATALOG_CACHE_PATH", tmp_path / "model_catalog_cache.json")
    settings = tmp_path / "model_settings.json"
    settings.write_text(json.dumps({"model_visibility": {"favourites": [
        "model:custom_openai_lmstudio:qwen3.6-35b-a3b"]}}), encoding="utf-8")
    monkeypatch.setattr(model_visibility, "_settings_path", lambda: settings)


def _isolated_tasks_module(tmp_path, monkeypatch, reload_for_data_dir):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    import row_bot.threads as threads

    monkeypatch.setattr(threads, "DB_PATH", str(tmp_path / "data" / "threads.db"))
    return reload_for_data_dir(tmp_path / "data", "row_bot.tasks")[0]


def test_phase0_diagnostics_reports_full_custom_provider_ref(tmp_path, monkeypatch):
    _isolated_provider_config(tmp_path, monkeypatch)
    ref = "model:custom_openai_lmstudio:qwen3.6-35b-a3b"

    diagnostics = model_selection_diagnostics(
        ref,
        runtime_surface="workflow",
        runtime_mode="agent",
        tools_bound=True,
    )

    assert diagnostics["raw_stored_model_override"] == ref
    assert diagnostics["selection_ref"] == ref
    assert diagnostics["provider_id"] == "custom_openai_lmstudio"
    assert diagnostics["runtime_model"] == "qwen3.6-35b-a3b"
    assert diagnostics["runtime_surface"] == "workflow"
    assert diagnostics["runtime_mode"] == "agent"
    assert diagnostics["tools_bound"] is True


def test_phase0_full_workflow_override_resolves_to_custom_provider(tmp_path, monkeypatch):
    _isolated_provider_config(tmp_path, monkeypatch)

    resolved = resolve_selection("model:custom_openai_lmstudio:qwen3.6-35b-a3b")

    assert resolved is not None
    assert resolved.ref == "model:custom_openai_lmstudio:qwen3.6-35b-a3b"
    assert resolved.provider_id == "custom_openai_lmstudio"
    assert resolved.model_id == "qwen3.6-35b-a3b"


def test_phase0_bare_custom_looking_model_currently_resolves_to_ollama(tmp_path, monkeypatch):
    _isolated_provider_config(tmp_path, monkeypatch)

    resolved = resolve_selection("qwen3.6-35b-a3b")

    assert resolved is not None
    assert resolved.ref == "model:ollama:qwen3.6-35b-a3b"
    assert resolved.provider_id == "ollama"


def test_phase3_telegram_thread_reload_keeps_full_model_ref(tmp_path, monkeypatch, reload_for_data_dir):
    import row_bot.threads as threads

    (threads,) = reload_for_data_dir(tmp_path / "threads", "row_bot.threads")
    ref = "model:custom_openai_lmstudio:qwen3.6-35b-a3b"
    threads._save_thread_meta("tg_123_abc", "Telegram 123")
    threads._set_thread_model_override("tg_123_abc", ref)

    listed = threads._list_threads()
    row = next(row for row in listed if row[0] == "tg_123_abc")

    assert row[4] == ref


def test_telegram_refresh_thread_model_override_syncs_cached_config(tmp_path, monkeypatch, reload_for_data_dir):
    import row_bot.threads as threads
    from row_bot.channels.telegram import _refresh_thread_model_override

    (threads,) = reload_for_data_dir(tmp_path / "threads", "row_bot.threads")
    thread_id = "tg_123_abc"
    threads._save_thread_meta(thread_id, "Telegram 123")
    threads._set_thread_model_override(thread_id, "model:openai:gpt-4o-mini")

    refreshed = _refresh_thread_model_override({"configurable": {"thread_id": thread_id}})

    assert refreshed["configurable"]["model_override"] == "model:openai:gpt-4o-mini"

    threads._set_thread_model_override(thread_id, "")
    refreshed = _refresh_thread_model_override({
        "configurable": {
            "thread_id": thread_id,
            "model_override": "model:openai:gpt-4o-mini",
        }
    })

    assert "model_override" not in refreshed["configurable"]


def test_phase0_workflow_delivery_status_is_separate_from_routing(tmp_path, monkeypatch, reload_for_data_dir):
    import row_bot.tasks as tasks

    (tasks,) = reload_for_data_dir(tmp_path, "row_bot.tasks")
    task = {"name": "Delivery Only", "delivery_channel": "", "channels": []}

    assert tasks._deliver_to_channels(task, "done") == ("", "")


def test_phase1_full_custom_ref_stays_full_ref(tmp_path, monkeypatch):
    _isolated_provider_config(tmp_path, monkeypatch)

    canonical = canonicalize_model_selection(
        "model:custom_openai_lmstudio:qwen3.6-35b-a3b",
        "workflow",
    )

    assert canonical.ref == "model:custom_openai_lmstudio:qwen3.6-35b-a3b"
    assert canonical.provider_id == "custom_openai_lmstudio"
    assert canonical.model_id == "qwen3.6-35b-a3b"


def test_phase1_starred_model_display_canonicalizes_to_full_ref(tmp_path, monkeypatch):
    _isolated_provider_config(tmp_path, monkeypatch)
    _star_lmstudio_qwen(tmp_path, monkeypatch)

    canonical = canonicalize_model_selection("Qwen Local", "workflow")

    assert canonical.ref == "model:custom_openai_lmstudio:qwen3.6-35b-a3b"
    assert canonical.source == "star"


def test_phase1_unique_custom_bare_model_canonicalizes_to_full_ref(tmp_path, monkeypatch):
    _isolated_provider_config(tmp_path, monkeypatch)

    canonical = canonicalize_model_selection("qwen3.6-35b-a3b", "workflow")

    assert canonical.ref == "model:custom_openai_lmstudio:qwen3.6-35b-a3b"
    assert canonical.source == "custom_endpoint_model"


def test_phase1_ambiguous_bare_model_fails_clearly(tmp_path, monkeypatch):
    _isolated_provider_config(tmp_path, monkeypatch)
    cfg = provider_config.load_provider_config()
    cfg["custom_endpoints"].append({
        "id": "llamacpp",
        "name": "llama.cpp",
        "base_url": "http://127.0.0.1:8080/v1",
        "auth_required": False,
        "models": [{"model_id": "qwen3.6-35b-a3b"}],
    })
    provider_config.save_provider_config(cfg)

    with pytest.raises(ModelSelectionError, match="Ambiguous model selection"):
        canonicalize_model_selection("qwen3.6-35b-a3b", "workflow")


def test_phase1_unknown_workflow_bare_model_fails_clearly(tmp_path, monkeypatch):
    _isolated_provider_config(tmp_path, monkeypatch)

    with pytest.raises(ModelSelectionError, match="Cannot infer a provider"):
        canonicalize_model_selection("totally-unknown-local-name", "workflow")


def test_phase2_create_task_stores_full_custom_ref(tmp_path, monkeypatch, reload_for_data_dir):
    _isolated_provider_config(tmp_path, monkeypatch)
    tasks = _isolated_tasks_module(tmp_path, monkeypatch, reload_for_data_dir)

    task_id = tasks.create_task(
        "Full ref workflow",
        prompts=["say hi"],
        model_override="model:custom_openai_lmstudio:qwen3.6-35b-a3b",
    )

    assert tasks.get_task(task_id)["model_override"] == "model:custom_openai_lmstudio:qwen3.6-35b-a3b"


def test_phase2_create_task_canonicalizes_unique_custom_bare_model(tmp_path, monkeypatch, reload_for_data_dir):
    _isolated_provider_config(tmp_path, monkeypatch)
    tasks = _isolated_tasks_module(tmp_path, monkeypatch, reload_for_data_dir)

    task_id = tasks.create_task(
        "Bare custom workflow",
        prompts=["say hi"],
        model_override="qwen3.6-35b-a3b",
    )

    assert tasks.get_task(task_id)["model_override"] == "model:custom_openai_lmstudio:qwen3.6-35b-a3b"


def test_phase2_update_task_canonicalizes_model_override(tmp_path, monkeypatch, reload_for_data_dir):
    _isolated_provider_config(tmp_path, monkeypatch)
    tasks = _isolated_tasks_module(tmp_path, monkeypatch, reload_for_data_dir)
    task_id = tasks.create_task("Update workflow", prompts=["say hi"])

    tasks.update_task(task_id, model_override="qwen3.6-35b-a3b")

    assert tasks.get_task(task_id)["model_override"] == "model:custom_openai_lmstudio:qwen3.6-35b-a3b"


def test_phase2_ambiguous_bare_workflow_model_fails_on_save(tmp_path, monkeypatch, reload_for_data_dir):
    _isolated_provider_config(tmp_path, monkeypatch)
    cfg = provider_config.load_provider_config()
    cfg["custom_endpoints"].append({
        "id": "llamacpp",
        "name": "llama.cpp",
        "base_url": "http://127.0.0.1:8080/v1",
        "auth_required": False,
        "models": [{"model_id": "qwen3.6-35b-a3b"}],
    })
    provider_config.save_provider_config(cfg)
    tasks = _isolated_tasks_module(tmp_path, monkeypatch, reload_for_data_dir)

    with pytest.raises(ModelSelectionError, match="Ambiguous model selection"):
        tasks.create_task(
            "Ambiguous workflow",
            prompts=["say hi"],
            model_override="qwen3.6-35b-a3b",
        )


def test_phase4_a_workflow_turn_runs_on_its_own_model_never_the_default(tmp_path, monkeypatch, reload_for_data_dir):
    _isolated_provider_config(tmp_path, monkeypatch)
    tasks = _isolated_tasks_module(tmp_path, monkeypatch, reload_for_data_dir)
    ref = "model:custom_openai_lmstudio:qwen3.6-35b-a3b"
    task_id = tasks.create_task("No fallback workflow", prompts=["say hi"], model_override=ref)
    thread_id = tasks._prepare_task_thread(tasks.get_task(task_id))
    from row_bot.application import conversation_followups

    started = []
    monkeypatch.setattr(conversation_followups, "start",
                        lambda _service, conversation_id, followup, *, model_ref="", **_: started.append(
                            (conversation_id, followup.prompt, model_ref)) or None)

    tasks.run_task_background(task_id, thread_id, [])

    [(conversation, prompt, model)] = started
    assert (conversation, model) == (thread_id, ref)
    assert prompt.endswith("\n\n---\n\nsay hi")  # After the workflow's notes (G4).
    assert tasks.get_recent_runs(1)[0]["status"] == "failed"


def test_phase5_delivery_failure_is_separate_from_execution_success(tmp_path, monkeypatch, reload_for_data_dir):
    _isolated_provider_config(tmp_path, monkeypatch)
    tasks = _isolated_tasks_module(tmp_path, monkeypatch, reload_for_data_dir)
    task_id = tasks.create_task("Delivery failure workflow", prompts=["say hi"], channels=["slack"])
    task = tasks.get_task(task_id)
    thread_id = tasks._prepare_task_thread(task)
    run_id = tasks._record_run_start(task_id, thread_id, 1)
    sent = []

    class _FailingChannel:
        name = "slack"
        display_name = "Slack"

        def get_default_target(self):
            return "chat"

        def send_message(self, target, text):
            sent.append(text)
            raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(tasks, "get_task_channels", lambda _task: [_FailingChannel()])
    from row_bot import workflow_turns

    # The run's last turn ended with its reply; the run then delivers it.
    workflow_turns._finish(tasks.read_task_run(run_id), task, "completed", "execution ok", "")
    run = tasks.get_recent_runs(1)[0]

    assert run["status"] == "completed_delivery_failed"
    assert len(sent) == 1 and "execution ok" in sent[0]
    assert "Slack: telegram unavailable" in run["status_message"]


def test_phase6_shared_model_command_is_provider_aware_and_read_only(tmp_path, monkeypatch):
    _isolated_provider_config(tmp_path, monkeypatch)
    _star_lmstudio_qwen(tmp_path, monkeypatch)
    import row_bot.channels.commands as commands

    response = commands.cmd_model("Slack", "Qwen Local")

    assert "model:custom_openai_lmstudio:qwen3.6-35b-a3b" in response
    assert "cannot persist per-conversation model overrides" in response


def test_phase6_shared_model_command_ambiguous_bare_fails_clearly(tmp_path, monkeypatch):
    _isolated_provider_config(tmp_path, monkeypatch)
    cfg = provider_config.load_provider_config()
    cfg["custom_endpoints"].append({
        "id": "llamacpp",
        "name": "llama.cpp",
        "base_url": "http://127.0.0.1:8080/v1",
        "auth_required": False,
        "models": [{"model_id": "qwen3.6-35b-a3b"}],
    })
    provider_config.save_provider_config(cfg)
    import row_bot.channels.commands as commands

    response = commands.cmd_model("Discord", "qwen3.6-35b-a3b")

    assert "Ambiguous model selection" in response
    assert "model:custom_openai_lmstudio:qwen3.6-35b-a3b" in response
    assert "model:custom_openai_llamacpp:qwen3.6-35b-a3b" in response


def test_phase9_legacy_model_diagnostics_are_read_only(tmp_path, monkeypatch, reload_for_data_dir):
    _isolated_provider_config(tmp_path, monkeypatch)
    tasks = _isolated_tasks_module(tmp_path, monkeypatch, reload_for_data_dir)
    task_id = tasks.create_task("Legacy task", prompts=["hi"])
    db_path = pathlib.Path(tmp_path / "data" / "tasks.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET model_override = ? WHERE id = ?",
            ("qwen3.6-35b-a3b", task_id),
        )
        conn.commit()

    import row_bot.threads as threads

    (threads,) = reload_for_data_dir(None, "row_bot.threads")
    threads._save_thread_meta("tg_legacy", "Telegram legacy")
    threads._set_thread_model_override("tg_legacy", "qwen3.6-35b-a3b")

    diagnostics = tasks.diagnose_legacy_model_overrides()

    assert {
        (item["scope"], item["raw_value"], item["status"])
        for item in diagnostics
    } >= {
        ("task", "qwen3.6-35b-a3b", "canonicalizable"),
        ("thread", "qwen3.6-35b-a3b", "canonicalizable"),
    }
    assert tasks.get_task(task_id)["model_override"] == "qwen3.6-35b-a3b"
    assert threads._get_thread_model_override("tg_legacy") == "qwen3.6-35b-a3b"
