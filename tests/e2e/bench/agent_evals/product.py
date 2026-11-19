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

        # A turn is sent with the tool groups that are on, as the app sends it (BenchRuntime.turn passes
        # config.tools to agent.stream_agent): here, every group a fresh profile has on.
        self.config.tools = tuple(sorted(tool.name for tool in registry.get_enabled_tools()))
        self.notes.append(f"default tools: {list(self.config.tools)}")
        self._undo.append(withhold_person_tools(self.withheld_tools))
        self._undo.append(install_call_gate(self.ledger))
        self._undo.append(install_recall_recorder(self.recall_log))
        from tests.e2e.live_eval import ledger as usage_rows

        self._seen_row = usage_rows.max_id(self.usage_db())

    def turn_with_followups(self, thread_id: str, prompt: str, roots: list[Path]) -> list[Step]:
        """One message, then the one follow-up the app makes after it (:func:`app_followup`), never chained."""
        steps = [self.turn(thread_id, prompt, name="task")]
        if steps[0].status != "done":
            return steps
        seen = len(steps[0].tool_calls)  # the runtime lists the conversation's calls so far: keep each turn's own
        found = app_followup(thread_id, steps[0].answer, self.config.model_ref, roots, record=self.followups)
        if found is not None:
            name, followup_prompt = found
            steps.append(self.turn(thread_id, followup_prompt, name=name))
            steps[-1].tool_calls = steps[-1].tool_calls[seen:]
        return steps


def app_followup(thread_id: str, reply: str, model_ref: str, roots: list[Path], *,
                 record: list[str] | None = None) -> tuple[str, str] | None:
    """The follow-up the app would start after a completed turn with *reply*, as ``(name, prompt)``, or None.

    The app's own steps (``conversation_followups._carry_on`` and ``_carry_on_if_still_working``): the word rule
    finds a reply that stops on its next step, then the chat's own model confirms it stopped mid-work (the
    narrower rule decides when that check can't run); otherwise a reply that names files it says it saved and
    that aren't there gets the file check. *roots* are where those files are looked for (the task's workspace).
    """
    from row_bot.application import conversation_followups as followups
    from row_bot.file_claims import claimed_missing, could_claim

    notes = record if record is not None else []
    text = str(reply or "").strip()
    if followups.stopped_on_next_step(text):
        unchecked = bool(followups._SURE_NEXT_STEP.search(text[-followups._TAIL_CHARS:]))  # noqa: SLF001
        if followups._still_working(thread_id, text[-1500:], model_ref, unchecked=unchecked):  # noqa: SLF001
            notes.append("carry on")
            return "carry on", followups.CARRY_ON_PROMPT
        notes.append("carry on: the stop check said the work was done")
        return None
    if could_claim(text):
        missing = claimed_missing(text, roots)
        if missing:
            notes.append(f"file check: {missing}")
            return "file check", followups.file_check_prompt(missing)
    return None
