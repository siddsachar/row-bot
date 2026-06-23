# Contributing to Row-Bot

Thanks for taking an interest in Row-Bot. This document describes how to set up a
dev environment, the expected workflow for changes, and the bar for getting a
pull request merged.

Row-Bot is a personal-AI-sovereignty project. The priorities, in order, are:

1. **Privacy** â€” local-first defaults, no telemetry, no surprise network calls
2. **Reliability** â€” destructive actions confirm, errors degrade gracefully
3. **Test coverage** â€” new behavior ships with deterministic coverage in `tests/contracts/`, `tests/subsystem/`, or focused files under `tests/`
4. **Cross-platform** â€” Windows and macOS are first-class; Linux is best-effort

---

## 1. Quick start

### Prerequisites

- **Python 3.12** (the packaged app and CI both target 3.12)
- **uv** for locked Python dependency installs (`python -m pip install "uv>=0.7,<1.0"`)
- **Git**
- **Ollama** (optional but recommended for running the full test suite locally:
  https://ollama.com)
- Windows: PowerShell 5.1 or 7
- macOS: Xcode command-line tools

### Setup

```powershell
# Windows (PowerShell)
git clone https://github.com/siddsachar/row-bot.git
cd Row-Bot
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install "uv>=0.7,<1.0"
uv sync --locked --all-extras --group test
```

```bash
# macOS / Linux
git clone https://github.com/siddsachar/row-bot.git
cd Row-Bot
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install "uv>=0.7,<1.0"
uv sync --locked --all-extras --group test
```

`pyproject.toml` is the canonical dependency manifest. `uv.lock` is the full resolution, and `requirements.txt` is a generated pip export for installer compatibility only. Do not edit `requirements.txt` by hand.

### Run Row-Bot

```bash
uv run python launcher.py
```

### Run the tests

```bash
uv run python scripts/run_test_matrix.py fast
```

For new subsystem work, you can also run the deterministic pytest lane directly:

```bash
uv run python -m pytest tests/contracts tests/subsystem -m "not live_provider" -q
```

`tests/test_suite.py`, `tests/integration_tests.py`, and
`tests/test_memory_e2e.py` are retired compatibility shims. Their former
coverage is mapped in `tests/helpers/legacy_inventory.py` and
`tests/helpers/legacy_inventory_snapshot.py`; add or update real coverage in
focused pytest files instead.

Before opening a shared or release-sensitive PR, run:

```bash
uv run python scripts/run_test_matrix.py pr
```

The PR matrix also runs `uv run python scripts/run_test_matrix.py coverage`,
which writes `.tmp/coverage/migrated-subsystems.xml` and enforces the migrated
subsystem coverage baseline.

When changing dependencies, edit `pyproject.toml`, then run:

```bash
uv lock
python scripts/export_locked_requirements.py
uv sync --locked --all-extras --group test
uv run python scripts/verify_runtime_dependencies.py all
```

Runtime extras are `voice`, `designer`, `browser`, `channels`, `mcp`, `developer`, `local-embeddings`, and `media`. Use `all` for normal development and installer builds.

---

## 2. Branching & commits

Row-Bot uses a **trunk-based** model:

- `main` is always releasable.
- Every change goes through a short-lived feature branch and a pull request,
  even from the maintainer.
- No direct pushes to `main`.

### Branch names

Use one of the following prefixes:

- `feat/<slug>` â€” new feature
- `fix/<slug>` â€” bug fix
- `docs/<slug>` â€” documentation only
- `refactor/<slug>` â€” internal refactor with no behavior change
- `test/<slug>` â€” adds or fixes tests
- `chore/<slug>` â€” tooling, deps, CI

Examples: `feat/designer-pdf-export`, `fix/discord-voice-warning`.

### Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>

<optional longer body>
```

Types match the branch prefixes above (`feat`, `fix`, `docs`, `refactor`,
`test`, `chore`, plus `perf`, `build`, `ci`).

Example:

```
fix(designer): persist attached files before first turn

The first-draft path was sending the build prompt before references hit
disk, so the agent never saw the uploaded brief.
```

Keep commits focused. If a PR ends up with cleanup noise, squash on merge.

---

## 3. Pull requests

1. Fork or create a feature branch off the latest `main`.
2. Make your change. Add or update deterministic tests in `tests/contracts/`, `tests/subsystem/`, or focused files under `tests/`.
3. Run the affected pytest lane locally, then `uv run python scripts/run_test_matrix.py pr` when touching shared behavior.
4. Push and open a PR against `main`. The PR template will prompt for the
   relevant info; fill out every section.
5. CI will run on Windows, macOS, and Linux. All checks must be green.
6. A maintainer will review. Address feedback by pushing additional commits
   (do not force-push during review unless asked).
7. Squash-and-merge is the default merge style.

### What gets a PR rejected fast

- New behavior with no test coverage
- Adds telemetry, analytics, or "phone home" logic
- Adds a third-party network dependency without an opt-out
- Touches `docs/index.html` (the marketing site is hand-curated)
- Breaks the test suite on any platform
- Includes secrets, API keys, or personal data
- Is enormous and touches unrelated areas

### What gets a PR through fast

- A linked issue describing the problem
- A short rationale in the PR description
- New tests in the right contract/subsystem folder or focused `tests/test_*.py` file
- Screenshots or a short clip for UI changes
- Stays under ~500 lines of diff where possible

---

## 4. Code style

Python:

- Type hints on new public functions
- Standard library first, third-party second, local last
- Match the surrounding style; no en-masse reformatting in a feature PR
- No `print(...)` for diagnostics in shipped code; use `logging`

UI (NiceGUI):

- Reuse the shared primitives in `ui/` (`bulk_select`, `confirm`, `skeleton`,
  `timer_utils`, `chat_components`) before reaching for new abstractions
- Match the existing dark-mode-first card aesthetic

---

## 5. Tests

Prefer deterministic pytest tests:

- `tests/contracts/` for fake provider/channel/MCP/tool interface contracts.
- `tests/subsystem/` for end-to-end behavior with fakes and isolated temp data.
- `tests/e2e/` for opt-in live provider or real service checks.
- `tests/helpers/legacy_inventory.py` for proving retired legacy coverage still maps to replacement lanes.

`tests/test_suite.py`, `tests/integration_tests.py`, and `tests/test_memory_e2e.py`
are not destinations for new coverage. Add behavior under `tests/contracts/`,
`tests/subsystem/`, `tests/integration/`, `tests/smoke/`, or a focused
`tests/test_*.py` file. If a retired legacy mapping changes, update the
machine-readable inventory so the old section still points at the replacement
pytest files and verification command.

Tests should:

- Be deterministic (no real network calls; mock cloud providers)
- Run on Windows, macOS, and Linux
- Not depend on a specific Ollama model being pulled (skip cleanly when not
  available)
- Finish in seconds, not minutes
- Mark live/provider/network tests with `live_provider` or an opt-in marker so
  CI deterministic lanes stay offline

---

## 6. Reporting issues

Use the issue templates at https://github.com/siddsachar/row-bot/issues/new/choose.

For security issues, **do not open a public issue.** See [SECURITY.md](SECURITY.md).

---

## 7. Code of Conduct

Participation in this project is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
By contributing, you agree to uphold it.

---

## 8. Licensing

Row-Bot is released under the [Apache License 2.0](LICENSE). By submitting a
contribution you agree your work is licensed under the same terms. See the
[NOTICE](NOTICE) file for attribution requirements.
