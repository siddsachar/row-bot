# -*- coding: utf-8 -*-
"""Row-Bot agent implementation for pawbench evaluation.

Source for ``pawbench/agents/impl/rowbot_agent.py`` in a PawBench checkout (kept in Row-Bot's repository
until it is upstreamed; never imported by Row-Bot). Register it in ``pawbench/agents/factory.py``:

    from pawbench.agents.impl.rowbot_agent import RowBotAgent
    "rowbot": (RowBotAgent, ROWBOT_DEFAULT_IMAGE),

and build the image from a Row-Bot checkout:

    docker build -f tests/e2e/bench/agent_evals/pawbench/docker/Dockerfile.pawbench-rowbot -t rowbot-pawbench:latest .

The run keeps PawBench's contract: the same task prompt (``instruction``), the same workspace
(``AGENT_WORKSPACE``), the same timeout behaviour (the inner ``timeout`` plus PawBench's own), a transcript
in ``<workspace>/sessions/`` in the OpenClaw format every harness's transcript is normalised to, and the same
result dict. The model is Row-Bot's own provider: ``ollama/<tag>`` reaches the host's Ollama at
``host.docker.internal:11434``; the model the runner passes (``--model custom/qwen3.6:27b-q4_K_M``) only
names it.
"""

import json
import shlex
from typing import Any, Dict, Optional

from pawbench.agents.base import ContainerAgent
from pawbench.agents.constants import AGENT_WORKSPACE
from pawbench.envs.base import BaseEnvironment

ROWBOT_DEFAULT_IMAGE = "rowbot-pawbench:latest"
_OUT = "/tmp/rowbot"
_OLLAMA = "http://host.docker.internal:11434"


def _rowbot_model(model: str) -> str:
    """PawBench's ``provider/model`` -> Row-Bot's ``provider:model``: a local Ollama tag is served by Row-Bot's
    ``ollama`` provider whatever prefix the runner used."""
    _, _, name = str(model).partition("/")
    return f"ollama:{name or model}"


class RowBotAgent(ContainerAgent):
    def __init__(self, name: str = "rowbot", **kwargs: Any):
        super().__init__(name, **kwargs)
        self._last_system_prompt: Optional[str] = None

    @property
    def version(self) -> Optional[str]:
        return self.config.get("rowbot_version") or "checkout"

    async def install(self, environment: BaseEnvironment) -> None:
        check = await environment.execute_command("test -x /opt/rowbot/.venv/bin/python && echo ok", timeout=30)
        if "ok" not in (check.get("stdout") or ""):
            raise RuntimeError(f"Row-Bot is not installed in this image; build {ROWBOT_DEFAULT_IMAGE}")

    async def setup(self, environment: BaseEnvironment) -> None:
        await self.install(environment)
        await environment.execute_command(
            f"rm -rf {_OUT} && mkdir -p {_OUT} {AGENT_WORKSPACE}/sessions {AGENT_WORKSPACE}/output", timeout=30)

    async def run(self, instruction: str, environment: BaseEnvironment) -> Dict[str, Any]:
        model = _rowbot_model(self.config.get("model") or "")
        timeout_s = int(self.config.get("task_timeout_s") or 1800)
        await environment.write_file(f"{_OUT}/instruction.txt", instruction)
        command = (
            f"cd {shlex.quote(AGENT_WORKSPACE)} && "
            f"OLLAMA_HOST={_OLLAMA} ROW_BOT_INSTALL_COUNT=0 "
            f"timeout --kill-after=10s {timeout_s}s /opt/rowbot/.venv/bin/python -m "
            "tests.e2e.bench.agent_evals.container_task --benchmark pawbench "
            f"--instruction-file {_OUT}/instruction.txt --workspace {shlex.quote(AGENT_WORKSPACE)} "
            f"--model {shlex.quote(model)} --out {_OUT} > {_OUT}/stdout.txt 2>&1; echo exit=$?"
        )
        result = await environment.execute_command(f"cd /opt/rowbot && {command}", timeout=timeout_s + 70)
        raw = await environment.read_file(f"{_OUT}/result.json") or "{}"
        try:
            outcome = json.loads(raw)
        except ValueError:
            outcome = {"status": "error", "answer": ""}
        await self._sync_workspace_to_output(environment, AGENT_WORKSPACE)
        return {
            "success": outcome.get("status") == "done",
            "output": str(outcome.get("answer") or ""),
            "error": str(outcome.get("reason") or result.get("stderr") or ""),
            "returncode": result.get("returncode"),
            "session_data": "",
            "metrics": {"execution_time": 0, "model_used": model, "provider": "ollama", "model_name": model,
                        "turns": outcome.get("turns")},
        }

    async def post_run_collect(self, environment: BaseEnvironment) -> None:
        self._last_system_prompt = await environment.read_file(f"{_OUT}/system_prompt.txt") or None
        await environment.execute_command(
            f"mkdir -p {AGENT_WORKSPACE}/output/rowbot && cp {_OUT}/trajectory.jsonl {_OUT}/ledger.jsonl "
            f"{_OUT}/result.json {AGENT_WORKSPACE}/output/rowbot/ 2>/dev/null || true", timeout=30)

    async def teardown(self, environment: BaseEnvironment) -> None:
        await environment.execute_command(f"rm -rf {_OUT}/data", timeout=30)
