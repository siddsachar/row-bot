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

from row_bot.audit import (
    Action, Actor, ActorKind, AuditLayer, InvalidAuditEvent, chain_hash, from_dict, genesis_hash, make_link,
    new_event_id, redact, to_dict, to_ocsf,
)
from tests.contracts.audit.examples import START, example

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]


def _schema(name: str) -> Draft202012Validator:
    schema = json.loads((ROOT / "contracts" / "audit" / "v1" / f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize("action", list(Action), ids=lambda action: action.value)
def test_every_action_round_trips_and_matches_the_published_schema(action):
    event = example(action)
    data = to_dict(event)

    assert from_dict(json.loads(json.dumps(data))) == event
    assert data["category"] == action.value.split(".")[0]
    _schema("AuditEvent").validate(data)
    link = make_link(genesis_hash("org:acme"), 0, event)
    _schema("ChainLink").validate(link.to_dict())


def _tool() -> dict:
    return to_dict(example(Action.TOOL_CALLED))


def _with(action: Action, change):
    def build() -> dict:
        data = to_dict(example(action))
        change(data)
        return data
    return build


# Each changes a valid event's JSON into one that from_dict and the published schema both refuse.
REFUSED = {
    "unknown field": _with(Action.TOOL_CALLED, lambda d: d.update(extra=1)),
    "missing field": _with(Action.TOOL_CALLED, lambda d: d.pop("recorded_at")),
    "personal layer": _with(Action.TOOL_CALLED, lambda d: d.update(layer="personal")),
    "personal layer on a line move": _with(Action.LINE_ITEMS_MOVED, lambda d: d.update(layer="personal")),
    "layer the action doesn't use": _with(Action.TOOL_CALLED, lambda d: d.update(layer="company")),
    "unknown action": _with(Action.TOOL_CALLED, lambda d: d.update(action="tool.deleted")),
    "category of another action": _with(Action.TOOL_CALLED, lambda d: d.update(category="model")),
    "actor who can't act": _with(Action.TOOL_CALLED, lambda d: d["actor"].update(kind="admin")),
    "unknown actor kind": _with(Action.TOOL_CALLED, lambda d: d["actor"].update(kind="robot")),
    "extra actor field": _with(Action.TOOL_CALLED, lambda d: d["actor"].update(name="Priya")),
    "outcome the action can't have": _with(Action.MODEL_CALLED, lambda d: d.update(outcome="denied")),
    "target of the wrong kind": _with(Action.TOOL_CALLED, lambda d: d["target"].update(kind="person")),
    "missing target": _with(Action.DEVICE_REVOKED, lambda d: d.update(target=None)),
    "target where none belongs": _with(Action.MODEL_CALLED, lambda d: d.update(target={"kind": "tool", "ref": "x"})),
    "target ref that is a path": _with(Action.TOOL_CALLED, lambda d: d["target"].update(ref="C:/Users/priya/notes")),
    "decided_by on a non-approval": _with(Action.POLICY_CHANGED, lambda d: d.update(decided_by="person")),
    "approval without decided_by": _with(Action.TOOL_CALLED, lambda d: d.update(decided_by=None)),
    "unknown detail": _with(Action.MODEL_CALLED, lambda d: d["details"].update(prompt="hello")),
    "missing required detail": _with(Action.MODEL_CALLED, lambda d: d["details"].pop("input_tokens")),
    "summary at metadata": _with(Action.TOOL_CALLED, lambda d: d.update(redaction="metadata")),
    "arguments at summaries": _with(Action.TOOL_CALLED, lambda d: d.update(redaction="summaries")),
    "unknown redaction level": _with(Action.TOOL_CALLED, lambda d: d.update(redaction="strict")),
    "time with an offset": _with(Action.TOOL_CALLED, lambda d: d.update(occurred_at="2026-11-02T09:30:00.123456+00:00")),
    "time without microseconds": _with(Action.TOOL_CALLED, lambda d: d.update(occurred_at="2026-11-02T09:30:00Z")),
    "naive time": _with(Action.TOOL_CALLED, lambda d: d.update(recorded_at="2026-11-02T09:30:00.123456")),
    "sequential id": _with(Action.TOOL_CALLED, lambda d: d.update(id="42")),
    "uppercase id": _with(Action.TOOL_CALLED, lambda d: d.update(id="ABCDEF01-0000-4000-8000-000000000001")),
    "not version 4": _with(Action.TOOL_CALLED, lambda d: d.update(id="00000000-0000-1000-8000-000000000001")),
    "bad org id": _with(Action.TOOL_CALLED, lambda d: d.update(org_id="Acme Corp")),
    "NUL in a summary": _with(Action.TOOL_CALLED, lambda d: d["details"].update(summary="a\x00b")),
    "summary too long": _with(Action.TOOL_CALLED, lambda d: d["details"].update(summary="x" * 301)),
    "empty summary": _with(Action.TOOL_CALLED, lambda d: d["details"].update(summary="")),
    "float in arguments": _with(Action.TOOL_CALLED, lambda d: d["details"]["arguments"].update(scale=0.5)),
    "huge integer in arguments": _with(Action.TOOL_CALLED, lambda d: d["details"]["arguments"].update(n=2**53)),
    "arguments not an object": _with(Action.TOOL_CALLED, lambda d: d["details"].update(arguments=["a"])),
    "bool for a count": _with(Action.MODEL_CALLED, lambda d: d["details"].update(input_tokens=True)),
    "negative count": _with(Action.MODEL_CALLED, lambda d: d["details"].update(output_tokens=-1)),
    "count beyond the safe range": _with(Action.MODEL_CALLED, lambda d: d["details"].update(output_tokens=2**53)),
    "zero items moved": _with(Action.LINE_ITEMS_MOVED, lambda d: d["details"].update(count=0)),
    "notice deferred beyond 90 days": _with(Action.ESCROW_NOTICE_DEFERRED, lambda d: d["details"].update(days=91)),
    "unknown enum value": _with(Action.TOOL_CALLED, lambda d: d["details"].update(surface="email")),
    "personal export scope": _with(Action.EXPORT_STARTED, lambda d: d["details"].update(scope="personal")),
    "short hash": _with(Action.POLICY_LOADED, lambda d: d["details"].update(digest="ab" * 31)),
    "uppercase hash": _with(Action.POLICY_LOADED, lambda d: d["details"].update(digest="AB" * 32)),
    "label with a newline": _with(Action.POLICY_LOADED, lambda d: d["details"].update(policy_version="a\nb")),
    "details not an object": _with(Action.MODEL_CALLED, lambda d: d.update(details=[])),
}


@pytest.mark.parametrize("name", list(REFUSED))
def test_from_dict_and_the_schema_refuse_the_same_bad_events(name):
    data = REFUSED[name]()

    with pytest.raises(InvalidAuditEvent):
        from_dict(copy.deepcopy(data))
    assert not _schema("AuditEvent").is_valid(data), name


def test_what_the_schema_cannot_say_from_dict_still_refuses():
    bad = []
    data = _tool()
    data["target"]["ref"] = "sk-proj-abcdefghijklmnopqrstuvwx"  # A secret where an id belongs.
    bad.append(data)
    data = _tool()
    data["details"]["summary"] = "Used token=abcdefgh12345678"  # Unmasked secret text, as from another writer.
    bad.append(data)
    data = to_dict(example(Action.POLICY_LOADED))
    data["device_id"] = "dev-other"  # A device reporting as another device.
    bad.append(data)
    data = _tool()
    data["occurred_at"] = "2026-02-30T09:30:00.000000Z"  # Not a real day.
    bad.append(data)

    for data in bad:
        assert _schema("AuditEvent").is_valid(data)
        with pytest.raises(InvalidAuditEvent):
            from_dict(data)


def test_a_personal_event_cannot_be_built_or_written_out():
    assert "personal" not in {layer.value for layer in AuditLayer}
    with pytest.raises(InvalidAuditEvent):
        example(Action.LINE_ITEMS_MOVED, layer="personal")
    event = example(Action.LINE_ITEMS_MOVED)
    with pytest.raises(InvalidAuditEvent):
        dataclasses.replace(event, layer="personal")

    object.__setattr__(event, "layer", "personal")  # Forced past the frozen dataclass.

    for write_out in (to_dict, to_ocsf, lambda forced: chain_hash(genesis_hash("org:acme"), forced),
                      lambda forced: redact(forced, "metadata")):
        with pytest.raises(InvalidAuditEvent):
            write_out(event)


def test_a_detail_added_after_building_cannot_be_written_out():
    event = example(Action.MODEL_CALLED)
    object.__setattr__(event, "details", {**event.details, "prompt": "What's my salary?"})

    with pytest.raises(InvalidAuditEvent):
        to_dict(event)


def test_records_refuse_bad_values_when_built():
    with pytest.raises(InvalidAuditEvent):
        example(Action.POLICY_LOADED, device_id=None)  # A device acting names itself.
    with pytest.raises(InvalidAuditEvent):
        example(Action.TOOL_CALLED, occurred_at=datetime(2026, 11, 2, 9, 30))  # Naive.
    with pytest.raises(InvalidAuditEvent):
        example(Action.TOOL_CALLED, recorded_at=START.astimezone(timezone(timedelta(hours=1))))
    with pytest.raises(InvalidAuditEvent):
        Actor(ActorKind.PERSON, "")
    with pytest.raises(InvalidAuditEvent):
        example(Action.TOOL_CALLED, device_id="ghp_abcdefghijklmnopqrstuvwxyz0123")  # Secret-shaped id.
    details = example(Action.TOOL_CALLED).details
    for bad in ({"summary": "half a pair \ud800"}, {"arguments": {"key\x00": 1}}, {"arguments": {"n": 1.0}}):
        with pytest.raises(InvalidAuditEvent):  # Nothing that can't be hashed as RFC 8785 JSON gets in.
            example(Action.TOOL_CALLED, details={**details, **bad})


def test_event_ids_are_random_version_4_uuids():
    first, second = new_event_id(), new_event_id()

    assert first != second
    assert from_dict(to_dict(example(Action.TOOL_CALLED, id=first))).id == first


@pytest.mark.slow
def test_the_package_imports_without_the_rest_of_row_bot(tmp_path):
    probe = (
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        "before = set(sys.modules)\n"
        "import row_bot.audit\n"
        "loaded = set(sys.modules) - before\n"
        "outside = sorted(name for name in loaded if not (name == 'row_bot' or name.startswith('row_bot.audit'))\n"
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
