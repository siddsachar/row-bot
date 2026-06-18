"""Typed helpers shared by the public docs automation scripts."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


_ID_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = _ID_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return slug or "item"


def clean_public_text(value: Any) -> str:
    text = str(value if value is not None else "")
    replacements = {
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u2192": "->",
        "\u00b7": "-",
        "â€“": "-",
        "â€”": "-",
        "â€¦": "...",
        "â†’": "->",
        "Â·": "-",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", errors="ignore").decode("ascii")
    return " ".join(text.split())


def repo_path(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def public_route_for_doc(path: Path, docs_root: Path) -> str:
    rel = path.relative_to(docs_root).with_suffix("")
    parts = list(rel.parts)
    if parts == ["index"]:
        return "/docs/"
    if parts[-1] == "index":
        return "/docs/" + "/".join(parts[:-1]) + "/"
    return "/docs/" + "/".join(parts)


@dataclass(frozen=True)
class InventoryRecord:
    id: str
    title: str
    description: str = ""
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocsPageRecord:
    id: str
    title: str
    description: str
    route: str
    path: str
    source: str


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(to_jsonable(value), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
