import tomllib
from pathlib import Path


def _dependency_names(entries):
    return {
        entry.split(";", 1)[0]
        .split("[", 1)[0]
        .split(">=", 1)[0]
        .split("==", 1)[0]
        .split("<", 1)[0]
        .strip()
        .lower()
        for entry in entries
    }


def test_youtube_transcript_runtime_dependency_is_packaged():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    media_deps = _dependency_names(pyproject["project"]["optional-dependencies"]["media"])
    requirements = Path("requirements.txt").read_text(encoding="utf-8")

    assert "youtube-search" in media_deps
    assert "youtube-transcript-api" in media_deps
    assert "httpx" in _dependency_names(pyproject["project"]["dependencies"])
    assert "# This file is generated from pyproject.toml and uv.lock." in requirements
    assert "youtube-search==" in requirements
    assert "youtube-transcript-api==" in requirements


def test_youtube_tool_uses_transcript_loader():
    source = Path("src/row_bot/tools/youtube_tool.py").read_text(encoding="utf-8")

    assert "YoutubeLoader" in source
    assert "youtube_transcript" in source


def test_runtime_dependency_verifier_covers_shipped_feature_groups():
    verifier = Path("scripts/verify_runtime_dependencies.py").read_text(encoding="utf-8")
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    for group in (
        "core",
        "media",
        "channels",
        "voice",
        "local-embeddings",
        "providers",
        "tools",
        "embeddings",
        "youtube",
    ):
        assert f'"{group}"' in verifier

    assert '"httpx"' in verifier
    assert '"youtube_transcript_api"' in verifier
    assert "python scripts/verify_runtime_dependencies.py" in ci
    assert "python scripts/verify_runtime_dependencies.py" in release
