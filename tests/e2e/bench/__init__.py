"""Row-Bot's public-benchmark harness (benchmarks plan, P0 plumbing).

Opt-in only: nothing here runs in the deterministic lanes. The runner is
``scripts/bench.py``; the deterministic tests of the harness itself are in
``tests/subsystem/memory/test_bench_harness.py``; the opt-in live run is
``tests/e2e/test_memory_bench.py`` (marker ``e2e``).

Every task runs in its own worker process with a scratch data folder, test mode
(the OS keychain closed), Learning off, the install count off and the pinned
model, tools and approval level. Nothing reads or writes the everyday profile.

Modules:

- ``ledger``: the cost ledger (a hard stop at the paid-API cap, $0 by default; plan and
  local calls counted, not capped).
- ``booking``: the shared booking file for the graphics card and the ChatGPT eval profile.
- ``trajectory``: per-task trajectories, JSONL, and an ATIF exporter stub.
- ``netguard``: the network allow/block hook (answer sites, canary strings).
- ``runtime``: one task's Row-Bot runtime inside a worker (environment checks, the
  eval profile's sign-in, the call gate, turns, the pinned clock).
- ``worker``: ``python -m tests.e2e.bench.worker``: one task in a fresh process.
- ``memory``: the memory-benchmark adapter (LoCoMo with the audited key, LongMemEval-S,
  BEAM-1M, LongMemEval-V2): datasets, ingestion through Row-Bot's real memory path, the
  question turns, the full-context baseline and the published judges.
- ``orchestrate``: the CLI behind ``scripts/bench.py``.
"""
