"""Fakes for Run on this computer (E1): pinned files served on loopback, fake builds and models.

``FakeFiles`` serves files the way GitHub's and Hugging Face's CDNs do (Range requests, a cut
connection, a slow body); ``opener`` points a pinned https address's path at it, so tests prove the
pinned URLs are what is asked for, and nothing reaches the network. ``fake_build`` makes a build whose
``llama-server`` is a Python script (``FAKE_SERVER``) run through this interpreter, so it works on
every OS. Nothing here imports Learning.
"""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import threading
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from row_bot.local_runtime import builds, models

FAKE_SERVER = Path(__file__).with_name("local_fake_llama_server.py")


@dataclass
class FakeFiles:
    """Files on loopback, served the way a CDN serves them."""

    files: dict[str, bytes] = field(default_factory=dict)
    # path -> bytes to send before dropping the connection (once each).
    cut_after: dict[str, int] = field(default_factory=dict)
    # path -> an event the server waits for (up to 10 s) before sending the body: a slow download.
    hold: dict[str, threading.Event] = field(default_factory=dict)
    requests: list[dict[str, Any]] = field(default_factory=list)
    base: str = ""

    def add(self, name: str, data: bytes) -> None:
        self.files[f"/{name}"] = data

    def __enter__(self) -> FakeFiles:
        served = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802 - the http.server hook
                path = "/" + unquote(urlsplit(self.path).path).rsplit("/", 1)[-1]
                served.requests.append({"path": path, "range": self.headers.get("Range")})
                data = served.files.get(path)
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = 0
                wanted = self.headers.get("Range")
                if wanted:
                    start = int(wanted.split("=", 1)[1].split("-", 1)[0])
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                held = served.hold.get(path)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                cut = served.cut_after.pop(path, None)
                if held is not None:
                    self.wfile.write(body[:1])
                    self.wfile.flush()
                    held.wait(10)
                    body = body[1:]
                if cut is not None:
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    return
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()

    def asked(self, name: str) -> list[str | None]:
        """The Range header of each request for ``name`` (None: from the start)."""

        return [item["range"] for item in self.requests if item["path"] == f"/{name}"]


def opener(files: FakeFiles):
    """An opener for ``fetch.Fetcher``: the pinned https address's file name, served by ``files``."""

    import httpx

    @contextmanager
    def open_url(url: str, headers: Mapping[str, str]) -> Iterator[Any]:
        assert url.startswith("https://"), url
        name = urlsplit(url).path.rsplit("/", 1)[-1]
        with httpx.Client(trust_env=False, timeout=10) as client:
            with client.stream("GET", f"{files.base}/{name}", headers=dict(headers)) as response:
                response.raise_for_status()
                yield response

    return open_url


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def zip_bytes(entries: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def tar_bytes(entries: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            bundle.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def server_script(*, version_exit: int = 0) -> bytes:
    """A ``llama-server`` stand-in: the fake server's source, or one whose ``--version`` fails."""

    source = FAKE_SERVER.read_text(encoding="utf-8")
    if version_exit:
        source = f"import sys\nsys.exit({version_exit})\n"
    return source.encode("utf-8")


def fake_build(files: FakeFiles, *, version_exit: int = 0, tarball: bool = False,
               runtime: bytes | None = None) -> builds.Build:
    """A build served by ``files``: a Windows-style flat zip, or a Linux-style tarball with one top
    folder and (``runtime``) a CUDA runtime archive unpacked next to it."""

    script = server_script(version_exit=version_exit)
    if tarball:
        archive = tar_bytes({"llama-b12359/llama-server": script, "llama-b12359/LICENSE": b"MIT",
                             "llama-b12359/libggml.so": b"lib"})
        assets = [builds.Asset("llama-fake-bin-ubuntu-x64.tar.gz", len(archive), sha256(archive))]
        files.add(assets[0].name, archive)
        if runtime is not None:
            assets.append(builds.Asset("cudart-llama-fake.tar.gz", len(runtime), sha256(runtime)))
            files.add(assets[1].name, runtime)
        return builds.Build(builds.LINUX_X64, builds.CUDA_13 if runtime is not None else builds.CPU, tuple(assets))
    archive = zip_bytes({"llama-server.exe": script, "LICENSE": b"MIT", "ggml-cpu.dll": b"dll"})
    asset = builds.Asset("llama-fake-bin-win-cpu-x64.zip", len(archive), sha256(archive))
    files.add(asset.name, archive)
    return builds.Build(builds.WINDOWS_X64, builds.CPU, (asset,))


def fake_model(files: FakeFiles, data: bytes = b"GGUF" + b"\x00" * 4096, *, served: bytes | None = None,
               model_id: str = "qwen3.5-4b") -> models.Model:
    """A catalog model whose pinned file is ``data`` (``served``: what the server sends instead)."""

    real = models.get(model_id)
    assert real is not None
    model = models.Model(real.id, real.name, real.repo, real.revision, f"{real.id}-test.gguf", len(data),
                         sha256(data), real.billions, real.kv_bytes_per_token)
    files.add(model.file, served if served is not None else data)
    return model


def python_command(executable: Path) -> list[str]:
    """Runs a fake build's ``llama-server`` (a Python script) through this interpreter."""

    return [sys.executable, str(executable)]


__all__ = ["FAKE_SERVER", "FakeFiles", "fake_build", "fake_model", "opener", "python_command", "sha256",
           "tar_bytes", "zip_bytes"]
