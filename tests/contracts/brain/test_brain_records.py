from __future__ import annotations

import copy
import dataclasses
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from row_bot.brain import (
    SCHEMA_TYPES, ChangeOp, ChangePage, Fact, InvalidRecord, ItemKind, ItemRef, Layer, Principal, RecallQuery,
    ReferenceBrain, Relation, Space, SpaceAccess, Validity, from_dict, to_dict,
)
from tests.contracts.brain.suite import (
    ACME, ALICE, ALICE_NOTES, ALICE_WORK, LEGAL, PAYMENTS, PRIYA, SPACES, START, FakeClock, fact, provenance, skill,
    source,
)

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]


def _schema(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "contracts" / "brain" / "v1" / f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _records() -> list:
    brain = ReferenceBrain(SPACES, clock=FakeClock())
    first = brain.write(PRIYA, fact(PAYMENTS, "Refunds", "Five days", actor="priya", owner_principal="priya",
                                    verified_at=START, properties={"days": 5, "tags": ["a", None]},
                                    audience=frozenset({"team-payments"})))
    desk = brain.write(PRIYA, fact(PAYMENTS, "Refund desk", "The desk", actor="priya"))
    edge = brain.write(PRIYA, Relation(space=PAYMENTS, provenance=provenance("priya"),
                                       validity=Validity(valid_from=START), source=first, relation="handled_by",
                                       target=desk))
    doc = brain.write(PRIYA, source(PAYMENTS, "refund policy", actor="priya"))
    how = brain.write(PRIYA, skill(PAYMENTS, "1.0"))
    old = brain.write(LEGAL, fact(ACME, "Refunds", "Company refunds", actor="lee"))
    brain.supersede(LEGAL, old, fact(ACME, "Refunds", "Company refunds in ten days", actor="lee"))
    items = [brain.get(PRIYA, ref) for ref in (first, edge, doc, how)] + [brain.get(LEGAL, old)]
    brain.forget(PRIYA, desk, "test")
    hits = brain.recall(RecallQuery(text="refund refunds", principal=PRIYA, include_superseded=True))
    pages = [brain.changes(PRIYA, PAYMENTS), brain.changes(PRIYA, ACME)]
    events = [event for page in pages for event in page.events]
    stored = items[0]
    return [*SPACES, PRIYA, *items, first, stored.provenance, stored.space, stored.validity, stored.lineage,
            *hits, *pages, *events,
            RecallQuery(text="refunds", principal=ALICE, spaces=frozenset({ALICE_NOTES, PAYMENTS}),
                        kinds=frozenset({ItemKind.FACT}), as_of=START, include_superseded=True)]


def _stored_fact() -> Fact:
    brain = ReferenceBrain(clock=FakeClock())
    return brain.get(ALICE, brain.write(ALICE, fact(ALICE_NOTES, "Tea", "green")))


def test_what_the_brain_returns_validates_against_the_published_schemas():
    records = _records()

    assert {type(record) for record in records} == set(SCHEMA_TYPES)
    pages = [record for record in records if isinstance(record, ChangePage)]
    assert {event.op for page in pages for event in page.events} == set(ChangeOp)
    for record in records:
        _schema(type(record).__name__).validate(to_dict(record))


def test_the_published_schemas_refuse_what_from_dict_refuses():
    good = to_dict(_stored_fact())
    validator = _schema("Fact")
    validator.validate(good)
    for change in ({"surprise": 1}, {"kind": "skill"}, {"subject": ""}, {"entity_type": "Not A Name"},
                   {"validity": {**good["validity"], "valid_from": "2026-03-02T09:00:00+02:00"}}):
        bad = {**good, **change}
        assert not validator.is_valid(bad), change
        with pytest.raises(InvalidRecord):
            from_dict(Fact, bad)


@pytest.mark.parametrize("change", [
    pytest.param(lambda data: data.update(extra=True), id="unknown field"),
    pytest.param(lambda data: data.pop("text"), id="missing field"),
    pytest.param(lambda data: data.update(kind="relation"), id="wrong kind"),
    pytest.param(lambda data: data.update(subject="x" * 201), id="text too long"),
    pytest.param(lambda data: data.update(text="null\x00byte"), id="NUL in text"),
    pytest.param(lambda data: data["provenance"].update(captured_at="2026-03-02T09:00:00"), id="naive time"),
    pytest.param(lambda data: data["provenance"].update(captured_at="2026-03-02 09:00:00Z"), id="not ISO-8601"),
    pytest.param(lambda data: data["provenance"].update(confidence=1.5), id="confidence out of range"),
    pytest.param(lambda data: data["provenance"].update(confidence=True), id="bool for a number"),
    pytest.param(lambda data: data["provenance"].update(actor="has space"), id="bad id"),
    pytest.param(lambda data: data["space"].update(layer="galaxy"), id="unknown layer"),
    pytest.param(lambda data: data.update(properties={"deep": [[[[[[[[["too deep"]]]]]]]]]}), id="JSON too deep"),
    pytest.param(lambda data: data.update(properties={"big": "x" * 17_000}), id="JSON too big"),
    pytest.param(lambda data: data.update(properties={"nan": float("nan")}), id="JSON not finite"),
    pytest.param(lambda data: data.update(audience=["g", "g"]), id="repeated set member"),
    pytest.param(lambda data: data.update(audience=[{"g": 1}]), id="set member of the wrong type"),
    pytest.param(lambda data: data.update(lineage=[]), id="list for an object"),
    pytest.param(lambda data: data["space"].update(layer=7), id="number for an enum"),
    pytest.param(lambda data: data.update(audience=["g"]), id="audience on a personal item"),
    pytest.param(lambda data: data.update(owner_principal="priya"), id="owner on a personal item"),
    pytest.param(lambda data: data["space"].update(layer="work") or data.update(portable=True),
                 id="portable outside personal"),
    pytest.param(lambda data: data.update(id=""), id="recorded without an id"),
    pytest.param(lambda data: data["validity"].update(valid_to="2026-03-01T00:00:00Z"), id="ends before it starts"),
    pytest.param(lambda data: data["validity"].update(superseded_at="2026-03-03T00:00:00Z"),
                 id="superseded_at without superseded_by"),
    pytest.param(lambda data: data["lineage"].update(derived_from=[{"kind": "fact", "id": "a"}] * 2),
                 id="repeated lineage"),
])
def test_from_dict_refuses_bad_input(change):
    data = to_dict(_stored_fact())
    from_dict(Fact, copy.deepcopy(data))  # The unchanged record is fine.
    change(data)
    with pytest.raises(InvalidRecord):
        from_dict(Fact, data)


def test_records_refuse_bad_values_when_built():
    with pytest.raises(InvalidRecord):
        fact(ALICE_NOTES, "Tea", validity=Validity(valid_from=datetime(2026, 3, 2, 9)))  # Naive.
    with pytest.raises(InvalidRecord):
        fact(ALICE_NOTES, "Tea", validity=Validity(valid_from=START.astimezone(timezone(timedelta(hours=2)))))
    with pytest.raises(InvalidRecord):
        fact(ALICE_WORK, "Tea", portable=True)
    with pytest.raises(InvalidRecord):
        fact(ALICE_NOTES, "Tea", audience=frozenset({"everyone"}))
    with pytest.raises(InvalidRecord):
        dataclasses.replace(skill(PAYMENTS, "1.0"), signature="c2ln")  # A signature names its key.
    with pytest.raises(InvalidRecord):
        SpaceAccess(space=ALICE_WORK, title="Mine", readers=frozenset({"everyone"}))
    with pytest.raises(InvalidRecord):
        ItemRef(ItemKind.FACT, "")
    with pytest.raises(InvalidRecord):
        Principal("alice", frozenset(f"g{index}" for index in range(1_025)))
    with pytest.raises(InvalidRecord):
        RecallQuery(text="tea", principal=ALICE, limit=101)
    with pytest.raises(InvalidRecord):
        Space(Layer.TEAM, "x" * 129)


def test_a_clock_that_is_not_utc_is_refused():
    brain = ReferenceBrain(clock=lambda: datetime(2026, 3, 2, 9))

    with pytest.raises(InvalidRecord):
        brain.write(ALICE, fact(ALICE_NOTES, "Tea", "green"))


@pytest.mark.slow
def test_the_package_imports_without_the_rest_of_row_bot(tmp_path):
    probe = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        "before = set(sys.modules)\n"
        "import row_bot.brain\n"
        "loaded = set(sys.modules) - before\n"
        "outside = sorted(name for name in loaded if not (name == 'row_bot' or name.startswith('row_bot.brain'))\n"
        "                 and name.split('.')[0] not in sys.stdlib_module_names)\n"
        "print(','.join(outside))\n"
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    env["ROW_BOT_DATA_DIR"] = str(tmp_path)
    done = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, env=env,
                          cwd=tmp_path, timeout=60, check=False)

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""
    assert not list(tmp_path.iterdir())  # And it wrote nothing to the data folder.
