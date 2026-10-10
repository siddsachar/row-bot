# AGENTS.md

Canonical instructions for AI coding agents working in Row-Bot. Keep this file
concise, concrete, and aligned with `scripts/run_test_matrix.py`.

## Project Identity

Row-Bot is a local-first desktop AI assistant with provider-aware agent
runtimes, tools, workflows, durable memory/knowledge graph data, MCP, plugins,
skills, channels, voice, Developer Studio, Designer Studio, and platform
installers.

Priorities, in order:

1. Protect local user data, secrets, and local-first defaults.
2. Avoid surprise network calls, provider calls, or real channel messages.
3. Preserve approval gates and graceful recovery for destructive actions.
4. Add deterministic tests for changed behavior.
5. Keep Windows and macOS first-class; keep Linux browser/server mode healthy.

## Ground Rules

- Do not add Row-Bot first-party telemetry, analytics, or hidden phone-home
  behavior. A third-party dependency with telemetry may be used only when its
  behavior is reviewed, documented, disclosed before installation or first
  execution, and accepted by the user. Never describe third-party telemetry
  as Row-Bot telemetry, and never allow dependency telemetry to include
  Row-Bot prompts, files, memories, secrets, screenshots, tool arguments, or
  channel content. The reviewed, opt-in Cua Driver integration is the only
  currently approved exception; future dependencies require a separate review.
- Do not commit secrets, API keys, provider tokens, private local paths, or real
  user data in code, tests, docs, fixtures, snapshots, or logs.
- Do not make default tests depend on live providers, live MCP servers, real
  messaging channels, real network availability, or a specific local Ollama
  model. Mark those tests `live_provider` or `e2e`.
- Do not add test files to the `tests/` root; put them in the lane and area
  they cover (see Where To Put Tests).
- Do not edit `requirements.txt` by hand. It is generated from `uv.lock`.
- Do not add runtime implementation code to root wrappers such as `app.py` or
  `launcher.py`; application code belongs under `src/row_bot/`.
- Do not recursively delete ignored/generated directories such as `.tmp/`,
  `.testtmp/`, `dist/`, or `installer/build/` unless explicitly asked.
- Keep changes scoped. Avoid unrelated refactors, reformatting, and metadata
  churn.

## Repository Map

- `src/row_bot/providers/`: provider config, catalogs, readiness, selection,
  transports, and model routing.
- `src/row_bot/tools/`: agent tools, registry, media tools, shell/browser, MCP,
  Developer and Designer tools.
- `src/row_bot/tasks.py`: workflows, scheduling, approvals, delivery defaults,
  pipeline state, and run history.
- `src/row_bot/knowledge_graph.py`, `memory*.py`, `dream_cycle.py`,
  `wiki_vault.py`, `documents.py`: memory, recall, extraction, Dream Cycle,
  wiki vault, and document knowledge.
- `src/row_bot/channels/`: channel adapters, registry, auth, approvals, media,
  and tunnel helpers.
- `src/row_bot/mcp_client/`: MCP config, runtime, safety, requirements, and
  marketplace/client integration.
- `src/row_bot/developer/`: Developer Studio sandbox, runtime, import gate, Git
  helpers, inspector, and state.
- `src/row_bot/designer/`: Designer Studio state, export, templates, rendering,
  previews, thumbnails, and AI content.
- `src/row_bot/plugins/`, `skills_hub/`, `skills.py`: plugin and skill systems.
- `frontend/`: the React client (the only UI), served at `/app-v2/`.
- `src/row_bot/server.py` and `src/row_bot/app.py`: the FastAPI app, its
  routes, middleware and start-up sequence, run by uvicorn.
- `tests/contracts/`: fake adapter and interface contracts.
- `tests/subsystem/`: deterministic subsystem end-to-end tests with fakes.
- `tests/integration/`: deterministic cross-subsystem tests.
- `tests/e2e/`: opt-in live provider or real-service tests.
- `tests/fixtures/` and `tests/helpers/`: fakes, snapshot and subprocess helpers.
- `frontend/src/**/*.test.ts(x)` and `frontend/tests/browser/`: vitest and
  Playwright tests of the React client.
- `scripts/run_test_matrix.py`: local and CI test matrix source of truth.
- `installer/`, `.github/workflows/` and `.github/actions/`: packaging, CI,
  nightly, release, installer verification, live e2e, update manifest, and
  notarization flows, and the shared install smokes.

## Before Editing

1. Read the relevant source and nearby tests first.
2. Identify the subsystem owner and test lane before changing behavior.
3. Prefer existing helpers, fixtures, UI primitives, and local patterns.
4. Use structured parsers/APIs for structured data when reasonable.
5. Add or update focused tests for behavior changes.
6. Put tests where the subsystem's tests live (Where To Put Tests) and mark
   them `slow` or `platform` as Writing Tests describes.
7. Treat sandbox/import gates, shell execution, MCP safety, updater/installer
   flows, signing, and release workflows as security sensitive.

## Dependencies

`pyproject.toml` is canonical. `uv.lock` is the locked resolution.
`requirements.txt` is a generated installer export.

For dependency changes:

```powershell
uv lock
python scripts/export_locked_requirements.py
uv sync --locked --all-extras --group test
uv run python scripts/verify_runtime_dependencies.py all
uv run python scripts/run_test_matrix.py pr
```

For dependency verification only:

```powershell
uv lock --check
python scripts/export_locked_requirements.py --check
uv sync --locked --all-extras --group test
uv run python scripts/verify_runtime_dependencies.py all
```

Runtime extras are `voice`, `designer`, `browser`, `channels`, `mcp`,
`developer`, `local-embeddings`, and `media`; `all` is the normal development
and installer build set.

## Test Matrix

Use `scripts/run_test_matrix.py` as the executable source of truth; CI runs its
tiers.

- While iterating: focused `uv run python -m pytest <files>` and, for the
  client, `npm --prefix frontend test -- <files>` (paths relative to
  `frontend/`; `npm exec` would run vitest from the checkout root).
- Small focused change: `uv run python scripts/run_test_matrix.py fast`
  (static checks and contracts, under 2 minutes).
- The tests for what changed, by convention:
  `uv run python scripts/run_test_matrix.py changed --base origin/main`
- Before a pull request: `uv run python scripts/run_test_matrix.py pr`, the
  Linux PR lane in one command: `quality` (lock files, ruff safety and
  deserialization lint, dependency and client-platform checks),
  `client-foundation`, runtime dependencies, one deterministic pytest pass
  without the `slow` tests (coverage recorded, not gated) and the strict app
  smoke.
- OS-sensitive code: `uv run python scripts/run_test_matrix.py platform` (the
  `platform` tests and a launcher smoke) on your own OS; CI runs it on Windows
  (Python 3.13, as shipped) and macOS for every pull request.
- Browser: `uv run python scripts/run_test_matrix.py browser-smoke` (Chromium
  desktop; set `ROW_BOT_BROWSER_CHANNEL=msedge` to use an installed Edge) runs
  on every pull request; `browser-nightly` runs nightly; `browser-budgets`
  (performance budgets and Windows pixel baselines) needs a quiet local machine.
- Nightly (`.github/workflows/nightly.yml`): the whole deterministic suite with
  the slow tests on Linux (with coverage), Windows and macOS, the browser
  nightly set at desktop and phone, a Linux package smoke and the docs reference
  check; weekly, installer-verify on Windows and macOS and the browser smoke on
  Firefox and WebKit. Locally: `uv run python scripts/run_test_matrix.py nightly`.
- `tests/docs` and `tests/marketing` belong to `.github/workflows/docs.yml`
  (`run_test_matrix.py docs` locally).
- The required check on `main` is `CI / ci-ok`. Never add Ollama or another
  live service to a deterministic lane.

Useful focused tiers: `quality`, `client-foundation`, `python`, `deterministic`,
`contracts`, `subsystem`, `installer-contracts`, `dependency-integrity`,
`app-smoke`, `docs`. CI splits the `python` tier across jobs with
`ROW_BOT_TEST_SHARD=k/N`.

## Where To Put Tests

- Providers/media routing, secrets and API keys: `tests/contracts/test_provider_contract.py`
  and `tests/subsystem/providers/`.
- Channels: `tests/contracts/test_channel_contract.py` and
  `tests/subsystem/channels/`.
- MCP: `tests/contracts/test_mcp_contract.py` and `tests/subsystem/mcp/`.
- Apps & Skills (catalogs, plans, access, the safe fetcher, apps in chats):
  `tests/subsystem/integrations/`.
- Agents, approvals, goals: `tests/subsystem/agents/`.
- Workflows/tasks/approvals: `tests/subsystem/workflows/`.
- Memory, knowledge graph, wiki vault, documents, Dream Cycle:
  `tests/subsystem/knowledge_graph/`, `tests/subsystem/dream_cycle/`,
  `tests/integration/wiki_vault/`, and `tests/subsystem/regression/`.
  Memory tool behavior also has deterministic coverage in
  `tests/subsystem/tools/`.
- Developer Studio: `tests/subsystem/developer/`.
- Designer: `tests/subsystem/designer/` and `tests/snapshots/`.
- Plugins: `tests/contracts/plugins/` and `tests/subsystem/plugins/`.
- App start-up, server and native host: `tests/subsystem/client_host/`; the
  React client's API: `tests/subsystem/client_protocol/`.
- Installer, CLI, packaging, updater, GitHub workflow contracts:
  `tests/subsystem/installer/`, `tests/subsystem/updater/`, and
  `tests/contracts/installers/`.
- React client: vitest next to the component; browser specs in
  `frontend/tests/browser/`.
- Live providers, real MCP, real channels, real network: `tests/e2e/` with
  `live_provider` or `e2e` markers.

## Writing Tests

- Test behaviour through public functions, HTTP APIs or rendered UI. Do not
  assert on source text, private constants, imports, or copy that is not a
  user-facing safety contract, and do not add tests that police test
  bookkeeping.
- Use `tmp_path`, `monkeypatch`, and isolated `ROW_BOT_DATA_DIR`. A test that
  needs modules re-bound to a fresh data folder uses the `reload_for_data_dir`
  fixture, which restores them afterwards; never pop modules by hand.
- Reuse `tests/fixtures/` and `tests/helpers/` before inventing one-off fakes.
- Deterministic tests never reach the network: `tests/conftest.py` fails a test
  that connects to anything but loopback or looks up a real name. They never
  touch real user data (the live-state guard) or the checkout's git repository.
- Mark a deterministic test that takes about a second or more, or starts real
  processes, `@pytest.mark.slow`; it runs nightly, not per pull request.
- Mark tests of OS-sensitive code (process trees, paths, secret storage,
  launcher, updater, installer, plugin and MCP runtimes) `pytest.mark.platform`;
  a check fails when a test module importing such a module lacks the marker.
- Security-sensitive behaviour (sandbox and import gate, shell classification,
  MCP safety, approvals, auth/tokens/secrets, updater/installer, channel
  delivery semantics, plugin isolation) keeps a behaviour test in the pull
  request lane, never only a `slow` one.
- Snapshots are committed; record one on purpose with `ROW_BOT_UPDATE_SNAPSHOTS=1`.
- Avoid sleeps, real clocks, real users, or globally installed services in
  deterministic lanes.

## Subsystem Cautions

- Providers: keep provider IDs and provider-qualified model refs explicit; live
  catalogs and provider calls must be faked or opt-in.
- Channels: do not send real messages in deterministic tests; preserve `None`
  versus `[]` delivery semantics.
- Memory/knowledge: never read or mutate real user memory in tests; cover
  relation normalization, recall ranking, deduplication, migration, wiki sync,
  Dream Cycle idle/busy behavior, and repair paths.
- MCP: use fake stdio/HTTP/SSE transports by default; destructive tools must be
  approval-gated.
- Developer Studio/shell: classify install, network, delete, git commit, git
  push, and PR actions conservatively; test sandbox import gates.
- Designer/UI: reuse the React client primitives in `frontend/src/ui` and
  deterministic export/snapshot checks; run `npm --prefix frontend run check`
  for client changes; report manual visual checks when needed.
- Installers/release: never add signing secrets to CI. Windows signing is
  local-only; macOS notarization is manual.

## Release Flow

1. Prepare release changes on a branch.
2. For an actual versioned release, run `python scripts/cut_release.py X.Y.Z`.
3. Run `uv run python scripts/run_test_matrix.py pr`, open the release-prep PR,
   merge it after `CI / ci-ok` passes, and wait for CI on the merge commit.
4. Trigger `.github/workflows/release.yml` with the version and platform
   builds. Its `release-gate` checks the version and lock files and refuses a
   commit without a green `CI / ci-ok`; it runs the nightly suite once only if
   that commit has no green nightly run. Nothing else is re-tested.
5. Each build install-smokes its own package (`.github/actions/smoke-*`);
   review the artifacts and the checksum manifest.
6. `.github/workflows/installer-verify.yml` is the unsigned dry run for
   branches (nightly runs its Linux job daily and Windows and macOS weekly).
7. Sign Windows locally, run the macOS notarization workflows, and perform
   clean machine/VM manual smoke checks before publishing final assets.

Manual checks outside default PR CI: real provider accounts, real MCP servers,
real channels, clean-machine installer UX, repair/upgrade/uninstall, Windows
signing, macOS notarization, and release notes review.

## Coding And Handoff

- Match surrounding style. Add type hints for new public functions.
- Use `logging`, not `print`, in shipped code.
- Keep root launch wrappers thin.
- Prefer small local helpers over broad abstractions unless the pattern already
  exists.
- Preserve cross-platform behavior with `pathlib` and careful shell syntax.
- Before handoff, report exact commands run, meaningful result counts, skips,
  warnings, blockers, and manual testing still required.
- Do not claim release readiness unless installer, signing, notarization,
  live-provider/channel, and manual UX checks are complete or documented as
  pending.
