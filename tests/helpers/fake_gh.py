"""A fake GitHub CLI put first on PATH, so Row-Bot's real ``gh`` lookup and subprocess call run.

    gh = install_fake_gh(tmp_path, monkeypatch, pr_url="https://github.com/example/repo/pull/7")
    ...  # code under test runs `gh pr create ...`
    assert gh.calls() == [["pr", "create", "--draft", ...]]

``gh auth status`` succeeds (or fails with ``signed_in=False``), ``gh pr create`` prints ``pr_url``,
anything else exits 0 with no output. Every call's arguments are recorded; a ``--body-file`` path is
recorded as ``"@" + its contents``.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_SCRIPT = r'''
import json, sys
config = json.load(open(sys.argv[1], encoding="utf-8"))
args = sys.argv[2:]
if "--body-file" in args:
    # Record the body itself; the file is gone once gh returns.
    index = args.index("--body-file") + 1
    args[index] = "@" + open(args[index], encoding="utf-8").read()
with open(config["log"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
if args[:2] == ["auth", "status"]:
    if config["signed_in"]:
        print("Logged in to github.com account example (keyring)")
        sys.exit(0)
    print("You are not logged into any GitHub hosts.", file=sys.stderr)
    sys.exit(1)
if args[:2] == ["pr", "create"]:
    print(config["pr_url"])
    sys.exit(0)
if args[:1] == ["--version"]:
    print("gh version 2.99.0 (fake)")
sys.exit(0)
'''


@dataclass
class FakeGh:
    path: Path
    log: Path

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


def install_fake_gh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pr_url: str = "https://github.com/example/repo/pull/1",
    signed_in: bool = True,
) -> FakeGh:
    bin_dir = tmp_path / "fake-gh-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = bin_dir / "calls.jsonl"
    config = bin_dir / "config.json"
    config.write_text(json.dumps({"log": str(log), "pr_url": pr_url, "signed_in": signed_in}), encoding="utf-8")
    script = bin_dir / "fake_gh.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    if os.name == "nt":
        gh = bin_dir / "gh.cmd"
        gh.write_text(f'@"{sys.executable}" "{script}" "{config}" %*\r\n', encoding="utf-8")
    else:
        gh = bin_dir / "gh"
        gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "{config}" "$@"\n', encoding="utf-8")
        gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return FakeGh(path=gh, log=log)
