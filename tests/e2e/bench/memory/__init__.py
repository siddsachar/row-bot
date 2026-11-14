"""The memory-benchmark adapter: LoCoMo (audited key), LongMemEval-S, BEAM-1M and LongMemEval-V2
through Row-Bot's real memory path, and the same model reading the full history as the baseline.

- ``datasets``: the benchmark files as tasks (sessions, questions, dates, perspectives).
- ``adapter``: ingestion (saved conversations, background extraction, optional Dream Cycle)
  and the question turns, inside a worker.
- ``full_context``: the baseline: the whole history (cut by each benchmark's rule) and the question.
- ``protocols``: the published judges, read from the benchmarks' own files; ``v2_metrics`` ports
  LongMemEval-V2's deterministic scorers.
- ``judge``: judging, the judge-agreement check and the scores.
- ``published.json``: others' published numbers, sourced and dated, shown as context.
"""
