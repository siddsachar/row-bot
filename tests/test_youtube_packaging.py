from pathlib import Path


def test_youtube_transcript_runtime_dependency_is_packaged():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()
    normalized = {
        line.strip().split(";", 1)[0].split("#", 1)[0].strip().lower()
        for line in requirements
    }

    assert "youtube-search" in normalized
    assert "youtube-transcript-api" in normalized
    assert "httpx" in normalized


def test_youtube_tool_uses_transcript_loader():
    source = Path("src/row_bot/tools/youtube_tool.py").read_text(encoding="utf-8")

    assert "YoutubeLoader" in source
    assert "youtube_transcript" in source


def test_runtime_dependency_verifier_covers_shipped_feature_groups():
    verifier = Path("scripts/verify_runtime_dependencies.py").read_text(encoding="utf-8")
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    for group in ("core", "providers", "tools", "channels", "voice", "embeddings", "youtube"):
        assert f'"{group}"' in verifier

    assert '"httpx"' in verifier
    assert '"youtube_transcript_api"' in verifier
    assert "python scripts/verify_runtime_dependencies.py" in ci
    assert "python scripts/verify_runtime_dependencies.py" in release
