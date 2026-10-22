"""Serving a personal model: registering it in a local runtime and checking it is ready (D10, O26, O30, O33).

Where a version runs is the person's choice ("Runs in"):

- **Row-Bot** (the default where it can run, O75): Row-Bot's own llama.cpp server
  (``llama_server.py``), which Row-Bot downloads with the training engine, starts when a request needs
  it and stops when it is idle. The version's GGUF is registered under its name, loaded for the checks
  and unloaded after them. llama.cpp's grammar keeps the model's tool calls well formed (L2v: the same
  GGUF passed 20 of 48 tasks there against 13 in Ollama).
- **Ollama** (automated, and the default where Row-Bot's server has no build): ``ollama create`` from the version's GGUF with a Modelfile
  that carries Ollama's own Qwen3.5 renderer and parser and the context it serves at; the model is
  addressed by its tag (``row-bot-personal-v1:latest``), its capabilities are what Ollama's own
  ``/api/show`` says (the local catalog is refreshed from it, O33), and Row-Bot saves the served
  context and "thinking off" as the model's own settings, so serving matches training (O30, O24).
  Only an Ollama on this computer is used: the files never leave it. After its checks the model is
  unloaded, so it holds no graphics memory until it is used.
- **llama.cpp server**, **LM Studio**, **vLLM** (Linux): Row-Bot shows the file and one command to
  load it (llama-server with ``--jinja --offline --no-webui --host 127.0.0.1`` and reasoning off, no
  server tools; vLLM serves the starting model with the version's adapter), then registers a local
  OpenAI-compatible endpoint with the matching profile once the model answers there.

**Ready** means Row-Bot's own probe says the model is Agent-ready **and** one real Agent turn calls a
tool and answers with its result (O26). Until then the version is not offered anywhere; Learning's
personal models are never listed in the model picker before L2c2 offers a version itself.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import shutil
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

ROW_BOT = "row_bot"
RUNTIMES = (ROW_BOT, "ollama", "lmstudio", "llama_cpp", "vllm")
RUNTIME_NAMES = {ROW_BOT: "Row-Bot", "ollama": "Ollama", "lmstudio": "LM Studio", "llama_cpp": "llama.cpp server",
                 "vllm": "vLLM"}
# Runtimes Row-Bot registers a version in by itself (the others show a command to run).
AUTOMATED = frozenset({ROW_BOT, "ollama"})
# Why Row-Bot's own server couldn't make a version ready, as the version's failure.
SERVER_FAILURES = {"unavailable": "server_unavailable", "not_installed": "server_missing",
                   "load_failed": "load_failed", "unknown_model": "load_failed"}
# Where each manual runtime serves on this computer, and Row-Bot's endpoint profile for it.
PORTS = {"lmstudio": 1234, "llama_cpp": 8181, "vllm": 8000}
PROFILES = {"lmstudio": "lmstudio", "llama_cpp": "llama_cpp", "vllm": "vllm"}
NAME_PREFIX = "row-bot-personal"
# The check turn: a real Agent turn on a throwaway conversation with one harmless tool.
CHECK_PROMPT = "Use the calculator to work out 1234 * 5678, then reply with just the result."
CHECK_ANSWER = "7006652"
CHECK_TOOLS = ("calculator",)
CHECK_SECONDS = 300.0
CREATE_SECONDS = 1200.0

Outcome = Literal["ready", "waiting_for_you", "failed"]


def model_name(version: int) -> str:
    """The name a version runs under ("row-bot-personal-v1")."""

    return f"{NAME_PREFIX}-v{int(version)}"


def profile_tag(root: Path | None = None) -> str:
    """A short tag for this data folder, so two Row-Bot profiles on one computer never share a
    model name in the same Ollama."""

    import hashlib

    from row_bot import learning

    folder = Path(root) if root is not None else learning.data_dir()
    return hashlib.sha256(str(folder.resolve()).lower().encode("utf-8")).hexdigest()[:8]


def ollama_tag(version: int, root: Path | None = None) -> str:
    """The Ollama model a version is ("row-bot-personal-v1:3f2a9c1d"), addressed with its tag (O33)."""

    return f"{model_name(version)}:{profile_tag(root)}"


def endpoint_id(runtime: str) -> str:
    return f"{NAME_PREFIX}-{runtime.replace('_', '-')}"


def row_bot_here(check: Any = None) -> bool:
    """Whether Row-Bot's own server has a build for this computer (an NVIDIA card that can train, on
    Windows or Linux x86-64), installed or not."""

    from row_bot.learning.training import hardware, server_files

    found = check if check is not None else hardware.peek()
    variant = found.variant if found is not None and getattr(found, "can_train", False) else None
    return bool(server_files.plan(variant))


def available_runtimes(check: Any = None) -> tuple[str, ...]:
    """The runtimes this computer can offer: Row-Bot's own server where it has a build, and vLLM on
    Linux (or in WSL)."""

    return tuple(item for item in RUNTIMES if (item != "vllm" or sys.platform.startswith("linux"))
                 and (item != ROW_BOT or row_bot_here(check)))


def effective_runtime(choice: str | None, check: Any = None) -> str:
    """Where a new version runs: the person's choice, with Row-Bot's own server as the default where
    it can run and Ollama where it can't (O75)."""

    if choice in RUNTIMES and choice != ROW_BOT:
        return str(choice)
    return ROW_BOT if row_bot_here(check) else "ollama"


@dataclass(frozen=True)
class ServeOutcome:
    """Where a version stands in a runtime after one attempt to make it ready."""

    state: Outcome
    runtime: str
    model_ref: str | None = None
    # not_installed · not_running · not_local · create_failed · not_loaded · probe_failed · tool_turn_failed;
    # Row-Bot's own server: server_missing · server_unavailable · server_failed · load_failed
    reason: str | None = None
    command: str | None = None
    runtime_version: str | None = None
    probe: Mapping[str, Any] = field(default_factory=dict)
    tool_turn: Mapping[str, Any] = field(default_factory=dict)
    seconds: float = 0.0


class Verifier(Protocol):
    """Row-Bot's own checks of a served model (fakes in tests)."""

    def probe(self, model_ref: str, *, runtime: str, endpoint: str | None = None,
              model_id: str | None = None) -> Mapping[str, Any]: ...

    def tool_turn(self, model_ref: str) -> Mapping[str, Any]: ...


# ── Ollama ──────────────────────────────────────────────────────────────────


def _loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").strip("[]").lower()
    if host in {"localhost", ""}:
        return host == "localhost"
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def modelfile_text(gguf: Path, serve_context: int) -> str:
    """The Modelfile: the GGUF, Ollama's own Qwen3.5 renderer and parser (Qwen3.5's tool calls can't be
    a Go template), and the context the model serves at."""

    return (f"FROM {Path(gguf).resolve()}\n"
            "RENDERER qwen3.5\n"
            "PARSER qwen3.5\n"
            f"PARAMETER num_ctx {int(serve_context)}\n")


class OllamaRuntime:
    """The Ollama on this computer: its HTTP API and its own command line (``ollama create``)."""

    def __init__(self, base_url: str | None = None, *, executable: str | None = None,
                 runner: Callable[..., Any] | None = None, timeout: float = 10.0) -> None:
        self._base = base_url
        self._executable = executable
        self._runner = runner
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        if self._base:
            return self._base.rstrip("/")
        from row_bot.models import _ollama_base_url

        return _ollama_base_url().rstrip("/")

    def executable(self) -> str | None:
        if self._executable:
            return self._executable
        found = shutil.which("ollama")
        if found:
            return found
        candidates = []
        if os.name == "nt":
            local = os.environ.get("LOCALAPPDATA", "")
            if local:
                candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
        else:
            candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama"),
                           Path("/Applications/Ollama.app/Contents/Resources/ollama")]
        return next((str(path) for path in candidates if path.is_file()), None)

    def _json(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        import httpx

        with httpx.Client(trust_env=False, timeout=self.timeout) as client:
            response = client.request(method, f"{self.base_url}{path}", json=dict(body) if body is not None else None)
            response.raise_for_status()
            value = response.json() if response.content else {}
        return value if isinstance(value, dict) else {}

    def version(self) -> str | None:
        try:
            return str(self._json("GET", "/api/version").get("version") or "") or None
        except Exception:
            return None

    def show(self, name: str) -> dict[str, Any]:
        return self._json("POST", "/api/show", {"model": name})

    def create(self, name: str, modelfile: Path) -> bool:
        """``ollama create <name> -f <Modelfile>``: the person's own Ollama copies the GGUF in."""

        executable = self.executable()
        if not executable:
            return False
        from row_bot.learning.training.engine import run_owned

        keep = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR", "HOME",
                "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL", "OLLAMA_MODELS", "XDG_RUNTIME_DIR")
        environment = {key: value for key, value in os.environ.items() if key.upper() in keep}
        environment["OLLAMA_HOST"] = self.base_url
        runner = self._runner or run_owned
        result = runner([executable, "create", name, "-f", str(modelfile)], cwd=Path(modelfile).parent,
                        env=environment, timeout=CREATE_SECONDS, check=lambda: None)
        if getattr(result, "code", 1) != 0:
            logger.warning("Ollama couldn't create the personal model (exit %s)", getattr(result, "code", None))
            return False
        return True

    def loaded(self) -> list[dict[str, Any]] | None:
        """The models Ollama holds in memory now (``/api/ps``: name, graphics memory, expiry), or None
        when it doesn't answer."""

        try:
            items = self._json("GET", "/api/ps").get("models")
        except Exception:
            return None
        found = []
        for item in items or ():
            if isinstance(item, dict) and (item.get("name") or item.get("model")):
                found.append({"name": str(item.get("name") or item.get("model")),
                              "size_vram": int(item.get("size_vram") or 0),
                              "expires_at": str(item.get("expires_at") or "")})
        return found

    def unload(self, name: str) -> bool:
        """Free the graphics memory the model holds now (it loads again when next used)."""

        try:
            self._json("POST", "/api/generate", {"model": name, "keep_alive": 0})
            return True
        except Exception:
            return False

    def delete(self, name: str) -> bool:
        try:
            self._json("DELETE", "/api/delete", {"model": name})
            return True
        except Exception:
            return False


def refresh_ollama_catalog() -> int:
    """Row-Bot's local catalog, from what Ollama itself says each model can do (``/api/show``, O33).
    Returns how many models it lists."""

    from dataclasses import replace

    from row_bot.providers.model_catalog import load_ollama_catalog_rows
    from row_bot.providers.model_catalog_cache import read_model_catalog_cache, write_model_catalog_cache

    rows = [dict(row) for row in load_ollama_catalog_rows()]
    write_model_catalog_cache(replace(read_model_catalog_cache(), ollama_rows=rows, generated_at=time.time(),
                                      reason="personal model"))
    return len(rows)


# ── OpenAI-compatible runtimes the person starts ────────────────────────────


def command_for(runtime: str, *, gguf: Path, version: int, serve_context: int, base_model: Path | None = None,
                adapter: Path | None = None) -> str:
    """The one command that loads the version in ``runtime`` on this computer."""

    name = model_name(version)
    if runtime == "llama_cpp":
        return (f'llama-server -m "{Path(gguf).resolve()}" -a {name} --host 127.0.0.1 --port {PORTS[runtime]} '
                f"-c {int(serve_context)} -ngl 99 --jinja --offline --no-webui --reasoning off")
    if runtime == "lmstudio":
        return (f'lms import "{Path(gguf).resolve()}" && lms load {name} --identifier {name} '
                f"--context-length {int(serve_context)}")
    if runtime == "vllm":
        base = f'"{Path(base_model).resolve()}"' if base_model else "<starting model folder>"
        lora = f'"{Path(adapter).resolve()}"' if adapter else "<adapter folder>"
        return (f"vllm serve {base} --enable-lora --lora-modules {name}={lora} --max-model-len {int(serve_context)} "
                f"--host 127.0.0.1 --port {PORTS[runtime]} --enable-auto-tool-choice --tool-call-parser qwen3_coder")
    raise ValueError(f"no command for {runtime}")


def served_models(base_url: str, *, timeout: float = 5.0) -> list[str] | None:
    """The model ids an OpenAI-compatible server on this computer lists, or None when nothing answers."""

    if not _loopback(base_url):
        return None
    import httpx

    try:
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            response = client.get(f"{base_url.rstrip('/')}/models")
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return None
    items = payload.get("data") if isinstance(payload, dict) else None
    return [str(item.get("id")) for item in items or () if isinstance(item, dict) and item.get("id")]


def register_endpoint(runtime: str, *, serve_context: int) -> str:
    """Save the local OpenAI-compatible endpoint for ``runtime`` (thinking off). Returns its provider id."""

    from row_bot.providers import custom

    identifier = endpoint_id(runtime)
    custom.save_custom_endpoint({
        "id": identifier, "name": f"Personal model ({RUNTIME_NAMES[runtime]})",
        "base_url": f"http://127.0.0.1:{PORTS[runtime]}/v1", "profile": PROFILES[runtime],
        "execution_location": "local", "auth_required": False, "reasoning_mode": "off",
        "manual_capabilities": {"context_window": int(serve_context)},
    })
    custom.refresh_custom_endpoint_models(identifier)
    return custom.custom_provider_id(identifier)


# ── Row-Bot's own checks ────────────────────────────────────────────────────


class RowBotVerifier:
    """The product's checks: Row-Bot's readiness probe, then one real Agent turn with a tool."""

    def probe(self, model_ref: str, *, runtime: str, endpoint: str | None = None,
              model_id: str | None = None) -> Mapping[str, Any]:
        started = time.monotonic()
        if runtime == "ollama":
            from row_bot.providers.readiness import evaluate_agent_readiness

            result = evaluate_agent_readiness(model_ref, probe_ollama_tools=True)
            return {"agent_ready": bool(result.ready), "context": result.context_window,
                    "errors": [str(item)[:200] for item in result.errors[:3]],
                    "seconds": round(time.monotonic() - started, 1)}
        from row_bot.providers import custom

        found = custom.probe_custom_endpoint(str(endpoint), model_id)
        return {"agent_ready": found.get("classification") == "agent_ready", "context": found.get("context_window"),
                "errors": [str(item)[:200] for item in (found.get("errors") or [])[:3]],
                "seconds": round(time.monotonic() - started, 1)}

    def tool_turn(self, model_ref: str) -> Mapping[str, Any]:
        return agent_tool_turn(model_ref)


def _check_events(events: Any) -> Any:
    """The check turn's events up to its first approval question (a version that asks fails it)."""

    for event in events:
        yield event
        if event[0] == "interrupt":
            return


def agent_tool_turn(model_ref: str, *, timeout: float = CHECK_SECONDS) -> dict[str, Any]:
    """One real Agent turn with the model: Row-Bot's own prompt, runtime and the calculator tool, on a
    throwaway conversation that is never saved (no chat appears, Learning records nothing of it) and
    is removed afterwards. Passes when a tool was called and the answer holds its result."""

    from row_bot import agent
    from row_bot import threads
    from row_bot.cancellation import CancellationScope, use_cancellation_scope
    from row_bot.thread_cleanup import delete_thread

    thread_id = f"learning-check-{uuid.uuid4().hex[:12]}"
    config = {"configurable": {
        "thread_id": thread_id, "model_override": model_ref, "runtime_mode": "agent",
        "runtime_surface": "normal_chat", "approval_mode": "approve", "generation_id": uuid.uuid4().hex,
    }}
    # Under a cancellation scope, as a chat: the time limit ends a request stuck in the runtime too.
    stop = threading.Event()
    scope = CancellationScope(stop)
    timer = threading.Timer(timeout, scope.cancel, args=("time_limit",))
    timer.daemon = True
    calls: list[str] = []
    answer, errors = "", []
    started = time.monotonic()
    timer.start()
    try:
        with use_cancellation_scope(scope):
            turn = list(_check_events(agent.stream_agent(CHECK_PROMPT, list(CHECK_TOOLS), config, stop_event=stop)))
        for event, payload in turn:
            if event == "tool_call":
                name = getattr(payload, "runtime_name", "") or getattr(payload, "raw_name", "") or (
                    payload.get("name") if isinstance(payload, Mapping) else str(payload))
                calls.append(str(name)[:64])
            elif event == "done":
                answer = str(payload or "")
            elif event in {"error", "terminal"}:
                errors.append(event)
            elif event == "interrupt":
                errors.append("asked_for_approval")
                break
    except Exception as error:  # noqa: BLE001 - a failed turn is a result
        errors.append(type(error).__name__)
    finally:
        timer.cancel()
        try:
            agent._set_active_runtime_context()  # noqa: SLF001 - leave no turn context behind
        except Exception:
            pass
        try:
            delete_thread(thread_id)
            threads._purge_thread_rows(thread_id)  # noqa: SLF001 - an unsaved conversation's checkpoints
        except Exception:
            logger.debug("Learning couldn't tidy its check conversation", exc_info=True)
    digits = "".join(ch for ch in answer if ch.isdigit())
    passed = bool(calls) and CHECK_ANSWER in digits and not errors
    return {"ok": passed, "tool_calls": len(calls), "tools": calls[:5], "answered": CHECK_ANSWER in digits,
            "errors": errors[:3], "timed_out": stop.is_set(), "seconds": round(time.monotonic() - started, 1)}


# ── Making a version ready ──────────────────────────────────────────────────


def serve(
    runtime: str,
    *,
    version: int,
    gguf: Path,
    serve_context: int,
    folder: Path,
    base_model: Path | None = None,
    adapter: Path | None = None,
    ollama: OllamaRuntime | None = None,
    verifier: Verifier | None = None,
    catalog: Callable[[], int] = refresh_ollama_catalog,
    save_model_settings: Callable[..., None] | None = None,
    register: Callable[..., str] = register_endpoint,
    list_models: Callable[[str], list[str] | None] = served_models,
    root: Path | None = None,
    tag: str | None = None,
    tool_turn: bool = True,
    managed: Any = None,
) -> ServeOutcome:
    """Register ``gguf`` in ``runtime`` and check it is ready (the probe and a real tool turn).
    ``root``: the data folder (its Ollama tag). ``tag`` names the Ollama model instead of the
    version's own name (the untouched starting model a first version is compared with, L2c2), and
    ``tool_turn=False`` checks only Row-Bot's probe (that model never learned the tool bridge).
    ``managed``: Row-Bot's own server (``llama_server.get()`` by default; tests pass a fake)."""

    from row_bot.providers.selection import model_ref as make_ref

    if runtime not in RUNTIMES:
        raise ValueError("unknown runtime")
    if tag is not None and runtime not in AUTOMATED:
        raise ValueError("a named model is served by Row-Bot or in Ollama")
    started = time.monotonic()
    verifier = verifier or RowBotVerifier()
    if runtime == ROW_BOT:
        return _serve_row_bot(tag or model_name(version), gguf=gguf, serve_context=serve_context, root=root,
                              verifier=verifier, tool_turn=tool_turn, managed=managed, started=started)
    served = ""  # the Ollama model this call made (none for the runtimes the person starts)
    if runtime == "ollama":
        ollama = ollama or OllamaRuntime()
        if not _loopback(ollama.base_url):
            return ServeOutcome("failed", runtime, reason="not_local", seconds=time.monotonic() - started)
        found = ollama.version()
        if found is None:
            reason = "not_running" if ollama.executable() else "not_installed"
            return ServeOutcome("failed", runtime, reason=reason, seconds=time.monotonic() - started)
        modelfile = Path(folder) / "Modelfile"
        modelfile.write_text(modelfile_text(gguf, serve_context), encoding="utf-8", newline="\n")
        served = tag or ollama_tag(version, root)
        if not ollama.create(served, modelfile):
            return ServeOutcome("failed", runtime, reason="create_failed", runtime_version=found,
                                seconds=time.monotonic() - started)
        try:
            catalog()
        except Exception:
            logger.warning("Learning couldn't refresh the local model catalog", exc_info=True)
        if save_model_settings is None:
            from row_bot.models import set_local_model_settings as save_model_settings
        save_model_settings(served, context=int(serve_context), reasoning="off")
        reference = make_ref("ollama", served)
        probe = dict(verifier.probe(reference, runtime=runtime, model_id=served))
    else:
        base_url = f"http://127.0.0.1:{PORTS[runtime]}/v1"
        command = command_for(runtime, gguf=gguf, version=version, serve_context=serve_context,
                              base_model=base_model, adapter=adapter)
        listed = list_models(base_url)
        if listed is None:
            return ServeOutcome("waiting_for_you", runtime, reason="not_running", command=command,
                                seconds=time.monotonic() - started)
        wanted = model_name(version).lower()
        model_id = next((item for item in listed if wanted in item.lower()), None)
        if model_id is None:
            return ServeOutcome("waiting_for_you", runtime, reason="not_loaded", command=command,
                                seconds=time.monotonic() - started)
        provider = register(runtime, serve_context=serve_context)
        reference = make_ref(provider, model_id)
        probe = dict(verifier.probe(reference, runtime=runtime, endpoint=provider, model_id=model_id))
        found = None
    turn: dict[str, Any] = {}
    reason = "" if probe.get("agent_ready") else "probe_failed"
    if not reason and tool_turn:
        turn = dict(verifier.tool_turn(reference))
        reason = "" if turn.get("ok") else "tool_turn_failed"
    if served and ollama is not None:
        # The checks loaded it: its graphics memory goes back now (Ollama would hold it for half an
        # hour, and the person's own local model, or training, needs the card).
        ollama.unload(served)
    if reason:
        if served and ollama is not None:  # nothing half-working stays in Ollama (the version's file is kept)
            _take_back(ollama, served, catalog)
        return ServeOutcome("failed", runtime, model_ref=reference, reason=reason, runtime_version=found,
                            probe=probe, tool_turn=turn, seconds=time.monotonic() - started)
    return ServeOutcome("ready", runtime, model_ref=reference, runtime_version=found, probe=probe, tool_turn=turn,
                        seconds=time.monotonic() - started)


def _serve_row_bot(name: str, *, gguf: Path, serve_context: int, root: Path | None, verifier: Verifier,
                   tool_turn: bool, managed: Any, started: float) -> ServeOutcome:
    """Row-Bot's own server: register the GGUF under ``name``, load it, run the checks, unload it.
    A version that fails is taken off the server's list (its file stays)."""

    from row_bot.learning.training import llama_server, lockfile
    from row_bot.providers.custom import custom_provider_id
    from row_bot.providers.selection import model_ref as make_ref

    server = managed if managed is not None else llama_server.get(root)
    files = server.files()
    if not files.installed:
        reason = "server_unavailable" if files.state == "unavailable" else "server_missing"
        return ServeOutcome("failed", ROW_BOT, reason=reason, seconds=time.monotonic() - started)
    server.register(name, gguf, serve_context)
    provider = custom_provider_id(llama_server.ENDPOINT_ID)
    reference = make_ref(provider, name)
    probe: dict[str, Any] = {}
    turn: dict[str, Any] = {}
    reason = ""
    try:
        with server.hold():
            server.load(name)
            probe = dict(verifier.probe(reference, runtime=ROW_BOT, endpoint=provider, model_id=name))
            reason = "" if probe.get("agent_ready") else "probe_failed"
            if not reason and tool_turn:
                turn = dict(verifier.tool_turn(reference))
                reason = "" if turn.get("ok") else "tool_turn_failed"
            # The checks loaded it: its graphics memory goes back now (training or the person's own
            # local model may need the card; it loads again when asked for).
            server.unload(name)
    except llama_server.ServerError as error:
        reason = SERVER_FAILURES.get(error.code, "server_failed")
    if reason:
        try:
            server.unregister(name)  # nothing half-working stays on its list
        except Exception:  # noqa: BLE001
            logger.debug("Learning couldn't take a model off its server's list", exc_info=True)
        return ServeOutcome("failed", ROW_BOT, model_ref=reference, reason=reason, probe=probe, tool_turn=turn,
                            seconds=time.monotonic() - started)
    return ServeOutcome("ready", ROW_BOT, model_ref=reference, runtime_version=lockfile.LLAMA_CPP_RELEASE,
                        probe=probe, tool_turn=turn, seconds=time.monotonic() - started)


def _take_back(ollama: OllamaRuntime, tag: str, catalog: Callable[[], int] | None = None) -> None:
    """Remove ``tag`` from Ollama with its saved settings (and refresh the model list)."""

    ollama.unload(tag)
    ollama.delete(tag)
    try:
        from row_bot.models import clear_local_model_settings

        clear_local_model_settings(tag)
    except Exception:
        logger.debug("Learning couldn't clear a personal model's settings", exc_info=True)
    if catalog is not None:
        try:
            catalog()
        except Exception:
            logger.debug("Learning couldn't refresh the local model catalog", exc_info=True)


def withdraw(runtime: str, version: int, *, ollama: OllamaRuntime | None = None, root: Path | None = None,
             catalog: Callable[[], int] | None = None) -> None:
    """Remove a version from a runtime Row-Bot registered it in (its own server, or Ollama with its
    saved settings)."""

    if runtime == ROW_BOT:
        from row_bot.learning.training import llama_server

        llama_server.get(root).unregister(model_name(version))
        return
    if runtime != "ollama":
        return
    _take_back(ollama or OllamaRuntime(), ollama_tag(version, root), catalog)


def describe(outcome: ServeOutcome) -> dict[str, Any]:
    """The outcome as JSON (no paths: the command is kept separately)."""

    return {"state": outcome.state, "runtime": outcome.runtime, "model_ref": outcome.model_ref,
            "reason": outcome.reason, "runtime_version": outcome.runtime_version, "probe": dict(outcome.probe),
            "tool_turn": dict(outcome.tool_turn), "seconds": round(outcome.seconds, 1)}


__all__ = [
    "AUTOMATED",
    "CHECK_ANSWER",
    "CHECK_PROMPT",
    "NAME_PREFIX",
    "OllamaRuntime",
    "PORTS",
    "RUNTIMES",
    "ROW_BOT",
    "RUNTIME_NAMES",
    "RowBotVerifier",
    "ServeOutcome",
    "Verifier",
    "agent_tool_turn",
    "available_runtimes",
    "command_for",
    "describe",
    "effective_runtime",
    "endpoint_id",
    "model_name",
    "modelfile_text",
    "ollama_tag",
    "refresh_ollama_catalog",
    "register_endpoint",
    "row_bot_here",
    "serve",
    "served_models",
    "withdraw",
]
