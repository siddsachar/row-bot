"""Row-Bot's narrow view of the Hugging Face Hub for training (C1): the few calls a remote run needs.

Everything goes to ``huggingface.co`` in the person's own account, with their sign-in's token passed per call
(never a token found on the computer: ``HF_HUB_DISABLE_IMPLICIT_TOKEN``; never Row-Bot's environment). The
calls: who is signed in, the Jobs hardware and its prices, a private repository made and deleted, files
uploaded, a job run, read, found by its label, cancelled and its log read, and a download address for one file
of a private repository (a short-lived signed address on Hugging Face's own storage, fetched by Row-Bot's
resumable downloader, ``training/fetch.py``).

``huggingface_hub`` (Hugging Face's own library, already part of Row-Bot's full install) does the uploads and the
Jobs API; its usage reporting is off (:data:`SWITCHES`, set before it is imported, and documented in
``docs/LEARNING_ENGINE_TELEMETRY.md``). Tests replace :class:`Hub` with a fake (``tests/fixtures/hugging_face.py``):
no deterministic test reaches the network.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

ENDPOINT = "https://huggingface.co"
# Hugging Face's own library's switches (O27/E14 for C1): no usage reporting, no token found on the computer.
SWITCHES: Mapping[str, str] = {
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1",
    "HF_HUB_DISABLE_EXPERIMENTAL_WARNING": "1",
    "DO_NOT_TRACK": "1",
}
# A job's terminal stages (Hugging Face's ``JobStage``).
TERMINAL = frozenset({"COMPLETED", "CANCELED", "ERROR", "DELETED"})


class HubError(RuntimeError):
    """A Hub call that didn't work; ``code`` is stable: ``signed_out`` (the sign-in is gone or refused),
    ``no_credit`` (Jobs need credit), ``not_found``, ``rate_limited``, ``refused`` (another HTTP refusal),
    ``network`` (no answer: the outcome is unknown) or ``unavailable`` (Row-Bot's install has no Hub library)."""

    def __init__(self, code: str, status: int | None = None) -> None:
        self.code = code
        self.status = status
        super().__init__(code if status is None else f"{code} ({status})")


@dataclass(frozen=True)
class Account:
    name: str
    pro: bool = False
    can_pay: bool | None = None


@dataclass(frozen=True)
class Flavor:
    """A Jobs hardware flavour and its price (Hugging Face's own figures, per minute)."""

    name: str
    label: str
    gpu_memory_gb: int | None
    price_per_minute: float

    @property
    def price_per_hour(self) -> float:
        return round(self.price_per_minute * 60, 4)


@dataclass(frozen=True)
class RemoteJob:
    id: str
    stage: str
    message: str | None = None
    running_seconds: int | None = None
    labels: Mapping[str, str] = field(default_factory=dict)
    flavor: str | None = None

    @property
    def terminal(self) -> bool:
        return self.stage in TERMINAL


class Hub(Protocol):
    def account(self) -> Account: ...

    def flavors(self) -> list[Flavor]: ...

    def create_private_repo(self, repo_id: str, kind: str) -> None: ...

    def delete_repo(self, repo_id: str, kind: str) -> bool: ...

    def repo_exists(self, repo_id: str, kind: str) -> bool: ...

    def upload(self, repo_id: str, kind: str, files: Mapping[str, Path]) -> None: ...

    def run_job(self, *, image: str, command: list[str], env: Mapping[str, str], secrets: Mapping[str, str],
                flavor: str, timeout_seconds: int, labels: Mapping[str, str]) -> RemoteJob: ...

    def job(self, job_id: str) -> RemoteJob: ...

    def find_jobs(self, labels: Mapping[str, str]) -> list[RemoteJob]: ...

    def cancel(self, job_id: str) -> None: ...

    def log_lines(self, job_id: str, tail: int = 200) -> list[str]: ...

    def read_text(self, repo_id: str, kind: str, path: str) -> str: ...

    def read_bytes(self, repo_id: str, kind: str, path: str) -> bytes: ...

    def download_address(self, repo_id: str, kind: str, path: str) -> str: ...


def _flavor(item: Any) -> Flavor:
    accelerator = getattr(item, "accelerator", None)
    memory = None
    text = str(getattr(accelerator, "vram", "") or "") if accelerator is not None else ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        memory = int(digits)
    unit = str(getattr(item, "unit_label", "minute") or "minute")
    cost = float(getattr(item, "unit_cost_usd", 0.0) or 0.0)
    per_minute = cost / 60.0 if unit == "hour" else cost
    return Flavor(str(item.name), str(getattr(item, "pretty_name", item.name)), memory, per_minute)


def _job(info: Any) -> RemoteJob:
    status = getattr(info, "status", None)
    durations = getattr(info, "durations", None)
    return RemoteJob(
        id=str(info.id), stage=str(getattr(status, "stage", "") or "UNKNOWN"),
        message=(str(status.message)[:500] if getattr(status, "message", None) else None),
        running_seconds=getattr(durations, "running_secs", None) if durations is not None else None,
        labels=dict(getattr(info, "labels", None) or {}), flavor=str(getattr(info, "flavor", "") or "") or None,
    )


class HfHub:
    """The real Hub, through ``huggingface_hub`` with the person's token (``token``, asked per call)."""

    def __init__(self, token: Callable[[], str], *, namespace: Callable[[], str] | None = None) -> None:
        self._token = token
        self._namespace = namespace

    def _api(self) -> Any:
        for key, value in SWITCHES.items():
            os.environ.setdefault(key, value)
        try:
            from huggingface_hub import HfApi
        except ImportError as error:
            raise HubError("unavailable") from error
        return HfApi(endpoint=ENDPOINT, token=self._token(), library_name="row-bot")

    def _call(self, action: Callable[[Any], Any]) -> Any:
        api = self._api()
        try:
            return action(api)
        except HubError:
            raise
        except Exception as error:  # noqa: BLE001 - every library error becomes one stable code
            raise _classify(error) from error

    def account(self) -> Account:
        found = self._call(lambda api: api.whoami(cache=False))
        return Account(str(found.get("name") or ""), bool(found.get("isPro")),
                       found.get("canPay") if isinstance(found.get("canPay"), bool) else None)

    def flavors(self) -> list[Flavor]:
        return [_flavor(item) for item in self._call(lambda api: api.list_jobs_hardware())]

    def create_private_repo(self, repo_id: str, kind: str) -> None:
        self._call(lambda api: api.create_repo(repo_id, repo_type=kind, private=True, exist_ok=True))

    def delete_repo(self, repo_id: str, kind: str) -> bool:
        try:
            self._call(lambda api: api.delete_repo(repo_id, repo_type=kind))
            return True
        except HubError as error:
            if error.code == "not_found":
                return False
            raise

    def repo_exists(self, repo_id: str, kind: str) -> bool:
        return bool(self._call(lambda api: api.repo_exists(repo_id, repo_type=kind)))

    def upload(self, repo_id: str, kind: str, files: Mapping[str, Path]) -> None:
        from huggingface_hub import CommitOperationAdd

        operations = [CommitOperationAdd(path_in_repo=name, path_or_fileobj=str(path)) for name, path in files.items()]
        self._call(lambda api: api.create_commit(repo_id, operations=operations, repo_type=kind,
                                                 commit_message="Row-Bot training data"))

    def run_job(self, *, image: str, command: list[str], env: Mapping[str, str], secrets: Mapping[str, str],
                flavor: str, timeout_seconds: int, labels: Mapping[str, str]) -> RemoteJob:
        return _job(self._call(lambda api: api.run_job(
            image=image, command=list(command), env=dict(env), secrets=dict(secrets), flavor=flavor,
            timeout=int(timeout_seconds), labels=dict(labels), name="row-bot-personal-model",
            namespace=self._namespace() if self._namespace else None)))

    def job(self, job_id: str) -> RemoteJob:
        return _job(self._call(lambda api: api.inspect_job(
            job_id=job_id, namespace=self._namespace() if self._namespace else None)))

    def find_jobs(self, labels: Mapping[str, str]) -> list[RemoteJob]:
        return [_job(item) for item in self._call(lambda api: list(api.list_jobs(
            labels=dict(labels), namespace=self._namespace() if self._namespace else None)))]

    def cancel(self, job_id: str) -> None:
        self._call(lambda api: api.cancel_job(job_id=job_id,
                                              namespace=self._namespace() if self._namespace else None))

    def log_lines(self, job_id: str, tail: int = 200) -> list[str]:
        return [str(line) for line in self._call(lambda api: list(api.fetch_job_logs(
            job_id=job_id, follow=False, tail=tail, namespace=self._namespace() if self._namespace else None)))]

    def read_text(self, repo_id: str, kind: str, path: str) -> str:
        return self.read_bytes(repo_id, kind, path).decode("utf-8")

    def read_bytes(self, repo_id: str, kind: str, path: str) -> bytes:
        """A small file of a private repository (a manifest, an adapter's settings), whole."""

        import tempfile

        with tempfile.TemporaryDirectory(prefix="row-bot-hf-") as folder:
            found = self._call(lambda api: api.hf_hub_download(repo_id, path, repo_type=kind, local_dir=folder))
            return Path(found).read_bytes()

    def download_address(self, repo_id: str, kind: str, path: str) -> str:
        """Where one file of a private repository can be fetched now: Hugging Face answers its ``resolve`` address
        (asked with the token, to ``huggingface.co`` only) with a short-lived signed address on its own storage."""

        import httpx

        prefix = "datasets/" if kind == "dataset" else ""
        url = f"{ENDPOINT}/{prefix}{repo_id}/resolve/main/{path}"
        try:
            response = httpx.get(url, headers={"Authorization": f"Bearer {self._token()}", "User-Agent": "Row-Bot"},
                                 follow_redirects=False, timeout=30.0, trust_env=False)
        except httpx.HTTPError as error:
            raise HubError("network") from error
        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
            location = str(httpx.URL(url).join(response.headers["location"]))
            return location
        raise _status(response.status_code)


def _status(code: int) -> HubError:
    if code == 401 or code == 403:
        return HubError("signed_out", code)
    if code == 402:
        return HubError("no_credit", code)
    if code == 404:
        return HubError("not_found", code)
    if code == 429:
        return HubError("rate_limited", code)
    return HubError("refused", code)


def _classify(error: BaseException) -> HubError:
    response = getattr(error, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        found = _status(code)
        if found.code == "refused" and code >= 500:
            return HubError("network", code)  # Hugging Face itself had a problem: nothing known about the call
        text = ""
        try:
            text = str(getattr(response, "text", "") or "")[:400].lower()
        except Exception:  # noqa: BLE001
            text = ""
        if found.code == "refused" and ("credit" in text or "payment" in text or "billing" in text):
            return HubError("no_credit", code)
        return found
    name = type(error).__name__.lower()
    if any(word in name for word in ("timeout", "connect", "network", "remoteprotocol", "readerror")):
        return HubError("network")
    if isinstance(error, OSError):
        return HubError("network")
    logger.debug("Hugging Face call failed (%s)", type(error).__name__)
    return HubError("refused")


def labels_for(job_id: str) -> dict[str, str]:
    """The label a run's job carries: its Row-Bot job id, so a run whose submission had no answer can be found."""

    return {"row-bot-job": job_id}


__all__ = ["Account", "ENDPOINT", "Flavor", "HfHub", "Hub", "HubError", "RemoteJob", "SWITCHES", "TERMINAL",
           "labels_for"]

