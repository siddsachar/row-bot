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

- Do not add telemetry, analytics, or hidden phone-home behavior.
- Do not commit secrets, API keys, provider tokens, private local paths, or real
  user data in code, tests, docs, fixtures, snapshots, or logs.
- Do not make default tests depend on live providers, live MCP servers, real
  messaging channels, real network availability, or a specific local Ollama
  model. Mark those tests `live_provider` or `e2e`.
- Do not add substantive tests to `tests/test_suite.py`,
  `tests/integration_tests.py`, or `tests/test_memory_e2e.py`; they are retired
  compatibility shims.
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
- `src/row_bot/ui/`: NiceGUI UI surfaces and reusable UI helpers.
- `tests/contracts/`: fake adapter and interface contracts.
- `tests/subsystem/`: deterministic subsystem end-to-end tests with fakes.
- `tests/integration/`: deterministic cross-subsystem tests.
- `tests/e2e/`: opt-in live provider or real-service tests.
- `tests/fixtures/` and `tests/helpers/`: fakes, snapshots, source-test map,
  legacy inventory, subprocess helpers, and coverage inventory.
- `scripts/run_test_matrix.py`: local and CI test matrix source of truth.
- `installer/` and `.github/workflows/`: packaging, CI, release, installer
  verification, live e2e, lockfile, update manifest, and notarization flows.

## Before Editing

1. Read the relevant source and nearby tests first.
2. Identify the subsystem owner and test lane before changing behavior.
3. Prefer existing helpers, fixtures, UI primitives, and local patterns.
4. Use structured parsers/APIs for structured data when reasonable.
5. Add or update focused tests for behavior changes.
6. If a change crosses subsystem ownership, update `tests/helpers/source_test_map.py`
   and any affected inventory tests.
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

Use `scripts/run_test_matrix.py` as the executable source of truth.

- Small focused change: `uv run python scripts/run_test_matrix.py fast`
- Everything touched by changed source paths:
  `uv run python scripts/run_test_matrix.py changed --base origin/main`
- Shared, cross-subsystem, security-sensitive, dependency, installer, release,
  or high-risk change: `uv run python scripts/run_test_matrix.py pr`
- Release preflight equivalent: `uv run python scripts/run_test_matrix.py release`

Useful focused tiers:

```powershell
uv run python scripts/run_test_matrix.py contracts
uv run python scripts/run_test_matrix.py subsystem
uv run python scripts/run_test_matrix.py contract-subsystem
uv run python scripts/run_test_matrix.py coverage
uv run python scripts/run_test_matrix.py deterministic
uv run python scripts/run_test_matrix.py installer-contracts
uv run python scripts/run_test_matrix.py app-smoke
uv run python scripts/run_test_matrix.py legacy-inventory
```

The coverage tier measures selected migrated subsystem modules only. It writes
`.tmp/coverage/migrated-subsystems.xml` and enforces the current 55% migrated
subsystem baseline. It is not whole-app coverage.

## Where To Put Tests

- Providers/media routing: `tests/contracts/test_provider_contract.py`,
  `tests/subsystem/providers/`, and focused provider tests. Provider catalog,
  runtime, and selection are part of the migrated coverage gate.
- Channels: `tests/contracts/test_channel_contract.py` and
  `tests/subsystem/channels/`.
- MCP: `tests/contracts/test_mcp_contract.py`, `tests/subsystem/mcp/`, and
  focused `tests/test_mcp_client.py` coverage.
- Workflows/tasks/approvals: `tests/subsystem/workflows/` and focused
  task/workflow tests.
- Memory, knowledge graph, wiki vault, documents, Dream Cycle:
  `tests/subsystem/knowledge_graph/`, `tests/subsystem/dream_cycle/`,
  `tests/integration/wiki_vault/`, and `tests/subsystem/regression/`.
  Memory tool behavior also has deterministic coverage in
  `tests/subsystem/tools/`.
- Developer Studio: `tests/subsystem/developer/` plus approval/write-lock tests.
- Designer: `tests/subsystem/designer/` and `tests/snapshots/`.
- Plugins: `tests/contracts/plugins/` and `tests/subsystem/plugins/`.
- Installer, CLI, packaging, updater, release contracts:
  `tests/subsystem/installer/`, `tests/subsystem/updater/`, and
  `tests/contracts/installers/`.
- Live providers, real MCP, real channels, real network: `tests/e2e/` with
  `live_provider` or `e2e` markers.

Test rules:

- Use `tmp_path`, `monkeypatch`, and isolated `ROW_BOT_DATA_DIR`.
- Reuse `tests/fixtures/` and `tests/helpers/` before inventing one-off fakes.
- Assert behavior and contracts, not only imports or implementation strings,
  unless a source-level contract is intentional.
- Keep snapshots deterministic.
- Avoid sleeps, real clocks, real network, real users, or globally installed
  services in deterministic lanes.

## Legacy Inventory

Retired shim files:

- `tests/test_suite.py`
- `tests/integration_tests.py`
- `tests/test_memory_e2e.py`

Coverage ownership is tracked by:

- `tests/helpers/legacy_inventory.py`
- `tests/helpers/legacy_inventory_snapshot.py`
- `tests/helpers/coverage_inventory.py`
- `tests/helpers/source_test_map.py`

If ownership changes, update the relevant helper and run:

```powershell
uv run python -m pytest tests/subsystem/test_coverage_inventory.py tests/subsystem/test_legacy_inventory.py tests/subsystem/test_source_test_map.py -q
```

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
- Designer/UI: reuse existing NiceGUI helpers and deterministic export/snapshot
  checks; report manual visual checks when needed.
- Installers/release: never add signing secrets to CI. Windows signing is
  local-only; macOS notarization is manual.

## Release Flow

1. Prepare release changes on a branch.
2. For an actual versioned release, run `python scripts/cut_release.py X.Y.Z`.
3. Run `uv run python scripts/run_test_matrix.py pr`.
4. Open and merge the release-prep PR after CI passes.
5. Trigger `.github/workflows/release.yml` manually with the intended version
   and selected platform builds.
6. Review artifacts and checksum manifest.
7. Run `.github/workflows/installer-verify.yml` for deeper installed-package
   smoke checks when validating release candidates.
8. Sign Windows locally, run macOS notarization workflows, and perform clean
   machine/VM manual smoke checks before publishing final assets.

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
