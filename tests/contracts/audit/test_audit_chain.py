from __future__ import annotations

import dataclasses
import hashlib

import pytest

from row_bot.audit import (
    Action, ChainLink, InvalidAuditEvent, NotCanonicalisable, canonical_bytes, canonical_json, chain_hash,
    genesis_hash, make_link, redact, to_dict, verify_chain,
)
from tests.contracts.audit.examples import example

pytestmark = pytest.mark.contract

GENESIS = genesis_hash("device:dev-7f3a")
ACTIONS = [Action.POLICY_CHANGED, Action.SESSION_SIGNED_IN, Action.TOOL_CALLED, Action.MODEL_CALLED,
           Action.DOCUMENT_UPLOADED, Action.SESSION_SIGNED_OUT]


def _chain() -> list[dict]:
    links, prev = [], GENESIS
    for seq, action in enumerate(ACTIONS):
        link = make_link(prev, seq, redact(example(action, number=seq + 1), "metadata"))
        links.append(link.to_dict())
        prev = link.hash
    return links


def test_a_sound_chain_verifies_from_its_genesis():
    links = _chain()

    report = verify_chain(links, start_hash=GENESIS, expected_head=links[-1]["hash"])

    assert (report.ok, report.checked, report.head, report.broken_at, report.reason) == (
        True, 6, links[-1]["hash"], None, None)
    assert verify_chain([ChainLink.from_dict(link) for link in links], start_hash=GENESIS).ok
    # A segment verifies from a trusted checkpoint.
    assert verify_chain(links[3:], start_hash=links[2]["hash"], start_seq=3).ok


def test_the_hash_is_the_documented_construction():
    event = example(Action.TOOL_CALLED)

    def lp(data: bytes) -> bytes:
        return len(data).to_bytes(8, "big") + data

    expected = hashlib.sha256(lp(b"row-bot/audit/v1") + lp(bytes.fromhex(GENESIS))
                              + lp(canonical_json(to_dict(event)))).hexdigest()
    assert chain_hash(GENESIS, event) == expected
    assert canonical_bytes(event) == canonical_json(to_dict(event))
    assert GENESIS == hashlib.sha256(lp(b"row-bot/audit/v1/genesis") + lp(b"device:dev-7f3a")).hexdigest()


def _broken(links: list[dict], **kwargs) -> tuple[int | None, str | None]:
    report = verify_chain(links, start_hash=GENESIS, **kwargs)
    assert not report.ok
    return report.broken_at, report.reason


def test_an_edited_field_breaks_its_own_link():
    links = _chain()
    links[2]["event"]["outcome"] = "denied"

    assert _broken(links) == (2, "hash")


def test_an_edit_with_a_fresh_hash_breaks_the_next_link():
    links = _chain()
    event = ChainLink.from_dict(links[2]).event
    links[2] = make_link(links[2]["prev_hash"], 2, dataclasses.replace(event, outcome="denied")).to_dict()

    assert _broken(links) == (3, "prev_hash")


def test_reordered_links_break_where_the_order_changes():
    links = _chain()
    links[2], links[3] = links[3], links[2]

    assert _broken(links) == (2, "prev_hash")


def test_a_deleted_link_breaks_where_it_was():
    links = _chain()
    del links[4]

    assert _broken(links) == (4, "prev_hash")


def test_an_inserted_link_breaks_the_link_after_it():
    links = _chain()
    forged = make_link(links[1]["hash"], 2, redact(example(Action.TOOL_CALLED, number=99), "metadata")).to_dict()
    links.insert(2, forged)

    assert _broken(links) == (3, "prev_hash")


def test_a_cut_tail_shows_only_against_a_kept_head():
    links = _chain()
    head = links[-1]["hash"]
    del links[-2:]

    assert verify_chain(links, start_hash=GENESIS).ok
    assert _broken(links, expected_head=head) == (4, "head")


def test_a_renumbered_or_invalid_link_is_reported():
    links = _chain()
    links[1]["seq"] = 7
    assert _broken(links) == (1, "seq")

    links = _chain()
    links[3]["event"]["layer"] = "personal"
    assert _broken(links) == (3, "invalid")

    links = _chain()
    links[0]["hash"] = "zz"
    assert _broken(links) == (0, "invalid")


def test_links_cannot_move_between_chains():
    links = _chain()

    assert verify_chain(links, start_hash=genesis_hash("org:acme")).broken_at == 0
    with pytest.raises(InvalidAuditEvent):
        genesis_hash("has space")


def test_redacting_after_chaining_breaks_the_chain():
    full = example(Action.TOOL_CALLED)
    stored = make_link(GENESIS, 0, full).to_dict()
    stored["event"] = to_dict(redact(full, "metadata"))  # Redact first, then chain.

    assert _broken([stored]) == (0, "hash")


# RFC 8785 §3.2.3: members sorted by UTF-16 code units, so U+1F600 (D83D DE00) sorts before U+FB33.
def test_jcs_sorts_keys_by_utf16_code_units():
    value = {"\u20ac": "Euro Sign", "\r": "Carriage Return", "\ufb33": "Hebrew Letter Dalet With Dagesh",
             "1": "One", "\U0001f600": "Emoji: Grinning Face", "\u0080": "Control", "\u00f6": "Latin Small Letter O"}

    assert canonical_json(value) == (
        '{"\\r":"Carriage Return","1":"One","\u0080":"Control","\u00f6":"Latin Small Letter O",'
        '"\u20ac":"Euro Sign","\U0001f600":"Emoji: Grinning Face","\ufb33":"Hebrew Letter Dalet With Dagesh"}'
    ).encode("utf-8")

def test_jcs_escapes_only_what_it_must():
    text = 'q" b\\ \b\t\n\f\r \x01 \x1f \x7f   é \U0001f600'

    assert canonical_json(text) == ('"q\\" b\\\\ \\b\\t\\n\\f\\r \\u0001 \\u001f \x7f   é \U0001f600"'
                                    ).encode("utf-8")


def test_jcs_writes_integers_and_literals_without_whitespace():
    assert canonical_json({"b": [1, -2, 0, 2**53 - 1, True, False, None], "a": {}}) == (
        b'{"a":{},"b":[1,-2,0,9007199254740991,true,false,null]}')
    assert canonical_json([]) == b"[]"


@pytest.mark.parametrize("value", [2**53, -(2**53), 1.5, 1.0, "\ud800", {1: "x"}, {"a": object()}])
def test_jcs_refuses_what_is_outside_its_subset(value):
    with pytest.raises(NotCanonicalisable):
        canonical_json(value)
