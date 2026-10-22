"""The training engine's trainer: LoRA SFT with Unsloth, TRL and PEFT, driven by Row-Bot's
training protocol (``learning/training/protocol.py``, L2a). Runs inside the engine's own
interpreter (``entry.py``); never imports Row-Bot.

``serve`` is the worker: it says ``hello``, reads the job spec, trains on the job's own lease
(``examples.jsonl``: one chat per line, ``{"messages": [...], "tools": [...]}`` in OpenAI's
format, as Row-Bot sends requests), reports every optimizer step, writes a checkpoint every
``checkpoint_every`` steps (``checkpoint-<step>``, with Row-Bot's manifest written last), pauses
at the next step when asked (it saves first), continues from a checkpoint, and ends with the
adapter's files. Errors are reported as a reason class, never the engine's own text.

The recipe is L0d's certified 8 GB one (O23): bf16 LoRA (r 16, alpha 16, dropout 0) on the
attention and MLP projections, learning rate 2e-4 (linear, 2 warmup steps), adamw_8bit, batch 1
x accumulation 4, Unsloth's gradient checkpointing, PyTorch held to 95% of the card (W8), loss
only on the assistant's turns. A recipe's settings can change these within limits.

``fit`` and ``export`` are for ``scripts/learning_gpu_check.py``: a few steps on rows of one
length to measure memory and speed, and a merged GGUF made with llama.cpp's own tools (never
Unsloth's GGUF path, which clones and builds).

Heavy libraries are imported inside functions, so tests can load this file and drive the
protocol with a fake backend.
"""

from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import math
import os
import signal
import statistics
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

ASSISTANT = "<|im_start|>assistant\n"
END = "<|im_end|>"
EMPTY_THINK = "<think>\n\n</think>\n\n"
TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")
DEFAULTS: dict[str, Any] = {
    "max_seq_length": 16384,
    "bits": 16,
    "rank": 16,
    "alpha": 16,
    "learning_rate": 2e-4,
    "batch_size": 1,
    "gradient_accumulation": 4,
    "warmup_steps": 2,
    "weight_decay": 0.0,
    "vram_cap": 0.95,
    "seed": 3407,
}
LIMITS: dict[str, tuple[float, float]] = {
    "max_seq_length": (512, 131072),
    "bits": (4, 16),
    "rank": (1, 256),
    "alpha": (1, 512),
    "learning_rate": (1e-7, 1e-2),
    "batch_size": (1, 64),
    "gradient_accumulation": (1, 256),
    "warmup_steps": (0, 10000),
    "weight_decay": (0.0, 1.0),
    # The share of the card training may use. As low as a fifth, so a large card can stand in for a
    # smaller tier's (an 8 GB check on a 32 GB card caps at 0.2375, O77).
    "vram_cap": (0.2, 0.99),
    "seed": (0, 2**31 - 1),
}


class TrainingFailure(Exception):
    """A failure with a protocol reason class (``out_of_gpu_memory``, ``out_of_disk``,
    ``cannot_resume``, ``data_unreadable``, ``engine_error``)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def settings_from(recipe_settings: dict[str, Any]) -> dict[str, Any]:
    """The recipe's settings over the defaults, kept within their limits (unknown names ignored)."""

    merged = dict(DEFAULTS)
    for key, (low, high) in LIMITS.items():
        value = recipe_settings.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(float(value)) or not low <= value <= high:
            continue
        merged[key] = type(DEFAULTS[key])(value)
    if merged["bits"] not in (4, 16):
        merged["bits"] = 16
    return merged


def classify(error: BaseException) -> str:
    """The protocol's reason class for an exception (never the engine's text)."""

    name = type(error).__name__
    text = str(error)
    if isinstance(error, TrainingFailure):
        return error.reason
    if name == "OutOfMemoryError" or "CUDA out of memory" in text or "out of memory" in text.lower():
        return "out_of_gpu_memory"
    if isinstance(error, OSError) and (error.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1))
                                       or getattr(error, "winerror", None) in (39, 112)):
        return "out_of_disk"
    return "engine_error"


# ── The data ────────────────────────────────────────────────────────────────


def read_rows(path: Path, limit: int = 1_000_000) -> list[dict[str, Any]]:
    """The lease's chats; each must be an object with a list of messages."""

    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("messages"), list):
                    raise TrainingFailure("data_unreadable")
                rows.append(row)
                if len(rows) > limit:
                    raise TrainingFailure("data_unreadable")
    except (OSError, ValueError) as error:
        raise TrainingFailure("data_unreadable") from error
    if not rows:
        raise TrainingFailure("data_unreadable")
    return rows


def template_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI's wire format → what the chat template expects: tool-call arguments as objects
    (and no ``weight``: that marks training targets, not content)."""

    out = []
    for message in messages:
        message = dict(message)
        message.pop("weight", None)
        if message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                call = json.loads(json.dumps(call))
                function = call.get("function") or {}
                if isinstance(function.get("arguments"), str):
                    try:
                        function["arguments"] = json.loads(function["arguments"] or "{}")
                    except ValueError:
                        function["arguments"] = {}
                calls.append(call)
            message["tool_calls"] = calls
        if message.get("content") is None:
            message["content"] = ""
        out.append(message)
    return out


def assistant_spans(text: str) -> list[tuple[int, int]]:
    """Character spans the loss is computed on: each assistant turn's answer, through its end
    marker, after the empty thinking block (personal models are non-thinking, O24)."""

    spans = []
    position = 0
    while True:
        index = text.find(ASSISTANT, position)
        if index < 0:
            break
        begin = index + len(ASSISTANT)
        if text.startswith(EMPTY_THINK, begin):
            begin += len(EMPTY_THINK)
        end = text.find(END, begin)
        if end < 0:
            break
        spans.append((begin, end + len(END)))
        position = end + len(END)
    return spans


def target_spans(text: str, messages: list[dict[str, Any]]) -> list[tuple[int, int]] | None:
    """The spans of the assistant messages that are targets. Messages marked ``"weight": 0`` (an
    earlier turn's reply, kept as context) carry no loss; None when the rendered turns can't be
    matched to the messages, so the row is left out rather than trained on the wrong text."""

    spans = assistant_spans(text)
    weights = [message.get("weight", 1) for message in messages if message.get("role") == "assistant"]
    if all(weight for weight in weights):
        return spans
    if len(spans) != len(weights):
        return None
    return [span for span, weight in zip(spans, weights) if weight]


def _render(tokenizer: Any, row: dict[str, Any]) -> tuple[str, list[tuple[int, int]] | None]:
    text = tokenizer.apply_chat_template(template_messages(row["messages"]), tools=row.get("tools") or None,
                                         tokenize=False, add_generation_prompt=False)
    return text, target_spans(text, row["messages"])


def render_and_mask(tokenizer: Any, row: dict[str, Any], max_seq: int) -> dict[str, Any] | None:
    """One chat → token ids and labels (-100 outside the target assistant turns); None when it is
    too long, has no target, or can't be rendered with the model's chat template."""

    try:
        text, spans = _render(tokenizer, row)
    except Exception:  # noqa: BLE001 - a row the template refuses is left out, never trained on
        return None
    if spans is None:
        return None
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    ids, offsets = encoded["input_ids"], encoded["offset_mapping"]
    if len(ids) > max_seq:
        return None
    labels = []
    for token, (start, stop) in zip(ids, offsets):
        inside = any(start >= low and stop <= high and stop > start for low, high in spans)
        labels.append(token if inside else -100)
    if all(label == -100 for label in labels):
        return None
    return {"input_ids": ids, "labels": labels}


def measure_row(tokenizer: Any, row: dict[str, Any]) -> tuple[int, int]:
    """(tokens, target tokens) of one row as training renders it; (-1, 0) when the chat template
    refuses it."""

    try:
        text, spans = _render(tokenizer, row)
    except Exception:  # noqa: BLE001 - reported to Row-Bot as unrenderable
        return -1, 0
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    if spans is None:
        return len(encoded["input_ids"]), 0
    targets = sum(1 for start, stop in encoded["offset_mapping"]
                  if stop > start and any(start >= low and stop <= high for low, high in spans))
    return len(encoded["input_ids"]), targets


# ── The backend (Unsloth + TRL) ─────────────────────────────────────────────


def _cap_memory(torch: Any, share: float) -> None:
    # W8: Windows can spill CUDA allocations into shared system memory instead of failing;
    # held under the card's size, "fits" means fits in the card.
    torch.cuda.set_per_process_memory_fraction(float(share), 0)


def _sdpa_patch(torch: Any) -> bool:
    """W1: without flash attention (the Windows wheel), expand K/V heads so the memory-efficient
    SDPA kernel runs instead of the math fallback (OOM at 11.6K tokens for Qwen3.5)."""

    if torch.backends.cuda.is_flash_attention_available():
        return False
    import transformers.integrations.sdpa_attention as sdpa_attention

    sdpa_attention.use_gqa_in_sdpa = lambda *args, **kwargs: False
    try:
        import unsloth.utils.attention_dispatch as dispatch

        dispatch.SDPA_HAS_GQA = False
        dispatch._sdpa_flash_takes_gqa = lambda *args, **kwargs: False
    except Exception:  # an Unsloth without its own dispatch: transformers' patch is enough
        pass
    return True


def load_model(model_dir: str, settings: dict[str, Any]) -> tuple[Any, Any, Any]:
    """The starting model with LoRA adapters, from its local folder only."""

    import torch

    if not torch.cuda.is_available():
        raise TrainingFailure("engine_error")
    _cap_memory(torch, settings["vram_cap"])
    cache = os.environ.get("TORCHINDUCTOR_CACHE_DIR")
    from unsloth import FastLanguageModel  # after the switches, the guard and the cap

    if cache:  # W7: Unsloth drops this at import; keep Inductor's cache in Row-Bot's folder
        os.environ["TORCHINDUCTOR_CACHE_DIR"] = cache
    _sdpa_patch(torch)
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_dir, max_seq_length=settings["max_seq_length"], load_in_4bit=settings["bits"] == 4,
        load_in_16bit=settings["bits"] == 16, full_finetuning=False, local_files_only=True, dtype=None,
    )
    model = FastLanguageModel.get_peft_model(
        model, r=settings["rank"], lora_alpha=settings["alpha"], lora_dropout=0, bias="none",
        target_modules=list(TARGETS), use_gradient_checkpointing="unsloth", random_state=settings["seed"],
        max_seq_length=settings["max_seq_length"],
    )
    text_tokenizer = getattr(tokenizer, "tokenizer", tokenizer)  # a processor for Qwen3.5's VLM path
    return torch, model, text_tokenizer


def encode_rows(tokenizer: Any, rows: list[dict[str, Any]], max_seq: int) -> list[dict[str, Any]]:
    encoded = [item for item in (render_and_mask(tokenizer, row, max_seq) for row in rows) if item is not None]
    if not encoded:
        raise TrainingFailure("data_unreadable")
    return encoded


class UnslothBackend:
    """Trains for real. ``reporter`` gets each step, checkpoint and the pause point."""

    name = "unsloth"

    def __init__(self, model_dir: str) -> None:
        self.model_dir = model_dir

    def run(self, spec: dict[str, Any], rows: list[dict[str, Any]], reporter: "Reporter") -> list[dict[str, Any]]:
        settings = settings_from(spec["recipe"]["settings"])
        output = Path(spec["output"])
        torch, model, tokenizer = load_model(self.model_dir, settings)
        encoded = encode_rows(tokenizer, rows, settings["max_seq_length"])
        from datasets import Dataset
        from transformers import TrainerCallback
        from trl import SFTConfig, SFTTrainer

        dataset = Dataset.from_list(encoded)
        protocol = reporter.protocol

        class Bridge(TrainerCallback):
            """HF's order within a step: on_step_end, then on_log (this step's loss), then on_save.
            A step is reported once its loss is known (or before its checkpoint, or at the end)."""

            def __init__(self) -> None:
                self.started_at: float | None = None
                self.pending: tuple[int, float] | None = None

            def flush(self, loss: float | None = None) -> None:
                if self.pending is not None:
                    step, seconds = self.pending
                    self.pending = None
                    reporter.step(step, loss, seconds)

            def on_step_begin(self, args, state, control, **kwargs):
                if self.started_at is None:
                    self.started_at = time.monotonic()

            def on_step_end(self, args, state, control, **kwargs):
                now = time.monotonic()
                self.flush()
                self.pending = (state.global_step, now - (self.started_at or now))
                self.started_at = now
                if reporter.pause_requested():
                    control.should_save = True
                    control.should_training_stop = True

            def on_log(self, args, state, control, logs=None, **kwargs):
                if logs and "loss" in logs:
                    value = float(logs["loss"])
                    self.flush(value if math.isfinite(value) else None)

            def on_save(self, args, state, control, **kwargs):
                self.flush()
                folder = output / protocol.checkpoint_name(state.global_step)
                if folder.is_dir():
                    reporter.checkpoint(state.global_step, folder)

            def on_train_end(self, args, state, control, **kwargs):
                self.flush()

        every = int(spec["checkpoint_every"])
        config = SFTConfig(
            output_dir=str(output), per_device_train_batch_size=settings["batch_size"],
            gradient_accumulation_steps=settings["gradient_accumulation"], learning_rate=settings["learning_rate"],
            max_steps=int(spec["steps_total"]), warmup_steps=settings["warmup_steps"], logging_steps=1,
            optim="adamw_8bit", weight_decay=settings["weight_decay"], lr_scheduler_type="linear",
            seed=settings["seed"], report_to="none", max_length=settings["max_seq_length"],
            save_strategy="steps", save_steps=every, save_total_limit=2, dataset_num_proc=1, packing=False,
            bf16=True, dataloader_num_workers=0, disable_tqdm=True, save_only_model=False,
        )
        trainer = SFTTrainer(model=model, processing_class=tokenizer, train_dataset=dataset, args=config,
                             callbacks=[Bridge()])
        resume = str(output / spec["resume_from"]) if spec["resume_from"] else None
        torch.cuda.empty_cache()
        try:
            trainer.train(resume_from_checkpoint=resume)
        except (TrainingFailure, KeyboardInterrupt):
            raise
        except Exception as error:
            if resume and not reporter.stepped and classify(error) == "engine_error":
                raise TrainingFailure("cannot_resume") from error
            raise
        if reporter.pause_requested() and reporter.step_now < int(spec["steps_total"]):
            return []
        model.save_pretrained(str(output))  # the adapter's two files, beside its checkpoints
        return [_describe(output / name) for name in ("adapter_model.safetensors", "adapter_config.json")
                if (output / name).is_file()]


def _describe(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"name": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()}


# ── The protocol side ───────────────────────────────────────────────────────


class Reporter:
    """Sends the worker's messages in the order Row-Bot's ``protocol.Session`` accepts."""

    def __init__(self, protocol: Any, send: Callable[..., None], spec: dict[str, Any]) -> None:
        self.protocol = protocol
        self._send = send
        self.total = int(spec["steps_total"])
        self.step_now = protocol.checkpoint_step(spec["resume_from"]) if spec["resume_from"] else 0
        self.checkpointed = self.step_now
        self.stepped = False
        self._pause = threading.Event()

    def request_pause(self) -> None:
        self._pause.set()

    def pause_requested(self) -> bool:
        return self._pause.is_set()

    def step(self, step: int, loss: float | None, seconds: float | None) -> None:
        if step <= self.step_now or step > self.total:
            return
        self.step_now = step
        self.stepped = True
        loss = loss if loss is None or math.isfinite(loss) else None
        seconds = round(seconds, 3) if seconds is not None and math.isfinite(seconds) and seconds >= 0 else None
        self._send("progress", step=step, steps_total=self.total, loss=None if loss is None else round(loss, 6),
                   seconds_per_step=seconds)

    def checkpoint(self, step: int, folder: Path) -> None:
        if step <= self.checkpointed or step > self.step_now:
            return
        self.protocol.write_checkpoint_manifest(folder, step)
        self.checkpointed = step
        self._send("checkpoint", step=step, name=folder.name)


def _load_protocol(path: str) -> Any:
    spec = importlib.util.spec_from_file_location("_row_bot_training_protocol", path)
    if spec is None or spec.loader is None:
        raise SystemExit("row-bot engine: the protocol can't be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _host_gone() -> None:
    """Row-Bot is gone: nothing is left running (on Windows the job object ends the tree)."""

    if os.name != "nt":
        try:
            os.killpg(os.getpgid(0), signal.SIGKILL)
        except OSError:
            pass
    os._exit(0)


def run_protocol(protocol: Any, reader: Any, writer: Any, backend: Any,
                 on_host_gone: Callable[[], None] = _host_gone) -> int:
    """One run of the worker: hello, the spec, training, and exactly one last message."""

    lock = threading.Lock()

    def send(kind: str, **fields: Any) -> None:
        raw = protocol.encode(protocol.message(kind, **fields))
        with lock:
            writer.write(raw)
            writer.flush()

    send("hello", worker=getattr(backend, "name", "engine"))
    first = protocol.check_message(protocol.decode(reader.readline(protocol.MAX_FRAME + 1)))
    if first["type"] != "start":
        return 2
    spec = first["spec"]
    reporter = Reporter(protocol, send, spec)

    def control() -> None:
        while True:
            raw = reader.readline(protocol.MAX_FRAME + 1)
            if not raw:
                on_host_gone()
                return
            try:
                message = protocol.check_message(protocol.decode(raw))
            except Exception:
                on_host_gone()
                return
            if message["type"] == "pause":
                reporter.request_pause()

    try:
        rows = read_rows(Path(spec["lease"]["dataset"]) / "examples.jsonl")
    except TrainingFailure as failure:
        send("error", reason=failure.reason, step=None)
        return 1
    threading.Thread(target=control, name="row-bot-engine-control", daemon=True).start()
    send("started", step=reporter.step_now, steps_total=reporter.total)
    try:
        files = backend.run(spec, rows, reporter)
    except BaseException as error:  # noqa: BLE001 - every failure ends as one error message
        reason = classify(error)
        sys.stderr.write("row-bot engine: training stopped: " + "".join(
            traceback.format_exception_only(type(error), error))[-2000:] + "\n")
        send("error", reason=reason, step=reporter.step_now if reporter.stepped else None)
        return 1
    if reporter.pause_requested() and reporter.step_now < reporter.total:
        if reporter.checkpointed != reporter.step_now:
            send("error", reason="engine_error", step=reporter.step_now)
            return 1
        send("paused", step=reporter.step_now)
        return 0
    if reporter.step_now != reporter.total:
        send("error", reason="engine_error", step=reporter.step_now)
        return 1
    if files:
        send("artifacts", files=files)
    send("finished", step=reporter.step_now)
    return 0


def serve(writer: Any, reader: Any, *, protocol_path: str, model_dir: str, backend: Any = None) -> int:
    """The ``train`` mode: the protocol on the private standard input and output (``entry.py``)."""

    protocol = _load_protocol(protocol_path)
    code = run_protocol(protocol, reader, writer, backend or UnslothBackend(model_dir))
    sys.stderr.flush()
    os._exit(code)  # nothing else runs after the last message (Triton's threads, CUDA teardown)


# ── fit and export (the GPU check script) ───────────────────────────────────


def _peak(torch: Any) -> dict[str, int]:
    return {"peak_reserved_mib": int(torch.cuda.max_memory_reserved() // 2**20),
            "peak_allocated_mib": int(torch.cuda.max_memory_allocated() // 2**20)}


FILLER = ("The quarterly report lists travel, equipment and training costs for each team, "
          "with the owner, the due date and the approved amount for every line. ")


def synthetic_rows(tokenizer: Any, target: int, count: int = 16) -> list[dict[str, Any]]:
    """Made-up chats of about ``target`` tokens (for measuring memory and speed only)."""

    def row(repeats: int, number: int) -> dict[str, Any]:
        return {"messages": [
            {"role": "system", "content": "You are Row-Bot, a helpful assistant."},
            {"role": "user", "content": f"Report {number}: " + FILLER * repeats + "\nWhich costs does it list?"},
            {"role": "assistant", "content": "Travel, equipment and training, per team."},
        ]}

    probe = render_and_mask(tokenizer, row(50, 0), 10**9)
    per = max(1, (len(probe["input_ids"]) if probe else 50 * 30) // 50)
    repeats = max(1, (target - 96) // per)
    return [row(repeats, number) for number in range(count)]


def fit_main(writer: Any, options: dict[str, str]) -> int:
    """A few optimizer steps on rows of one length: does it fit, how much memory, how fast.

    ``--rows <jsonl>`` gives the rows, or ``--target-tokens N`` makes them (made-up text)."""

    result: dict[str, Any] = {"ran": False}
    settings = settings_from({
        "max_seq_length": int(options.get("max_seq", 16384)),
        "bits": int(options.get("bits", 16)),
        "gradient_accumulation": int(options.get("accum", 2)),
    })
    steps = int(options.get("steps", 4))
    started = time.monotonic()
    try:
        torch, model, tokenizer = load_model(options["model"], settings)
        result["vram_after_load_mib"] = int(torch.cuda.memory_allocated() // 2**20)
        if options.get("target_tokens"):
            rows = synthetic_rows(tokenizer, int(options["target_tokens"]))
        else:
            rows = read_rows(Path(options["rows"]))
        encoded = encode_rows(tokenizer, rows, settings["max_seq_length"])
        result["tokens_median"] = int(statistics.median(len(item["input_ids"]) for item in encoded))
        from datasets import Dataset
        from transformers import TrainerCallback
        from trl import SFTConfig, SFTTrainer

        times: list[float] = []

        class Meter(TrainerCallback):
            last = None

            def on_step_begin(self, args, state, control, **kwargs):
                if self.last is None:
                    self.last = time.monotonic()

            def on_step_end(self, args, state, control, **kwargs):
                now = time.monotonic()
                times.append(now - self.last)
                self.last = now

        config = SFTConfig(
            output_dir=str(Path(options["out"]).with_suffix(".runs")), per_device_train_batch_size=1,
            gradient_accumulation_steps=settings["gradient_accumulation"], learning_rate=settings["learning_rate"],
            max_steps=steps, warmup_steps=0, logging_steps=1, optim="adamw_8bit", report_to="none",
            max_length=settings["max_seq_length"], save_strategy="no", dataset_num_proc=1, packing=False, bf16=True,
            dataloader_num_workers=0, disable_tqdm=True,
        )
        trainer = SFTTrainer(model=model, processing_class=tokenizer, train_dataset=Dataset.from_list(encoded),
                             args=config, callbacks=[Meter()])
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        trainer.train()
        steady = times[2:] if len(times) > 3 else times
        result.update(_peak(torch))
        result["step_seconds_median"] = round(statistics.median(steady), 2) if steady else None
        result["seconds_per_row"] = (round(result["step_seconds_median"] / settings["gradient_accumulation"], 2)
                                     if result["step_seconds_median"] else None)
        result["ran"] = True
    except BaseException as error:  # noqa: BLE001 - a cell that doesn't fit is a result, not a crash
        result["error"] = classify(error)
        result["error_type"] = type(error).__name__
        try:
            import torch

            result.update(_peak(torch))
        except Exception:
            pass
    from entry import blocked

    result["seconds"] = round(time.monotonic() - started, 1)
    result["blocked_connections"] = blocked()
    Path(options["out"]).write_text(json.dumps(result, indent=1), encoding="utf-8")
    writer.write((json.dumps({"ok": result["ran"]}) + "\n").encode("utf-8"))
    writer.flush()
    os._exit(0)


def measure_main(writer: Any, options: dict[str, str]) -> int:
    """Each row's length and target tokens as training would render it (the starting model's own
    tokenizer and chat template, on the CPU): ``--rows <jsonl> --out <json>`` writes a list of
    ``[tokens, target_tokens]``, one per row (``[-1, 0]`` for a row the template refuses)."""

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(options["model"], local_files_only=True)
    tokenizer = getattr(tokenizer, "tokenizer", tokenizer)
    rows = read_rows(Path(options["rows"]))
    lengths = [list(measure_row(tokenizer, row)) for row in rows]
    Path(options["out"]).write_text(json.dumps(lengths), encoding="utf-8")
    writer.write((json.dumps({"ok": True, "rows": len(lengths)}) + "\n").encode("utf-8"))
    writer.flush()
    return 0


QUANTIZATIONS = ("Q8_0", "Q4_K_M")


def export_main(writer: Any, options: dict[str, str]) -> int:
    """Merge the adapter into its 16-bit starting model (on the CPU), convert it to GGUF with
    llama.cpp's converter and quantize it with ``llama-quantize``: ``--quant`` names the kinds
    (``Q8_0``, ``Q4_K_M``; both when not given, as the GPU check script measures).

    ``export.json`` in the output folder records each step's time and size, or the step that failed
    and its error's type (never its text, which names files); ``export.step`` says which step runs."""

    out = Path(options["out"])
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"steps": {}}
    progress = _Progress(out)
    try:
        _export(options, out, report, progress)
    except BaseException as error:
        report["failed"] = {"step": progress["step"], "error_type": type(error).__name__}
        (out / "export.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        raise
    from entry import blocked

    report["blocked_connections"] = blocked()
    (out / "export.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    writer.write((json.dumps({"ok": True}) + "\n").encode("utf-8"))
    writer.flush()
    return 0


class _Progress(dict):
    """The export's current step, also written to ``export.step`` for Row-Bot to read."""

    def __init__(self, out: Path) -> None:
        super().__init__()
        self._file = out / "export.step"
        self["step"] = "merge"

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        try:
            self._file.write_text(value, encoding="utf-8")
        except OSError:
            pass


def _export(options: dict[str, str], out: Path, report: dict[str, Any], progress: dict[str, str]) -> None:
    import shutil
    import subprocess

    kinds = [kind.strip() for kind in options.get("quant", ",".join(QUANTIZATIONS)).split(",") if kind.strip()]
    if not kinds or any(kind not in QUANTIZATIONS for kind in kinds):
        raise ValueError("unknown quantization")
    started = time.monotonic()
    # ``--adapter none``: the untouched starting model, converted as it is (Row-Bot compares a first
    # version with it, L2c2): nothing to merge, and the starting model's own folder is never removed.
    untouched = options.get("adapter", "").strip().lower() == "none"
    if untouched:
        merged = Path(options["model"])
        options = {**options, "keep_merged": "1"}
    else:
        import torch
        from peft import PeftModel
        from transformers import AutoConfig, AutoModelForCausalLM, AutoModelForImageTextToText, AutoTokenizer

        config = AutoConfig.from_pretrained(options["model"], local_files_only=True)
        architecture = (config.architectures or [""])[0]
        loader = AutoModelForImageTextToText if "ConditionalGeneration" in architecture else AutoModelForCausalLM
        model = loader.from_pretrained(options["model"], torch_dtype=torch.bfloat16, local_files_only=True,
                                       device_map="cpu")
        model = PeftModel.from_pretrained(model, options["adapter"], local_files_only=True).merge_and_unload()
        merged = out / "merged"
        model.save_pretrained(str(merged), safe_serialization=True)
        AutoTokenizer.from_pretrained(options["model"], local_files_only=True).save_pretrained(str(merged))
        for name in ("chat_template.jinja", "preprocessor_config.json", "video_preprocessor_config.json"):
            source = Path(options["model"]) / name
            if source.is_file() and not (merged / name).exists():
                (merged / name).write_bytes(source.read_bytes())
        del model
    report["steps"]["merge_seconds"] = round(time.monotonic() - started, 1)
    progress["step"] = "convert"
    llama = Path(options["llama"])
    name = options.get("name", "personal-model")
    bf16 = out / f"{name}-BF16.gguf"
    converter = next(iter(sorted(llama.glob("src/**/convert_hf_to_gguf.py"))), None)
    quantize = next((item for item in sorted(llama.glob("bin/**/llama-quantize*"))
                     if item.name in ("llama-quantize", "llama-quantize.exe")), None)  # not its -impl.dll
    if converter is None or quantize is None:
        raise FileNotFoundError("llama.cpp's converter or llama-quantize is missing")
    # The converter runs in the engine's interpreter with the same switches and network guard, its
    # own folder (its ``conversion`` package) and llama.cpp's ``gguf-py`` on the path.
    here = Path(__file__).resolve().parent
    code = (
        "import runpy, sys; sys.path.insert(0, {here!r}); import entry; entry.apply_switches(); "
        "entry.install_guard(); entry.add_site({site!r}); sys.path[:0] = [{folder!r}, {gguf!r}]; "
        "sys.argv = [{script!r}, *sys.argv[1:]]; runpy.run_path({script!r}, run_name='__main__')"
    ).format(here=str(here), site=options["site"], folder=str(converter.parent),
             gguf=str(converter.parent / "gguf-py"), script=str(converter))
    started = time.monotonic()
    subprocess.run([sys.executable, "-I", "-S", "-c", code, str(merged), "--outfile", str(bf16), "--outtype", "bf16"],
                   check=True)
    report["steps"]["convert_seconds"] = round(time.monotonic() - started, 1)
    report["steps"]["BF16"] = {"bytes": bf16.stat().st_size}
    if options.get("keep_merged") != "1":
        shutil.rmtree(merged, ignore_errors=True)  # not needed any more: less disk at the peak
    environment = dict(os.environ)
    if os.name != "nt":  # the release's shared libraries sit beside llama-quantize
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(
            filter(None, (str(quantize.parent), environment.get("LD_LIBRARY_PATH", ""))))
    for kind in kinds:
        progress["step"] = f"quantize {kind}"
        started = time.monotonic()
        target = out / f"{name}-{kind}.gguf"
        subprocess.run([str(quantize), str(bf16), str(target), kind], check=True, env=environment)
        report["steps"][kind] = {"bytes": target.stat().st_size, "seconds": round(time.monotonic() - started, 1)}
    bf16.unlink(missing_ok=True)
