"""Sandboxes (5.3.1 B4): a second Row-Bot on its own data folder, port and workspace.

A sandbox is for trying a workflow or skill before it touches real accounts, filming demos and
screenshots, and showing Row-Bot to someone without your data. It starts empty or with a made-up
data pack (``sandbox_packs``) in its workspace.

It runs as its own process on this computer only (127.0.0.1), started by this Row-Bot as its
launcher:

- Nothing of the real profile reaches it. It has its own data folder (so its own settings,
  memory, chats and, through the keyring's per-folder service, its own saved keys). Its
  environment carries no keys, tokens or passwords. The real data folder is private to it,
  the way Row-Bot's own data folder is to itself.
- It never updates the install or counts installs, and starts no Ollama of its own: local
  models are the computer's.
- A hosted model needs its own sign-in inside the sandbox. A person opens it through a one-time
  sign-in link this Row-Bot asks it for with its launch secret, so only that window or
  browser may change anything there.
- It shows a "Sandbox" mark.

Reset brings it back to its pack, Stop ends its process, and Delete removes its folder. Every
sandbox stops when Row-Bot quits.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

SANDBOX_ID = re.compile(r"box-[0-9a-f]{8}")
Status = Literal["stopped", "starting", "running", "failed"]
MAX_SANDBOXES = 5
START_SECONDS = 120
SANDBOX_ENV = "ROW_BOT_SANDBOX"
# The real data folder, private to the sandbox (its file tools never read or change it).
SANDBOX_OF_ENV = "ROW_BOT_SANDBOX_OF"
_FOLDER = "sandboxes"
# Never passed on: anything that looks like a key, token, password or credential, and Row-Bot's own settings
# (the sandbox gets its own below).
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION)", re.IGNORECASE)
_LOCK = threading.RLock()
_PROCESSES: dict[str, subprocess.Popen] = {}
_SECRETS: dict[str, str] = {}


class SandboxError(ValueError):
    """The sandbox can't do that now; the message says why."""


@dataclass
class Sandbox:
    sandbox_id: str
    name: str
    pack: str
    created_at: float
    status: str = "stopped"
    port: int = 0
    problem: str = ""
    # Its process, by id and start time: one a crash of this Row-Bot left running is ended before it starts again.
    pid: int = 0
    pid_birth: float = 0.0
    # The approval level its new chats start at: the level of the chat that made it ("" for Ask).
    approval: str = ""

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/app-v2/" if self.port else ""


def sandboxes_root() -> Path:
    from row_bot.data_paths import get_row_bot_data_dir

    return get_row_bot_data_dir() / _FOLDER


def _folder(sandbox_id: str) -> Path:
    return sandboxes_root() / sandbox_id


def _save(sandbox: Sandbox) -> None:
    path = _folder(sandbox.sandbox_id) / "sandbox.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f"sandbox.json.{uuid.uuid4().hex[:6]}.part")
    partial.write_text(json.dumps(asdict(sandbox)), encoding="utf-8", newline="\n")
    os.replace(partial, path)


def _load(path: Path) -> Sandbox | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not SANDBOX_ID.fullmatch(str(data.get("sandbox_id") or "")):
            return None
        return Sandbox(sandbox_id=data["sandbox_id"], name=str(data.get("name") or "Sandbox")[:60],
                       pack=str(data.get("pack") or "empty"), created_at=float(data.get("created_at") or 0),
                       status=str(data.get("status") or "stopped"), port=int(data.get("port") or 0),
                       problem=str(data.get("problem") or "")[:300], pid=int(data.get("pid") or 0),
                       pid_birth=float(data.get("pid_birth") or 0), approval=_level(data.get("approval")))
    except (OSError, ValueError, TypeError, KeyError):
        return None


def get(sandbox_id: str) -> Sandbox | None:
    if not SANDBOX_ID.fullmatch(str(sandbox_id or "")):
        return None
    sandbox = _load(_folder(sandbox_id) / "sandbox.json")
    if sandbox is not None and sandbox.status in {"running", "starting"} and sandbox_id not in _PROCESSES:
        # Its process ended with the Row-Bot that started it (one a crash left running is ended at its next start).
        sandbox.status, sandbox.port = "stopped", 0
    return sandbox


def _end_left_over(sandbox: Sandbox) -> bool:
    """A sandbox process a crash of this Row-Bot left running, still the one it started: ended. True if one was."""
    from row_bot.background_processes import _same_process
    from row_bot.process_cancellation import kill_process_tree

    left = _same_process(sandbox.pid, sandbox.pid_birth or None) if sandbox.pid else None
    if left is None:
        return False
    kill_process_tree(left)
    logger.info("Ended a sandbox a crash left running")
    return True


def list_sandboxes() -> list[Sandbox]:
    root = sandboxes_root()
    found = [get(path.name) for path in root.iterdir() if path.is_dir()] if root.is_dir() else []
    return sorted((item for item in found if item is not None), key=lambda item: item.created_at)


def _level(value: Any) -> str:
    """A sandbox's starting level: Block, Ask or Auto; Hands-off is only ever chosen by the person, there."""
    from row_bot.approval_policy import known_approval_mode

    level = known_approval_mode(value)
    return "auto" if level == "full" else (level or "")


# Its model sign-in: the model chosen, its providers and their saved credentials (keychain entries are kept by
# the data folder's path). A reset keeps them, so a sandbox signs in once.
_SIGN_IN = ("model_settings.json", "providers.json", "secure-secrets")


def _seed(sandbox: Sandbox, *, keep_sign_in: bool = False) -> None:
    """Its workspace with the pack's made-up files, and a fresh data folder (with its sign-in, if kept)."""
    from row_bot.sandbox_packs import FILES

    folder = _folder(sandbox.sandbox_id)
    kept = folder / "kept-sign-in"
    shutil.rmtree(kept, ignore_errors=True)
    if keep_sign_in:
        for name in _SIGN_IN:
            if (folder / "data" / name).exists():
                kept.mkdir(exist_ok=True)
                os.replace(folder / "data" / name, kept / name)
    for name in ("data", "workspace"):
        shutil.rmtree(folder / name, ignore_errors=True)
        (folder / name).mkdir(parents=True, exist_ok=True)
    if kept.is_dir():
        for item in kept.iterdir():
            os.replace(item, folder / "data" / item.name)
        kept.rmdir()
    for relative, text in FILES.get(sandbox.pack, {}).items():
        target = folder / "workspace" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


def create(name: str, pack: str = "empty", *, approval: str = "") -> Sandbox:
    """A new sandbox, seeded with its pack; it starts with ``start``. Its new chats start at *approval*
    (Hands-off becomes Auto; empty is Ask)."""
    from row_bot.sandbox_packs import PACKS

    if pack not in PACKS:
        raise SandboxError(f"A sandbox starts with one of: {', '.join(PACKS)}.")
    if len(list_sandboxes()) >= MAX_SANDBOXES:
        raise SandboxError(f"At most {MAX_SANDBOXES} sandboxes: delete one first.")
    sandbox = Sandbox(sandbox_id=f"box-{uuid.uuid4().hex[:8]}",
                      name=re.sub(r"\s+", " ", str(name or "")).strip()[:60] or PACKS[pack], pack=pack,
                      created_at=time.time(), approval=_level(approval))
    _seed(sandbox)
    _save(sandbox)
    logger.info("Sandbox created (%s)", pack)
    return sandbox


def environment(sandbox: Sandbox, port: int, secret: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """The sandbox process's environment: this one's minus anything secret and Row-Bot's own settings, plus its
    own data folder, port, workspace and launch secret, updates and install counting off, its mark and the
    level its new chats start at."""
    from row_bot.approval_policy import SANDBOX_LEVEL_ENV
    from row_bot.data_paths import get_row_bot_data_dir

    source = dict(os.environ if base is None else base)
    kept = {name: value for name, value in source.items()
            if not name.upper().startswith("ROW_BOT_") and not _SECRET_NAME.search(name)}
    folder = _folder(sandbox.sandbox_id)
    kept.update({
        "ROW_BOT_DATA_DIR": str(folder / "data"),
        "ROW_BOT_WORKSPACE": str(folder / "workspace"),
        "ROW_BOT_HOST": "127.0.0.1",
        "ROW_BOT_PORT": str(port),
        "ROW_BOT_LAUNCH_SECRET": secret,
        "ROW_BOT_UPDATES": "external",
        "ROW_BOT_UPDATES_DIR": str(folder / "updates"),
        "ROW_BOT_INSTALL_COUNT": "0",
        "ROW_BOT_AUTO_START_OLLAMA": "0",
        "ROW_BOT_NO_OPEN": "1",
        "ROW_BOT_DISABLE_TRAY": "1",
        "ROW_BOT_DISABLE_SPLASH": "1",
        SANDBOX_ENV: "1",
        SANDBOX_OF_ENV: str(get_row_bot_data_dir(create=False)),
    })
    if sandbox.approval:
        kept[SANDBOX_LEVEL_ENV] = sandbox.approval
    return kept


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _ping(port: int, secret: str) -> bool:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/api/launcher-ping",
                                     headers={"Authorization": f"Bearer {secret}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def start(sandbox_id: str) -> Sandbox:
    """Start its process; it's running once it answers (followed in the background)."""
    with _LOCK:
        sandbox = get(sandbox_id)
        if sandbox is None:
            raise SandboxError("That sandbox is no longer here.")
        if sandbox_id in _PROCESSES and _PROCESSES[sandbox_id].poll() is None:
            return sandbox
        _end_left_over(sandbox)  # Never two on one data folder.
        port, secret = _free_port(), secrets.token_urlsafe(32)
        folder = _folder(sandbox_id)
        log = open(folder / "sandbox.log", "ab")  # noqa: SIM115 - the process writes to it while it runs.
        try:
            process = subprocess.Popen(  # noqa: S603 - this Python, Row-Bot's own app module
                [sys.executable, "-m", "row_bot.app"], cwd=str(folder / "workspace"),
                env=environment(sandbox, port, secret), stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            log.close()
        _PROCESSES[sandbox_id], _SECRETS[sandbox_id] = process, secret
        from row_bot.background_processes import _birth

        sandbox.status, sandbox.port, sandbox.problem = "starting", port, ""
        sandbox.pid, sandbox.pid_birth = int(process.pid or 0), float(_birth(process.pid) or 0)
        _save(sandbox)
    threading.Thread(target=_follow, args=(sandbox_id, process, port, secret), daemon=True,
                     name=f"row-bot-{sandbox_id}").start()
    logger.info("Sandbox starting")
    return sandbox


def _follow(sandbox_id: str, process: subprocess.Popen, port: int, secret: str) -> None:
    deadline = time.monotonic() + START_SECONDS
    while time.monotonic() < deadline and process.poll() is None:
        if _ping(port, secret):
            _set(sandbox_id, status="running")
            return
        time.sleep(1)
    problem = ("It stopped while starting: its log is in the sandbox's folder." if process.poll() is not None
               else "It didn't answer in time.")
    _end(sandbox_id)
    _set(sandbox_id, status="failed", port=0, problem=problem, pid=0, pid_birth=0.0)


def _set(sandbox_id: str, **changes: Any) -> None:
    with _LOCK:
        sandbox = _load(_folder(sandbox_id) / "sandbox.json")
        if sandbox is None:
            return
        for name, value in changes.items():
            setattr(sandbox, name, value)
        _save(sandbox)


def _end(sandbox_id: str) -> None:
    process = _PROCESSES.pop(sandbox_id, None)
    _SECRETS.pop(sandbox_id, None)
    if process is None or process.poll() is not None:
        return
    import psutil

    from row_bot.process_cancellation import kill_process_tree

    try:
        family = psutil.Process(process.pid)
        started = family.children(recursive=True)
    except psutil.Error:
        family, started = None, []
    process.terminate()
    try:
        process.wait(10)
    except subprocess.TimeoutExpired:
        pass
    # Its own browser and servers go with it.
    for member in ([family] if family is not None and process.poll() is None else []) + started:
        kill_process_tree(member)


def stop(sandbox_id: str) -> Sandbox:
    with _LOCK:
        if get(sandbox_id) is None:
            raise SandboxError("That sandbox is no longer here.")
        _end(sandbox_id)
        _set(sandbox_id, status="stopped", port=0, problem="", pid=0, pid_birth=0.0)
    return get(sandbox_id)  # type: ignore[return-value]


def reset(sandbox_id: str) -> Sandbox:
    """Stopped, and back to its pack: its chats, settings and workspace changes go; its model sign-in stays."""
    sandbox = stop(sandbox_id)
    _seed(sandbox, keep_sign_in=True)
    return sandbox


def delete(sandbox_id: str) -> None:
    with _LOCK:
        if get(sandbox_id) is None:
            raise SandboxError("That sandbox is no longer here.")
        _end(sandbox_id)
        shutil.rmtree(_folder(sandbox_id), ignore_errors=True)


def stop_all() -> int:
    """When Row-Bot quits, its sandboxes stop with it."""
    with _LOCK:
        ids = list(_PROCESSES)
        for sandbox_id in ids:
            _end(sandbox_id)
            _set(sandbox_id, status="stopped", port=0, pid=0, pid_birth=0.0)
    return len(ids)


def sign_in_url(sandbox_id: str) -> str:
    """The sandbox's address with a one-time sign-in, for a window, a browser or Row-Bot's browser to open."""
    sandbox = get(sandbox_id)
    secret = _SECRETS.get(sandbox_id, "")
    if sandbox is None or sandbox.status != "running" or not secret:
        raise SandboxError("Start the sandbox first.")
    request = urllib.request.Request(f"http://127.0.0.1:{sandbox.port}/api/launcher/sign-in", data=b"",
                                     method="POST", headers={"Authorization": f"Bearer {secret}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=5) as response:
            answer = json.loads(response.read(4096).decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        raise SandboxError("The sandbox didn't give a sign-in: try again in a moment.") from None
    token = str(answer.get("invitation") or "") if isinstance(answer, dict) else ""
    if not token:
        raise SandboxError("The sandbox didn't give a sign-in: try again in a moment.")
    from urllib.parse import quote

    return f"{sandbox.url}#sign-in={quote(token, safe='')}"


def running_ports() -> set[int]:
    """The ports of the sandboxes running now (this Row-Bot's own servers)."""
    with _LOCK:
        ids = [sandbox_id for sandbox_id, process in _PROCESSES.items() if process.poll() is None]
    return {sandbox.port for sandbox_id in ids if (sandbox := get(sandbox_id)) is not None and sandbox.port}


def end_left_overs() -> int:
    """At start-up: sandboxes a crash of this Row-Bot left running are ended. How many were."""
    ended = 0
    for sandbox in list_sandboxes():
        if sandbox.sandbox_id not in _PROCESSES and _end_left_over(sandbox):
            _set(sandbox.sandbox_id, status="stopped", port=0, problem="", pid=0, pid_birth=0.0)
            ended += 1
    return ended


def name_at(url: str) -> str:
    """The name of the running sandbox serving *url* (its own port on this computer), or ""."""
    from urllib.parse import urlparse

    parsed = urlparse(str(url or ""))
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or not parsed.port:
        return ""
    with _LOCK:
        ids = [sandbox_id for sandbox_id, process in _PROCESSES.items() if process.poll() is None]
    return next((sandbox.name for sandbox_id in ids
                 if (sandbox := get(sandbox_id)) is not None and sandbox.port == parsed.port), "")


def wait_running(sandbox_id: str, seconds: float) -> Sandbox | None:
    """Wait while it starts; the sandbox as it is then."""
    deadline = time.monotonic() + seconds
    sandbox = get(sandbox_id)
    while sandbox is not None and sandbox.status == "starting" and time.monotonic() < deadline:
        time.sleep(0.5)
        sandbox = get(sandbox_id)
    return sandbox


def model(sandbox_id: str) -> str:
    """The chat model the sandbox has chosen (it signs in to a provider of its own, decision 4), or ""."""
    try:
        saved = json.loads((_folder(sandbox_id) / "data" / "model_settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(saved.get("model") or "").strip() if isinstance(saved, dict) else ""


def has_model(sandbox_id: str) -> bool:
    return bool(model(sandbox_id))


def reusable(pack: str) -> Sandbox | None:
    """A sandbox with this pack and a model, a running one first: reusing it keeps its sign-in."""
    found = [item for item in list_sandboxes() if item.pack == pack and has_model(item.sandbox_id)]
    found.sort(key=lambda item: (item.status != "running", -item.created_at))
    return found[0] if found else None


def view(sandbox: Sandbox) -> dict[str, Any]:
    from row_bot.sandbox_packs import PACKS

    return {"sandbox_id": sandbox.sandbox_id, "name": sandbox.name, "pack": sandbox.pack,
            "pack_name": PACKS.get(sandbox.pack, sandbox.pack), "status": sandbox.status,
            "url": sandbox.url if sandbox.status == "running" else "", "problem": sandbox.problem,
            "needs_model": not has_model(sandbox.sandbox_id)}


def is_sandbox(environ: dict[str, str] | None = None) -> bool:
    """True in a sandbox's own process."""
    return str((os.environ if environ is None else environ).get(SANDBOX_ENV) or "") == "1"
