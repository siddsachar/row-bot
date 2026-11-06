"""The scaling study's own rules (PREREG-SCALING §2, §3): ``--plan`` runs nothing, the build pin refuses another
build, and no folder the study writes may be the everyday profile, its backups, the eval profile or a checkout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.learning.scaling import study
from tests.e2e.learning.seeded import scratch

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


@pytest.fixture
def home(tmp_path, monkeypatch):
    folder = tmp_path / "home"
    (folder / ".row-bot").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: folder))
    monkeypatch.setenv(scratch.PROTECT_ENV, str(tmp_path / "backups"))
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "row-bot-data"))  # restored after the test
    return folder


def _args(tmp_path: Path, *extra: str, out: Path | None = None, profile: Path | None = None) -> list[str]:
    found = ["--out", str(out or tmp_path / "out"), "--scratch", str(tmp_path / "scratch"), "--dry-run", *extra]
    return found + (["--eval-profile", str(profile)] if profile else [])


def test_the_plan_runs_nothing_and_says_what_would_run(tmp_path, home, monkeypatch, capsys):
    def refuse(*_args, **_kwargs):
        raise AssertionError("--plan started a process")

    monkeypatch.setattr(study.subprocess, "Popen", refuse)

    assert study.main(_args(tmp_path, "--plan")) == 0

    plan = json.loads(capsys.readouterr().out)
    assert plan["people"]["mara"]["training_block"]["requests"] > 0
    assert plan["people"]["mara"]["held_out_block"]["requests"] > 0
    assert plan["subsets"]["sft-3000"]["state"] == "whole_pool"
    assert plan["chatgpt_lane_tokens"]["total"] > 0 and plan["gpu_hours"]["total"] > 0
    assert not (tmp_path / "out").exists()


def test_the_build_pin_refuses_another_build(tmp_path, home):
    args = study.parse(_args(tmp_path))
    pinned = study.Study(args, build=lambda: {"commit": "a" * 40, "code_hash": "1" * 64})
    pinned.pin()

    assert study.Study(args, build=lambda: {"commit": "a" * 40, "code_hash": "1" * 64}).pin()["commit"] == "a" * 40
    with pytest.raises(scratch.ScratchError, match="commit"):
        study.Study(args, build=lambda: {"commit": "b" * 40, "code_hash": "1" * 64}).pin()
    with pytest.raises(scratch.ScratchError, match="code_hash"):
        study.Study(args, build=lambda: {"commit": "a" * 40, "code_hash": "2" * 64}).pin()


def test_a_stage_on_another_build_runs_nothing(tmp_path, home):
    args = study.parse(_args(tmp_path))
    study.Study(args, build=lambda: {"commit": "a" * 40, "code_hash": "1" * 64}).pin()
    started: list[list[str]] = []
    later = study.Study(args, build=lambda: {"commit": "b" * 40, "code_hash": "1" * 64},
                        runner=lambda command, env, log: started.append(command) or 0)

    with pytest.raises(scratch.ScratchError):
        later.run(["pool"])
    assert started == []


@pytest.mark.parametrize("where", ["out", "scratch"])
def test_no_folder_it_writes_may_be_the_everyday_profile(tmp_path, home, where):
    inside = home / ".row-bot" / "study"
    args = study.parse(["--out", str(inside if where == "out" else tmp_path / "out"),
                        "--scratch", str(inside if where == "scratch" else tmp_path / "scratch"), "--dry-run"])

    with pytest.raises(scratch.ScratchError, match="scratch folders"):
        study.Study(args)


def test_no_folder_it_writes_may_be_the_backups_the_eval_profile_or_a_checkout(tmp_path, home):
    profile = tmp_path / "eval-profile"
    profile.mkdir()
    for out in (tmp_path / "backups" / "study", profile / "study", Path(study.ROOT) / "scaling-out"):
        with pytest.raises(scratch.ScratchError):
            study.Study(study.parse(_args(tmp_path, out=out, profile=profile)))


def test_every_folder_it_writes_is_under_its_scratch_folders(tmp_path, home):
    found = study.Study(study.parse(_args(tmp_path)))

    for folder in found.folders():
        assert folder.is_relative_to(tmp_path / "out") or folder.is_relative_to(tmp_path / "scratch")
