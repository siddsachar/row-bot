"""The pinned models Row-Bot can download to run on this computer.

**Qwen3.8 27B** is the default, at the quantization that fits the graphics card: about 4-bit for a 20 GB+
card, 3-bit for 16 GB, 2-bit for 12 GB (Unsloth's Dynamic GGUFs). Below that, or on the processor
alone, a smaller model of the generation before (**Qwen3.5 9B or 4B**) is offered, labelled as such.
Each is one GGUF file from a Hugging Face repository at an exact revision, with its size and SHA-256
(the repository's own LFS records, ``docs/LOCAL_RUNTIME.md``). :func:`recommend` picks the best one that fits
the graphics card at :data:`CONTEXT`; :func:`context_for` then gives each model the largest window it still
runs at entirely on the card (D1); :func:`fit` says how a model would run on a computer at that window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from row_bot.local_runtime import builds
from row_bot.local_runtime.hardware import GIB, MIB, Hardware

Fit = Literal["gpu", "partial", "cpu", "too_big"]

# The window every model is picked at, and the least it is served at (tokens).
CONTEXT = 32_768
# The windows a model is served at, largest first (D1): the largest that still runs it entirely on the card.
CONTEXTS = (131_072, 65_536, CONTEXT)
# llama.cpp's --fit keeps this much of each card free and moves layers to the processor when a model would
# leave less (``--fit-target``, default 1024 MiB: b12359 ``common/common.h:502``). A larger window is served
# only with this much to spare, so it never moves part of the model off the card.
FIT_MARGIN_BYTES = 1024 * MIB
# Working memory besides the file and the context cache, fitted to llama.cpp b12359's own buffer lines and the
# memory measured after load and after prompts up to 110K (RTX 5090, 22 Nov 2026; M4 Mac mini 16 GB Metal,
# 21 Nov), so every measured case is predicted at or above what it used (by 170 MiB or more):
# - the compute buffers grow by 6,144 bytes a token: 5,120 on the card and 1,024 in host memory on both
#   (counted whole, since a Mac's memory is one; 9B on Metal: 305, 497 and 881 MiB at 32K, 64K and 128K);
# - a fixed part per model (``Model.fixed_bytes``): the runtime itself and the recurrent state, less the
#   embedding table llama.cpp keeps in host memory (the 27B's 682 MiB, so its file overstates the card).
COMPUTE_BYTES_PER_TOKEN = 6_144
FIXED_BYTES = 256 * MIB
# On the processor, a model may take this share of the computer's memory.
CPU_SHARE = 0.6
# Without a card that fits a model, one is recommended only with this much memory, and only a small
# one: up to 4B with 16 GB, up to 9B with 32 GB (larger ones answer too slowly on a processor).
CPU_RECOMMEND_RAM = 16 * GIB
CPU_LARGER_RAM = 32 * GIB
# Below this size a model is good for everyday chat; long tasks need a larger one.
EVERYDAY_BILLIONS = 8.0
# On a Mac the model shares memory with macOS and every app, so a 27B build is recommended only with this much
# memory (M1, the owner's table): 24 GB the 2-bit, 32 GB the 3-bit, 48 GB or more the 4-bit. A 16 GB Mac gets
# Qwen3.5 9B and stays responsive; a 27B build that fits can still be chosen.
MAC_RECOMMEND_RAM = {"qwen3.8-27b-q2": 24 * GIB, "qwen3.8-27b-q3": 32 * GIB, "qwen3.8-27b-q4": 48 * GIB}

QWEN38_REPO = "unsloth/Qwen3.8-27B-GGUF"
QWEN38_REVISION = "4af51b3542f775b0a8e4b310bfb3f17e01c306c9"
# The window Qwen3.8 27B and Qwen3.5 were trained for; no model is served at more.
NATIVE_CONTEXT = 262_144


def _kv_q8_0(full_attention_layers: int) -> int:
    """Context-cache bytes per token in llama.cpp's 8-bit type: the full-attention layers x 2 (key and value)
    x 4 key-value heads x head size 256 values, each block of 32 values taking 34 bytes (``q8_0``)."""

    return full_attention_layers * 2 * 4 * 256 * 34 // 32


# Qwen3.8 27B (Qwen3.5's architecture) has 16 full-attention layers; Qwen3.5 9B and 4B have 8.
_KV_27B = _kv_q8_0(16)
_KV_SMALL = _kv_q8_0(8)


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    repo: str
    revision: str
    file: str
    size: int
    sha256: str
    billions: float
    # Its context cache per token in 8-bit (``q8_0``, every model's cache type: :func:`_kv_q8_0`).
    kv_bytes_per_token: int
    # The quantization in plain words ("4-bit"), shown beside the name.
    quantization: str = ""
    # The generation before the default (offered where the default doesn't fit).
    older: bool = False
    licence_id: str = "apache-2.0"
    native_context: int = NATIVE_CONTEXT
    # The fixed part of its working memory (see :data:`COMPUTE_BYTES_PER_TOKEN`).
    fixed_bytes: int = FIXED_BYTES
    # Whether it thinks unless the chat says otherwise (D3); every request says so explicitly. The 27B thinks;
    # the 9B and 4B don't (lane E's local tau2 run, 23 Nov: the 9B with thinking off passed 8 points more
    # often and was 27% faster).
    thinks: bool = True

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.file}"

    @property
    def source(self) -> str:
        return f"https://huggingface.co/{self.repo}"

    def need_bytes(self, context: int = CONTEXT) -> int:
        """Memory it takes loaded at ``context``: its file, its context's cache, compute buffers that grow with
        the window and a fixed part."""

        return self.size + (self.kv_bytes_per_token + COMPUTE_BYTES_PER_TOKEN) * int(context) + self.fixed_bytes

    @property
    def everyday(self) -> bool:
        return self.billions < EVERYDAY_BILLIONS

    @property
    def label(self) -> str:
        """Its name where people see it: "Qwen3.8 27B · 3-bit" for a build of the default model, "Qwen3.5 4B"
        for a smaller older one (as Setup names them)."""

        return self.name if self.older or not self.quantization else f"{self.name} · {self.quantization}"


def _qwen38(quant: str, label: str, file: str, size: int, sha256: str) -> Model:
    return Model(f"qwen3.8-27b-{quant}", "Qwen3.8 27B", QWEN38_REPO, QWEN38_REVISION, file, size, sha256, 27.0,
                 _KV_27B, quantization=label)


# Best first: the recommendation is the first that fits the graphics card.
CATALOG: tuple[Model, ...] = (
    _qwen38("q4", "4-bit", "Qwen3.8-27B-UD-Q4_K_M.gguf", 16_564_259_744,
            "1bce504dfecd84153666928a41d8a66706f0158d3a317bc5196b72819d7dc529"),
    _qwen38("q3", "3-bit", "Qwen3.8-27B-UD-Q3_K_XL.gguf", 13_146_393_504,
            "7b20f0679da6f5c6ec7971e241630f44fe1e123e5c99bfea90c433268b355723"),
    _qwen38("q2", "2-bit", "Qwen3.8-27B-UD-Q2_K_XL.gguf", 9_948_666_784,
            "18f1187f38e7994b05c08cb45c674b90b7f17523687c83a4614fe493f7ea739d"),
    Model("qwen3.5-9b", "Qwen3.5 9B", "unsloth/Qwen3.5-9B-GGUF", "3885219b6810b007914f3a7950a8d1b469d598a5",
          "Qwen3.5-9B-Q4_K_M.gguf", 5_680_522_464,
          "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8", 9.0, _KV_SMALL,
          quantization="4-bit", older=True, thinks=False),
    Model("qwen3.5-4b", "Qwen3.5 4B", "unsloth/Qwen3.5-4B-GGUF", "e87f176479d0855a907a41277aca2f8ee7a09523",
          "Qwen3.5-4B-Q4_K_M.gguf", 2_740_937_888,
          "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4", 4.0, _KV_SMALL,
          # Its weights on the card and in host memory come to 487 MiB more than its file (llama.cpp's buffer
          # lines: 2,603.5 + 497.3 MiB): its output layer is its embedding table, held in both places.
          quantization="4-bit", older=True, fixed_bytes=768 * MIB, thinks=False),
)
_BY_ID = {model.id: model for model in CATALOG}


def get(model_id: str) -> Model | None:
    return _BY_ID.get(str(model_id or ""))


def _on_card(model: Model, hardware: Hardware, context: int, spare: int = 0) -> bool:
    card = hardware.usable_gpu_bytes
    return bool(hardware.supported and card) and model.need_bytes(context) + spare <= card


def _suits(model: Model, hardware: Hardware) -> bool:
    """On a Mac a 27B build only with the memory :data:`MAC_RECOMMEND_RAM` names; anything else anywhere."""

    if hardware.acceleration != builds.METAL or model.older:
        return True
    return hardware.ram_bytes >= MAC_RECOMMEND_RAM.get(model.id, 0)


def context_for(model: Model, hardware: Hardware) -> int:
    """The window ``model`` is served at on this computer (D1), the one value its presets, its endpoint, :func:`fit`
    and Setup all use: the largest of :data:`CONTEXTS` (never above its native window) at which it still runs
    entirely on the graphics card with llama.cpp's :data:`FIT_MARGIN_BYTES` to spare. A model that runs partly on
    the card or on the processor stays at :data:`CONTEXT`: a longer prompt there would make the first word too
    slow. The models are picked at :data:`CONTEXT` first, so a larger window never costs a quality level.

    A Mac stays at :data:`CONTEXT` too, sized by speed rather than memory: on an M4 Mac mini (16 GB, b12359)
    Qwen3.5 9B read a prompt at about 200 tokens a second, falling to 101 at 110K, so a cold first word took 58 s
    at 12K, 148 s at 27K, 407 s at 60K and 1,095 s at 110K (past the 600 s silence limit), and 128K went into
    swap. Pro and Max chips are measured before a larger window is automatic there; a person may still choose
    one (U1)."""

    if hardware.acceleration == builds.METAL or not _on_card(model, hardware, CONTEXT):
        return CONTEXT
    return next((context for context in CONTEXTS if context <= model.native_context
                 and _on_card(model, hardware, context, FIT_MARGIN_BYTES)), CONTEXT)


def fit(model: Model, hardware: Hardware) -> Fit:
    """How ``model`` would run here at :func:`context_for`'s window: all on the graphics card, partly on it, on
    the processor, or not at all (the window grows only while the model stays on the card, so it runs the
    same way at :data:`CONTEXT`)."""

    if not hardware.supported:
        return "too_big"
    need = model.need_bytes(context_for(model, hardware))
    card = hardware.usable_gpu_bytes
    if hardware.acceleration == builds.METAL:
        return "gpu" if need <= card else "too_big"
    if card and need <= card:
        return "gpu"
    cpu = int(hardware.ram_bytes * CPU_SHARE)
    if card and need <= card + cpu:
        return "partial"
    return "cpu" if need <= cpu else "too_big"


def recommend(hardware: Hardware) -> Model | None:
    """The best model that fits the graphics card (Qwen3.8 27B at the largest quantization that fits,
    else a smaller model of the generation before; on a Mac, a 27B build only with the memory
    :data:`MAC_RECOMMEND_RAM` names); without one, a small model the processor runs well on a computer
    with enough memory; else None."""

    on_card = next((model for model in CATALOG if fit(model, hardware) == "gpu" and _suits(model, hardware)),
                   None)
    if on_card is not None:
        return on_card
    if hardware.ram_bytes < CPU_RECOMMEND_RAM:
        return None
    largest = 9.0 if hardware.ram_bytes >= CPU_LARGER_RAM else 4.0
    return next((model for model in CATALOG if model.older and model.billions <= largest
                 and fit(model, hardware) in ("cpu", "partial")), None)


__all__ = ["CATALOG", "CONTEXT", "CONTEXTS", "Fit", "Model", "context_for", "fit", "get", "recommend"]
