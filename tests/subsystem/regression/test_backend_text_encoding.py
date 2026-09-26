"""Backend text never carries double-encoded UTF-8 ("âš¡" for "⚡")."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.subsystem

SOURCE = Path(__file__).resolve().parents[3] / "src" / "row_bot"
# UTF-8 bytes read back as cp1252: bolt, dashes and quotes, arrows, emoji.
MOJIBAKE = ("âš", "â€", "â†", "âœ", "ðŸ", "Ã©", "Â·")


def test_backend_strings_are_not_double_encoded():
    found = []
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE)
        # The legacy NiceGUI package is retired separately; comments may name
        # the pattern they guard against.
        if relative.parts[0] == "ui":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if any(token in line for token in MOJIBAKE):
                found.append(f"{relative}:{number}: {line.strip()[:80]}")
    assert found == []


def test_workflow_defaults_and_failure_names_use_the_real_bolt():
    text = (SOURCE / "tasks.py").read_text(encoding="utf-8")
    assert "icon                TEXT DEFAULT '⚡'" in text
    assert 'f"⚡ {task[\'name\']} (failed) — "' in text
