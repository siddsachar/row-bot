"""The desktop app never loads the Org server: importing its whole server and launcher leaves it out."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_IMPORT_THE_APP = """
import sys
import row_bot.app, row_bot.launcher
loaded = sorted(name for name in sys.modules if name == "row_bot_org" or name.startswith("row_bot_org."))
assert not loaded, loaded
"""


@pytest.mark.slow
def test_importing_the_desktop_app_never_imports_the_org_server(tmp_path: Path) -> None:
    env = {**os.environ, "ROW_BOT_DATA_DIR": str(tmp_path / "data"), "ROW_BOT_INSTALL_COUNT": "0"}

    result = subprocess.run([sys.executable, "-c", _IMPORT_THE_APP], capture_output=True, text=True,
                            env=env, timeout=180)

    assert result.returncode == 0, result.stdout + result.stderr
