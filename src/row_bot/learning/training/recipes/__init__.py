"""Training recipes: one data file per hardware tier (O23, D3).

A recipe says how a personal model is trained on a tier's graphics card: the starting model,
the trainer's settings (``learning_engine/trainer.py`` clamps each to its limits), how many
passes over the examples (each pass's adapter is kept, so a version that can't finish a short task
falls back to the pass before, O119), how often a checkpoint is saved, the fewest examples worth training
on, the context it is served at (O30), its quantization (O26) and that it is non-thinking
(O24). Only **certified** recipes are ever chosen: the 16, 24 and 32 GB tiers (Qwen3.5 9B QLoRA at
16K, measured on the RTX 5090 in L2x); a larger card trains with the best certified recipe that fits it.
O124: the 8 GB tier (Qwen3.5 2B) went in T1, so the 16 GB recipe is the default.

**Hugging Face recipes** (C1, ``hf-9b.json``, ``hf-27b.json``, tier ``hf``) train the same way (the same trainer,
settings, passes and serving shape) in a Hugging Face job, and carry a ``remote`` block: the GPU flavour, its
price when measured, and the measured times the quote is made from (setup, seconds per step, export per pass).

The files are package data (``pyproject.toml``), so every install carries them.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

FORMAT = "row-bot-recipe"
FOLDER = Path(__file__).resolve().parent
ORDER = ("16gb", "24gb", "32gb")
# The Hugging Face recipes (C1): trained in a job in the person's own account, run on this computer after.
REMOTE = ("hf-9b", "hf-27b")
REMOTE_TIER = "hf"
# A remote block's keys (all required): the flavour, its hourly price when measured (Hugging Face's live price
# wins in the quote), the image, and the measured times the quote is made from.
REMOTE_KEYS = frozenset({"flavor", "price_per_hour", "image", "setup_seconds", "seconds_per_step",
                         "export_seconds_per_pass", "upload_seconds"})
# Optional: other single cards reviewed for the recipe (Hugging Face may have none of its own free: the run
# then waits in its queue). Each names its flavour and hourly price, and may give its own time a step;
# otherwise the main card's times stand (an estimate until measured there).
ALTERNATIVE_KEYS = frozenset({"flavor", "price_per_hour", "seconds_per_step"})
METHODS = frozenset({"lora_sft"})
QUANTIZATIONS = frozenset({"Q8_0", "Q4_K_M"})
# The trainer's own settings (``trainer.DEFAULTS``); anything else in a file is refused.
SETTING_NAMES = frozenset({
    "max_seq_length", "bits", "rank", "alpha", "learning_rate", "batch_size", "gradient_accumulation",
    "warmup_steps", "weight_decay", "vram_cap", "seed",
})
# O23: Row-Bot's real envelope is 10.75K tokens, so no recipe trains on less than 16K.
MIN_TRAINING_CONTEXT = 16_384
# What the CUDA context and its libraries hold on the card beside PyTorch's own memory (measured 410-600 MiB on
# the RTX 5090): a recipe's need on the card (``memory_mib``) includes it, PyTorch's cap doesn't (O112).
CONTEXT_MIB = 512
# O105: the systems a recipe's measured system memory is given for (Windows charges what a run holds on
# the card to its commit limit too, so its figure is larger than Linux's).
SYSTEMS = frozenset({"windows", "linux"})
_KEYS = frozenset({
    "format", "version", "id", "tier", "certified", "title", "starting_model", "method", "evidence", "settings",
    "epochs", "checkpoint_every", "min_examples", "min_new_examples", "validation_share", "serve_context",
    "quantization", "reasoning", "memory_mib", "system_memory_mib",
})


def this_system(platform: str | None = None) -> str:
    """``windows`` or ``linux`` (or the platform's own name, which no recipe measures)."""

    platform = platform or sys.platform
    return "windows" if platform == "win32" else "linux" if platform.startswith("linux") else platform


class RecipeError(ValueError):
    """A recipe file that can't be used; ``code`` is stable."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class Recipe:
    """One tier's recipe, read from its data file."""

    id: str
    version: int
    tier: str
    certified: bool
    title: str
    starting_model: str
    method: str
    evidence: str
    settings: Mapping[str, int | float]
    epochs: int
    checkpoint_every: int
    min_examples: int
    min_new_examples: int
    validation_share: float
    serve_context: int
    quantization: str
    reasoning: str
    sha256: str
    # The graphics memory a run needs free to start (its measured peak, rounded up): a card that also
    # drives the desktop trains when this much is free, not its whole tier (O91, L2x).
    memory_mib: int = 8192
    # The computer's memory a run needs free (RAM and virtual memory), per system, measured as the
    # commit it added at its peak (O105). A system without a figure isn't checked.
    system_memory_mib: Mapping[str, int] = MappingProxyType({})
    # C1: a Hugging Face recipe's job: flavour, price and measured times (None for a card tier's recipe).
    remote: Mapping[str, Any] | None = None

    @property
    def is_remote(self) -> bool:
        return self.remote is not None

    def system_memory_need(self, platform: str | None = None) -> int | None:
        """MiB of RAM and virtual memory a run needs free on this system, or None (not measured)."""

        value = self.system_memory_mib.get(this_system(platform))
        return int(value) if value else None

    @property
    def name(self) -> str:
        """The recipe as the training protocol names it ("16gb-v1")."""

        return f"{self.id}-v{self.version}"

    @property
    def max_seq_length(self) -> int:
        return int(self.settings["max_seq_length"])

    @property
    def rows_per_step(self) -> int:
        return max(1, int(self.settings.get("batch_size", 1)) * int(self.settings.get("gradient_accumulation", 1)))

    def steps_for(self, examples: int) -> int:
        """Optimizer steps for ``examples`` training rows over the recipe's epochs (at least one)."""

        return max(1, math.ceil(max(0, int(examples)) / self.rows_per_step) * self.epochs)

    def checkpoint_interval(self, steps: int) -> int:
        return max(1, min(self.checkpoint_every, int(steps)))

    def spec(self, card_mib: int | None = None) -> dict[str, Any]:
        """The ``recipe`` of a job spec: its name and the trainer's settings (no usage reporting),
        with its passes (O119).

        ``card_mib``: the card it trains on. Its cap is then at most the recipe's own need on the card,
        less the CUDA context's share (O112): training starts when the need is free, so PyTorch never holds
        more than was free (on Windows more would spill into system memory, W8). On a card the size of
        its tier (or smaller) the recipe's own certified cap stays."""

        settings = {**dict(self.settings), "report_to": "none", "passes": self.epochs}
        if card_mib and self.memory_mib and card_mib > int(self.tier.removesuffix("gb")) * 1024:
            cap = float(settings.get("vram_cap", 0.95))
            share = (self.memory_mib - CONTEXT_MIB) / float(card_mib)
            settings["vram_cap"] = round(max(0.2, min(cap, share)), 4)
        return {"name": self.name, "settings": settings}


def _number(value: Any, where: str, *, integer: bool = False, low: float = 0.0, high: float = math.inf) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise RecipeError("invalid_recipe", where)
    if integer and not isinstance(value, int):
        raise RecipeError("invalid_recipe", where)
    if not low <= value <= high:
        raise RecipeError("invalid_recipe", where)
    return value


def parse(text: str, *, source: str = "") -> Recipe:
    """A recipe from its file's text. Strict: unknown keys, wrong types and out-of-range values
    are refused, and a certified recipe must train on at least 16K tokens (O23)."""

    try:
        data = json.loads(text)
    except ValueError as error:
        raise RecipeError("invalid_recipe", f"{source} is not JSON") from error
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != 1:
        raise RecipeError("invalid_recipe", f"{source} format")
    data = dict(data)
    remote = data.pop("remote", None)
    unknown = sorted(set(data) - _KEYS)
    missing = sorted(_KEYS - set(data))
    if unknown or missing:
        raise RecipeError("invalid_recipe", f"{source} keys {unknown or missing}")
    if remote is not None:
        remote = _remote(remote, source)
    elif data.get("tier") == REMOTE_TIER:
        raise RecipeError("invalid_recipe", f"{source}: a Hugging Face recipe says where it trains")
    settings = data["settings"]
    if not isinstance(settings, dict) or set(settings) - SETTING_NAMES or "max_seq_length" not in settings:
        raise RecipeError("invalid_recipe", f"{source} settings")
    for key, value in settings.items():
        _number(value, f"settings.{key}")
    for key in ("id", "tier", "title", "starting_model", "method", "evidence", "quantization", "reasoning"):
        # The evidence says what was measured, in full (O105, O111); the other fields are short names.
        if not isinstance(data[key], str) or not data[key] or len(data[key]) > (2048 if key == "evidence" else 512):
            raise RecipeError("invalid_recipe", key)
    if data["method"] not in METHODS or data["quantization"] not in QUANTIZATIONS or data["reasoning"] != "off":
        raise RecipeError("invalid_recipe", "method, quantization or reasoning")
    if not isinstance(data["certified"], bool):
        raise RecipeError("invalid_recipe", "certified")
    system = data["system_memory_mib"]
    if not isinstance(system, dict) or set(system) - SYSTEMS:
        raise RecipeError("invalid_recipe", "system_memory_mib")
    for key, value in system.items():
        _number(value, f"system_memory_mib.{key}", integer=True, low=1024, high=1_048_576)
    if data["certified"] and "windows" not in system and remote is None:
        raise RecipeError("invalid_recipe", "a certified recipe says what it needs of the computer's memory (O105)")
    recipe = Recipe(
        id=data["id"], version=int(data["version"]), tier=data["tier"], certified=data["certified"],
        title=data["title"], starting_model=data["starting_model"], method=data["method"],
        evidence=data["evidence"], settings=MappingProxyType(dict(settings)),
        epochs=_number(data["epochs"], "epochs", integer=True, low=1, high=100),
        checkpoint_every=_number(data["checkpoint_every"], "checkpoint_every", integer=True, low=1, high=100_000),
        min_examples=_number(data["min_examples"], "min_examples", integer=True, low=1, high=1_000_000),
        min_new_examples=_number(data["min_new_examples"], "min_new_examples", integer=True, low=1, high=1_000_000),
        validation_share=_number(data["validation_share"], "validation_share", low=0.0, high=0.5),
        serve_context=_number(data["serve_context"], "serve_context", integer=True, low=MIN_TRAINING_CONTEXT,
                              high=1_048_576),
        quantization=data["quantization"], reasoning=data["reasoning"],
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        memory_mib=_number(data["memory_mib"], "memory_mib", integer=True, low=1024, high=262_144),
        system_memory_mib=MappingProxyType(dict(system)),
        remote=MappingProxyType(remote) if remote is not None else None,
    )
    if recipe.certified and recipe.max_seq_length < MIN_TRAINING_CONTEXT:
        raise RecipeError("invalid_recipe", "a certified recipe trains on at least 16K tokens (O23)")
    if recipe.serve_context < recipe.max_seq_length:
        raise RecipeError("invalid_recipe", "served at less context than it trained on")
    return recipe


def _remote(value: Any, source: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"alternatives"} != REMOTE_KEYS:
        raise RecipeError("invalid_recipe", f"{source} remote")
    alternatives = value.get("alternatives", [])
    if not isinstance(alternatives, list) or len(alternatives) > 4:
        raise RecipeError("invalid_recipe", f"{source} remote.alternatives")
    seen = {value.get("flavor")}
    for item in alternatives:
        if not isinstance(item, dict) or not {"flavor", "price_per_hour"} <= set(item) <= ALTERNATIVE_KEYS:
            raise RecipeError("invalid_recipe", f"{source} remote.alternatives")
        if not isinstance(item["flavor"], str) or not item["flavor"] or len(item["flavor"]) > 64 or \
                item["flavor"] in seen:
            raise RecipeError("invalid_recipe", f"{source} remote.alternatives.flavor")
        seen.add(item["flavor"])
        for key in set(item) - {"flavor"}:
            _number(item[key], f"remote.alternatives.{key}", low=0.0, high=1_000_000.0)
    if not isinstance(value["flavor"], str) or not value["flavor"] or len(value["flavor"]) > 64:
        raise RecipeError("invalid_recipe", f"{source} remote.flavor")
    if not isinstance(value["image"], str) or not value["image"] or len(value["image"]) > 256:
        raise RecipeError("invalid_recipe", f"{source} remote.image")
    for key in REMOTE_KEYS - {"flavor", "image"}:
        _number(value[key], f"remote.{key}", low=0.0, high=1_000_000.0)
    return dict(value)


@functools.cache
def load(recipe_id: str) -> Recipe:
    """The recipe with this id, from its data file beside this module."""

    if recipe_id not in ORDER and recipe_id not in REMOTE:
        raise RecipeError("unknown_recipe", recipe_id)
    path = FOLDER / f"{recipe_id}.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RecipeError("recipe_missing", recipe_id) from error
    recipe = parse(text, source=path.name)
    tier = REMOTE_TIER if recipe_id in REMOTE else recipe_id
    if recipe.id != recipe_id or recipe.tier != tier or (recipe_id in REMOTE) != recipe.is_remote:
        raise RecipeError("invalid_recipe", f"{path.name} names another tier")
    return recipe


def all_recipes() -> tuple[Recipe, ...]:
    """Every tier's recipe, smallest card first (certified or not)."""

    return tuple(load(recipe_id) for recipe_id in ORDER)


def for_hardware(check: Any) -> Recipe | None:
    """The recipe this computer trains with: its tier's when certified, else the best certified
    one its card fits (a 32 GB card trains the 24 GB recipe if 32 GB weren't certified). None when
    the computer can't train."""

    if check is None or not getattr(check, "can_train", False):
        return None
    # O112: the tier Row-Bot recommends (the largest whose need fits the card's free memory), else the card's.
    tier = getattr(check, "recommended", None) or getattr(check, "tier", None)
    fits = [tier.id] if tier is not None else []
    fits += [item.id for item in getattr(check, "larger_tiers", ()) or ()]
    chosen: Recipe | None = None
    for recipe in all_recipes():
        if recipe.certified and (recipe.tier in fits or (tier is not None and ORDER.index(recipe.tier)
                                                         <= ORDER.index(tier.id))):
            chosen = recipe
    return chosen


def fits(recipe: Recipe, check: Any) -> bool:
    """Whether a certified recipe trains on this computer's card (its tier, or a smaller one)."""

    if not recipe.certified or check is None or not getattr(check, "can_train", False):
        return False
    tier = getattr(check, "tier", None)
    return tier is not None and tier.id in ORDER and ORDER.index(recipe.tier) <= ORDER.index(tier.id)


def certified_for(check: Any) -> tuple[Recipe, ...]:
    """Every certified recipe this computer's card trains, largest tier first."""

    return tuple(recipe for recipe in reversed(all_recipes()) if fits(recipe, check))


def for_model(model_id: str, check: Any) -> Recipe | None:
    """The certified recipe that trains ``model_id`` on this card, or None: the largest one whose need on
    the card fits what it had free when checked (O112), else the smallest (it waits for room)."""

    found = [recipe for recipe in certified_for(check) if recipe.starting_model == model_id]
    gpu = getattr(check, "gpu", None)
    free = getattr(gpu, "free_mib", None) if gpu is not None else None
    roomy = [recipe for recipe in found if free is None or recipe.memory_mib <= free]
    return (roomy or found[-1:] or [None])[0]


def current(root: Any = None, check: Any = None) -> Recipe:
    """The recipe the person's choices train with: the one for their starting model when this card
    trains it, else the card's own (``for_hardware``), else the 16 GB recipe."""

    from row_bot import learning
    from row_bot.learning.training import hardware

    found = check if check is not None else hardware.peek()
    chosen = learning.read_settings(root).starting_model
    return for_model(chosen, found) or for_hardware(found) or default()


def default() -> Recipe:
    """The certified 16 GB recipe, the smallest (used before the hardware check has run, O124)."""

    return load("16gb")


__all__ = [
    "FOLDER",
    "certified_for",
    "current",
    "fits",
    "for_model",
    "MIN_TRAINING_CONTEXT",
    "ORDER",
    "Recipe",
    "RecipeError",
    "REMOTE",
    "REMOTE_TIER",
    "SYSTEMS",
    "all_recipes",
    "default",
    "for_hardware",
    "load",
    "parse",
    "this_system",
]
