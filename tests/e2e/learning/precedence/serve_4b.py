"""Qwen3.5 4B for the precedence study, served the way Row-Bot serves a local Qwen3.5 (Lane B).

Ollama has no Qwen3.5 4B on this machine, but its weights are on disk (L2x's engine folder). This builds a
Q4_K_M GGUF of the untouched weights with **Row-Bot's own export** (the engine's pinned llama.cpp converter and
``llama-quantize``; no download) and registers it in Ollama with **Row-Bot's own Modelfile**
(``serving.modelfile_text``: Ollama's Qwen3.5 renderer and parser) as ``prec-qwen3.5-4b``. Q4_K_M matches the
quantization of Ollama's ``qwen3.5:9b`` and ``qwen3.8:27b``. ``--remove`` takes it out of Ollama again.

Run with ``ROW_BOT_DATA_DIR`` set to the ``prec_export`` profile; the engine is hard-linked from ``--engine-from``
(nothing in it is written).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

NAME = "prec-qwen3.5-4b"
CONTEXT = 65_536


def _link(source: Path, target: Path) -> None:
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if relative.parts and relative.parts[0] == "cache":
            continue
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            continue
        if item.suffix == ".json":
            shutil.copy2(item, destination)
        else:
            try:
                os.link(item, destination)
            except OSError:
                shutil.copy2(item, destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine-from", required=True, help="a data folder whose runtimes/learning holds the engine")
    parser.add_argument("--weights", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args(argv)
    from row_bot.data_paths import get_row_bot_data_dir
    from row_bot.learning.training import engine, export, serving

    ollama = serving.OllamaRuntime()
    if args.remove:
        print("removed" if ollama.delete(NAME) else "not there")
        return 0
    data = get_row_bot_data_dir().resolve()
    if "prec_" not in data.as_posix():
        raise SystemExit("set ROW_BOT_DATA_DIR to a prec_ profile")
    target = data / "runtimes" / "learning"
    if not target.exists():
        _link(Path(args.engine_from) / "runtimes" / "learning", target)
    status = engine.status(data, refresh=True)
    if not status.installed:
        raise SystemExit(f"engine not ready: {status.state} {status.reason}")
    weights = Path(args.weights)
    size = sum(item.stat().st_size for item in weights.glob("*.safetensors"))
    out = Path(args.out)
    gguf = out / f"{NAME}-Q4_K_M.gguf"
    if not gguf.is_file():
        result = export.export_gguf(engine=status, weights=weights, weights_bytes=size, adapter=out / "none",
                                    folder=out, name=NAME, quantization="Q4_K_M", untouched=True,
                                    on_step=lambda step: print(f"export: {step}", flush=True))
        gguf = result.gguf
        print(f"built {gguf} ({result.gguf_bytes:,} bytes, {result.seconds} s)")
    modelfile = out / "Modelfile"
    modelfile.write_text(serving.modelfile_text(gguf, CONTEXT), encoding="utf-8")
    print("created" if ollama.create(NAME, modelfile) else "create failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
