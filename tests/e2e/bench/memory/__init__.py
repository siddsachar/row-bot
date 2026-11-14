"""The memory-benchmark adapter: LoCoMo (audited key) and LongMemEval-S through Row-Bot's real memory path.

- ``datasets``: the benchmark files as tasks (sessions, questions, dates, perspectives).
- ``adapter``: ingestion (saved conversations, background extraction, optional Dream Cycle)
  and the question turns, inside a worker.
- ``protocols``: the published judges, read from the benchmarks' own files.
- ``judge``: judging, the judge-sensitivity check and the scores.
"""
