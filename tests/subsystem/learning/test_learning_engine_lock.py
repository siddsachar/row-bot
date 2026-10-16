"""The training engine's lock (L2b, E1, E14, O25): what Row-Bot would download, and what it never
would. The real lock is read as shipped; a small lock made here checks the walk itself."""

from __future__ import annotations

import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from row_bot.learning.training import fetch, lockfile
from tests.fixtures.learning_engine import FakeIndex, fake_lock, wheel

pytestmark = pytest.mark.subsystem

ROOT = Path(__file__).resolve().parents[3]
WINDOWS = {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64"}
LINUX = {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix", "platform_machine": "x86_64"}
LINUX_TAGS = [f"manylinux_2_{minor}_x86_64" for minor in range(39, 4, -1)] + [
    "manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64", "linux_x86_64"]
COMPUTERS = {"windows": (WINDOWS, ["win_amd64"]), "linux": (LINUX, LINUX_TAGS)}
# Telemetry, remote caches and Hub kernels, and the xformers build for another torch (E14, O25).
NEVER = ("wandb", "kernels", "redis", "trackio", "xformers", "comet-ml", "mlflow")


def _plan(variant: str, computer: str) -> lockfile.EnginePlan:
    environment, platforms = COMPUTERS[computer]
    return lockfile.engine_plan(variant, platforms=platforms, environment=environment)


@pytest.mark.parametrize("computer", sorted(COMPUTERS))
@pytest.mark.parametrize("variant", ["cu130", "cu128"])
def test_every_engine_build_is_pinned_wheels_from_reviewed_hosts(variant, computer):
    plan = _plan(variant, computer)

    assert plan.python_version == "3.12.15"
    torch = next(item for item in plan.wheels if item.name == "torch")
    assert torch.version == {"cu130": "2.12.1+cu130", "cu128": "2.11.0+cu128"}[variant]
    for item in (*plan.wheels, plan.uv):
        assert item.filename.endswith(".whl"), item  # wheels only: nothing is ever built
        assert urlsplit(item.url).scheme == "https"
        assert urlsplit(item.url).hostname in fetch.HOSTS
        assert len(item.sha256) == 64
        # Every size is known before the download starts (a real denominator).
        assert item.known_size and item.known_size > 0, item.filename
    windows = computer == "windows"
    assert ("triton-windows" in plan.names) is windows
    assert ("triton" in plan.names) is not windows
    assert plan.uv.name == "uv" and plan.uv.version == "0.12.22"
    assert "uv" not in plan.names  # the installer is fetched first, never installed into the engine


def test_the_windows_cuda_13_engine_is_the_one_l0d_measured():
    plan = _plan("cu130", "windows")

    assert len(plan.wheels) == 85
    assert 2.1e9 < plan.download_bytes() < 2.4e9
    versions = {item.name: item.version for item in plan.wheels}
    assert {name: versions[name] for name in ("unsloth", "unsloth-zoo", "transformers", "trl", "peft", "accelerate",
                                              "bitsandbytes", "triton-windows")} == {
        "unsloth": "2026.10.6", "unsloth-zoo": "2026.10.6", "transformers": "5.17.0", "trl": "1.15.0",
        "peft": "0.21.2", "accelerate": "1.15.0", "bitsandbytes": "0.50.2", "triton-windows": "3.7.1.post27",
    }


@pytest.mark.parametrize("computer", sorted(COMPUTERS))
@pytest.mark.parametrize("variant", ["cu130", "cu128"])
def test_no_build_installs_telemetry_remote_caches_or_xformers(variant, computer):
    assert not set(NEVER) & _plan(variant, computer).names


def test_the_lock_itself_names_none_of_them_and_overrides_xformers_away():
    names = lockfile.every_package_name()

    assert not set(NEVER) & names
    document = tomllib.loads(lockfile.LOCK_FILE.read_text(encoding="utf-8"))
    assert document["manifest"]["overrides"] == [{"name": "xformers", "marker": "python_full_version < '3'"}]


def test_the_engine_is_never_part_of_row_bots_own_environment():
    # E1: not in Row-Bot's own lock, requirements, the `all` extra or the installers' payload.
    engine_only = {"unsloth", "unsloth-zoo", "trl", "peft", "bitsandbytes", "triton-windows", "xformers"}
    main = {package["name"] for package in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]}
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert not engine_only & main
    assert not any(f"\n{name}==" in requirements for name in engine_only)
    extras = " ".join(" ".join(items) for items in project["project"]["optional-dependencies"].values())
    assert not any(name in extras for name in engine_only)


# ── The walk, on a small lock ───────────────────────────────────────────────


@pytest.fixture
def small_lock(tmp_path):
    index = FakeIndex()
    plain = wheel(index, "plain", "1.0")
    nested = wheel(index, "nested", "2.0")
    only_linux = wheel(index, "only-linux", "1.0", marker="sys_platform == 'linux'")
    extra_child = wheel(index, "extra-child", "1.0")
    torch_new = wheel(index, "torch", "2.12.1+cu130", source={"registry": "https://download.pytorch.org/whl/cu130"})
    torch_old = wheel(index, "torch", "2.11.0+cu128", source={"registry": "https://download.pytorch.org/whl/cu128"})
    uv = wheel(index, "uv", "0.12.22")
    path = fake_lock(
        tmp_path / "uv.lock", common=[plain, only_linux], cu130=[torch_new], cu128=[torch_old], uv=uv,
        edges={"plain": [{"name": "nested"}, {"name": "extra-host", "extra": ["fast"]}]},
    )
    # A package whose extra pulls a child, as uv locks it.
    host = wheel(index, "extra-host", "1.0")
    text = path.read_text(encoding="utf-8")
    text += ('\n[[package]]\nname = "extra-host"\nversion = "1.0"\nsource = { registry = "https://pypi.org/simple" }\n'
             f'wheels = [{{ url = "{host.url}", hash = "sha256:{host.sha256}", size = {len(host.data)} }}]\n'
             '[package.optional-dependencies]\nfast = [{ name = "extra-child" }]\n'
             f'\n[[package]]\nname = "nested"\nversion = "2.0"\nsource = {{ registry = "https://pypi.org/simple" }}\n'
             f'wheels = [{{ url = "{nested.url}", hash = "sha256:{nested.sha256}", size = {len(nested.data)} }}]\n'
             f'\n[[package]]\nname = "extra-child"\nversion = "1.0"\nsource = {{ registry = "https://pypi.org/simple" }}\n'
             f'wheels = [{{ url = "{extra_child.url}", hash = "sha256:{extra_child.sha256}", size = {len(extra_child.data)} }}]\n')
    path.write_text(text, encoding="utf-8")
    return lockfile.load(path)


def test_the_walk_follows_markers_extras_and_the_variant(small_lock):
    windows = lockfile.engine_plan("cu130", lock=small_lock, platforms=["win_amd64"], environment=WINDOWS)
    linux = lockfile.engine_plan("cu128", lock=small_lock, platforms=LINUX_TAGS, environment=LINUX)

    assert {(item.name, item.version) for item in windows.wheels} == {
        ("plain", "1.0"), ("nested", "2.0"), ("extra-host", "1.0"), ("extra-child", "1.0"), ("torch", "2.12.1+cu130")}
    assert ("only-linux", "1.0") in {(item.name, item.version) for item in linux.wheels}
    assert ("torch", "2.11.0+cu128") in {(item.name, item.version) for item in linux.wheels}
    assert windows.identity != linux.identity
    assert windows.requirements().splitlines()[0].startswith("extra-child==1.0 --hash=sha256:")


def test_the_best_wheel_for_cpython_312_here_is_chosen():
    package = {"name": "fast", "version": "1.0", "wheels": [
        {"url": "https://files.pythonhosted.org/p/fast-1.0-py3-none-any.whl", "hash": "sha256:" + "a" * 64},
        {"url": "https://files.pythonhosted.org/p/fast-1.0-cp312-cp312-win_amd64.whl", "hash": "sha256:" + "b" * 64},
        {"url": "https://files.pythonhosted.org/p/fast-1.0-cp313-cp313-win_amd64.whl", "hash": "sha256:" + "c" * 64},
        {"url": "https://files.pythonhosted.org/p/fast-1.0-cp312-cp312-manylinux_2_28_x86_64.whl",
         "hash": "sha256:" + "d" * 64},
    ]}
    lock = {"version": 1, "package": [
        package, {"name": "uv", "version": "0.12.22", "wheels": [
            {"url": "https://files.pythonhosted.org/p/uv-0.12.22-py3-none-any.whl", "hash": "sha256:" + "e" * 64}]},
        {"name": "row-bot-learning-engine", "version": "1.0.0", "source": {"virtual": "."},
         "dependencies": [{"name": "fast"}], "optional-dependencies": {"cu130": [{"name": "fast"}]}},
    ]}

    on_windows = lockfile.engine_plan("cu130", lock=lock, platforms=["win_amd64"], environment=WINDOWS)
    on_linux = lockfile.engine_plan("cu130", lock=lock, platforms=LINUX_TAGS, environment=LINUX)

    assert on_windows.wheels[0].filename == "fast-1.0-cp312-cp312-win_amd64.whl"
    assert on_linux.wheels[0].filename == "fast-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"


def test_a_package_without_a_fitting_wheel_or_a_forbidden_one_refuses_the_plan():
    windows_only = {"version": 1, "package": [
        {"name": "native", "version": "1.0", "wheels": [
            {"url": "https://files.pythonhosted.org/p/native-1.0-cp312-cp312-win_amd64.whl",
             "hash": "sha256:" + "a" * 64}]},
        {"name": "uv", "version": "0.12.22", "wheels": [
            {"url": "https://files.pythonhosted.org/p/uv-0.12.22-py3-none-any.whl", "hash": "sha256:" + "b" * 64}]},
        {"name": "row-bot-learning-engine", "version": "1.0.0", "source": {"virtual": "."},
         "dependencies": [{"name": "native"}], "optional-dependencies": {"cu130": [{"name": "uv"}]}},
    ]}
    with pytest.raises(lockfile.LockError) as missing:
        lockfile.engine_plan("cu130", lock=windows_only, platforms=LINUX_TAGS, environment=LINUX)
    assert missing.value.code == "no_wheel"

    forbidden = {"version": 1, "package": [
        {"name": "wandb", "version": "1.0", "wheels": [
            {"url": "https://files.pythonhosted.org/p/wandb-1.0-py3-none-any.whl", "hash": "sha256:" + "a" * 64}]},
        {"name": "uv", "version": "0.12.22", "wheels": [
            {"url": "https://files.pythonhosted.org/p/uv-0.12.22-py3-none-any.whl", "hash": "sha256:" + "b" * 64}]},
        {"name": "row-bot-learning-engine", "version": "1.0.0", "source": {"virtual": "."},
         "dependencies": [{"name": "wandb"}], "optional-dependencies": {"cu130": [{"name": "uv"}]}},
    ]}
    with pytest.raises(lockfile.LockError) as refused:
        lockfile.engine_plan("cu130", lock=forbidden, platforms=["win_amd64"], environment=WINDOWS)
    assert refused.value.code == "forbidden_package"


def test_an_unknown_variant_is_refused(small_lock):
    with pytest.raises(lockfile.LockError) as unknown:
        lockfile.engine_plan("rocm", lock=small_lock)
    assert unknown.value.code == "unknown_variant"
