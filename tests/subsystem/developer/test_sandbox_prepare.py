"""Prepare sandbox (F18): the Docker sandbox gets the project's test tools, with the network on only for that.

Docker is a fake here: it records each call and keeps the containers and images it "has". The
install runs in its own short-lived container with the network; the sandbox's own container and
its network setting are never touched. Whatever happens (success, failure, Stop, an error), the
install container is removed afterwards, so the network is off again, and the episode is kept.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.subsystem]

IMAGE = "nikolaik/python-nodejs:python3.11-nodejs20"
_FRESH_MODULES = (
    "row_bot.developer.executables",
    "row_bot.developer.storage",
    "row_bot.developer.tool_context",
    "row_bot.developer.change_ledger",
    "row_bot.developer.edits",
    "row_bot.developer.sandbox_runtime",
    "row_bot.tools.developer_tool",
)


# ── What the project needs ──────────────────────────────────────────────────


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "project"
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    root.mkdir(exist_ok=True)
    return root


def test_a_packaged_python_project_installs_itself_with_its_test_extra_and_pytest(tmp_path):
    from row_bot.developer.sandbox_prepare import plan_for

    root = _project(tmp_path, {"pyproject.toml": """
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[project]
name = "tide"
dependencies = ["httpx>=0.27"]
[project.optional-dependencies]
test = ["pytest-cov"]
""", "tests/test_tide.py": "def test_ok():\n    assert True\n"})
    plan = plan_for(root)
    assert plan.commands == ("python -m pip install --user -e '.[test]' pytest",)
    assert plan.sources == ("pyproject.toml",)
    assert plan.summary == "pytest and the project with its test extra"


def test_a_test_extra_that_has_pytest_isnt_given_it_twice(tmp_path):
    from row_bot.developer.sandbox_prepare import plan_for

    root = _project(tmp_path, {"pyproject.toml": """
[build-system]
requires = ["setuptools"]
[project]
name = "tide"
[project.optional-dependencies]
dev = ["PyTest>=8", "ruff"]
"""})
    assert plan_for(root).commands == ("python -m pip install --user -e '.[dev]'",)


def test_dependency_groups_and_requirement_files_are_read_and_quoted(tmp_path):
    from row_bot.developer.sandbox_prepare import plan_for

    root = _project(tmp_path, {
        "pyproject.toml": """
[dependency-groups]
lint = ["ruff"]
test = ["hypothesis; python_version >= '3.10'", {include-group = "lint"}, "x; rm -rf /"]
""",
        "requirements.txt": "flask==3.0\n",
        "requirements-dev.txt": "# tools\npytest==8.3\n-r requirements.txt\n",
    })
    plan = plan_for(root)
    (command,) = plan.commands
    assert command == ("python -m pip install --user 'hypothesis; python_version >= '\"'\"'3.10'\"'\"'' ruff "
                       "'x; rm -rf /' -r requirements-dev.txt -r requirements.txt")
    assert plan.sources == ("pyproject.toml", "requirements-dev.txt", "requirements.txt")
    assert "its test dependency group" in plan.summary


def test_a_folder_with_only_tests_gets_pytest(tmp_path):
    from row_bot.developer.sandbox_prepare import plan_for

    root = _project(tmp_path, {"tests/test_one.py": "def test_one():\n    pass\n"})
    plan = plan_for(root)
    assert plan.commands == ("python -m pip install --user pytest",) and plan.summary == "pytest"


@pytest.mark.parametrize("lock, command", [
    ("package-lock.json", "npm ci"),
    ("yarn.lock", "yarn install --frozen-lockfile --non-interactive"),
    ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
    (None, "npm install --no-package-lock"),
])
def test_node_projects_install_by_their_lockfile(tmp_path, lock, command):
    from row_bot.developer.sandbox_prepare import plan_for

    files = {"package.json": json.dumps({"devDependencies": {"vitest": "^2"}, "scripts": {"test": "vitest"}})}
    if lock:
        files[lock] = "{}"
    plan = plan_for(_project(tmp_path, files))
    assert plan.commands == (command,) and plan.node and not plan.python


def test_a_node_project_with_a_tests_folder_gets_no_pytest(tmp_path):
    from row_bot.developer.sandbox_prepare import plan_for

    root = _project(tmp_path, {"package.json": json.dumps({"devDependencies": {"jest": "^29"}}),
                               "package-lock.json": "{}", "tests/app.test.js": "test('x', () => {});\n"})
    plan = plan_for(root)
    assert plan.commands == ("npm ci",) and not plan.python


def test_nothing_to_install_is_none(tmp_path):
    from row_bot.developer.sandbox_prepare import plan_for

    assert plan_for(_project(tmp_path, {"README.md": "hi\n", "package.json": "{}"})) is None


@pytest.mark.parametrize("output, words", [
    ("/usr/local/bin/python: No module named pytest", "pytest isn't installed in the sandbox"),
    ("sh: 1: pytest: not found", "pytest isn't installed in the sandbox"),
    ("E   ModuleNotFoundError: No module named 'httpx'",
     "the project's packages aren't installed in the sandbox (no module named 'httpx')"),
    ("sh: 1: vitest: not found", "the project's packages aren't installed in the sandbox (vitest isn't there)"),
    ("Error: Cannot find module 'react'", "the project's packages aren't installed in the sandbox (no module 'react')"),
    ("1 failed, 3 passed", ""),
])
def test_a_test_run_that_lacked_its_tools_is_recognised(output, words):
    from row_bot.developer.sandbox_prepare import missing_tools

    assert missing_tools(output) == words


# ── Running it, with a fake Docker ──────────────────────────────────────────


class FakeDocker:
    """Docker as Prepare sees it: containers and images it has, and every call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.containers: set[str] = {"row-bot-dev-main"}
        self.images = {IMAGE}
        self.available = True
        self.exit_code = 0
        self.output = "Successfully installed pytest-8.3.3\n"
        self.rm_works = True
        self.hold = threading.Event()
        self.hold.set()
        self.started = threading.Event()
        self.runs: list[list[str]] = []
        self.start_error: Exception | None = None

    def install(self, monkeypatch, module) -> None:
        monkeypatch.setattr(module, "_runtime", lambda: SimpleNamespace(
            available=self.available, binary="docker" if self.available else "", message="not running"))
        monkeypatch.setattr(module, "_docker", self.docker)
        monkeypatch.setattr(module, "_start_process", self.start)
        monkeypatch.setattr(module, "_process_done", lambda record, timeout: record.done.wait(timeout))
        monkeypatch.setattr(module, "_stop_process", self.stop)
        monkeypatch.setattr(module, "_tail", lambda record: record.output)

    def docker(self, binary, *args, timeout=30):
        self.calls.append(list(args))
        if args[:2] == ("image", "inspect"):
            return SimpleNamespace(returncode=0 if args[2] in self.images else 1, stdout="", stderr="")
        if args[0] == "inspect":
            return SimpleNamespace(returncode=0 if args[-1] in self.containers else 1, stdout="", stderr="")
        if args[0] == "rm" and self.rm_works:
            self.containers.discard(args[-1])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def start(self, job, argv, folder, name):
        if self.start_error is not None:
            raise self.start_error
        record = SimpleNamespace(process_id="proc-0000abcd", exit_code=None, output=self.output,
                                 done=threading.Event())
        self.calls.append(argv[1:])
        if argv[1] == "pull":
            self.images.add(argv[2])
            record.exit_code = 0
            record.done.set()
            return record
        self.runs.append(argv)
        self.containers.add(argv[argv.index("--name") + 1])  # A running container, until it is removed.
        self.started.set()

        def finish():
            if self.hold.wait(10) and not record.done.is_set():
                record.exit_code = self.exit_code
                record.done.set()

        threading.Thread(target=finish, daemon=True).start()
        return record

    def stop(self, record, by):
        # Ending the docker command leaves its container running: only rm -f ends it.
        record.exit_code = 137
        record.done.set()

    def network_changes(self) -> list[list[str]]:
        return [call for call in self.calls if call[:1] == ["network"] or (call[:1] == ["run"] and "none" in call)]


@pytest.fixture
def sandbox(tmp_path, monkeypatch, reload_for_data_dir):
    """A Docker-mode code folder (network off) with tests and a pyproject, its agent context set."""
    _executables, storage, tool_context, *_rest, sandbox_runtime, developer_tool = reload_for_data_dir(
        tmp_path / "data", *_FRESH_MODULES)
    from row_bot.developer import sandbox_prepare

    root = _project(tmp_path, {"pyproject.toml": "[build-system]\nrequires = ['setuptools']\n[project]\nname = 'tide'\n",
                               "tests/test_tide.py": "def test_ok():\n    assert True\n"})
    workspace = storage.add_or_update_local_workspace(str(root))
    storage.set_workspace_execution_settings(workspace.id, execution_mode="docker", sandbox_network="off",
                                             sandbox_image=IMAGE)
    thread_id = storage.ensure_workspace_thread(workspace.id)
    tokens = tool_context.set_context(workspace_id=workspace.id, thread_id=thread_id)
    docker = FakeDocker()
    docker.install(monkeypatch, sandbox_prepare)
    approvals: list[dict] = []
    answer = {"approve": True}
    monkeypatch.setattr(developer_tool, "interrupt", lambda request: approvals.append(request) or answer["approve"])

    def mode(value: str) -> None:
        monkeypatch.setattr(developer_tool, "_active_approval_mode", lambda: value)

    yield SimpleNamespace(storage=storage, workspace=storage.get_workspace(workspace.id), thread_id=thread_id,
                          docker=docker, module=sandbox_prepare, tool=developer_tool, approvals=approvals,
                          answer=answer, mode=mode, sandbox_runtime=sandbox_runtime)
    tool_context.reset_context(tokens)


def _network_off_again(env, job) -> None:
    """The install container is gone, the episode is recorded, and the sandbox's own network never changed."""
    assert job.container not in env.docker.containers
    assert job.network_on_at is not None and job.network_off_at is not None
    assert job.network_on_at <= job.network_off_at and job.network_off_confirmed
    assert env.storage.get_workspace(env.workspace.id).sandbox_network == "off"
    assert env.docker.network_changes() == []
    assert "row-bot-dev-main" in env.docker.containers
    view = env.module.view(job)
    assert view["network"] == "off" and view["network_off_at"]


def test_a_prepared_sandbox_installs_with_the_network_on_only_for_the_install(sandbox):
    env = sandbox
    assert env.storage.get_workspace(env.workspace.id).sandbox_network == "off"
    job = env.module.start(env.workspace, env.thread_id, by="you")
    assert env.module.wait(job, 5)
    assert job.status == "ready" and job.problem == ""
    (run,) = env.docker.runs
    # Its own short-lived container, on the sandbox's copy, with the packages where the sandbox sees them.
    assert run[:3] == ["docker", "run", "--rm"]
    assert "--network" not in run
    assert run[run.index("-v") + 1].endswith(":/workspace")
    assert "PYTHONUSERBASE=/workspace/.row-bot-sandbox/python" in run
    assert run[-4:-1] == [IMAGE, "/bin/sh", "-lc"]
    assert run[-1] == "set -e\npython -m pip install --user -e . pytest"
    _network_off_again(env, job)
    saved = json.loads((env.module.jobs_root() / f"{job.job_id}.json").read_text(encoding="utf-8"))
    assert saved["network_on_at"] and saved["network_off_at"] and saved["status"] == "ready"


def test_a_failed_install_still_turns_the_network_off(sandbox):
    env = sandbox
    env.docker.exit_code = 1
    env.docker.output = "ERROR: No matching distribution found for tide-private\n"
    job = env.module.start(env.workspace, env.thread_id)
    assert env.module.wait(job, 5)
    assert job.status == "failed" and job.problem == "install_failed"
    assert "No matching distribution found for tide-private" in job.detail
    _network_off_again(env, job)


def test_stop_ends_the_install_and_turns_the_network_off(sandbox):
    env = sandbox
    env.docker.hold.clear()
    job = env.module.start(env.workspace, env.thread_id)
    assert env.docker.started.wait(5)
    assert env.module.view(job)["network"] == "on"
    env.module.stop(job.job_id, by="you")
    assert env.module.wait(job, 5)
    assert job.status == "stopped" and job.stopped_by == "you"
    _network_off_again(env, job)
    env.docker.hold.set()


def test_an_error_starting_the_install_still_turns_the_network_off(sandbox):
    env = sandbox
    env.docker.start_error = RuntimeError("Too many processes are running in Row-Bot right now.")
    job = env.module.start(env.workspace, env.thread_id)
    assert env.module.wait(job, 5)
    assert job.status == "failed"
    assert job.network_off_confirmed and env.storage.get_workspace(env.workspace.id).sandbox_network == "off"


def test_a_container_that_wont_go_is_reported_and_removed_on_the_next_start(sandbox):
    env = sandbox
    env.docker.rm_works = False
    job = env.module.start(env.workspace, env.thread_id)
    assert env.module.wait(job, 5)
    assert job.status == "failed" and job.problem == "not_finished" and not job.network_off_confirmed
    assert "couldn't confirm" in env.module.summary(job)
    env.docker.rm_works = True
    assert env.module.sweep_leftovers(wait=True) == 1
    again = env.module.get(job.job_id)
    assert again.network_off_confirmed and again.container not in env.docker.containers


def test_a_record_naming_another_container_is_never_acted_on(sandbox):
    env = sandbox
    env.docker.hold.clear()
    job = env.module.start(env.workspace, env.thread_id)
    assert env.docker.started.wait(5)
    saved_path = env.module.jobs_root() / f"{job.job_id}.json"
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    saved["container"] = "row-bot-dev-main"  # Tampered: the sandbox's own container.
    saved_path.write_text(json.dumps(saved), encoding="utf-8", newline="\n")
    env.module._RECORDS.clear()
    env.module.sweep_leftovers(wait=True)
    assert "row-bot-dev-main" in env.docker.containers
    assert not any(call[:1] == ["rm"] and call[-1] == "row-bot-dev-main" for call in env.docker.calls)
    env.module.stop(job.job_id)
    env.docker.hold.set()
    env.module.wait(job, 5)


def test_a_job_a_crash_cut_short_is_marked_and_its_container_removed(sandbox):
    env = sandbox
    env.docker.hold.clear()
    job = env.module.start(env.workspace, env.thread_id)
    assert env.docker.started.wait(5)
    # Row-Bot "crashes": the record on disk still says installing, network on.
    saved = json.loads((env.module.jobs_root() / f"{job.job_id}.json").read_text(encoding="utf-8"))
    assert saved["status"] == "installing" and not saved["network_off_confirmed"]
    env.module._RECORDS.clear()
    assert env.module.sweep_leftovers(wait=True) >= 1
    again = env.module.get(job.job_id)
    assert again.status == "failed" and again.problem == "not_finished" and again.network_off_confirmed
    assert job.container not in env.docker.containers
    env.module.stop(job.job_id)
    env.docker.hold.set()
    env.module.wait(job, 5)


def test_a_missing_image_is_downloaded_first(sandbox):
    env = sandbox
    env.docker.images.clear()
    job = env.module.start(env.workspace, env.thread_id)
    assert env.module.wait(job, 5)
    assert ["pull", IMAGE] in env.docker.calls
    assert job.status == "ready"


def test_the_image_download_alone_is_a_job_too(sandbox):
    env = sandbox
    env.docker.images.clear()
    job = env.module.start(env.workspace, env.thread_id, kind="pull")
    assert env.module.wait(job, 5)
    assert job.status == "ready" and job.network_on_at is None and env.docker.runs == []
    assert IMAGE in env.docker.images


def test_one_prepare_at_a_time_per_code_folder(sandbox):
    env = sandbox
    env.docker.hold.clear()
    first = env.module.start(env.workspace, env.thread_id)
    assert env.docker.started.wait(5)
    assert env.module.start(env.workspace, env.thread_id).job_id == first.job_id
    env.docker.hold.set()
    assert env.module.wait(first, 5)


def test_prepare_needs_docker_mode_docker_and_something_to_install(sandbox, tmp_path):
    env = sandbox
    env.docker.available = False
    with pytest.raises(env.module.PrepareError) as refused:
        env.module.start(env.workspace, env.thread_id)
    assert refused.value.code == "sandbox_runtime_unavailable"
    local = env.storage.set_workspace_execution_settings(env.workspace.id, execution_mode="local")
    with pytest.raises(env.module.PrepareError) as refused:
        env.module.start(local, env.thread_id)
    assert refused.value.code == "sandbox_docker_required"
    assert env.docker.runs == []


# ── The agent's Prepare goes through the approval gate ──────────────────────


def test_ask_asks_first_and_a_no_installs_nothing(sandbox):
    env = sandbox
    env.mode("approve")
    env.answer["approve"] = False
    result = env.tool._prepare_sandbox()
    (asked,) = env.approvals
    assert asked["question"] == "Prepare the sandbox for tests?"
    assert "Turns the sandbox network on for the install only" in asked["description"]
    assert asked["args"]["installs"] == "python -m pip install --user -e . pytest"
    assert "nothing was installed" in result and env.docker.runs == []


def test_ask_approved_prepares_and_says_the_network_is_off_again(sandbox):
    env = sandbox
    env.mode("approve")
    result = env.tool._prepare_sandbox()
    assert len(env.approvals) == 1
    assert result.startswith("Sandbox job prep-") and "prepared: installed pytest" in result
    assert "is off again" in result
    assert len(env.docker.runs) == 1


def test_block_refuses_without_asking(sandbox):
    env = sandbox
    env.mode("block")
    assert "Block" in env.tool._prepare_sandbox()
    assert env.approvals == [] and env.docker.runs == []


def test_hands_off_prepares_without_asking(sandbox):
    env = sandbox
    env.mode("full")
    assert "prepared" in env.tool._prepare_sandbox()
    assert env.approvals == [] and len(env.docker.runs) == 1


@pytest.mark.parametrize(("decision", "asked"), [("approve", 0), ("ask", 1)])
def test_auto_shows_prepare_to_the_reviewer_first(sandbox, monkeypatch, decision, asked):
    from row_bot.approval_review import ReviewVerdict

    env = sandbox
    reviewed: list[tuple] = []
    monkeypatch.setattr("row_bot.approval_review.review", lambda payload, *, risks=(), **_kw: reviewed.append(
        (payload["tool"], tuple(risks))) or ReviewVerdict(decision, "You asked to run the tests."))
    env.mode("auto")
    assert "prepared" in env.tool._prepare_sandbox()
    assert len(env.approvals) == asked
    [(tool, risks)] = reviewed
    assert tool == "developer_prepare_sandbox"
    assert any("network on" in risk for risk in risks)


def test_a_test_run_that_lacked_pytest_offers_prepare(sandbox, monkeypatch):
    env = sandbox
    from row_bot.developer.runtime import CommandResult
    from row_bot.developer.sandbox import ApprovalDecision

    result = CommandResult(command="python -m pytest", cwd="/workspace", returncode=1, stdout="",
                           stderr="/usr/local/bin/python: No module named pytest",
                           decision=ApprovalDecision("allow", ""), execution_mode="docker")
    text = env.tool._command_text(result, tool_name="developer_run_detected_test")
    assert "Sandbox not ready for tests: pytest isn't installed in the sandbox." in text
    assert "developer_prepare_sandbox" in text
    local = CommandResult(command="python -m pytest", cwd=".", returncode=1, stdout="",
                          stderr="No module named pytest", decision=ApprovalDecision("allow", ""))
    assert "Sandbox not ready" not in env.tool._command_text(local, tool_name="developer_run_detected_test")


def test_the_chat_shows_the_offer_and_the_job_card():
    from row_bot.application.conversation_traces import specialize_tool_result

    offer = specialize_tool_result({"name": "developer_run_detected_test", "content": (
        "$ python -m pytest\nexit code 1 · docker\nSandbox not ready for tests: pytest isn't installed in the "
        "sandbox. Offer to prepare it: …")})
    assert offer.kind == "sandbox_unprepared" and offer.error_code == "pytest_missing"
    packages = specialize_tool_result({"name": "developer_run_command", "content": (
        "$ npm test\nSandbox not ready for tests: the project's packages aren't installed in the sandbox.")})
    assert packages.error_code == "packages_missing"
    job = specialize_tool_result({"name": "developer_prepare_sandbox", "content": (
        "Approval: approved by you\nSandbox job prep-0a1b2c3d: prepared: installed pytest.")})
    assert job.kind == "sandbox_job" and job.job_id == "prep-0a1b2c3d"
    assert specialize_tool_result({"name": "developer_prepare_sandbox", "content": "Sandbox job prep-x: no"}) is None
