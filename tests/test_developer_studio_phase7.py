from __future__ import annotations

import importlib
import sys


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("ROW_BOT_DATA_DIR", str(tmp_path / "data"))
    sys.modules.pop("developer.runtime", None)
    import row_bot.developer.runtime as runtime

    return importlib.reload(runtime)


def test_developer_runtime_requires_approval_for_shell_control(tmp_path, monkeypatch):
    runtime = _fresh_modules(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    repo.mkdir()

    result = runtime.run_workspace_command(str(repo), "python --version && python --version", "approve")

    assert result.ran is False
    assert result.decision.decision == "ask"
