from __future__ import annotations

import re
from pathlib import Path


def normalize_html_snapshot(html: str) -> str:
    html = re.sub(r"\s+", " ", html).strip()
    html = html.replace("><", ">\n<")
    return html


def assert_or_write_snapshot(path: Path, content: str) -> None:
    normalized = content.replace("\r\n", "\n")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(normalized, encoding="utf-8")
        return
    assert path.read_text(encoding="utf-8").replace("\r\n", "\n") == normalized
