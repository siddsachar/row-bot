"""With the stale-skills setting off (the default) the product is exactly as before: nothing is written, no prompt
text is added and Dream has no new phase. Arm N, on, adds no prompt text and no phase either; only D adds its line and
only R adds its phase."""

from __future__ import annotations

import json
import uuid

import pytest

from row_bot import stale_skills
from tests.fixtures.stale_skills import stale_world
from tests.subsystem.agents.test_turn_context import THREAD_ID, _block, env  # noqa: F401 - the shared agent fixture

pytestmark = pytest.mark.subsystem

SKILL = "1. Send one email with `mail_send` to hello@sandgate-bakery.example with the loaves.\n2. Tell Mara it's done."


@pytest.fixture
def world(tmp_path, monkeypatch, reload_for_data_dir):
    return stale_world(tmp_path, monkeypatch, reload_for_data_dir)


def _stale_scene(world) -> dict:
    """A skill naming a fact, then memory learning a newer value of it (and the usual writes around it)."""

    bakery = world.kg.save_entity("organisation", "Sandgate Bakery", "Orders go to hello@sandgate-bakery.example.")
    olu = world.kg.save_entity("person", "Olu Adeyemi", "Our treasurer.")
    world.kg.add_relation(olu["id"], bakery["id"], "works_with")
    world.skill("bread_order_procedure", SKILL, display="Bread order")
    world.later(days=19)
    world.kg.update_entity(bakery["id"], "Orders go to orders@sandgate-bakery.example from today.")
    world.skills.update_skill("bread_order_procedure", description="How the bread order is done (v2).")
    world.memory_evolution.mark_superseded(olu["id"], bakery["id"], reason="test")
    world.memory_tool._search_memory("bakery", as_of="2026-11-05")
    return bakery


def _loaded(world) -> dict:
    from row_bot.skill_discovery import build_skill_discovery_tools, collect_enabled_skill_records

    records = [item for item in collect_enabled_skill_records() if item.canonical_id == "bread_order_procedure"]
    _search, load = build_skill_discovery_tools(tuple(records), thread_id=f"t-{uuid.uuid4().hex}",
                                                context_tokens=32_768)
    return json.loads(load.invoke({"name": "bread_order_procedure"}))


def test_with_the_setting_off_nothing_is_written_and_nothing_is_offered(world, monkeypatch):
    from row_bot.skills_activation import DATA_DIR  # noqa: F401 - bound to the test's folder by the fixture

    monkeypatch.setattr("row_bot.skills_activation.STATE_PATH", world.data / "activation.json")
    tables = world.tables()
    _stale_scene(world)
    loaded = _loaded(world)

    assert world.tables() == tables  # no new table or index in the memory database
    assert not (world.data / stale_skills.FOLDER).exists()
    assert not list(world.data.glob("stale_skills*"))
    assert "date_check" not in loaded and loaded["instructions"] == SKILL
    tool = next(item for item in world.memory_tool.MemoryTool().as_langchain_tools() if item.name == "search_memory")
    assert set(tool.args) == {"query", "category"}
    assert stale_skills.operations() == []


@pytest.mark.parametrize("arm", [None, "N", "R", "F"])
def test_only_arm_d_adds_its_line_to_a_loaded_skill(world, monkeypatch, arm):
    monkeypatch.setattr("row_bot.skills_activation.STATE_PATH", world.data / "activation.json")
    world.set_arm("N")
    _stale_scene(world)
    world.set_arm(arm)
    plain = _loaded(world)
    world.set_arm("D")
    noted = _loaded(world)

    assert "date_check" not in plain
    assert noted["instructions"] == plain["instructions"] == SKILL
    assert noted["date_check"].startswith('DATE CHECK for the skill "Bread order"')


def test_the_turn_context_is_unchanged_off_and_with_arm_n_and_has_the_dated_line_with_arm_d(env, world, monkeypatch):  # noqa: F811
    from langchain_core.messages import HumanMessage

    from row_bot.skill_discovery import SkillRecord
    from row_bot.skills_activation import pin_skill

    world.set_arm("N")
    _stale_scene(world)
    record = SkillRecord(canonical_id="bread_order_procedure", alias=None, display_name="Bread order", icon="R",
                         description="", tags=(), activation={}, instructions=SKILL, source="manual", root=None)
    pin_skill(THREAD_ID, "bread_order_procedure")

    def block(arm: str | None) -> str:
        world.set_arm(arm)
        env.agent._TURN_CONTEXT_VALUES.clear()
        state = {"messages": [HumanMessage(content="Bread order please: 20 large loaves.", id="human-t1")],
                 "execution_budget": env.agent.new_execution_budget("t1")}
        env.activate("t1")
        env.agent._current_authorized_skill_records_var.set((record,))
        return _block(env.agent._pre_model_trim(state)["llm_input_messages"])

    off, arm_n, arm_d = block(None), block("N"), block("D")
    assert off == arm_n
    assert "DATE CHECK" not in off and "DATE CHECK" in arm_d
    assert [line for line in arm_d.splitlines() if "DATE CHECK" not in line] == off.splitlines()


def _dream(world, monkeypatch, reload_for_data_dir):
    (dream,) = reload_for_data_dir(world.data, "row_bot.dream_cycle")
    monkeypatch.setattr(dream, "_model_chosen", lambda: True)
    monkeypatch.setattr(dream, "_find_merge_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(dream, "_prepare_enrichment_inputs", lambda *args, **kwargs: ([], set()))
    monkeypatch.setattr(dream, "_find_cooccurring_pairs", lambda *args, **kwargs: [])
    monkeypatch.setattr(dream, "_run_insights_phase", lambda *args, **kwargs: {})
    monkeypatch.setattr(world.kg, "repair_projections", lambda **kwargs: {"complete": True})
    monkeypatch.setattr(dream, "_llm_call", lambda prompt, **kwargs: json.dumps(
        {"patch": True, "instructions": SKILL.replace("hello@", "orders@"), "reason": "new address"}))
    return dream


@pytest.mark.parametrize("min_entities", [0, 50])
def test_only_arm_r_gives_dream_its_reconcile_phase(world, monkeypatch, reload_for_data_dir, min_entities):
    dream = _dream(world, monkeypatch, reload_for_data_dir)
    (world.data / "dream_config.json").write_text(json.dumps({"min_entities": min_entities}), encoding="utf-8")
    _stale_scene(world)

    def cycle(arm: str | None) -> tuple[dict, list[str]]:
        world.set_arm(arm)
        said: list[str] = []
        summary = dream._run_dream_cycle(on_status=said.append)
        return summary, said

    shapes = {}
    for arm in (None, "N", "D", "F", "R"):
        summary, said = cycle(arm)
        shapes[arm] = (sorted(summary), [line for line in said if "Reconcil" in line])
    assert shapes[None] == shapes["N"] == shapes["D"] == shapes["F"]
    assert "reconciled_skills" not in shapes[None][0] and shapes[None][1] == []
    assert "reconciled_skills" in shapes["R"][0] and shapes["R"][1] == ["Phase 6: Reconciling skills with memory…"]
