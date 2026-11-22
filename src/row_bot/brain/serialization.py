"""Strict validation, JSON mapping and JSON Schema for the brain records, driven by their annotations.

Each record is a frozen dataclass. Its field annotations, with ``Annotated[..., Bounds(...)]`` limits, are the
one source of truth: constructing a record checks them (``check_fields``), ``to_dict`` / ``from_dict`` map a
record to and from JSON-safe dicts, and ``json_schema`` publishes it (``contracts/brain/v1/``).

The mapping is strict. Unknown and missing fields are refused, every string and collection is bounded, text
may not hold NUL (Postgres can't store it), and times are timezone-aware UTC, written as ISO-8601 with ``Z``.
"""

from __future__ import annotations

import dataclasses
import json
import math
import re
import types
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from functools import cache
from typing import Annotated, Any, TypeVar, Union, get_args, get_origin, get_type_hints

from .errors import InvalidRecord

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)")
TIME_PATTERN = "^" + _TIME.pattern + "$"


@dataclasses.dataclass(frozen=True)
class Bounds:
    """Limits on a value: string length and pattern, number range, collection size, JSON size and depth."""

    min_length: int = 0
    max_length: int | None = None
    pattern: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    max_items: int | None = None
    max_bytes: int | None = None
    max_depth: int | None = None


_NO_BOUNDS = Bounds()
T = TypeVar("T")


def format_time(value: datetime) -> str:
    """A UTC time as ISO-8601 with microseconds and ``Z``: one spelling for one instant."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_time(value: object, path: str = "time") -> datetime:
    """An ISO-8601 UTC time (``Z`` or ``+00:00``); any other offset, or none, is refused."""
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise InvalidRecord(f"{path}: must be a UTC ISO-8601 time")
    return datetime.fromisoformat(value).astimezone(timezone.utc)


@cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls, include_extras=True)


def _fields(cls: type) -> list[tuple[str, Any]]:
    hints = _hints(cls)
    return [(f.name, hints[f.name]) for f in dataclasses.fields(cls)]


def _unwrap(hint: Any) -> tuple[Any, Bounds]:
    if get_origin(hint) is Annotated:
        base, *extras = get_args(hint)
        return base, next((extra for extra in extras if isinstance(extra, Bounds)), _NO_BOUNDS)
    return hint, _NO_BOUNDS


def _arms(base: Any) -> tuple[Any, ...] | None:
    return get_args(base) if get_origin(base) in (Union, types.UnionType) else None


def _kind(cls: Any) -> str | None:
    return getattr(cls, "KIND", None) if isinstance(cls, type) else None


def _limit(bounds: Bounds, name: str, path: str) -> int:
    value = getattr(bounds, name)
    if value is None:  # A programming error in a record definition, not bad input.
        raise TypeError(f"{path}: every string and collection needs a {name}")
    return value


def _check_text(value: object, bounds: Bounds, path: str) -> str:
    if not isinstance(value, str):
        raise InvalidRecord(f"{path}: must be text")
    if not bounds.min_length <= len(value) <= _limit(bounds, "max_length", path):
        raise InvalidRecord(f"{path}: must be {bounds.min_length} to {bounds.max_length} characters")
    if "\x00" in value:
        raise InvalidRecord(f"{path}: may not contain NUL")
    if bounds.pattern is not None and not re.fullmatch(bounds.pattern, value):
        raise InvalidRecord(f"{path}: has the wrong form")
    return str(value)


def _check_number(value: object, base: type, bounds: Bounds, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float) if base is float else int):
        raise InvalidRecord(f"{path}: must be {'a number' if base is float else 'an integer'}")
    if base is float:
        value = float(value)
        if not math.isfinite(value):
            raise InvalidRecord(f"{path}: must be finite")
    if (bounds.minimum is not None and value < bounds.minimum) or (
            bounds.maximum is not None and value > bounds.maximum):
        raise InvalidRecord(f"{path}: must be between {bounds.minimum} and {bounds.maximum}")
    return value


def _check_time(value: object, path: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise InvalidRecord(f"{path}: must be a timezone-aware UTC time")
    return value.astimezone(timezone.utc)


def _freeze_json(value: object, depth: int, bounds: Bounds, path: str) -> object:
    if depth > _limit(bounds, "max_depth", path):
        raise InvalidRecord(f"{path}: nested too deeply")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidRecord(f"{path}: must be finite")
        return value
    if isinstance(value, str):
        if "\x00" in value:
            raise InvalidRecord(f"{path}: may not contain NUL")
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, depth + 1, bounds, f"{path}[{index}]") for index, item in enumerate(value))
    if isinstance(value, Mapping):
        frozen = {}
        for key, item in value.items():
            if not isinstance(key, str) or "\x00" in key:
                raise InvalidRecord(f"{path}: keys must be text")
            frozen[key] = _freeze_json(item, depth + 1, bounds, f"{path}.{key}")
        return types.MappingProxyType(frozen)
    raise InvalidRecord(f"{path}: must be JSON")


def _thaw_json(value: object) -> object:
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    return value


def check_value(value: object, hint: Any, path: str) -> Any:
    """``value`` checked against the annotation ``hint``, normalised (UTC times, frozen JSON), or refused."""
    base, bounds = _unwrap(hint)
    arms = _arms(base)
    if arms is not None:
        if value is None:
            if type(None) in arms:
                return None
            raise InvalidRecord(f"{path}: is required")
        choices = [arm for arm in arms if arm is not type(None)]
        if len(choices) == 1:
            return check_value(value, choices[0], path)
        if any(isinstance(value, _unwrap(arm)[0]) for arm in choices):
            return value  # A record, validated when it was built.
        raise InvalidRecord(f"{path}: must be one of {', '.join(_unwrap(arm)[0].__name__ for arm in choices)}")
    origin = get_origin(base)
    if base is str:
        return _check_text(value, bounds, path)
    if base is bool:
        if not isinstance(value, bool):
            raise InvalidRecord(f"{path}: must be true or false")
        return value
    if base in (int, float):
        return _check_number(value, base, bounds, path)
    if base is datetime:
        return _check_time(value, path)
    if isinstance(base, type) and issubclass(base, StrEnum):
        try:
            return base(value)
        except (TypeError, ValueError):
            raise InvalidRecord(f"{path}: must be one of {', '.join(member.value for member in base)}") from None
    if origin in (tuple, frozenset):
        if not isinstance(value, origin):
            raise InvalidRecord(f"{path}: must be a {origin.__name__}")
        if len(value) > _limit(bounds, "max_items", path):
            raise InvalidRecord(f"{path}: at most {bounds.max_items} items")
        item_hint = get_args(base)[0]
        checked = [check_value(item, item_hint, f"{path}[{index}]") for index, item in enumerate(value)]
        return origin(checked)
    if origin is Mapping:
        if not isinstance(value, Mapping):
            raise InvalidRecord(f"{path}: must be a JSON object")
        frozen = _freeze_json(value, 1, bounds, path)
        encoded = json.dumps(_thaw_json(frozen), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(encoded.encode("utf-8")) > _limit(bounds, "max_bytes", path):
            raise InvalidRecord(f"{path}: at most {bounds.max_bytes} bytes as JSON")
        return frozen
    if dataclasses.is_dataclass(base):
        if not isinstance(value, base):
            raise InvalidRecord(f"{path}: must be a {base.__name__}")
        return value
    raise TypeError(f"{path}: unsupported annotation {hint!r}")


def check_fields(record: object) -> None:
    """Check every field of a frozen dataclass against its annotation, storing the normalised values."""
    for name, hint in _fields(type(record)):
        value = getattr(record, name)
        checked = check_value(value, hint, f"{type(record).__name__}.{name}")
        if checked is not value:
            object.__setattr__(record, name, checked)


def _encode(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return format_time(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_dict(value)
    if isinstance(value, Mapping):
        return _thaw_json(value)
    if isinstance(value, (tuple, frozenset)):
        items = [_encode(item) for item in value]
        if isinstance(value, frozenset):  # A set has one spelling: its members in a stable order.
            items.sort(key=lambda item: json.dumps(item, sort_keys=True))
        return items
    return value


def to_dict(record: object) -> dict[str, Any]:
    """A record as a JSON-safe dict; a stored item carries its ``kind``."""
    out: dict[str, Any] = {}
    if (kind := _kind(type(record))) is not None:
        out["kind"] = kind
    for name, _ in _fields(type(record)):
        out[name] = _encode(getattr(record, name))
    return out


def _decode(value: object, hint: Any, path: str) -> object:
    base, bounds = _unwrap(hint)
    arms = _arms(base)
    if arms is not None:
        if value is None:
            return None
        choices = [arm for arm in arms if arm is not type(None)]
        if len(choices) == 1:
            return _decode(value, choices[0], path)
        kind = value.get("kind") if isinstance(value, Mapping) else None
        for arm in choices:
            if _kind(_unwrap(arm)[0]) == kind:
                return from_dict(_unwrap(arm)[0], value, path)
        raise InvalidRecord(f"{path}: unknown kind")
    origin = get_origin(base)
    if base is datetime:
        return parse_time(value, path)
    if origin in (tuple, frozenset):
        if not isinstance(value, list):
            raise InvalidRecord(f"{path}: must be a list")
        if bounds.max_items is not None and len(value) > bounds.max_items:
            raise InvalidRecord(f"{path}: at most {bounds.max_items} items")
        items = [_decode(item, get_args(base)[0], f"{path}[{index}]") for index, item in enumerate(value)]
        if origin is frozenset:
            try:
                unique = frozenset(items)
            except TypeError:
                raise InvalidRecord(f"{path}: has an item of the wrong type") from None
            if len(unique) != len(items):
                raise InvalidRecord(f"{path}: repeats an item")
            return unique
        return tuple(items)
    if dataclasses.is_dataclass(base):
        return from_dict(base, value, path)
    return value  # Checked when the record is built.


def from_dict(cls: type[T], data: object, path: str | None = None) -> T:
    """Build ``cls`` from a dict made by ``to_dict``; anything unknown, missing or out of bounds is refused."""
    path = path or cls.__name__
    if not isinstance(data, Mapping):
        raise InvalidRecord(f"{path}: must be an object")
    names = [name for name, _ in _fields(cls)]
    kind = _kind(cls)
    expected = set(names) | ({"kind"} if kind is not None else set())
    if unknown := sorted(set(data) - expected, key=str):
        raise InvalidRecord(f"{path}: unknown field {unknown[0]!r}")
    if missing := sorted(expected - set(data)):
        raise InvalidRecord(f"{path}: missing field {missing[0]!r}")
    if kind is not None and data["kind"] != kind:
        raise InvalidRecord(f"{path}: kind must be {kind!r}")
    return cls(**{name: _decode(data[name], hint, f"{path}.{name}") for name, hint in _fields(cls)})


def _schema(hint: Any, defs: dict[str, Any]) -> dict[str, Any]:
    base, bounds = _unwrap(hint)
    arms = _arms(base)
    if arms is not None:
        choices = [_schema(arm, defs) for arm in arms if arm is not type(None)]
        schema = choices[0] if len(choices) == 1 else {"oneOf": choices}
        return {"anyOf": [schema, {"type": "null"}]} if type(None) in arms else schema
    origin = get_origin(base)
    if base is str:
        schema = {"type": "string", "maxLength": bounds.max_length}
        if bounds.min_length:
            schema["minLength"] = bounds.min_length
        if bounds.pattern is not None:
            schema["pattern"] = f"^(?:{bounds.pattern})$"
        return schema
    if base is bool:
        return {"type": "boolean"}
    if base in (int, float):
        schema = {"type": "integer" if base is int else "number"}
        if bounds.minimum is not None:
            schema["minimum"] = bounds.minimum
        if bounds.maximum is not None:
            schema["maximum"] = bounds.maximum
        return schema
    if base is datetime:
        return {"type": "string", "format": "date-time", "pattern": TIME_PATTERN}
    if isinstance(base, type) and issubclass(base, StrEnum):
        defs.setdefault(base.__name__, {"type": "string", "enum": [member.value for member in base],
                                        "description": _doc(base)})
        return {"$ref": f"#/$defs/{base.__name__}"}
    if origin in (tuple, frozenset):
        schema = {"type": "array", "items": _schema(get_args(base)[0], defs), "maxItems": bounds.max_items}
        if origin is frozenset:
            schema["uniqueItems"] = True
        return schema
    if origin is Mapping:
        return {"type": "object", "description": (
            f"Any JSON object: at most {bounds.max_bytes} bytes as compact JSON and {bounds.max_depth} levels deep.")}
    if dataclasses.is_dataclass(base):
        if base.__name__ not in defs:
            defs[base.__name__] = {}  # Reserve the name first, so a cycle can't recurse forever.
            defs[base.__name__] = _object_schema(base, defs)
        return {"$ref": f"#/$defs/{base.__name__}"}
    raise TypeError(f"unsupported annotation {hint!r}")


def _doc(cls: type) -> str:
    return " ".join((cls.__doc__ or "").split())


def _object_schema(cls: type, defs: dict[str, Any]) -> dict[str, Any]:
    properties = {name: _schema(hint, defs) for name, hint in _fields(cls)}
    if (kind := _kind(cls)) is not None:
        properties["kind"] = {"const": kind}
    return {"type": "object", "description": _doc(cls), "additionalProperties": False,
            "properties": properties, "required": sorted(properties)}


def json_schema(cls: type) -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of a record type, self-contained with its ``$defs``."""
    defs: dict[str, Any] = {}
    root = _object_schema(cls, defs)
    defs.pop(cls.__name__, None)
    schema = {"$schema": SCHEMA_DIALECT, "title": cls.__name__, **root}
    if defs:
        schema["$defs"] = defs
    return schema
