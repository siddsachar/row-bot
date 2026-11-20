"""The local lane on llama.cpp: a ``llama-server`` of the bench's own, from an existing build and GGUF, read-only.

The Learning replication keeps a llama.cpp build and the stock Qwen3.5-9B Q8_0 GGUF in its study folder and
checks their SHA-256 before every serve, so this module never writes there:

- :func:`sha256_of` reads the GGUF to check it against the expected hash first;
- :class:`LlamaServer` runs the build's ``llama-server`` with the bench's own scratch folder as its working
  folder, home and temp, on a free loopback port outside the reserved ones (``local_runtime.server``'s rule),
  ``--offline`` and without its web page, an API key of its own in its environment only, and the replication's
  serving settings (the model's own chat template, one request at a time, the context it names); it stops the
  process by its exact PID;
- :func:`endpoint` is the Row-Bot side: an ordinary local llama.cpp endpoint (the same ``llama_cpp`` profile
  as Row-Bot's own "Run on this computer" endpoint), unmanaged, pointing at that port, saved in the worker's
  scratch data folder.

Its calls are local: :func:`local_billing` makes the cost ledger count them at $0, like Ollama's.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

ENDPOINT_ID = "bench-llamacpp"
PROVIDER_ID = f"custom_openai_{ENDPOINT_ID}"  # custom.custom_provider_id(ENDPOINT_ID)
KEY_ENV = "ROW_BOT_BENCH_LOCAL_KEY"
RESERVED = (8080, 8099, *range(8121, 8140), *range(8151, 8160))


def sha256_of(path: Path, *, chunk: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:  # read-only
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def free_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port not in RESERVED:
            return port


def server_argv(executable: Path, gguf: Path, *, port: int, context: int, alias: str, gpu: bool = True) -> list[str]:
    argv = [str(executable), "--model", str(gguf), "--alias", alias, "--ctx-size", str(int(context)),
            "--jinja", "--parallel", "1", "--host", "127.0.0.1", "--port", str(int(port)), "--offline",
            "--no-webui", "--cors-origins", "localhost"]
    return argv + (["--n-gpu-layers", "999"] if gpu else ["--n-gpu-layers", "0"])


def server_env(scratch: Path, key: str, *, gpu: bool = True) -> dict[str, str]:
    """Nothing of the person's: the system's own variables, then a home, caches and temp in the bench's scratch."""
    keep = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "LANG", "LC_ALL")
    env = {name: value for name, value in os.environ.items() if name.upper() in keep}
    home, temp = scratch / "home", scratch / "home" / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    env.update({"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home), "LOCALAPPDATA": str(home),
                "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp), "XDG_CACHE_HOME": str(temp),
                "LLAMA_CACHE": str(home / "cache"), "LLAMA_API_KEY": key})
    if not gpu:
        env["CUDA_VISIBLE_DEVICES"] = ""
    return env


class LlamaServer:
    def __init__(self, bin_dir: Path, gguf: Path, scratch: Path, *, context: int = 65_536, alias: str = "",
                 gpu: bool = True, start_s: float = 600.0, popen: Callable[..., Any] = subprocess.Popen,
                 health: Callable[[int], bool] | None = None) -> None:
        executable = Path(bin_dir) / ("llama-server.exe" if os.name == "nt" else "llama-server")
        if not executable.is_file():
            raise FileNotFoundError(f"no llama-server in {bin_dir}")
        self.alias = alias or Path(gguf).stem.lower()
        self.key = secrets.token_urlsafe(32)
        self.port = free_port()
        scratch.mkdir(parents=True, exist_ok=True)
        log = (scratch / "llama-server.log").open("ab")
        self.process = popen(server_argv(executable, Path(gguf), port=self.port, context=context, alias=self.alias,
                                         gpu=gpu), cwd=str(scratch), env=server_env(scratch, self.key, gpu=gpu),
                             stdout=log, stderr=subprocess.STDOUT)
        self.pid = int(self.process.pid)
        check = health or self._healthy
        deadline = time.monotonic() + start_s
        while not check(self.port):
            if self.process.poll() is not None or time.monotonic() > deadline:
                self.stop()
                raise RuntimeError(f"llama-server did not start (see {scratch / 'llama-server.log'})")
            time.sleep(1.0)

    @staticmethod
    def _healthy(port: int) -> bool:
        import httpx

        try:
            return httpx.get(f"http://127.0.0.1:{port}/health", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self) -> None:
        """Stop this server only, by its exact PID."""
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=30)


def endpoint(port: int, key: str, *, context: int) -> dict[str, Any]:
    """The worker's endpoint record: Row-Bot's ``llama_cpp`` profile, local, keyed, at the bench's port."""
    return {"id": ENDPOINT_ID, "name": "Bench llama.cpp", "base_url": f"http://127.0.0.1:{int(port)}/v1",
            "profile": "llama_cpp", "execution_location": "local", "auth_required": True, "api_key": key,
            "manual_capabilities": {"context_window": int(context)}}


def save_endpoint(port: int, key: str, *, context: int, model: str = "") -> dict[str, Any]:
    """In the worker (a scratch data folder): save the endpoint and its key (test mode keeps it in memory), then
    probe it for *model* the way the app does when a person adds an endpoint (tool calling, a tool-result round
    trip), so Agent Mode accepts it. Returns the probe's summary."""
    from row_bot import secret_store
    from row_bot.providers import custom

    secret_store.use_memory_backend()  # the key lives in this process only (test mode keeps the keychain closed)
    custom.save_custom_endpoint(endpoint(port, key, context=context))
    if not model:
        return {}
    return custom.custom_probe_summary(custom.probe_custom_endpoint(ENDPOINT_ID, model))


def local_billing() -> Callable[[], None]:
    """Count this endpoint's calls as local ($0, never capped) in the bench ledger, like Ollama's."""
    from .. import ledger

    original = ledger.billing_for

    def billing_for(provider: str) -> str:
        return ledger.LOCAL if str(provider) == PROVIDER_ID else original(provider)

    ledger.billing_for = billing_for

    def undo() -> None:
        ledger.billing_for = original

    return undo
