"""RFC 8785 JSON Canonicalization Scheme (JCS), for the subset the audit records use.

Supported: objects with text keys, arrays, text, integers, ``true``, ``false`` and ``null``. Members are sorted
by their keys' UTF-16 code units (RFC 8785 §3.2.3), text is written as UTF-8 with only ``"``, ``\\`` and the
control characters escaped (§3.2.2.2), and there is no whitespace.

Limits, by design:
- **No floats.** RFC 8785 writes numbers the ECMAScript way; the audit records never hold a non-integer number in a
  hashed field, so a float is refused rather than half-supported.
- **Integers only within ±(2**53 - 1)**, the I-JSON range every JSON parser reads exactly.
- **Text must be valid Unicode**: a lone surrogate is refused (it can't be written as UTF-8).
"""

from __future__ import annotations

from collections.abc import Mapping

MAX_SAFE_INTEGER = 2**53 - 1

_SHORT_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


class NotCanonicalisable(ValueError):
    """A value outside the JCS subset these records use."""


def _text(value: str) -> str:
    out = ['"']
    for char in value:
        if char in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[char])
        elif char < " ":
            out.append(f"\\u{ord(char):04x}")
        elif "\ud800" <= char <= "\udfff":
            raise NotCanonicalisable("text holds a lone surrogate")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be")


def _write(value: object, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise NotCanonicalisable("integer outside ±(2**53 - 1)")
        out.append(str(int(value)))
    elif isinstance(value, float):
        raise NotCanonicalisable("floats are not part of the audit JCS subset")
    elif isinstance(value, str):
        out.append(_text(value))
    elif isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise NotCanonicalisable("object keys must be text")
        out.append("{")
        for index, key in enumerate(sorted(value, key=_utf16_key)):
            if index:
                out.append(",")
            out.append(_text(key))
            out.append(":")
            _write(value[key], out)
        out.append("}")
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _write(item, out)
        out.append("]")
    else:
        raise NotCanonicalisable(f"{type(value).__name__} is not JSON")


def canonical_json(value: object) -> bytes:
    """The RFC 8785 canonical UTF-8 bytes of ``value`` (within the subset above)."""
    out: list[str] = []
    _write(value, out)
    return "".join(out).encode("utf-8")
