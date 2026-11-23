"""The real ``row-bot run`` command, in its own process, against a scripted model endpoint on loopback.

Each run is watched (``tests/helpers/headless_guard.py``): any connection or name lookup that isn't loopback
is refused and recorded, and so is any start of the install count or the updater. The process gets a home
folder of its own whose Row-Bot profile, and the ``ROW_BOT_DATA_DIR`` it inherits, must come out untouched.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from row_bot.headless.cli import EXIT_BUDGET, EXIT_DONE, EXIT_PROVIDER, EXIT_REFUSED
from row_bot.tools.approval_gate import NO_PERSON_REFUSAL
from tests.helpers.atif_schema import problems
from tests.helpers.fake_chat_endpoint import MODEL, calls, fake_chat_endpoint, text

pytestmark = [pytest.mark.subsystem, pytest.mark.installer, pytest.mark.platform, pytest.mark.slow]

ROOT = Path(__file__).resolve().parents[3]
KEY = "sk-headless-test-0123456789"
WINDOWS = sys.platform == "win32"
SHOW_KEY = 'Write-Output "key=[$env:BENCH_KEY]"' if WINDOWS else 'echo "key=[$BENCH_KEY]"'
DELETE = "Remove-Item doomed.txt" if WINDOWS else "rm doomed.txt"


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


class Run:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir()
        (self.workspace / "doomed.txt").write_text("delete me", encoding="utf-8")
        self.home = tmp_path / "home"
        self.profile = self.home / ".row-bot"
        self.profile.mkdir(parents=True)
        (self.profile / "memory.db").write_bytes(b"the person's own memories")
        self.temp = tmp_path / "temp"
        self.temp.mkdir()
        self.out = tmp_path / "out"
        self.record = tmp_path / "guard.jsonl"
        self.before = _snapshot(self.home)

    def __call__(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {name: value for name, value in os.environ.items()
               if name not in {"ROW_BOT_TEST_MODE", "ROW_BOT_INSTALL_COUNT"} and not name.startswith("ROW_BOT_RUN_")}
        env.update(HOME=str(self.home), USERPROFILE=str(self.home), ROW_BOT_DATA_DIR=str(self.profile),
                   TMP=str(self.temp), TEMP=str(self.temp), TMPDIR=str(self.temp), BENCH_KEY=KEY,
                   PYTHONIOENCODING="utf-8")
        return subprocess.run(
            [sys.executable, "-m", "tests.helpers.headless_guard", str(self.record), "run",
             "--workspace", str(self.workspace), "--output-dir", str(self.out), *args],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600, check=False)

    def guarded(self) -> list[dict[str, str]]:
        if not self.record.is_file():
            return []
        return [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines() if line]

    def output(self, name: str) -> str:
        return (self.out / name).read_text(encoding="utf-8")


def test_a_headless_run_does_the_task_on_a_throwaway_folder_and_leaves_a_valid_trajectory(tmp_path):
    run = Run(tmp_path)
    script = [calls(("run_command", {"command": SHOW_KEY})), calls(("run_command", {"command": DELETE})),
              text("Removed doomed.txt.")]
    with fake_chat_endpoint(script) as endpoint:
        finished = run("--task", "Delete doomed.txt", "--base-url", endpoint.url, "--model", MODEL,
                       "--api-key-env", "BENCH_KEY", "--approval", "hands-off", "--max-output-tokens", "777")

    assert finished.returncode == EXIT_DONE, finished.stderr[-3000:]
    assert finished.stdout.strip() == "Removed doomed.txt."
    assert not (run.workspace / "doomed.txt").exists()
    # The key reached the endpoint, and no command the agent ran could see it.
    assert {headers.get("authorization") for headers in endpoint.headers} == {f"Bearer {KEY}"}
    assert {body.get("max_tokens") for body in endpoint.agent_requests()} == {777}
    for name in ("trajectory.json", "answer.md", "summary.json", "row-bot.log"):
        assert KEY not in run.output(name)
    trajectory = json.loads(run.output("trajectory.json"))
    assert problems(trajectory) == []
    observed = [result["content"] for step in trajectory["steps"] for result in
                (step.get("observation") or {}).get("results", [])]
    assert len(observed) == 2 and "key=[]" in observed[0]
    assert trajectory["final_metrics"]["total_prompt_tokens"] == 300
    summary = json.loads(run.output("summary.json"))
    assert (summary["status"], summary["exit_code"], summary["data_folder"]) == ("done", 0, "throwaway")
    assert summary["usage"]["calls"] == 3 and run.output("answer.md") == "Removed doomed.txt."
    # Nothing but the endpoint was reached, nothing was counted or checked for updates, and the profile it
    # inherited and the one in its home are as they were. The throwaway folder is gone.
    assert run.guarded() == []
    assert _snapshot(run.home) == run.before
    assert list(run.temp.iterdir()) == []


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize(("case", "code"), [("refused", EXIT_REFUSED), ("rounds", EXIT_BUDGET),
                                            ("unreachable", EXIT_PROVIDER)])
def test_each_way_a_headless_run_ends_has_its_exit_code(tmp_path, case, code):
    run = Run(tmp_path)
    script = {"refused": [calls(("run_command", {"command": DELETE})), text("Nobody approved it.")],
              "rounds": [calls(("run_command", {"command": SHOW_KEY}))],
              "unreachable": [text("unused")]}[case]
    with fake_chat_endpoint(script) as endpoint:
        url = f"http://127.0.0.1:{_closed_port()}/v1" if case == "unreachable" else endpoint.url
        finished = run("--task", "Delete doomed.txt", "--base-url", url, "--model", MODEL,
                       *(["--max-rounds", "1", "--approval", "hands-off"] if case == "rounds" else []))

    assert finished.returncode == code, finished.stderr[-3000:]
    summary = json.loads(run.output("summary.json"))
    assert summary["exit_code"] == code and summary["reason"]
    assert problems(json.loads(run.output("trajectory.json"))) == []
    assert (run.workspace / "doomed.txt").exists()
    if case == "refused":
        assert [item["tool"] for item in summary["refused_actions"]] == ["run_command"]
        trajectory = json.loads(run.output("trajectory.json"))
        [refusal] = [result["content"] for step in trajectory["steps"]
                     for result in (step.get("observation") or {}).get("results", [])]
        assert refusal == NO_PERSON_REFUSAL
        assert trajectory["agent"]["extra"]["harness_text"] == summary["harness_text"]
    assert run.guarded() == []
    assert _snapshot(run.home) == run.before
