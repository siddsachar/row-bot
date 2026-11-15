"""The training run inside a Hugging Face job (C1): the same engine, trainer and recipe as on this computer.

This file runs on Hugging Face's machine, not in Row-Bot: standard library only (it installs what it needs),
started by the job's short bootstrap (``remote/training.py`` ``bootstrap_command``) after it has downloaded the
job's private dataset repository to ``/work/in``. That repository holds only what the person agreed to upload:

- ``data/examples.jsonl`` and ``data/validation.jsonl``: the training rows (the serving shape, O71/O96);
- ``code/``: Row-Bot's own engine files (``entry.py``, ``trainer.py``, ``protocol.py``, this file) and the engine's
  locked project (``engine/pyproject.toml``, ``engine/uv.lock``);
- ``job.json``: the plan (the starting model's repository, revision and every file's SHA-256, the recipe's
  settings, the steps, the llama.cpp release's pins, where the results go).

Steps: install the engine from its lock (the CUDA build the card's driver takes), download the starting model
at its pinned revision and check every file, train with the engine's own ``train`` mode (its network guard on,
exactly as on the person's computer), export each pass with the engine's ``export`` mode (llama.cpp's converter
and ``llama-quantize``), then upload the adapters, the GGUFs and a manifest (sizes and SHA-256) to the private
model repository Row-Bot made for this run. Row-Bot reads the job's log lines that start ``ROWBOT `` (one JSON
object each: stages, training progress, the result); nothing else of the run is reported anywhere. Usage
reporting of every library is switched off (``SWITCHES``), as on the person's computer.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

WORK = Path(os.environ.get("ROW_BOT_JOB_WORK", "/work"))
INPUT = WORK / "in"
PREFIX = "ROWBOT "
# The engine's switches (``learning_engine/entry.py``) plus the Hub's, for everything this job runs.
SWITCHES = {
    "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_UPDATE_CHECK": "1", "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "HF_HUB_DISABLE_PROGRESS_BARS": "1", "DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
    "UNSLOTH_DISABLE_STATISTICS": "1", "UNSLOTH_AUTO_INSTALL": "0", "USE_HUB_KERNELS": "NO",
    "WANDB_MODE": "disabled", "WANDB_DISABLED": "true", "TRACKIO_DISABLED": "1", "COMET_MODE": "DISABLED",
    "TOKENIZERS_PARALLELISM": "false", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
    "UNSLOTH_EXPERIMENTAL_HYBRID_PACKING": "0", "UNSLOTH_CE_LOSS_TARGET_GB": "1",
}


def say(kind: str, **fields: object) -> None:
    """One line Row-Bot reads (``ROWBOT {...}``): counts, names and times only."""

    print(PREFIX + json.dumps({"type": kind, "at": round(time.time(), 1), **fields}, sort_keys=True), flush=True)


class JobFailure(RuntimeError):
    def __init__(self, reason: str, stage: str) -> None:
        self.reason = reason
        self.stage = stage
        super().__init__(f"{stage}: {reason}")


def run(argv: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    subprocess.run(argv, check=True, env=env, cwd=str(cwd) if cwd else None)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class CardPeak(threading.Thread):
    """The card's memory in use at its peak (``nvidia-smi``, every 2 s): the recipe's measured need."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak = 0
        self.total = 0
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.wait(2.0):
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                                      "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                     timeout=10).stdout.splitlines()[0]
                used, total = (int(float(item)) for item in out.split(","))
                self.peak, self.total = max(self.peak, used), total
            except Exception:  # noqa: BLE001 - a missed sample is just a missed sample
                continue


def driver_variant() -> tuple[str, str, str]:
    """(engine variant, GPU name, driver): CUDA 13.0 PyTorch with a driver of 580 or newer, else 12.8 (O25)."""

    out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                         capture_output=True, text=True, timeout=30, check=True).stdout.splitlines()[0]
    name, driver = (part.strip() for part in out.split(","))
    major = int(driver.split(".")[0])
    return ("cu130" if major >= 580 else "cu128"), name, driver


def install_engine(plan: dict, variant: str) -> tuple[Path, Path]:
    """The engine from its own lock (``uv sync --locked``): the interpreter and its site-packages."""

    project = WORK / "engine"
    project.mkdir(parents=True, exist_ok=True)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(INPUT / "code" / "engine" / name, project / name)
    run([sys.executable, "-m", "pip", "install", "--quiet", "--no-input", f"uv=={plan['uv']}"])
    venv = WORK / "venv"
    env = {**os.environ, **SWITCHES, "UV_PROJECT_ENVIRONMENT": str(venv), "UV_NO_CONFIG": "1",
           "UV_PYTHON_PREFERENCE": "only-system", "UV_LINK_MODE": "copy"}
    run([sys.executable, "-m", "uv", "sync", "--locked", "--extra", variant, "--no-install-project",
         "--project", str(project), "--python", sys.executable], env=env)
    python = venv / "bin" / "python"
    site = Path(subprocess.run([str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                               capture_output=True, text=True, check=True).stdout.strip())
    return python, site


def download_model(plan: dict, token: str) -> Path:
    """The starting model at its pinned revision; every file checked against its pin before training."""

    from huggingface_hub import snapshot_download

    target = WORK / "model"
    files = plan["model"]["files"]
    snapshot_download(plan["model"]["repo"], revision=plan["model"]["revision"], local_dir=str(target),
                      allow_patterns=[item["name"] for item in files], token=token or None, max_workers=8)
    for item in files:
        path = target / item["name"]
        if not path.is_file() or path.stat().st_size != int(item["size"]):
            raise JobFailure("model_mismatch", "downloading")
        if item.get("sha256") and sha256(path) != item["sha256"]:
            raise JobFailure("model_mismatch", "downloading")
    return target


def fetch_pinned(url: str, digest: str, target: Path) -> Path:
    with urllib.request.urlopen(url, timeout=120) as response, open(target, "wb") as handle:  # noqa: S310
        shutil.copyfileobj(response, handle, 8 * 1024 * 1024)
    if sha256(target) != digest:
        raise JobFailure("tools_mismatch", "setting_up")
    return target


def install_llama(plan: dict) -> Path:
    """llama.cpp's pinned release: its CPU tools (``llama-quantize``) and its source's converter."""

    llama = WORK / "llama"
    downloads = WORK / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    tools = fetch_pinned(plan["llama"]["tools"]["url"], plan["llama"]["tools"]["sha256"], downloads / "tools.tar.gz")
    source = fetch_pinned(plan["llama"]["source"]["url"], plan["llama"]["source"]["sha256"], downloads / "src.zip")
    (llama / "bin").mkdir(parents=True, exist_ok=True)
    with tarfile.open(tools) as archive:
        archive.extractall(llama / "bin", filter="data")
    with zipfile.ZipFile(source) as archive:
        for member in archive.namelist():
            if ".." in Path(member).parts or member.startswith("/"):
                raise JobFailure("tools_mismatch", "setting_up")
        archive.extractall(llama / "src")
    for found in (llama / "bin").rglob("llama-quantize"):
        found.chmod(0o755)
    return llama


def train(plan: dict, python: Path, site: Path, model: Path) -> dict:
    """The engine's ``train`` mode, driven by the same protocol Row-Bot's supervisor speaks."""

    sys.path.insert(0, str(INPUT / "code"))
    import protocol  # Row-Bot's training protocol (standard library only)

    run_dir = WORK / "run"
    (run_dir / "checkpoints").mkdir(parents=True, exist_ok=True)
    spec = {
        "job_id": plan["job_id"], "version": int(plan["version"]), "steps_total": int(plan["steps_total"]),
        "checkpoint_every": int(plan["checkpoint_every"]), "resume_from": None,
        "lease": {"dataset": str(INPUT / "data"), "examples": int(plan["examples"])},
        "output": str(run_dir / "checkpoints"), "recipe": plan["recipe"],
    }
    env = {**os.environ, **SWITCHES, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
           "TRITON_CACHE_DIR": str(WORK / "cache" / "triton"), "TORCHINDUCTOR_CACHE_DIR": str(WORK / "cache" / "inductor"),
           "UNSLOTH_COMPILE_LOCATION": str(WORK / "cache" / "unsloth"), "HF_HOME": str(WORK / "cache" / "hf")}
    env.pop("HF_TOKEN", None)  # training never needs the person's token (and its network is refused)
    argv = [str(python), "-I", "-S", str(INPUT / "code" / "entry.py"), "train", "--site", str(site),
            "--protocol", str(INPUT / "code" / "protocol.py"), "--model", str(model)]
    worker = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, cwd=str(run_dir))
    result: dict = {"passes": None, "artifacts": None}
    started = time.monotonic()
    assert worker.stdin is not None and worker.stdout is not None
    hello = protocol.check_message(protocol.decode(worker.stdout.readline(protocol.MAX_FRAME + 1)))
    if hello["type"] != "hello":
        raise JobFailure("engine_error", "training")
    worker.stdin.write(protocol.encode(protocol.start(spec)))
    worker.stdin.flush()
    while True:
        raw = worker.stdout.readline(protocol.MAX_FRAME + 1)
        if not raw:
            break
        message = protocol.check_message(protocol.decode(raw))
        kind = message["type"]
        if kind == "progress":
            say("progress", step=message["step"], steps_total=message["steps_total"], loss=message["loss"],
                seconds_per_step=message["seconds_per_step"])
        elif kind == "passes":
            result["passes"] = {"losses": message["losses"], "files": message["files"]}
        elif kind == "artifacts":
            result["artifacts"] = {"files": message["files"]}
        elif kind == "error":
            worker.wait(timeout=600)
            raise JobFailure(message["reason"], "training")
        elif kind == "finished":
            break
    code = worker.wait(timeout=600)
    if code != 0:
        raise JobFailure("engine_error", "training")
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def export(plan: dict, python: Path, site: Path, model: Path, llama: Path, adapter: Path | None, name: str) -> Path:
    out = WORK / "export" / name
    argv = [str(python), "-I", "-S", str(INPUT / "code" / "entry.py"), "export", "--site", str(site),
            "--model", str(model), "--adapter", str(adapter) if adapter else "none", "--out", str(out),
            "--llama", str(llama), "--quant", plan["quantization"], "--name", "personal-model"]
    # llama-quantize needs OpenMP's runtime: PyTorch's own copy (libgomp) when the image has none.
    libraries = os.pathsep.join(filter(None, (str(site / "torch" / "lib"), os.environ.get("LD_LIBRARY_PATH", ""))))
    run(argv, env={**os.environ, **SWITCHES, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                   "LD_LIBRARY_PATH": libraries})
    found = out / f"personal-model-{plan['quantization']}.gguf"
    if not found.is_file():
        raise JobFailure("export_failed", "exporting")
    return found


def upload(plan: dict, token: str, files: dict[str, Path], report: dict) -> None:
    """Everything the run produced, with a manifest of sizes and SHA-256 Row-Bot checks each download against."""

    from huggingface_hub import HfApi

    staging = WORK / "outputs"
    staging.mkdir(parents=True, exist_ok=True)
    manifest = {"format": "row-bot-remote-run", "version": 1, "files": []}
    for target, source in files.items():
        destination = staging / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            os.link(source, destination) if source.stat().st_dev == staging.stat().st_dev else shutil.copy2(
                source, destination)
        manifest["files"].append({"name": target, "size": destination.stat().st_size, "sha256": sha256(destination)})
    (staging / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    (staging / "outputs.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    HfApi(token=token).upload_folder(folder_path=str(staging), repo_id=plan["output_repo"], repo_type="model",
                                     commit_message="Row-Bot personal model run")


def main() -> int:
    os.environ.update(SWITCHES)
    token = os.environ.get("HF_TOKEN", "")
    plan = json.loads((INPUT / "job.json").read_text(encoding="utf-8"))
    stage = "setting_up"
    peak = CardPeak()
    peak.start()
    report: dict = {"times": {}}
    begun = time.monotonic()
    try:
        say("stage", stage=stage)
        variant, gpu, driver = driver_variant()
        report.update({"gpu": gpu, "driver": driver, "variant": variant})
        moment = time.monotonic()
        python, site = install_engine(plan, variant)
        llama = install_llama(plan)
        report["times"]["setup_seconds"] = round(time.monotonic() - moment, 1)
        stage = "downloading"
        say("stage", stage=stage)
        moment = time.monotonic()
        model = download_model(plan, token)
        report["times"]["model_seconds"] = round(time.monotonic() - moment, 1)
        stage = "training"
        say("stage", stage=stage, steps_total=int(plan["steps_total"]))
        trained = train(plan, python, site, model)
        report["times"]["train_seconds"] = trained["seconds"]
        report["card_peak_mib"], report["card_mib"] = peak.peak, peak.total
        stage = "exporting"
        files: dict[str, Path] = {}
        checkpoints = WORK / "run" / "checkpoints"
        passes = (trained["passes"] or {}).get("files") or []
        moment = time.monotonic()
        numbers = list(range(len(passes), 0, -1)) if passes else [0]
        for number in numbers:  # the last pass first: the one most likely kept (O119)
            say("stage", stage=stage, part=f"pass-{number}" if number else "adapter")
            adapter = checkpoints / f"pass-{number}" if number else checkpoints
            for item in (passes[number - 1] if number else (trained["artifacts"] or {}).get("files") or []):
                files[f"adapters/{'pass-' + str(number) if number else 'final'}/{item['name']}"] = adapter / item["name"]
            files[f"gguf/{'pass-' + str(number) if number else 'final'}.gguf"] = export(
                plan, python, site, model, llama, adapter, f"pass-{number}")
        if plan.get("base_gguf"):
            say("stage", stage=stage, part="base")
            files["gguf/base.gguf"] = export(plan, python, site, model, llama, None, "base")
        report["times"]["export_seconds"] = round(time.monotonic() - moment, 1)
        stage = "uploading"
        say("stage", stage=stage)
        moment = time.monotonic()
        report["passes"] = trained["passes"]
        report["artifacts"] = trained["artifacts"]
        report["times"]["total_seconds"] = round(time.monotonic() - begun, 1)
        upload(plan, token, files, report)
        report["times"]["upload_seconds"] = round(time.monotonic() - moment, 1)
        say("done", times=report["times"], card_peak_mib=peak.peak, card_mib=peak.total)
        return 0
    except JobFailure as failure:
        say("error", reason=failure.reason, stage=failure.stage)
        return 1
    except subprocess.CalledProcessError:
        say("error", reason="step_failed", stage=stage)
        return 1
    except Exception as error:  # noqa: BLE001 - every failure ends as one plain line
        say("error", reason=type(error).__name__[:64], stage=stage)
        return 1
    finally:
        peak.stop.set()


if __name__ == "__main__":
    raise SystemExit(main())
