"""Row-Bot as a Harbor installed agent (Terminal-Bench 2.x, SkillsBench), source for ``--agent-import-path``.

    harbor run -d terminal-bench@2.1 --agent-import-path rowbot_agent:RowBotAgent -m ollama/qwen3.8:27b -k 1 \\
        --ak rowbot_source=<a Row-Bot source archive made by `git archive --format=tar.gz HEAD`>

Runs in Harbor's own environment (never imported by Row-Bot). Like Harbor's Codex, Claude Code and Hermes
agents it installs into each task container and runs there, so Row-Bot's own shell and file tools act on the
task's machine:

- ``install``: uv, then Row-Bot from the uploaded source archive (``uv sync --locked``, no desktop extras)
  into ``/opt/rowbot``, once per container;
- ``run``: the instruction as one message on a Hands-off conversation
  (``tests.e2e.bench.agent_evals.container_task``), with the local model reached on the host
  (``host.docker.internal:11434``); Row-Bot's ATIF trajectory is copied to the trial's logs and its token
  counts go into Harbor's context.

The plan's models (the ChatGPT sign-in) are never put into a task container by this file; a plan run needs
the owner's decision first (AGENT-EVALS-MATCHED.md, "Questions for the owner").
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from harbor.agents.installed.base import BaseInstalledAgent

_ROOT = "/opt/rowbot"
_OUT = "/tmp/rowbot"
_OLLAMA = "http://host.docker.internal:11434"


class RowBotAgent(BaseInstalledAgent):
    @staticmethod
    def name() -> str:
        return "row-bot"

    def version(self) -> str | None:
        return "checkout"

    def _rowbot_model(self) -> str:
        provider, _, model = str(self.model_name or "").partition("/")
        if provider != "ollama" or not model:
            raise ValueError("Row-Bot's Harbor agent runs local models only here: -m ollama/<tag>")
        return f"ollama:{model}"

    async def install(self, environment: Any) -> None:
        source = Path(str(self._kwargs.get("rowbot_source") or ""))  # type: ignore[attr-defined]
        if not source.is_file():
            raise ValueError("pass --ak rowbot_source=<git archive of the Row-Bot checkout>")
        await self.ensure_system_dependencies(environment, ("curl", "git", "bash"))
        await environment.upload_file(source, "/tmp/rowbot-source.tar.gz")
        await self.exec_as_root(environment, command=(
            "set -euo pipefail; "
            "command -v uv >/dev/null || (curl -LsSf https://astral.sh/uv/install.sh | env HOME=/root sh "
            "&& ln -sf /root/.local/bin/uv /usr/local/bin/uv); "
            f"mkdir -p {_ROOT} && tar -xzf /tmp/rowbot-source.tar.gz -C {_ROOT} && cd {_ROOT} && "
            "uv sync --locked --no-dev --python 3.13"))

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        model = self._rowbot_model()
        cwd = str(getattr(environment, "working_dir", "") or "/app")
        await self.exec_as_agent(environment, command=f"mkdir -p {_OUT}", timeout_sec=30)
        await self._upload_config_text(environment, content=instruction, remote_path=f"{_OUT}/instruction.txt",
                                       filename="instruction.txt")
        command = (f"cd {_ROOT} && OLLAMA_HOST={_OLLAMA} ROW_BOT_INSTALL_COUNT=0 {_ROOT}/.venv/bin/python -m "
                   "tests.e2e.bench.agent_evals.container_task --benchmark terminal-bench "
                   f"--instruction-file {_OUT}/instruction.txt --workspace {shlex.quote(cwd)} "
                   f"--model {shlex.quote(model)} --out {_OUT} > {_OUT}/stdout.txt 2>&1 || true")
        await self.exec_as_agent(environment, command=command)
        raw = await environment.exec(f"cat {_OUT}/atif.json")
        try:
            atif = json.loads(getattr(raw, "stdout", "") or "{}")
        except ValueError:
            atif = {}
        totals = atif.get("final_metrics") or {}
        context.n_input_tokens = int(totals.get("prompt_tokens") or 0)
        context.n_output_tokens = int(totals.get("completion_tokens") or 0)
        context.n_cache_tokens = int(totals.get("cached_tokens") or 0)
        context.cost_usd = float(totals.get("cost_usd") or 0.0)
        logs = Path(str(getattr(self, "logs_dir", "") or ""))
        if str(logs) not in {"", "."}:
            logs.mkdir(parents=True, exist_ok=True)
            (logs / "trajectory.json").write_text(json.dumps(atif, indent=1), encoding="utf-8")
