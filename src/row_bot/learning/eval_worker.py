"""Learning's evaluation worker: test tasks through the real Agent runtime, in an owned process (O56).

Comparing personal-model versions needs Row-Bot's real runtime: the personal model learned
Row-Bot's tool bridge (``tool_search`` / ``tool_invoke``), so testing it without the bridge measures
the wrong thing (O29). This worker runs each pack task as a real Agent turn, the way the model
will serve: the person's own enabled tools (with A5's deferral and the bridge on a small context),
the pack's fake apps behind them, Row-Bot's filesystem tool on a throwaway workspace, thinking off
for a personal model (its saved settings, O24/O30).

**What it is, and what it may touch**

- An **owned process** like the training worker (E15, ``training/worker.py``): the app's base
  interpreter (never a virtual environment's launcher, O43), a private kill-on-close job object on
  Windows and its own process group elsewhere; :meth:`EvalWorker.stop` (and quitting Row-Bot) ends
  the whole tree. It has no conversation owner.
- Its **own throwaway data folder** in the system temp folder (refused inside the person's data
  folder), deleted when it stops. It never reads the person's conversations, memory or settings:
  every Row-Bot module in it binds to the throwaway folder (``ROW_BOT_DATA_DIR``).
- **Credentials** for the arms' models are read once from the person's store by this (parent)
  process and handed over on the worker's standard input, the way L0c's ChatGPT lane does it: in
  the worker they live in an in-memory keyring (``secret_store.use_memory_backend``), never on its
  disk or in its logs (its log lines are redacted). A token the worker refreshes (ChatGPT's sign-in
  rotates) comes back on its standard output and is written **only to the person's own store**,
  with Row-Bot's own writer, and only when the person's sign-in is still the one that was read.
- Tools that act on this computer or an account outside the test (shell, browser, desktop, mail,
  calendar, developer tools, MCP servers, updates…) are left out of the turn (:data:`UNSAFE_TOOLS`),
  so the test runs Hands-off inside its own workspace.

Every arm gets the same budget (E12): the same tokens per task (a gate refuses a model call that
would pass it, before it is sent), the same tool calls per task and the same time.

**Each model is measured the way it serves** (L2v): its own saved serving settings (a personal
version and its untouched starting model as ``serving.serve`` registers them: their context,
thinking off, O24/O30; the person's main model with the person's own settings), so its tool
exposure is Row-Bot's own for that context (A5's bridge at 32K or less, everything bound above
64K). The worker's own profile doesn't distort that: the pack's apps are switched on (a tool that
is off is listed to the model as off), the person's libraries answer as the empty libraries they
are, nothing one task leaves behind (its conversation, memories) is seen by the next, and a check
reads the reply's last message. Each case says why it didn't pass (:data:`CAUSES`).
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BOOT = Path(__file__).resolve().with_name("eval_worker_boot.py")
FRAME_MAX = 8 * 1024 * 1024
READY_SECONDS = 240.0
TASK_SECONDS = 900.0
STOP_WAIT_SECONDS = 10.0
#: Tool calls a task may make: its own step count times this (the same for every arm).
TOOL_CALL_FACTOR = 3
PREFIX = "row-bot-eval-"
#: ``notifications.DESKTOP_SWITCH``, written out so this module imports nothing more for it.
DESKTOP_SWITCH = "ROW_BOT_DESKTOP_NOTIFICATIONS"
#: Tools that act on this computer or an account outside the test's workspace, reach the network, or
#: always stop for a person: never bound in an evaluation turn.
UNSAFE_TOOLS = frozenset({
    # act on this computer or an account outside the test's workspace
    "shell", "browser", "computer_use", "gmail", "calendar", "x", "developer", "designer", "mcp",
    "row_bot_updater", "custom_tool_builder", "system_info", "send_file", "file_delivery", "email",
    "telegram", "slack", "discord", "whatsapp", "sms",
    # reach the network
    "web_search", "duckduckgo", "url_reader", "web_fetch", "youtube", "wikipedia", "arxiv", "weather",
    # extra media calls, or work that outlives the turn (workflows, goals) or stops for a person
    "vision", "image_gen", "video_gen", "video_editing", "conversation_setup", "task", "goal",
})
#: The fake apps, as the worker registers them (pack toolset → tool name).
APP_TOOLS = {"calendar": "learning_calendar", "mail": "learning_mail", "issues": "learning_issues"}
_CARRIED_SKIP = re.compile(r"^(ROW_BOT|PYTEST|COV_|PYTHONPATH$|PYTHONHOME$|VIRTUAL_ENV$|UV_)")
#: Why a task that ran didn't pass (a case's ``cause``), decided from the turn's own events (L2v).
#: Codes only: no task text, so a hidden task's cause says nothing about the task (E10).
CAUSES: dict[str, str] = {
    "provider_error": "the model call failed (the provider or its tool-call parser)",
    "harness": "the test itself couldn't run or check the task",
    "budget": "ran out of its steps, tokens or time",
    "no_tool": "answered without using any tool",
    "tool_not_found": "never reached the tool the task needs",
    "bad_call": "a tool call was malformed or refused",
    "file_write": "the file the task asks for wasn't written",
    "no_answer": "the turn ended without an answer",
    "wrong_answer": "used the tools and answered, but the check failed",
}
#: Trace events a developer's run keeps per task (only when it asks for them).
TRACE_EVENTS = 160
_APP_PREFIXES = ("calendar_", "mail_", "issues_")
_FILE_WRITERS = frozenset({"workspace_write_file", "write_file", "workspace_edit_file", "edit_file"})


def _result_problem(name: str, content: Any) -> str:
    """The refusal or error a tool result reports, as a short code ("" when it worked)."""

    text = content if isinstance(content, str) else json.dumps(content, default=str) if content else ""
    text = str(text or "")
    head = text.lstrip()[:4000]
    if head.startswith("{"):
        try:
            found = json.loads(head if len(text) <= 4000 else text)
        except ValueError:
            found = None
        if isinstance(found, dict):
            if found.get("ok") is False:
                error = found.get("error")
                code = error.get("code") if isinstance(error, dict) else error
                return str(code or "refused")[:40]
            if "ok" not in found and found.get("error"):
                return "tool_error"  # an app's own refusal ("no event …")
            if name == "tool_search" and found.get("ok") and not found.get("results"):
                return "no_match"
    lowered = head[:600].lower()
    if "is not a valid tool" in lowered:
        return "unknown_tool"
    if "validation error" in lowered or "error invoking tool" in lowered or "field required" in lowered:
        return "invalid_arguments"
    if lowered.startswith(("error:", "tool error:")):
        return "tool_error"
    return ""


@dataclass
class TurnRecord:
    """What one evaluation turn did, compact: the tools it called (a ``tool_invoke`` counts as its
    target), the tool results that were refusals or errors, model-call errors and how it ended. The
    trace (``events``) is kept only when asked: a developer's run of the visible packs, never the
    product's comparison or a hidden task."""

    keep: bool = False
    calls: list[str] = field(default_factory=list)
    #: Every tool result in order: (the tool it came from, a ``tool_invoke`` as its target; its problem or "").
    results: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    searches: int = 0
    empty_searches: int = 0
    ended: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    _names: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def problems(self) -> list[tuple[str, str]]:
        return [(name, problem) for name, problem in self.results if problem and problem != "no_match"]

    def unrecovered(self) -> list[tuple[str, str]]:
        """Refused or failed calls the turn never got right afterwards (the same tool working later)."""

        found: list[tuple[str, str]] = []
        for index, (name, problem) in enumerate(self.results):
            if problem and problem != "no_match" and not any(
                    later == name and not again for later, again in self.results[index + 1:]):
                found.append((name, problem))
        return found

    def _keep(self, event: dict[str, Any]) -> None:
        if self.keep and len(self.events) < TRACE_EVENTS:
            self.events.append(event)

    def add(self, kind: str, payload: Any) -> None:
        if kind == "tool_call":
            raw = str(getattr(payload, "raw_name", "") or payload or "")
            name = str(getattr(payload, "runtime_name", "") or raw)
            self.calls.append(name)
            self._names[str(getattr(payload, "call_id", "") or "")] = name
            if raw == "tool_search":
                self.searches += 1
            args = getattr(payload, "args", None)
            self._keep({"call": name, "via": raw if raw != name else "",
                        "args": json.dumps(args, ensure_ascii=False, default=str)[:400] if args else ""})
        elif kind == "tool_done" and isinstance(payload, Mapping):
            raw = str(payload.get("raw_name") or payload.get("name") or "")
            name = self._names.get(str(payload.get("tool_call_id") or ""), raw) or raw
            content = payload.get("content")
            problem = _result_problem(raw, content)
            if problem == "no_match":
                self.empty_searches += 1
            self.results.append((name, problem))
            text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
            self._keep({"result": name, "problem": problem, "head": str(text or "")[:400]})
        elif kind == "error":
            self.errors.append(str(payload or "")[:300])
            self._keep({"error": str(payload or "")[:400]})
        elif kind == "terminal":
            reason = payload.get("terminal_reason") if isinstance(payload, Mapping) else ""
            self.ended = str(reason or "terminal")
            self._keep({"terminal": self.ended})
        elif kind == "interrupt":
            self.ended = "asked"
            self._keep({"interrupt": True})
        elif kind == "done":
            self._keep({"done": str(payload or "")[:600]})
        elif kind == "reply":  # the whole reply, when its last message (``done``) is only part of it
            self._keep({"reply": str(payload or "")[:600]})
        elif kind == "warning":
            self._keep({"warning": str(payload or "")[:300]})


def failure_cause(status: str, detail: str, record: TurnRecord, *, task_tools: Sequence[str] = (),
                  check: str = "") -> str:
    """Why a task that ran didn't pass ("" when it passed or didn't run): one of :data:`CAUSES`.

    In order: the model call failed; the test itself failed; a budget ran out; then, for a failed
    check, no tool used at all, the task's app never reached, the file the check needs refused
    when written (O22), a malformed or refused call it never got right, and otherwise a wrong
    answer."""

    if status in ("passed", "not_run", "skipped", ""):
        return ""
    if status == "error":
        return "provider_error" if detail.startswith("the model call failed") else "harness"
    if status == "budget":
        return "budget"
    if detail == "stopped to ask for approval":
        return "harness"
    if detail == "the turn ended without an answer":
        return "no_answer"
    if not record.calls:
        return "no_tool"
    apps = [app for app in task_tools if app in APP_TOOLS]
    if apps and not any(call.startswith(_APP_PREFIXES) for call in record.calls):
        return "tool_not_found"
    if check == "file" and ("was not written" in detail or "is not JSON" in detail) and any(
            name in _FILE_WRITERS for name, _ in record.problems):
        return "file_write"
    if record.unrecovered():
        return "bad_call"
    return "wrong_answer"


class EvalWorkerError(RuntimeError):
    """The worker couldn't start, was refused, or ended early; ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


# ── What the worker is handed ───────────────────────────────────────────────


@dataclass(frozen=True)
class WorkerArm:
    """One model the worker runs tasks on."""

    name: str
    model_ref: str
    local: bool = False


@dataclass
class Credential:
    """One provider's sign-in, read from the person's store (kept in this process's memory only)."""

    provider: str
    kind: str  # api_key · codex_oauth
    values: dict[str, str]
    revision: str = ""

    def secrets(self) -> tuple[str, ...]:
        return tuple(value for key, value in self.values.items()
                     if value and key in ("api_key", "access_token", "refresh_token", "id_token"))


def _provider_of(model_ref: str) -> str:
    from row_bot.providers.selection import parse_model_ref

    parsed = parse_model_ref(model_ref) if model_ref else None
    return parsed[0] if parsed else ""


# The person's Claude plan runs their own, unmodified Claude Code: Row-Bot never reads, stores or hands over its
# sign-in (Anthropic's terms), so an arm on it is never run here.
_NEVER_HANDED_OVER = frozenset({"claude_subscription"})


def gather(arms: Sequence[WorkerArm]) -> tuple[list[Credential], dict[str, Any], list[str]]:
    """In the app: the credentials, endpoint definitions and local model settings the arms need,
    and the arms that can't be handed over (their names). Never refreshes a sign-in."""

    from row_bot import models
    from row_bot.providers import auth_store, custom

    credentials: dict[str, Credential] = {}
    endpoints: dict[str, dict[str, Any]] = {}
    local_settings: dict[str, dict[str, Any]] = {}
    unsupported: list[str] = []
    for arm in arms:
        provider = _provider_of(arm.model_ref)
        model_id = arm.model_ref.split(":", 2)[-1] if arm.model_ref.count(":") >= 2 else ""
        if provider in _NEVER_HANDED_OVER:
            unsupported.append(arm.name)  # nothing of it is read
            continue
        if provider == "ollama":
            found = models.local_model_settings(model_id)
            if found:
                local_settings[model_id] = found
            continue
        if provider.startswith("custom_openai_"):
            endpoint = custom.get_custom_endpoint(provider) or {}
            if not endpoint:
                unsupported.append(arm.name)
                continue
            endpoints[provider] = {key: endpoint[key] for key in (
                "id", "name", "base_url", "profile", "execution_location", "auth_required", "reasoning_mode",
                "manual_capabilities", "enabled") if key in endpoint}
            if endpoint.get("auth_required") and provider not in credentials:
                key = auth_store.get_provider_secret(provider, "api_key")
                if not key:
                    unsupported.append(arm.name)
                    continue
                credentials[provider] = Credential(provider, "api_key", {"api_key": key})
            continue
        if provider in credentials:
            continue
        if provider == "codex":
            values, entry, revision = auth_store.read_provider_oauth_bundle_snapshot("codex")
            if not (values.get("access_token") or values.get("refresh_token")):
                unsupported.append(arm.name)
                continue
            credentials[provider] = Credential(provider, "codex_oauth", {
                "access_token": values.get("access_token") or "", "refresh_token": values.get("refresh_token") or "",
                "id_token": values.get("id_token") or "", "account_id": values.get("account") or "",
                "expires_at": str(entry.get("expires_at") or ""), "plan_type": str(entry.get("plan_type") or ""),
            }, revision)
            continue
        if provider in auth_store.OAUTH_PROVIDER_IDS:
            unsupported.append(arm.name)  # only ChatGPT's sign-in can be handed over so far
            continue
        key = auth_store.get_provider_secret(provider, "api_key") if provider else ""
        if not key:
            unsupported.append(arm.name)
            continue
        credentials[provider] = Credential(provider, "api_key", {"api_key": key})
    return list(credentials.values()), {"custom_endpoints": list(endpoints.values()),
                                        "local_model_settings": local_settings}, unsupported


def write_back(credential: Credential, refreshed: Mapping[str, Any]) -> str:
    """Save tokens the worker refreshed into the person's own store; nothing when they didn't change,
    or when the person's sign-in changed meanwhile (it is theirs, newer than ours)."""

    if credential.kind != "codex_oauth":
        return "unchanged"
    tokens = ("access_token", "refresh_token", "id_token")
    merged = {key: str(refreshed.get(key) or credential.values.get(key) or "") for key in (
        *tokens, "account_id", "expires_at", "plan_type")}
    if not merged["access_token"] or all(merged[key] == credential.values.get(key, "") for key in tokens):
        return "unchanged"
    from row_bot.providers import codex
    from row_bot.providers.config import ProviderConfigError

    try:
        codex.save_codex_oauth_tokens(codex.CodexTokenSet(
            access_token=merged["access_token"], refresh_token=merged["refresh_token"], id_token=merged["id_token"],
            expires_at=merged["expires_at"], account_id=merged["account_id"], plan_type=merged["plan_type"],
        ), expected_revision=credential.revision or None)
    except ProviderConfigError:
        return "not_written"
    from row_bot.providers.auth_store import read_provider_oauth_bundle_snapshot

    try:
        credential.revision = read_provider_oauth_bundle_snapshot("codex")[2]
    except Exception:  # noqa: BLE001
        credential.revision = ""
    credential.values.update(merged)
    return "written"


def safe_tools(enabled: Iterable[str]) -> list[str]:
    """The person's enabled tools an evaluation turn may bind (filesystem always)."""

    names = [str(name) for name in enabled if str(name) and str(name) not in UNSAFE_TOOLS]
    return list(dict.fromkeys(["filesystem", *names]))


def person_tools() -> list[str]:
    """The person's enabled tools (the registry's own answer, in this process)."""

    from row_bot.tools import registry

    return [tool.name for tool in registry.get_enabled_tools()]


# ── The process (the app's side) ────────────────────────────────────────────


def _interpreter() -> str:
    base = getattr(sys, "_base_executable", "") or sys.executable
    return base if Path(base).is_file() else sys.executable


def _import_paths() -> tuple[list[str], list[str]]:
    """Where this process imports Row-Bot and its libraries from (handed to the worker)."""

    import row_bot

    sites = [path for path in sys.path if path and os.path.basename(path.rstrip("\\/")).lower() == "site-packages"
             and os.path.isdir(path)]
    package_parent = str(Path(row_bot.__file__).resolve().parents[1])
    return list(dict.fromkeys(sites)), [package_parent]


def worker_environment(data: Path, *, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The worker's environment: the person's, minus Row-Bot's own switches and provider keys
    (credentials come on standard input), bound to the throwaway folder."""

    try:
        from row_bot.providers.auth_store import PROVIDER_API_KEY_ENV

        keys = {name.upper() for name in PROVIDER_API_KEY_ENV.values()}
    except Exception:  # noqa: BLE001
        keys = set()
    environment = {key: value for key, value in os.environ.items()
                   if not _CARRIED_SKIP.match(key.upper()) and key.upper() not in keys}
    environment.update({
        "ROW_BOT_DATA_DIR": str(data), "ROW_BOT_AUTO_START_OLLAMA": "0", "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONNOUSERSITE": "1",
        # A failed test turn never reaches the person's desktop (no notification, no sound; L2v).
        DESKTOP_SWITCH: "off",
    })
    for key, value in (extra or {}).items():
        environment[str(key)] = str(value)
    return environment


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


class EvalWorker:
    """One owned worker process for one comparison (see the module's docstring).

    ``command`` replaces the real worker in tests (a small script speaking the same lines).
    """

    def __init__(self, spec: Mapping[str, Any], *, credentials: Sequence[Credential] = (),
                 person_data: Path | None = None, command: Sequence[str] | None = None,
                 on_refreshed: Callable[[Credential, Mapping[str, Any]], str] = write_back,
                 ready_seconds: float = READY_SECONDS, scratch: Path | None = None) -> None:
        self.spec = dict(spec)
        self.credentials = {item.provider: item for item in credentials}
        self.person_data = Path(person_data) if person_data is not None else None
        self._command = list(command) if command else None
        self._on_refreshed = on_refreshed
        self.ready_seconds = ready_seconds
        self._scratch = scratch
        self.folder: Path | None = None
        self._process: subprocess.Popen | None = None
        self._job: Any = None
        self._frames: list[dict[str, Any]] = []
        self._condition = threading.Condition()
        self._ended = False
        self._tail = bytearray()
        self.write_backs: list[str] = []
        self.notes: list[str] = []
        self.tools: list[str] = []
        self.violation: str | None = None

    # ── Lifecycle ───────────────────────────────────────────────────────

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None and not self._ended

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def __enter__(self) -> EvalWorker:
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def start(self) -> None:
        if self._process is not None:
            raise EvalWorkerError("started")
        parent = self._scratch if self._scratch is not None else Path(tempfile.gettempdir())
        parent.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix=PREFIX, dir=str(parent)))
        if self.person_data is not None and (_inside(folder, self.person_data) or _inside(self.person_data, folder)):
            shutil.rmtree(folder, ignore_errors=True)
            raise EvalWorkerError("refused", "the worker's folder is inside the person's data folder")
        self.folder = folder
        data = folder / "data"
        data.mkdir()
        (folder / "work").mkdir()
        if self._command is not None:
            argv = list(self._command)
        else:
            sites, paths = _import_paths()
            argv = [_interpreter(), "-I", "-B", str(BOOT)]
            for item in sites:
                argv += ["--site", item]
            for item in paths:
                argv += ["--path", item]
        kwargs: dict[str, Any] = {
            "cwd": str(folder / "work"), "env": worker_environment(data), "shell": False,
            "stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "bufsize": 0,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(argv, **kwargs)  # noqa: S603 - our own worker, no shell
        except OSError as error:
            shutil.rmtree(folder, ignore_errors=True)
            raise EvalWorkerError("start_failed") from error
        try:
            # The worker waits for its spec on standard input: nothing it runs exists before it is owned.
            if os.name == "nt":
                from row_bot.plugins.worker_ownership import WindowsJob

                self._job = WindowsJob(process)
        except Exception:
            process.kill()
            process.wait(STOP_WAIT_SECONDS)
            shutil.rmtree(folder, ignore_errors=True)
            raise EvalWorkerError("ownership_unavailable") from None
        self._process = process
        threading.Thread(target=self._read, name="learning-eval-reader", daemon=True).start()
        threading.Thread(target=self._drain, name="learning-eval-log", daemon=True).start()
        _register(self)
        spec = {**self.spec, "data": str(data), "person_data": str(self.person_data or ""),
                "credentials": [asdict(item) for item in self.credentials.values()]}
        if not self._send({"type": "start", "spec": spec}):
            self.stop()
            raise EvalWorkerError("start_failed", "the worker took no spec")
        ready = self._wait(lambda frame: frame.get("type") in ("ready", "failed"), self.ready_seconds)
        if ready is None or ready.get("type") != "ready":
            reason = str((ready or {}).get("reason") or "no_answer")[:64]
            self.stop()
            raise EvalWorkerError("not_ready", reason)
        self.tools = [str(item) for item in ready.get("tools") or ()][:512]
        self.notes = [str(item)[:200] for item in ready.get("notes") or ()][:16]

    def stop(self) -> bool:
        """End the worker and its whole tree now, and delete its throwaway folder."""

        process = self._process
        ended = True
        if process is not None:
            self._send({"type": "stop"})
            ended = self._end_tree(process)
            try:
                process.wait(STOP_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                ended = False
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError):
                    pass
        with self._condition:
            self._ended = True
            self._condition.notify_all()
        _unregister(self)
        if self.folder is not None:
            for _ in range(20):
                shutil.rmtree(self.folder, ignore_errors=True)
                if not self.folder.exists():
                    break
                time.sleep(0.25)
        return ended

    def _end_tree(self, process: subprocess.Popen) -> bool:
        if self._job is not None:
            return bool(self._job.close())
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                return False
            return True
        if process.poll() is None:
            process.kill()
        return True

    # ── Frames ──────────────────────────────────────────────────────────

    def _send(self, message: Mapping[str, Any]) -> bool:
        process = self._process
        if process is None or process.stdin is None:
            return False
        raw = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(raw) > FRAME_MAX:
            raise EvalWorkerError("frame_too_large")
        try:
            process.stdin.write(raw)
            process.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _read(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        stream = io.BufferedReader(process.stdout, buffer_size=65536)
        try:
            while True:
                raw = stream.readline(FRAME_MAX + 1)
                if not raw:
                    break
                try:
                    frame = json.loads(raw.decode("utf-8"))
                    if not isinstance(frame, dict) or not isinstance(frame.get("type"), str):
                        raise ValueError("not a frame")
                except (ValueError, UnicodeDecodeError):
                    self.violation = "bad_frame"
                    break
                if frame["type"] == "refreshed":
                    self._refreshed(frame)
                    continue
                with self._condition:
                    self._frames.append(frame)
                    self._condition.notify_all()
        except (OSError, ValueError):
            pass
        with self._condition:
            self._ended = True
            self._condition.notify_all()

    def _drain(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for block in iter(lambda: process.stderr.read(8192), b""):
                self._tail.extend(block)
                if len(self._tail) > 32 * 1024:
                    del self._tail[: len(self._tail) - 32 * 1024]
        except (OSError, ValueError):
            pass

    def log_tail(self) -> str:
        """The end of the worker's log, with every credential it was handed taken out."""

        text = bytes(self._tail).decode("utf-8", "replace")
        for credential in self.credentials.values():
            for secret in credential.secrets():
                text = text.replace(secret, "[redacted]")
        return text

    def _refreshed(self, frame: Mapping[str, Any]) -> None:
        credential = self.credentials.get(str(frame.get("provider") or ""))
        bundle = frame.get("bundle")
        if credential is None or not isinstance(bundle, dict):
            return
        try:
            self.write_backs.append(self._on_refreshed(credential, bundle))
        except Exception as error:  # noqa: BLE001 - a failed write-back never ends the run
            logger.warning("Learning couldn't save a refreshed sign-in (%s)", type(error).__name__)
            self.write_backs.append("failed")

    def _wait(self, wanted: Callable[[dict[str, Any]], bool], timeout: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for index, frame in enumerate(self._frames):
                    if wanted(frame):
                        return self._frames.pop(index)
                left = deadline - time.monotonic()
                if self._ended or left <= 0:
                    return None
                self._condition.wait(min(left, 1.0))

    # ── Tasks ───────────────────────────────────────────────────────────

    def run(self, task: Mapping[str, Any], arm: str, *, budget: Mapping[str, Any],
            timeout: float = TASK_SECONDS, trace: bool = False) -> dict[str, Any]:
        """Run one pack task (its canonical dict) on one arm; the worker's result frame (with the
        turn's trace when ``trace``: a developer's look at the visible packs)."""

        if not self.alive:
            raise EvalWorkerError("not_running")
        ident = uuid.uuid4().hex
        message = {"type": "task", "id": ident, "arm": arm, "task": dict(task), "budget": dict(budget),
                   "timeout": timeout}
        if trace:
            message["trace"] = True
        if not self._send(message):
            raise EvalWorkerError("not_running")
        frame = self._wait(lambda item: item.get("type") == "result" and item.get("id") == ident, timeout + 120)
        if frame is None:
            raise EvalWorkerError("no_result", "the worker stopped answering")
        return frame


_live: set[EvalWorker] = set()
_live_lock = threading.Lock()


def _register(worker: EvalWorker) -> None:
    with _live_lock:
        _live.add(worker)


def _unregister(worker: EvalWorker) -> None:
    with _live_lock:
        _live.discard(worker)


def stop_all() -> int:
    """Row-Bot is quitting (or Learning stopped): every evaluation worker ends with its tree."""

    with _live_lock:
        workers = list(_live)
    for worker in workers:
        try:
            worker.stop()
        except Exception:  # noqa: BLE001
            logger.debug("Learning couldn't stop an evaluation worker", exc_info=True)
    return len(workers)


# ── The worker (its own process) ────────────────────────────────────────────


class _Redacting(logging.Formatter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        return text


class _Current:
    """The fake apps of the task that is running (the worker runs one task at a time)."""

    apps: Any = None


_CURRENT = _Current()


def _register_fake_apps() -> list[str]:
    """The fake apps as Row-Bot tools in this (worker) process."""

    from langchain_core.tools import StructuredTool

    from row_bot.learning.packs.fake_apps import FakeApps
    from row_bot.tools import registry
    from row_bot.tools.base import BaseTool

    names = {"calendar": "Calendar (test)", "mail": "Mail (test)", "issues": "Issue tracker (test)"}

    class FakeAppTool(BaseTool):
        def __init__(self, app: str, name: str) -> None:
            self._app, self._name = app, name

        @property
        def name(self) -> str:
            return self._name

        @property
        def display_name(self) -> str:
            return names[self._app]

        @property
        def description(self) -> str:
            return f"The {self._app} app of this test."

        @property
        def enabled_by_default(self) -> bool:
            return False

        def execute(self, query: str) -> str:
            return "Use the app's own tools."

        def as_langchain_tools(self) -> list:
            def bind(tool_name: str):
                return lambda **args: (_CURRENT.apps or FakeApps()).call(tool_name, args)

            return [StructuredTool.from_function(func=bind(tool.name), name=tool.name, description=tool.description,
                                                 args_schema=tool.parameters())
                    for tool in FakeApps().tools([self._app])]

    for app, name in APP_TOOLS.items():
        if registry.get_tool(name) is None:
            registry.register(FakeAppTool(app, name))
        # On in this throwaway profile: a tool the registry holds but hasn't switched on is listed
        # to the model as "TOOLS THAT ARE OFF" in every turn's context, beside the same app as
        # available, and small models then wouldn't use it (L2v). Each turn still binds only its
        # task's apps.
        if not registry.is_enabled(name):
            registry.set_enabled(name, True)
    return list(APP_TOOLS.values())


def _empty_libraries() -> list[str]:
    """The person's own libraries are empty in the throwaway profile, and their tools say so.

    The documents tool embeds the query before it looks at the (empty) library, so where the
    embedding model isn't on this computer it answered every call with an error, and small models
    called it again and again (L2v). In the worker it answers as an empty library does, with the
    same name, description and schema the model sees when it serves."""

    from row_bot.tools import registry

    changed: list[str] = []
    tool = registry.get_tool("documents")
    if tool is not None:
        tool.execute = lambda query: f"No results found for: {query}"  # type: ignore[method-assign]
        changed.append("documents")
    return changed


def _forget_task(thread_id: str) -> None:
    """What one task's turn left in the worker's profile goes before the next task, so no task
    (or arm) finds another's conversation or memories (L2v): its thread, and every memory (the
    throwaway profile has none of its own)."""

    try:
        from row_bot.thread_cleanup import delete_thread

        delete_thread(thread_id)
    except Exception as error:  # noqa: BLE001 - a later task still runs
        logging.getLogger(__name__).info("An evaluation thread wasn't removed (%s)", type(error).__name__)
    try:
        from row_bot import knowledge_graph

        if knowledge_graph.count_entities():
            knowledge_graph.delete_all_entities()
    except Exception as error:  # noqa: BLE001
        logging.getLogger(__name__).info("An evaluation task's memories weren't removed (%s)", type(error).__name__)


def _seed_credentials(credentials: Sequence[Mapping[str, Any]], emit: Callable[[dict[str, Any]], None],
                      secrets: list[str]) -> None:
    from row_bot.providers import auth_store

    for raw in credentials:
        provider, kind = str(raw.get("provider") or ""), str(raw.get("kind") or "")
        values = {str(key): str(value) for key, value in dict(raw.get("values") or {}).items()}
        secrets.extend(value for key, value in values.items()
                       if value and key in ("api_key", "access_token", "refresh_token", "id_token"))
        if kind == "api_key" and values.get("api_key"):
            auth_store.set_provider_secret(provider, "api_key", values["api_key"])
        elif kind == "codex_oauth" and values.get("access_token"):
            from row_bot.providers import codex

            codex.save_codex_oauth_tokens(codex.CodexTokenSet(
                access_token=values["access_token"], refresh_token=values.get("refresh_token", ""),
                id_token=values.get("id_token", ""), expires_at=values.get("expires_at", ""),
                account_id=values.get("account_id", ""), plan_type=values.get("plan_type", "")))
            original = codex.save_codex_oauth_tokens

            def save_and_report(token_set: Any, *args: Any, **kwargs: Any) -> Any:
                saved = original(token_set, *args, **kwargs)
                bundle = {"access_token": token_set.access_token, "refresh_token": token_set.refresh_token,
                          "id_token": token_set.id_token, "account_id": token_set.account_id,
                          "expires_at": token_set.expires_at, "plan_type": token_set.plan_type}
                secrets.extend(value for key, value in bundle.items() if value and key.endswith("token"))
                emit({"type": "refreshed", "provider": "codex", "bundle": bundle})
                return saved

            codex.save_codex_oauth_tokens = save_and_report


def _setup_providers(spec: Mapping[str, Any], notes: list[str]) -> None:
    from row_bot import models
    from row_bot.providers import custom

    providers = dict(spec.get("providers") or {})
    for endpoint in providers.get("custom_endpoints") or ():
        try:
            custom.save_custom_endpoint(dict(endpoint))
            custom.refresh_custom_endpoint_models(str(endpoint.get("id") or ""))
        except Exception as error:  # noqa: BLE001 - the arm's first task reports it
            notes.append(f"endpoint {str(endpoint.get('id') or '')[:40]}: {type(error).__name__}")
    for name, values in dict(providers.get("local_model_settings") or {}).items():
        try:
            models.set_local_model_settings(str(name), context=values.get("context"), reasoning=values.get("reasoning"))
        except Exception as error:  # noqa: BLE001
            notes.append(f"local settings: {type(error).__name__}")
    if any(str(arm.get("model_ref") or "").startswith("model:ollama:") for arm in spec.get("arms") or ()):
        try:
            from row_bot.learning.training.serving import refresh_ollama_catalog

            notes.append(f"local catalog: {refresh_ollama_catalog()} Ollama models")
        except Exception as error:  # noqa: BLE001
            notes.append(f"local catalog: {type(error).__name__}")
    for arm in spec.get("arms") or ():
        reference = str(arm.get("model_ref") or "")
        provider = _provider_of(reference)
        if provider.startswith("custom_openai_"):
            # Row-Bot's own probe, as the person's endpoint passed it (its result lives in their settings).
            try:
                custom.probe_custom_endpoint(provider, reference.split(":", 2)[-1])
            except Exception as error:  # noqa: BLE001
                notes.append(f"probe {provider[:40]}: {type(error).__name__}")
        elif provider and provider != "ollama":
            try:
                models.refresh_cloud_provider_models(provider)
            except Exception as error:  # noqa: BLE001
                notes.append(f"catalog {provider}: {type(error).__name__}")


class _TaskGate:
    """Refuses a model call that would take the task past its token allowance (E12), before it is
    sent; counts tool calls against the task's step budget."""

    def __init__(self) -> None:
        self.allowance = 0
        self.cloud_cap: int | None = None
        self.spent = 0
        self.cloud_spent = 0
        self.refused = ""

        self.sent: dict[str, int] = {}
        self.estimated = 0

    def reset(self, allowance: int, cloud_cap: int | None) -> None:
        self.allowance, self.cloud_cap = int(allowance), cloud_cap
        self.spent = self.cloud_spent = 0
        self.refused = ""
        self.sent = {}
        self.estimated = 0

    def admit(self, run_id: Any, estimate: int, *, local: bool) -> None:
        """Before a model call: refuse it (raise) when its estimate would pass the task's allowance
        or the month's limit; otherwise remember what it is about to send."""

        from row_bot.agent_budget import ModelCallRefused

        if self.spent + estimate > self.allowance:
            self.refused = "task"
            raise ModelCallRefused("learning evaluation budget: the task's token budget is spent")
        if not local and self.cloud_cap is not None and self.cloud_spent + estimate > self.cloud_cap:
            self.refused = "limit"
            raise ModelCallRefused("learning evaluation budget: the monthly limit would be passed")
        from row_bot.learning.evaluation import _OUTPUT_ALLOWANCE

        self.sent[str(run_id)] = max(0, int(estimate) - _OUTPUT_ALLOWANCE)

    def charge(self, run_id: Any, used: int, *, local: bool) -> None:
        """After a model call: charge what it used. A runtime that reports no usage (llama.cpp's
        server streams none here) is charged the estimate of what was sent, so every arm's budget
        holds the same (E12; L2v)."""

        sent = self.sent.pop(str(run_id), 0)
        if used <= 0 and sent > 0:
            used = sent
            self.estimated += 1
        self.spent += max(0, int(used))
        if not local:
            self.cloud_spent += max(0, int(used))


def _install_gate(gate: _TaskGate) -> None:
    from row_bot.learning.evaluation import estimate_tokens
    from row_bot.learning.spending import is_local_provider
    from row_bot.providers import usage_ledger

    callback = usage_ledger.UsageLedgerCallback
    original_start = callback.on_chat_model_start
    original_end = callback.on_llm_end

    def flatten(messages: Any) -> list[Any]:
        found: list[Any] = []
        for item in messages or []:
            found.extend(item) if isinstance(item, (list, tuple)) else found.append(item)
        return found

    def on_chat_model_start(self: Any, serialized: Any, messages: Any, *, run_id: Any, **kwargs: Any) -> None:
        estimate = estimate_tokens(flatten(messages), (kwargs.get("invocation_params") or {}).get("tools"))
        gate.admit(run_id, estimate, local=is_local_provider(str(getattr(self, "provider_id", "") or "")))
        return original_start(self, serialized, messages, run_id=run_id, **kwargs)

    def on_llm_end(self: Any, response: Any, *, run_id: Any, **kwargs: Any) -> Any:
        try:
            usage = usage_ledger._usage_from_result(response)  # noqa: SLF001 - the ledger's own reading
            used = int(usage.input_tokens or 0) + int(usage.output_tokens or 0)
        except Exception:  # noqa: BLE001
            used = 0
        gate.charge(run_id, used, local=is_local_provider(str(getattr(self, "provider_id", "") or "")))
        return original_end(self, response, run_id=run_id, **kwargs)

    callback.on_chat_model_start = on_chat_model_start
    callback.on_llm_end = on_llm_end
    callback.raise_error = True


def _ledger_rows(after: int) -> tuple[list[dict[str, Any]], int]:
    import sqlite3
    from contextlib import closing

    from row_bot.data_paths import get_usage_db_path

    path = Path(get_usage_db_path(create_parent=False))
    if not path.is_file():
        return [], after
    with closing(sqlite3.connect(str(path), timeout=5)) as connection:
        rows = connection.execute(
            "SELECT id, provider, model, COALESCE(input_tokens,0), COALESCE(output_tokens,0),"
            " COALESCE(cache_read_tokens,0), COALESCE(cache_write_tokens,0), COALESCE(reasoning_tokens,0)"
            " FROM model_usage WHERE id > ? ORDER BY id", (after,)).fetchall()
    found = [{"provider": str(row[1] or "")[:128], "model": str(row[2] or "")[:256], "input": int(row[3]),
              "output": int(row[4]), "cache_read": int(row[5]), "cache_write": int(row[6]), "reasoning": int(row[7])}
             for row in rows]
    return found, (int(rows[-1][0]) if rows else after)


def _consume(events: Iterable[tuple], stop: threading.Event, max_calls: int,
             record: TurnRecord | None = None) -> dict[str, Any]:
    outcome: dict[str, Any] = {"answer": "", "final": "", "status": "unknown", "calls": 0, "errors": []}
    # The reply's last message (the text after its last tool step): progress lines written before a
    # tool call are part of the reply the person sees, not the answer a check reads (L2v).
    last: list[str] = []
    for event in events:
        kind = event[0]
        payload = event[1] if len(event) > 1 else None
        if kind == "token":
            last.append(str(payload or ""))
        elif kind in ("tool_call", "tool_done"):
            last = []
        elif kind == "done":
            outcome["answer"] = str(payload or "")
            outcome["final"] = "".join(last).strip() or outcome["answer"]
            if outcome["status"] == "unknown":
                outcome["status"] = "done"
        if record is not None:
            record.add(kind, outcome["final"] if kind == "done" else payload)
            if kind == "done" and outcome["final"] != outcome["answer"].strip():
                record.add("reply", outcome["answer"])
        if kind == "tool_call":
            outcome["calls"] += 1
            if outcome["calls"] > max_calls:
                outcome["status"] = "steps"
                stop.set()
        elif kind == "terminal":
            outcome["status"] = "steps"
        elif kind == "error":
            if outcome["status"] == "unknown":
                outcome["status"] = "error"
            outcome["errors"].append(str(payload or "")[:160])
        elif kind == "interrupt":
            outcome["status"] = "asked"
            break
    return outcome


def _run_task(message: Mapping[str, Any], spec: Mapping[str, Any], tools: list[str], gate: _TaskGate,
              work: Path) -> dict[str, Any]:
    from row_bot import agent, models, threads
    from row_bot.learning.packs import PackTask
    from row_bot.learning.packs.fake_apps import FakeApps
    from row_bot.learning.packs.verify import Outcome, verify, workspace_files
    from row_bot.learning.spending import is_local_provider
    from row_bot.tools import registry

    task = PackTask.from_dict(dict(message["task"]))
    arm = next(item for item in spec["arms"] if item["name"] == message["arm"])
    budget = dict(message.get("budget") or {})
    started = time.monotonic()
    workspace = Path(tempfile.mkdtemp(prefix="task-", dir=str(work)))
    for name, content in task.files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    _CURRENT.apps = FakeApps(task.apps)
    registry.set_tool_config("filesystem", "workspace_root", str(workspace))
    models.adopt_saved_default(str(arm["model_ref"]))
    names = [*tools, *(APP_TOOLS[app] for app in task.tools if app in APP_TOOLS)]
    max_calls = int(budget.get("max_steps") or task.max_steps) * TOOL_CALL_FACTOR
    gate.reset(int(budget.get("task_tokens") or 150_000), budget.get("cloud_cap"))
    _, baseline = _ledger_rows(0)
    thread_id = f"eval-{uuid.uuid4().hex[:16]}"
    threads.create_thread(f"Evaluation {task.id}", thread_id=thread_id, seed_default_skills=False)
    stop = threading.Event()
    timer = threading.Timer(float(message.get("timeout") or TASK_SECONDS), stop.set)
    timer.daemon = True
    timer.start()
    config = {"configurable": {
        "thread_id": thread_id, "model_override": str(arm["model_ref"]), "runtime_mode": "agent",
        "runtime_surface": "normal_chat", "approval_mode": "full", "generation_id": uuid.uuid4().hex,
    }}
    record = TurnRecord(keep=bool(message.get("trace")))
    try:
        outcome = _consume(agent.stream_agent(task.prompt, names, config, stop_event=stop), stop, max_calls, record)
    except Exception as error:  # noqa: BLE001 - a failed turn is a result
        outcome = {"answer": "", "status": "error", "calls": 0, "errors": [type(error).__name__]}
        record.add("error", type(error).__name__)
    finally:
        timer.cancel()
        try:
            agent._set_active_runtime_context()  # noqa: SLF001 - leave no turn context behind
        except Exception:  # noqa: BLE001
            pass
        # The task's usage rows first: deleting its conversation takes them with it.
        rows, _ = _ledger_rows(baseline)
        _forget_task(thread_id)
    tokens = sum(row["input"] + row["output"] for row in rows)
    local = sum(row["input"] + row["output"] for row in rows if is_local_provider(row["provider"]))
    if gate.estimated and tokens < gate.spent and is_local_provider(_provider_of(str(arm["model_ref"]))):
        tokens = local = gate.spent  # a local runtime that reports no usage: what was sent (estimated)
    status, detail = "failed", ""
    if gate.refused == "limit":
        status, detail = "not_run", "limit"
    elif gate.refused == "task":
        status, detail = "budget", "ran out of its token budget"
    elif stop.is_set() and outcome["status"] != "steps":
        status, detail = "budget", "ran out of time"
    elif outcome["status"] == "steps":
        status, detail = "budget", "ran out of steps"
    elif outcome["status"] == "asked":
        status, detail = "failed", "stopped to ask for approval"
    elif outcome["status"] == "error":
        status, detail = "error", "the model call failed: " + "; ".join(outcome["errors"])[:200]
        logging.getLogger(__name__).info("An evaluation turn failed: %s", "; ".join(outcome["errors"])[:500])
    elif outcome["status"] == "done":
        try:
            verdict = verify(task, Outcome(outcome["final"] or outcome["answer"], workspace_files(workspace),
                                           _CURRENT.apps.state()),
                             backend=str(spec.get("code_backend") or "container"))
            status, detail = ("passed" if verdict.passed else "failed"), verdict.detail
        except Exception as error:  # noqa: BLE001 - the check couldn't run
            status, detail = "error", f"the check could not run ({type(error).__name__})"
    else:
        status, detail = "failed", "the turn ended without an answer"
    shutil.rmtree(workspace, ignore_errors=True)
    case = {"task_id": task.id, "suite": "pack", "kind_of_work": task.kind_of_work, "status": status,
            "check": task.verifier.kind, "tokens": tokens, "local_tokens": local, "steps": int(outcome["calls"]),
            "seconds": round(time.monotonic() - started, 3), "detail": detail[:300],
            "cause": failure_cause(status, detail, record, task_tools=task.tools, check=task.verifier.kind)}
    frame = {"type": "result", "id": message["id"], "case": case, "usage": rows}
    if record.keep:
        frame["trace"] = record.events
    return frame


def worker_main() -> int:
    """The worker process: read its spec, set up its throwaway Row-Bot, then run tasks until told to
    stop (or until its standard input closes: the app is gone)."""

    out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)  # stray prints go to the log, never into the frames
    sys.stdout = sys.stderr
    stdin = sys.stdin.buffer
    lock = threading.Lock()

    def emit(frame: Mapping[str, Any]) -> None:
        with lock:
            out.write((json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            out.flush()

    first = stdin.readline(FRAME_MAX + 1)
    try:
        start = json.loads(first.decode("utf-8")) if first else {}
        spec = dict(start.get("spec") or {}) if start.get("type") == "start" else {}
    except ValueError:
        spec = {}
    data = Path(str(spec.get("data") or ""))
    here = Path(os.environ.get("ROW_BOT_DATA_DIR", ""))
    person = str(spec.get("person_data") or "")
    if not spec or not str(data) or here.resolve() != data.resolve() or any(data.iterdir()) or (
            person and (_inside(data, Path(person)) or _inside(Path(person), data))):
        emit({"type": "failed", "reason": "refused"})
        return 2
    # Secrets live in this process's memory only (before anything can read one).
    from row_bot import secret_store

    secret_store.use_memory_backend()
    secrets: list[str] = []
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Redacting(secrets))
    handler.setLevel(logging.INFO)
    logging.getLogger("row_bot").addHandler(handler)
    logging.getLogger("row_bot").setLevel(logging.INFO)
    notes: list[str] = []
    try:
        _seed_credentials(spec.get("credentials") or (), emit, secrets)
        spec.pop("credentials", None)
        import row_bot.agent  # noqa: F401 - binds the runtime before the gate patches the ledger

        _setup_providers(spec, notes)
        _empty_libraries()
        fake = _register_fake_apps()
        tools = [name for name in safe_tools(spec.get("tools") or ()) if name not in fake]
        gate = _TaskGate()
        _install_gate(gate)
    except Exception as error:  # noqa: BLE001
        logging.getLogger(__name__).warning("The evaluation worker couldn't start (%s)", type(error).__name__)
        emit({"type": "failed", "reason": type(error).__name__})
        return 3
    work = data.parent / "work"
    emit({"type": "ready", "tools": tools, "notes": notes})
    while True:
        raw = stdin.readline(FRAME_MAX + 1)
        if not raw:
            return 0  # the app is gone: so is this worker
        try:
            message = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if not isinstance(message, dict) or message.get("type") == "stop":
            return 0
        if message.get("type") != "task":
            continue
        try:
            emit(_run_task(message, spec, tools, gate, work))
        except Exception as error:  # noqa: BLE001 - one task's failure is its result
            emit({"type": "result", "id": message.get("id"), "case": {
                "task_id": str((message.get("task") or {}).get("id") or ""), "suite": "pack", "kind_of_work": "",
                "status": "error", "check": "", "tokens": 0, "local_tokens": 0, "steps": 0, "seconds": 0.0,
                "detail": f"the task could not run ({type(error).__name__})", "cause": "harness"}, "usage": []})


# ── The runner evaluation uses ──────────────────────────────────────────────


@dataclass
class RuntimeRunner:
    """Evaluation's pack-task runner through the worker (``evaluation.compare``'s ``runner``).

    Before a cloud arm's task it checks the month's allowance (and stops the comparison cleanly at
    the limit); afterwards it charges what the task used, and records the worker's model calls in the
    person's usage ledger as ``learning_test`` (counts only), so the monthly limit sees them.
    """

    worker: EvalWorker
    arms: Mapping[str, WorkerArm]
    record_usage: Callable[[Sequence[Mapping[str, Any]]], None] | None = None
    seconds: float = TASK_SECONDS
    signature: str = "agent-runtime-2"
    unsupported: set[str] = field(default_factory=set)
    #: A developer's look at visible tasks: called with (task, arm name, case, trace). Never set by the
    #: product's comparison, so no hidden task's turn ever leaves the worker (E10).
    on_trace: Callable[[Any, str, Mapping[str, Any], list[dict[str, Any]]], None] | None = None

    def run(self, task: Any, arm: Any, *, budget: Any, meter: Any, arm_left: int | None) -> Any:
        from row_bot.learning.evaluation import CaseResult, LimitReached, estimate_tokens

        worker_arm = self.arms.get(arm.name)
        if worker_arm is None or arm.name in self.unsupported:
            return CaseResult(task.id, "pack", task.kind_of_work, "not_run", "none",
                              detail="this model couldn't be handed to the test runner")
        allowance = budget.task_tokens if arm_left is None else min(budget.task_tokens, max(0, arm_left))
        cloud_cap = None
        if meter is not None and not worker_arm.local:
            estimate = min(allowance, estimate_tokens([{"content": task.prompt}]) * 3)
            if not meter.allows(estimate, local=False):
                raise LimitReached()
            cloud_cap = max(0, int(meter.remaining))
        tracing = self.on_trace is not None and getattr(task, "split", "") == "visible"
        options = {"trace": True} if tracing else {}
        frame = self.worker.run(task.to_dict(), arm.name, budget={
            "task_tokens": allowance, "max_steps": budget.max_steps, "cloud_cap": cloud_cap}, timeout=self.seconds,
            **options)
        case = dict(frame.get("case") or {})
        if tracing and self.on_trace is not None:
            self.on_trace(task, arm.name, case, list(frame.get("trace") or ()))
        usage = [dict(row) for row in frame.get("usage") or () if isinstance(row, dict)]
        if usage and self.record_usage is not None:
            self.record_usage(usage)
        tokens = int(case.get("tokens") or 0)
        local_tokens = int(case.get("local_tokens") or 0)
        if meter is not None:
            meter.charge(tokens - local_tokens, local=False)
            meter.charge(local_tokens, local=True)
        if case.get("status") == "not_run" and case.get("detail") == "limit":
            raise LimitReached()
        status = str(case.get("status") or "error")
        cause = str(case.get("cause") or "")
        return CaseResult(task.id, "pack", task.kind_of_work,
                          status if status in ("passed", "failed", "budget", "error") else "error",  # type: ignore[arg-type]
                          str(case.get("check") or task.verifier.kind), tokens, local_tokens,
                          int(case.get("steps") or 0), float(case.get("seconds") or 0.0),
                          str(case.get("detail") or "")[:300], cause if cause in CAUSES else "")


def ledger_recorder(purpose: str = "learning_test") -> Callable[[Sequence[Mapping[str, Any]]], None]:
    """Write the worker's model calls into the person's usage ledger (counts only)."""

    def record(rows: Sequence[Mapping[str, Any]]) -> None:
        from row_bot.prompt_cache import ModelUsage
        from row_bot.providers.usage_ledger import UsageRecord, record_model_usage

        for row in rows:
            record_model_usage(UsageRecord(
                purpose=purpose, provider=str(row.get("provider") or ""), model=str(row.get("model") or ""),
                usage=ModelUsage(input_tokens=int(row.get("input") or 0), output_tokens=int(row.get("output") or 0),
                                 cache_read_tokens=int(row.get("cache_read") or 0),
                                 cache_write_tokens=int(row.get("cache_write") or 0),
                                 reasoning_tokens=int(row.get("reasoning") or 0)),
            ))

    return record


__all__ = [
    "APP_TOOLS",
    "Credential",
    "EvalWorker",
    "EvalWorkerError",
    "RuntimeRunner",
    "TASK_SECONDS",
    "UNSAFE_TOOLS",
    "WorkerArm",
    "gather",
    "ledger_recorder",
    "person_tools",
    "safe_tools",
    "stop_all",
    "worker_environment",
    "worker_main",
    "write_back",
]
