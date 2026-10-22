"""The training engine's trainer (``learning_engine/trainer.py``, L2b): its side of the training
protocol, driven here in-process with a stand-in for Unsloth/TRL and checked message by message
by the host's own ``protocol.Session``. Also its recipe limits, error classes and loss masks.
No GPU, no torch."""

from __future__ import annotations

import errno
import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from row_bot.learning.training import lockfile, protocol

pytestmark = pytest.mark.subsystem


def _trainer():
    spec = importlib.util.spec_from_file_location("_row_bot_engine_trainer", lockfile.ENGINE_DIR / "trainer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trainer = _trainer()


class FakeBackend:
    """Trains nothing: every step writes a checkpoint's files on schedule, like HF's Trainer."""

    name = "fake"

    def __init__(self, *, pause_at: int | None = None, fail_with: BaseException | None = None,
                 fail_at: int = 1) -> None:
        self.pause_at = pause_at
        self.fail_with = fail_with
        self.fail_at = fail_at
        self.rows = 0

    def run(self, spec, rows, reporter):
        self.rows = len(rows)
        output = Path(spec["output"])
        every = spec["checkpoint_every"]
        start = protocol.checkpoint_step(spec["resume_from"]) if spec["resume_from"] else 0
        for step in range(start + 1, spec["steps_total"] + 1):
            if self.fail_with is not None and step == self.fail_at:
                raise self.fail_with
            if step == self.pause_at:
                reporter.request_pause()  # as the host's pause frame does
            reporter.step(step, 2.0 / step, 1.5)
            if step % every == 0 or reporter.pause_requested():
                folder = output / protocol.checkpoint_name(step)
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "adapter_model.safetensors").write_bytes(b"w" * step)
                reporter.checkpoint(step, folder)
            if reporter.pause_requested():
                return []
        (output / "adapter_model.safetensors").write_bytes(b"final")
        return [trainer._describe(output / "adapter_model.safetensors")]  # noqa: SLF001


def _spec(run: Path, *, steps: int = 6, every: int = 2, resume: str | None = None) -> dict:
    (run / "data").mkdir(parents=True, exist_ok=True)
    (run / "checkpoints").mkdir(parents=True, exist_ok=True)
    rows = [{"messages": [{"role": "user", "content": f"Task {n}"}, {"role": "assistant", "content": "Done."}]}
            for n in range(3)]
    (run / "data" / "examples.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return {"job_id": "job_1", "version": 1, "steps_total": steps, "checkpoint_every": every, "resume_from": resume,
            "lease": {"dataset": str((run / "data").resolve()), "examples": 3},
            "output": str((run / "checkpoints").resolve()),
            "recipe": {"name": "recommended", "settings": {"report_to": "none"}}}


def _run(spec: dict, backend: FakeBackend, *, pause_frame: bool = False) -> tuple[int, list[dict], protocol.Session]:
    """Run the worker's side in-process; the host's Session checks every message it sends."""

    read, write = os.pipe()
    host = os.fdopen(write, "wb", buffering=0)
    host.write(protocol.encode(protocol.start(spec)))
    if pause_frame:
        host.write(protocol.encode(protocol.pause()))
    reader = io.BufferedReader(os.fdopen(read, "rb", buffering=0))
    out = io.BytesIO()
    session = protocol.Session(spec)
    if pause_frame or backend.pause_at:
        session.request_pause()
    gone = threading.Event()
    code = trainer.run_protocol(protocol, reader, out, backend, on_host_gone=gone.set)
    host.close()  # the worker would exit now; its control thread just ends here
    messages = [protocol.decode(line + b"\n") for line in out.getvalue().splitlines()]
    for message in messages:
        session.accept(message)
    return code, messages, session


def test_a_run_reports_every_step_checkpoints_on_schedule_and_finishes_with_its_files(tmp_path):
    spec = _spec(tmp_path)

    code, messages, session = _run(spec, FakeBackend())

    kinds = [message["type"] for message in messages]
    assert code == 0 and session.ended
    assert kinds == ["hello", "started", "progress", "progress", "checkpoint", "progress", "progress", "checkpoint",
                     "progress", "progress", "checkpoint", "artifacts", "finished"]
    assert [m["step"] for m in messages if m["type"] == "checkpoint"] == [2, 4, 6]
    # Row-Bot's manifest is written last in each checkpoint, so the host can trust it.
    protocol.read_checkpoint_manifest(Path(spec["output"]) / "checkpoint-4")
    assert messages[-2]["files"][0]["name"] == "adapter_model.safetensors"


def test_a_pause_saves_at_the_next_step_and_ends_with_paused(tmp_path):
    code, messages, session = _run(_spec(tmp_path), FakeBackend(pause_at=3))

    assert code == 0 and session.ended
    assert [m["type"] for m in messages[-2:]] == ["checkpoint", "paused"]
    assert messages[-1]["step"] == 3 and messages[-2]["step"] == 3


def test_the_hosts_pause_frame_reaches_the_trainer(tmp_path):
    seen = threading.Event()

    class Waits(FakeBackend):
        def run(self, spec, rows, reporter):
            for _ in range(200):  # the control thread reads the frame
                if reporter.pause_requested():
                    seen.set()
                    break
                threading.Event().wait(0.01)
            return super().run(spec, rows, reporter)

    _run(_spec(tmp_path), Waits(), pause_frame=True)

    assert seen.is_set()


def test_a_resumed_run_starts_after_its_checkpoint(tmp_path):
    first = _spec(tmp_path)
    _run(first, FakeBackend(pause_at=4))

    code, messages, _session = _run(_spec(tmp_path, resume="checkpoint-4"), FakeBackend())

    assert code == 0
    assert messages[1] == {"v": 1, "type": "started", "step": 4, "steps_total": 6}
    assert [m["step"] for m in messages if m["type"] == "progress"] == [5, 6]


class OutOfMemoryError(RuntimeError):
    """What torch raises when a step doesn't fit the card (matched by name)."""


@pytest.mark.parametrize("error, reason", [
    (OutOfMemoryError("CUDA out of memory. Tried to allocate 3.94 GiB"), "out_of_gpu_memory"),
    (OSError(errno.ENOSPC, "No space left on device"), "out_of_disk"),
    (ValueError("something inside the engine"), "engine_error"),
])
def test_failures_are_reported_as_a_reason_class_never_the_engines_text(tmp_path, error, reason):
    code, messages, session = _run(_spec(tmp_path), FakeBackend(fail_with=error, fail_at=2))

    assert code == 1 and session.ended
    assert messages[-1] == {"v": 1, "type": "error", "reason": reason, "step": 1}


def test_unreadable_examples_are_reported_before_training(tmp_path):
    spec = _spec(tmp_path)
    (Path(spec["lease"]["dataset"]) / "examples.jsonl").write_text("not json\n", encoding="utf-8")

    code, messages, _session = _run(spec, FakeBackend())

    assert code == 1
    assert [m["type"] for m in messages] == ["hello", "error"] and messages[-1]["reason"] == "data_unreadable"


def test_recipe_settings_stay_within_their_limits():
    settings = trainer.settings_from({"rank": 32, "learning_rate": 5.0, "bits": 8, "max_seq_length": 32768,
                                      "report_to": "wandb", "seed": True})

    assert settings["rank"] == 32 and settings["max_seq_length"] == 32768
    assert settings["learning_rate"] == trainer.DEFAULTS["learning_rate"]  # out of range: the default
    assert settings["bits"] == 16  # only 4 or 16
    assert settings["seed"] == trainer.DEFAULTS["seed"]  # a switch isn't a number
    assert "report_to" not in settings  # never a reporting integration
    # A large card can stand in for a smaller tier's (8 GB of a 32 GB card), never for no card at all.
    assert trainer.settings_from({"vram_cap": 0.2375})["vram_cap"] == 0.2375
    assert trainer.settings_from({"vram_cap": 0.05})["vram_cap"] == trainer.DEFAULTS["vram_cap"]


class FakeTokenizer:
    """Renders Qwen's chat template shape and tokenizes by character."""

    def apply_chat_template(self, messages, tools=None, tokenize=False, add_generation_prompt=False):
        text = ""
        for message in messages:
            text += f"<|im_start|>{message['role']}\n"
            if message["role"] == "assistant":
                text += trainer.EMPTY_THINK
                if message.get("tool_calls"):
                    text += json.dumps(message["tool_calls"][0]["function"]["arguments"])
            text += f"{message['content']}<|im_end|>\n"
        return text

    def __call__(self, text, add_special_tokens=False, return_offsets_mapping=True):
        return {"input_ids": [ord(char) for char in text], "offset_mapping": [(i, i + 1) for i in range(len(text))]}


def test_loss_is_only_on_the_assistants_answers():
    row = {"messages": [
        {"role": "user", "content": "Add a meeting"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "tool_search",
                                                                       "arguments": "{\"query\": \"calendar\"}"}}]},
        {"role": "tool", "content": "found"},
        {"role": "assistant", "content": "Done."},
    ]}

    encoded = trainer.render_and_mask(FakeTokenizer(), row, 10_000)

    trained = "".join(chr(token) for token, label in zip(encoded["input_ids"], encoded["labels"], strict=True)
                      if label != -100)
    assert trained == '{"query": "calendar"}<|im_end|>Done.<|im_end|>'
    assert trainer.render_and_mask(FakeTokenizer(), row, 20) is None  # longer than the context: left out


def _trained_text(encoded) -> str:
    return "".join(chr(token) for token, label in zip(encoded["input_ids"], encoded["labels"], strict=True)
                   if label != -100)


def test_an_earlier_turns_reply_is_context_and_only_weighted_replies_train():
    row = {"messages": [
        {"role": "system", "content": "You are Row-Bot."},
        {"role": "user", "content": "First"},
        {"role": "assistant", "content": "Old answer.", "weight": 0},
        {"role": "user", "content": "Second"},
        {"role": "assistant", "content": "New answer.", "weight": 1},
    ]}

    encoded = trainer.render_and_mask(FakeTokenizer(), row, 10_000)
    tokens, targets = trainer.measure_row(FakeTokenizer(), row)

    assert _trained_text(encoded) == "New answer.<|im_end|>"
    assert "weight" not in json.dumps(trainer.template_messages(row["messages"]))  # never rendered as content
    assert tokens == len(encoded["input_ids"]) and targets == len("New answer.<|im_end|>")


def test_a_row_the_template_refuses_or_cant_match_is_left_out():
    class Refuses(FakeTokenizer):
        def apply_chat_template(self, *args, **kwargs):
            raise ValueError("System message must be at the beginning.")

    class DropsOne(FakeTokenizer):
        def apply_chat_template(self, messages, **kwargs):  # renders one assistant turn fewer
            return super().apply_chat_template(messages[:-1], **kwargs)

    row = {"messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Old.", "weight": 0},
                        {"role": "user", "content": "Again"}, {"role": "assistant", "content": "New.", "weight": 1}]}

    assert trainer.render_and_mask(Refuses(), row, 10_000) is None
    assert trainer.measure_row(Refuses(), row) == (-1, 0)
    assert trainer.render_and_mask(DropsOne(), row, 10_000) is None  # never trained on the wrong text
    assert trainer.measure_row(DropsOne(), row)[1] == 0


PROBE = '''
import importlib.util, json, os, sys
spec = importlib.util.spec_from_file_location("engine_entry", sys.argv[1])
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
reader = entry.private_stdin()
first = reader.readline().decode()
own = [sys.stdin.read(), os.read(0, 16).decode()]
rest = reader.read().decode()
print(json.dumps({"first": first, "own": own, "rest": rest}))
'''


@pytest.mark.slow
def test_the_trainer_keeps_standard_input_to_itself(tmp_path):
    """Row-Bot's frames reach only the trainer's private copy of standard input; the process's own
    reads nothing, so a library that inspects it as it loads can't block on the pipe."""

    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")

    done = subprocess.run([sys.executable, "-I", str(probe), str(lockfile.ENGINE_DIR / "entry.py")],
                          input=b"frame one\nframe two\n", capture_output=True, timeout=60, check=True)

    seen = json.loads(done.stdout)
    assert seen == {"first": "frame one\n", "own": ["", ""], "rest": "frame two\n"}


def test_the_export_merges_each_adapted_layer_a_file_at_a_time_and_keeps_everything_else(tmp_path):
    """L2x: a 27B model's 55 GB never sits in memory to merge (one weights file at a time)."""

    torch = pytest.importorskip("torch")
    tensors = pytest.importorskip("safetensors.torch")
    model, adapter = tmp_path / "model", tmp_path / "adapter"
    model.mkdir()
    adapter.mkdir()
    q = "model.language_model.layers.0.self_attn.q_proj.weight"
    weight = torch.arange(12, dtype=torch.float32).reshape(3, 4).to(torch.bfloat16)
    tensors.save_file({q: weight, "mtp.fc.weight": torch.ones(2, 2, dtype=torch.bfloat16)},
                      str(model / "model-00001-of-00002.safetensors"))
    tensors.save_file({"model.language_model.layers.1.mlp.down_proj.weight": torch.zeros(3, 4, dtype=torch.bfloat16)},
                      str(model / "model-00002-of-00002.safetensors"))
    (model / "config.json").write_text("{}", encoding="utf-8")
    (model / "tokenizer.json").write_text("{}", encoding="utf-8")
    a = torch.tensor([[1.0, 0, 0, 0], [0, 1.0, 0, 0]])
    b = torch.tensor([[1.0, 0], [0, 1.0], [1.0, 1.0]])
    prefix = "base_model.model.model.language_model.layers.0.self_attn.q_proj"
    tensors.save_file({f"{prefix}.lora_A.weight": a, f"{prefix}.lora_B.weight": b},
                      str(adapter / "adapter_model.safetensors"))
    (adapter / "adapter_config.json").write_text(json.dumps({"r": 2, "lora_alpha": 4}), encoding="utf-8")

    merged = tmp_path / "merged"
    assert trainer.merge_streaming(model, adapter, merged) == 1

    first = tensors.load_file(str(merged / "model-00001-of-00002.safetensors"))
    assert first[q].dtype == torch.bfloat16
    assert torch.equal(first[q], (weight.float() + 2.0 * (b @ a)).to(torch.bfloat16))  # W + alpha/r * B @ A
    assert torch.equal(first["mtp.fc.weight"], torch.ones(2, 2, dtype=torch.bfloat16))  # not adapted: as it was
    second = tensors.load_file(str(merged / "model-00002-of-00002.safetensors"))
    assert torch.equal(second["model.language_model.layers.1.mlp.down_proj.weight"], torch.zeros(3, 4,
                                                                                                 dtype=torch.bfloat16))
    assert (merged / "config.json").is_file() and (merged / "tokenizer.json").is_file()

    elsewhere = "base_model.model.model.language_model.layers.9.self_attn.q_proj"
    tensors.save_file({f"{elsewhere}.lora_A.weight": a, f"{elsewhere}.lora_B.weight": b},
                      str(adapter / "adapter_model.safetensors"))
    with pytest.raises(ValueError):  # an adapter for layers this model doesn't have
        trainer.merge_streaming(model, adapter, tmp_path / "merged-2")
