"""The untouched starting model, made runnable so a first version can be compared with it (L2c2).

UX §5: a version is compared with the version in use, or, for v1, with **the untouched starting
model**. That model is exported once, as it is (no adapter: llama.cpp's converter and
``llama-quantize`` at the recipe's quantization, through the training engine), into
``learning_models/base/<model id>/``, and served in Ollama as ``row-bot-personal-base:<tag>``: a
reserved name, so it never shows in a model list and never reads as "Answered by your personal
model". Only Row-Bot's probe is checked (the untouched model never learned Row-Bot's tool bridge,
so its tool turn isn't a readiness test, it is what the comparison measures). It is unloaded after
its checks and removed with **Delete version** of the last version, or **Remove engine**.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BASE_FOLDER = "base"
BASE_NAME = "row-bot-personal-base"
STATE_FILE = "state.json"


@dataclass(frozen=True)
class BaseState:
    model_id: str
    phase: str  # ready · failed
    model_ref: str | None = None
    gguf: str | None = None
    gguf_bytes: int = 0
    quantization: str = "Q8_0"
    failure: str | None = None
    at: float = 0.0


def folder(model_id: str, root: Path | None = None) -> Path:
    from row_bot.learning.training import jobs as jobs_module

    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(model_id))[:64] or "model"
    return jobs_module.models_dir(root) / BASE_FOLDER / safe


def tag(root: Path | None = None) -> str:
    from row_bot.learning.training.serving import profile_tag

    return f"{BASE_NAME}:{profile_tag(root)}"


def read(model_id: str, root: Path | None = None) -> BaseState | None:
    try:
        data = json.loads((folder(model_id, root) / STATE_FILE).read_text(encoding="utf-8"))
        return BaseState(**{key: data[key] for key in BaseState.__dataclass_fields__ if key in data})
    except (OSError, ValueError, TypeError):
        return None


def _write(state: BaseState, root: Path | None = None) -> BaseState:
    target = folder(state.model_id, root)
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / f".{STATE_FILE}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(asdict(state), indent=1), encoding="utf-8")
    os.replace(temporary, target / STATE_FILE)
    return state


def ensure(*, root: Path | None = None, engine_status: Any = None, model: Any = None,
           weights_folder: Path | None = None, weights_bytes: int = 0, recipe: Any = None,
           exporter: Callable[..., Any] | None = None, server: Callable[..., Any] | None = None,
           ollama: Any = None, clock: Callable[[], float] = time.time) -> tuple[str | None, str | None]:
    """The untouched starting model's reference in Ollama, made once (export, then the probe);
    ``(None, reason)`` when it can't run here (no engine, no Ollama, the export failed)."""

    from row_bot.learning.training import export, serving

    if model is None or weights_folder is None:
        return None, "engine_missing"
    if recipe is None:
        from row_bot.learning.training import hardware, recipes

        recipe = recipes.for_hardware(hardware.peek()) or recipes.default()
    target = folder(model.id, root)
    state = read(model.id, root)
    ollama = ollama or serving.OllamaRuntime()
    name = tag(root)
    if state is not None and state.phase == "ready" and state.model_ref and state.gguf and \
            (target / state.gguf).is_file():
        try:
            ollama.show(name)
            return state.model_ref, None
        except Exception:
            pass  # gone from Ollama (removed, a restore): serve it again from the file kept here
    gguf = target / state.gguf if state is not None and state.gguf and (target / state.gguf).is_file() else None
    if gguf is None:
        target.mkdir(parents=True, exist_ok=True)
        try:
            result = (exporter or export.export_gguf)(
                engine=engine_status, weights=weights_folder, weights_bytes=weights_bytes, adapter=target,
                folder=target, name=BASE_NAME, quantization=recipe.quantization, untouched=True)
        except export.ExportError as error:
            _write(BaseState(model.id, "failed", failure=error.code, at=float(clock())), root)
            return None, error.code
        gguf = result.gguf
    outcome = (server or serving.serve)("ollama", version=1, gguf=gguf, serve_context=recipe.serve_context,
                                        folder=target, root=root, tag=name, tool_turn=False, ollama=ollama)
    if outcome.state != "ready" or not outcome.model_ref:
        _write(BaseState(model.id, "failed", gguf=gguf.name, failure=outcome.reason or "failed", at=float(clock())),
               root)
        return None, outcome.reason or "failed"
    _write(BaseState(model.id, "ready", model_ref=outcome.model_ref, gguf=gguf.name, gguf_bytes=gguf.stat().st_size,
                     quantization=recipe.quantization, at=float(clock())), root)
    return outcome.model_ref, None


def withdraw(model_id: str, *, root: Path | None = None, ollama: Any = None, remove_files: bool = False) -> None:
    """Take the starting model out of Ollama (and, with ``remove_files``, its file)."""

    import shutil

    from row_bot.learning.training import serving

    serving._take_back(ollama or serving.OllamaRuntime(), tag(root), serving.refresh_ollama_catalog)  # noqa: SLF001
    if remove_files:
        shutil.rmtree(folder(model_id, root), ignore_errors=True)


__all__ = ["BASE_NAME", "BaseState", "ensure", "folder", "read", "tag", "withdraw"]
