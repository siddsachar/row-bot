"""The pinned models Row-Bot can download to run on this computer.

**Qwen3.8 27B** is the default, at the quantization that fits the graphics card: about 4-bit for a 20 GB+
card, 3-bit for 16 GB, 2-bit for 12 GB (Unsloth's Dynamic GGUFs). Below that, or on the processor
alone, a smaller model of the generation before (**Qwen3.5 9B or 4B**) is offered, labelled as such.
Each is one GGUF file from a Hugging Face repository at an exact revision, with its size and SHA-256
(the repository's own LFS records, ``docs/LOCAL_RUNTIME.md``). :func:`fit` says how a model would run on
a computer; :func:`recommend` picks the best one that fits its graphics card.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from row_bot.local_runtime import builds
from row_bot.local_runtime.hardware import GIB, MIB, Hardware

Fit = Literal["gpu", "partial", "cpu", "too_big"]

# The context every model is served at (tokens). Row-Bot's small-window profile handles it.
CONTEXT = 32_768
# Working memory a loaded model needs besides its file and its context cache (compute buffers).
OVERHEAD_BYTES = 700 * MIB
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
# Qwen3.8 27B and Qwen3.5 27B: 16 full-attention layers x 2 x 4 key-value heads x head size 256.
_KV_27B_F16 = 16 * 2 * 4 * 256 * 2
_KV_SMALL_F16 = 8 * 2 * 4 * 256 * 2


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
    # Its context cache per token at ``kv_cache_type`` (from the model's own config: the
    # full-attention layers x 2 x key-value heads x head size x bytes per value).
    kv_bytes_per_token: int
    # The quantization in plain words ("4-bit"), shown beside the name.
    quantization: str = ""
    # The generation before the default (offered where the default doesn't fit).
    older: bool = False
    # 8-bit context cache halves its memory with no noticeable loss (the 27B builds use it).
    kv_cache_type: str = "f16"
    licence_id: str = "apache-2.0"
    context: int = CONTEXT
    # Whether it thinks unless the chat says otherwise (D3); every request says so explicitly. Every model
    # thinks for now, as its template does by default. D3 turns the 9B and 4B off (small models with thinking
    # on scored poorly in a 32K window, Learning's L2v) once lane E's local measurement confirms it.
    thinks: bool = True

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{self.file}"

    @property
    def source(self) -> str:
        return f"https://huggingface.co/{self.repo}"

    @property
    def need_bytes(self) -> int:
        """Memory it takes loaded: its file, its context's cache and working memory."""

        return self.size + self.kv_bytes_per_token * self.context + OVERHEAD_BYTES

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
                 _KV_27B_F16 // 2, quantization=label, kv_cache_type="q8_0")


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
          "03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8", 9.0, _KV_SMALL_F16,
          quantization="4-bit", older=True),
    Model("qwen3.5-4b", "Qwen3.5 4B", "unsloth/Qwen3.5-4B-GGUF", "e87f176479d0855a907a41277aca2f8ee7a09523",
          "Qwen3.5-4B-Q4_K_M.gguf", 2_740_937_888,
          "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4", 4.0, _KV_SMALL_F16,
          quantization="4-bit", older=True),
)
_BY_ID = {model.id: model for model in CATALOG}


def get(model_id: str) -> Model | None:
    return _BY_ID.get(str(model_id or ""))


def fit(model: Model, hardware: Hardware) -> Fit:
    """How ``model`` would run here: all on the graphics card, partly on it, on the processor, or not
    at all."""

    if not hardware.supported:
        return "too_big"
    need = model.need_bytes
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

    def suits(model: Model) -> bool:
        if hardware.acceleration != builds.METAL or model.older:
            return True
        return hardware.ram_bytes >= MAC_RECOMMEND_RAM.get(model.id, 0)

    on_card = next((model for model in CATALOG if fit(model, hardware) == "gpu" and suits(model)), None)
    if on_card is not None:
        return on_card
    if hardware.ram_bytes < CPU_RECOMMEND_RAM:
        return None
    largest = 9.0 if hardware.ram_bytes >= CPU_LARGER_RAM else 4.0
    return next((model for model in CATALOG if model.older and model.billions <= largest
                 and fit(model, hardware) in ("cpu", "partial")), None)


__all__ = ["CATALOG", "CONTEXT", "Fit", "Model", "fit", "get", "recommend"]
