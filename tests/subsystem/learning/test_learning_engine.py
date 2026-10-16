"""Installing, checking and removing the training engine (L2b, E1, E14, O43), with a stand-in
for uv and a package index on loopback. Nothing reaches the network or a graphics card."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from row_bot.learning.training import engine, fetch, lockfile
from row_bot.learning.training.worker import PROTOCOL_PATH, TELEMETRY_ENV, venv_launcher
from tests.fixtures.learning import save_learning_settings
from tests.fixtures.learning_engine import (
    FakeIndex,
    fake_engine_site,
    fake_lock,
    loopback_opener,
    uv_command,
    wheel,
)

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]


class Setup:
    def __init__(self, index: FakeIndex, root: Path, lock: Path, log: Path) -> None:
        self.index, self.root, self.lock, self.log = index, root, lock, log

    def plan(self, variant: str = "cu130") -> lockfile.EnginePlan:
        return lockfile.engine_plan(variant, lock=lockfile.load(self.lock))

    def installer(self, variant: str = "cu130", fail: str | None = None, **options) -> engine.EngineInstaller:
        return engine.EngineInstaller(
            self.plan(variant), root=self.root, fetcher=fetch.Fetcher(opener=loopback_opener(self.index), retry_wait=0),
            uv=uv_command(self.log, fail), run_check=False, **options)

    def uv_runs(self) -> list[dict]:
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    log = tmp_path / "uv.jsonl"
    with FakeIndex() as index:
        common = [wheel(index, "transformers", "5.17.0"), wheel(index, "unsloth", "2026.10.6"),
                  wheel(index, "trl", "1.15.0")]
        cu130 = [wheel(index, "torch", "2.12.1+cu130",
                       files={"torch/__init__.py": b"__version__ = '2.12.1'\n" + b"#" * 300_000},
                       source={"registry": "https://download.pytorch.org/whl/cu130"})]
        cu128 = [wheel(index, "torch", "2.11.0+cu128", source={"registry": "https://download.pytorch.org/whl/cu128"})]
        uv = wheel(index, "uv", "0.12.22")
        lock = fake_lock(tmp_path / "uv.lock", common=common, cu130=cu130, cu128=cu128, uv=uv)
        monkeypatch.setattr(lockfile, "LOCK_FILE", lock)
        engine.forget_status()
        yield Setup(index, root, lock, log)
        engine.forget_status()


def test_an_install_downloads_builds_and_publishes_the_engine_last(setup):
    phases: list[str] = []
    installer = setup.installer(on_progress=lambda progress: phases.append(progress.phase))

    found = installer.install()

    assert found.state == "installed" and found.variant == "cu130"
    assert found.site is not None and (found.site / "torch" / "__init__.py").is_file()
    assert found.python is not None and found.python.is_file()
    base = engine.engine_dir(setup.root)
    installed = json.loads((base / "engine.json" if False else base / "installed.json").read_text(encoding="utf-8"))
    assert installed["variant"] == "cu130" and (base / installed["folder"] / "engine.json").is_file()
    # The wheels and the build's leftovers are gone; only the published engine stays.
    assert sorted(item.name for item in base.iterdir()) == sorted([installed["folder"], "installed.json"])
    assert phases[0] == "engine" and "installing" in phases
    assert found.bytes > 0


def test_uv_runs_isolated_offline_and_from_the_verified_wheels_only(setup):
    setup.installer().install()

    runs = setup.uv_runs()
    python, pip = runs[0], runs[1]
    assert python["argv"][:2] == ["python", "install"] and python["argv"][2] == "3.12.15"
    assert {"--no-bin", "--no-registry", "--install-dir"} <= set(python["argv"])
    assert {"--offline", "--no-index", "--require-hashes", "--no-deps", "--no-build"} <= set(pip["argv"])
    for run in runs:
        assert run["env"]["UV_NO_CONFIG"] == "1"
        assert run["env"]["UV_PYTHON_INSTALL_REGISTRY"] == "0"
        assert not any(key.startswith("ROW_BOT") for key in run["env"])  # no data folder, keys or settings
        assert "HF_TOKEN" not in run["env"] and "HUGGING_FACE_HUB_TOKEN" not in run["env"]
        assert run["env"]["HF_HUB_DISABLE_TELEMETRY"] == "1"
        # Its home is inside the build: nothing lands in the person's own folders.
        assert str(engine.engine_dir(setup.root)) in run["env"]["HOME"]


def test_a_stop_part_way_installs_nothing_and_the_next_attempt_downloads_only_the_rest(setup):
    torch_path = next(path for path in setup.index.files if "torch-2.12.1" in path)
    downloaded = engine.downloads_dir(setup.root)

    def check() -> None:
        # Stop once two wheels are whole (the third is under way).
        if downloaded.is_dir() and len(list(downloaded.glob("*.whl"))) >= 2:
            raise fetch.Stopped()

    with pytest.raises(fetch.Stopped):
        setup.installer().install(check)

    assert engine.status(setup.root, refresh=True).state == "not_installed"
    assert len(list(downloaded.glob("*.whl"))) == 2  # what arrived is kept
    before = len(setup.index.requests)

    setup.installer().install()

    again = [item["path"] for item in setup.index.requests[before:]]
    assert len(again) < len(setup.plan().wheels)  # the finished wheels weren't downloaded again
    assert engine.status(setup.root).state == "installed"
    assert torch_path in [item["path"] for item in setup.index.requests]


def test_a_wheel_that_doesnt_match_the_lock_is_refused_and_nothing_is_installed(setup):
    path = next(path for path in setup.index.files if "trl" in path)
    setup.index.files[path] = setup.index.files[path] + b"tampered"

    with pytest.raises(engine.EngineError) as refused:
        setup.installer().install()

    assert refused.value.code == "mismatch"
    assert engine.status(setup.root, refresh=True).state == "not_installed"


def test_uv_failing_leaves_no_half_built_engine_and_keeps_the_previous_one(setup):
    first = setup.installer().install()
    forget = engine.engine_dir(setup.root) / "installed.json"
    marker = forget.read_bytes()

    with pytest.raises(engine.EngineError) as failed:
        setup.installer(fail="pip install").install()

    assert failed.value.code == "install_failed"
    assert forget.read_bytes() == marker
    assert engine.status(setup.root, refresh=True).folder == first.folder
    assert not list(engine.engine_dir(setup.root).glob(".staging-*"))


def test_an_engine_missing_files_reads_as_damaged_and_repair_builds_it_again(setup):
    first = setup.installer().install()
    torch_info = next(first.site.glob("torch-*.dist-info"))
    (torch_info / "METADATA").unlink()

    damaged = engine.status(setup.root, refresh=True)
    assert damaged.state == "damaged"

    repaired = setup.installer().install()
    assert repaired.state == "installed" and repaired.folder != first.folder
    assert not first.folder.exists()  # the damaged one was removed after the new one was published


def test_an_engine_built_from_another_lock_reads_as_outdated(setup):
    setup.installer().install()

    found = engine.status(setup.root, refresh=True, plan_identity=lambda variant: "another-plan")

    assert found.state == "outdated"


def test_the_driver_picks_the_cuda_128_build(setup):
    found = setup.installer("cu128").install()

    assert found.variant == "cu128"
    assert any(path.name.startswith("torch-2.11.0") for path in found.site.glob("torch-*.dist-info"))


def test_remove_deletes_the_engine_and_its_caches_and_says_how_much_it_freed(setup):
    found = setup.installer().install()
    caches = engine.cache_dir(setup.root) / "triton"
    caches.mkdir(parents=True)
    (caches / "kernel.bin").write_bytes(b"k" * 1000)

    freed = engine.remove(setup.root)

    assert freed >= found.bytes + 1000
    assert engine.status(setup.root, refresh=True).state == "not_installed"
    assert not engine.learning_runtimes(setup.root).exists()


def test_the_worker_runs_the_engines_base_interpreter_never_a_venv_launcher(setup, monkeypatch):
    # O43: the engine's own interpreter (not Scripts\\python.exe), -I -S, the entry, its site-packages.
    from row_bot import learning
    from row_bot.learning.training import weights

    found = setup.installer().install()
    save_learning_settings(setup.root, enabled=True)
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(setup.root))
    learning.invalidate()
    model_folder = weights.model_dir(weights.QWEN35_2B, setup.root)
    local = engine.LocalEngine(setup.root)
    assert local.ready() is False  # the starting model isn't there yet
    monkeypatch.setattr(weights, "status", lambda model, root=None: weights.WeightsStatus("downloaded", 1, model_folder))

    command = local.command(None)

    assert local.ready() is True
    assert command.argv[0] == str(found.python) and not venv_launcher(command.argv[0])
    assert command.argv[1:5] == ("-I", "-S", str(engine.ENTRY), "train")
    assert command.argv[command.argv.index("--site") + 1] == str(found.site)
    assert command.argv[command.argv.index("--protocol") + 1] == str(PROTOCOL_PATH)
    assert command.argv[command.argv.index("--model") + 1] == str(model_folder)
    assert command.env["UNSLOTH_CE_LOSS_TARGET_GB"] == "1"
    assert command.env["TRITON_CACHE_DIR"].startswith(str(engine.cache_dir(setup.root)))
    assert not set(command.env) & set(TELEMETRY_ENV)  # the worker sets those itself, last


def test_uv_comes_from_its_verified_wheel(tmp_path):
    from tests.fixtures.learning_engine import make_wheel

    executable = "uv.exe" if os.name == "nt" else "uv"
    filename, data = make_wheel("uv", "0.12.22", {f"uv-0.12.22.data/scripts/{executable}": b"the uv program",
                                                  "uv/__init__.py": b""})
    path = tmp_path / filename
    path.write_bytes(data)

    found = engine.extract_uv(path, tmp_path / "tools")

    assert found.read_bytes() == b"the uv program" and found.name == executable


# ── The engine's own check (entry.py), run by the app's Python on stand-in packages ──


def _check(tmp_path, **site_options) -> dict:
    site = fake_engine_site(tmp_path / "engine", **site_options)
    return engine.engine_check(Path(sys.executable), site, cwd=tmp_path / "work", check=lambda: None)


def test_the_check_imports_every_package_with_the_switches_already_set(tmp_path):
    seen = tmp_path / "seen.json"

    report = _check(tmp_path, seen=seen)

    assert report["ok"] is True and report["cuda"] is True and report["torch"] == "2.12.1+cu130"
    switches = json.loads(seen.read_text(encoding="utf-8"))
    assert switches == {
        "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_OFFLINE": "1", "UNSLOTH_DISABLE_STATISTICS": "1",
        "UNSLOTH_AUTO_INSTALL": "0", "USE_HUB_KERNELS": "NO", "WANDB_MODE": "disabled", "DO_NOT_TRACK": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1", "HF_HUB_DISABLE_UPDATE_CHECK": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
    }


def test_the_engines_network_guard_refuses_a_library_phoning_home(tmp_path):
    report = _check(tmp_path, phone_home=True)

    # The lookup and the connection were both refused before anything left the computer.
    assert report["ok"] is True and report["blocked_connections"] == 2


def test_no_graphics_card_or_a_broken_package_fails_the_check(tmp_path):
    with pytest.raises(engine.EngineError) as no_cuda:
        _check(tmp_path, cuda=False)
    assert no_cuda.value.code == "no_cuda"

    with pytest.raises(engine.EngineError) as broken:
        _check(tmp_path / "again", broken="trl")
    assert broken.value.code == "check_failed"
