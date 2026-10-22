"""Training recipes (L2c): one data file per tier, read strictly; only certified recipes are ever
chosen (the 8 GB tier today, O23), so a larger card trains with the best certified one it fits."""

from __future__ import annotations

import json

import pytest

from row_bot.learning.training import hardware, recipes
from tests.fixtures.learning import save_learning_settings

pytestmark = [pytest.mark.subsystem]


def _card(mib: int, driver: str = "596.08") -> hardware.HardwareCheck:
    return hardware.classify([hardware.Gpu("NVIDIA GeForce RTX", mib, driver)])


def test_every_shipped_recipe_reads_and_the_measured_tiers_are_certified():
    found = {recipe.id: recipe for recipe in recipes.all_recipes()}

    assert list(found) == ["8gb", "16gb", "24gb", "32gb"]
    assert {recipe.id for recipe in found.values() if recipe.certified} == {"8gb", "16gb", "24gb", "32gb"}  # L2x
    eight = found["8gb"]
    assert eight.name == "8gb-v1" and eight.starting_model == "qwen3.5-2b"
    assert eight.max_seq_length == 16_384 and eight.serve_context == 32_768  # O23, O30
    assert (eight.quantization, eight.reasoning) == ("Q8_0", "off")  # O26, O24
    # The 16, 24 and 32 GB tiers train the 9B as QLoRA (O111, O121); a 9B Q8_0 serves comfortably there (O26).
    assert all((recipe.starting_model, recipe.settings["bits"], recipe.quantization) == ("qwen3.5-9b", 4, "Q8_0")
               for recipe in (found["16gb"], found["24gb"], found["32gb"]))
    for recipe in found.values():
        if recipe.certified:  # measured with a gradient-norm and loss check (O111), its words say so
            assert "gradient norms" in recipe.evidence and "measured on an RTX" in recipe.evidence
            # O97/O114: 90 rows in the serving shape (imported ones count once rendered, O113) is the smallest
            # set three passes were measured to help.
            assert recipe.min_examples == 90


def test_a_computer_trains_with_the_best_certified_recipe_its_card_fits():
    assert recipes.for_hardware(_card(8_188)).id == "8gb"
    assert recipes.for_hardware(_card(16_376)).id == "16gb"
    assert recipes.for_hardware(_card(24_564)).id == "24gb"
    assert recipes.for_hardware(_card(4_096)) is None  # too small
    assert recipes.for_hardware(_card(8_188, driver="551.23")) is None  # O25: driver too old
    assert recipes.for_hardware(hardware.HardwareCheck("no_gpu")) is None
    assert recipes.for_hardware(hardware.HardwareCheck("container")) is None
    assert recipes.default().id == "8gb"


def test_steps_checkpoints_and_the_protocol_spec():
    recipe = recipes.load("8gb")

    # O114: three passes over the examples (one moved nothing on 90 rows; three lifted own work).
    assert recipe.rows_per_step == 4 and recipe.steps_for(154) == 39 * 3 and recipe.steps_for(0) == 1
    assert recipe.checkpoint_interval(39) == 10 and recipe.checkpoint_interval(3) == 3
    spec = recipe.spec()
    assert spec["name"] == "8gb-v1" and spec["settings"]["report_to"] == "none"
    assert spec["settings"]["max_seq_length"] == 16_384
    assert spec["settings"]["passes"] == 3  # each pass's adapter kept, so an earlier one can be used (O119)


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
    {"system_memory_mib": None},
    {"system_memory_mib": {"macos": 16384}},  # O105: only the systems a recipe is measured on
    {"system_memory_mib": {"windows": "16 GB"}},
    {"system_memory_mib": {"windows": 512}},
    {"system_memory_mib": {"linux": 16384}},  # a certified recipe says what it needs on Windows
])
def test_a_recipe_file_is_read_strictly(changes):
    with pytest.raises(recipes.RecipeError) as error:
        recipes.parse(_text(**changes), source="8gb.json")
    assert error.value.code == "invalid_recipe"


def test_an_unknown_tier_is_refused():
    with pytest.raises(recipes.RecipeError) as error:
        recipes.load("64gb")
    assert error.value.code == "unknown_recipe"


# ── The larger tiers (L2x, O90) ──────────────────────────────────────────────


def test_every_certified_recipe_trains_a_pinned_model_its_tier_fits_and_says_what_it_needs():
    from row_bot.learning.training import weights

    tiers = {tier.id: tier for tier in hardware.TIERS}
    for recipe in recipes.all_recipes():
        tier = tiers[recipe.tier]
        assert tier.certified == recipe.certified  # the tier table reads the recipe files
        if not recipe.certified:
            assert not tier.models and tier.summary.endswith("measured soon.")
            continue
        assert recipe.starting_model in weights.MODELS and [model.id for model in tier.models] == [recipe.starting_model]
        assert recipe.max_seq_length >= recipes.MIN_TRAINING_CONTEXT and recipe.serve_context >= recipe.max_seq_length
        size_mib = int(recipe.tier.removesuffix("gb")) * 1024
        assert recipe.memory_mib <= size_mib  # measured within its tier's memory


def test_the_recipe_follows_the_starting_model_the_person_chose(data_dir):
    card = _card(32_607, driver="610.60")
    certified = recipes.certified_for(card)
    assert certified and certified[0] == recipes.for_hardware(card)  # the card's own tier first

    for model in {recipe.starting_model for recipe in certified}:
        save_learning_settings(data_dir, enabled=True, starting_model=model)
        # A smaller tier's model trains with the largest recipe for it the card fits.
        assert recipes.current(data_dir, card) == next(recipe for recipe in certified if recipe.starting_model == model)
    # A model this card has no certified recipe for (or none chosen): the card's own recipe.
    small = _card(8_188)
    save_learning_settings(data_dir, enabled=True, starting_model=certified[0].starting_model)
    assert recipes.current(data_dir, small).id == "8gb"


def test_a_recipe_says_what_it_needs_of_the_computers_memory_on_each_system():
    # O105: Windows charges what a run holds on the card to its commit limit, so its figure is measured
    # there; a system without a figure isn't checked.
    recipe = recipes.parse(_text(system_memory_mib={"windows": 15_360, "linux": 9_216}), source="8gb.json")

    assert recipe.system_memory_need("win32") == 15_360 and recipe.system_memory_need("linux") == 9_216
    assert recipe.system_memory_need("darwin") is None
    windows_only = recipes.parse(_text(system_memory_mib={"windows": 15_360}), source="8gb.json")
    assert windows_only.system_memory_need("linux") is None
    for shipped in recipes.all_recipes():
        assert not shipped.certified or shipped.system_memory_need("win32")  # every certified tier measured


def test_a_model_trains_with_the_largest_recipe_whose_need_fits_the_cards_free_memory():
    nine = [recipe for recipe in recipes.all_recipes() if recipe.certified and recipe.starting_model == "qwen3.5-9b"]
    if len(nine) < 2:
        pytest.skip("one certified recipe trains the 9B")
    small = min(nine, key=lambda recipe: recipe.memory_mib)
    large = max(nine, key=lambda recipe: (recipe.memory_mib, recipes.ORDER.index(recipe.tier)))  # the largest tier
    card = lambda used: hardware.classify([hardware.Gpu("NVIDIA GeForce RTX 5090", 32_607, "610.60", used)])  # noqa: E731

    assert recipes.for_model("qwen3.5-9b", card(1_000)) == large
    assert recipes.for_model("qwen3.5-9b", card(32_607 - large.memory_mib + 1)) != large
    assert recipes.for_model("qwen3.5-9b", card(32_607 - small.memory_mib + 1)) == small  # waits for room


def test_a_runs_cap_on_a_larger_card_is_the_recipes_need_less_the_cuda_context():
    recipe = recipes.load("8gb")

    on_8gb = recipe.spec(8_151)["settings"]["vram_cap"]
    on_32gb = recipe.spec(32_607)["settings"]["vram_cap"]

    assert on_8gb == recipe.settings["vram_cap"]  # the card it fills keeps its certified cap
    assert on_32gb == round((recipe.memory_mib - recipes.CONTEXT_MIB) / 32_607, 4)  # never more than was free
    assert recipe.spec()["settings"]["vram_cap"] == recipe.settings["vram_cap"]
