# Row-Bot Test Architecture

Row-Bot keeps deterministic subsystem coverage separate from live/provider
checks:

- `tests/contracts/` verifies fake provider, fake channel, MCP, and other
  adapter contracts.
- `tests/subsystem/` exercises real Row-Bot subsystems end to end with fakes,
  isolated temp data dirs, and no real network calls.
- `tests/e2e/` is reserved for opt-in live provider or real-world service tests.
- `tests/helpers/legacy_inventory.py` and
  `tests/helpers/legacy_inventory_snapshot.py` keep the retired legacy coverage
  ledger machine-checkable.

Recommended deterministic lane:

```bash
uv run python -m pytest tests/contracts tests/subsystem -m "not live_provider" -q
```

Preferred local matrix commands:

```bash
uv run python scripts/run_test_matrix.py fast
uv run python scripts/run_test_matrix.py coverage
uv run python scripts/run_test_matrix.py pr
uv run python scripts/run_test_matrix.py changed --base origin/main
```

Full local smoke matrix before release-sensitive changes, expanded:

```bash
uv sync --locked --all-extras --group test
uv run python scripts/verify_runtime_dependencies.py all
uv run python -m pytest tests/contracts tests/subsystem -m "not live_provider" -q
uv run python scripts/run_test_matrix.py coverage
uv run python -m pytest tests -m "not live_provider" -q
uv run python scripts/smoke_app.py --port 8090 --timeout 120
```

The old monoliths `tests/test_suite.py`, `tests/integration_tests.py`, and
`tests/test_memory_e2e.py` are compatibility shims only. Their section-level
coverage has been split into focused pytest suites and preserved in the legacy
inventory snapshot.

`tests/helpers/source_test_map.py` maps changed source paths to targeted tests.
`tests/helpers/legacy_inventory.py` verifies that every retired legacy section
has covered replacement paths. PR and release CI use `scripts/run_test_matrix.py`;
no substantive coverage should be added back to the legacy script files.

`scripts/run_test_matrix.py coverage` runs the migrated contract/subsystem lane
with `pytest-cov`, writes `.tmp/coverage/migrated-subsystems.xml`, and enforces
the current 55% migrated-subsystem baseline. The selected migrated gate now
includes provider catalog/runtime/selection, the memory tool, and updater safety
coverage in addition to the earlier channels, MCP, memory, Dream Cycle,
Developer Studio, Designer export, and existing plugin modules.

Use `python scripts/coverage_summary.py` after a coverage run for a compact
per-module summary of `.tmp/coverage/migrated-subsystems.xml`.

Live provider, real MCP, and real channel checks stay opt-in. Use the manual
`.github/workflows/live-e2e.yml` workflow when credentials or external services
are available.
