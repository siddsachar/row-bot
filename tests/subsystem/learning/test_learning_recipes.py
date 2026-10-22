"""Training recipes (L2c): one data file per tier, read strictly; only certified recipes are ever
chosen (the 8 GB tier today, O23), so a larger card trains with the best certified one it fits."""

from __future__ import annotations

import json

import pytest

from row_bot.learning.training import hardware, recipes

pytestmark = [pytest.mark.subsystem]


def _card(mib: int, driver: str = "596.08") -> hardware.HardwareCheck:
    return hardware.classify([hardware.Gpu("NVIDIA GeForce RTX", mib, driver)])


def test_every_shipped_recipe_reads_and_only_the_8gb_tier_is_certified():
    found = {recipe.id: recipe for recipe in recipes.all_recipes()}

    assert list(found) == ["8gb", "12gb", "24gb", "32gb"]
    assert [recipe.id for recipe in found.values() if recipe.certified] == ["8gb"]
    eight = found["8gb"]
    assert eight.name == "8gb-v1" and eight.starting_model == "qwen3.5-2b"
    assert eight.max_seq_length == 16_384 and eight.serve_context == 32_768  # O23, O30
    assert (eight.quantization, eight.reasoning, eight.min_examples) == ("Q8_0", "off", 100)  # O26, O24
    assert all(recipe.quantization == "Q4_K_M" for recipe in found.values() if recipe.id in ("24gb", "32gb"))


def test_a_computer_trains_with_the_best_certified_recipe_its_card_fits():
    assert recipes.for_hardware(_card(8_188)).id == "8gb"
    assert recipes.for_hardware(_card(24_564)).id == "8gb"  # 24 GB isn't certified yet
    assert recipes.for_hardware(_card(4_096)) is None  # too small
    assert recipes.for_hardware(_card(8_188, driver="551.23")) is None  # O25: driver too old
    assert recipes.for_hardware(hardware.HardwareCheck("no_gpu")) is None
    assert recipes.for_hardware(hardware.HardwareCheck("container")) is None
    assert recipes.default().id == "8gb"


def test_steps_checkpoints_and_the_protocol_spec():
    recipe = recipes.load("8gb")

    assert recipe.rows_per_step == 4 and recipe.steps_for(154) == 39 and recipe.steps_for(0) == 1
    assert recipe.checkpoint_interval(39) == 10 and recipe.checkpoint_interval(3) == 3
    spec = recipe.spec()
    assert spec["name"] == "8gb-v1" and spec["settings"]["report_to"] == "none"
    assert spec["settings"]["max_seq_length"] == 16_384


def _text(**changes) -> str:
    data = json.loads((recipes.FOLDER / "8gb.json").read_text(encoding="utf-8"))
    for key, value in changes.items():
        if value is None:
            data.pop(key)
        else:
            data[key] = value
    return json.dumps(data)


@pytest.mark.parametrize("changes", [
    {"surprise": 1},
    {"epochs": None},
    {"epochs": 1.5},
    {"reasoning": "on"},
    {"quantization": "F16"},
    {"method": "full_finetune"},
    {"certified": "yes"},
    {"validation_share": 0.9},
    {"serve_context": 8192},
    {"settings": {"max_seq_length": 8192}},  # a certified recipe trains on at least 16K (O23)
    {"settings": {"max_seq_length": 16384, "report_to": "wandb"}},
    {"settings": {"max_seq_length": 16384, "rank": True}},
])
def test_a_recipe_file_is_read_strictly(changes):
    with pytest.raises(recipes.RecipeError) as error:
        recipes.parse(_text(**changes), source="8gb.json")
    assert error.value.code == "invalid_recipe"


def test_an_unknown_tier_is_refused():
    with pytest.raises(recipes.RecipeError) as error:
        recipes.load("64gb")
    assert error.value.code == "unknown_recipe"
