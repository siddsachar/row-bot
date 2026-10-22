"""The personal model's hardware check: what ``nvidia-smi`` reports, mapped to a recipe tier (O23).
Detection is faked: no test runs the real tool, touches the GPU or the network."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from row_bot.learning.training import hardware

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]  # runs a process (faked here)


def _run(stdout: str = "", returncode: int = 0, error: Exception | None = None, calls: list | None = None):
    def run(args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        if error is not None:
            raise error
        return SimpleNamespace(stdout=stdout, returncode=returncode)

    return run


def _which(found: bool = True):
    return lambda name: "/fake/bin/nvidia-smi" if found and name == "nvidia-smi" else None


def test_an_8_gb_card_trains_with_the_certified_small_model_recipe():
    calls: list = []

    check = hardware.detect(run=_run("NVIDIA GeForce RTX 5070 Laptop GPU, 8151, 596.08\n", calls=calls),
                            which=_which())

    assert check.state == "gpu" and check.can_train
    assert check.gpu and check.gpu.label == "NVIDIA GeForce RTX 5070 Laptop (8\u00a0GB)"
    assert check.tier and check.tier.certified and check.tier.id == "8gb"
    assert check.tier.summary == "Can train small models (up to about 2B) on your tasks."
    assert [(model.name, model.licence) for model in check.tier.models] == [("Qwen3.5 2B", "Apache 2.0")]
    assert check.larger_tiers == ()
    (args, kwargs), = calls
    assert args[0] == "/fake/bin/nvidia-smi" and "--query-gpu=name,memory.total,driver_version" in args
    assert kwargs["timeout"] <= 15 and kwargs["check"] is False
    if sys.platform == "win32":
        # No console window flashes up from the desktop app.
        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_a_larger_card_uses_the_certified_recipe_and_lists_its_own_tiers_as_measured_soon():
    check = hardware.detect(
        run=_run("NVIDIA GeForce RTX 3060, 12288, 580.1\nNVIDIA GeForce RTX 5090, 32607, 580.1\n"),
        which=_which(),
    )

    assert check.state == "gpu"
    assert check.gpu and check.gpu.name == "NVIDIA GeForce RTX 5090" and check.gpu.memory_gb == 32
    assert check.tier and check.tier.id == "8gb"
    assert [tier.id for tier in check.larger_tiers] == ["12gb", "24gb", "32gb"]
    assert all(not tier.certified and tier.summary.endswith("measured soon.") for tier in check.larger_tiers)


@pytest.mark.parametrize(
    ("run", "found", "state"),
    [
        (_run("NVIDIA GeForce GTX 1660, 6144, 560.0\n"), True, "small_gpu"),
        (_run(""), False, "no_gpu"),
        (_run("", returncode=9), True, "no_gpu"),
        (_run("garbage without memory\n"), True, "no_gpu"),
        (_run(error=subprocess.TimeoutExpired("nvidia-smi", 5)), True, "unknown"),
        (_run(error=OSError("denied")), True, "unknown"),
    ],
)
def test_without_a_card_that_fits_this_computer_cant_train(run, found, state, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # no fallback location to look in

    check = hardware.detect(run=run, which=_which(found))

    assert check.state == state and not check.can_train and check.tier is None


def test_the_docker_image_says_training_is_unavailable(monkeypatch):
    monkeypatch.setenv("ROW_BOT_CONTAINERIZED", "1")  # O46: set by Row-Bot's official image
    calls: list = []

    check = hardware.detect(run=_run("NVIDIA GeForce RTX 5070 Laptop GPU, 8151, 596.08\n", calls=calls),
                            which=_which())

    assert check.state == "container" and not check.can_train and calls == []


def test_the_cards_memory_now_is_read_from_the_driver():
    calls: list = []

    reading = hardware.gpu_memory(run=_run("2915, 8151\n", calls=calls), which=_which())

    assert reading == (2915, 8151)
    assert "--query-gpu=memory.used,memory.total" in calls[0][0]
    assert hardware.gpu_memory(run=_run("", returncode=9), which=_which()) is None
    assert hardware.gpu_memory(run=_run(error=OSError("denied")), which=_which()) is None


def test_training_waits_while_another_app_holds_the_cards_memory():
    hardware.check(refresh=True, detector=lambda: hardware.classify([hardware.Gpu("NVIDIA RTX 5070", 8151, "596")]))
    now = [100.0]
    try:
        assert hardware.gpu_free_for_training(memory=lambda: (120, 8151), clock=lambda: now[0]) is True
        assert hardware.gpu_free_for_training(memory=lambda: (2915, 8151), clock=lambda: now[0]) is False
        assert hardware.gpu_free_for_training(memory=lambda: None, clock=lambda: now[0]) is True  # unreadable
        hardware.check(refresh=True, detector=lambda: hardware.HardwareCheck("no_gpu"))
        assert hardware.gpu_free_for_training(memory=lambda: (8000, 8151)) is True  # no card to wait for
    finally:
        hardware.forget()


def test_the_check_is_kept_for_the_process_until_asked_again():
    hardware.forget()
    answers = iter([hardware.HardwareCheck("no_gpu"), hardware.classify([hardware.Gpu("NVIDIA RTX A4000", 16376)])])
    asked: list[int] = []

    def detector():
        asked.append(1)
        return next(answers)

    try:
        first = hardware.check(detector=detector)
        again = hardware.check(detector=detector)
        fresh = hardware.check(refresh=True, detector=detector)
    finally:
        hardware.forget()

    assert first.state == again.state == "no_gpu" and len(asked) == 2
    assert fresh.state == "gpu" and [tier.id for tier in fresh.larger_tiers] == ["12gb"]
