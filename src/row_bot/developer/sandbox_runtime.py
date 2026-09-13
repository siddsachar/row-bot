from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
import time
import threading
import uuid
from functools import wraps
from dataclasses import dataclass, field
from datetime import datetime

from row_bot.developer.executables import resolve_docker, resolve_podman
from row_bot.developer.state import DeveloperWorkspace
from row_bot.developer.storage import DEVELOPER_DIR
from row_bot.runtime_paths import is_containerized_runtime

logger = logging.getLogger(__name__)


SANDBOX_ROOT = DEVELOPER_DIR / "sandboxes"
PENDING_CHANGES_PATH = SANDBOX_ROOT / "pending_changes.json"
_PENDING_LOCK = threading.RLock()


def _pending_mutation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with _PENDING_LOCK:
            _load_client_pending()
            return function(*args, **kwargs)
    return guarded


def _load_client_pending() -> dict:
    if not PENDING_CHANGES_PATH.exists():
        return {"changes": []}
    if PENDING_CHANGES_PATH.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("sandbox_history_unavailable")
    try:
        value = json.loads(PENDING_CHANGES_PATH.read_text(encoding="utf-8"))
        if type(value) is not dict or type(value.get("changes")) is not list or any(type(item) is not dict for item in value["changes"]):
            raise ValueError
        return value
    except (ValueError, TypeError, OSError):
        raise ValueError("sandbox_history_unavailable") from None


def validate_client_pending() -> None:
    with _PENDING_LOCK:
        _load_client_pending()


def prepared_shadow_workspace(workspace: DeveloperWorkspace) -> pathlib.Path:
    from row_bot.developer.review import scoped_workspace_path
    shadow = sandbox_shadow_path(workspace.id)
    try:
        scoped_workspace_path(SANDBOX_ROOT, shadow.relative_to(SANDBOX_ROOT).as_posix())
        if not shadow.is_dir():
            raise ValueError
        return shadow
    except (OSError, ValueError):
        raise ValueError("sandbox_unprepared") from None

SESSIONS_PATH = SANDBOX_ROOT / "sessions.json"
_TEXT_LIMIT = 1_000_000
_COPY_SKIP_DIRS = {
    ".row-bot-edit-recovery",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "venv",
}
_SNAPSHOT_SKIP_DIRS = _COPY_SKIP_DIRS | {".git", ".hg", ".svn"}
OFFICIAL_CONTAINER_SANDBOX_UNAVAILABLE = (
    "Developer Docker Sandbox is not available inside the Row-Bot application "
    "container. Run Row-Bot on the host to use Docker Sandbox, or use Local mode "
    "with an explicitly mounted workspace."
)


@dataclass(frozen=True)
class SandboxProbe:
    available: bool
    binary: str = ""
    version: str = ""
    message: str = ""


@dataclass(frozen=True)
class SandboxProcessInfo:
    pid: int
    command: str
    log_path: str = ""
    started_at: str = ""

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "command": self.command,
            "log_path": self.log_path,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "SandboxProcessInfo":
        return cls(
            pid=int(raw.get("pid") or 0),
            command=str(raw.get("command", "")),
            log_path=str(raw.get("log_path", "")),
            started_at=str(raw.get("started_at", "")),
        )


@dataclass(frozen=True)
class SandboxStatus:
    available: bool
    backend: str = "docker"
    container_name: str = ""
    running: bool = False
    exists: bool = False
    image: str = ""
    network: str = "off"
    shadow_workspace: str = ""
    runtime_version: str = ""
    message: str = ""
    processes: list[SandboxProcessInfo] = field(default_factory=list)


@dataclass(frozen=True)
class SandboxCommandOutcome:
    command: str
    cwd: str
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    changed_files: list[str] = field(default_factory=list)
    pending_change_id: str = ""
    execution_mode: str = "docker"
    sandbox_backend: str = "docker"
    sandbox_workspace: str = ""
    container_name: str = ""

    @property
    def ran(self) -> bool:
        return self.returncode is not None


@dataclass(frozen=True)
class SandboxPendingChange:
    id: str
    workspace_id: str
    thread_id: str
    command: str
    patch: str
    files: list[str]
    created_at: str
    imported: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "thread_id": self.thread_id,
            "command": self.command,
            "patch": self.patch,
            "files": list(self.files),
            "created_at": self.created_at,
            "imported": self.imported,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "SandboxPendingChange":
        return cls(
            id=str(raw.get("id", "")),
            workspace_id=str(raw.get("workspace_id", "")),
            thread_id=str(raw.get("thread_id", "")),
            command=str(raw.get("command", "")),
            patch=str(raw.get("patch", "")),
            files=[str(item) for item in raw.get("files", []) if str(item or "").strip()],
            created_at=str(raw.get("created_at", "")),
            imported=bool(raw.get("imported", False)),
        )


def detect_container_runtime() -> SandboxProbe:
    if is_containerized_runtime():
        return SandboxProbe(
            available=False,
            message=OFFICIAL_CONTAINER_SANDBOX_UNAVAILABLE,
        )
    for name, resolver in (("docker", resolve_docker), ("podman", resolve_podman)):
        binary = resolver()
        if not binary:
            continue
        try:
            proc = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception as exc:
            return SandboxProbe(False, binary=binary, message=str(exc))
        if proc.returncode == 0:
            version = (proc.stdout or proc.stderr).strip()
            if name == "docker":
                try:
                    server_proc = subprocess.run(
                        [binary, "info", "--format", "{{.ServerVersion}}"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                except Exception as exc:
                    message = _friendly_docker_engine_error(str(exc))
                    return SandboxProbe(False, binary=binary, version=version, message=f"Docker CLI is installed but the engine is not accessible: {message}")
                if server_proc.returncode != 0:
                    message = _friendly_docker_engine_error(server_proc.stderr or server_proc.stdout or "Docker engine is not accessible.")
                    return SandboxProbe(False, binary=binary, version=version, message=f"Docker CLI is installed but the engine is not accessible: {message}")
                server_version = (server_proc.stdout or "").strip()
                if server_version:
                    version = f"{version} (engine {server_version})"
            return SandboxProbe(True, binary=binary, version=version)
        return SandboxProbe(False, binary=binary, message=(proc.stderr or proc.stdout or "Container runtime failed.").strip())
    return SandboxProbe(False, message="Docker or Podman was not found on PATH.")


def sandbox_shadow_path(workspace_id: str) -> pathlib.Path:
    digest = hashlib.sha1(workspace_id.encode("utf-8")).hexdigest()[:12]
    return SANDBOX_ROOT / workspace_id / digest / "workspace"


def sandbox_container_name(workspace_id: str) -> str:
    digest = hashlib.sha1(workspace_id.encode("utf-8")).hexdigest()[:16]
    return f"row-bot-dev-{digest}"


def get_docker_sandbox_status(workspace: DeveloperWorkspace) -> SandboxStatus:
    probe = detect_container_runtime()
    name = sandbox_container_name(workspace.id)
    shadow = sandbox_shadow_path(workspace.id)
    if not probe.available:
        return SandboxStatus(
            available=False,
            container_name=name,
            image=workspace.sandbox_image,
            network=workspace.sandbox_network,
            shadow_workspace=str(shadow),
            message=probe.message,
            processes=list_sandbox_processes(workspace.id),
        )
    exists, running = _container_state(probe.binary, name)
    image_present = exists or _docker_image_exists(probe.binary, workspace.sandbox_image)
    message = "Running" if running else ("Stopped" if exists else "Not created")
    if not image_present:
        message = _missing_image_message(workspace.sandbox_image)
    return SandboxStatus(
        available=image_present,
        container_name=name,
        exists=exists,
        running=running,
        image=workspace.sandbox_image,
        network=workspace.sandbox_network,
        shadow_workspace=str(shadow),
        runtime_version=probe.version,
        message=message,
        processes=list_sandbox_processes(workspace.id),
    )


def list_pending_changes(*, workspace_id: str, thread_id: str | None = None, include_imported: bool = False) -> list[SandboxPendingChange]:
    payload = _load_pending_payload()
    rows: list[SandboxPendingChange] = []
    for raw in payload.get("changes", []):
        if not isinstance(raw, dict):
            continue
        change = SandboxPendingChange.from_dict(raw)
        if change.workspace_id != workspace_id:
            continue
        if thread_id is not None and change.thread_id != thread_id:
            continue
        if change.imported and not include_imported:
            continue
        rows.append(change)
    rows.sort(key=lambda item: item.created_at, reverse=True)
    return rows


def get_pending_change(change_id: str) -> SandboxPendingChange | None:
    payload = _load_pending_payload()
    for raw in payload.get("changes", []):
        if isinstance(raw, dict) and raw.get("id") == change_id:
            return SandboxPendingChange.from_dict(raw)
    return None


@_pending_mutation
def mark_pending_change_imported(change_id: str, *, expected_revision: str | None = None,
                                command_id: str | None = None, validate=None) -> None:
    payload = _load_pending_payload()
    found = False
    for raw in payload.get("changes", []):
        if isinstance(raw, dict) and raw.get("id") == change_id:
            if validate:
                validate()
            if expected_revision is not None:
                revision = pending_change_revision(raw)
                if raw.get("imported") and raw.get("import_command_id") == command_id:
                    return
                if revision != expected_revision:
                    raise ValueError("sandbox_change_revision_conflict")
            raw["imported"] = True
            if command_id is not None:
                raw["import_command_id"] = command_id
            found = True
            break
    if expected_revision is not None and not found:
        raise ValueError("sandbox_change_unavailable")
    if validate:
        validate()
    _save_pending_payload(payload)


def pending_change_revision(raw: dict) -> str:
    """Bind the full canonical row, including retained unknown metadata."""
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=True,
        separators=(",", ":"), allow_nan=False).encode("ascii")).hexdigest()


def read_pending_import_rows(workspace_id: str, thread_id: str) -> list[dict]:
    """Validated saved rows only; never prepare a sandbox or mutate its history."""
    with _PENDING_LOCK:
        payload = _load_client_pending()
        rows, identities = [], set()
        for raw in payload["changes"]:
            identity = raw.get("id")
            if type(identity) is not str or not identity or len(identity) > 128 or identity in identities:
                raise ValueError("sandbox_history_unavailable")
            identities.add(identity)
            if raw.get("workspace_id") != workspace_id or raw.get("thread_id") != thread_id:
                continue
            if (type(raw.get("patch")) is not str or type(raw.get("files")) is not list or
                    any(type(path) is not str for path in raw["files"]) or type(raw.get("imported", False)) is not bool):
                raise ValueError("sandbox_history_unavailable")
            import copy
            rows.append(copy.deepcopy(raw))
        return rows


@_pending_mutation
def cleanup_thread_pending_changes(thread_id: str) -> dict[str, int]:
    """Drop imported records for a thread and retain every unimported change."""

    clean = str(thread_id or "").strip()
    stats = {"imported_removed": 0, "unimported_retained": 0}
    if not clean:
        return stats
    payload = _load_pending_payload()
    retained: list[dict] = []
    changed = False
    for raw in payload.get("changes", []):
        if not isinstance(raw, dict) or str(raw.get("thread_id") or "") != clean:
            retained.append(raw)
            continue
        if bool(raw.get("imported")):
            stats["imported_removed"] += 1
            changed = True
        else:
            stats["unimported_retained"] += 1
            retained.append(raw)
    if changed:
        payload["changes"] = retained
        _save_pending_payload(payload)
    return stats


@_pending_mutation
def cleanup_orphaned_imported_changes(owner_thread_ids: set[str]) -> int:
    """Remove imported pending-change rows whose conversation no longer exists."""

    owners = {str(thread_id) for thread_id in owner_thread_ids}
    payload = _load_pending_payload()
    existing = list(payload.get("changes", []))
    retained = [
        raw
        for raw in existing
        if not (
            isinstance(raw, dict)
            and bool(raw.get("imported"))
            and str(raw.get("thread_id") or "") not in owners
        )
    ]
    removed = len(existing) - len(retained)
    if removed:
        payload["changes"] = retained
        _save_pending_payload(payload)
    return removed


def run_docker_sandbox_command(
    workspace: DeveloperWorkspace,
    command: str,
    *,
    thread_id: str,
    timeout: int = 120,
) -> SandboxCommandOutcome:
    probe = detect_container_runtime()
    if not probe.available:
        return SandboxCommandOutcome(
            command=command,
            cwd=workspace.path,
            returncode=None,
            stderr=f"Docker Sandbox is not available: {probe.message}",
        )

    try:
        container_name, shadow = ensure_docker_sandbox(workspace, probe=probe)
    except Exception as exc:
        return SandboxCommandOutcome(
            command=command,
            cwd=workspace.path,
            returncode=None,
            stderr=str(exc),
        )
    before = _snapshot_text_files(shadow)
    docker_args = _docker_exec_args(probe.binary, container_name, command)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            docker_args,
            cwd=str(shadow),
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        after_timeout = _snapshot_text_files(shadow)
        pending = _record_pending_change(workspace, thread_id, command, before, after_timeout)
        return SandboxCommandOutcome(
            command=command,
            cwd="/workspace",
            returncode=None,
            stdout=(exc.stdout or "")[-20_000:] if isinstance(exc.stdout, str) else "",
            stderr=f"Sandbox command timed out after {timeout}s.",
            changed_files=pending.files if pending else [],
            pending_change_id=pending.id if pending else "",
            sandbox_workspace=str(shadow),
            container_name=container_name,
        )
    elapsed = time.perf_counter() - started
    if elapsed > 5:
        logger.info("developer sandbox command completed in %.3fs workspace=%s", elapsed, workspace.id)
    after = _snapshot_text_files(shadow)
    pending = _record_pending_change(workspace, thread_id, command, before, after)
    return SandboxCommandOutcome(
        command=command,
        cwd="/workspace",
        returncode=completed.returncode,
        stdout=(completed.stdout or "")[-20_000:],
        stderr=(completed.stderr or "")[-20_000:],
        changed_files=pending.files if pending else [],
        pending_change_id=pending.id if pending else "",
        sandbox_workspace=str(shadow),
        container_name=container_name,
    )


def write_file_in_docker_sandbox(
    workspace: DeveloperWorkspace,
    path: str,
    content: str,
    *,
    thread_id: str,
    prepared_only: bool = False,
    expected_digest: str = "",
    command_id: str = "",
    persist_recovery=None,
    recovery=None,
    validate=None,
    expected_identity: str | None = None,
    expected_metadata: str | None = None,
    expected_parent_identity: str | None = None,
) -> SandboxCommandOutcome:
    if prepared_only:
        from row_bot.developer.edits import FileEditError, publish_text_revision, read_edit_bytes
        shadow = prepared_shadow_workspace(workspace)
        validate_client_pending()
        if recovery is None:
            source, _, _, _ = read_edit_bytes(shadow, path)
            before_text = source.decode("utf-8") if source is not None else None
            # Git's configured attributes/EOL policy owns host import. A patch
            # cannot promise that a newline-only intent survives that policy.
            if before_text is not None and before_text != content and before_text.splitlines() == content.splitlines():
                raise FileEditError("sandbox_patch_unavailable")
            _client_file_patch(path, before_text, content)
        publication = publish_text_revision(shadow, path, content, expected_digest=expected_digest,
            command_id=command_id, persist_recovery=persist_recovery, recovery=recovery, validate=validate,
            expected_identity=expected_identity, expected_metadata=expected_metadata,
            expected_parent_identity=expected_parent_identity)
        if not publication.changed:
            return SandboxCommandOutcome(f"write {path}", "/workspace", 0)
        try:
            pending = _record_pending_change(workspace, thread_id, f"write {path}",
                {path: publication.before_text}, {path: content}, command_id=command_id)
            if pending is None:
                raise ValueError("sandbox_patch_unavailable")
        except Exception:
            raise FileEditError("sandbox_history_incomplete", publication.recovery, file_saved=True) from None
        return SandboxCommandOutcome(f"write {path}", "/workspace", 0, changed_files=[path],
            pending_change_id=pending.id if pending else "", sandbox_workspace=str(shadow),
            container_name=sandbox_container_name(workspace.id))
    try:
        ensure_docker_sandbox(workspace)
        container_name, shadow = _ensure_shadow_workspace(workspace)
    except Exception as exc:
        return SandboxCommandOutcome(
            command=f"write {path}",
            cwd=workspace.path,
            returncode=None,
            stderr=str(exc),
        )
    before = _snapshot_text_files(shadow)
    try:
        target = _validate_shadow_relative_path(shadow, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as exc:
        return SandboxCommandOutcome(
            command=f"write {path}",
            cwd="/workspace",
            returncode=None,
            stderr=str(exc),
            sandbox_workspace=str(shadow),
            container_name=container_name,
        )
    after = _snapshot_text_files(shadow)
    pending = _record_pending_change(workspace, thread_id, f"write {path}", before, after)
    return SandboxCommandOutcome(
        command=f"write {path}",
        cwd="/workspace",
        returncode=0,
        stdout=f"Wrote {path} in Docker Sandbox shadow workspace. No container command was needed.",
        changed_files=pending.files if pending else [],
        pending_change_id=pending.id if pending else "",
        sandbox_workspace=str(shadow),
        container_name=container_name,
    )


def apply_patch_in_docker_sandbox(
    workspace: DeveloperWorkspace,
    patch: str,
    *,
    thread_id: str,
    summary: str = "",
) -> SandboxCommandOutcome:
    try:
        ensure_docker_sandbox(workspace)
        container_name, shadow = _ensure_shadow_workspace(workspace)
    except Exception as exc:
        return SandboxCommandOutcome(
            command=summary or "apply patch",
            cwd=workspace.path,
            returncode=None,
            stderr=str(exc),
        )
    before = _snapshot_text_files(shadow)
    try:
        paths = _paths_from_patch(shadow, patch)
        if not paths:
            raise ValueError("Patch did not include any workspace file paths.")
        check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn", "-"],
            cwd=str(shadow),
            input=patch,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if check.returncode != 0:
            raise ValueError((check.stderr or check.stdout or "Patch did not apply.").strip())
        applied = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=str(shadow),
            input=patch,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if applied.returncode != 0:
            raise ValueError((applied.stderr or applied.stdout or "Patch failed while applying.").strip())
    except Exception as exc:
        return SandboxCommandOutcome(
            command=summary or "apply patch",
            cwd="/workspace",
            returncode=None,
            stderr=str(exc),
            sandbox_workspace=str(shadow),
            container_name=container_name,
        )
    after = _snapshot_text_files(shadow)
    pending = _record_pending_change(workspace, thread_id, summary or "apply patch", before, after)
    return SandboxCommandOutcome(
        command=summary or "apply patch",
        cwd="/workspace",
        returncode=0,
        stdout="Applied patch in Docker Sandbox shadow workspace. No container command was needed.",
        changed_files=pending.files if pending else [],
        pending_change_id=pending.id if pending else "",
        sandbox_workspace=str(shadow),
        container_name=container_name,
    )


def ensure_docker_sandbox(
    workspace: DeveloperWorkspace,
    *,
    probe: SandboxProbe | None = None,
    rebuild: bool = False,
) -> tuple[str, pathlib.Path]:
    probe = probe or detect_container_runtime()
    if not probe.available:
        raise RuntimeError(f"Docker Sandbox is not available: {probe.message}")
    host_root = pathlib.Path(workspace.path).expanduser().resolve()
    if not host_root.is_dir():
        raise ValueError(f"Workspace folder does not exist: {workspace.path}")

    container_name = sandbox_container_name(workspace.id)
    shadow = sandbox_shadow_path(workspace.id)
    if rebuild:
        _remove_container(probe.binary, container_name)
        _remove_shadow_workspace(workspace.id)
    if not shadow.exists():
        _prepare_shadow_workspace(workspace.id, host_root)

    exists, running = _container_state(probe.binary, container_name)
    if exists and _container_network_mismatch(probe.binary, container_name, workspace):
        logger.info(
            "Developer sandbox network policy changed; recreating container %s for workspace %s",
            container_name,
            workspace.id,
        )
        _remove_container(probe.binary, container_name)
        exists = False
        running = False
    if exists and running:
        return container_name, shadow
    if exists:
        proc = subprocess.run(
            [probe.binary, "start", container_name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "Failed to start Docker Sandbox.").strip())
        return container_name, shadow

    if not _docker_image_exists(probe.binary, workspace.sandbox_image):
        raise RuntimeError(_missing_image_message(workspace.sandbox_image))

    args = _docker_create_args(probe.binary, workspace, shadow, container_name)
    proc = subprocess.run(
        args,
        cwd=str(shadow),
        shell=False,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(_friendly_docker_error(proc.stderr or proc.stdout or "Failed to create Docker Sandbox."))
    return container_name, shadow


def rebuild_docker_sandbox(workspace: DeveloperWorkspace) -> SandboxStatus:
    ensure_docker_sandbox(workspace, rebuild=True)
    return get_docker_sandbox_status(workspace)


def cleanup_workspace_sandbox(workspace_id: str) -> bool:
    probe = detect_container_runtime()
    container_name = sandbox_container_name(workspace_id)
    if probe.binary:
        _remove_container(probe.binary, container_name)
    _clear_sandbox_processes(workspace_id)
    target = SANDBOX_ROOT / workspace_id
    resolved = target.resolve()
    root = SANDBOX_ROOT.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Refusing to clean sandbox outside sandbox root: {target}") from exc
    if not resolved.exists():
        return False
    _safe_rmtree(resolved)
    return True


def start_docker_sandbox_process(
    workspace: DeveloperWorkspace,
    command: str,
    *,
    thread_id: str = "",
) -> SandboxCommandOutcome:
    del thread_id
    probe = detect_container_runtime()
    if not probe.available:
        return SandboxCommandOutcome(
            command=command,
            cwd=workspace.path,
            returncode=None,
            stderr=f"Docker Sandbox is not available: {probe.message}",
        )
    try:
        container_name, shadow = ensure_docker_sandbox(workspace, probe=probe)
    except Exception as exc:
        return SandboxCommandOutcome(
            command=command,
            cwd=workspace.path,
            returncode=None,
            stderr=str(exc),
        )
    log_name = f"row-bot-dev-{hashlib.sha1((command + str(time.time())).encode('utf-8')).hexdigest()[:10]}.log"
    log_path = f"/tmp/{log_name}"
    script = f"nohup /bin/sh -lc {shlex.quote(command)} > {shlex.quote(log_path)} 2>&1 & echo $!"
    proc = subprocess.run(
        [probe.binary, "exec", container_name, "/bin/sh", "-lc", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return SandboxCommandOutcome(
            command=command,
            cwd="/workspace",
            returncode=None,
            stderr=(proc.stderr or proc.stdout or "Failed to start sandbox process.").strip(),
            sandbox_workspace=str(shadow),
            container_name=container_name,
        )
    try:
        pid = int((proc.stdout or "").strip().splitlines()[-1])
    except Exception:
        pid = 0
    if pid:
        _record_sandbox_process(
            workspace.id,
            SandboxProcessInfo(
                pid=pid,
                command=command,
                log_path=log_path,
                started_at=datetime.now().isoformat(),
            ),
        )
    return SandboxCommandOutcome(
        command=command,
        cwd="/workspace",
        returncode=0,
        stdout=f"Started sandbox PID {pid}" if pid else "Started sandbox process",
        sandbox_workspace=str(shadow),
        container_name=container_name,
    )


def stop_docker_sandbox_processes(workspace: DeveloperWorkspace) -> int:
    probe = detect_container_runtime()
    if not probe.available:
        return 0
    container_name = sandbox_container_name(workspace.id)
    stopped = 0
    for process in list_sandbox_processes(workspace.id):
        if process.pid <= 0:
            continue
        subprocess.run(
            [probe.binary, "exec", container_name, "/bin/sh", "-lc", f"kill {int(process.pid)} 2>/dev/null || true"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        stopped += 1
    _clear_sandbox_processes(workspace.id)
    return stopped


def list_sandbox_processes(workspace_id: str) -> list[SandboxProcessInfo]:
    payload = _load_sessions_payload()
    raw = payload.get("processes", {}).get(workspace_id, [])
    if not isinstance(raw, list):
        return []
    return [SandboxProcessInfo.from_dict(item) for item in raw if isinstance(item, dict)]


def _docker_create_args(binary: str, workspace: DeveloperWorkspace, shadow: pathlib.Path, container_name: str) -> list[str]:
    args = [
        binary,
        "run",
        "-d",
        "--name",
        container_name,
        "--label",
        f"row_bot.developer.workspace_id={workspace.id}",
        "-v",
        f"{str(shadow)}:/workspace",
        "-w",
        "/workspace",
    ]
    if workspace.sandbox_network == "off":
        args.extend(["--network", "none"])
    for name in workspace.sandbox_env_allowlist:
        value = os.environ.get(name)
        if value is not None:
            args.extend(["-e", f"{name}={value}"])
    args.extend([workspace.sandbox_image, "/bin/sh", "-lc", "sleep infinity"])
    return args


def _docker_exec_args(binary: str, container_name: str, command: str) -> list[str]:
    return [binary, "exec", container_name, "/bin/sh", "-lc", command]


def _docker_image_exists(binary: str, image: str) -> bool:
    proc = subprocess.run(
        [binary, "image", "inspect", image],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return proc.returncode == 0


def _container_state(binary: str, container_name: str) -> tuple[bool, bool]:
    proc = subprocess.run(
        [binary, "inspect", "-f", "{{.State.Running}}", container_name],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        return False, False
    return True, (proc.stdout or "").strip().lower() == "true"


def _container_network_mismatch(binary: str, container_name: str, workspace: DeveloperWorkspace) -> bool:
    mode = _container_network_mode(binary, container_name)
    if not mode:
        return False
    if workspace.sandbox_network == "off":
        return mode != "none"
    return mode == "none"


def _container_network_mode(binary: str, container_name: str) -> str:
    proc = subprocess.run(
        [binary, "inspect", "-f", "{{.HostConfig.NetworkMode}}", container_name],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip().lower()


def _missing_image_message(image: str) -> str:
    return (
        f"Docker Sandbox image '{image}' is not available locally. "
        "Row-Bot will not auto-pull sandbox images during command execution because "
        "Docker credential-helper and network failures can otherwise interrupt the chat. "
        f"Pull the image manually with `docker pull {image}`, or choose a sandbox image "
        "that already exists locally."
    )


def _remove_container(binary: str, container_name: str) -> None:
    proc = subprocess.run(
        [binary, "rm", "-f", container_name],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning(
            "Failed to remove Developer sandbox container %s: %s",
            container_name,
            (proc.stderr or proc.stdout or "").strip(),
        )


def _safe_rmtree(path: pathlib.Path) -> None:
    def _onerror(func, target, exc_info):
        try:
            os.chmod(target, 0o700)
            func(target)
        except Exception:
            raise exc_info[1]

    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            shutil.rmtree(path, onerror=_onerror)
            return
        except FileNotFoundError:
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"Failed to remove sandbox folder {path}: {last_error}") from last_error


def _remove_shadow_workspace(workspace_id: str) -> bool:
    target = SANDBOX_ROOT / workspace_id
    resolved = target.resolve()
    root = SANDBOX_ROOT.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Refusing to remove sandbox outside sandbox root: {target}") from exc
    if not resolved.exists():
        return False
    _safe_rmtree(resolved)
    return True


def _record_sandbox_process(workspace_id: str, process: SandboxProcessInfo) -> None:
    payload = _load_sessions_payload()
    processes = payload.setdefault("processes", {})
    rows = [item for item in processes.get(workspace_id, []) if isinstance(item, dict)]
    rows = [item for item in rows if int(item.get("pid") or 0) != process.pid]
    rows.append(process.to_dict())
    processes[workspace_id] = rows
    _save_sessions_payload(payload)


def _clear_sandbox_processes(workspace_id: str) -> None:
    payload = _load_sessions_payload()
    processes = payload.setdefault("processes", {})
    processes.pop(workspace_id, None)
    _save_sessions_payload(payload)


def _prepare_shadow_workspace(workspace_id: str, host_root: pathlib.Path) -> pathlib.Path:
    shadow = sandbox_shadow_path(workspace_id)
    root = SANDBOX_ROOT.resolve()
    shadow_parent = shadow.parent.resolve()
    try:
        shadow_parent.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Refusing to prepare sandbox outside sandbox root: {shadow}") from exc
    if shadow.exists():
        _safe_rmtree(shadow)
    shadow.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(host_root, shadow, ignore=_copy_ignore)
    return shadow


def _ensure_shadow_workspace(workspace: DeveloperWorkspace) -> tuple[str, pathlib.Path]:
    host_root = pathlib.Path(workspace.path).expanduser().resolve()
    if not host_root.is_dir():
        raise ValueError(f"Workspace folder does not exist: {workspace.path}")
    shadow = sandbox_shadow_path(workspace.id)
    if not shadow.exists():
        _prepare_shadow_workspace(workspace.id, host_root)
    return sandbox_container_name(workspace.id), shadow


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _COPY_SKIP_DIRS}


def _friendly_docker_error(message: str) -> str:
    text = (message or "Docker Sandbox failed.").strip()
    if "docker-credential-desktop" in text and "not found" in text:
        return (
            text
            + "\n\nDocker is trying to use the Docker Desktop credential helper, but "
            "docker-credential-desktop is not on PATH. Start Docker Desktop once, repair "
            "the Docker Desktop install, or remove/adjust the credsStore entry in your "
            "Docker config. Docker Sandbox file edits can still use the shadow workspace, "
            "but running sandbox commands requires a working Docker engine and image pull."
        )
    if "Unable to find image" in text:
        return (
            text
            + "\n\nThe sandbox image is not available locally. Start Docker Desktop and "
            "pull the image, or choose an image that already exists locally."
        )
    return text


def _friendly_docker_engine_error(message: str) -> str:
    text = (message or "Docker engine is not accessible.").strip()
    lowered = text.lower()
    if "permission denied" in lowered:
        return text
    stopped_markers = (
        "dockerdesktoplinuxengine",
        "the system cannot find the file specified",
        "open //./pipe/docker",
        "open \\\\.\\pipe\\docker",
        "error during connect",
        "is the docker daemon running",
        "cannot connect to the docker daemon",
    )
    if any(marker in lowered for marker in stopped_markers):
        return (
            "Docker Desktop is installed but not running. Start Docker Desktop and wait "
            "until the engine is ready, then retry the Docker Sandbox command."
        )
    return text


def _validate_shadow_relative_path(root: pathlib.Path, rel_path: str) -> pathlib.Path:
    clean = (rel_path or "").strip().replace("\\", "/")
    if not clean or clean == "/dev/null":
        raise ValueError("Empty sandbox path.")
    if clean.startswith(("a/", "b/")):
        clean = clean[2:]
    if clean.startswith("/") or clean.startswith("../") or "/../" in clean:
        raise ValueError(f"Path escapes sandbox workspace: {rel_path}")
    target = (root / clean).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path escapes sandbox workspace: {rel_path}") from exc
    return target


def _paths_from_patch(root: pathlib.Path, patch: str) -> list[str]:
    paths: list[str] = []
    for line in (patch or "").splitlines():
        text = line.strip()
        candidates: list[str] = []
        if text.startswith("diff --git "):
            parts = text.split()
            if len(parts) >= 4:
                candidates.extend([parts[2], parts[3]])
        elif text.startswith("--- ") or text.startswith("+++ "):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                candidates.append(parts[1].split("\t", 1)[0])
        for candidate in candidates:
            if candidate == "/dev/null":
                continue
            clean = candidate[2:] if candidate.startswith(("a/", "b/")) else candidate
            _validate_shadow_relative_path(root, clean)
            if clean not in paths:
                paths.append(clean)
    return paths


def _snapshot_text_files(root: pathlib.Path) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if _is_skipped_path(rel):
            continue
        if path.stat().st_size > _TEXT_LIMIT:
            continue
        if not _looks_text(path):
            continue
        try:
            snapshot[rel] = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
    return snapshot


@_pending_mutation
def _record_pending_change(
    workspace: DeveloperWorkspace,
    thread_id: str,
    command: str,
    before: dict[str, str | None],
    after: dict[str, str | None],
    *, command_id: str | None = None,
) -> SandboxPendingChange | None:
    changed = sorted(path for path in (set(before) | set(after)) if before.get(path) != after.get(path))
    if not changed:
        return None
    patch_parts: list[str] = []
    for path in changed:
        patch = (_client_file_patch(path, before.get(path), after.get(path)) if command_id
                 else _unified_file_patch(path, before.get(path), after.get(path)))
        if patch:
            patch_parts.append(patch)
    combined_patch = ("".join(patch_parts) if command_id else "\n".join(patch_parts).strip() + "\n") if patch_parts else ""
    if not combined_patch:
        return None
    change_id = ("sbox_" + uuid.uuid5(uuid.NAMESPACE_URL, "row-bot:client-edit:" + command_id).hex if command_id
                 else f"sbox_{hashlib.sha1((workspace.id + command + str(time.time())).encode('utf-8')).hexdigest()[:12]}")
    payload = _load_client_pending() if command_id else _load_pending_payload()
    if command_id:
        matches = [row for row in payload["changes"] if row.get("id") == change_id]
        if matches:
            existing = SandboxPendingChange.from_dict(matches[0])
            if len(matches) != 1 or (existing.workspace_id, existing.thread_id, existing.command, existing.patch, existing.files) != (workspace.id, thread_id, command, combined_patch, changed):
                raise ValueError("sandbox_history_identity_conflict")
            return existing
    change = SandboxPendingChange(
        id=change_id,
        workspace_id=workspace.id,
        thread_id=thread_id,
        command=command,
        patch=combined_patch,
        files=changed,
        created_at=datetime.now().isoformat(),
    )
    payload.setdefault("changes", []).append(change.to_dict())
    _save_pending_payload(payload)
    return change


def _client_file_patch(path: str, before: str | None, after: str | None) -> str:
    """Retain patch line endings; host import still uses Git's EOL policy."""
    if before == after:
        return ""
    def git_lines(value):
        parts = (value or "").split("\n")
        return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    before_lines, after_lines = git_lines(before), git_lines(after)
    lines = difflib.unified_diff(before_lines, after_lines,
        fromfile="/dev/null" if before is None else f"a/{path}\t",
        tofile="/dev/null" if after is None else f"b/{path}\t", lineterm="\n")
    body = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
    if body:
        # A preceding empty-file mode block otherwise consumes this file's
        # ordinary headers as its own. Every emitted file needs a boundary.
        mode = "new file mode 100644\n" if before is None else "deleted file mode 100644\n" if after is None else ""
        return f"diff --git a/{path} b/{path}\n" + mode + body
    # Empty-file creation has no hunk; Git represents it using mode/index.
    if before is None and after == "":
        return f"diff --git a/{path} b/{path}\nnew file mode 100644\nindex 0000000..e69de29\n"
    if before == "" and after is None:
        return f"diff --git a/{path} b/{path}\ndeleted file mode 100644\nindex e69de29..0000000\n"
    raise ValueError("sandbox_patch_unavailable")


def _unified_file_patch(path: str, before: str | None, after: str | None) -> str:
    # Both callers ultimately emit the same Git patch. Dropping line endings
    # made no-final-newline edits inapplicable and silently omitted empty files.
    return _client_file_patch(path, before, after).rstrip("\n")


def _is_skipped_path(rel_path: str) -> bool:
    return any(part in _SNAPSHOT_SKIP_DIRS for part in pathlib.PurePosixPath(rel_path).parts)


def _looks_text(path: pathlib.Path, *, sniff_bytes: int = 4096) -> bool:
    try:
        data = path.read_bytes()[:sniff_bytes]
    except Exception:
        return False
    return b"\0" not in data


def _load_pending_payload() -> dict:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    if not PENDING_CHANGES_PATH.exists():
        return {"changes": []}
    try:
        payload = json.loads(PENDING_CHANGES_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load Developer sandbox pending changes from %s", PENDING_CHANGES_PATH)
        return {"changes": []}
    if not isinstance(payload, dict):
        return {"changes": []}
    payload.setdefault("changes", [])
    return payload


def _save_pending_payload(payload: dict) -> None:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    tmp_path: pathlib.Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="pending_changes.", suffix=".tmp", dir=SANDBOX_ROOT)
        tmp_path = pathlib.Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(PENDING_CHANGES_PATH)
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.debug("Failed to remove temp sandbox pending file %s", tmp_path, exc_info=True)


def _load_sessions_payload() -> dict:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    if not SESSIONS_PATH.exists():
        return {"processes": {}}
    try:
        payload = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load Developer sandbox sessions from %s", SESSIONS_PATH)
        return {"processes": {}}
    if not isinstance(payload, dict):
        return {"processes": {}}
    payload.setdefault("processes", {})
    return payload


def _save_sessions_payload(payload: dict) -> None:
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    tmp_path: pathlib.Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="sessions.", suffix=".tmp", dir=SANDBOX_ROOT)
        tmp_path = pathlib.Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(SESSIONS_PATH)
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logger.debug("Failed to remove temp sandbox session file %s", tmp_path, exc_info=True)
