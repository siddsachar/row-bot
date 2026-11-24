"""Run on this computer (E1): which build and which model fit a computer.

Pure: ``classify`` gets a computer's facts, ``recommend`` and ``fit`` answer for it. Nothing here
reads the real computer, downloads or starts anything.
"""

from __future__ import annotations

import dataclasses
import sys

import pytest

from row_bot.local_runtime import builds, catalog_view, models
from row_bot.local_runtime.hardware import AMD, GIB, INTEL, NVIDIA, Gpu, classify, parse_nvidia_smi

pytestmark = [pytest.mark.subsystem]


def _nvidia(gb: int, driver: str = "581.42") -> Gpu:
    return Gpu(NVIDIA, "NVIDIA GeForce RTX 4070", gb * GIB, driver)


def _pick(hardware) -> str | None:
    found = models.recommend(hardware)
    return found.id if found else None


@pytest.mark.parametrize(("gb", "expected"), [
    (24, "qwen3.8-27b-q4"), (20, "qwen3.8-27b-q4"), (16, "qwen3.8-27b-q3"), (12, "qwen3.8-27b-q2"),
    (8, "qwen3.5-9b"), (6, "qwen3.5-4b")])
def test_an_nvidia_card_gets_qwen38_27b_at_the_quantization_that_fits_else_a_smaller_older_model(gb, expected):
    hardware = classify(builds.WINDOWS_X64, 16 * GIB, [_nvidia(gb)])

    assert hardware.acceleration == builds.CUDA_13
    assert _pick(hardware) == expected
    assert models.fit(models.get(expected), hardware) == "gpu"
    assert models.get(expected).older is expected.startswith("qwen3.5")


def test_the_27b_builds_are_qwen38_and_the_small_ones_are_the_older_generation():
    big = [model for model in models.CATALOG if not model.older]
    small = [model for model in models.CATALOG if model.older]

    assert {model.name for model in big} == {"Qwen3.8 27B"}
    assert [model.quantization for model in big] == ["4-bit", "3-bit", "2-bit"]
    assert all(model.repo == "unsloth/Qwen3.8-27B-GGUF" for model in big)
    assert [model.name for model in small] == ["Qwen3.5 9B", "Qwen3.5 4B"]


def test_the_driver_decides_between_cuda_13_cuda_12_and_vulkan():
    assert classify(builds.WINDOWS_X64, 16 * GIB, [_nvidia(12, "581.57")]).acceleration == builds.CUDA_13
    assert classify(builds.LINUX_X64, 16 * GIB, [_nvidia(12, "550.54.14")]).acceleration == builds.CUDA_12
    old = classify(builds.WINDOWS_X64, 16 * GIB, [_nvidia(12, "472.12")])
    assert old.acceleration == builds.VULKAN and old.note == "driver_too_old"
    # A card the old driver still drives keeps its memory for the model.
    assert _pick(old) == "qwen3.8-27b-q2"


@pytest.mark.parametrize(("gb", "expected"), [
    (8, "qwen3.5-4b"), (16, "qwen3.5-9b"), (24, "qwen3.8-27b-q2"), (32, "qwen3.8-27b-q3"), (36, "qwen3.8-27b-q3"),
    (48, "qwen3.8-27b-q4"), (64, "qwen3.8-27b-q4")])
def test_apple_silicon_shares_its_memory_with_metal(gb, expected):
    """M1: a Mac keeps room for macOS and its apps: 16 GB gets Qwen3.5 9B, 24 GB the 27B 2-bit, 32 GB the
    3-bit, 48 GB or more the 4-bit."""
    hardware = classify(builds.MACOS_ARM64, gb * GIB, apple_silicon=True)

    assert hardware.acceleration == builds.METAL
    assert hardware.usable_gpu_bytes == int(gb * GIB * 0.70)
    assert _pick(hardware) == expected


def test_a_16_gb_mac_can_still_choose_the_27b_that_fits_its_memory():
    hardware = classify(builds.MACOS_ARM64, 16 * GIB, apple_silicon=True)

    assert models.fit(models.get("qwen3.8-27b-q2"), hardware) == "gpu"
    assert catalog_view(hardware)["recommended"] == "qwen3.5-9b"


def test_an_amd_card_runs_the_vulkan_build_and_an_integrated_one_doesnt_count():
    amd = classify(builds.LINUX_X64, 32 * GIB, [Gpu(AMD, "AMD Radeon RX 7800 XT", 16 * GIB)])
    integrated = classify(builds.WINDOWS_X64, 32 * GIB, [Gpu(INTEL, "Intel UHD Graphics", 128 * 1024**2)])

    assert amd.acceleration == builds.VULKAN and _pick(amd) == "qwen3.8-27b-q3"
    assert integrated.acceleration == builds.CPU and integrated.gpu is None


def test_without_a_card_only_a_small_older_model_is_recommended_and_only_with_enough_memory():
    large = classify(builds.WINDOWS_X64, 32 * GIB)
    medium = classify(builds.WINDOWS_X64, 16 * GIB)
    small = classify(builds.WINDOWS_X64, 8 * GIB)

    assert large.acceleration == builds.CPU and _pick(large) == "qwen3.5-9b"
    assert _pick(medium) == "qwen3.5-4b"
    assert models.fit(models.get("qwen3.8-27b-q2"), large) == "cpu"  # can still be chosen, slower
    assert _pick(small) is None
    assert models.fit(models.get("qwen3.5-4b"), small) == "cpu"  # but a small one can be chosen
    assert models.fit(models.get("qwen3.8-27b-q4"), small) == "too_big"


def test_a_card_too_small_for_a_model_runs_it_partly_on_the_processor():
    hardware = classify(builds.WINDOWS_X64, 64 * GIB, [_nvidia(12)])

    assert models.fit(models.get("qwen3.8-27b-q4"), hardware) == "partial"


def test_the_recommendation_is_never_a_model_too_big_for_the_computer():
    for ram in (4, 8, 16, 32, 64):
        for card in (None, 2, 4, 6, 8, 10, 12, 16, 20, 24, 48):
            hardware = classify(builds.LINUX_X64, ram * GIB, [_nvidia(card)] if card else [])
            pick = models.recommend(hardware)
            if pick is not None:
                assert models.fit(pick, hardware) != "too_big"


def test_a_computer_no_build_runs_on_gets_nothing():
    assert builds.platform_key("Darwin", "x86_64") is None  # Intel Mac
    assert builds.platform_key("Windows", "ARM64") is None
    hardware = classify(None, 64 * GIB, [_nvidia(24)])

    assert not hardware.supported and models.recommend(hardware) is None
    assert all(models.fit(model, hardware) == "too_big" for model in models.CATALOG)
    assert catalog_view(hardware)["supported"] is False


def test_each_computer_gets_a_pinned_build_for_its_acceleration():
    assert builds.platform_key("Windows", "AMD64") == builds.WINDOWS_X64
    assert builds.platform_key("Darwin", "arm64") == builds.MACOS_ARM64
    assert builds.platform_key("Linux", "x86_64") == builds.LINUX_X64
    cuda = builds.select_build(builds.WINDOWS_X64, builds.CUDA_13)
    assert cuda is not None and len(cuda.assets) == 2  # the build and its CUDA runtime
    assert all(asset.url.startswith("https://github.com/ggml-org/llama.cpp/releases/download/b12359/")
               for asset in cuda.assets)
    assert builds.select_build(builds.MACOS_ARM64, builds.METAL).executable == "llama-server"
    assert builds.select_build(builds.WINDOWS_X64, builds.CPU).executable == "llama-server.exe"


def test_nvidia_smi_output_is_read_card_by_card():
    cards = parse_nvidia_smi("NVIDIA GeForce RTX 4070, 12282, 581.57\nbroken line\nNVIDIA A2000, 6144, 550.10\n")

    assert [(card.name, card.memory_bytes // 1024**2, card.driver_major) for card in cards] == [
        ("NVIDIA GeForce RTX 4070", 12282, 581), ("NVIDIA A2000", 6144, 550)]


def test_the_catalog_view_reports_the_recommendation_with_each_models_fit():
    view = catalog_view(classify(builds.WINDOWS_X64, 32 * GIB, [_nvidia(12)]))

    assert view["supported"] and view["recommended"] == "qwen3.8-27b-q2"
    assert view["acceleration"] == builds.CUDA_13
    assert view["build_size"] == builds.select_build(builds.WINDOWS_X64, builds.CUDA_13).size
    assert {row["id"]: row["fit"] for row in view["models"]} == {
        "qwen3.8-27b-q4": "partial", "qwen3.8-27b-q3": "partial", "qwen3.8-27b-q2": "gpu",
        "qwen3.5-9b": "gpu", "qwen3.5-4b": "gpu"}
    assert [row["older"] for row in view["models"]] == [False, False, False, True, True]
    assert view["computer"] == "NVIDIA GeForce RTX 4070 (12 GB)"


def test_importing_the_runtime_and_reading_its_catalog_loads_nothing_of_learning():
    before = {name for name in sys.modules if name.startswith("row_bot.learning")}

    catalog_view(classify(builds.LINUX_X64, 16 * GIB))

    assert {name for name in sys.modules if name.startswith("row_bot.learning")} == before


# ── The window each model is served at (D1) ───────────────────────────────────

_MIB = 1024**2
# The published file sizes (docs/LOCAL_RUNTIME.md) and each model's context cache per token in llama.cpp's 8-bit
# type: full-attention layers (16 for the 27B, 8 for Qwen3.5 9B and 4B) x 2 x 4 key-value heads x head size 256,
# 34 bytes per block of 32 values.
_FILE = {"qwen3.8-27b-q4": 16_564_259_744, "qwen3.8-27b-q3": 13_146_393_504, "qwen3.8-27b-q2": 9_948_666_784,
         "qwen3.5-9b": 5_680_522_464, "qwen3.5-4b": 2_740_937_888}
_KV = {model_id: (16 if model_id.startswith("qwen3.8") else 8) * 2 * 4 * 256 * 34 // 32 for model_id in _FILE}
# The working memory fitted to llama.cpp b12359's buffer lines and the measured memory (RTX 5090, 22 Nov 2026;
# M4 Mac mini 16 GB, 21 Nov): 6,144 bytes a token of compute buffers, and a fixed part of 256 MiB (768 MiB for
# Qwen3.5 4B, whose output layer is its embedding table, on the card and in host memory both).
_COMPUTE = 6_144
_FIXED = {model_id: (768 if model_id == "qwen3.5-4b" else 256) * _MIB for model_id in _FILE}


def _need(model_id: str, window: int) -> int:
    """The file, the window's cache and compute buffers, and the fixed part."""
    return _FILE[model_id] + (_KV[model_id] + _COMPUTE) * window + _FIXED[model_id]


# What each model used once loaded and through prompts up to 110K (MiB): the RTX 5090's card memory over its
# baseline, and on the Mac the larger of its resident size after load and its wired memory's growth (the prompt
# cache, at most 2 GiB of ordinary host memory, is left out).
_MEASURED = [
    ("qwen3.8-27b-q4", 131_072, 20_533), ("qwen3.8-27b-q4", 65_536, 18_050), ("qwen3.8-27b-q4", 32_768, 16_802),
    ("qwen3.5-9b", 131_072, 8_320), ("qwen3.5-9b", 65_536, 6_912), ("qwen3.5-9b", 32_768, 6_208),
    ("qwen3.5-4b", 65_536, 4_650), ("qwen3.5-4b", 32_768, 3_944),
    ("qwen3.5-9b", 131_072, 8_100), ("qwen3.5-9b", 65_536, 6_726), ("qwen3.5-9b", 32_768, 6_222),
    ("qwen3.5-4b", 65_536, 4_209), ("qwen3.5-4b", 32_768, 3_635),
]


@pytest.mark.parametrize(("model_id", "window", "used_mib"), _MEASURED)
def test_the_memory_a_model_is_counted_at_covers_what_it_used_on_a_real_card_and_mac(model_id, window, used_mib):
    assert models.get(model_id).need_bytes(window) >= used_mib * _MIB
    assert models.get(model_id).need_bytes(window) == _need(model_id, window)


def _room(gb: int) -> int:
    """What a card leaves a model with llama.cpp's --fit margin (1024 MiB) kept free: the card less the 512 MiB
    the system keeps, less that margin."""
    return gb * GIB - 512 * _MIB - 1024 * _MIB


@pytest.mark.parametrize(("gb", "pick", "window"), [
    (24, "qwen3.8-27b-q4", 131_072), (20, "qwen3.8-27b-q4", 65_536), (16, "qwen3.8-27b-q3", 32_768),
    (12, "qwen3.8-27b-q2", 32_768), (10, "qwen3.5-9b", 131_072), (8, "qwen3.5-9b", 32_768),
    (6, "qwen3.5-4b", 32_768)])
def test_the_recommended_model_is_served_at_the_largest_window_that_keeps_it_on_the_card(gb, pick, window):
    """The model is picked at 32K as before; then it gets 128K, 64K or 32K, the largest that still fits on the
    card with llama.cpp's margin free, so a window never costs a quality level."""
    hardware = classify(builds.WINDOWS_X64, 32 * GIB, [_nvidia(gb)])

    assert _pick(hardware) == pick
    found = models.get(pick)
    assert models.context_for(found, hardware) == window
    assert models.fit(found, hardware) == "gpu"
    if window > 32_768:
        assert _need(pick, window) <= _room(gb)
    if window < 131_072:  # the next size up would spill off the card
        assert _need(pick, window * 2) > _room(gb)


def test_a_24_gb_card_gets_the_27b_4_bit_at_128k_and_a_16_gb_card_keeps_the_3_bit_rather_than_a_larger_window():
    large = classify(builds.WINDOWS_X64, 32 * GIB, [_nvidia(24)])
    medium = classify(builds.WINDOWS_X64, 32 * GIB, [_nvidia(16)])

    assert _need("qwen3.8-27b-q4", 131_072) <= _room(24)
    assert models.context_for(models.get("qwen3.8-27b-q4"), large) == 131_072
    # The 2-bit build would read 64K on the 16 GB card, but the pick stays the 3-bit at the window it fits.
    assert _need("qwen3.8-27b-q2", 65_536) <= _room(16) and _need("qwen3.8-27b-q3", 65_536) > _room(16)
    assert _pick(medium) == "qwen3.8-27b-q3"
    assert models.context_for(models.get("qwen3.8-27b-q3"), medium) == 32_768


def test_a_12_gb_card_keeps_todays_pick_at_32k():
    hardware = classify(builds.WINDOWS_X64, 32 * GIB, [_nvidia(12)])

    assert _pick(hardware) == "qwen3.8-27b-q2"
    assert _need("qwen3.8-27b-q2", 32_768) <= 12 * GIB - 512 * _MIB  # it fits the card at 32K, as before
    assert models.context_for(models.get("qwen3.8-27b-q2"), hardware) == 32_768


@pytest.mark.parametrize("ram", [16, 32, 64])
def test_without_a_card_every_model_stays_at_32k(ram):
    hardware = classify(builds.WINDOWS_X64, ram * GIB)

    assert {models.context_for(model, hardware) for model in models.CATALOG} == {32_768}


def test_a_model_partly_on_the_card_stays_at_32k():
    hardware = classify(builds.WINDOWS_X64, 64 * GIB, [_nvidia(12)])

    assert models.fit(models.get("qwen3.8-27b-q4"), hardware) == "partial"
    assert models.context_for(models.get("qwen3.8-27b-q4"), hardware) == 32_768


@pytest.mark.parametrize(("gb", "pick"), [
    (16, "qwen3.5-9b"), (24, "qwen3.8-27b-q2"), (32, "qwen3.8-27b-q3"), (48, "qwen3.8-27b-q4")])
def test_a_mac_serves_every_model_at_32k_even_with_memory_to_spare(gb, pick):
    """Sized by speed: a 16 GB M4 read a 110K prompt for 18 minutes before its first word."""
    hardware = classify(builds.MACOS_ARM64, gb * GIB, apple_silicon=True)

    assert _pick(hardware) == pick
    if pick == "qwen3.5-9b":  # the memory would hold 128K
        assert _need(pick, 131_072) + 1024 * _MIB <= int(gb * GIB * 0.70)
    assert {models.context_for(model, hardware) for model in models.CATALOG} == {32_768}


def test_no_model_is_served_beyond_its_native_window():
    hardware = classify(builds.WINDOWS_X64, 32 * GIB, [_nvidia(48)])
    short = dataclasses.replace(models.get("qwen3.5-4b"), native_context=65_536)

    assert models.context_for(models.get("qwen3.5-4b"), hardware) == 131_072
    assert models.context_for(short, hardware) == 65_536
