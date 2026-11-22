"""The ``Brain`` contract suite: every brain implementation must pass it.

Subclass ``BrainContract`` in a ``test_*.py`` module and give it a ``brain_factory`` fixture, a callable
``(spaces, clock) -> Brain`` that returns an empty brain holding the given team and company ``SpaceAccess``
lists and reading time from ``clock``. ``tests/contracts/brain/test_reference_brain.py`` runs it on
``ReferenceBrain``; the SQLite (Wave 1) and Postgres (ORG-4) brains run it the same way.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

from row_bot.brain import (
    AccessDenied, ChangeOp, ChangePage, Conflict, Fact, InvalidRecord, ItemKind, ItemRef, Layer, Lineage, Owner,
    Principal, Provenance, RecallQuery, Relation, Skill, SourceDoc, SourceKind, Space, SpaceAccess, Validity,
    from_dict, to_dict,
)

START = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)

ALICE_NOTES = Space(Layer.PERSONAL, "alice")
ALICE_WORK = Space(Layer.WORK, "alice")
BOB_NOTES = Space(Layer.PERSONAL, "bob")
PAYMENTS = Space(Layer.TEAM, "payments")
ACME = Space(Layer.COMPANY, "acme")
SPACES = (
    SpaceAccess(space=PAYMENTS, title="Team Payments", readers=frozenset({"team-payments"}),
                publishers=frozenset({"payments-owners"})),
    SpaceAccess(space=ACME, title="Acme", readers=frozenset({"everyone"}), publishers=frozenset({"legal"})),
)

ALICE = Principal("alice", frozenset({"team-payments", "everyone"}))
BOB = Principal("bob", frozenset({"everyone"}))
PRIYA = Principal("priya", frozenset({"team-payments", "payments-owners", "everyone"}))
LEGAL = Principal("lee", frozenset({"legal", "everyone"}))


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: int = 1) -> datetime:
        self.now += timedelta(minutes=minutes)
        return self.now


def provenance(actor: str = "alice", kind: SourceKind = SourceKind.STATEMENT) -> Provenance:
    return Provenance(source_kind=kind, source_ref="conversation:42", captured_at=START, actor=actor,
                      confidence=0.9)


def fact(space: Space, subject: str, text: str = "", *, actor: str = "alice", **fields) -> Fact:
    fields.setdefault("validity", Validity(valid_from=START))
    return Fact(space=space, provenance=provenance(actor), subject=subject, entity_type="topic", text=text, **fields)


def source(space: Space, title: str, *, actor: str = "alice", **fields) -> SourceDoc:
    return SourceDoc(space=space, provenance=provenance(actor, SourceKind.DOCUMENT),
                     validity=Validity(valid_from=START), uri="https://docs.example.test/" + title.replace(" ", "-"),
                     title=title, content_hash="sha256:" + "ab" * 32, fetched_at=START, **fields)


def skill(space: Space, version: str, *, actor: str = "priya") -> Skill:
    return Skill(space=space, provenance=provenance(actor, SourceKind.PROMOTION), validity=Validity(valid_from=START),
                 name="refund-review", version=version, description="Review a refund request",
                 body="---\nname: refund-review\n---\nCheck the order, then the policy.\n")


def refs(hits) -> list[ItemRef]:
    return [hit.ref for hit in hits]


def replay(brain, principal: Principal, space: Space, limit: int = 100) -> list:
    """Every event of ``space`` the principal sees, following ``next_seq`` to the end."""
    events, after = [], 0
    while True:
        page = brain.changes(principal, space, after_seq=after, limit=limit)
        events.extend(page.events)
        assert page.next_seq >= after
        after = page.next_seq
        if not page.has_more:
            return events


class BrainContract:
    """The behaviour every ``Brain`` must have. Subclasses provide the ``brain_factory`` fixture."""

    @pytest.fixture
    def clock(self) -> FakeClock:
        return FakeClock()

    @pytest.fixture
    def brain(self, brain_factory, clock):
        return brain_factory(SPACES, clock)

    # ── Access ──────────────────────────────────────────────────────────────

    def test_a_person_reads_and_writes_their_own_personal_and_work_spaces(self, brain):
        notes = brain.write(ALICE, fact(ALICE_NOTES, "Coffee", "Alice likes a flat white"))
        work = brain.write(ALICE, fact(ALICE_WORK, "Refunds", "Refunds over 500 need a second look"))

        assert brain.get(ALICE, notes.ref) == notes
        assert brain.get(ALICE, work.ref) == work
        assert notes.id and notes.validity.recorded_at == START

    def test_nobody_else_reads_or_writes_a_persons_spaces(self, brain):
        notes = brain.write(ALICE, fact(ALICE_NOTES, "Coffee", "flat white"))
        work = brain.write(ALICE, fact(ALICE_WORK, "Refunds", "second look"))

        for intruder in (BOB, PRIYA, LEGAL):
            for item in (notes, work):
                with pytest.raises(AccessDenied):
                    brain.get(intruder, item.ref)
                with pytest.raises(AccessDenied):
                    brain.changes(intruder, item.space)
                with pytest.raises(AccessDenied):
                    brain.recall(RecallQuery(text="coffee refunds", principal=intruder, spaces=frozenset({item.space})))
                with pytest.raises(AccessDenied):
                    brain.forget(intruder, item.ref, "not theirs")
            with pytest.raises(AccessDenied):
                brain.write(intruder, fact(ALICE_NOTES, "Planted", "planted", actor=intruder.id))
            assert brain.recall(RecallQuery(text="coffee flat white refunds", principal=intruder)) == ()
        assert brain.get(ALICE, notes.ref) == notes

    def test_a_denial_reveals_nothing_about_existence(self, brain):
        hidden = brain.write(ALICE, fact(ALICE_NOTES, "Diagnosis", "private"))
        missing = ItemRef(ItemKind.FACT, "no-such-item")
        replacement = fact(ALICE_NOTES, "Diagnosis", "changed", actor="bob")

        def denial(call) -> str:
            with pytest.raises(AccessDenied) as caught:
                call()
            return str(caught.value)

        for ref in (hidden.ref, missing):
            assert denial(lambda: brain.get(BOB, ref)) == denial(lambda: brain.get(BOB, missing))
            assert denial(lambda: brain.forget(BOB, ref, "probe")) == denial(lambda: brain.get(BOB, missing))
            assert denial(lambda: brain.supersede(BOB, ref, replacement)) == denial(lambda: brain.get(BOB, missing))
        # A shared space nobody configured looks like one the caller may not read.
        assert denial(lambda: brain.changes(BOB, Space(Layer.TEAM, "unknown"))) == denial(
            lambda: brain.changes(BOB, PAYMENTS))

    def test_a_team_space_is_read_by_its_group_and_written_only_by_its_publishers(self, brain):
        with pytest.raises(AccessDenied):
            brain.write(ALICE, fact(PAYMENTS, "Retries", "Retry card payments three times"))
        published = brain.write(PRIYA, fact(PAYMENTS, "Retries", "Retry card payments three times", actor="priya",
                                             owner_principal="priya", verified_at=START))

        assert brain.get(ALICE, published.ref) == published
        assert refs(brain.recall(RecallQuery(text="retry card payments", principal=ALICE))) == [published.ref]
        assert [event.ref for event in replay(brain, ALICE, PAYMENTS)] == [published.ref]
        with pytest.raises(AccessDenied):
            brain.get(BOB, published.ref)
        assert brain.recall(RecallQuery(text="retry card payments", principal=BOB)) == ()

    def test_members_who_are_not_publishers_cannot_change_shared_items(self, brain):
        published = brain.write(PRIYA, fact(PAYMENTS, "Retries", "three times", actor="priya"))

        with pytest.raises(AccessDenied):
            brain.supersede(ALICE, published.ref, fact(PAYMENTS, "Retries", "five times"))
        with pytest.raises(AccessDenied):
            brain.forget(ALICE, published.ref, "disagree")
        with pytest.raises(AccessDenied):
            brain.write(BOB, fact(ACME, "Holidays", "25 days", actor="bob"))

        assert brain.get(PRIYA, published.ref) == published

    def test_an_audience_narrows_who_reads_a_company_item(self, brain):
        everyone = brain.write(LEGAL, fact(ACME, "Holidays", "Everyone gets 25 days", actor="lee"))
        narrowed = brain.write(LEGAL, fact(ACME, "Payments audit", "The payments audit is in May", actor="lee",
                                           audience=frozenset({"team-payments"})))

        assert brain.get(ALICE, narrowed.ref) == narrowed
        assert brain.get(LEGAL, narrowed.ref) == narrowed  # Publishers read all of their space.
        with pytest.raises(AccessDenied):
            brain.get(BOB, narrowed.ref)
        assert brain.get(BOB, everyone.ref) == everyone
        assert refs(brain.recall(RecallQuery(text="payments audit holidays", principal=BOB))) == [everyone.ref]
        assert {event.ref for event in replay(brain, BOB, ACME)} == {everyone.ref}
        assert {event.ref for event in replay(brain, ALICE, ACME)} == {everyone.ref, narrowed.ref}

    # ── Supersede and time travel ───────────────────────────────────────────

    def test_supersede_keeps_history_and_recall_shows_the_current_item(self, brain, clock):
        old = brain.write(ALICE, fact(ALICE_NOTES, "Coffee", "Alice drinks a flat white"))
        before = clock.advance()
        clock.advance()
        new = brain.supersede(ALICE, old.ref, fact(ALICE_NOTES, "Coffee", "Alice drinks oat flat white",
                                                   validity=Validity(valid_from=clock.now)))

        history = brain.get(ALICE, old.ref)
        assert history.validity.superseded_by == new.ref
        assert history.validity.superseded_at == clock.now
        assert brain.get(ALICE, new.ref).validity.superseded_by is None

        query = RecallQuery(text="flat white", principal=ALICE)
        assert refs(brain.recall(query)) == [new.ref]
        then = brain.recall(RecallQuery(text="flat white", principal=ALICE, as_of=before))
        assert refs(then) == [old.ref]
        assert then[0].item.validity.superseded_by is None  # As it was then.
        assert set(refs(brain.recall(RecallQuery(text="flat white", principal=ALICE, include_superseded=True)))) == {
            old.ref, new.ref}

    def test_an_item_is_superseded_once(self, brain):
        old = brain.write(ALICE, fact(ALICE_NOTES, "Coffee", "flat white"))
        brain.supersede(ALICE, old.ref, fact(ALICE_NOTES, "Coffee", "oat flat white"))

        with pytest.raises(Conflict):
            brain.supersede(ALICE, old.ref, fact(ALICE_NOTES, "Coffee", "espresso"))

    def test_supersede_keeps_kind_space_and_a_skills_name(self, brain):
        old = brain.write(ALICE, fact(ALICE_NOTES, "Coffee", "flat white"))
        first = brain.write(PRIYA, skill(PAYMENTS, "1.0"))

        with pytest.raises(InvalidRecord):
            brain.supersede(ALICE, old.ref, fact(ALICE_WORK, "Coffee", "espresso"))
        with pytest.raises(InvalidRecord):
            brain.supersede(ALICE, old.ref, source(ALICE_NOTES, "coffee"))
        with pytest.raises(InvalidRecord):
            brain.supersede(PRIYA, first.ref, dataclasses.replace(skill(PAYMENTS, "2.0"), name="other-skill"))

        second = brain.supersede(PRIYA, first.ref, skill(PAYMENTS, "2.0"))
        assert [hit.item.version for hit in brain.recall(
            RecallQuery(text="refund review", principal=ALICE, kinds=frozenset({ItemKind.SKILL})))] == ["2.0"]
        assert brain.get(ALICE, first.ref).validity.superseded_by == second.ref

    def test_valid_time_bounds_what_recall_shows(self, brain, clock):
        ends = START + timedelta(hours=1)
        leave = brain.write(ALICE, fact(ALICE_WORK, "Bob on leave", "Bob is on leave",
                                        validity=Validity(valid_from=START, valid_to=ends)))
        later = brain.write(ALICE, fact(ALICE_WORK, "Bob back", "Bob is back from leave",
                                        validity=Validity(valid_from=ends)))
        query = dict(text="bob leave", principal=ALICE)

        assert refs(brain.recall(RecallQuery(**query, as_of=START))) == [leave.ref]
        clock.advance(120)
        assert refs(brain.recall(RecallQuery(**query))) == [later.ref]
        assert set(refs(brain.recall(RecallQuery(**query, include_superseded=True)))) == {leave.ref, later.ref}

    def test_as_of_hides_what_was_recorded_later(self, brain, clock):
        first = brain.write(ALICE, fact(ALICE_NOTES, "Tea", "Alice likes green tea"))
        then = clock.advance()
        clock.advance()
        brain.write(ALICE, fact(ALICE_NOTES, "Tea", "Alice likes green tea with honey"))

        assert refs(brain.recall(RecallQuery(text="green tea", principal=ALICE, as_of=then))) == [first.ref]
        assert refs(brain.recall(RecallQuery(text="green tea", principal=ALICE, as_of=then,
                                             include_superseded=True))) == [first.ref]

    # ── Forget ──────────────────────────────────────────────────────────────

    def test_forget_retracts_everything_derived_and_leaves_only_retractions_in_the_feed(self, brain):
        doc = brain.write(ALICE, source(ALICE_WORK, "pricing sheet"))
        price = brain.write(ALICE, fact(ALICE_WORK, "Price", "Pro costs 20 a seat",
                                        lineage=Lineage(derived_from=(doc.ref,))))
        derived = brain.write(ALICE, fact(ALICE_WORK, "Discount", "Pro costs 18 a seat for schools",
                                          lineage=Lineage(derived_from=(price.ref,))))
        plan = brain.write(ALICE, fact(ALICE_WORK, "Pro plan", "The Pro plan"))
        edge = brain.write(ALICE, Relation(space=ALICE_WORK, provenance=provenance(),
                                           validity=Validity(valid_from=START), source=price.ref,
                                           relation="price_of", target=plan.ref))
        kept = brain.write(ALICE, fact(ALICE_WORK, "Seats", "Pro seats are monthly"))

        retracted = brain.forget(ALICE, doc.ref, "the sheet was withdrawn")

        assert retracted[0] == doc.ref
        assert set(retracted) == {doc.ref, price.ref, derived.ref, edge.ref}
        for ref in retracted:
            with pytest.raises(AccessDenied):
                brain.get(ALICE, ref)
        assert brain.get(ALICE, plan.ref) == plan
        assert set(refs(brain.recall(RecallQuery(text="pro costs seat pricing sheet", principal=ALICE,
                                                 include_superseded=True, as_of=START)))) == {plan.ref, kept.ref}
        events = replay(brain, ALICE, ALICE_WORK)
        assert {event.ref for event in events if event.op is ChangeOp.RETRACT} == set(retracted)
        assert all(event.item is None for event in events if event.op is ChangeOp.RETRACT)
        assert not [event for event in events if event.op is ChangeOp.UPSERT and event.ref in set(retracted)]

    def test_forget_crosses_spaces_but_reports_only_what_the_caller_may_read(self, brain):
        # Promotion cuts lineage into personal spaces (ORG-6); between shared spaces, lineage stays.
        mine = brain.write(ALICE, fact(ALICE_NOTES, "Recipe", "Alice's sourdough recipe"))
        theirs = brain.write(BOB, fact(BOB_NOTES, "Recipe", "Bob's copy of Alice's sourdough",
                                       actor="bob", lineage=Lineage(derived_from=(mine.ref,))))
        team = brain.write(PRIYA, fact(PAYMENTS, "Refunds", "Refunds take five days", actor="priya"))
        company = brain.write(LEGAL, fact(ACME, "Refunds", "Legal's note on refund timing", actor="lee",
                                          audience=frozenset({"legal"}), lineage=Lineage(derived_from=(team.ref,))))

        assert brain.forget(ALICE, mine.ref, "personal data") == (mine.ref,)
        assert brain.forget(PRIYA, team.ref, "wrong") == (team.ref,)

        for principal, ref in ((BOB, theirs.ref), (LEGAL, company.ref)):
            with pytest.raises(AccessDenied):
                brain.get(principal, ref)
        assert [event.op for event in replay(brain, BOB, BOB_NOTES)] == [ChangeOp.RETRACT]
        assert [event.op for event in replay(brain, LEGAL, ACME)] == [ChangeOp.RETRACT]
        assert replay(brain, PRIYA, ACME) == []

    def test_forget_needs_write_access_and_a_reason(self, brain):
        item = brain.write(PRIYA, fact(PAYMENTS, "Retries", "three times", actor="priya"))

        with pytest.raises(AccessDenied):
            brain.forget(ALICE, item.ref, "not mine to forget")
        with pytest.raises(InvalidRecord):
            brain.forget(PRIYA, item.ref, "")
        with pytest.raises(InvalidRecord):
            brain.forget(PRIYA, item.ref, "x" * 501)
        assert brain.get(ALICE, item.ref) == item

    # ── Change feed ─────────────────────────────────────────────────────────

    def test_changes_are_ordered_and_page_without_gaps_or_repeats(self, brain, clock):
        written = []
        for index in range(7):
            written.append(brain.write(ALICE, fact(ALICE_NOTES, f"Note {index}", "text")).ref)
            clock.advance()

        pages, after = [], 0
        while True:
            page = brain.changes(ALICE, ALICE_NOTES, after_seq=after, limit=3)
            assert isinstance(page, ChangePage) and page.space == ALICE_NOTES
            pages.append(page)
            after = page.next_seq
            if not page.has_more:
                break
        events = [event for page in pages for event in page.events]

        assert [len(page.events) for page in pages] == [3, 3, 1]
        assert [event.ref for event in events] == written
        assert all(earlier.seq < later.seq for earlier, later in zip(events, events[1:]))
        assert all(event.op is ChangeOp.UPSERT and event.item.ref == event.ref for event in events)
        tail = brain.changes(ALICE, ALICE_NOTES, after_seq=after)
        assert tail.events == () and tail.next_seq == after and not tail.has_more

    def test_supersede_publishes_both_items(self, brain, clock):
        old = brain.write(ALICE, fact(ALICE_NOTES, "Coffee", "flat white"))
        cursor = brain.changes(ALICE, ALICE_NOTES).next_seq
        clock.advance()
        new = brain.supersede(ALICE, old.ref, fact(ALICE_NOTES, "Coffee", "espresso"))

        events = brain.changes(ALICE, ALICE_NOTES, after_seq=cursor).events
        assert {event.ref: event.item for event in events} == {new.ref: new, old.ref: brain.get(ALICE, old.ref)}
        assert all(event.at == clock.now for event in events)

    def test_a_principal_who_loses_a_group_loses_the_space_and_its_items(self, brain):
        team = brain.write(PRIYA, fact(PAYMENTS, "Retries", "Retry card payments three times", actor="priya"))
        narrowed = brain.write(LEGAL, fact(ACME, "Audit", "The payments audit is in May", actor="lee",
                                           audience=frozenset({"team-payments"})))
        assert [event.ref for event in replay(brain, ALICE, PAYMENTS)] == [team.ref]

        moved = Principal("alice", frozenset({"everyone"}))  # Alice left Team Payments.

        with pytest.raises(AccessDenied):
            brain.changes(moved, PAYMENTS)
        with pytest.raises(AccessDenied):
            brain.get(moved, team.ref)
        with pytest.raises(AccessDenied):
            brain.get(moved, narrowed.ref)
        assert brain.recall(RecallQuery(text="retry payments audit", principal=moved)) == ()
        assert replay(brain, moved, ACME) == []
        brain.forget(LEGAL, narrowed.ref, "audit moved")
        assert replay(brain, moved, ACME) == []  # Not even the retraction of what they can no longer see.
        assert [event.op for event in replay(brain, ALICE, ACME)] == [ChangeOp.RETRACT]

    def test_hidden_events_still_move_the_cursor(self, brain):
        for index in range(4):
            brain.write(LEGAL, fact(ACME, f"Legal memo {index}", "privileged", actor="lee",
                                    audience=frozenset({"legal"})))
        shown = brain.write(LEGAL, fact(ACME, "Holidays", "25 days", actor="lee"))

        events, after, calls = [], 0, 0
        while True:
            calls += 1
            page = brain.changes(BOB, ACME, after_seq=after, limit=2)
            events.extend(page.events)
            assert page.next_seq >= after
            after = page.next_seq
            if not page.has_more:
                break
        assert [event.ref for event in events] == [shown.ref]
        assert calls <= 5

    # ── Records ─────────────────────────────────────────────────────────────

    def test_every_record_the_brain_returns_round_trips(self, brain):
        doc = brain.write(PRIYA, source(PAYMENTS, "refund policy", actor="priya",
                                        audience=frozenset({"team-payments"})))
        first = brain.write(PRIYA, fact(PAYMENTS, "Refunds", "Refunds take five days", actor="priya",
                                        owner_principal="priya", verified_at=START,
                                        properties={"days": 5, "tags": ["refund", None, 1.5], "nested": {"ok": True}},
                                        lineage=Lineage(derived_from=(doc.ref,))))
        second = brain.write(PRIYA, fact(PAYMENTS, "Refund desk", "The refund desk", actor="priya"))
        edge = brain.write(PRIYA, Relation(space=PAYMENTS, provenance=provenance("priya"),
                                           validity=Validity(valid_from=START), source=first.ref,
                                           relation="handled_by", target=second.ref))
        how = brain.write(PRIYA, skill(PAYMENTS, "1.0"))
        personal = brain.write(ALICE, fact(ALICE_NOTES, "Negotiation", "Anchor high, then listen", portable=True))
        hits = brain.recall(RecallQuery(text="refund refunds negotiation", principal=PRIYA, limit=20))
        records = [doc, first, second, edge, how, personal, *hits, brain.changes(PRIYA, PAYMENTS),
                   *brain.changes(PRIYA, PAYMENTS).events]

        for record in records:
            data = json.loads(json.dumps(to_dict(record)))
            assert from_dict(type(record), data) == record

    def test_the_brain_refuses_bad_input(self, brain):
        stored = brain.write(ALICE, fact(ALICE_NOTES, "Coffee", "flat white"))
        other = brain.write(ALICE, fact(ALICE_WORK, "Coffee machine", "on floor two"))

        with pytest.raises(InvalidRecord):
            brain.write(ALICE, stored)  # Already recorded: the brain sets id and recorded_at.
        with pytest.raises(InvalidRecord):
            brain.write(ALICE, to_dict(fact(ALICE_NOTES, "Coffee", "as a dict")))
        with pytest.raises(InvalidRecord):
            brain.write(ALICE, Relation(space=ALICE_NOTES, provenance=provenance(), validity=Validity(valid_from=START),
                                        source=stored.ref, relation="near", target=other.ref))
        with pytest.raises(InvalidRecord):
            brain.changes(ALICE, ALICE_NOTES, limit=0)
        with pytest.raises(InvalidRecord):
            brain.changes(ALICE, ALICE_NOTES, after_seq=-1)
        with pytest.raises(InvalidRecord):
            brain.recall({"text": "coffee", "principal": to_dict(ALICE)})

    # ── Recall ──────────────────────────────────────────────────────────────

    def test_items_with_every_query_term_rank_first(self, brain, clock):
        partial = brain.write(ALICE, fact(ALICE_WORK, "Retry", "We retry sometimes"))
        clock.advance()
        exact = brain.write(ALICE, fact(ALICE_WORK, "Card payment retry policy", "Retry card payments three times"))
        clock.advance()
        brain.write(ALICE, fact(ALICE_WORK, "Lunch", "Lunch is at noon"))

        hits = brain.recall(RecallQuery(text="card payment retry policy", principal=ALICE))
        assert refs(hits)[:2] == [exact.ref, partial.ref]
        assert all(0 <= hit.score <= 1 for hit in hits)
        assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)
        assert hits[0].score > hits[1].score
        top = brain.recall(RecallQuery(text="card payment retry policy", principal=ALICE, limit=1))
        assert refs(top) == [exact.ref]
        assert brain.recall(RecallQuery(text="retry", principal=ALICE, kinds=frozenset({ItemKind.SKILL}))) == ()

    def test_recall_labels_each_hit_with_its_layer_and_owner(self, brain):
        brain.write(ALICE, fact(ALICE_NOTES, "Standup", "standup notes personal"))
        brain.write(ALICE, fact(ALICE_WORK, "Standup", "standup notes work"))
        brain.write(PRIYA, fact(PAYMENTS, "Standup", "standup notes team", actor="priya", owner_principal="priya",
                                verified_at=START))
        brain.write(LEGAL, fact(ACME, "Standup", "standup notes company", actor="lee"))

        hits = {hit.layer: hit for hit in brain.recall(RecallQuery(text="standup notes", principal=ALICE))}

        assert {layer: hit.label for layer, hit in hits.items()} == {
            Layer.PERSONAL: "From your notes", Layer.WORK: "From your work notes",
            Layer.TEAM: "From Team Payments", Layer.COMPANY: "From Acme"}
        assert {layer: hit.owner for layer, hit in hits.items()} == {
            Layer.PERSONAL: Owner.PERSON, Layer.WORK: Owner.ORGANISATION, Layer.TEAM: Owner.ORGANISATION,
            Layer.COMPANY: Owner.ORGANISATION}
        assert (hits[Layer.TEAM].owner_principal, hits[Layer.TEAM].verified_at) == ("priya", START)
        assert hits[Layer.PERSONAL].provenance == hits[Layer.PERSONAL].item.provenance
        assert hits[Layer.PERSONAL].provenance.source_ref == "conversation:42"
