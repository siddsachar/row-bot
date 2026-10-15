"""Prepare sandbox (F18): the Docker sandbox gets the project's test tools, with the network on only for that.

The Docker sandbox runs with its network off by default, and its image has no pytest, so a
Python project's first test run there fails and installing is blocked. Prepare sandbox fixes
that in one step the person agrees to first:

- It reads what the project's tests need from its own files: ``pyproject.toml`` (its test extra
  or dependency group, and the project itself), ``requirements*.txt``, ``package.json`` (its
  lockfile decides the install command); pytest is added for a Python project that has none.
- It turns the network on for the install only: the install runs in a separate, short-lived
  container of the same image with the network, sharing the sandbox's copy of the code folder.
  The sandbox's own container never gets the network, and its network setting never changes.
- It turns the network off again in a ``finally``: the install container is removed whether the
  install worked, failed or was stopped, and the episode (when the network was on, when it was
  off again) is kept with the conversation. A container a crash left behind is removed on the
  next start.

Python packages go into ``/workspace/.row-bot-sandbox/python`` (``PYTHONUSERBASE``), which the
sandbox's commands see too; Node packages into the project's own ``node_modules``. Neither is
copied from or back to the real code folder (both are skipped like ``.venv``).

When Docker mode is chosen the image may not be on the computer yet: ``pull`` downloads it as a
job of its own (the computer's Docker downloads it; the sandbox's network isn't involved).

Each install or download is a job ``prep-xxxxxxxx`` of its conversation. It runs through the
background process manager, so it is contained, listed in Monitor with its output, and Stop
ends it. Stopping needs no approval.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

JOB_ID = re.compile(r"prep-[0-9a-f]{8}")
Kind = Literal["prepare", "pull"]
Status = Literal["pulling", "installing", "finishing", "ready", "failed", "stopped"]
StoppedBy = Literal["", "you", "agent", "conversation", "quit"]
ACTIVE: frozenset[str] = frozenset({"pulling", "installing", "finishing"})
KEEP = 50
# Where the sandbox's Python packages go: inside its copy of the code folder, skipped like .venv.
PACKAGES_DIR = ".row-bot-sandbox"
PYTHON_USER_BASE = f"/workspace/{PACKAGES_DIR}/python"
# The default image's download, as Docker Hub listed it (15 Oct 2026: 575 MB amd64, 558 MB arm64).
DEFAULT_IMAGE_DOWNLOAD = "about 570 MB"
_TEST_EXTRAS = ("test", "tests", "testing", "dev")
_REQUIREMENT_FILES = (
    "requirements-test.txt", "requirements_test.txt", "test-requirements.txt", "requirements-tests.txt",
    "requirements-dev.txt", "requirements_dev.txt", "dev-requirements.txt",
    "requirements/test.txt", "requirements/tests.txt", "requirements/dev.txt", "requirements.txt",
)
_MAX_REQUIREMENTS = 200
_MAX_REQUIREMENT_CHARS = 300
_INSTALL_ENV = {
    "PYTHONUSERBASE": PYTHON_USER_BASE,
    "PIP_NO_INPUT": "1",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    # Packages go to the user folder above, never the image's own Python.
    "PIP_BREAK_SYSTEM_PACKAGES": "1",
    "npm_config_update_notifier": "false",
    "npm_config_fund": "false",
    "npm_config_audit": "false",
}


class PrepareError(RuntimeError):
    """Prepare can't start; ``code`` says why (an API problem code), the message in words."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ── What the project needs ──────────────────────────────────────────────────


@dataclass(frozen=True)
class PreparePlan:
    """The install, read from the project's own files."""

    commands: tuple[str, ...]
    sources: tuple[str, ...]
    summary: str
    python: bool = False
    node: bool = False


def _read_toml(path: Path) -> dict:
    try:
        if path.stat().st_size > 1_000_000:
            return {}
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}


def _requirements(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    found = []
    for value in values[:_MAX_REQUIREMENTS]:
        text = str(value).strip() if isinstance(value, str) else ""
        if text and len(text) <= _MAX_REQUIREMENT_CHARS and "\n" not in text and "\x00" not in text:
            found.append(text)
    return found


def _group(groups: dict, name: str, seen: set[str] | None = None) -> list[str]:
    """A PEP 735 dependency group, with the groups it includes (once each)."""
    seen = seen or set()
    if name in seen or not isinstance(groups.get(name), list):
        return []
    seen.add(name)
    found: list[str] = []
    for item in groups[name][:_MAX_REQUIREMENTS]:
        if isinstance(item, dict) and isinstance(item.get("include-group"), str):
            found.extend(_group(groups, item["include-group"], seen))
        else:
            found.extend(_requirements([item]))
    return found


def _name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else ""


def _python_plan(root: Path) -> tuple[list[str], list[str], str] | None:
    """(pip arguments, files, what it installs) for a Python project, or None."""
    pyproject = root / "pyproject.toml"
    data = _read_toml(pyproject) if pyproject.is_file() else {}
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    args: list[str] = []
    sources: list[str] = []
    named: set[str] = set()
    what: list[str] = []
    if project:
        sources.append("pyproject.toml")
        extras = project.get("optional-dependencies")
        extras = extras if isinstance(extras, dict) else {}
        extra = next((name for name in _TEST_EXTRAS if name in extras), "")
        installable = isinstance(data.get("build-system"), dict) or (root / "setup.py").is_file() \
            or (root / "setup.cfg").is_file()
        if installable:
            args.extend(["-e", f".[{extra}]" if extra else "."])
            what.append(f"the project with its {extra} extra" if extra else "the project")
        else:
            requirements = _requirements(project.get("dependencies")) + _requirements(extras.get(extra))
            args.extend(requirements)
            if requirements:
                what.append("the project's dependencies" + (f" and its {extra} extra" if extra else ""))
        named.update(_name(item) for item in _requirements(extras.get(extra)) + _requirements(project.get("dependencies")))
    groups = data.get("dependency-groups") if isinstance(data.get("dependency-groups"), dict) else {}
    group = next((name for name in _TEST_EXTRAS if name in groups), "")
    if group:
        requirements = _group(groups, group)
        if requirements:
            if "pyproject.toml" not in sources:
                sources.append("pyproject.toml")
            args.extend(requirements)
            named.update(_name(item) for item in requirements)
            what.append(f"its {group} dependency group")
    files = []
    for name in _REQUIREMENT_FILES:
        path = root / name
        if path.is_file() and not path.is_symlink():
            files.append(name)
            args.extend(["-r", name])
            try:
                named.update(_name(line) for line in path.read_text(encoding="utf-8", errors="replace")
                             .splitlines()[:2000] if line.strip() and not line.lstrip().startswith(("#", "-")))
            except OSError:
                pass
    if files:
        sources.extend(files)
        what.append("the packages in " + ", ".join(files))
    if not (sources or any((root / name).is_file() for name in (
            "pytest.ini", "tox.ini", "conftest.py", "setup.cfg", "setup.py")) or _python_tests(root)):
        return None
    if "pytest" not in named:
        args.append("pytest")
        what.insert(0, "pytest")
    return args, sources, _words(what)


def _python_tests(root: Path) -> bool:
    """A tests/ or test/ folder with Python files in it (a Node project's tests/ is not a Python project)."""
    for name in ("tests", "test"):
        folder = root / name
        if folder.is_dir() and not folder.is_symlink():
            try:
                if any(item.suffix == ".py" for _index, item in zip(range(200), folder.iterdir())):
                    return True
            except OSError:
                continue
    return False


def _words(items: list[str]) -> str:
    items = list(dict.fromkeys(item for item in items if item))
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _node_plan(root: Path) -> tuple[str, list[str], str] | None:
    """(the install command, files, what it installs) for a Node project, or None."""
    package = root / "package.json"
    if not package.is_file() or package.is_symlink():
        return None
    try:
        data = json.loads(package.read_text(encoding="utf-8")) if package.stat().st_size <= 1_000_000 else {}
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not (data.get("dependencies") or data.get("devDependencies")):
        return None
    for lock, command in (("package-lock.json", "npm ci"), ("npm-shrinkwrap.json", "npm ci"),
                          ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
                          ("yarn.lock", "yarn install --frozen-lockfile --non-interactive"),
                          ("bun.lockb", "bun install --frozen-lockfile"),
                          ("bun.lock", "bun install --frozen-lockfile")):
        if (root / lock).is_file():
            return command, ["package.json", lock], f"the packages in package.json (from {lock})"
    return "npm install --no-package-lock", ["package.json"], "the packages in package.json"


def plan_for(workspace_path: str | Path) -> PreparePlan | None:
    """What Prepare would install for this code folder, or None when it finds nothing."""
    root = Path(workspace_path)
    if not root.is_dir():
        return None
    commands: list[str] = []
    sources: list[str] = []
    what: list[str] = []
    python = _python_plan(root)
    if python is not None:
        args, files, words = python
        commands.append("python -m pip install --user " + " ".join(shlex.quote(arg) for arg in args))
        sources.extend(files)
        what.append(words)
    node = _node_plan(root)
    if node is not None:
        command, files, words = node
        commands.append(command)
        sources.extend(file for file in files if file not in sources)
        what.append(words)
    if not commands:
        return None
    return PreparePlan(tuple(commands), tuple(sources), _words(what), python is not None, node is not None)


# ── Why a test run failed ───────────────────────────────────────────────────

_MISSING = (
    (re.compile(r"No module named '?pytest'?|(?:^|\W)pytest: (?:command )?not found", re.IGNORECASE | re.MULTILINE),
     "pytest isn't installed in the sandbox"),
    (re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'"),
     "the project's packages aren't installed in the sandbox (no module named '{0}')"),
    (re.compile(r"(?:^|\W)(jest|vitest|mocha|ava|tsc|eslint|playwright|ts-node|tsx): (?:command )?not found",
                re.MULTILINE),
     "the project's packages aren't installed in the sandbox ({0} isn't there)"),
    (re.compile(r"Cannot find module '([^']{1,120})'"),
     "the project's packages aren't installed in the sandbox (no module '{0}')"),
)


def missing_tools(output: str) -> str:
    """Why a sandbox test run couldn't run, when its tools or packages are missing; else ``""``."""
    text = str(output or "")[-40_000:]
    for pattern, words in _MISSING:
        match = pattern.search(text)
        if match:
            return words.format(*(match.groups() or ("",)))
    return ""


UNPREPARED_LINE = "Sandbox not ready for tests: "


# ── Jobs ────────────────────────────────────────────────────────────────────


@dataclass
class SandboxJob:
    job_id: str
    conversation_id: str
    workspace_id: str
    workspace_name: str
    kind: Kind
    image: str
    started_at: float
    by: str = "you"
    status: Status = "installing"
    commands: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    summary: str = ""
    container: str = ""
    process_id: str = ""
    ended_at: float | None = None
    network_on_at: float | None = None
    network_off_at: float | None = None
    network_off_confirmed: bool = True
    stopped_by: str = ""
    problem: str = ""
    detail: str = ""
    done: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)
    # The running step's process record, for its latest line of output (not saved).
    record: Any = None

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def elapsed(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.time()
        return max(0.0, end - self.started_at)


_SAVED = ("job_id", "conversation_id", "workspace_id", "workspace_name", "kind", "image", "started_at", "by",
          "status", "commands", "sources", "summary", "container", "process_id", "ended_at", "network_on_at",
          "network_off_at", "network_off_confirmed", "stopped_by", "problem", "detail")
_LOCK = threading.RLock()
_RECORDS: dict[str, SandboxJob] = {}
_LOADED_ROOT: Path | None = None


def jobs_root() -> Path:
    from row_bot.developer import sandbox_runtime

    return sandbox_runtime.SANDBOX_ROOT / "jobs"


def _path(job: SandboxJob) -> Path:
    return jobs_root() / f"{job.job_id}.json"


def _save(job: SandboxJob) -> None:
    try:
        folder = jobs_root()
        folder.mkdir(parents=True, exist_ok=True)
        data = {name: getattr(job, name) for name in _SAVED}
        temporary = folder / f".{job.job_id}.{uuid.uuid4().hex[:6]}.tmp"
        temporary.write_text(json.dumps(data, sort_keys=True), encoding="utf-8", newline="\n")
        temporary.replace(_path(job))
    except OSError:
        logger.warning("Couldn't save a sandbox job record", exc_info=True)


def _load(path: Path) -> SandboxJob | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not JOB_ID.fullmatch(str(data.get("job_id") or "")):
            return None
        values = {name: data[name] for name in _SAVED if name in data}
        return SandboxJob(**values)
    except (OSError, ValueError, TypeError):
        return None


def _ensure_loaded() -> None:
    global _LOADED_ROOT
    root = jobs_root()
    with _LOCK:
        if _LOADED_ROOT == root:
            return
        _RECORDS.clear()
        _LOADED_ROOT = root
        if not root.is_dir():
            return
        for path in sorted(root.glob("prep-*.json")):
            job = _load(path)
            if job is None:
                continue
            job.done.set()
            _RECORDS[job.job_id] = job


def sweep_leftovers(*, wait: bool = False) -> int:
    """At start-up: a job a crash cut short is marked, and its install container removed (network off).

    Removing needs Docker, which can be slow to answer, so it happens in the background unless
    *wait*; a container that can't be confirmed gone is tried again on the next start.
    """
    global _LOADED_ROOT
    with _LOCK:
        _LOADED_ROOT = None
        _ensure_loaded()
        cut_short = [job for job in _RECORDS.values() if job.active or not job.network_off_confirmed]
    for job in cut_short:
        if job.active:
            job.status = "failed"
            job.problem = job.problem or "not_finished"
            job.stopped_by = job.stopped_by or "quit"
            job.detail = job.detail or "Row-Bot closed before this finished."
            job.ended_at = job.ended_at or job.started_at
        _save(job)
    leftovers = [job for job in cut_short if job.container and not job.network_off_confirmed]

    def remove() -> None:
        for job in leftovers:
            if _remove_container(job):
                _set(job, network_off_at=job.network_off_at or time.time(), network_off_confirmed=True)

    if leftovers:
        if wait:
            remove()
        else:
            threading.Thread(target=remove, daemon=True, name="row-bot-sandbox-sweep").start()
    return len(cut_short)


_LISTENERS: list[Callable[[], None]] = []


def add_listener(callback: Callable[[], None]) -> Callable[[], None]:
    _LISTENERS.append(callback)
    return lambda: _LISTENERS.remove(callback) if callback in _LISTENERS else None


def _set(job: SandboxJob, **changes: Any) -> None:
    with _LOCK:
        for name, value in changes.items():
            setattr(job, name, value)
    _save(job)
    for callback in list(_LISTENERS):
        try:
            callback()
        except Exception:
            logger.debug("A sandbox job listener failed", exc_info=True)


# ── Docker, behind seams the tests replace ──────────────────────────────────


def _runtime() -> Any:
    from row_bot.developer.sandbox_runtime import detect_container_runtime

    return detect_container_runtime()


def _docker(binary: str, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout, check=False)


def _start_process(job: SandboxJob, argv: list[str], folder: Path, name: str) -> Any:
    from row_bot import background_processes

    return background_processes.start(job.conversation_id, argv, command=" ".join(argv[:2]) + " …",
                                      folder=str(folder), kind="command", name=name)


def _process_done(record: Any, timeout: float) -> bool:
    from row_bot import background_processes

    return background_processes.wait(record, timeout)


def _stop_process(record: Any, by: str) -> None:
    from row_bot import background_processes

    background_processes.stop(record.process_id, by="you" if by == "you" else "agent")


def _tail(record: Any) -> str:
    text, _dropped = record.log.tail(8192)
    return text


def image_present(binary: str, image: str) -> bool:
    try:
        return _docker(binary, "image", "inspect", image, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _container_gone(binary: str, name: str) -> bool:
    try:
        return _docker(binary, "inspect", "--type", "container", name, timeout=15).returncode != 0
    except (OSError, subprocess.SubprocessError):
        return False


# Only ever Prepare's own install containers: "row-bot-dev-<16 hex>-prep-<8 hex>".
INSTALL_CONTAINER = re.compile(r"row-bot-dev-[0-9a-f]{16}-prep-[0-9a-f]{8}")


def _remove_container(job: SandboxJob) -> bool:
    """The install container is gone (its network with it); False when that can't be confirmed."""
    if not INSTALL_CONTAINER.fullmatch(str(job.container or "")):
        # A record naming anything else is never acted on.
        return False
    probe = _runtime()
    if not getattr(probe, "binary", ""):
        return False
    for _attempt in range(3):
        try:
            _docker(probe.binary, "rm", "-f", job.container, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass
        if _container_gone(probe.binary, job.container):
            return True
    return False


# ── Running one ─────────────────────────────────────────────────────────────


def conversation_workspace(conversation_id: str) -> Any:
    """The code folder a conversation works in, or None."""
    from row_bot.conversation_resources import list_bindings
    from row_bot.developer.storage import get_workspace

    try:
        binding = next((item for item in list_bindings(conversation_id).bindings if item.kind == "workspace"), None)
    except Exception:
        return None
    return get_workspace(binding.resource_id) if binding is not None else None


def active_for(workspace_id: str) -> SandboxJob | None:
    _ensure_loaded()
    with _LOCK:
        return next((job for job in _RECORDS.values() if job.workspace_id == workspace_id and job.active), None)


def start(workspace: Any, conversation_id: str, *, kind: Kind = "prepare", by: str = "you") -> SandboxJob:
    """Start Prepare (or the image download) in the background; one at a time per code folder.

    The approval has already been given: by the gate for the agent, by the person's own
    confirmation in the app. Raises PrepareError when it can't start.
    """
    from row_bot.developer import sandbox_runtime

    conversation = str(conversation_id or "").strip()
    if not conversation:
        raise PrepareError("sandbox_unavailable", "Prepare belongs to a conversation.")
    if getattr(workspace, "execution_mode", "") != "docker":
        raise PrepareError("sandbox_docker_required",
                           "This code folder runs commands on this computer, not in the Docker sandbox.")
    running = active_for(workspace.id)
    if running is not None:
        return running
    plan = plan_for(workspace.path) if kind == "prepare" else None
    if kind == "prepare" and plan is None:
        raise PrepareError("sandbox_nothing_to_install",
                           "Row-Bot found nothing to install: no pyproject.toml, requirements*.txt or package.json.")
    probe = _runtime()
    if not getattr(probe, "available", False):
        raise PrepareError("sandbox_runtime_unavailable", "Docker isn't running on this computer. Start Docker "
                           "Desktop (or Docker), then try again.")
    job_id = f"prep-{uuid.uuid4().hex[:8]}"
    job = SandboxJob(
        job_id=job_id, conversation_id=conversation, workspace_id=workspace.id,
        workspace_name=str(workspace.name or "")[:200], kind=kind, image=str(workspace.sandbox_image)[:256],
        started_at=time.time(), by=by if by in {"you", "agent"} else "you",
        status="pulling" if kind == "pull" else "installing",
        commands=list(plan.commands) if plan else [f"docker pull {workspace.sandbox_image}"],
        sources=list(plan.sources) if plan else [],
        summary=plan.summary if plan else f"the sandbox image {workspace.sandbox_image}",
        container=f"{sandbox_runtime.sandbox_container_name(workspace.id)}-{job_id}",
    )
    with _LOCK:
        _ensure_loaded()
        _RECORDS[job_id] = job
    _save(job)
    _forget_old()
    thread = threading.Thread(target=_run, args=(job, workspace, probe), daemon=True, name=f"row-bot-{job_id}")
    thread.start()
    logger.info("Sandbox %s started", kind)
    return job


def _install_argv(binary: str, job: SandboxJob, workspace: Any, shadow: Path) -> list[str]:
    """One short-lived container of the sandbox's image, with the network, on the sandbox's copy."""
    import os

    args = [binary, "run", "--rm", "--name", job.container, "--label", f"row_bot.developer.prepare={job.job_id}",
            "--label", f"row_bot.developer.workspace_id={workspace.id}",
            "-v", f"{shadow}:/workspace", "-w", "/workspace"]
    for name, value in _INSTALL_ENV.items():
        args.extend(["-e", f"{name}={value}"])
    for name in getattr(workspace, "sandbox_env_allowlist", []) or []:
        value = os.environ.get(name)
        if value is not None:
            args.extend(["-e", f"{name}={value}"])
    script = "set -e\n" + "\n".join(job.commands)
    args.extend([job.image, "/bin/sh", "-lc", script])
    return args


def _follow(job: SandboxJob, argv: list[str], folder: Path, name: str) -> tuple[int | None, str]:
    """Run one step through the process manager until it ends; (exit code, its last output)."""
    record = _start_process(job, argv, folder, name)
    job.record = record
    _set(job, process_id=str(getattr(record, "process_id", "") or ""))
    while not _process_done(record, 0.5):
        if job.stop_event.is_set():
            _stop_process(record, job.stopped_by)
    return getattr(record, "exit_code", None), _tail(record)


def _failure_words(output: str) -> str:
    lines = [line.strip() for line in str(output or "").splitlines() if line.strip()]
    useful = [line for line in lines if re.search(r"error|not found|no matching|could not|failed|denied|refused",
                                                  line, re.IGNORECASE)]
    last = (useful or lines or ["no details"])[-1]
    return last[:300]


def _run(job: SandboxJob, workspace: Any, probe: Any) -> None:
    from row_bot.developer import sandbox_runtime

    try:
        if not image_present(probe.binary, job.image):
            _set(job, status="pulling")
            folder = jobs_root()
            folder.mkdir(parents=True, exist_ok=True)
            code, output = _follow(job, [probe.binary, "pull", job.image], folder, "Sandbox image download")
            if job.stop_event.is_set():
                raise _Stopped()
            if code != 0:
                _set(job, status="failed", problem="pull_failed",
                     detail=f"The image didn't download: {_failure_words(output)}", ended_at=time.time())
                return
        if job.kind == "pull":
            _set(job, status="ready", ended_at=time.time())
            return
        _container, shadow = sandbox_runtime._ensure_shadow_workspace(workspace)
        (shadow / PACKAGES_DIR).mkdir(parents=True, exist_ok=True)
        if job.stop_event.is_set():
            raise _Stopped()
        _set(job, status="installing", network_on_at=time.time(), network_off_confirmed=False)
        code, output = _follow(job, _install_argv(probe.binary, job, workspace, shadow), shadow, "Prepare sandbox")
        if job.stop_event.is_set():
            raise _Stopped()
        if code != 0:
            _set(job, problem="install_failed", detail=f"The install failed: {_failure_words(output)}")
            return
    except _Stopped:
        _set(job, stopped_by=job.stopped_by or "you")
    except Exception as exc:
        logger.warning("Sandbox %s failed", job.kind, exc_info=True)
        _set(job, problem=job.problem or ("pull_failed" if job.kind == "pull" else "install_failed"),
             detail=job.detail or f"Something went wrong: {str(exc)[:200]}")
    finally:
        # The network goes off again, whatever happened: the install container is removed.
        if job.network_on_at is not None:
            _set(job, status="finishing")
            off = _remove_container(job)
            _set(job, network_off_at=time.time(), network_off_confirmed=off)
            if not off and not job.problem:
                _set(job, problem="not_finished",
                     detail="Row-Bot couldn't confirm the install container stopped. It tries again when it next "
                            "starts; you can also remove it in Docker.")
        if job.status in ACTIVE:
            status: Status = "stopped" if job.stop_event.is_set() else "failed" if job.problem else "ready"
            _set(job, status=status, ended_at=time.time())
        job.done.set()
        logger.info("Sandbox %s ended (%s)", job.kind, job.status)


class _Stopped(Exception):
    pass


def stop(job_id: str, *, by: StoppedBy = "you") -> SandboxJob | None:
    """Ask a job to stop; it ends shortly after (``wait``), with the network off. No approval."""
    job = get(job_id)
    if job is None:
        return None
    with _LOCK:
        if not job.active:
            return job
        job.stopped_by = job.stopped_by or by
    job.stop_event.set()
    return job


def wait(job: SandboxJob, timeout: float | None) -> bool:
    return job.done.wait(timeout)


def stop_conversation(conversation_id: str, *, timeout: float = 15.0) -> int:
    jobs = [job for job in list_jobs(conversation_id) if job.active]
    for job in jobs:
        stop(job.job_id, by="conversation")
    deadline = time.monotonic() + timeout
    for job in jobs:
        wait(job, max(0.0, deadline - time.monotonic()))
    return len(jobs)


def stop_all(*, timeout: float = 10.0) -> int:
    with _LOCK:
        jobs = [job for job in _RECORDS.values() if job.active]
    for job in jobs:
        stop(job.job_id, by="quit")
    deadline = time.monotonic() + timeout
    for job in jobs:
        wait(job, max(0.0, deadline - time.monotonic()))
    return len(jobs)


def get(job_id: str) -> SandboxJob | None:
    if not JOB_ID.fullmatch(str(job_id or "")):
        return None
    _ensure_loaded()
    with _LOCK:
        return _RECORDS.get(job_id)


def list_jobs(conversation_id: str | None = None) -> list[SandboxJob]:
    """Jobs running now first, then finished ones, newest first."""
    _ensure_loaded()
    with _LOCK:
        jobs = [job for job in _RECORDS.values() if conversation_id is None or job.conversation_id == conversation_id]
    return sorted(jobs, key=lambda job: (not job.active, -job.started_at))


def latest_for(workspace_id: str) -> SandboxJob | None:
    _ensure_loaded()
    with _LOCK:
        jobs = [job for job in _RECORDS.values() if job.workspace_id == workspace_id]
    return max(jobs, key=lambda job: job.started_at) if jobs else None


def _forget_old() -> None:
    finished = [job for job in list_jobs() if not job.active]
    for job in finished[KEEP:]:
        with _LOCK:
            _RECORDS.pop(job.job_id, None)
        try:
            _path(job).unlink(missing_ok=True)
        except OSError:
            pass


def delete_conversation(conversation_id: str) -> int:
    """Forget a deleted conversation's jobs (their packages stay in the sandbox)."""
    removed = 0
    for job in list_jobs(conversation_id):
        if job.active:
            continue
        with _LOCK:
            _RECORDS.pop(job.job_id, None)
        try:
            _path(job).unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return removed


# ── What people and the agent see ───────────────────────────────────────────


def _iso(value: float | None) -> str | None:
    return None if value is None else datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def progress_line(job: SandboxJob) -> str:
    """The install's latest line of output, for the card while it runs."""
    if not job.active or job.record is None:
        return ""
    try:
        text = _tail(job.record)[-2048:]
    except Exception:
        return ""
    lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
    return lines[-1][:160] if lines else ""


def view(job: SandboxJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "conversation_id": job.conversation_id,
        "workspace_name": job.workspace_name,
        "kind": job.kind,
        "status": job.status,
        "summary": job.summary[:300],
        "sources": job.sources[:16],
        "commands": [command[:600] for command in job.commands[:4]],
        "image": job.image,
        "started_at": _iso(job.started_at),
        "ended_at": _iso(job.ended_at),
        "seconds": round(job.elapsed(), 1),
        "network": "on" if job.network_on_at is not None and job.network_off_at is None else "off",
        "network_on_at": _iso(job.network_on_at),
        "network_off_at": _iso(job.network_off_at),
        "network_off_confirmed": bool(job.network_off_confirmed),
        "process_id": job.process_id or None,
        "progress": progress_line(job),
        "by": job.by if job.by in {"you", "agent"} else "you",
        "stopped_by": job.stopped_by if job.stopped_by in {"you", "agent", "conversation", "quit"} else "",
        "problem": job.problem if job.problem in {"pull_failed", "install_failed", "not_finished"} else "",
        "detail": job.detail[:400],
    }


def summary(job: SandboxJob) -> str:
    """The tool result's first line: ``Sandbox job prep-…: …`` (the chat's card follows it by that id)."""
    head = f"Sandbox job {job.job_id}: "
    network = ("The sandbox network was on only for the install and is off again."
               if job.network_on_at is not None and job.network_off_confirmed
               else "The network was not turned on." if job.network_on_at is None
               else "Row-Bot couldn't confirm the install container stopped; tell the person.")
    if job.status == "ready" and job.kind == "pull":
        return head + f"the sandbox image {job.image} is downloaded."
    if job.status == "ready":
        return (head + f"prepared: installed {job.summary}. {network} Run the tests again now "
                "(developer_run_detected_test).")
    if job.status == "failed":
        return head + f"failed. {job.detail or 'It did not finish.'} {network}"
    if job.status == "stopped":
        who = {"you": "by the person", "agent": "by you", "conversation": "with its conversation",
               "quit": "when Row-Bot closed"}.get(job.stopped_by, "")
        return head + f"stopped{(' ' + who) if who else ''}. {network}"
    return (head + f"{'downloading the sandbox image' if job.status == 'pulling' else 'installing'}. It carries on in "
            "the background and the chat shows its progress with Stop. Call developer_prepare_sandbox again to "
            "wait for it.")


def image_status(workspace: Any) -> dict[str, Any]:
    """Docker, the image and the install for a code folder: what the sandbox controls show."""
    from row_bot.developer.state import DEFAULT_SANDBOX_IMAGE

    plan = plan_for(workspace.path)
    result: dict[str, Any] = {
        "workspace_name": str(workspace.name or "")[:200],
        "execution_mode": workspace.execution_mode,
        "network": workspace.sandbox_network,
        "image": str(workspace.sandbox_image)[:256],
        "image_download": DEFAULT_IMAGE_DOWNLOAD if workspace.sandbox_image == DEFAULT_SANDBOX_IMAGE else None,
        "runtime": "not_checked",
        "runtime_detail": "",
        "image_present": None,
        "prepare": None if plan is None else {
            "summary": plan.summary[:300], "sources": list(plan.sources[:16]),
            "commands": [command[:600] for command in plan.commands[:4]]},
        "job": None,
    }
    latest = latest_for(workspace.id)
    if latest is not None:
        result["job"] = view(latest)
    if workspace.execution_mode != "docker":
        return result
    probe = _runtime()
    if not getattr(probe, "available", False):
        result["runtime"] = "unavailable"
        result["runtime_detail"] = ("Docker isn't running on this computer. Start Docker Desktop (or Docker), "
                                    "then check again.")
        return result
    result["runtime"] = "ready"
    result["image_present"] = image_present(probe.binary, workspace.sandbox_image)
    return result
