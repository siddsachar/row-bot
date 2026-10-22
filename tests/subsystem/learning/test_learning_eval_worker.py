"""Learning's real-runtime evaluation worker (L2c2, O56): an owned process with its own throwaway
data folder, credentials handed over on its standard input and kept in memory there, refreshed
tokens written back only to the person's own store, and its whole tree ended on stop and on quit.

The process tests run a fake worker (``tests/fixtures/learning_eval_worker.py``, standard library
only) that speaks the real worker's lines; the worker's own setup is tested in this process with
fakes. A slow test runs the real worker once against a scripted local model."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from row_bot.learning import eval_worker
from row_bot.learning.evaluation import Arm, Budget, LimitReached
from row_bot.learning.packs import visible_tasks
from row_bot.learning.spending import TokenMeter
from tests.fixtures.learning import process_alive, wait_for_file

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]

FAKE = Path(__file__).resolve().parents[2] / "fixtures" / "learning_eval_worker.py"


def _python() -> str:
    base = getattr(sys, "_base_executable", "") or sys.executable
    return base if Path(base).is_file() else sys.executable


def _worker(tmp_path, person, *, fake=None, credentials=(), on_refreshed=None, arms=None) -> eval_worker.EvalWorker:
    spec = {"arms": arms or [{"name": "new", "model_ref": "model:ollama:row-bot-personal-v1:abcd"},
                             {"name": "main", "model_ref": "model:openai:gpt-5.6-sol"}],
            "tools": ["filesystem", "calculator"], "providers": {}, "fake": fake or {}}
    options = {"on_refreshed": on_refreshed} if on_refreshed is not None else {}
    return eval_worker.EvalWorker(spec, credentials=list(credentials), person_data=person,
                                  command=[_python(), "-I", str(FAKE)], scratch=tmp_path / "temp", **options)


@pytest.fixture
def person(tmp_path):
    folder = tmp_path / "person"
    folder.mkdir()
    (folder / "threads.db").write_bytes(b"the person's conversations")
    return folder


def test_the_worker_gets_its_own_throwaway_folder_and_the_spec_on_its_standard_input(tmp_path, person):
    worker = _worker(tmp_path, person)
    worker.start()
    try:
        assert worker.alive and worker.notes == ["fake worker"]
        folder = worker.folder
        assert folder is not None and folder.name.startswith(eval_worker.PREFIX)
        assert not str(folder.resolve()).startswith(str(person.resolve()))
        seen = json.loads((folder / "data" / "seen.json").read_text(encoding="utf-8"))
        assert seen["arms"] == ["new", "main"] and seen["tools"] == ["filesystem", "calculator"]
        assert sorted(path.name for path in person.iterdir()) == ["threads.db"]  # nothing of the person's touched
    finally:
        worker.stop()
    assert not folder.exists()  # the throwaway folder goes with it


def test_a_folder_inside_the_persons_data_folder_is_refused(tmp_path, person):
    worker = eval_worker.EvalWorker({"arms": []}, person_data=person, command=[_python(), "-I", str(FAKE)],
                                    scratch=person / "inside")
    with pytest.raises(eval_worker.EvalWorkerError) as refused:
        worker.start()
    assert refused.value.code == "refused"
    assert not list((person / "inside").iterdir())


def test_a_worker_that_wont_start_is_ended_and_says_why(tmp_path, person):
    worker = _worker(tmp_path, person, fake={"refuse": True})
    with pytest.raises(eval_worker.EvalWorkerError) as failed:
        worker.start()
    assert failed.value.code == "not_ready"
    assert worker.folder is not None and not worker.folder.exists()


def test_stop_ends_the_workers_whole_process_tree(tmp_path, person):
    worker = _worker(tmp_path, person, fake={"spawn_child": True})
    worker.start()
    child = int(wait_for_file(worker.folder / "work" / "child.pid"))
    assert process_alive(child)

    assert worker.stop()

    deadline = time.monotonic() + 5
    while process_alive(child) and time.monotonic() < deadline:  # Windows ends a job's processes asynchronously
        time.sleep(0.05)
    assert not process_alive(child) and not worker.alive


def test_quitting_row_bot_stops_every_evaluation_worker(tmp_path, person):
    worker = _worker(tmp_path, person, fake={"spawn_child": True})
    worker.start()
    child = int(wait_for_file(worker.folder / "work" / "child.pid"))

    assert eval_worker.stop_all() == 1

    deadline = time.monotonic() + 5
    while process_alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not process_alive(child) and not worker.alive


def test_credentials_go_on_standard_input_never_into_the_command_line_environment_or_log(tmp_path, person):
    secret = "sk-test-0123456789abcdef"
    credential = eval_worker.Credential("openai", "api_key", {"api_key": secret})
    worker = _worker(tmp_path, person, fake={"leak": True}, credentials=[credential])
    worker.start()
    try:
        process = worker._process  # noqa: SLF001 - the test looks at what was started
        assert secret not in " ".join(process.args)
        assert all(secret not in value for value in eval_worker.worker_environment(worker.folder / "data").values())
        deadline = time.monotonic() + 5
        while "debug:" not in worker.log_tail() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert "debug: using [redacted]" in worker.log_tail() and secret not in worker.log_tail()
        seen = json.loads((worker.folder / "data" / "seen.json").read_text(encoding="utf-8"))
        assert seen["credential_kinds"] == ["api_key"]
        assert secret not in "".join(path.read_text(encoding="utf-8", errors="replace")
                                     for path in worker.folder.rglob("*") if path.is_file())
    finally:
        worker.stop()


def test_a_token_the_worker_refreshes_is_handed_back_to_be_written_to_the_persons_store(tmp_path, person):
    written = []
    credential = eval_worker.Credential("codex", "codex_oauth", {"access_token": "old", "refresh_token": "r"},
                                        revision="rev-1")
    worker = _worker(tmp_path, person, fake={"refresh": True}, credentials=[credential],
                     on_refreshed=lambda item, bundle: written.append((item.provider, dict(bundle))) or "written")
    worker.start()
    try:
        task = visible_tasks()[0]
        worker.run(task.to_dict(), "main", budget={"task_tokens": 1000})
    finally:
        worker.stop()
    assert written == [("codex", {"access_token": "fresh-access-token", "refresh_token": "fresh-refresh-token"})]
    assert worker.write_backs == ["written"]


def test_the_worker_environment_is_bound_to_the_throwaway_folder_without_keys_or_row_bot_switches(monkeypatch,
                                                                                                tmp_path):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", "C:/the/person")
    monkeypatch.setenv("ROW_BOT_LEARNING_PREVIEW", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
    monkeypatch.setenv("PYTHONPATH", "somewhere")

    environment = eval_worker.worker_environment(tmp_path / "data")

    assert environment["ROW_BOT_DATA_DIR"] == str(tmp_path / "data")
    assert "ROW_BOT_LEARNING_PREVIEW" not in environment and "OPENAI_API_KEY" not in environment
    assert "PYTHONPATH" not in environment and environment["ROW_BOT_AUTO_START_OLLAMA"] == "0"


def test_only_tools_that_stay_inside_the_test_are_bound():
    tools = eval_worker.safe_tools(["shell", "browser", "duckduckgo", "weather", "memory", "calculator", "gmail",
                                    "task", "goal", "documents"])

    assert tools == ["filesystem", "memory", "calculator", "documents"]


# ── The runner evaluation uses ──────────────────────────────────────────────


class FakeWorker:
    """The worker as RuntimeRunner sees it: one result per task, what it was asked kept."""

    def __init__(self, status="passed", tokens=1000, local=False, refuse_limit=False):
        self.asked: list[tuple[str, str, dict]] = []
        self.status, self.tokens, self.local, self.refuse_limit = status, tokens, local, refuse_limit

    def run(self, task, arm, *, budget, timeout=0):
        self.asked.append((task["id"], arm, dict(budget)))
        status = "not_run" if self.refuse_limit else self.status
        return {"type": "result", "case": {"task_id": task["id"], "status": status, "check": "answer",
                                           "tokens": self.tokens, "local_tokens": self.tokens if self.local else 0,
                                           "steps": 3, "seconds": 2.0, "detail": "limit" if self.refuse_limit else ""},
                "usage": [{"provider": "openai", "model": "gpt-5.6-sol", "input": 900, "output": 100}]}


def test_the_runner_records_the_workers_calls_in_the_persons_ledger_and_charges_the_month():
    recorded = []
    worker = FakeWorker(tokens=1000)
    runner = eval_worker.RuntimeRunner(worker, {"main": eval_worker.WorkerArm("main", "model:openai:gpt-5.6-sol")},
                                       record_usage=recorded.extend)
    meter = TokenMeter(100_000)
    task = visible_tasks()[0]

    case = runner.run(task, Arm("main", "model:openai:gpt-5.6-sol"), budget=Budget(task_tokens=50_000), meter=meter,
                      arm_left=None)

    assert case.status == "passed" and case.tokens == 1000 and meter.spent == 1000
    assert recorded == [{"provider": "openai", "model": "gpt-5.6-sol", "input": 900, "output": 100}]
    assert worker.asked[0][2]["task_tokens"] == 50_000 and worker.asked[0][2]["cloud_cap"] == 100_000


def test_a_cloud_task_that_would_pass_the_monthly_limit_isnt_sent():
    worker = FakeWorker()
    runner = eval_worker.RuntimeRunner(worker, {"main": eval_worker.WorkerArm("main", "model:openai:gpt-5.6-sol")})
    task = visible_tasks()[0]

    with pytest.raises(LimitReached):
        runner.run(task, Arm("main", "model:openai:gpt-5.6-sol"), budget=Budget(), meter=TokenMeter(10), arm_left=None)
    assert worker.asked == []


def test_a_task_the_worker_stopped_at_the_limit_stops_the_comparison():
    runner = eval_worker.RuntimeRunner(FakeWorker(refuse_limit=True),
                                       {"main": eval_worker.WorkerArm("main", "model:openai:gpt-5.6-sol")})

    with pytest.raises(LimitReached):
        runner.run(visible_tasks()[0], Arm("main", "model:openai:gpt-5.6-sol"), budget=Budget(),
                   meter=TokenMeter(1_000_000), arm_left=None)


def test_a_local_arm_is_free_and_never_refused_by_the_month():
    worker = FakeWorker(local=True)
    runner = eval_worker.RuntimeRunner(worker, {"new": eval_worker.WorkerArm("new", "model:ollama:p", local=True)})
    meter = TokenMeter(0)

    case = runner.run(visible_tasks()[0], Arm("new", "model:ollama:p"), budget=Budget(), meter=meter, arm_left=None)

    assert case.status == "passed" and meter.spent == 0 and meter.local_tokens == 1000
    assert worker.asked[0][2]["cloud_cap"] is None


# ── The worker's side, in this process ──────────────────────────────────────


@pytest.fixture
def memory_keyring(monkeypatch):
    from row_bot import secret_store

    monkeypatch.setattr(secret_store, "_backend_override", None)
    backend = secret_store.use_memory_backend()
    yield backend
    secret_store._set_backend_for_tests(None)  # noqa: SLF001


def test_the_workers_secrets_live_in_memory_and_a_refresh_is_reported(memory_keyring, tmp_path, monkeypatch):
    from row_bot.providers import codex

    saved = []
    monkeypatch.setattr(codex, "save_codex_oauth_tokens", lambda token_set, **kw: saved.append(token_set) or {})
    frames: list[dict] = []
    secrets: list[str] = []

    eval_worker._seed_credentials([{"provider": "codex", "kind": "codex_oauth", "values": {  # noqa: SLF001
        "access_token": "access-1", "refresh_token": "refresh-1"}}], frames.append, secrets)
    codex.save_codex_oauth_tokens(codex.CodexTokenSet(access_token="access-2", refresh_token="refresh-2"))

    assert [item.access_token for item in saved] == ["access-1", "access-2"]
    assert frames == [{"type": "refreshed", "provider": "codex", "bundle": {
        "access_token": "access-2", "refresh_token": "refresh-2", "id_token": "", "account_id": "", "expires_at": "",
        "plan_type": ""}}]
    assert {"access-1", "refresh-1", "access-2", "refresh-2"} <= set(secrets)


def test_a_redacting_log_never_shows_a_credential():
    import logging

    formatter = eval_worker._Redacting(["sk-secret-value"])  # noqa: SLF001
    record = logging.LogRecord("row_bot.x", logging.INFO, __file__, 1, "calling with sk-secret-value", (), None)

    assert "sk-secret-value" not in formatter.format(record) and "[redacted]" in formatter.format(record)


def test_write_back_saves_only_changed_tokens_and_never_over_a_newer_sign_in(monkeypatch):
    from row_bot.providers import auth_store, codex
    from row_bot.providers.config import ProviderConfigError

    calls = []

    def save(token_set, *, expected_revision=None, **_kw):
        calls.append((token_set.access_token, expected_revision))
        if expected_revision == "stale":
            raise ProviderConfigError("revision changed")
        return {}

    monkeypatch.setattr(codex, "save_codex_oauth_tokens", save)
    monkeypatch.setattr(auth_store, "read_provider_oauth_bundle_snapshot", lambda provider: ({}, {}, "rev-2"))
    credential = eval_worker.Credential("codex", "codex_oauth", {"access_token": "a1", "refresh_token": "r1"}, "rev-1")

    assert eval_worker.write_back(credential, {"access_token": "a1", "refresh_token": "r1"}) == "unchanged"
    assert eval_worker.write_back(credential, {"access_token": "a2"}) == "written"
    assert calls == [("a2", "rev-1")] and credential.revision == "rev-2"
    stale = eval_worker.Credential("codex", "codex_oauth", {"access_token": "a1"}, "stale")
    assert eval_worker.write_back(stale, {"access_token": "a3"}) == "not_written"
    api_key = eval_worker.Credential("openai", "api_key", {"api_key": "k"})
    assert eval_worker.write_back(api_key, {"api_key": "other"}) == "unchanged"


def test_gather_reads_the_persons_credentials_once_and_never_refreshes(monkeypatch):
    from row_bot.providers import auth_store

    reads = []
    monkeypatch.setattr(auth_store, "get_provider_secret", lambda provider, name="api_key": reads.append(provider)
                        or "sk-openai")
    monkeypatch.setattr(auth_store, "read_provider_oauth_bundle_snapshot", lambda provider: (
        {"access_token": "acc", "refresh_token": "ref", "id_token": "", "account": "acct"},
        {"expires_at": "2026-10-20T00:00:00Z", "plan_type": "plus"}, "rev-9"))

    credentials, providers, unsupported = eval_worker.gather([
        eval_worker.WorkerArm("a", "model:openai:gpt-5.6-sol"), eval_worker.WorkerArm("b", "model:openai:gpt-6.1-sol"),
        eval_worker.WorkerArm("c", "model:codex:gpt-6.1-sol"), eval_worker.WorkerArm("d", "model:ollama:qwen3:8b"),
        eval_worker.WorkerArm("e", "model:claude_subscription:opus"),
    ])

    assert reads == ["openai"]  # one read per provider
    kinds = {item.provider: item.kind for item in credentials}
    assert kinds == {"openai": "api_key", "codex": "codex_oauth"}
    assert next(item for item in credentials if item.provider == "codex").revision == "rev-9"
    assert unsupported == ["e"] and providers["custom_endpoints"] == []


# ── The real worker, once (slow) ────────────────────────────────────────────


@pytest.mark.slow
def test_the_real_worker_runs_a_pack_task_as_a_real_agent_turn(tmp_path, monkeypatch, data_dir):
    """A real worker process (Row-Bot itself, the throwaway folder, the Agent runtime) against a
    scripted loopback model served like llama-server: the task's turn runs and is verified."""

    from tests.e2e.learning.scripted_model import ALIAS, ScriptedModel

    with ScriptedModel() as scripted:
        endpoint = {"id": "eval-scripted", "name": "Scripted", "base_url": scripted.base_url, "profile": "llama_cpp",
                    "execution_location": "local", "auth_required": False}
        ref = f"model:custom_openai_eval-scripted:{ALIAS}"
        spec = {"arms": [{"name": "main", "model_ref": ref}], "tools": ["calculator"],
                "providers": {"custom_endpoints": [endpoint], "local_model_settings": {}}, "code_backend": "local"}
        worker = eval_worker.EvalWorker(spec, person_data=data_dir, scratch=tmp_path / "temp")
        worker.start()
        try:
            task = next(item for item in visible_tasks() if item.pack == "document_lookups")
            frame = worker.run(task.to_dict(), "main", budget={"task_tokens": 100_000, "max_steps": None})
        finally:
            folder = worker.folder
            tail = worker.log_tail()
            worker.stop()
    case = frame["case"]
    # Verified either way (the scripted model only knows calculations).
    assert case["task_id"] == task.id and case["status"] in ("passed", "failed"), (case, worker.notes, tail[-2000:])
    assert case["tokens"] > 0 and frame["usage"]
    assert scripted.requests > 0 and not folder.exists()
