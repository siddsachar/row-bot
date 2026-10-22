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
- `tests/conftest.py` fails a test that reaches the network, writes live user
  data or leaves a re-imported or reloaded `row_bot` module behind, stops git at
  the checkout's `.tmp`, and gives pytest-xdist workers their own data folders.
- Snapshots are committed; record one with `ROW_BOT_UPDATE_SNAPSHOTS=1`.
- Security-sensitive behaviour keeps a behaviour test in the PR lane.

Live provider checks should use the real Row-Bot runtime with at least one
recommended chat model per configured provider. The only acceptable
provider-side failure is an explicit quota, credit or billing-limit response;
schema, auth, routing, streaming, tool replay and unsupported-parameter
failures are bugs.

## Live eval (opt-in, spends money)

`tests/e2e/live_eval/` runs about 25 representative tasks through the real
Agent runtime. They cover chat, files, tools, a loopback web page, memory, a
workflow, delegation, a long thread past compaction and a local Ollama model.
`tests/e2e/test_live_verification.py` checks the cost plan's provider work
against real APIs. Both need `ROW_BOT_LIVE_EVAL=1`.

How a run is set up:

- **Keys.** They come from provider environment variables only (for example
  `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`), never from a data folder or the OS
  keychain.
- **Data.** Each provider runs in its own process with a fresh data folder.
- **Budget.** The run stops before passing `ROW_BOT_LIVE_EVAL_MAX_USD`
  (default 5) or `ROW_BOT_LIVE_EVAL_MAX_TOKENS` (default 2,000,000).
- **Report.** It lands in `.tmp/live-eval/<timestamp>/report.md` and
  `report.json`.

```bash
uv run python scripts/live_eval.py plan          # tasks x providers and estimated tokens; no network
ROW_BOT_LIVE_EVAL=1 uv run python scripts/live_eval.py run --providers anthropic --checks
uv run python scripts/live_eval.py ab --a baseline --b cache-key-none --providers openai   # with ROW_BOT_LIVE_EVAL=1
uv run python scripts/live_eval.py manual        # the checks a person runs by hand
```

Costs are reported in one of three ways:

- the provider's own figure (OpenRouter);
- figures from a rates file you supply (`ROW_BOT_LIVE_EVAL_RATES`; see
  `tests/e2e/live_eval/rates.example.json`);
- otherwise "unpriced". There is no built-in price table.

CI runs the same suite only by manual dispatch of `.github/workflows/live-e2e.yml`
(`run_live_eval`), using repository secrets.

**Learning tier** (`--tier learning`, `tests/e2e/live_eval/learning.py`): Learning's
four task packs (visible splits, `ROW_BOT_LIVE_EVAL_LEARNING_PER_PACK` per pack,
default 8, `all` for 16) and replay fixtures, each checked by its pack's verifier.
Two lanes besides the API-key providers:

- **ChatGPT** (`codex`): its sign-in comes from a dedicated eval profile outside the
  repo (`ROW_BOT_LIVE_EVAL_CODEX_PROFILE`), handed to the worker through a pipe;
  only refreshed tokens are written back to the profile, and nothing secret reaches
  the run folder. Token cap per run: `ROW_BOT_LIVE_EVAL_CODEX_MAX_TOKENS` (default
  1,500,000). The model is GPT-6.1 Sol if the live catalog lists it, else GPT-5.6 Sol.
- **Local Ollama** (`ROW_BOT_LIVE_EVAL_OLLAMA_MODEL`): free, so the caps never hold it.

```bash
ROW_BOT_LIVE_EVAL_CODEX_PROFILE=<eval profile> uv run python scripts/live_eval.py plan --tier learning --providers codex
```
