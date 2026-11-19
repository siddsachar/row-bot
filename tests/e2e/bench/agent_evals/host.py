"""The bridge's :class:`~.bridge.Host` on a real :class:`~tests.e2e.bench.runtime.BenchRuntime`.

Each benchmark session is its own incognito Row-Bot conversation (nothing is extracted into
memory, so one task never learns from another), with the benchmark's instructions as an Agent
Profile's instructions: they reach the stable system prompt the way a person's own profile does
(``agent._agent_profile_system_context``). Every session is one trajectory; plain model calls
(a user simulator, a judge) are recorded on the session that asked for them.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from ..ledger import BudgetStop
from ..runtime import BenchRuntime, ProviderLimit, limit_text
from ..trajectory import Step, Trajectory, append_jsonl
from .bridge import Stopped

PROFILE_SLUG = "bench-agent"


def model_ref(model: str) -> str:
    """``codex:gpt-5.2`` -> ``model:codex:gpt-5.2`` (a bare model id is refused: the provider is explicit)."""
    text = str(model or "").strip()
    if text.startswith("model:"):
        return text
    provider, _, name = text.partition(":")
    if not provider or not name:
        raise ValueError(f"a completion model is provider:model, not {model!r}")
    return f"model:{provider}:{name}"


class RuntimeHost:
    def __init__(self, runtime: BenchRuntime, benchmark: str, out: Path, *,
                 redact: Callable[[str], str] = lambda text: text, record: dict[str, Any] | None = None) -> None:
        self.runtime = runtime
        self.benchmark = benchmark
        self.out = out
        self.redact = redact
        self.record = dict(record or {})
        self.threads: dict[str, str] = {}
        self.trajectories: dict[str, Trajectory] = {}
        self.current = ""
        self._seen_calls: dict[str, int] = {}
        self._profiles: dict[str, str] = {}

    # -- sessions -----------------------------------------------------------------

    def _profile(self, instructions: str) -> str:
        digest = hashlib.sha256(instructions.encode("utf-8")).hexdigest()[:12]
        if digest not in self._profiles:
            from row_bot.agent_profiles import save_agent_profile

            saved = save_agent_profile(
                slug=f"{PROFILE_SLUG}-{digest}", display_name="Benchmark agent",
                description="The benchmark's own instructions for its agent.", instructions=instructions,
                tool_policy_json={"capability": "write_capable"}, source="user_created", scope="user")
            self._profiles[digest] = str(saved["id"])
        return self._profiles[digest]

    def new_session(self, session: str, tools: list[dict[str, Any]], instructions: str,
                    meta: dict[str, Any]) -> None:
        from row_bot import threads

        config = self.runtime.config
        thread_id = f"bench_{uuid.uuid4().hex[:12]}"
        profile = self._profile(instructions) if instructions.strip() else ""
        threads.create_thread(f"{self.benchmark} {session}"[:80], thread_id=thread_id,
                              approval_mode=config.approval_mode, model_override=config.model_ref,
                              agent_profile_id=profile, seed_default_skills=False, incognito=True)
        if config.reasoning_effort:
            threads.set_thread_reasoning_selection(thread_id, config.model_ref,
                                                   {"kind": "effort", "effort": config.reasoning_effort})
        history = seeded_messages(meta.get("history") or [])
        if history:
            threads.append_checkpoint_messages(thread_id, history)
        self.threads[session] = thread_id
        self.current = session
        self._seen_calls[session] = 0
        self.trajectories[session] = Trajectory(task_id=session, benchmark=self.benchmark, config={
            **config.to_dict(), **self.record, "meta": meta, "tools": [tool.get("function", tool).get("name")
                                                                      for tool in tools],
            "instructions_sha256": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
            "local_model": self.runtime.local_model or None, "setup_notes": self.runtime.notes})

    def turn(self, session: str, prompt: str) -> Step:
        trajectory = self.trajectories[session]
        number = sum(step.kind == "turn" for step in trajectory.steps) + 1
        try:
            step = self.runtime.turn(self.threads[session], prompt, name=f"turn {number}")
        except ProviderLimit as exc:
            trajectory.status, trajectory.reason = "provider_limit", str(exc)[:500]
            raise Stopped("limit", str(exc)) from exc
        # The runtime reads the conversation's calls from its checkpoint (all of them so far): keep this turn's.
        seen = self._seen_calls.get(session, 0)
        step.tool_calls, self._seen_calls[session] = step.tool_calls[seen:], len(step.tool_calls)
        trajectory.steps.append(step)
        if step.status == "budget":
            trajectory.status, trajectory.reason = "budget", "; ".join(step.errors)[:500]
            raise Stopped("budget", "; ".join(step.errors) or "the cost cap")
        return step

    def complete(self, purpose: str, model: str, effort: str, messages: list[dict[str, str]]) -> str:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        step = Step(kind="complete", name=purpose, metrics={"model": model, "effort": effort or "provider default",
                                                            "messages": len(messages)})
        chat = {"system": SystemMessage, "assistant": AIMessage}
        started = time.monotonic()
        try:
            reply = _completion_llm(model_ref(model), effort, purpose).invoke(
                [chat.get(item["role"], HumanMessage)(content=item["content"]) for item in messages])
        except BudgetStop as exc:
            raise Stopped("budget", str(exc)) from exc
        except Exception as exc:
            if limit_text(str(exc)):
                raise Stopped("limit", f"{type(exc).__name__}: {exc}"[:300]) from exc
            raise
        content = getattr(reply, "content", reply)
        if isinstance(content, list):
            content = "".join(str(part.get("text") or "") if isinstance(part, dict) else str(part) for part in content)
        step.answer = str(content or "")
        step.seconds = round(time.monotonic() - started, 3)
        self.runtime.drain_calls(step, phase=purpose)
        if self.current in self.trajectories:
            self.trajectories[self.current].steps.append(step)
        return step.answer

    def end(self, session: str, outcome: dict[str, Any], calls: list[dict[str, Any]]) -> None:
        trajectory = self.trajectories.pop(session, None)
        if trajectory is None:
            return
        trajectory.steps.append(Step(kind="outcome", name=str(outcome.get("task_id") or session),
                                     metrics={**outcome, "bridge_tool_calls": len(calls)}))
        if trajectory.status == "running":
            trajectory.status = "done"
        trajectory.finished_at = time.time()
        append_jsonl(self.out, trajectory, redact=self.redact)

    def flush(self) -> None:
        """Sessions the benchmark never ended (it crashed, or stopped): written as they are."""
        for session in list(self.trajectories):
            trajectory = self.trajectories[session]
            if trajectory.status == "running":
                trajectory.status, trajectory.reason = "unfinished", "the benchmark never ended this session"
            self.end(session, {"status": trajectory.status}, [])


def seeded_messages(history: list[dict[str, str]]) -> list[Any]:
    """The benchmark's opening turns (tau2's greeting) as the conversation's first messages."""
    from langchain_core.messages import AIMessage, HumanMessage

    kinds = {"assistant": AIMessage, "user": HumanMessage}
    found = []
    for number, item in enumerate(history):
        kind = kinds.get(str(item.get("role") or ""))
        if kind is None:
            raise ValueError(f"only user and assistant turns are seeded, not {item.get('role')!r}")
        found.append(kind(id=f"seed-{number}", content=str(item.get("content") or "")))
    return found


def _completion_llm(ref: str, effort: str, purpose: str) -> Any:
    """Row-Bot's own model for *ref* at the named reasoning effort (or the provider's default), tagged with
    *purpose* in the usage ledger, the way background calls are built (``conversation_naming``)."""
    from row_bot import models
    from row_bot.providers.resolution import resolve_provider_config
    from row_bot.providers.reasoning import (
        ReasoningRequestPlan,
        ReasoningSelection,
        canonical_reasoning_model_ref,
        resolve_reasoning_capabilities_for_ref,
    )
    from row_bot.providers.usage_ledger import with_usage_purpose

    resolved = resolve_provider_config(ref, allow_legacy_local=True)
    canonical = canonical_reasoning_model_ref(resolved.provider_id, resolved.runtime_model)
    capabilities = resolve_reasoning_capabilities_for_ref(canonical)
    selection = ReasoningSelection(kind="effort", effort=effort) if effort else ReasoningSelection()
    plan = ReasoningRequestPlan(canonical, selection, capabilities)
    return with_usage_purpose(models.get_llm_for(resolved.selection_ref, reasoning_plan=plan), purpose)
