# Row-Bot tests

Deterministic tests use fakes, isolated data folders and no network; live
checks are opt-in.

- `tests/contracts/`: fake adapter and interface contracts (providers, channels,
  MCP, plugins, installers and workflows, recorded client-platform protocol).
- `tests/subsystem/<area>/`: one subsystem end to end with fakes.
- `tests/integration/<area>/`: several subsystems together.
- `tests/e2e/`: opt-in live provider or real-service tests (`live_provider`,
  `e2e` markers; `.github/workflows/live-e2e.yml`).
- `tests/docs/`, `tests/marketing/`: docs and marketing tooling, run by
  `.github/workflows/docs.yml`.
- `tests/browser/`: the runners and fixture backends for the Playwright specs in
  `frontend/tests/browser/`.
- `tests/fixtures/`, `tests/helpers/`, `tests/snapshots/`: shared fakes, helpers
  and committed snapshots.

## Lanes

`scripts/run_test_matrix.py` is the source of truth; CI runs its tiers.

```bash
uv run python scripts/run_test_matrix.py fast       # static checks + contracts, < 2 min
uv run python scripts/run_test_matrix.py changed --base origin/main
uv run python scripts/run_test_matrix.py pr         # the Linux PR lane
uv run python scripts/run_test_matrix.py platform   # OS-sensitive tests + launcher smoke
uv run python scripts/run_test_matrix.py browser-smoke
uv run python scripts/run_test_matrix.py nightly    # everything, slow tests included
```

- Pull requests (`ci.yml`, required check `CI / ci-ok`): quality checks, client
  checks, the deterministic suite once on Linux without the `slow` tests (split
  into shards with `ROW_BOT_TEST_SHARD=k/N`, coverage recorded but not gated),
  the `platform` tests on Windows and macOS, and the browser smoke set.
- Nightly (`nightly.yml`): the whole suite with the slow tests on Linux, Windows
  and macOS, the browser nightly set, a Linux package smoke and the docs
  reference check; weekly installer-verify and Firefox/WebKit.

## Writing tests

- Test behaviour through public functions, HTTP APIs or rendered UI, never
  source text, private constants or imports.
- Use `tmp_path` and `monkeypatch`. For modules that bind `ROW_BOT_DATA_DIR`
  paths at import, use the `reload_for_data_dir` fixture; it restores them.
- Mark tests of about a second or more, or that start real processes,
  `@pytest.mark.slow` (nightly). Mark tests of OS-sensitive code
  `pytest.mark.platform` (checked).
- `tests/conftest.py` fails a test that reaches the network or writes live user
  data, stops git at the checkout's `.tmp`, and gives pytest-xdist workers their
  own data folders.
- Snapshots are committed; record one with `ROW_BOT_UPDATE_SNAPSHOTS=1`.
- Security-sensitive behaviour keeps a behaviour test in the PR lane.

Live provider checks should use the real Row-Bot runtime with at least one
recommended chat model per configured provider. The only acceptable
provider-side failure is an explicit quota, credit or billing-limit response;
schema, auth, routing, streaming, tool replay and unsupported-parameter
failures are bugs.
