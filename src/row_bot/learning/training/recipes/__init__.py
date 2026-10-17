"""Training recipes: one data file per hardware tier (O23, D3).

A recipe says how a personal model is trained on a tier's graphics card: the starting model,
the trainer's settings (``learning_engine/trainer.py`` clamps each to its limits), how many
passes over the examples, how often a checkpoint is saved, the fewest examples worth training
on, the context it is served at (O30), its quantization (O26) and that it is non-thinking
(O24). Only **certified** recipes are ever chosen: today the 8 GB tier (Qwen3.5 2B, bf16 LoRA
at 16K, measured in L0d and L2b). The larger tiers are listed, uncertified, until the RTX 5090
results certify them (L2c2); a larger card trains with the best certified recipe that fits it.

The files are package data (``pyproject.toml``), so every install carries them.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

FORMAT = "row-bot-recipe"
FOLDER = Path(__file__).resolve().parent
ORDER = ("8gb", "12gb", "24gb", "32gb")
METHODS = frozenset({"lora_sft"})
QUANTIZATIONS = frozenset({"Q8_0", "Q4_K_M"})
# The trainer's own settings (``trainer.DEFAULTS``); anything else in a file is refused.
SETTING_NAMES = frozenset({
    "max_seq_length", "bits", "rank", "alpha", "learning_rate", "batch_size", "gradient_accumulation",
    "warmup_steps", "weight_decay", "vram_cap", "seed",
})
# O23: Row-Bot's real envelope is 10.75K tokens, so no recipe trains on less than 16K.
MIN_TRAINING_CONTEXT = 16_384
_KEYS = frozenset({
    "format", "version", "id", "tier", "certified", "title", "starting_model", "method", "evidence", "settings",
    "epochs", "checkpoint_every", "min_examples", "min_new_examples", "validation_share", "serve_context",
    "quantization", "reasoning",
})


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

    @property
    def name(self) -> str:
        """The recipe as the training protocol names it ("8gb-v1")."""

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

    def spec(self) -> dict[str, Any]:
        """The ``recipe`` of a job spec: its name and the trainer's settings (no usage reporting)."""

        return {"name": self.name, "settings": {**dict(self.settings), "report_to": "none"}}


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
    unknown = sorted(set(data) - _KEYS)
    missing = sorted(_KEYS - set(data))
    if unknown or missing:
        raise RecipeError("invalid_recipe", f"{source} keys {unknown or missing}")
    settings = data["settings"]
    if not isinstance(settings, dict) or set(settings) - SETTING_NAMES or "max_seq_length" not in settings:
        raise RecipeError("invalid_recipe", f"{source} settings")
    for key, value in settings.items():
        _number(value, f"settings.{key}")
    for key in ("id", "tier", "title", "starting_model", "method", "evidence", "quantization", "reasoning"):
        if not isinstance(data[key], str) or not data[key] or len(data[key]) > 512:
            raise RecipeError("invalid_recipe", key)
    if data["method"] not in METHODS or data["quantization"] not in QUANTIZATIONS or data["reasoning"] != "off":
        raise RecipeError("invalid_recipe", "method, quantization or reasoning")
    if not isinstance(data["certified"], bool):
        raise RecipeError("invalid_recipe", "certified")
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
    )
    if recipe.certified and recipe.max_seq_length < MIN_TRAINING_CONTEXT:
        raise RecipeError("invalid_recipe", "a certified recipe trains on at least 16K tokens (O23)")
    if recipe.serve_context < recipe.max_seq_length:
        raise RecipeError("invalid_recipe", "served at less context than it trained on")
    return recipe


@functools.cache
def load(recipe_id: str) -> Recipe:
    """The recipe with this id, from its data file beside this module."""

    if recipe_id not in ORDER:
        raise RecipeError("unknown_recipe", recipe_id)
    path = FOLDER / f"{recipe_id}.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RecipeError("recipe_missing", recipe_id) from error
    recipe = parse(text, source=path.name)
    if recipe.id != recipe_id or recipe.tier != recipe_id:
        raise RecipeError("invalid_recipe", f"{path.name} names another tier")
    return recipe


def all_recipes() -> tuple[Recipe, ...]:
    """Every tier's recipe, smallest card first (certified or not)."""

    return tuple(load(recipe_id) for recipe_id in ORDER)


def for_hardware(check: Any) -> Recipe | None:
    """The recipe this computer trains with: its tier's when certified, else the best certified
    one its card fits (a 24 GB card trains the 8 GB recipe until 24 GB is certified). None when
    the computer can't train."""

    if check is None or not getattr(check, "can_train", False):
        return None
    tier = getattr(check, "tier", None)
    fits = [tier.id] if tier is not None else []
    fits += [item.id for item in getattr(check, "larger_tiers", ()) or ()]
    chosen: Recipe | None = None
    for recipe in all_recipes():
        if recipe.certified and (recipe.tier in fits or (tier is not None and ORDER.index(recipe.tier)
                                                         <= ORDER.index(tier.id))):
            chosen = recipe
    return chosen


def default() -> Recipe:
    """The certified 8 GB recipe (used before the hardware check has run)."""

    return load("8gb")


__all__ = [
    "FOLDER",
    "MIN_TRAINING_CONTEXT",
    "ORDER",
    "Recipe",
    "RecipeError",
    "all_recipes",
    "default",
    "for_hardware",
    "load",
    "parse",
]
