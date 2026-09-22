from scripts.marketing import capture_landing_story as cli


def test_cli_exposes_all_phases_and_requires_run_ids() -> None:
    parser = cli._parser()
    assert parser.parse_args(["preflight"]).phase == "preflight"
    prepared = parser.parse_args(
        ["prepare", "--authorize-real-profile", "--run-id", "run-123"]
    )
    assert prepared.phase == "prepare"
    assert prepared.run_id == "run-123"
    enriched = parser.parse_args(
        [
            "prepare",
            "--authorize-real-profile",
            "--run-id",
            "run-123",
            "--enrich-knowledge-graph",
        ]
    )
    assert enriched.enrich_knowledge_graph is True
    for phase in ("capture", "process", "validate", "publish"):
        args = parser.parse_args([phase, "--run-id", "run-123"])
        assert args.phase == phase
        assert args.run_id == "run-123"


def test_external_route_filter_allows_only_loopback_and_page_local_urls() -> None:
    outcomes: list[str] = []

    class Request:
        def __init__(self, url: str):
            self.url = url

    class Route:
        def __init__(self, url: str):
            self.request = Request(url)

        def continue_(self) -> None:
            outcomes.append("continue")

        def abort(self, reason: str) -> None:
            outcomes.append(f"abort:{reason}")

    class Context:
        def route(self, _pattern: str, handler: object) -> None:
            for url in (
                "http://127.0.0.1:8765/",
                "ws://127.0.0.1:8765/socket",
                "data:image/png;base64,AA==",
                "blob:http://127.0.0.1:8765/id",
                "https://example.com/private",
            ):
                handler(Route(url))  # type: ignore[operator]

    cli._block_external_routes(Context())
    assert outcomes == [
        "continue",
        "continue",
        "continue",
        "continue",
        "abort:blockedbyclient",
    ]


def test_ffmpeg_override_must_be_an_existing_file(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "ffmpeg.exe"
    executable.write_bytes(b"reviewed test binary")
    monkeypatch.setenv(cli.FFMPEG_ENV, str(executable))
    assert cli._ffmpeg_executable() == str(executable.resolve())
    monkeypatch.setenv(cli.FFMPEG_ENV, str(tmp_path / "missing.exe"))
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    assert cli._ffmpeg_executable() == ""
