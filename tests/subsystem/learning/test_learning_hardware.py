"""The personal model's hardware check: what ``nvidia-smi`` reports, mapped to a recipe tier (O23).
Detection is faked: no test runs the real tool, touches the GPU or the network."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from row_bot.learning.training import hardware

pytestmark = [pytest.mark.subsystem, pytest.mark.platform]  # runs a process (faked here)

# A starting model larger than the 16 GB tier's, standing in for one the larger tiers might offer.
LARGER = hardware.StartingModel("test-larger", "A larger model", "Apache 2.0", 30_000_000_000)


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


ROOMY = hardware.SystemMemory(limit_mib=131_072, free_mib=90_000)  # never the real computer's memory


def test_an_8_gb_card_trains_with_the_certified_small_model_recipe():
    calls: list = []

    check = hardware.detect(run=_run("NVIDIA GeForce RTX 5070 Laptop GPU, 8151, 596.08\n", calls=calls),
                            which=_which(), memory=lambda: ROOMY)

    assert check.state == "gpu" and check.can_train and check.memory == ROOMY
    assert check.gpu and check.gpu.label == "NVIDIA GeForce RTX 5070 Laptop (8\u00a0GB)"
    assert check.tier and check.tier.certified and check.tier.id == "8gb"
    assert check.tier.summary == "Can train small models (up to about 2B) on your tasks."
    assert [(model.name, model.licence) for model in check.tier.models] == [("Qwen3.5 2B", "Apache 2.0")]
    assert check.larger_tiers == ()
    (args, kwargs), = calls
    assert args[0] == "/fake/bin/nvidia-smi" and "--query-gpu=name,memory.total,driver_version,memory.used" in args
    assert kwargs["timeout"] <= 15 and kwargs["check"] is False
    if sys.platform == "win32":
        # No console window flashes up from the desktop app.
        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_a_larger_card_uses_the_largest_certified_recipe_and_lists_larger_tiers_as_measured_soon():
    check = hardware.detect(
        run=_run("NVIDIA GeForce RTX 3060, 12288, 580.1\nNVIDIA GeForce RTX 5090, 32607, 580.1\n"),
        which=_which(), memory=lambda: ROOMY,
    )

    certified = [tier for tier in hardware.TIERS if tier.certified]
    assert check.state == "gpu"
    assert check.gpu and check.gpu.name == "NVIDIA GeForce RTX 5090" and check.gpu.memory_gb == 32
    assert check.tier == certified[-1]  # the largest certified tier the card fits
    assert all(not tier.certified and tier.summary.endswith("measured soon.") for tier in check.larger_tiers)
    assert [tier.id for tier in check.larger_tiers] == [tier.id for tier in hardware.TIERS
                                                         if not tier.certified and tier.min_memory_mib > check.tier.min_memory_mib]


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

    check = hardware.detect(run=run, which=_which(found), memory=lambda: ROOMY)

    assert check.state == state and not check.can_train and check.tier is None


def test_the_docker_image_says_training_is_unavailable(monkeypatch):
    monkeypatch.setenv("ROW_BOT_CONTAINERIZED", "1")  # O46: set by Row-Bot's official image
    calls: list = []

    check = hardware.detect(run=_run("NVIDIA GeForce RTX 5070 Laptop GPU, 8151, 596.08\n", calls=calls),
                            which=_which(), memory=lambda: ROOMY)

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


def test_a_large_card_that_drives_the_desktop_trains_a_smaller_tiers_recipe_beside_it():
    # The 5090 trains the 8 GB recipe until its own tier is certified: it needs 8 GB free, not 90%.
    hardware.check(refresh=True, detector=lambda: hardware.classify([hardware.Gpu("NVIDIA RTX 5090", 32607, "610")]))
    try:
        assert hardware.training_need_mib() == 8 * 1024
        assert hardware.gpu_free_for_training(memory=lambda: (5258, 32607), clock=lambda: 1.0) is True  # browsers
        assert hardware.gpu_free_for_training(memory=lambda: (25000, 32607), clock=lambda: 100.0) is False  # a model
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
    assert fresh.state == "gpu" and fresh.tier.id == "16gb"  # a 16 GB card's own (certified) tier


# ── The computer's memory (O105) ─────────────────────────────────────────────


def _tiers(monkeypatch, needs: dict[str, int]) -> None:
    """The tier table with these tiers certified, each needing this much of the computer's memory."""

    models = {"8gb": hardware.QWEN35_2B, "16gb": hardware.QWEN35_9B, "24gb": LARGER,
              "32gb": LARGER}  # a larger model per tier, so a tier held back offers something
    table = tuple(hardware.Tier(tier.id, tier.label, tier.min_memory_mib, tier.id in needs,
                                tier.summary if tier.id in needs else f"Larger models for {tier.label} cards: measured soon.",
                                (models[tier.id],) if tier.id in needs else (), needs.get(tier.id))
                  for tier in hardware.TIERS)
    monkeypatch.setattr(hardware, "TIERS", table)


def test_a_tier_the_computers_memory_cant_hold_isnt_offered_and_says_what_it_needs(monkeypatch):
    _tiers(monkeypatch, {"8gb": 15_360, "16gb": 30_720, "24gb": 40_960})
    card = [hardware.Gpu("NVIDIA GeForce RTX 5090", 32_607, "610.60")]

    # 32 GB of RAM and a small page file: 36 GB of memory in all.
    check = hardware.classify(card, hardware.SystemMemory(limit_mib=36_864, free_mib=20_000))

    assert check.state == "gpu" and check.can_train and check.tier.id == "16gb"
    assert [tier.id for tier in check.memory_short] == ["24gb"]  # the card fits it, the memory doesn't
    assert [tier.id for tier in check.larger_tiers] == ["32gb"]  # still being measured
    assert check.tier.system_memory_mib == 30_720 and check.memory.limit_gb == 36
    # Enough virtual memory: every certified tier the card fits.
    roomy = hardware.classify(card, ROOMY)
    assert roomy.tier.id == "24gb" and roomy.memory_short == ()
    # Memory that couldn't be read holds nothing back.
    assert hardware.classify(card, None).tier.id == "24gb"


def test_a_computer_whose_memory_cant_hold_even_the_smallest_tier_says_so_up_front(monkeypatch):
    _tiers(monkeypatch, {"8gb": 15_360})
    card = [hardware.Gpu("NVIDIA GeForce RTX 4060", 8_188, "596.08")]

    check = hardware.classify(card, hardware.SystemMemory(limit_mib=12_288, free_mib=6_000))

    assert check.state == "low_memory" and not check.can_train
    assert check.tier.id == "8gb" and check.tier.system_memory_mib == 15_360  # what it would need
    assert check.starting_models == () and check.variant is None


def test_training_waits_until_the_computer_has_the_memory_it_needs(monkeypatch):
    from row_bot.learning.training import recipes

    # The recipes' measured figures are Windows' (the commit limit); a system without one isn't checked.
    monkeypatch.setattr(recipes, "this_system", lambda platform=None: "linux")
    hardware.check(refresh=True, detector=lambda: hardware.classify([hardware.Gpu("NVIDIA RTX 5070", 8_151, "596")],
                                                                    ROOMY))
    try:
        assert hardware.training_system_need_mib() is None
        assert hardware.memory_free_for_training(
            reader=lambda: hardware.SystemMemory(limit_mib=131_072, free_mib=0)) is True
        monkeypatch.setattr(recipes, "this_system", lambda platform=None: "windows")
        need = hardware.training_system_need_mib()
        assert need is not None and need >= 8 * 1024  # the 8 GB recipe's measured figure for this system
        free = lambda mib: lambda: hardware.SystemMemory(limit_mib=131_072, free_mib=mib)  # noqa: E731
        assert hardware.memory_free_for_training(reader=free(need)) is True
        assert hardware.memory_free_for_training(reader=free(need - 1)) is False  # other apps hold it: wait
        assert hardware.memory_free_for_training(reader=lambda: None) is True  # unreadable: nothing to wait for
        hardware.check(refresh=True, detector=lambda: hardware.HardwareCheck("no_gpu"))
        assert hardware.memory_free_for_training(reader=free(0)) is True  # no card: nothing trains here
    finally:
        hardware.forget()


def test_the_computers_memory_is_read_from_the_system():
    meminfo = ("MemTotal:       32768000 kB\nMemFree:  100 kB\nMemAvailable:   20480000 kB\n"
               "SwapTotal: 8192000 kB\nSwapFree: 4096000 kB\n")

    linux = hardware.system_memory(platform="linux", read_text=lambda path: meminfo)

    assert linux == hardware.SystemMemory(limit_mib=(32_768_000 + 8_192_000) // 1024,
                                          free_mib=(20_480_000 + 4_096_000) // 1024)
    assert hardware.system_memory(platform="linux", read_text=lambda path: "garbage") is None
    assert hardware.system_memory(platform="darwin") is None  # nothing trains on a Mac
    real = hardware.system_memory()  # this computer's own (Windows: the commit limit; Linux: RAM and swap)
    if sys.platform == "win32" or sys.platform.startswith("linux"):
        assert real is not None and 0 < real.free_mib <= real.limit_mib
    else:
        assert real is None


# ── Room on the card (O112) ─────────────────────────────────────────────────


def _needs(monkeypatch, needs: dict[str, int]) -> None:
    """The tier table with these tiers certified, each needing this much free on the card."""

    table = tuple(hardware.Tier(tier.id, tier.label, tier.min_memory_mib, tier.id in needs, tier.summary,
                                ((hardware.QWEN35_2B,) if tier.id == "8gb" else (hardware.QWEN35_9B,))
                                if tier.id in needs else (), None, needs.get(tier.id))
                  for tier in hardware.TIERS)
    monkeypatch.setattr(hardware, "TIERS", table)


def test_the_check_recommends_the_largest_model_whose_need_fits_what_the_card_has_free(monkeypatch):
    _needs(monkeypatch, {"8gb": 8_192, "16gb": 15_616, "24gb": 20_480})
    card = lambda used: [hardware.Gpu("NVIDIA GeForce RTX 4090", 24_564, "596.08", used)]  # noqa: E731

    roomy = hardware.classify(card(1_200), ROOMY)  # 23.3 GB free
    busy = hardware.classify(card(6_000), ROOMY)  # the display and other apps hold 6 GB
    full = hardware.classify(card(17_000), ROOMY)  # 7.4 GB free: nothing fits, the smallest waits for room

    assert (roomy.tier.id, roomy.recommended.id) == ("24gb", "24gb")
    assert (busy.tier.id, busy.recommended.id) == ("24gb", "16gb")
    assert busy.starting_models[0].id == "qwen3.5-9b" and busy.gpu.free_mib == 18_564
    assert (full.recommended.id, [model.id for model in full.starting_models]) == ("8gb", ["qwen3.5-2b", "qwen3.5-9b"])
    unknown = hardware.classify([hardware.Gpu("NVIDIA GeForce RTX 4090", 24_564, "596.08")], ROOMY)
    assert unknown.recommended is None and unknown.recommended_tier.id == "24gb"  # the driver didn't say


def test_the_check_reads_what_the_card_has_in_use():
    check = hardware.detect(run=_run("NVIDIA GeForce RTX 5090, 32607, 610.60, 5120\n"), which=_which(),
                            memory=lambda: ROOMY)

    assert check.gpu.used_mib == 5_120 and check.gpu.free_mib == 27_487


def test_training_waits_for_the_recipes_measured_need_not_a_share_of_the_card():
    from row_bot.learning.training import recipes

    found = hardware.classify([hardware.Gpu("NVIDIA RTX 5090", 32_607, "610", 3_000)], ROOMY)
    hardware.check(refresh=True, detector=lambda: found)
    try:
        need = recipes.current(check=found).memory_mib
        assert hardware.training_need_mib() == need
        # Exactly the recipe's need (its measured peak with a margin), never a share of the card.
        assert hardware.gpu_free_for_training(memory=lambda: (32_607 - need, 32_607), clock=lambda: 1.0) is True
        assert hardware.gpu_free_for_training(memory=lambda: (32_607 - need + 1, 32_607), clock=lambda: 100.0) is False
        # The 2B fills an 8 GB card: it starts when the card is all but empty (160 MiB for others).
        hardware.check(refresh=True, detector=lambda: hardware.classify(
            [hardware.Gpu("NVIDIA RTX 5070 Laptop", 8_151, "596", 0)], ROOMY))
        assert hardware.gpu_free_for_training(memory=lambda: (150, 8_151), clock=lambda: 200.0) is True
        assert hardware.gpu_free_for_training(memory=lambda: (700, 8_151), clock=lambda: 300.0) is False
    finally:
        hardware.forget()
