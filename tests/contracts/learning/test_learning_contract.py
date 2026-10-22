"""Learning's records: every one round-trips through its own text form, carries a
schema version with one rule for other versions, keeps lineage, fidelity and the
untrusted stamp, never labels a repaired call as the model's, and has no place
for a credential."""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any, ClassVar

import pytest

from row_bot.learning import contracts
from row_bot.learning.contracts import (
    Attempt,
    ContractError,
    Cost,
    DataSpec,
    Decision,
    Episode,
    Pin,
    Ref,
    Release,
    ReleaseIdentity,
    Signal,
    TaskFixture,
)

from tests.fixtures.learning import SHA_A, SHA_B, SHA_C, SHA_D, full_records, minimal_records

pytestmark = pytest.mark.contract

ALL = full_records() + minimal_records()


def test_every_record_type_has_a_sample():
    kinds = {record.KIND for record in full_records()}
    assert kinds == set(contracts.RECORD_TYPES)
    assert {record.KIND for record in minimal_records()} == kinds


@pytest.mark.parametrize("record", ALL, ids=lambda record: f"{record.KIND}-{record.id}")
def test_every_record_round_trips_through_its_own_form_losslessly(record):
    text = contracts.to_text(record)
    back = contracts.from_text(text)
    assert back == record
    assert contracts.to_text(back) == text  # canonical: the same bytes again
    if record.FORM == "jsonl":
        assert text.endswith("\n") and text.count("\n") == 1
    else:
        assert text.startswith("{\n") and json.loads(text)["record"] == record.KIND
    assert contracts.record_from_dict(json.loads(json.dumps(record.to_dict()))) == record
    assert contracts.from_manifest(contracts.to_manifest(record)) == record


def test_event_records_are_jsonl_and_kept_things_are_manifests():
    forms = {cls.KIND: cls.FORM for cls in contracts.RECORD_TYPES.values()}
    assert forms == {
        "episode": "jsonl",
        "decision": "jsonl",
        "signal": "jsonl",
        "test_run": "jsonl",
        "attempt": "jsonl",
        "lesson": "manifest",
        "fixture": "manifest",
        "release": "manifest",
        "job": "manifest",
    }


def test_a_jsonl_stream_of_mixed_records_round_trips():
    events = [record for record in ALL if record.FORM == "jsonl"]
    text = contracts.dumps_jsonl(events)
    assert len(text.splitlines()) == len(events)
    assert contracts.loads_jsonl(text + "\n\n") == events


def test_every_record_carries_its_kind_and_schema_version():
    for record in ALL:
        data = record.to_dict()
        assert data["record"] == record.KIND
        assert data["schema_version"] == type(record).SCHEMA_VERSION == 1


@pytest.mark.parametrize(
    ("version", "detail"),
    [(2, "newer"), (99, "newer"), (0, "retired"), (-1, "retired")],
)
def test_other_schema_versions_are_rejected_by_an_explicit_rule(version, detail):
    data = Signal(id="sig_v", kind="retry", thread_id="chat-1", created_at=1).to_dict()
    data["schema_version"] = version
    with pytest.raises(ContractError) as error:
        contracts.record_from_dict(data)
    assert error.value.code == "unsupported_schema_version"
    assert detail in error.value.detail


@pytest.mark.parametrize("version", [None, "1", 1.0, True])
def test_a_missing_or_malformed_schema_version_is_rejected(version):
    data = Signal(id="sig_v", kind="retry", thread_id="chat-1", created_at=1).to_dict()
    if version is None:
        del data["schema_version"]
    else:
        data["schema_version"] = version
    with pytest.raises(ContractError) as error:
        contracts.record_from_dict(data)
    assert error.value.code == "missing_schema_version"


def test_an_older_version_migrates_one_step_at_a_time_or_is_rejected():
    @dataclasses.dataclass(frozen=True)
    class SignalV3(Signal):
        SCHEMA_VERSION: ClassVar[int] = 3
        MIGRATIONS: ClassVar[dict] = {
            # v1 notes were called "comment"; v2 renamed them.
            1: lambda data: {**{k: v for k, v in data.items() if k != "comment"}, "note": data.get("comment")},
            2: lambda data: data,
        }

    old = {
        "record": "signal", "schema_version": 1, "id": "sig_old", "kind": "teach", "thread_id": "chat-1",
        "created_at": 1.0, "comment": "Use the ID",
    }
    migrated = SignalV3.from_dict(old)
    assert migrated.note == "Use the ID" and migrated.to_dict()["schema_version"] == 3

    @dataclasses.dataclass(frozen=True)
    class SignalGap(Signal):
        SCHEMA_VERSION: ClassVar[int] = 3
        MIGRATIONS: ClassVar[dict] = {2: lambda data: data}

    with pytest.raises(ContractError) as error:
        SignalGap.from_dict({**old, "comment": None})
    assert error.value.code == "unsupported_schema_version"
    assert "no migration" in error.value.detail


def _with(base: contracts.Record, changes: dict[str, Any]) -> dict[str, Any]:
    return {**base.to_dict(), **changes}


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"record": "feedback"}, "unknown_record_kind"),
        ({"api_key": "PRIVATE"}, "unknown_field"),
        ({"credential": "PRIVATE"}, "unknown_field"),
        ({"sequence": True}, "invalid_field"),
        ({"sequence": -1}, "invalid_field"),
        ({"created_at": "yesterday"}, "invalid_field"),
        ({"kind": "thumbs_up"}, "invalid_field"),
        ({"fidelity": "approximate"}, "invalid_field"),
        ({"id": "has space"}, "invalid_field"),
        ({"thread_id": "../escape"}, "invalid_field"),
        ({"request_blob": "not-a-hash"}, "invalid_field"),
        ({"tool_name": "x" * 257, "kind": "tool_call"}, "invalid_field"),
    ],
)
def test_invalid_records_are_rejected_clearly(changes, code):
    base = minimal_records()[1]  # a Decision
    with pytest.raises(ContractError) as error:
        contracts.record_from_dict(_with(base, changes))
    assert error.value.code == code


def test_a_missing_required_field_is_rejected():
    data = minimal_records()[0].to_dict()
    del data["thread_id"]
    with pytest.raises(ContractError) as error:
        contracts.record_from_dict(data)
    assert error.value.code == "missing_field"


def test_json_that_is_not_a_record_is_rejected():
    for text in ("not json", "[1, 2]", '{"record": "episode", "schema_version": 1, "started_at": NaN}'):
        with pytest.raises(ContractError):
            contracts.from_text(text)


def test_attempts_and_releases_keep_their_lineage():
    attempt, release = full_records()[6], full_records()[7]
    assert contracts.from_text(contracts.to_text(attempt)).parent_ids == ("att_1", "att_2")
    assert contracts.from_text(contracts.to_text(release)).parent_ids == ("rel_1",)
    with pytest.raises(ContractError):
        Attempt(id="att_x", change_kind="lesson", hypothesis="h", created_at=1, parent_ids=("att_x",))
    with pytest.raises(ContractError):
        Attempt(id="att_x", change_kind="lesson", hypothesis="h", created_at=1, parent_ids=("a", "a"))
    with pytest.raises(ContractError):
        Release(id="rel_x", kind="behaviour", version=1, created_at=1, parent_ids=("rel_x",))


def test_every_attempt_is_recorded_whether_kept_or_not_and_only_improvements_are_kept():
    for outcome in ("pending", "not_better", "worse", "unclear", "failed", "stopped", "improved"):
        Attempt(id="att_o", change_kind="training", hypothesis="h", created_at=1, outcome=outcome, kept=False)
        if outcome != "improved":
            with pytest.raises(ContractError):
                Attempt(id="att_o", change_kind="training", hypothesis="h", created_at=1, outcome=outcome,
                        kept=True)
    kept = Attempt(id="att_k", change_kind="strategy", hypothesis="h", created_at=1, outcome="improved", kept=True,
                   cost=Cost(tokens=5, gpu_seconds=1.5), reason="beat v4 on 30 fresh problems")
    assert contracts.from_text(contracts.to_text(kept)).cost == Cost(5, 1.5)


def test_fidelity_classes_and_the_untrusted_stamp():
    for fidelity in ("exact", "redacted", "partial"):
        decision = Decision(id="d", episode_id="e", thread_id="t", sequence=0, kind="model_call", created_at=1,
                            fidelity=fidelity, untrusted=True)
        back = contracts.from_text(contracts.to_text(decision))
        assert (back.fidelity, back.untrusted) == (fidelity, True)
    # A past chat is never claimed to be exact.
    with pytest.raises(ContractError):
        Episode(id="ep", thread_id="t", source="my_chat", started_at=1, historical=True)
    past = Episode(id="ep", thread_id="t", source="my_chat", started_at=1, historical=True, fidelity="partial")
    assert contracts.from_text(contracts.to_text(past)).historical is True


def test_a_repaired_call_is_never_labelled_as_model_generated():
    repaired = Decision(id="d", episode_id="e", thread_id="t", sequence=0, kind="model_call", created_at=1,
                        raw_output_blob=SHA_A, repaired_output_blob=SHA_B, executed="repaired")
    assert repaired.model_generated_blob == SHA_A
    assert contracts.from_text(contracts.to_text(repaired)).model_generated_blob == SHA_A
    # The repair can't pass as the raw output, nor be stored without saying it ran.
    bad = [
        dict(raw_output_blob=SHA_A, repaired_output_blob=SHA_B, executed="raw"),
        dict(raw_output_blob=SHA_A, repaired_output_blob=SHA_A, executed="repaired"),
        dict(raw_output_blob=SHA_A, executed="repaired"),
        dict(executed="raw"),
    ]
    for fields in bad:
        with pytest.raises(ContractError):
            Decision(id="d", episode_id="e", thread_id="t", sequence=0, kind="model_call", created_at=1, **fields)
    raw = Decision(id="d", episode_id="e", thread_id="t", sequence=0, kind="model_call", created_at=1,
                   raw_output_blob=SHA_A, executed="raw")
    assert raw.model_generated_blob == SHA_A


def test_signals_cover_teach_and_the_implicit_corrections():
    implicit = ("next_message_correction", "approval_denied", "stop", "retry")
    for kind in implicit:
        signal = Signal(id="s", kind=kind, thread_id="t", created_at=1)
        assert signal.explicit is False
        with pytest.raises(ContractError):
            Signal(id="s", kind=kind, thread_id="t", created_at=1, note="only Teach has a note")
    teach = Signal(id="s", kind="teach", thread_id="t", created_at=1, category="other")
    assert teach.explicit is True


def test_release_identity_has_room_for_every_part():
    identity = full_records()[7].identity
    assert {item.name for item in dataclasses.fields(ReleaseIdentity)} == {
        "base", "tokenizer", "template", "adapter", "quantization", "runtime", "way_of_working", "lessons",
        "data", "recipe", "tests",
    }
    assert all(getattr(identity, item.name) not in (None, ()) for item in dataclasses.fields(ReleaseIdentity))
    for kind in ("model", "behaviour", "strategy"):
        empty = Release(id="r", kind=kind, version=1, created_at=1)
        assert contracts.from_text(contracts.to_text(empty)).identity == ReleaseIdentity()


_CREDENTIAL_NAME = re.compile(
    r"(^|_)(api_?key|key|token|secret|password|passwd|credential|cookie|auth|authorization|bearer)(_|$)",
    re.IGNORECASE,
)


def _field_names(cls: type) -> set[str]:
    names: set[str] = set()
    for item in dataclasses.fields(cls):
        names.add(item.name)
    for part in (Ref, Cost, Pin, DataSpec, ReleaseIdentity):
        names.update(item.name for item in dataclasses.fields(part))
    return names


def test_no_record_has_a_field_for_a_credential():
    for cls in contracts.RECORD_TYPES.values():
        for name in _field_names(cls):
            assert not _CREDENTIAL_NAME.search(name), f"{cls.__name__}.{name}"
        for item in dataclasses.fields(cls):
            assert "dict" not in str(item.type), f"{cls.__name__}.{item.name} is a free-form mapping"


def test_blobs_named_by_a_record_are_its_content_not_its_hashes():
    decision, fixture, run, release, job = (full_records()[i] for i in (1, 4, 5, 7, 8))
    assert decision.blob_refs() == {SHA_A, SHA_B, SHA_C}  # not the tool-schema hash (SHA_D)
    assert fixture.blob_refs() == {SHA_A, SHA_B}
    assert run.blob_refs() == {SHA_C}
    assert release.blob_refs() == frozenset()  # pinned hashes are identities, not stored blobs
    assert job.blob_refs() == {SHA_D}


def test_which_conversations_own_a_record_and_which_it_was_derived_from():
    episode, decision, signal, lesson, fixture = full_records()[:5]
    assert contracts.owner_threads(episode) == ("agent-child-1", "tg_42_abc")
    assert contracts.owner_threads(decision) == ("agent-child-1", "tg_42_abc")
    assert contracts.owner_threads(signal) == ("chat-1",)
    assert contracts.owner_threads(fixture) == ("chat-1",)
    assert contracts.owner_threads(lesson) == ()
    assert contracts.derived_from_threads(lesson) == ("chat-1", "chat-2")
    pack = minimal_records()[4]
    assert contracts.owner_threads(pack) == ()
    with pytest.raises(ContractError):
        TaskFixture(id="f", origin="pack", input_blob=SHA_A, verifier="v", created_at=1, thread_id="chat-1")
    with pytest.raises(ContractError):
        TaskFixture(id="f", origin="replay", input_blob=SHA_A, verifier="v", created_at=1)


def test_a_record_too_large_to_store_is_rejected(monkeypatch):
    monkeypatch.setattr(contracts, "MAX_RECORD_BYTES", 4096)
    huge = Release(id="r", kind="model", version=1, created_at=1,
                   source_thread_ids=tuple(f"chat-{n}" for n in range(1000)))
    with pytest.raises(ContractError) as error:
        contracts.to_json(huge)
    assert error.value.code == "record_too_large"
    with pytest.raises(ContractError) as error:
        contracts.from_text(json.dumps(huge.to_dict()))
    assert error.value.code == "record_too_large"
