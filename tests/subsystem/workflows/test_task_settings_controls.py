from dataclasses import asdict, replace
import json
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from row_bot.application import task_settings_controls as control
from tests.test_agent_profiles import _fresh_agent_modules

pytestmark = pytest.mark.subsystem


@pytest.fixture
def owner(tmp_path, monkeypatch):
    profiles, tasks, threads = _fresh_agent_modules(tmp_path, monkeypatch)
    monkeypatch.setattr(control, "tasks", tasks)
    monkeypatch.setattr(tasks, "_canonicalize_workflow_model_override", lambda value: value)
    monkeypatch.setattr(tasks, "run_task_background", lambda *a, **k: pytest.fail("unexpected run"))
    monkeypatch.setattr(tasks, "_get_scheduler", lambda: pytest.fail("unexpected scheduler"))
    monkeypatch.setattr(threads, "_save_thread_meta", lambda *a, **k: pytest.fail("unexpected conversation creation"))
    task_id = tasks.create_task(name="Synthetic settings", prompts=["Saved prompt"], enabled=False, channels=[])
    return tasks, profiles, task_id


def review(owner, **changes):
    snapshot = control.get_task_settings(owner[2])
    return control.review_task_settings(owner[2], replace(snapshot.fields, **changes))


def save(snapshot, **kwargs):
    return control.update_saved_task_settings(snapshot.task_id, snapshot.fields,
        expected_revision=snapshot.revision, expected_profile_revision=snapshot.profile_revision,
        validate=kwargs.pop("validate", lambda: None), **kwargs)


def test_load_and_review_do_not_mutate_or_allocate_secret_or_conversation(owner, monkeypatch):
    tasks, _, task_id = owner
    before = tasks.read_task_for_edit(task_id)
    monkeypatch.setattr(tasks, "generate_webhook_secret", lambda: pytest.fail("secret generated during review"))
    snapshot = review(owner, trigger_type="webhook", persistent_enabled=True)
    assert snapshot.fields.trigger_type == "webhook" and snapshot.fields.persistent_enabled
    assert not snapshot.webhook_configured and snapshot.conversation_id is None
    assert tasks.read_task_for_edit(task_id) == before
    assert tasks.get_recent_runs() == []


def test_explicit_settings_save_preserves_graph_schedule_delivery_and_run_state(owner):
    tasks, _, task_id = owner
    tasks.update_task(task_id, schedule="daily:08:00", channels=[],
        steps=[{"id": "p", "type": "prompt", "prompt": "Hidden metadata", "future": {"v": 1}}])
    before = tasks.get_task(task_id)
    result = save(review(owner, concurrency_group="synthetic-gpu", approval_mode="approve",
                        persistent_enabled=True, model_override="model:synthetic:agent"))
    after = tasks.get_task(task_id)
    assert result.fields.concurrency_group == "synthetic-gpu"
    assert result.conversation_id.startswith("pt_")
    assert after["steps"] == before["steps"] and after["schedule"] == "daily:08:00"
    assert after["channels"] == [] and not after["enabled"]
    assert tasks.get_recent_runs() == []


def test_turning_persistence_off_retains_existing_conversation_and_history(owner, monkeypatch):
    tasks, _, task_id = owner
    monkeypatch.setattr(tasks, "_delete_thread", lambda *a: pytest.fail("delete user content"), raising=False)
    tasks.update_task(task_id, persistent_thread_id="existing-thread")
    result = save(review(owner, persistent_enabled=False))
    assert result.conversation_id is None
    assert tasks.get_task(task_id)["persistent_thread_id"] is None


def test_webhook_secret_generated_only_on_activation_and_never_in_dto(owner):
    tasks, _, task_id = owner
    result = save(review(owner, trigger_type="webhook"))
    secret = tasks.get_task(task_id)["trigger"]["secret"]
    assert len(secret) >= 24 and result.webhook_configured
    assert secret not in json.dumps(asdict(result))
    assert "secret" not in asdict(result)
    result = save(review(owner, concurrency_group="other"))
    assert tasks.get_task(task_id)["trigger"]["secret"] == secret
    assert secret not in json.dumps(asdict(result))


def test_existing_unprotected_legacy_webhook_is_not_silently_rekeyed(owner):
    tasks, _, task_id = owner
    tasks.update_task(task_id, trigger={"type": "webhook", "secret": "", "future": 3})
    result = save(review(owner, concurrency_group="other"))
    assert not result.webhook_configured
    assert tasks.get_task(task_id)["trigger"] == {"type": "webhook", "secret": "", "future": 3}


def test_explicit_rotation_changes_secret_once_and_rejects_old_revision(owner):
    tasks, _, task_id = owner
    snapshot = save(review(owner, trigger_type="webhook"))
    first = tasks.get_task(task_id)["trigger"]["secret"]
    result = control.rotate_task_webhook(task_id, expected_revision=snapshot.revision, validate=lambda: None)
    rotated = tasks.get_task(task_id)["trigger"]["secret"]
    assert rotated != first and result.webhook_configured
    with pytest.raises(control.TaskSettingsError, match="task_revision_conflict"):
        control.rotate_task_webhook(task_id, expected_revision=snapshot.revision, validate=lambda: None)
    assert tasks.get_task(task_id)["trigger"]["secret"] == rotated


def test_rotation_receipt_failure_rolls_back_secret(owner):
    tasks, _, task_id = owner
    snapshot = save(review(owner, trigger_type="webhook"))
    before = tasks.read_task_for_edit(task_id)
    def fail(conn, identity):
        assert conn.in_transaction and identity == task_id
        raise RuntimeError("synthetic receipt failure")
    with pytest.raises(RuntimeError):
        control.rotate_task_webhook(task_id, expected_revision=snapshot.revision,
            validate=lambda: None, record_commit=fail)
    assert tasks.read_task_for_edit(task_id) == before


def test_explicit_webhook_download_contains_encoded_secret_without_sending(owner):
    tasks, _, task_id = owner
    snapshot = save(review(owner, trigger_type="webhook"))
    secret = tasks.get_task(task_id)["trigger"]["secret"]
    payload = control.export_webhook_configuration(task_id, expected_revision=snapshot.revision, validate=lambda: None)
    assert isinstance(payload, bytes)
    config = json.loads(payload)
    url = urlsplit(config["relative_url"])
    assert config["method"] == "POST" and url.path == f"/api/webhook/{task_id}"
    assert parse_qs(url.query) == {"secret": [secret]}
    assert tasks.get_recent_runs() == []


@pytest.mark.parametrize("reject_at", [1, 2])
def test_download_checks_authority_before_read_and_before_return(owner, reject_at):
    snapshot = save(review(owner, trigger_type="webhook"))
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls == reject_at:
            raise PermissionError("revoked")
    with pytest.raises(PermissionError):
        control.export_webhook_configuration(owner[2], expected_revision=snapshot.revision, validate=validate)


def test_download_rejects_stale_revision_and_non_webhook(owner):
    snapshot = control.get_task_settings(owner[2])
    with pytest.raises(control.TaskSettingsError, match="task_webhook_unavailable"):
        control.export_webhook_configuration(owner[2], expected_revision=snapshot.revision, validate=lambda: None)
    save(review(owner, concurrency_group="updated"))
    with pytest.raises(control.TaskSettingsError, match="task_revision_conflict"):
        control.export_webhook_configuration(owner[2], expected_revision=snapshot.revision, validate=lambda: None)


def test_current_profile_policy_is_captured_and_concurrent_profile_change_rejected(owner):
    tasks, profiles, task_id = owner
    custom = profiles.duplicate_agent_profile("worker", {"display_name": "Synthetic profile"})
    snapshot = review(owner, agent_profile_id=custom["id"], approval_mode="allow_all")
    assert snapshot.profile_available and snapshot.profile_revision
    profiles.save_agent_profile({**custom, "approval_policy_json": {"mode": "block"}})
    with pytest.raises(control.TaskSettingsError, match="task_settings_profile_conflict"):
        save(snapshot)
    assert tasks.get_task(task_id)["agent_profile_id"] != custom["id"]


def test_missing_profile_loads_for_recovery_but_cannot_be_saved_without_valid_replacement(owner):
    tasks, _, task_id = owner
    conn = tasks._get_conn()
    conn.execute("UPDATE tasks SET agent_profile_id='missing-profile' WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    snapshot = control.get_task_settings(task_id)
    assert not snapshot.profile_available and snapshot.profile_revision is None
    with pytest.raises(control.TaskSettingsError, match="task_settings_profile_unavailable"):
        control.review_task_settings(task_id, snapshot.fields)
    recovered = save(review(owner, agent_profile_id="builtin:worker"))
    assert recovered.profile_available and recovered.fields.agent_profile_id == "builtin:worker"


def test_profile_cap_is_reported_without_silently_lowering_the_saved_parent_policy(owner):
    _, profiles, _ = owner
    custom = profiles.duplicate_agent_profile("worker", {"display_name": "Restricted synthetic profile"})
    profiles.save_agent_profile({**custom, "approval_policy_json": {"mode": "block"}})
    snapshot = review(owner, agent_profile_id=custom["id"], approval_mode="allow_all")
    assert snapshot.fields.approval_mode == "allow_all" and snapshot.effective_approval_mode == "block"
    assert save(snapshot).effective_approval_mode == "block"


def test_model_is_checked_again_after_admission_without_live_readiness_probe(owner, monkeypatch):
    tasks, _, task_id = owner
    calls = []
    def cached(value):
        calls.append(value)
        if len(calls) == 3:
            raise ValueError("synthetic inactive choice")
        return value
    monkeypatch.setattr(tasks, "_canonicalize_workflow_model_override", cached)
    snapshot = review(owner, model_override="model:fake:agent")
    with pytest.raises(control.TaskSettingsError, match="task_settings_model_unavailable"):
        save(snapshot)
    assert calls == ["model:fake:agent"] * 3
    assert tasks.get_task(task_id)["model_override"] is None


def test_unchanged_legacy_model_is_not_rewritten_or_revalidated(owner, monkeypatch):
    tasks, _, task_id = owner
    tasks.update_task(task_id, model_override="legacy-model")
    monkeypatch.setattr(tasks, "_canonicalize_workflow_model_override", lambda *a: pytest.fail("legacy rewritten"))
    result = save(review(owner, concurrency_group="other"))
    assert result.fields.model_override == "legacy-model"


@pytest.mark.parametrize("change", [
    {"approval_mode": "auto"}, {"trigger_type": "unknown"}, {"trigger_type": "task_complete"},
    {"trigger_task_id": "unexpected"}, {"persistent_enabled": 1}, {"concurrency_group": " x "},
    {"agent_profile_id": "../outside"}, {"model_override": "x" * 1025},
])
def test_invalid_fields_never_save(owner, change):
    before = owner[0].read_task_for_edit(owner[2])
    with pytest.raises(control.TaskSettingsError):
        review(owner, **change)
    assert owner[0].read_task_for_edit(owner[2]) == before


def test_completion_trigger_saved_without_execution_and_missing_or_cycles_rejected(owner):
    tasks, _, task_id = owner
    child = tasks.create_task(name="Synthetic source", prompts=["Source"], enabled=False)
    result = save(review(owner, trigger_type="task_complete", trigger_task_id=child))
    assert result.fields.trigger_task_id == child
    assert tasks.get_recent_runs() == []
    tasks.update_task(child, trigger={"type": "task_complete", "target_task": task_id})
    with pytest.raises(control.TaskSettingsError, match="task_trigger_cycle"):
        save(review(owner, concurrency_group="changed"))
    with pytest.raises(control.TaskSettingsError, match="task_trigger_target_unavailable"):
        save(review(owner, trigger_type="task_complete", trigger_task_id="missing"))


@pytest.mark.parametrize("reject_at", [1, 2])
def test_revocation_rolls_back_settings_after_admission_or_before_commit(owner, reject_at):
    snapshot = review(owner, concurrency_group="changed")
    before = owner[0].read_task_for_edit(owner[2])
    calls = 0
    def validate():
        nonlocal calls
        calls += 1
        if calls == reject_at:
            raise PermissionError("revoked")
    with pytest.raises(PermissionError):
        save(snapshot, validate=validate)
    assert owner[0].read_task_for_edit(owner[2]) == before


def test_concurrent_settings_double_submit_admits_one_revision(owner):
    snapshot = review(owner, concurrency_group="changed")
    barrier = threading.Barrier(2)
    results = []
    def submit():
        barrier.wait(timeout=5)
        try:
            results.append(save(snapshot))
        except control.TaskSettingsError as exc:
            results.append(exc.code)
    workers = [threading.Thread(target=submit) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert sum(isinstance(result, control.TaskSettingsSnapshot) for result in results) == 1
    assert results.count("task_revision_conflict") == 1


def test_postcommit_read_failure_is_truthful_and_old_revision_cannot_repeat_rotation(owner, monkeypatch):
    tasks, _, task_id = owner
    snapshot = save(review(owner, trigger_type="webhook"))
    original = control.get_task_settings
    monkeypatch.setattr(control, "get_task_settings", lambda *a: (_ for _ in ()).throw(RuntimeError("lost response")))
    with pytest.raises(control.TaskSettingsError) as caught:
        control.rotate_task_webhook(task_id, expected_revision=snapshot.revision, validate=lambda: None)
    assert caught.value.committed
    after = tasks.read_task_for_edit(task_id)
    monkeypatch.setattr(control, "get_task_settings", original)
    with pytest.raises(control.TaskSettingsError, match="task_revision_conflict"):
        control.rotate_task_webhook(task_id, expected_revision=snapshot.revision, validate=lambda: None)
    assert tasks.read_task_for_edit(task_id) == after


def test_profile_alias_review_returns_canonical_id(owner):
    snapshot = review(owner, agent_profile_id="review")
    assert snapshot.fields.agent_profile_id == "builtin:review"
    assert save(snapshot).fields.agent_profile_id == "builtin:review"


def test_read_and_review_use_no_schema_or_data_writes(owner, monkeypatch):
    tasks, _, task_id = owner
    fields = control.get_task_settings(task_id).fields
    original = tasks._get_conn
    statements = []
    def connection():
        conn = original()
        conn.execute("PRAGMA query_only=ON")
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(tasks, "_get_conn", connection)
    assert control.get_task_settings(task_id).task_id == task_id
    assert control.review_task_settings(task_id, fields).profile_available
    assert all(not sql.lstrip().upper().startswith(("CREATE", "UPDATE", "INSERT", "DELETE", "ALTER"))
               for sql in statements)


def test_actual_cached_model_resolver_never_probes_provider_or_reads_credentials(owner, monkeypatch):
    tasks, _, _ = owner
    from row_bot.providers import selection, runtime
    import row_bot.secret_store as secret_store

    # Use the actual catalog resolution logic with deterministic saved metadata.
    monkeypatch.setattr(selection, "_strict_catalog_model_candidates", lambda **kw: [])
    monkeypatch.setattr(runtime, "provider_status", lambda *a, **k: pytest.fail("live provider status"))
    monkeypatch.setattr(secret_store, "get_secret", lambda *a, **k: pytest.fail("credential read"))
    def canonical(value):
        return selection.resolve_catalog_model_selection(value, surface="workflow", allow_default=True,
                                                         require_agent_ready=True).ref or None
    monkeypatch.setattr(tasks, "_canonicalize_workflow_model_override", canonical)
    result = save(review(owner, model_override="model:openai:synthetic-agent"))
    assert result.fields.model_override == "model:openai:synthetic-agent"
