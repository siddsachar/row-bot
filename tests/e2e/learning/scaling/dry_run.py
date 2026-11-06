"""The study's dry run: every stage end to end with fakes, no network and no graphics card (SC1's gate).

    python -m tests.e2e.learning.scaling.study --dry-run --out <folder> --scratch <folder>

One person (Mara), five training days and two held-out days, subsets of 1, 2 and 3 rows and a largest N beyond the
pool (the whole pool), DAgger rounds of 2 rows, one run per task, eight hidden pack tasks (one pack), and few
resamples. The teacher, the student and every version are one scripted model on loopback (``scripted_model.py``,
served like llama-server through Row-Bot's ``llama_cpp`` endpoint profile, on ports 8130-8139), so every turn is a
real Agent turn, captured and checked as in the study; training is the fake trainer behind the real worker protocol
(``tests.fixtures.learning.FakeEngine``) and a version is "exported" and "served" by fakes that register its own
endpoint. Its numbers mean nothing: it shows each stage runs, resumes and hands its files to the next.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.e2e.learning.scaling.design import Design
from tests.e2e.learning.scripted_model import ALIAS, ScriptedModel
from tests.e2e.learning.seeded.scratch import GpuGate

DESIGN = Design(people=("mara",), training_days=(1, 5), held_out_days=(6, 7), sizes=(1, 2, 3, 99), dagger_rows=2,
                runs=1, resamples=200, permutations=200, min_tasks=1, hidden_tasks=8, hidden_packs=("document_lookups",),
                dry_run=True)
PORTS = range(8130, 8140)
PREFIX = "dry-"


class DryModel(ScriptedModel):
    """The scripted model on the first free port of the study's range."""

    def __init__(self) -> None:
        super().__init__()
        self.server.server_close()
        for port in PORTS:
            try:
                self.server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
                break
            except OSError:
                continue
        else:
            raise OSError("no free port in 8130-8139")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)


class DryKit:
    """The fakes the dry run uses in place of the sign-in, the engine, serving, Ollama and the card."""

    sign_in = None

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.model: DryModel | None = None

    def boot(self, data: Path) -> None:
        """The scripted model, and every dry endpoint this profile knows pointed at it (its port is this process's)."""

        if self.model is not None:
            return
        from row_bot.providers import custom

        self.model = DryModel().__enter__()
        for endpoint in custom.list_custom_endpoints():
            if str(endpoint.get("id") or "").startswith(PREFIX):
                self._endpoint(str(endpoint["id"])[len(PREFIX):])

    def _endpoint(self, name: str) -> str:
        from row_bot.providers import custom
        from row_bot.providers.selection import model_ref

        endpoint = f"{PREFIX}{name}"
        custom.save_custom_endpoint({"id": endpoint, "name": f"Dry run {name}", "base_url": self.model.base_url,
                                     "profile": "llama_cpp", "execution_location": "local", "auth_required": False})
        custom.refresh_custom_endpoint_models(endpoint)
        custom.probe_custom_endpoint(endpoint, ALIAS)
        return model_ref(custom.custom_provider_id(endpoint), ALIAS)

    def teacher(self, out: Path) -> str:
        return self._endpoint("teacher")

    def engine(self, data: Path) -> Any:
        from tests.fixtures.learning import FakeEngine

        # One measured validation loss per pass (the recipe trains three).
        return FakeEngine(pace="free", delay=0, validation_losses=[1.2, 1.1, 1.0])

    def supervisor_options(self) -> dict[str, Any]:
        return {"gpu_free": lambda: True, "memory_free": lambda: True}

    def maker_options(self, data: Path) -> dict[str, Any]:
        from row_bot.learning.training import weights

        folder = data.parent / "dry-weights"
        engine = data.parent / "dry-engine"
        model = weights.QWEN35_9B
        if not folder.is_dir():
            folder.mkdir(parents=True)
            main = next(item.name for item in model.files if item.name.endswith(".safetensors"))
            for name in (main, "tokenizer.json", "chat_template.jinja"):
                (folder / name).write_bytes(name.encode())
            engine.mkdir(parents=True, exist_ok=True)
            (engine / "engine.json").write_text(json.dumps({"packages": {"torch": "dry", "unsloth": "dry"}}),
                                                encoding="utf-8")
        return {"exporter": self._export, "server": self._serve, "weights_folder": lambda: (model, folder, 1),
                "engine_status": lambda: SimpleNamespace(folder=engine, python=None, site=None, llama=None)}

    @staticmethod
    def before_release(run: Path) -> None:
        """The fake trainer saves each pass as ``adapter.bin``; the export copies the PEFT adapter's two files, so
        stand-ins of them go beside each pass (the reported file keeps its checked hash)."""

        from row_bot.learning.training import export

        for folder in sorted((run / "checkpoints").glob("pass-*")):
            for name in export.ADAPTER_FILES:
                (folder / name).write_bytes(name.encode())

    @staticmethod
    def _export(**options: Any) -> Any:
        from row_bot.learning.training import export

        gguf = Path(options["folder"]) / f"{options['name']}-{options['quantization']}.gguf"
        gguf.write_bytes(b"GGUF" * 64)
        digest = hashlib.sha256(gguf.read_bytes()).hexdigest()
        return export.ExportResult(gguf=gguf, quantization=options["quantization"], gguf_bytes=256, gguf_sha256=digest,
                                   adapter=options["adapter"], adapter_bytes=1, adapter_sha256=digest, seconds=0.0)

    def _serve(self, runtime: str, **options: Any) -> Any:
        from row_bot.learning.training import serving

        ref = self._endpoint(f"v{int(options['version'])}")
        return serving.ServeOutcome("ready", runtime, model_ref=ref, runtime_version="dry",
                                    probe={"agent_ready": True}, tool_turn={"ok": True})

    def start(self, data: Path, recipe: Any) -> tuple[str | None, str | None]:
        return self._endpoint("start"), None

    def measure(self, data: Path) -> Any:
        return None  # estimated lengths, as the product counts rows without an engine

    def local_27b(self, design: Design) -> str:
        return self._endpoint("27b")

    def unload(self, arm: str, design: Design) -> None:
        return None

    def gpu(self, label: str) -> GpuGate:
        return GpuGate(None, self.args.lane, label=label, training=lambda: False, quiet=lambda: None,
                       card=lambda: None, after_quiet=lambda: False, say=lambda _text: None)


__all__ = ["DESIGN", "DryKit", "DryModel"]
