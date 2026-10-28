"""Fakes for training on Hugging Face (C1): the Hub, the sign-in and the downloader, all in memory.

No test reaches huggingface.co: :class:`FakeHub` keeps repositories, files and jobs in dictionaries and can be
told to fail (no answer, a refusal) call by call; :class:`FakeFetcher` "downloads" from it and checks the
SHA-256 like Row-Bot's real downloader; :func:`fake_oauth` is an ``httpx`` transport standing in for Hugging
Face's OAuth endpoints.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from row_bot.learning.remote.hub import Account, Flavor, HubError, RemoteJob


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class FakeJob:
    id: str
    labels: dict[str, str]
    flavor: str
    timeout: int
    env: dict[str, str]
    secrets: dict[str, str]
    command: list[str]
    image: str
    stage: str = "SCHEDULING"
    running_seconds: int = 0
    lines: list[str] = field(default_factory=list)


class FakeHub:
    """The Hub in memory. ``fail`` maps a method name to the error its next call raises (once)."""

    def __init__(self, *, user: str = "ada", can_pay: bool = True) -> None:
        self.user = user
        self.can_pay = can_pay
        self.repos: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, FakeJob] = {}
        self.fail: dict[str, HubError] = {}
        self.calls: list[str] = []
        self.deleted: list[str] = []
        self._next = 0

    def _check(self, name: str) -> None:
        self.calls.append(name)
        error = self.fail.pop(name, None)
        if error is not None:
            raise error

    def account(self) -> Account:
        self._check("account")
        return Account(self.user, True, self.can_pay)

    def flavors(self) -> list[Flavor]:
        self._check("flavors")
        return [Flavor("a100-large", "1x Nvidia A100", 80, 2.5 / 60), Flavor("l40sx1", "1x Nvidia L40S", 48, 0.03)]

    def create_private_repo(self, repo_id: str, kind: str) -> None:
        self._check("create_private_repo")
        self.repos.setdefault(repo_id, {"kind": kind, "private": True, "files": {}})

    def delete_repo(self, repo_id: str, kind: str) -> bool:
        self._check("delete_repo")
        self.deleted.append(repo_id)
        return self.repos.pop(repo_id, None) is not None

    def repo_exists(self, repo_id: str, kind: str) -> bool:
        return repo_id in self.repos

    def upload(self, repo_id: str, kind: str, files: Mapping[str, Path]) -> None:
        self._check("upload")
        self.repos[repo_id]["files"].update({name: Path(path).read_bytes() for name, path in files.items()})

    def run_job(self, *, image: str, command: list[str], env: Mapping[str, str], secrets: Mapping[str, str],
                flavor: str, timeout_seconds: int, labels: Mapping[str, str]) -> RemoteJob:
        self.calls.append("run_job")
        self._next += 1
        job = FakeJob(f"hfjob{self._next:04d}", dict(labels), flavor, timeout_seconds, dict(env), dict(secrets),
                      list(command), image)
        self.jobs[job.id] = job  # created even when the answer is lost below
        error = self.fail.pop("run_job", None)
        if error is not None:
            if error.code != "network":
                del self.jobs[job.id]  # refused: nothing was created
            raise error
        return self._remote(job)

    def _remote(self, job: FakeJob) -> RemoteJob:
        return RemoteJob(job.id, job.stage, None, job.running_seconds, dict(job.labels), job.flavor)

    def job(self, job_id: str) -> RemoteJob:
        self._check("job")
        found = self.jobs.get(job_id)
        if found is None:
            raise HubError("not_found", 404)
        return self._remote(found)

    def find_jobs(self, labels: Mapping[str, str]) -> list[RemoteJob]:
        self._check("find_jobs")
        return [self._remote(job) for job in self.jobs.values() if all(job.labels.get(k) == v for k, v in labels.items())]

    def cancel(self, job_id: str) -> None:
        self._check("cancel")
        if job_id in self.jobs:
            self.jobs[job_id].stage = "CANCELED"

    def log_lines(self, job_id: str, tail: int = 200) -> list[str]:
        self._check("log_lines")
        return list(self.jobs[job_id].lines[-tail:]) if job_id in self.jobs else []

    def read_text(self, repo_id: str, kind: str, path: str) -> str:
        self._check("read_text")
        try:
            return self.repos[repo_id]["files"][path].decode("utf-8")
        except KeyError as error:
            raise HubError("not_found", 404) from error

    def read_bytes(self, repo_id: str, kind: str, path: str) -> bytes:
        self._check("read_bytes")
        try:
            return self.repos[repo_id]["files"][path]
        except KeyError as error:
            raise HubError("not_found", 404) from error

    def download_address(self, repo_id: str, kind: str, path: str) -> str:
        self._check("download_address")
        if path not in self.repos.get(repo_id, {}).get("files", {}):
            raise HubError("not_found", 404)
        return f"https://cas-bridge.xethub.hf.co/fake/{repo_id}/{path}?signed=1"

    # ── What the job does on Hugging Face, played by the test ──────────────

    def say(self, job_id: str, kind: str, **fields: Any) -> None:
        self.jobs[job_id].lines.append("ROWBOT " + json.dumps({"type": kind, **fields}))

    def finish(self, job_id: str, *, output_repo: str, passes: int = 3, base: bool = False,
               running_seconds: int = 3_600) -> None:
        """The job completes as ``hf_job.py`` does: adapters, GGUFs, a report and the manifest uploaded."""

        files: dict[str, bytes] = {}
        losses, pass_files = [], []
        for number in range(1, passes + 1):
            weights = f"adapter weights pass {number}".encode()
            config = b'{"r": 16, "lora_alpha": 16}'
            files[f"adapters/pass-{number}/adapter_model.safetensors"] = weights
            files[f"adapters/pass-{number}/adapter_config.json"] = config
            files[f"gguf/pass-{number}.gguf"] = f"GGUF pass {number}".encode()
            losses.append(0.5 / number)
            pass_files.append([{"name": "adapter_model.safetensors", "size": len(weights), "sha256": sha256(weights)},
                               {"name": "adapter_config.json", "size": len(config), "sha256": sha256(config)}])
        if base:
            files["gguf/base.gguf"] = b"GGUF untouched starting model"
        report = {"times": {"train_seconds": 1200}, "gpu": "NVIDIA L40S", "card_peak_mib": 30_000, "card_mib": 46_068,
                  "passes": {"losses": losses, "files": pass_files}, "artifacts": None}
        manifest = {"format": "row-bot-remote-run", "version": 1,
                    "files": [{"name": name, "size": len(data), "sha256": sha256(data)} for name, data in files.items()]}
        stored = self.repos[output_repo]["files"]
        stored.update(files)
        stored["report.json"] = json.dumps(report).encode()
        stored["outputs.json"] = json.dumps(manifest).encode()
        self.say(job_id, "stage", stage="done")
        self.say(job_id, "done", times=report["times"])
        self.jobs[job_id].stage = "COMPLETED"
        self.jobs[job_id].running_seconds = running_seconds


class FakeSignIn:
    """A signed-in person (or not); the token is a plain marker string that tests look for elsewhere."""

    TOKEN = "hf_oauth_FAKE_TOKEN_never_written_anywhere"

    def __init__(self, user: str = "ada", signed_in: bool = True) -> None:
        self.user = user
        self.signed_in = signed_in
        self.signing_in = False

    def status(self) -> Any:
        from row_bot.learning.remote.auth import DeviceCode, SignInState

        if self.signing_in:
            return SignInState("signing_in", code=DeviceCode(
                "ABCD-EFGH", "https://huggingface.co/oauth/device",
                "https://huggingface.co/oauth/device?user_code=ABCD-EFGH", 5.0, 9e12))
        return SignInState("signed_in", user=self.user) if self.signed_in else SignInState("signed_out")

    def start(self) -> Any:
        self.signing_in = True
        return self.status()

    def authorize(self) -> None:
        """The person clicked Authorize on Hugging Face."""

        self.signing_in, self.signed_in = False, True

    def cancel(self) -> None:
        self.signing_in = False

    def sign_out(self) -> None:
        self.signed_in = False

    def token(self, *, valid_for: float = 0.0) -> str:
        from row_bot.learning.remote.auth import SignInError

        if not self.signed_in:
            raise SignInError("signed_out")
        return self.TOKEN


class FakeFetcher:
    """Row-Bot's downloader played against :class:`FakeHub`: the bytes come from the repository the signed
    address names, and a file is kept only when its size and SHA-256 match its pin (as ``fetch.Fetcher``)."""

    def __init__(self, hub: FakeHub, *, stop: Callable[[], bool] = lambda: False) -> None:
        self.hub = hub
        self.fetched: list[str] = []

    def fetch(self, item: Any, folder: Path, *, progress: Any = None, check: Any = None) -> Path:
        from row_bot.learning.training.fetch import FetchError

        marker = "/fake/"
        rest = item.url.split(marker, 1)[1].split("?", 1)[0]
        owner, name, path = rest.split("/", 2)
        data = self.hub.repos[f"{owner}/{name}"]["files"][path]
        if sha256(data) != item.sha256 or (item.size is not None and len(data) != item.size):
            raise FetchError("mismatch")
        target = Path(folder) / item.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.fetched.append(path)
        return target


__all__ = ["FakeFetcher", "FakeHub", "FakeJob", "FakeSignIn", "sha256"]
