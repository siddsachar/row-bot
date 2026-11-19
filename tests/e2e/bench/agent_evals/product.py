"""Row-Bot with the tools it ships, for benchmarks that measure the harness itself (PawBench, Terminal-Bench,
SkillsBench).

The memory benchmarks pin a few tool groups and fence everything else (``runtime.BenchRuntime``). Here the
harness *is* the thing measured, so :class:`ProductRuntime` keeps the default tool set as a fresh profile
has it (only tools that need the person, such as ``ask_person``, are withheld, because nobody is there to
answer), keeps web search (PawBench's open tasks need the internet), and runs the follow-up the app runs
after a turn that claims files it never wrote (``conversation_followups.file_check_prompt``), once.

It also holds the per-benchmark local-model allow-list (A1 WP1): the stock ``qwen3.8:27b`` everywhere, and
``qwen3.6:27b-q4_K_M`` only for PawBench, whose published cells are on qwen3.6-27b. Never a personal
fine-tune, never ``gpt-oss:20b``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .. import netguard
from ..runtime import (
    BenchEnvironmentError,
    BenchRuntime,
    LOCAL_MODEL,
    SignIn,
    check_environment,
    install_call_gate,
    install_recall_recorder,
)
from ..trajectory import Step

#: Local models each benchmark may use (the owner approved the qwen3.6 download for PawBench, 19 Nov 2026).
LOCAL_MODELS = {"*": (LOCAL_MODEL,), "pawbench": (LOCAL_MODEL, "qwen3.6:27b-q4_K_M")}
#: Tools that stop for a person: nobody answers in a benchmark container.
PERSON_TOOLS = frozenset({"ask_person"})


def allowed_local_models(benchmark: str) -> tuple[str, ...]:
    return LOCAL_MODELS.get(str(benchmark or "").lower(), LOCAL_MODELS["*"])


def check_local_model(benchmark: str, model: str) -> None:
    if model not in allowed_local_models(benchmark):
        raise BenchEnvironmentError(f"{benchmark} may use the local models {list(allowed_local_models(benchmark))} "
                                    f"(stock weights) only, not {model!r}")


def ollama_identity(model: str, *, host: str = "", client: Any = None) -> dict[str, Any]:
    """``runtime.ollama_identity`` for any allowed model: Ollama's tag, digest and details (no load)."""
    import httpx

    from ..runtime import ollama_base

    base = ollama_base(host)
    http = client or httpx.Client(timeout=20)
    tags = http.get(f"{base}/api/tags").json().get("models") or []
    entry = next((item for item in tags if model in {item.get("name"), item.get("model")}), None)
    if entry is None:
        raise BenchEnvironmentError(f"{model} is not installed in Ollama")
    details = (http.post(f"{base}/api/show", json={"model": model}).json().get("details")) or {}
    return {"tag": str(entry.get("name") or model), "digest": str(entry.get("digest") or ""), "size": entry.get("size"),
            "family": details.get("family"), "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level")}


def withhold_person_tools(withheld: set[str]) -> Callable[[], None]:
    """Bind every tool the turn would bind except those that wait for a person."""
    import langgraph.prebuilt as prebuilt

    original = prebuilt.ToolNode

    class NoPersonToolNode(original):  # type: ignore[misc, valid-type]
        def __init__(self, tools: Any, *args: Any, **kwargs: Any) -> None:
            kept = []
            for tool in tools:
                name = str(getattr(tool, "name", "") or "")
                (withheld.add(name) if name in PERSON_TOOLS else kept.append(tool))
            super().__init__(kept, *args, **kwargs)

    prebuilt.ToolNode = NoPersonToolNode

    def undo() -> None:
        prebuilt.ToolNode = original

    return undo


@dataclass
class ProductRuntime(BenchRuntime):
    benchmark: str = ""
    blocked_hosts: tuple[str, ...] = ()
    followups: list[str] = field(default_factory=list)

    def setup(self) -> None:
        data_dir = check_environment()
        from row_bot import data_paths, learning, models, secret_store

        if Path(data_paths.get_row_bot_data_dir()).resolve() != data_dir.resolve():
            raise BenchEnvironmentError("Row-Bot bound another data folder than ROW_BOT_DATA_DIR")
        if not secret_store._test_mode_active():  # noqa: SLF001
            raise BenchEnvironmentError("test mode is not active")
        if learning.preview_enabled() or learning.is_enabled():
            raise BenchEnvironmentError("Learning is on in this process; benchmark data never enters Learning")
        import row_bot.agent  # noqa: F401 - binds the runtime before the patches

        self.policy = netguard.NetworkPolicy(blocked_hosts=tuple(self.blocked_hosts))
        self._undo.append(netguard.install(self.policy))
        watched = logging.getLogger("row_bot")
        watched.addHandler(self.limits)
        self._undo.append(lambda: watched.removeHandler(self.limits))
        if self.config.provider == "codex":
            if self.eval_profile is None:
                raise BenchEnvironmentError("the ChatGPT lane needs the eval profile (ROW_BOT_LIVE_EVAL_CODEX_PROFILE)")
            self.sign_in = SignIn(self.eval_profile)
            self.sign_in.install()
        if self.config.provider == "ollama":
            check_local_model(self.benchmark, self.config.model)
            self.local_model = ollama_identity(self.config.model)
        models.adopt_saved_default(self.config.model_ref)
        from row_bot.tools import registry

        self.notes.append(f"default tools: {sorted(tool.name for tool in registry.get_enabled_tools())}")
        self._undo.append(withhold_person_tools(self.withheld_tools))
        self._undo.append(install_call_gate(self.ledger))
        self._undo.append(install_recall_recorder(self.recall_log))
        from tests.e2e.live_eval import ledger as usage_rows

        self._seen_row = usage_rows.max_id(self.usage_db())

    def turn_with_followups(self, thread_id: str, prompt: str, roots: list[Path]) -> list[Step]:
        """One message, then the one follow-up the app makes after it, never chained: carry on when the reply
        stops on its next step, else check the files it says it saved."""
        steps = [self.turn(thread_id, prompt, name="task")]
        if steps[0].status != "done":
            return steps
        from row_bot.application.conversation_followups import (
            CARRY_ON_PROMPT,
            file_check_prompt,
            stopped_on_next_step,
        )
        from row_bot.file_claims import claimed_missing

        if stopped_on_next_step(steps[0].answer):
            self.followups.append("carry on")
            steps.append(self.turn(thread_id, CARRY_ON_PROMPT, name="carry on"))
            return steps
        missing = claimed_missing(steps[0].answer, roots)
        if missing:
            self.followups.append(f"file check: {missing}")
            steps.append(self.turn(thread_id, file_check_prompt(missing), name="file check"))
        return steps
