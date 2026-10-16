"""Row-Bot Learning: measure the personal-model training tiers on this computer's NVIDIA card.

One command for the larger tiers (12-16, 24 and 32 GB; written for an RTX 5090) that repeats on
this card what L0d measured on the 8 GB RTX 5070 Laptop, through Row-Bot's own code, and writes
one results file to bring back. Run it from a checkout of this branch after ``uv sync``; it
checks out nothing itself and touches nothing outside its own scratch folder (no Row-Bot profile,
nothing installed system-wide). In that folder it:

1. installs the training engine exactly as Row-Bot does (the engine's locked wheels from PyPI and
   the PyTorch index, the pinned uv, uv's CPython 3.12.15; about 2.2 GB on Windows, 3.0 GB on
   Linux), checks every hash, and runs the engine's own check;
2. downloads the candidate starting models at pinned revisions from Hugging Face, after printing
   the total and asking (no token is ever used): Qwen3.5-2B (4.6 GB), Qwen3.5-4B (9.3 GB),
   Qwen3.5-9B (about 19 GB) and Qwen3-8B (Unsloth's 4-bit build, 6.1 GB);
3. measures each model and context the tier could use (8K for the big ones, 16K and 32K): peak
   memory and seconds per step over a few optimizer steps, one fresh process per cell;
4. runs one short real training (the largest model that fits 16K) through Row-Bot's own training
   worker, asks it to pause part way and resumes it from its checkpoint;
5. exports the trained result with llama.cpp's own tools (a merged GGUF, Q8_0 and Q4_K_M), using
   the pinned llama.cpp b11669 release on Windows (``--llama-cpp`` elsewhere);
6. writes ``learning_gpu_check_results.json`` in the scratch folder: the card, driver, tier and
   every measurement. No tokens, user names, paths or host names.

Training, the matrix and the export run offline with every library's usage reporting switched
off and the engine's network guard on (any connection that isn't this computer is refused and
counted in the results).

PowerShell, from the checkout::

    uv sync --locked --all-extras --group test
    uv run python scripts/learning_gpu_check.py

It takes about one and a half to three hours (mostly downloads and the memory matrix), keeps
everything it downloaded in the scratch folder so a second run continues where the first stopped,
and prints where the results file is. Delete the scratch folder to remove everything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
K = 1024
DEFAULT_DATA = Path.home() / "row-bot-gpu-check"
RESULTS = "learning_gpu_check_results.json"

# The pinned llama.cpp release L0d used (b11669): its Windows CUDA build, its runtime and its source.
LLAMA_TAG = "b11669"
LLAMA_WINDOWS = (
    ("llama-b11669-bin-win-cuda-13.4-x64.zip", "3b92d5c064f5032e603e531d3ec8961f3bd6613e5dfad047399a92dec8e95dbc",
     "bin", f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/"),
    ("cudart-llama-bin-win-cuda-13.4-x64.zip", "738f8c251ac22b70c3ae6f83a10cf222725df0395246a2cf58f32bdb85fbe668",
     "bin", f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/"),
    (f"{LLAMA_TAG}.zip", "bf22d4717daf6db1ecff37bbe702bb53fcca2b7ab63a103c6d679aa65d5d932e", "src",
     "https://github.com/ggml-org/llama.cpp/archive/refs/tags/"),
)


def _say(text: str) -> None:
    print(text, flush=True)


# ── The models ───────────────────────────────────────────────────────────────


def pinned_models() -> dict[str, Any]:
    """The candidate starting models: every file pinned (size and SHA-256), or, for those L0d
    didn't download, a pinned revision whose files are checked against the Hub's own fingerprints."""

    from row_bot.learning.training.weights import QWEN35_2B, PinnedModel, WeightFile

    def files(*items: tuple[str, int, str]) -> tuple[WeightFile, ...]:
        return tuple(WeightFile(*item) for item in items)

    q35_4b = PinnedModel(
        "q35-4b", "Qwen3.5 4B", "Qwen/Qwen3.5-4B", "851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a", "Apache 2.0",
        "apache-2.0", files(
            ("LICENSE", 11_544, "bbedc3fda3305820b977265f01b8619d87570a6739de3a5582c3464840f1e57a"),
            ("README.md", 77_661, "1406be1b6b8fd8a6545870da516912804756593628a1d0fb0a7965211e82a7bb"),
            ("chat_template.jinja", 7_756, "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715"),
            ("config.json", 3_161, "ddc63e1c717afa86c865bb5e01313d89d72bb53b97ad4a8a03ba8510c0621670"),
            ("merges.txt", 3_353_259, "a9d356d7bdf1ef4949e3e748e95b8e10ad9d4e2e838eddc38a0a7b6b94d1db8d"),
            ("model.safetensors-00001-of-00002.safetensors", 5_329_398_688,
             "26a93f066e1916adb13453dae5a0c707c0fbc71299ed98779571a907b8e74c61"),
            ("model.safetensors-00002-of-00002.safetensors", 3_990_429_408,
             "cb544bd9bfae93dc59b0f22b292f5933573854a7f9b97835c67060d7d910e188"),
            ("model.safetensors.index.json", 76_196, "cf3f798ee02ba45f9622aa8892a47369ab667d0afbf154ee7c2212de42e6302d"),
            ("preprocessor_config.json", 390, "27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516"),
            ("tokenizer.json", 12_807_982, "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42"),
            ("tokenizer_config.json", 16_710, "316230d6a809701f4db5ea8f8fc862bc3a6f3229c937c174e674ff3ca0a64ac8"),
            ("video_preprocessor_config.json", 385, "7768af27c1fafa9cc9011c1dc20067e03f8915e03b63504550e11d5066986d13"),
            ("vocab.json", 6_722_759, "ce99b4cb2983d118806ce0a8b777a35b093e2000a503ebde25853284c9dfa003"),
        ))
    q3_8b = PinnedModel(
        "q3-8b", "Qwen3 8B (4-bit)", "unsloth/Qwen3-8B-bnb-4bit", "1deaf68f694c40dbce295da300851729d759b21a",
        "Apache 2.0", "apache-2.0", files(
            ("README.md", 16_104, "d01b68ed0d20792615b6c2d2dbd1f3b43eb37228979d997f14cbe1da9c7b0b78"),
            ("added_tokens.json", 707, "c0284b582e14987fbd3d5a2cb2bd139084371ed9acbae488829a1c900833c680"),
            ("chat_template.jinja", 4_673, "96fd16d36fb085260f9eb1e717b2c4e6e8b9e75a5e6504f66c8d6b128d82784d"),
            ("config.json", 1_330, "7966637bd4d5ec4ff6623f4b409a76c4a6550e5a044a4cef828567b002e993ee"),
            ("generation_config.json", 237, "7995f74dd367a291e8a6209c8e8bd44653b3582f43908a5a113551fc18515105"),
            ("merges.txt", 1_671_853, "8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5"),
            ("model.safetensors", 6_073_558_089, "2d7930212c1a43efade360fa2d66cc0188ebda793b08fb4f8ec1143299147381"),
            ("special_tokens_map.json", 614, "b5acd1507cb3a3539e63369cb4a2b675a599cf13afe62282d371518288967797"),
            ("tokenizer.json", 11_422_654, "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"),
            ("tokenizer_config.json", 10_534, "5f95699c6cf42ee1e3ea6c468d6a1ad61fef4aadf5802b1b56fddbf68370a192"),
            ("vocab.json", 2_776_833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
        ))
    q35_2b = PinnedModel("q35-2b", QWEN35_2B.name, QWEN35_2B.repo, QWEN35_2B.revision, QWEN35_2B.licence,
                         QWEN35_2B.licence_id, QWEN35_2B.files)
    return {
        "q35-2b": q35_2b,
        "q35-4b": q35_4b,
        # Pinned revision; its files are listed and checked against the Hub at that revision.
        "q35-9b": ("Qwen/Qwen3.5-9B", "c202236235762e1c871ad0ccb60c8ee5ba337b9a"),
        "q3-8b": q3_8b,
    }


PATTERNS = (".json", ".safetensors", ".jinja", ".txt", ".model")
# Small files the Hub fingerprints only by their git blob id, per model (for models pinned by revision).
SMALL_BLOBS: dict[str, dict[str, str]] = {}


def hub_model(key: str, repo: str, revision: str) -> Any:
    """A model pinned by revision only: its files and fingerprints, read from the Hub at that revision."""

    from row_bot.integrations.safe import fetch
    from row_bot.learning.training.weights import PinnedModel, WeightFile

    raw = fetch(f"https://huggingface.co/api/models/{repo}/revision/{revision}?blobs=true",
                hosts={"huggingface.co"}, max_bytes=8 * 1024 * 1024, timeout=60)
    info = json.loads(raw)
    if info.get("sha") != revision:
        raise SystemExit(f"{repo}: the Hub answered for another revision")
    if info.get("gated") or info.get("private"):
        raise SystemExit(f"{repo} needs an account; this check never uses a token")
    files = []
    small: dict[str, str] = {}
    for item in info.get("siblings") or []:
        name = str(item.get("rfilename") or "")
        if "/" in name or not (name.endswith(PATTERNS) or name.startswith("LICENSE") or name == "README.md"):
            continue
        lfs = item.get("lfs") or {}
        if lfs.get("sha256"):
            files.append(WeightFile(name, int(lfs["size"]), str(lfs["sha256"])))
        else:
            small[name] = str(item.get("blobId") or "")
            files.append(WeightFile(name, int(item.get("size") or 0), "0" * 64))
    licence = str((info.get("cardData") or {}).get("license") or "unknown")
    SMALL_BLOBS[key] = small
    return PinnedModel(key, repo.split("/")[-1], repo, revision, licence, licence, tuple(files))


def download_model(model: Any, root: Path, source: Path | None) -> Path:
    """The model's files in ``<root>/models/<key>/``: copied from ``source`` (an existing snapshot,
    checked) or downloaded (resumable, checked)."""

    from row_bot.integrations.safe import fetch as fetch_bytes
    from row_bot.learning.training import fetch

    folder = root / "models" / model.id
    done = folder / ".complete"
    if done.is_file():
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    small = SMALL_BLOBS.get(model.id, {})
    fetcher = fetch.Fetcher()
    total = sum(item.size for item in model.files)
    _say(f"  {model.repo}@{model.revision[:12]}: {total / 1e9:.1f} GB")
    for item in model.files:
        target = folder / item.name
        if source is not None:
            if not target.exists():
                shutil.copy2(source / item.name, target)
            if item.name not in small and fetch.sha256_file(target) != item.sha256:
                raise SystemExit(f"{item.name} in {model.repo} doesn't match its pin")
            continue
        if item.name in small:  # a small file the Hub fingerprints by its git blob id
            data = fetch_bytes(model.url(item), hosts={"huggingface.co"}, max_bytes=64 * 1024 * 1024, timeout=120,
                               redirects=3)
            blob = hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 - git's own id
            if blob != small[item.name]:
                raise SystemExit(f"{item.name} in {model.repo} doesn't match the Hub's fingerprint")
            target.write_bytes(data)
            continue
        last = [0.0]

        def progress(current: int, name: str = item.name, size: int = item.size) -> None:
            if time.monotonic() - last[0] > 15:
                last[0] = time.monotonic()
                _say(f"    {name}: {current / 1e9:.2f} of {size / 1e9:.2f} GB")

        fetcher.fetch(fetch.Pinned(model.url(item), item.name, item.sha256, item.size), folder, progress=progress)
    done.write_text(json.dumps({"repo": model.repo, "revision": model.revision}), encoding="utf-8")
    return folder


# ── The engine ───────────────────────────────────────────────────────────────


def install_engine(root: Path, variant: str, args: argparse.Namespace, results: dict[str, Any]) -> Any:
    from row_bot.learning.training import engine, lockfile

    found = engine.status(root, refresh=True)
    if found.installed and found.variant == variant:
        results["engine"] = {"variant": variant, "reused": True, "bytes": found.bytes}
        return found
    plan = lockfile.engine_plan(variant)
    last = [0.0]

    def progress(step: Any) -> None:
        if time.monotonic() - last[0] > 15 or step.phase != "engine":
            last[0] = time.monotonic()
            _say(f"  engine: {step.phase} {step.done / 1e9:.2f} of {step.total / 1e9:.2f} GB")

    started = time.monotonic()
    installer = engine.EngineInstaller(
        plan, root=root, uv=[args.uv] if args.uv else None, uv_cache=args.uv_cache, python_source=args.python,
        on_progress=progress)
    found = installer.install()
    results["engine"] = {"variant": variant, "seconds": round(time.monotonic() - started, 1), "bytes": found.bytes,
                         "wheels": len(plan.wheels), "download_bytes": plan.download_bytes(),
                         "from_uv_cache": bool(args.uv_cache)}
    report = engine.engine_check(found.python, found.site, cwd=root / "work" / "check", check=lambda: None)
    results["engine"]["check"] = {key: report.get(key) for key in ("torch", "torch_cuda", "cuda", "packages",
                                                                   "blocked_connections")}
    return found


def engine_run(found: Any, root: Path, mode: str, options: dict[str, str], timeout: float) -> dict[str, Any]:
    """One engine process (``fit`` or ``export``) with Row-Bot's isolated environment; its JSON line."""

    from row_bot.learning.training import engine
    from row_bot.learning.training.worker import TELEMETRY_ENV

    work = root / "work" / f"{mode}-{uuid.uuid4().hex[:6]}"
    env = {**engine._base_environment(work), **engine.engine_variables(root), **TELEMETRY_ENV}  # noqa: SLF001
    argv = [str(found.python), "-I", "-S", str(engine.ENTRY), mode, "--site", str(found.site)]
    for key, value in options.items():
        argv += [f"--{key}", str(value)]
    result = engine.run_owned(argv, cwd=work, env=env, timeout=timeout, check=lambda: None)
    shutil.rmtree(work, ignore_errors=True)
    line = next((item for item in reversed(result.stdout.splitlines()) if item.strip().startswith("{")), "{}")
    return {"code": result.code, **json.loads(line), "stderr_tail": result.stderr[-600:] if result.code else ""}


# ── The tiers ────────────────────────────────────────────────────────────────


def tier_for(memory_mib: int) -> int:
    gib = memory_mib / 1024
    return 8 if gib < 11 else 16 if gib < 20 else 24 if gib < 28 else 32


# (model, bits, contexts): every candidate the larger tiers might use; a cell that doesn't fit
# stops the larger contexts of that model and method.
MATRIX = (
    ("q35-2b", 16, (16 * K, 32 * K)),
    ("q35-4b", 16, (16 * K, 32 * K)),
    ("q35-4b", 4, (16 * K, 32 * K)),
    ("q3-8b", 4, (8 * K, 16 * K, 32 * K)),
    ("q35-9b", 16, (8 * K, 16 * K, 32 * K)),
    ("q35-9b", 4, (8 * K, 16 * K, 32 * K)),
)


def fit_matrix(found: Any, root: Path, folders: dict[str, Path], results: dict[str, Any]) -> None:
    cells = results.setdefault("fit", [])
    for model, bits, contexts in MATRIX:
        if model not in folders:
            continue
        for context in contexts:
            label = f"{model}-{'lora16' if bits == 16 else 'qlora4'}-{context // K}k"
            _say(f"  fit {label}")
            out = root / "work" / f"fit-{label}.json"
            started = time.monotonic()
            engine_run(found, root, "fit", {"model": str(folders[model]), "target-tokens": str(context - 256),
                                             "max-seq": str(context), "bits": str(bits), "steps": "4", "accum": "2",
                                             "out": str(out)}, timeout=40 * 60)
            cell = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {"ran": False, "error": "no result"}
            cell.update({"label": label, "model": model, "bits": bits, "context": context,
                         "wall_seconds": round(time.monotonic() - started, 1)})
            cells.append(cell)
            _say(f"    {'fits' if cell.get('ran') else 'does not fit'}: peak {cell.get('peak_reserved_mib')} MiB, "
                 f"{cell.get('step_seconds_median')} s/step")
            save(root, results)
            if not cell.get("ran"):
                break


# ── One short real training, paused and resumed, through Row-Bot's worker ────


TOOLS = [{"type": "function", "function": {
    "name": "tool_search", "description": "Find a tool by what it does.",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}]


def _rows(count: int = 48, characters: int = 38_000) -> list[dict[str, Any]]:
    """Made-up Row-Bot-like chats with a tool call (about 10K tokens each, like Row-Bot's requests)."""

    filler = ("The team calendar lists each meeting with its room, its owner and the people invited; "
              "moved meetings keep their old time in the notes. ")
    rows = []
    for number in range(count):
        document = filler * max(1, characters // len(filler))
        rows.append({"tools": TOOLS, "messages": [
            {"role": "system", "content": "You are Row-Bot. Use tool_search to find tools."},
            {"role": "user", "content": f"Calendar export {number}:\n{document}\nFind the tool to add a meeting."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": f"call_{number}", "type": "function",
                                                                 "function": {"name": "tool_search",
                                                                              "arguments": "{\"query\": \"calendar\"}"}}]},
            {"role": "tool", "tool_call_id": f"call_{number}", "content": "calendar_create_event"},
            {"role": "assistant", "content": f"I found calendar_create_event for meeting {number}."},
        ]})
    return rows


class Smi(threading.Thread):
    """The card's peak memory in use (MiB), from nvidia-smi every two seconds."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak = 0
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                text = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                      capture_output=True, text=True, timeout=10).stdout
                self.peak = max(self.peak, max(int(line) for line in text.split() if line.strip().isdigit()))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self.stop.wait(2.0)


def short_training(found: Any, root: Path, model_dir: Path, settings: dict[str, Any],
                   results: dict[str, Any]) -> Path | None:
    from row_bot.learning.training import engine, protocol
    from row_bot.learning.training.worker import PROTOCOL_PATH, EngineCommand, TrainingWorker

    run = root / "run"
    shutil.rmtree(run, ignore_errors=True)
    (run / "data").mkdir(parents=True)
    rows = _rows()
    (run / "data" / "examples.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    command = EngineCommand(
        argv=(str(found.python), "-I", "-S", str(engine.ENTRY), "train", "--site", str(found.site), "--protocol",
              str(PROTOCOL_PATH), "--model", str(model_dir)),
        env=engine.engine_variables(root), name="unsloth")
    total, every, pause_at = 12, 4, 5
    record: dict[str, Any] = {"model": settings["model"], "bits": settings["bits"], "context": settings["max_seq_length"],
                              "steps_total": total, "checkpoint_every": every}

    def spec(resume: str | None) -> dict[str, Any]:
        return {"job_id": "gpu_check", "version": 1, "steps_total": total, "checkpoint_every": every,
                "resume_from": resume, "lease": {"dataset": str((run / "data").resolve()), "examples": len(rows)},
                "output": str((run / "checkpoints").resolve()),
                "recipe": {"name": "gpu-check", "settings": {
                    "max_seq_length": settings["max_seq_length"], "bits": settings["bits"],
                    "gradient_accumulation": 4, "report_to": "none"}}}

    def part(resume: str | None, pause: bool) -> dict[str, Any]:
        seen: list[dict[str, Any]] = []
        finished = threading.Event()
        holder: dict[str, Any] = {}

        def on_message(message: dict[str, Any]) -> None:
            seen.append(message)
            if pause and message["type"] == "progress" and message["step"] >= pause_at and "asked" not in holder:
                holder["asked"] = True
                holder["worker"].request_pause()

        def on_exit(code: int | None, violation: str | None) -> None:
            holder["exit"] = (code, violation)
            finished.set()

        worker = TrainingWorker(command, run, spec(resume), on_message=on_message, on_exit=on_exit)
        holder["worker"] = worker
        smi = Smi()
        smi.start()
        started = time.monotonic()
        try:
            worker.start()
        except Exception as error:  # the worker couldn't start: say why, carry on
            smi.stop.set()
            return {"error": getattr(error, "code", type(error).__name__)}
        finished.wait(3 * 60 * 60)
        smi.stop.set()
        steps = [m for m in seen if m["type"] == "progress"]
        return {"messages": [m["type"] for m in seen if m["type"] != "progress"],
                "steps": [steps[0]["step"], steps[-1]["step"]] if steps else [],
                "seconds_per_step": [m["seconds_per_step"] for m in steps][-3:],
                "last": seen[-1] if seen else None, "exit": holder.get("exit"),
                "seconds": round(time.monotonic() - started, 1), "smi_peak_mib": smi.peak}

    _say(f"  training {settings['model']} at {settings['max_seq_length'] // K}K: part one "
         f"(asks it to pause after step {pause_at})")
    first = part(None, pause=True)
    record["part_one"] = first
    save(root, results | {"training": record})
    paused = (first.get("last") or {}).get("type") == "paused"
    step = (first.get("last") or {}).get("step")
    if not paused:
        record["result"] = "did not pause"
        results["training"] = record
        return None
    _say(f"  paused at step {step}; part two resumes from checkpoint-{step}")
    second = part(protocol.checkpoint_name(step), pause=False)
    record["part_two"] = second
    record["result"] = "finished" if (second.get("last") or {}).get("type") == "finished" else "failed"
    out = run / "checkpoints"
    record["adapter_bytes"] = (out / "adapter_model.safetensors").stat().st_size if (
        out / "adapter_model.safetensors").is_file() else None
    results["training"] = record
    if record["result"] != "finished":
        return None
    adapter = root / "adapter"
    shutil.rmtree(adapter, ignore_errors=True)
    adapter.mkdir()
    for name in ("adapter_model.safetensors", "adapter_config.json"):
        shutil.copy2(out / name, adapter / name)
    return adapter


# ── Export ───────────────────────────────────────────────────────────────────


def llama_cpp(root: Path, given: Path | None) -> Path | None:
    from row_bot.learning.training import fetch

    if given is not None:
        return given
    if os.name != "nt":
        return None  # only the Windows build is pinned; pass --llama-cpp elsewhere
    folder = root / "llama.cpp" / LLAMA_TAG
    if (folder / ".complete").is_file():
        return folder
    downloads = root / "llama.cpp" / "downloads"
    for name, digest, into, base in LLAMA_WINDOWS:
        path = downloads / name
        if not path.is_file():
            from row_bot.integrations.safe import fetch as fetch_bytes

            data = fetch_bytes(base + name, hosts={"github.com", "objects.githubusercontent.com",
                                                   "codeload.github.com", "release-assets.githubusercontent.com"},
                               max_bytes=1024 * 1024 * 1024, timeout=120, redirects=3)
            downloads.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if fetch.sha256_file(path) != digest:
            path.unlink()
            raise SystemExit(f"{name} doesn't match its pinned fingerprint")
        with zipfile.ZipFile(path) as archive:
            archive.extractall(folder / into)
    (folder / ".complete").write_text(LLAMA_TAG, encoding="utf-8")
    return folder


# ── Results ──────────────────────────────────────────────────────────────────


def save(root: Path, results: dict[str, Any]) -> Path:
    """Write the results file. It holds measurements only; as a last check, this computer's folder
    names, user name and host name are replaced if any reached it."""

    path = root / RESULTS
    text = json.dumps(results, indent=1, default=str)
    folders = {str(root), str(root.resolve()), str(Path.home()), str(ROOT)}
    for value in sorted(folders, key=len, reverse=True):
        for form in {value, json.dumps(value)[1:-1]}:
            text = text.replace(form, "<private>")
    for name in {getpass_user(), platform.node()} - {""}:
        text = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", "<private>", text, flags=re.IGNORECASE)
    path.write_text(text, encoding="utf-8")
    return path


def getpass_user() -> str:
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name to hide
        return ""


def card() -> dict[str, Any]:
    out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version,compute_cap",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30).stdout
    name, memory, driver, capability = [part.strip() for part in out.strip().splitlines()[0].split(",")]
    return {"name": name, "memory_mib": int(float(memory)), "driver": driver, "compute_capability": capability}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="the scratch folder (never a Row-Bot profile)")
    parser.add_argument("--models", default="",
                        help="comma-separated: q35-2b, q35-4b, q35-9b, q3-8b, or repo@revision for another model "
                             "(default: all four)")
    parser.add_argument("--tier", default="auto", choices=("auto", "8", "16", "24", "32"))
    parser.add_argument("--yes", action="store_true", help="don't ask before downloading")
    parser.add_argument("--skip-matrix", action="store_true")
    parser.add_argument("--skip-training", action="store_true")
    parser.add_argument("--skip-export", action="store_true")
    parser.add_argument("--llama-cpp", type=Path, help="an unpacked llama.cpp b11669 (bin/ and src/) to export with")
    # Reuse what is already on this computer instead of downloading it (developer runs).
    parser.add_argument("--uv", help=argparse.SUPPRESS)
    parser.add_argument("--uv-cache", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--python", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--weights-from", action="append", default=[], metavar="KEY=FOLDER", help=argparse.SUPPRESS)
    args = parser.parse_args()

    root = args.data.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "learning.db").exists() or (root / "threads.db").exists():
        raise SystemExit("That folder is a Row-Bot profile; pick an empty scratch folder")
    os.environ["ROW_BOT_DATA_DIR"] = str(root / "profile")  # Row-Bot's modules never see a real profile
    try:
        previous = json.loads((root / RESULTS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = {}
    sys.path.insert(0, str(ROOT / "src"))
    from row_bot.learning.training import hardware

    gpu = card()
    found = hardware.classify([hardware.Gpu(gpu["name"], gpu["memory_mib"], gpu["driver"])])
    variant = hardware.engine_variant(found.gpu)
    if variant is None:
        raise SystemExit("Update the NVIDIA driver (570 or newer), then run this again")
    tier = tier_for(gpu["memory_mib"]) if args.tier == "auto" else int(args.tier)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    results: dict[str, Any] = {
        "script": "learning_gpu_check", "version": 1, "row_bot_commit": commit,
        "when": time.strftime("%Y-%m-%d %H:%M", time.gmtime()) + " UTC", "gpu": gpu, "tier_gb": tier,
        "variant": variant, "os": f"{platform.system()} {platform.release()}", "machine": platform.machine(),
    }
    available = pinned_models()
    keys = [item.strip() for item in (args.models or "q35-2b,q35-4b,q35-9b,q3-8b").split(",") if item.strip()]
    models: dict[str, Any] = {}
    for key in keys:
        if "@" in key:
            repo, revision = key.split("@", 1)
            models[repo.split("/")[-1].lower()] = hub_model(repo.split("/")[-1].lower(), repo, revision)
        elif isinstance(available.get(key), tuple):
            models[key] = hub_model(key, *available[key])
        elif key in available:
            models[key] = available[key]
        else:
            raise SystemExit(f"Unknown model {key!r}; try {', '.join(available)} or repo@revision")
    sources = dict(item.split("=", 1) for item in args.weights_from)
    from row_bot.learning.training import engine, lockfile

    here = engine.status(root, refresh=True)
    engine_bytes = 0 if here.installed and here.variant == variant else lockfile.engine_plan(variant).download_bytes()
    model_bytes = {key: sum(item.size for item in model.files) for key, model in models.items()}
    fetching = [key for key in models if key not in sources and not (root / "models" / key / ".complete").is_file()]
    llama_bytes = 0.6e9 if os.name == "nt" and not args.llama_cpp and not args.skip_export and not (
        root / "llama.cpp" / LLAMA_TAG / ".complete").is_file() else 0
    _say(f"Card: {gpu['name']} ({round(gpu['memory_mib'] / 1024)} GB), driver {gpu['driver']} -> tier {tier} GB, "
         f"engine {variant}")
    parts = ([f"the training engine {engine_bytes / 1e9:.1f} GB"] if engine_bytes else []) + [
        f"{models[key].repo} {model_bytes[key] / 1e9:.1f} GB" for key in fetching] + (
        [f"llama.cpp {LLAMA_TAG} 0.6 GB"] if llama_bytes else [])
    total = engine_bytes + sum(model_bytes[key] for key in fetching) + llama_bytes
    _say(f"Downloads: {', '.join(parts)}" if parts else "Downloads: none (everything is already here)")
    _say(f"Total about {total / 1e9:.1f} GB into {root}")
    if not args.yes and input("Download and run? [y/N] ").strip().lower() not in ("y", "yes"):
        _say("Nothing downloaded.")
        return 1
    results["models"] = {key: {"repo": model.repo, "revision": model.revision, "bytes": model_bytes[key],
                               "licence": model.licence} for key, model in models.items()}
    save(root, results)
    _say("1. The training engine")
    engine_found = install_engine(root, variant, args, results)
    save(root, results)
    _say("2. The starting models")
    folders: dict[str, Path] = {}
    for key, model in models.items():
        started = time.monotonic()
        folders[key] = download_model(model, root, Path(sources[key]) if key in sources else None)
        results["models"][key]["seconds"] = round(time.monotonic() - started, 1)
        save(root, results)
    if not args.skip_matrix:
        _say("3. Memory and speed, model by context")
        fit_matrix(engine_found, root, folders, results)
    elif previous.get("fit"):
        results["fit"] = previous["fit"]  # measured by an earlier run in this folder
    adapter = None
    trained: dict[str, Any] | None = None
    if not args.skip_training:
        fits = [cell for cell in results.get("fit", []) if cell.get("ran") and cell["context"] == 16 * K]
        bf16 = [cell for cell in fits if cell["bits"] == 16]
        order = ("q35-9b", "q3-8b", "q35-4b", "q35-2b")
        pick = sorted(bf16 or fits, key=lambda cell: order.index(cell["model"]) if cell["model"] in order else 9)
        choice = pick[0] if pick else {"model": next(iter(folders)), "bits": 16, "context": 16 * K}
        trained = {"model": choice["model"], "bits": choice["bits"], "max_seq_length": choice["context"]}
        _say("4. One short training, paused and resumed")
        adapter = short_training(engine_found, root, folders[choice["model"]], trained, results)
        save(root, results)
    if not args.skip_export and adapter is not None and trained is not None:
        _say("5. Export")
        llama = llama_cpp(root, args.llama_cpp)
        if llama is None:
            results["export"] = {"skipped": "no pinned llama.cpp here; pass --llama-cpp"}
        else:
            out = root / "export"
            started = time.monotonic()
            report = engine_run(engine_found, root, "export", {
                "model": str(folders[trained["model"]]), "adapter": str(adapter), "out": str(out),
                "llama": str(llama), "name": f"{trained['model']}-check"}, timeout=2 * 60 * 60)
            export = json.loads((out / "export.json").read_text(encoding="utf-8")) if (out / "export.json").is_file() \
                else {"failed": {"step": "start", "error_type": "no result"}}
            if report.get("code"):
                export["exit_code"] = report["code"]
                _say(f"    the export failed; its last words (not saved):\n{report.get('stderr_tail', '')}")
            export["seconds"] = round(time.monotonic() - started, 1)
            results["export"] = export
        save(root, results)
    path = save(root, results)
    _say(f"Done. Bring back this file: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
