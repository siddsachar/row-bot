"""A hatched look is named for what was described, not "Generated A cute tiny…"."""

import pytest

from row_bot.buddy.hatch import _hatch_pack_name


@pytest.mark.parametrize(
    ("prompt", "name"),
    [
        ("A small round otter with a teal scarf and big curious eyes", "Small round otter"),
        ("A cute tiny mystical coding familiar for Row-Bot", "Mystical coding familiar"),
        ("an owl, wise and sleepy", "Owl"),
        ("A fox named Ember who loves tea", "Ember"),
        ("", "New look"),
    ],
)
def test_a_look_is_named_for_what_was_described(prompt: str, name: str) -> None:
    assert _hatch_pack_name(prompt, "draft-1") == name
