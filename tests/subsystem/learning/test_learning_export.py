"""Exporting a version (L2c, O26): the disk check comes first; the engine's ``export`` mode runs with
its own clean environment and the recipe's quantization; its steps are followed; the GGUF lands
beside the version's files and no work folder is left; failures name their step; Stop stops it;
the adapter is copied only when it is the one the engine reported."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from row_bot.learning.training import engine, export

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]  # the engine's paths and process environment

GIB = 1024**3


def _engine(tmp_path: Path) -> SimpleNamespace:
    llama = tmp_path / "engine" / "llama"
    (llama / "bin").mkdir(parents=True)
    (llama / "bin" / engine.QUANTIZE_NAMES[0]).write_bytes(b"tool")
    (llama / "src").mkdir()
    (llama / "src" / engine.CONVERTER).write_text("# converter\n", encoding="utf-8")
    return SimpleNamespace(python=tmp_path / "engine" / "python.exe", site=tmp_path / "engine" / "site", llama=llama)


def _adapter(tmp_path: Path) -> Path:
    folder = tmp_path / "checkpoints"
    folder.mkdir(exist_ok=True)
    (folder / "adapter_model.safetensors").write_bytes(b"lora" * 64)
    (folder / "adapter_config.json").write_text('{"r": 16}', encoding="utf-8")
    return folder


class FakeExport:
    """The engine's ``export`` mode: it reports each step, then writes the GGUF and its report."""

    def __init__(self, *, fail_at: str | None = None, error_type: str = "RuntimeError") -> None:
        self.fail_at = fail_at
        self.error_type = error_type
        self.seen: list[str] = []
        self.argv: list[str] = []
        self.env: dict[str, str] = {}

    def __call__(self, argv, *, cwd, env, timeout, check):
        self.argv, self.env = list(argv), dict(env)
        out = Path(argv[argv.index("--out") + 1])
        name, quant = argv[argv.index("--name") + 1], argv[argv.index("--quant") + 1]
        for step in ("merge", "convert", f"quantize {quant}"):
            (out / "export.step").write_text(step, encoding="utf-8")
            check()
            self.seen.append(step)
            if self.fail_at and step.startswith(self.fail_at):
                (out / "export.json").write_text(json.dumps(
                    {"steps": {}, "failed": {"step": step, "error_type": self.error_type}}), encoding="utf-8")
                return engine.Run(1, "", "")
        (out / f"{name}-{quant}.gguf").write_bytes(b"GGUF" + b"\0" * 252)
        (out / "export.json").write_text(json.dumps({"steps": {"merge": {"seconds": 2.0}}, "blocked_connections": 0}),
                                         encoding="utf-8")
        return engine.Run(0, "", "")


def _export(tmp_path, runner, **overrides):
    options = {
        "weights": tmp_path / "weights", "weights_bytes": 4 * GIB, "folder": tmp_path / "versions" / "v1",
        "name": "row-bot-personal-v1", "quantization": "Q8_0", "runner": runner,
        "disk_free": lambda _path: 500 * GIB,
    }
    options.update(overrides)
    if "engine" not in options:
        options["engine"] = _engine(tmp_path)
    if "adapter" not in options:
        options["adapter"] = _adapter(tmp_path)
    return export.export_gguf(**options)


def test_an_export_builds_the_gguf_beside_the_version_with_the_engines_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-for-the-engine")
    runner = FakeExport()
    steps: list[str] = []

    result = _export(tmp_path, runner, on_step=steps.append)

    folder = tmp_path / "versions" / "v1"
    assert result.gguf == folder / "row-bot-personal-v1-Q8_0.gguf" and result.gguf.read_bytes().startswith(b"GGUF")
    assert result.gguf_bytes == 256 and result.gguf_sha256 == hashlib.sha256(result.gguf.read_bytes()).hexdigest()
    assert result.quantization == "Q8_0" and result.steps == {"merge": {"seconds": 2.0}}
    assert steps == ["merge", "convert", "quantize"]
    assert sorted(item.name for item in folder.iterdir()) == ["row-bot-personal-v1-Q8_0.gguf"]  # no work left
    argv = runner.argv
    assert argv[1:4] == ["-I", "-S", str(engine.ENTRY)] and argv[4] == "export"
    assert argv[argv.index("--quant") + 1] == "Q8_0"
    assert "OPENAI_API_KEY" not in runner.env and runner.env["HOME"] == str(folder / ".export-home")


def test_a_larger_tier_exports_at_its_own_quantization(tmp_path):
    runner = FakeExport()

    result = _export(tmp_path, runner, quantization="Q4_K_M")

    assert result.gguf.name == "row-bot-personal-v1-Q4_K_M.gguf"
    assert runner.argv[runner.argv.index("--quant") + 1] == "Q4_K_M"


def test_the_disk_is_checked_before_anything_runs(tmp_path):
    runner = FakeExport()

    with pytest.raises(export.ExportError) as error:
        _export(tmp_path, runner, disk_free=lambda _path: 3 * GIB)

    needs = math.ceil(4 * GIB) + math.ceil(4 * GIB * export.BF16_SHARE) + export.MARGIN_BYTES
    assert (error.value.code, error.value.needs, error.value.free) == ("not_enough_disk", needs, 3 * GIB)
    assert runner.argv == []
    assert export.disk_needed(4 * GIB, "Q8_0") == needs


def test_an_engine_or_its_tools_missing_is_said_before_starting(tmp_path):
    with pytest.raises(export.ExportError) as error:
        _export(tmp_path, FakeExport(), engine=SimpleNamespace(python=None, site=None, llama=None))
    assert error.value.code == "engine_missing"

    broken = _engine(tmp_path / "other")
    (broken.llama / "bin" / engine.QUANTIZE_NAMES[0]).unlink()
    with pytest.raises(export.ExportError) as error:
        _export(tmp_path, FakeExport(), engine=broken)
    assert error.value.code == "tools_missing"


def test_a_failed_step_is_named_and_leaves_no_half_built_files(tmp_path):
    with pytest.raises(export.ExportError) as error:
        _export(tmp_path, FakeExport(fail_at="convert"))

    assert (error.value.code, error.value.step) == ("export_failed", "convert")
    assert list((tmp_path / "versions" / "v1").iterdir()) == []


def test_running_out_of_disk_midway_is_said_as_disk(tmp_path):
    free = iter([500 * GIB, 10 * 1024**2, 10 * 1024**2])

    with pytest.raises(export.ExportError) as error:
        _export(tmp_path, FakeExport(fail_at="quantize", error_type="OSError"), disk_free=lambda _path: next(free))

    assert error.value.code == "not_enough_disk"


def test_stop_stops_the_export(tmp_path):
    from row_bot.learning.training.releases import StopExport

    def check():
        raise StopExport()

    with pytest.raises(export.ExportError) as error:
        _export(tmp_path, FakeExport(), check=check)

    assert error.value.code == "stopped"
    assert list((tmp_path / "versions" / "v1").iterdir()) == [tmp_path / "versions" / "v1" / export.WORK]


def test_the_adapter_is_copied_only_when_it_is_the_one_the_engine_reported(tmp_path):
    source = _adapter(tmp_path)
    weights_sha = hashlib.sha256((source / "adapter_model.safetensors").read_bytes()).hexdigest()

    folder, size, sha = export.copy_adapter(source, tmp_path / "v1" / "adapter",
                                            {"adapter_model.safetensors": weights_sha})

    assert sorted(item.name for item in folder.iterdir()) == list(sorted(export.ADAPTER_FILES))
    assert size == 256 + len('{"r": 16}') and sha == weights_sha
    with pytest.raises(export.ExportError) as error:
        export.copy_adapter(source, tmp_path / "v2", {"adapter_model.safetensors": "0" * 64})
    assert error.value.code == "adapter_missing"
    (source / "adapter_config.json").unlink()
    with pytest.raises(export.ExportError):
        export.copy_adapter(source, tmp_path / "v3")
