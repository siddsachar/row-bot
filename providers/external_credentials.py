from __future__ import annotations

from typing import Any
import hashlib
import pathlib
import shutil
import subprocess


def _path_label(path: pathlib.Path) -> dict[str, str]:
    expanded = path.expanduser()
    return {
        "label": str(path),
        "path_hash": hashlib.sha256(str(expanded).encode("utf-8")).hexdigest()[:12],
        "exists": str(expanded.exists()).lower(),
    }


def discover_external_credentials() -> list[dict[str, Any]]:
    """Return display-safe references to known external credential stores."""
    from providers.codex import discover_codex_credentials

    results = [discover_codex_credentials()]

    if shutil.which("gh"):
        results.append({"provider_id": "copilot", "source": "gh_cli", "label": "gh auth token", "exists": "true"})
    return results


def read_gh_token() -> str:
    """Read a GitHub CLI token without logging it; callers decide whether it is usable."""
    if not shutil.which("gh"):
        return ""
    try:
        proc = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=False)
    except Exception:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""