"""A release builds only a commit that CI has already passed (B195).

release.yml used to re-run the whole PR matrix inside a 30-minute job that the
suite had outgrown. Its gate now reuses the commit's own results: it refuses a
commit without a successful `CI / ci-ok` check, and reports whether a nightly run
already passed on that commit so the nightly suite runs at most once more.
"""
from __future__ import annotations

import json
import subprocess

import pytest

import scripts.release_gate as gate


pytestmark = [pytest.mark.subsystem, pytest.mark.installer]

SHA = "0123456789abcdef0123456789abcdef01234567"


class FakeGitHub:
    """Answers the two gh queries the gate makes, and records them."""

    def __init__(self, *, ci_ok: list[str], nightly_green: int, fail: bool = False):
        self.ci_ok = ci_ok
        self.nightly_green = nightly_green
        self.fail = fail
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        if self.fail:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="HTTP 401")
        if argv[:2] == ["gh", "api"]:
            assert argv[2].startswith(f"repos/owner/row-bot/commits/{SHA}/check-runs?check_name=ci-ok")
            runs = [{"name": "ci-ok", "status": "completed", "conclusion": value} for value in self.ci_ok]
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"check_runs": runs}), stderr="")
        assert argv[:3] == ["gh", "run", "list"] and "nightly.yml" in argv and SHA in argv
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps([{"databaseId": n} for n in range(self.nightly_green)]), stderr="")


@pytest.fixture
def repository(tmp_path):
    (tmp_path / "src/row_bot").mkdir(parents=True)
    (tmp_path / "installer").mkdir()
    (tmp_path / "src/row_bot/version.py").write_text('__version__ = "4.10.0"\n', encoding="utf-8")
    (tmp_path / "installer/row_bot_setup.iss").write_text('#define MyAppVersion "4.10.0"\n', encoding="utf-8")
    return tmp_path


def _run(repository, monkeypatch, github, version="4.10.0"):
    output = repository / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GH_REPO", "owner/row-bot")
    monkeypatch.setattr(gate.subprocess, "run", github)
    code = gate.main(["--version", version, "--sha", SHA, "--root", str(repository)])
    return code, output.read_text(encoding="utf-8") if output.exists() else ""


def test_a_commit_without_a_green_ci_run_is_refused(repository, monkeypatch, capsys) -> None:
    code, output = _run(repository, monkeypatch, FakeGitHub(ci_ok=["failure"], nightly_green=1))

    assert code == 1
    assert "no successful CI" in capsys.readouterr().out
    assert "nightly_green" not in output


def test_a_commit_ci_never_ran_on_is_refused(repository, monkeypatch) -> None:
    assert _run(repository, monkeypatch, FakeGitHub(ci_ok=[], nightly_green=0))[0] == 1


def test_an_unreadable_ci_status_is_refused(repository, monkeypatch) -> None:
    assert _run(repository, monkeypatch, FakeGitHub(ci_ok=["success"], nightly_green=1, fail=True))[0] == 1


@pytest.mark.parametrize(("nightly_runs", "expected"), [(0, "false"), (1, "true")])
def test_a_green_commit_passes_and_says_whether_a_nightly_run_passed(
    repository, monkeypatch, nightly_runs, expected,
) -> None:
    code, output = _run(repository, monkeypatch, FakeGitHub(ci_ok=["failure", "success"], nightly_green=nightly_runs))

    assert code == 0
    assert output.strip() == f"nightly_green={expected}"


def test_a_version_that_does_not_match_the_source_is_refused(repository, monkeypatch) -> None:
    github = FakeGitHub(ci_ok=["success"], nightly_green=1)

    assert _run(repository, monkeypatch, github, version="4.10.1")[0] == 1
    assert github.calls == []
