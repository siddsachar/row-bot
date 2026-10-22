"""Fakes for the training engine's download and install and the starting model's (L2b).

- :class:`FakeIndex`: a package index / Hugging Face on loopback that serves files with HTTP
  ranges, can drop a connection part way, ignore ranges, redirect (like Hugging Face's
  ``resolve`` to its CDN), and records every request.
- :func:`loopback_opener`: the downloader's opener for it (the product's opener only speaks
  https to reviewed public hosts; this one maps a pinned https address's path onto the index).
- :func:`make_wheel`: a real wheel (with ``RECORD``) made on the spot.
- :func:`fake_lock`: a ``uv.lock`` naming those wheels, with the two variants.
- :func:`fake_engine_site`: a site-packages folder of tiny stand-ins for the engine's packages,
  for the engine's own check (run by the app's Python).
- ``FAKE_UV``: a stand-in for uv (``learning_fake_uv.py``).

Nothing here reaches the network or a graphics card.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import threading
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

FAKE_UV = Path(__file__).with_name("learning_fake_uv.py").resolve()


def uv_command(log: Path | None = None, fail: str | None = None) -> list[str]:
    """The fake uv, run by the app's own Python (``log``: its runs; ``fail``: a command that fails)."""

    command = [sys.executable, "-I", str(FAKE_UV)]
    if log is not None:
        command += ["--fake-log", str(log)]
    if fail is not None:
        command += ["--fake-fail", fail]
    return command


@dataclass
class FakeIndex:
    """Files on loopback, served the way a CDN serves them."""

    files: dict[str, bytes] = field(default_factory=dict)
    # path -> bytes to send before dropping the connection (once each).
    cut_after: dict[str, int] = field(default_factory=dict)
    # paths whose Range header is ignored (the whole file is sent again with 200).
    ignore_range: set[str] = field(default_factory=set)
    # path -> path it redirects to (302).
    redirects: dict[str, str] = field(default_factory=dict)
    # path -> an event the server waits for (up to 10 s) before sending the body: a slow download.
    hold: dict[str, threading.Event] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)
    base: str = ""

    def add(self, path: str, data: bytes) -> str:
        self.files[path] = data
        return path

    def __enter__(self) -> FakeIndex:
        index = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # quiet
                return

            def do_GET(self) -> None:  # noqa: N802 - the http.server hook
                path = unquote(urlsplit(self.path).path)
                index.requests.append({"path": path, "range": self.headers.get("Range")})
                if path in index.redirects:
                    self.send_response(302)
                    self.send_header("Location", index.base + index.redirects[path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                data = index.files.get(path)
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = 0
                wanted = self.headers.get("Range")
                if wanted and path not in index.ignore_range:
                    start = int(wanted.split("=", 1)[1].split("-", 1)[0])
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(data)}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                held = index.hold.get(path)
                if held is not None:
                    held.wait(10)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                cut = index.cut_after.pop(path, None)
                if cut is not None:
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()

    def ranged(self, path: str) -> list[str | None]:
        return [item["range"] for item in self.requests if item["path"] == path]


def loopback_opener(index: FakeIndex):
    """An opener for ``fetch.Fetcher``: a pinned https address's path, served by ``index``."""

    import httpx

    @contextmanager
    def opener(url: str, headers: Mapping[str, str]) -> Iterator[Any]:
        path = urlsplit(url).path
        with httpx.Client(follow_redirects=True, trust_env=False, timeout=10) as client:
            with client.stream("GET", index.base + path, headers=dict(headers)) as response:
                response.raise_for_status()
                yield response

    return opener


def _record_hash(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_wheel(name: str, version: str, files: Mapping[str, bytes] | None = None,
               tag: str = "py3-none-any") -> tuple[str, bytes]:
    """A real wheel: the files, ``METADATA``, ``WHEEL`` and a ``RECORD`` with sizes and hashes."""

    dist = f"{name.replace('-', '_')}-{version}"
    entries: dict[str, bytes] = dict(files or {f"{name.replace('-', '_')}/__init__.py": b"__version__ = '1'\n"})
    entries[f"{dist}.dist-info/METADATA"] = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n".encode()
    entries[f"{dist}.dist-info/WHEEL"] = f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: {tag}\n".encode()
    record = "".join(f"{path},{_record_hash(data)},{len(data)}\n" for path, data in entries.items())
    record += f"{dist}.dist-info/RECORD,,\n"
    entries[f"{dist}.dist-info/RECORD"] = record.encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries.items():
            info = zipfile.ZipInfo(path, date_time=(2026, 10, 16, 0, 0, 0))
            archive.writestr(info, data)
    return f"{dist}-{tag}.whl", buffer.getvalue()


@dataclass
class FakeWheel:
    name: str
    version: str
    filename: str
    data: bytes
    path: str  # on the index
    marker: str | None = None
    source: dict[str, str] | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    @property
    def url(self) -> str:
        return "https://files.pythonhosted.org" + self.path


def wheel(index: FakeIndex, name: str, version: str, *, files: Mapping[str, bytes] | None = None,
          tag: str = "py3-none-any", marker: str | None = None, source: dict[str, str] | None = None) -> FakeWheel:
    filename, data = make_wheel(name, version, files, tag)
    path = f"/packages/{filename}"
    index.add(path, data)
    return FakeWheel(name, version, filename, data, path, marker, source)


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _dependency(item: FakeWheel, *, exact: bool = False) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": item.name}
    if exact:
        entry["version"] = item.version
        entry["source"] = item.source or {"registry": "https://pypi.org/simple"}
    if item.marker:
        entry["marker"] = item.marker
    return entry


def fake_lock(path: Path, *, common: list[FakeWheel], cu130: list[FakeWheel], cu128: list[FakeWheel],
              uv: FakeWheel, edges: Mapping[str, list[dict[str, Any]]] | None = None, sizes: bool = True) -> Path:
    """A ``uv.lock`` for ``row-bot-learning-engine`` naming the given wheels (``edges``: a
    package's own dependencies, by name)."""

    lines = ['version = 1', 'revision = 5', 'requires-python = "==3.12.*"', ""]

    def package(item: FakeWheel, extra_lines: list[str] = ()) -> None:
        source = item.source or {"registry": "https://pypi.org/simple"}
        lines.extend(["[[package]]", f"name = {_toml_value(item.name)}", f"version = {_toml_value(item.version)}",
                      f"source = {_toml_value(source)}"])
        dependencies = (edges or {}).get(item.name)
        if dependencies:
            lines.append("dependencies = [" + ", ".join(_toml_value(dep) for dep in dependencies) + "]")
        wheel_entry: dict[str, Any] = {"url": item.url, "hash": f"sha256:{item.sha256}"}
        if sizes:
            wheel_entry["size"] = len(item.data)
        lines.append("wheels = [" + _toml_value(wheel_entry) + "]")
        lines.extend(extra_lines)
        lines.append("")

    seen: set[tuple[str, str]] = set()
    for item in [*common, *cu130, *cu128, uv]:
        if (item.name, item.version) in seen:
            continue
        seen.add((item.name, item.version))
        package(item)
    project = "row-bot-learning-engine"
    lines.extend(["[[package]]", f"name = {_toml_value(project)}", 'version = "1.0.0"', 'source = { virtual = "." }',
                  "dependencies = [" + ", ".join(_toml_value(_dependency(item)) for item in common) + "]", "",
                  "[package.optional-dependencies]"])
    for variant, items in (("cu128", cu128), ("cu130", cu130)):
        lines.append(f"{variant} = [" + ", ".join(_toml_value(_dependency(item, exact=True)) for item in items) + "]")
    lines.extend(["", "[package.dev-dependencies]", 'bootstrap = [{ name = "uv" }]', ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── llama.cpp's tools (L2c), as tiny stand-ins on the index ──────────────────

LLAMA_TOP = "llama.cpp-b11669"


def _zip(entries: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries.items():
            archive.writestr(zipfile.ZipInfo(path, date_time=(2026, 10, 16, 0, 0, 0)), data)
    return buffer.getvalue()


def fake_llama_archives(*, extra_source: Mapping[str, bytes] | None = None) -> tuple[bytes, bytes]:
    """(the CPU build, the source archive) of a stand-in llama.cpp release: ``llama-quantize`` and
    the converter's files, plus source the export never needs (left packed)."""

    build = _zip({"llama-quantize.exe": b"quantize", "llama-quantize": b"quantize",
                  "llama-quantize-impl.dll": b"impl", "llama-server.exe": b"server"})
    source = {
        f"{LLAMA_TOP}/convert_hf_to_gguf.py": b"print('convert')\n",
        f"{LLAMA_TOP}/conversion/__init__.py": b"",
        f"{LLAMA_TOP}/gguf-py/gguf/__init__.py": b"",
        f"{LLAMA_TOP}/LICENSE": b"MIT License\n",
        f"{LLAMA_TOP}/ggml/src/ggml.c": b"/* not needed for the export */\n",
    }
    source.update(extra_source or {})
    return build, _zip(source)


def fake_llama(index: FakeIndex, monkeypatch: Any) -> tuple[Any, Any]:
    """Pin llama.cpp's tools to stand-ins on ``index`` for every platform (macOS CI included).
    Returns the two pins (the build, the converter)."""

    from row_bot.learning.training import lockfile

    build, source = fake_llama_archives()
    pins = []
    for name, data in (("llama-b11669-bin-test-cpu.zip", build), ("llama.cpp-b11669-src.zip", source)):
        path = index.add(f"/ggml-org/llama.cpp/releases/download/b11669/{name}", data)
        pins.append(lockfile.LockedWheel("llama.cpp-tools", "b11669", "https://github.com" + path, name,
                                         hashlib.sha256(data).hexdigest(), len(data)))
    monkeypatch.setattr(lockfile, "LLAMA_CPP_TOOLS", {"test": pins[0]})
    monkeypatch.setattr(lockfile, "LLAMA_CPP_SOURCE", pins[1])
    monkeypatch.setattr(lockfile, "tools_platform", lambda platforms=None: "test")
    return pins[0], pins[1]


# ── The engine's packages, as tiny stand-ins (for the engine's own check) ─────

_TORCH = '''
import json, os, sys
_config = json.load(open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "_fake_engine.json")))
__version__ = "2.12.1+cu130"
class version:
    cuda = "13.0"
class _Cuda:
    @staticmethod
    def is_available():
        return _config["cuda"]
    @staticmethod
    def get_device_name(index=0):
        return "Fake GPU"
cuda = _Cuda()
# What a library would see at import: the switches must already be set.
_record = _config.get("seen")
if _record:
    keys = ("HF_HUB_DISABLE_TELEMETRY", "HF_HUB_OFFLINE", "UNSLOTH_DISABLE_STATISTICS", "UNSLOTH_AUTO_INSTALL",
            "USE_HUB_KERNELS", "WANDB_MODE", "DO_NOT_TRACK", "HF_HUB_DISABLE_IMPLICIT_TOKEN",
            "HF_HUB_DISABLE_UPDATE_CHECK", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")
    with open(_record, "w", encoding="utf-8") as handle:
        json.dump({key: os.environ.get(key) for key in keys}, handle)
print("a library printing at import")
'''

_PHONE_HOME = '''
import sys
class _Socket:
    family = 2  # AF_INET
refused = []
# A library that tries to report its use. sys.audit raises the same events a real lookup and
# connection would, so the engine's guard is tested without touching the network.
for event, args in (("socket.getaddrinfo", ("huggingface.co", 443, 0, 0, 0)),
                    ("socket.connect", (_Socket(), ("203.0.113.7", 443)))):
    try:
        sys.audit(event, *args)
    except PermissionError:
        refused.append(event)
REFUSED = refused
__version__ = "5.17.0"
'''


def fake_engine_site(root: Path, *, cuda: bool = True, seen: Path | None = None, phone_home: bool = False,
                     broken: str | None = None) -> Path:
    """A site-packages folder whose packages import like the engine's (``cuda``: whether PyTorch
    finds a graphics card; ``seen``: a file where it writes the switches it saw at import;
    ``phone_home``: one package tries to reach the Hub; ``broken``: that package fails to import)."""

    site = Path(root) / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    (site / "_fake_engine.json").write_text(json.dumps({"cuda": cuda, "seen": str(seen) if seen else None}),
                                            encoding="utf-8")
    modules = {
        "torch": _TORCH,
        "accelerate": "__version__ = '1.15.0'\n",
        "bitsandbytes": "__version__ = '0.50.2'\n",
        "datasets": "__version__ = '4.8.5'\n",
        "peft": "__version__ = '0.21.2'\n",
        "transformers": _PHONE_HOME if phone_home else "__version__ = '5.17.0'\n",
        "trl": "__version__ = '1.15.0'\n",
        "unsloth": "__version__ = '2026.10.6'\n",
    }
    for name, source in modules.items():
        package = site / name
        package.mkdir(exist_ok=True)
        body = "raise ImportError('broken on purpose')\n" if name == broken else source
        (package / "__init__.py").write_text(body, encoding="utf-8")
    return site


# ── A whole download: a fake lock and Hub, and the app's download wired to them ──

GPU_CHECK = {"name": "NVIDIA GeForce RTX 5070 Laptop GPU", "memory_mib": 8151, "driver": "596.08"}
FAKE_WEIGHTS = bytes(range(256)) * 4096 * 2  # 2 MiB
FAKE_LICENCE = b"Apache License\nVersion 2.0\n"


class DownloadWorld:
    """A data folder with Learning on, a fake engine lock and Hub on loopback, and a starting
    model pinned to the fake Hub's files."""

    def __init__(self, root: Path, index: FakeIndex, model: Any) -> None:
        from row_bot.learning.training import hardware

        self.root, self.index, self.model = root, index, model
        self.free = 10**12
        self.gpu = hardware.classify([hardware.Gpu(GPU_CHECK["name"], GPU_CHECK["memory_mib"], GPU_CHECK["driver"])])

    def downloads(self, **overrides: Any) -> Any:
        from row_bot.learning.training import downloads, fetch

        options: dict[str, Any] = {
            "fetcher": lambda: fetch.Fetcher(opener=loopback_opener(self.index), retry_wait=0),
            "installer_options": {"uv": uv_command(), "run_check": False},
            "check_hardware": lambda: self.gpu,
            "peek_hardware": lambda: self.gpu,
            "disk_free": lambda _path: self.free,
        }
        options.update(overrides)
        return downloads.Downloads(self.root, **options)

    @property
    def weights_path(self) -> str:
        return f"/cdn/{self.model.files[1].sha256}"


@contextmanager
def download_world(root: Path, tmp_path: Path, monkeypatch: Any) -> Iterator[DownloadWorld]:
    from row_bot.learning.training import engine, lockfile, weights

    with FakeIndex() as index:
        common = [wheel(index, "transformers", "5.17.0"), wheel(index, "unsloth", "2026.10.6")]
        cu130 = [wheel(index, "torch", "2.12.1+cu130", source={"registry": "https://download.pytorch.org/whl/cu130"})]
        cu128 = [wheel(index, "torch", "2.11.0+cu128", source={"registry": "https://download.pytorch.org/whl/cu128"})]
        lock = fake_lock(tmp_path / "uv.lock", common=common, cu130=cu130, cu128=cu128,
                         uv=wheel(index, "uv", "0.12.22"))
        monkeypatch.setattr(lockfile, "LOCK_FILE", lock)
        fake_llama(index, monkeypatch)
        files = []
        for name, data in (("LICENSE", FAKE_LICENCE), ("model.safetensors", FAKE_WEIGHTS)):
            digest = hashlib.sha256(data).hexdigest()
            index.add(f"/cdn/{digest}", data)
            files.append(weights.WeightFile(name, len(data), digest))
        model = weights.PinnedModel("qwen3.5-2b", "Qwen3.5 2B", "Qwen/Qwen3.5-2B", "a" * 40, "Apache 2.0",
                                    "apache-2.0", tuple(files))
        for item in model.files:
            index.redirects[f"/{model.repo}/resolve/{model.revision}/{item.name}"] = f"/cdn/{item.sha256}"
        monkeypatch.setattr(weights, "MODELS", {model.id: model})
        monkeypatch.setattr(weights, "QWEN35_2B", model)
        engine.forget_status()
        try:
            yield DownloadWorld(Path(root), index, model)
        finally:
            engine.forget_status()


__all__ = [
    "DownloadWorld",
    "FAKE_UV",
    "download_world",
    "FakeIndex",
    "FakeWheel",
    "fake_engine_site",
    "fake_llama",
    "fake_llama_archives",
    "fake_lock",
    "loopback_opener",
    "make_wheel",
    "uv_command",
    "wheel",
]
