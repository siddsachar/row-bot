"""Row-Bot as the agent in public agent benchmarks (benchmarks plan, lane E).

Opt-in only, like the memory harness beside it: the runner is ``scripts/bench_agents.py``; the
deterministic tests are ``tests/subsystem/bench/test_agent_eval_adapters.py``.

- ``wire``: the bridge's protocol and the benchmark side's client (standard library only: a
  benchmark's own runner imports it by path, in its own virtual environment).
- ``bridge``: the worker side: the benchmark's tools as a pinned Row-Bot tool group, one turn handed
  out call by call, the loopback server.
- ``host``: the bridge on a real ``BenchRuntime`` (incognito conversations, the benchmark's
  instructions as an Agent Profile, trajectories, plain model calls for a user simulator or judge).
- ``worker``: ``python -m tests.e2e.bench.agent_evals.worker``: the worker process.
- ``tau2``: τ²-bench 1.0.1: the tau2 side (``rowbot_tau2.py``) and pass^k scoring against published
  per-task rewards.
- ``pawbench``: PawBench v1.0, written up to the container step (the in-container worker, the
  OpenClaw-transcript converter, the submission writer and the ``RowBotAgent`` source).
- ``harbor``: Terminal-Bench and SkillsBench through Harbor, written up to the container step.
- ``published.json``: the published rows each eval is matched against, with sources and dates.
- ``cli``: the commands behind ``scripts/bench_agents.py``.
"""
