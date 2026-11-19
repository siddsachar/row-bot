"""PawBench v1.0 with Row-Bot as the harness, up to the container step: the transcript PawBench's grader
reads (``transcript``), PawBench's own scoring on a task subset (``score``), the ``RowBotAgent`` source for a
PawBench checkout (``adapter/rowbot.py``) and the image (``docker/Dockerfile.pawbench-rowbot``). The task runs
in its container through ``agent_evals.container_task``."""
